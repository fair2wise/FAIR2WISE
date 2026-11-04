"""Access guards and disclosure projection for the legacy UI API (task T18B, part 1).

``app/modules/f2w_agent/api.py:create_app`` builds one :class:`LegacyUiGuard` at
startup and every one of its 19 routes calls it:

* **Startup.** :func:`build_guard` turns explicit startup configuration into
  :class:`~app.modules.security.authorization.DeploymentSettings`. The profile
  defaults to ``local`` when unset (as in T03). An ALS profile needs a Tiled
  identity port bound to the profile's Tiled and a server-side session store;
  without them startup is refused (``StartupConfigError``) instead of running
  unprotected.
* **Entry.** Routes depend on :func:`ui_context` / :func:`ui_optional_context`,
  which wrap :func:`app.api.auth.request_context` (``ROUTE_POLICIES`` R01-R19).
  The conditional rules (settings fields, chat action decision/kind,
  own-or-any for sessions, traces and annotations) are checked in the handler
  with :meth:`LegacyUiGuard.require` before any effect; a conditional admin
  capability re-runs the ALS role check (``fresh_request_context``).
* **Disclosure.** Every response body and SSE event goes through one
  :class:`~app.modules.security.disclosure.DisclosureProjector`. Local paths
  become opaque ``f2w://workdir/...`` / ``f2w://workspace/...`` ids;
  :meth:`OpaquePaths.decode` turns ids the UI sends back into paths under those
  two roots only. ``GET /traces/{turn_id}`` uses ``project_trace_for`` (raw for
  ``trace.read_any``: every ``:local`` user and verified ALS admins).
* **Quotes (H2).** Chat answers and chat stream ``complete``/``error`` events
  are projected with the turn's retrieved PDF regions
  (:class:`~app.api.source_text.TurnSources`) and a per-turn
  :class:`~app.modules.security.disclosure.QuoteVerifier` whose reader runs only
  under a ``QUOTE_VERIFICATION`` grant from the guard's startup-built
  :class:`~app.modules.security.content_access.SourceAccessAuthority`, bound to
  the route's ``RequestContext``. Verified quotes keep their quotation marks;
  unverified ones become cited statements; more than 40% of one region is
  clipped (O-D2). A turn without PDF regions is projected as before.
* **Ownership (ALS only).** Sessions and turns are bound to the verified subject
  in an :class:`OwnerRegistry` persisted under the workdir. On ``:local`` no
  owner is recorded and no identity call is made.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..modules.contracts.context import ADMIN_CAPABILITIES, DeploymentProfile, RequestContext
from ..modules.json_types import JsonValue
from ..modules.security.authorization import (
    DeploymentSettings,
    StartupConfigError,
    chat_action_capabilities,
    chat_offers_ingestion,
    health_projection,
    require,
    settings_update_plan,
)
from ..modules.security.content_access import ReadPurpose, SourceAccessAuthority, SourceAccessDenied, SourceKind
from ..modules.security.disclosure import (
    DisclosureProjector,
    PathRoots,
    QuoteVerifier,
    SourceRef,
    Viewer,
    new_correlation_id,
)
from ..modules.security.tiled_identity import TiledIdentityPort
from .auth import (
    AccessRuntime,
    CredentialStore,
    fresh_request_context,
    install_access_control,
    optional_request_context,
    request_context,
)
from .source_text import TurnSources

logger = logging.getLogger(__name__)

#: Placeholder technical writer for the legacy UI API (same default as T03's adapter). It is
#: recorded as ``executed_as`` only and never establishes a caller's role.
DEFAULT_WRITER_IDENTITY = "f2w-writer"
STARTUP_KEYS = ("F2W_DEPLOYMENT_PROFILE", "F2W_AUTHORIZATION_MODE", "F2W_TILED_URL", "F2W_TILED_WRITER_IDENTITY",
                "F2W_LOCAL_BIND", "F2W_ROLE_CHECK_MAX_AGE", "F2W_CORPUS_SCOPE")
OPAQUE_PREFIX = "f2w://"
LEGACY_SESSION_KEY = "__legacy__"
#: Answer shown instead of a download/extraction offer to callers without ingest capabilities (AUTH-03).
NO_INGESTION_ANSWER = ("The current knowledge graph does not have enough direct evidence to answer this "
                       "question. Adding papers needs an administrator.")
_PENDING_PHASES = frozenset({"awaiting_download_decision", "awaiting_extraction_decision"})
SETTING_WARNING_HEADER = "X-F2W-Warning"


def deployment_settings_from(environ: Mapping[str, str] | None = None) -> DeploymentSettings:
    """Build startup settings from trusted startup configuration (``os.environ`` when ``None``).

    ``F2W_DEPLOYMENT_PROFILE`` defaults to ``local`` and ``F2W_TILED_WRITER_IDENTITY``
    to :data:`DEFAULT_WRITER_IDENTITY` when unset, as in T03. Read once, at startup.

    Raises:
        StartupConfigError: On any value ``DeploymentSettings`` rejects.
    """
    env = os.environ if environ is None else environ
    values: dict[str, str] = {k: str(env.get(k) or "") for k in STARTUP_KEYS}
    values["F2W_DEPLOYMENT_PROFILE"] = values["F2W_DEPLOYMENT_PROFILE"].strip() or "local"
    values["F2W_TILED_WRITER_IDENTITY"] = values["F2W_TILED_WRITER_IDENTITY"].strip() or DEFAULT_WRITER_IDENTITY
    return DeploymentSettings.from_mapping(values)


# --------------------------------------------------------------------------- opaque paths


class OpaquePaths:
    """Opaque ``f2w://<label>/<tail>`` ids for local paths, and their checked reverse mapping.

    Args:
        **roots: ``label=absolute_path``. Only these roots are resolvable.
    """

    def __init__(self, **roots: Any) -> None:  # noqa: ANN401  # Any: label=path pairs; values are str or Path
        """Register the roots; empty values are skipped and relative paths resolve against the cwd."""
        self._roots: dict[str, Path] = {}
        pairs: list[tuple[str, str]] = []
        for label, raw in roots.items():
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = Path.cwd() / path
            self._roots[label] = path
            for variant in {str(path), str(path.resolve())}:
                pairs.append((variant.rstrip("/\\"), label))
                if variant.startswith("/private/"):
                    pairs.append((variant[len("/private"):], label))
        pairs = sorted(set(pairs), key=lambda item: len(item[0]), reverse=True)
        self.roots = PathRoots(tuple(pairs))

    @staticmethod
    def is_opaque(value: object) -> bool:
        """Return whether ``value`` is an ``f2w://`` id."""
        return isinstance(value, str) and value.strip().startswith(OPAQUE_PREFIX)

    def decode(self, value: Any) -> JsonValue:
        """Return the server path for an ``f2w://`` id; other values are returned unchanged.

        Raises:
            ValueError: Unknown root label, ``..`` segments, or a result outside the root.
        """
        if not self.is_opaque(value):
            return value
        rest = value.strip()[len(OPAQUE_PREFIX):]
        label, _, tail = rest.partition("/")
        root = self._roots.get(label)
        if root is None:
            raise ValueError("Unknown graph reference")
        parts = [p for p in tail.split("/") if p not in ("", ".")]
        if any(p == ".." or "\\" in p or "\x00" in p for p in parts):
            raise ValueError("Unknown graph reference")
        base = root.resolve()
        target = base.joinpath(*parts).resolve()
        if not (target == base or target.is_relative_to(base)):
            raise ValueError("Unknown graph reference")
        return str(target)

    def decode_all(self, values: Iterable[Any] | None) -> list[Any] | None:
        """Decode every item of ``values`` (``None`` stays ``None``)."""
        if values is None:
            return None
        return [self.decode(v) for v in values]


# --------------------------------------------------------------------------- ownership


class OwnerRegistry:
    """Session and turn owners on ALS (``RequestContext.principal.subject``).

    Args:
        path: JSON file to persist to, or ``None`` for memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Load the persisted owners if ``path`` exists; an unreadable file is logged and ignored."""
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {"sessions": {}, "turns": {}}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                for kind in ("sessions", "turns"):
                    self._data[kind] = {str(k): str(v) for k, v in (loaded.get(kind) or {}).items()}
            except (OSError, ValueError, AttributeError):
                logger.warning("owner registry unreadable; every resource counts as unowned")

    def owner(self, kind: str, key: str | None) -> str | None:
        """Return the owner of ``key`` (``sessions`` or ``turns``)."""
        with self._lock:
            return self._data[kind].get(str(key)) if key else None

    def claim(self, kind: str, key: str | None, subject: str) -> None:
        """Record ``subject`` as the owner of an unowned ``key``."""
        if not key:
            return
        with self._lock:
            if str(key) in self._data[kind]:
                return
            self._data[kind][str(key)] = subject
            self._save()

    def forget(self, kind: str, key: str) -> None:
        """Drop the owner of ``key``."""
        with self._lock:
            if self._data[kind].pop(str(key), None) is not None:
                self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)


class PersonalSettingsStore:
    """Personal settings per ALS principal (T25D), persisted as JSON under the workdir.

    Keys come from :meth:`LegacyUiGuard.settings_key` (verified issuer and subject); values
    are the personal runtime fields the principal saved. A principal with no entry uses the
    deployment defaults.

    Args:
        path: JSON file to persist to, or ``None`` for memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Load the persisted settings if ``path`` exists; an unreadable file is logged and starts empty."""
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self._data = {str(k): dict(v) for k, v in (loaded.get("principals") or {}).items()
                              if isinstance(v, dict)}
            except (OSError, ValueError, AttributeError) as exc:
                logger.warning("personal settings store unreadable, starting empty: %s", exc)

    def get(self, key: str) -> dict[str, Any]:
        """Return a copy of ``key``'s saved personal fields (empty when never saved)."""
        with self._lock:
            return cast(dict[str, Any], json.loads(json.dumps(self._data.get(key) or {})))

    def update(self, key: str, values: Mapping[str, Any]) -> None:
        """Merge ``values`` into ``key``'s personal fields and persist."""
        with self._lock:
            merged = dict(self._data.get(key) or {})
            merged.update(json.loads(json.dumps(dict(values))))
            self._data[key] = merged
            if self._path is None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"principals": self._data}, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)


# --------------------------------------------------------------------------- dependencies


async def ui_context(request: Request) -> RequestContext:
    """Route dependency: the checked ``RequestContext``, also kept on ``request.state``."""
    ctx = await request_context(request)
    request.state.f2w_ctx = ctx
    return ctx


async def ui_optional_context(request: Request) -> RequestContext | None:
    """Route dependency for the public ``GET /health``: ``None`` for a signed-out ALS caller."""
    ctx = await optional_request_context(request)
    request.state.f2w_ctx = ctx
    return ctx


# --------------------------------------------------------------------------- guard


class LegacyUiGuard:
    """Per-app access and disclosure wiring for the 19 legacy UI routes.

    Args:
        runtime: Access runtime (startup settings, identity port, session store).
        projector: The one disclosure projector for this app.
        paths: Opaque path roots.
        owners: Session/turn owners (used on ALS only).
        session_key: Validates a session id and returns its key (``AgentPipelineService._session_key``).
    """

    def __init__(self, runtime: AccessRuntime, projector: DisclosureProjector, paths: OpaquePaths,
                 owners: OwnerRegistry, session_key: Callable[[str | None], str],
                 source_authority: SourceAccessAuthority | None = None,
                 personal_settings: PersonalSettingsStore | None = None) -> None:
        """Bind the collaborators; without ``source_authority`` a new ``SourceAccessAuthority`` is created."""
        self.runtime = runtime
        self.projector = projector
        #: Trusted startup wiring (ADR rule A1): the only issuer of quote-verification grants.
        self._source_authority = source_authority or SourceAccessAuthority()
        self.paths = paths
        self.owners = owners
        self._session_key = session_key
        #: T25D: personal settings per ALS principal; ``None`` on ``:local`` (server settings).
        self.personal_settings = personal_settings

    # ------------------------------------------------------------------ setup

    @property
    def settings(self) -> DeploymentSettings:
        """Startup deployment settings."""
        return self.runtime.settings

    @property
    def is_local(self) -> bool:
        """Whether this is the trusted-local profile."""
        return self.runtime.settings.is_local

    def install(self, app: FastAPI) -> FastAPI:
        """Register access control and the projected HTTP error handler on ``app``."""
        install_access_control(app, self.runtime)
        app.state.f2w_legacy_ui = self
        app.add_exception_handler(StarletteHTTPException, self._http_error_handler)
        return app

    def check_service(self, service: object) -> None:
        """Refuse startup when the T03 langgraph read path would run without a verified principal."""
        if not self.is_local and getattr(service, "_read_path", None) is not None:
            raise StartupConfigError("config_rejected: F2W_RUNTIME=langgraph is local-only on the legacy UI API "
                                     "until its read path carries the verified ALS context")

    async def _http_error_handler(self, request: Request, exc: Exception) -> Any:
        """Project ``HTTPException.detail`` (paths, raw exception text) and keep the legacy shape."""
        assert isinstance(exc, StarletteHTTPException)
        detail: Any = exc.detail
        projected = self.projector.project_http_error(exc.status_code, {"detail": detail},
                                                      self.request_viewer(request))
        safe = StarletteHTTPException(exc.status_code, detail=projected["detail"],
                                      headers=getattr(exc, "headers", None))
        return await http_exception_handler(request, safe)

    # ------------------------------------------------------------------ viewers and checks

    def viewer(self, ctx: RequestContext, *, session_id: str | None = None,
               correlation_id: str | None = None) -> Viewer:
        """Projection recipient; the turn id is the correlation id inside a chat turn."""
        base = Viewer.from_context(ctx)
        return replace(base, session=session_id or base.session,
                       correlation_id=correlation_id or base.correlation_id)

    def request_viewer(self, request: Request) -> Viewer:
        """Viewer for an error raised outside a handler's own projection."""
        ctx = getattr(request.state, "f2w_ctx", None)
        if isinstance(ctx, RequestContext):
            return self.viewer(ctx)
        return Viewer(actor="anonymous", session=None, correlation_id=new_correlation_id())

    def require(self, request: Request, ctx: RequestContext, *capabilities: str) -> RequestContext:
        """Check conditional capabilities before an effect; admin ones re-run the ALS role check."""
        caps = frozenset(capabilities)
        if caps & ADMIN_CAPABILITIES and not self.is_local:
            ctx = fresh_request_context(request)
            request.state.f2w_ctx = ctx
        return require(ctx, *caps)

    def _require_owner(self, request: Request, ctx: RequestContext, owner: str | None, own: str,
                       any_: str) -> RequestContext:
        """Own resource: ``own``; anyone else's (or unowned on ALS): ``any_`` (as ``require_owner_or``)."""
        if not self.is_local and owner != ctx.principal.subject:
            return self.require(request, ctx, any_)
        return require(ctx, own)

    # ------------------------------------------------------------------ sessions and turns

    def session_access(self, request: Request, ctx: RequestContext, session_id: str | None, *,
                       claim: bool) -> RequestContext:
        """Check R04/R05 (and chat's session binding) before the session is touched.

        On ALS the shared legacy session and another principal's session need
        ``session.any``; an unowned named session is claimed by the caller when
        ``claim`` (chat, reset) and counts as someone else's otherwise (delete).
        """
        if self.is_local:
            return require(ctx, "session.own")
        key = self._session_key(session_id)
        owner = self.owners.owner("sessions", key)
        if key == LEGACY_SESSION_KEY or (owner is None and not claim):
            return self.require(request, ctx, "session.any")
        if owner is None:
            require(ctx, "session.own")
            self.owners.claim("sessions", key, ctx.principal.subject)
            return ctx
        return self._require_owner(request, ctx, owner, "session.own", "session.any")

    def session_deleted(self, session_id: str) -> None:
        """Forget the owner of a deleted session."""
        if not self.is_local:
            self.owners.forget("sessions", session_id.strip())

    def turn_access(self, request: Request, ctx: RequestContext, turn_id: str | None) -> None:
        """Refuse reusing another principal's turn id (it would overwrite their trace)."""
        if self.is_local or not turn_id:
            return
        owner = self.owners.owner("turns", turn_id)
        if owner is not None and owner != ctx.principal.subject:
            self.require(request, ctx, "trace.read_any")

    def claim_turn(self, ctx: RequestContext, turn_id: str | None) -> None:
        """Bind a new turn to the caller (ALS)."""
        if not self.is_local:
            self.owners.claim("turns", turn_id, ctx.principal.subject)

    def chat_entry(self, request: Request, ctx: RequestContext, session_id: str | None,
                   turn_id: str | None) -> RequestContext:
        """R12/R15: session binding and turn id, before any model call."""
        ctx = self.session_access(request, ctx, session_id, claim=True)
        self.turn_access(request, ctx, turn_id)
        return ctx

    def chat_action_entry(self, request: Request, ctx: RequestContext, decision: str, kind: str | None,
                          session_id: str | None, turn_id: str | None) -> RequestContext:
        """R13/R16: ``yes`` needs ``chat_action_capabilities`` (fresh role check) before any effect."""
        ctx = self.require(request, ctx, *chat_action_capabilities(decision, kind))
        ctx = self.session_access(request, ctx, session_id, claim=True)
        self.turn_access(request, ctx, turn_id)
        return ctx

    # ------------------------------------------------------------------ settings

    def settings_key(self, ctx: RequestContext) -> str | None:
        """T25D: personal-settings key for the caller, or ``None`` for the server's own settings.

        ``None`` on ``:local`` (the single local operator's settings are the server's);
        on ALS the verified issuer and subject.
        """
        if self.is_local:
            return None
        issuer = ctx.principal.issuer or self.settings.tiled_target or ""
        return f"{issuer}|{ctx.principal.subject}"

    def settings_update(self, request: Request, ctx: RequestContext, update: Any) -> tuple[Any, frozenset[str]]:
        """R03: check the fields' capabilities; drop ignored fields; decode opaque paths (T25D plan).

        On ALS a non-admin's deployment-wide fields are ignored (warning), not refused, so the
        frozen UI's Save (which sends every field) succeeds; an admin's re-run the role check.

        Returns:
            ``(update to apply, ignored field names)``.
        """
        fields = set(update.model_dump(exclude_none=True))
        plan = settings_update_plan(fields, local=self.is_local, admin=ctx.has("settings.global"))
        self.require(request, ctx, *plan.capabilities)
        ignored = plan.ignored
        changes: dict[str, Any] = {name: None for name in ignored}
        if not self.is_local and not ctx.has("settings.global") and update.selected_graph_ids:
            # Paths are hidden from non-admins (project_settings), so the UI echoes placeholders;
            # their graph selection is by graph id.
            changes.update(json_graph_path=None, json_graph_paths=None)
        else:
            if update.json_graph_path is not None:
                changes["json_graph_path"] = self.paths.decode(update.json_graph_path)
            if update.json_graph_paths is not None:
                changes["json_graph_paths"] = self.paths.decode_all(update.json_graph_paths)
        if ignored:
            logger.info("ignored settings fields not writable on %s: %s", cast(DeploymentProfile, self.settings.profile).value,
                        ", ".join(sorted(ignored)))
        return update.model_copy(update=changes), ignored

    @staticmethod
    def setting_warning_headers(ignored: Iterable[str]) -> dict[str, str]:
        """Typed ``setting_not_writable`` warning for ignored fields (header; the body schema is frozen)."""
        names = sorted(ignored)
        return {SETTING_WARNING_HEADER: "setting_not_writable=" + ",".join(names)} if names else {}

    def project_settings(self, ctx: RequestContext, payload: dict[str, Any]) -> dict[str, Any]:
        """R02/R03 response: projected; paths, ``tiled_uri`` and ``tiled_error`` need ``settings.global``."""
        out = self.projector.project_settings(payload, self.viewer(ctx))
        if not ctx.has("settings.global"):
            out.update(json_graph_path=None, json_graph_paths=[], available_json_graphs=[], tiled_uri=None,
                       tiled_error=None)
            out["available_graphs"] = [{k: v for k, v in g.items() if k != "path"}
                                       for g in out.get("available_graphs") or [] if isinstance(g, Mapping)]
        return cast(dict[str, Any], out)

    # ------------------------------------------------------------------ projections

    def project_health(self, ctx: RequestContext | None, payload: dict[str, Any]) -> dict[str, Any]:
        """R01: full (paths opaque) for ``settings.global``, else status only."""
        if health_projection(ctx) == "full":
            out = self.projector.project_paths_only(payload)
        else:
            out = {"status": payload.get("status")}
        if self.settings.exposure_warning:
            out["exposure_warning"] = self.settings.exposure_warning
        return cast(dict[str, Any], out)

    def project_paths(self, payload: object) -> JsonValue:
        """Session reset/delete bodies: only local paths are internal."""
        return self.projector.project_paths_only(payload)

    def project_route(self, ctx: RequestContext, method: str, path: str, payload: object) -> JsonValue:
        """Graph, search, upload and publication bodies through ``project_route_response``."""
        return self.projector.project_route_response(method, path, 200, payload, self.viewer(ctx))

    def withhold_ingestion(self, ctx: RequestContext, payload: dict[str, Any]) -> dict[str, Any]:
        """AUTH-03: no download/extraction offer for callers without both ingest capabilities."""
        if chat_offers_ingestion(ctx) or not payload.get("pending"):
            return payload
        return dict(payload, status="insufficient_evidence", answer=NO_INGESTION_ANSWER, pending=None)

    def offers_ingestion(self, ctx: RequestContext) -> bool:
        """Whether a pending card may be kept for ``ctx``."""
        return chat_offers_ingestion(ctx)

    def turn_projector(self, ctx: RequestContext,
                       sources: TurnSources | None) -> tuple[DisclosureProjector, list[SourceRef], Any]:
        """Projector, source regions and grant for one turn's answer.

        Without captured PDF regions this is the app's projector with no sources (the
        T18B projection). Otherwise a projector sharing the app's budget, ledger, roots
        and resolver, with a :class:`QuoteVerifier` whose reader runs only through a
        ``SourceAccessScope`` for a grant bound to ``ctx`` (purpose ``QUOTE_VERIFICATION``,
        kind ``PARSED_TEXT``, the turn's works). The caller revokes the grant afterwards.
        """
        refs = sources.refs() if sources else []
        if not refs:
            return self.projector, [], None
        try:
            grant = self._source_authority.issue(
                ctx, purpose=ReadPurpose.QUOTE_VERIFICATION, kinds=(SourceKind.PARSED_TEXT,),
                work_ids=sorted({r.work_id for r in refs}))
        except SourceAccessDenied as exc:
            logger.warning("quote verification grant refused: %s", exc.reason)
            return self.projector, [], None
        verifier = QuoteVerifier(cast(TurnSources, sources).reader_for(refs), self._source_authority.scope(ctx, grant))
        base = self.projector
        projector = DisclosureProjector(budget=base.budget, verifier=verifier, ledger=base.ledger,
                                        roots=base.roots, citation_resolver=base.citation_resolver)
        return projector, refs, grant

    def _with_turn(self, ctx: RequestContext, sources: TurnSources | None,
                   project: Callable[[DisclosureProjector, list[SourceRef]], Any]) -> JsonValue:
        projector, refs, grant = self.turn_projector(ctx, sources)
        try:
            return project(projector, refs)
        finally:
            if grant is not None:
                self._source_authority.revoke(grant)

    def project_chat(self, ctx: RequestContext, payload: dict[str, Any], *, session_id: str | None,
                     turn_id: str | None, sources: TurnSources | None = None) -> dict[str, Any]:
        """R12/R13 body; ``sources`` are the turn's retrieved regions for quote verification."""
        viewer = self.viewer(ctx, session_id=session_id, correlation_id=turn_id)
        body = self.withhold_ingestion(ctx, payload)
        return cast(dict[str, Any], self._with_turn(ctx, sources, lambda projector, refs: projector.project_chat_response(
            body, viewer, sources=refs)))

    def project_sse(self, ctx: RequestContext, event: str, data: dict[str, Any], *, session_id: str | None,
                    turn_id: str | None, sources: TurnSources | None = None) -> dict[str, Any] | None:
        """R15/R16 event data; ``None`` drops the event (pending prompts for non-ingesting callers)."""
        viewer = self.viewer(ctx, session_id=session_id, correlation_id=turn_id)
        if event == "progress":
            if not chat_offers_ingestion(ctx) and data.get("phase") in _PENDING_PHASES:
                return None
            return cast(dict[str, Any] | None, self.projector.project_sse_event(event, data, viewer))
        if data.get("status") != "stream_error":
            data = self.withhold_ingestion(ctx, data)
        return cast(dict[str, Any] | None, self._with_turn(ctx, sources, lambda projector, refs: projector.project_sse_event(
            event, data, viewer, sources=refs)))

    def stream_error(self, ctx: RequestContext, exc: BaseException, *, turn_id: str) -> str:
        """User-safe ``stream_error`` message (``project_exception``; the turn id is the reference)."""
        viewer = self.viewer(ctx, correlation_id=turn_id)
        return self.projector.project_exception(exc, viewer)[1]

    def error_detail(self, ctx: RequestContext, exc: BaseException) -> str:
        """User-safe ``HTTPException.detail`` for an exception (``project_exception``)."""
        return self.projector.project_exception(exc, self.viewer(ctx))[1]

    # ------------------------------------------------------------------ traces, telemetry, annotations

    def trace_access(self, request: Request, ctx: RequestContext, turn_id: str) -> RequestContext:
        """R17: own turn ``trace.read_own``, else ``trace.read_any`` (before the store is read)."""
        owner = self.owners.owner("turns", turn_id) if not self.is_local else None
        return self._require_owner(request, ctx, owner,
                                   "trace.read_own", "trace.read_any")

    def project_trace(self, ctx: RequestContext, record: object) -> JsonValue:
        """R17 body: raw for ``trace.read_any``, projected otherwise (``project_trace_for``)."""
        return self.projector.project_trace_for(record, ctx)

    def annotation(self, request: Request, ctx: RequestContext, record: dict[str, Any]) -> dict[str, Any]:
        """R19 (O-A1): own turns for any signed-in user, any turn for admins; annotator is the verified actor."""
        owner = self.owners.owner("turns", record.get("turn_id")) if not self.is_local else None
        self._require_owner(request, ctx, owner, "annotation.write", "trace.read_any")
        return dict(record, annotator=ctx.principal.subject)

    @staticmethod
    def telemetry_events(ctx: RequestContext, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """R18: the actor is recorded from the context, never the payload."""
        return [dict(e, actor=ctx.principal.subject) for e in events]


def build_guard(workdir: Any, *, session_key: Callable[[str | None], str],
                deployment: DeploymentSettings | None = None,
                startup_config: Mapping[str, str] | None = None,
                identity_port: TiledIdentityPort | None = None,
                credential_store: CredentialStore | None = None,
                clock: Callable[[], datetime] | None = None,
                project_root: Path | None = None,
                projector: DisclosureProjector | None = None) -> LegacyUiGuard:
    """Build the guard from trusted startup configuration.

    Args:
        workdir: Service workdir (opaque root ``workdir``; ALS owner registry location).
        session_key: Session id validator.
        deployment: Explicit settings; otherwise :func:`deployment_settings_from(startup_config)`.
        startup_config: Startup mapping (``os.environ`` when ``None``), read once.
        identity_port: ALS Tiled identity port (required on ALS).
        credential_store: ALS server-side session store (required on ALS).
        clock: Current time (tests).
        project_root: Opaque root ``workspace`` (the process working directory, ``api.project_root()``).
        projector: Disclosure projector; a default one with these path roots otherwise.

    Raises:
        StartupConfigError: Rejected settings, or an ALS profile without a matching identity
            port and session store.
    """
    settings = deployment if deployment is not None else deployment_settings_from(startup_config)
    if not settings.is_local:
        if identity_port is None or credential_store is None:
            raise StartupConfigError(f"config_rejected: {cast(DeploymentProfile, settings.profile).value} needs a Tiled identity port and "
                                     "a server-side session store; refusing to start unprotected")
        if getattr(identity_port, "tiled_target", None) != settings.tiled_target:
            raise StartupConfigError(f"config_rejected: identity port is not bound to {settings.tiled_target}")
    runtime_kw: dict[str, Any] = {"identity_port": identity_port, "credential_store": credential_store}
    if clock is not None:
        runtime_kw["clock"] = clock
    runtime = AccessRuntime(settings, **runtime_kw)
    paths = OpaquePaths(workdir=workdir, workspace=project_root or Path.cwd())
    projector = projector or DisclosureProjector(roots=paths.roots)
    owners = OwnerRegistry(None if settings.is_local else Path(workdir) / "access" / "owners.json")
    personal = None if settings.is_local else PersonalSettingsStore(
        Path(workdir) / "access" / "personal_settings.json")
    return LegacyUiGuard(runtime, projector, paths, owners, session_key, source_authority=SourceAccessAuthority(),
                         personal_settings=personal)


__all__ = ("DEFAULT_WRITER_IDENTITY", "LegacyUiGuard", "NO_INGESTION_ANSWER", "OpaquePaths", "OwnerRegistry",
           "PersonalSettingsStore",
           "SETTING_WARNING_HEADER", "build_guard", "deployment_settings_from", "ui_context",
           "ui_optional_context")
