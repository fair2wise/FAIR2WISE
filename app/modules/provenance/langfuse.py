"""Opt-in Langfuse trace exporter (R-08, addendum A7 T29 B3; owner decision O-P3).

Traces hold prompts, merged context and source text, so this exporter is built so that
nothing leaves unless an operator turned it on and the data went through the disclosure
projector:

* **OFF by default and not enable-able by accident.** :class:`LangfuseSettings` defaults to
  ``enabled=False``; :func:`exporter_from_settings` returns ``None`` unless ``enabled is True``
  *and* both keys are set; :class:`LangfuseExporter` refuses to be constructed otherwise
  (:class:`LangfuseDisabled`). Nothing reads environment variables or ``.env``.
* **Only projected data.** :meth:`LangfuseExporter.export_trace` runs the trace record through
  :meth:`DisclosureProjector.project_trace` (never ``project_trace_for``: even an administrator's
  raw view must not be sent to a third party) and builds every span from that projection. There
  is no method that sends a raw record. Prompts, merged context and raw model outputs reach
  Langfuse only as SHA-256 digests and lengths.
* **Lazy optional import.** ``langfuse`` is not a dependency; it is imported only when an
  enabled exporter builds its client, and tests inject a fake ``client_factory``.
* **No user identity.** User and session ids are not sent.

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/langfuse_tracer.py
(``LangfuseTracer``, ``tracer_from_settings``) (BSD-3-Clause), rewritten for the v4 client
surface and the projector; the donor's ``trace_agent_run``/``trace_tool_call`` raw-input
methods are intentionally not carried over. ``base_url=`` is passed explicitly because the
SDK lets an environment variable outrank ``host=``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

from ..contracts.context import RequestContext
from ..security.disclosure import DisclosureProjector, Viewer

logger = logging.getLogger(__name__)

#: Methods this module calls on the Langfuse client (pin list for a test against the real SDK).
REQUIRED_CLIENT_METHODS: tuple[str, ...] = ("start_observation", "create_trace_id", "flush")


class LangfuseDisabled(RuntimeError):
    """The exporter was requested without the explicit setting (or without credentials)."""


@dataclass(frozen=True)
class LangfuseSettings:
    """Explicit Langfuse export settings. ``enabled`` is the only switch and defaults to off.

    Attributes:
        enabled: Must be exactly ``True``; anything else is off.
        public_key: Langfuse public key.
        secret_key: Langfuse secret key (never logged; excluded from ``repr``).
        base_url: Langfuse server (required for self-hosted; empty means the SDK default).
    """

    enabled: bool = False
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    base_url: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> LangfuseSettings:
        """Read ``langfuse_export_enabled`` / ``langfuse_public_key`` / ``langfuse_secret_key`` / ``langfuse_base_url``.

        Only the booleans ``True`` and the strings ``"true"``/``"1"``/``"yes"`` enable export.
        """
        raw = values.get("langfuse_export_enabled", False)
        enabled = raw is True or (isinstance(raw, str) and raw.strip().lower() in {"true", "1", "yes"})
        return cls(enabled=enabled, public_key=str(values.get("langfuse_public_key") or ""),
                   secret_key=str(values.get("langfuse_secret_key") or ""),
                   base_url=str(values.get("langfuse_base_url") or ""))

    @property
    def active(self) -> bool:
        """Whether export is enabled and fully configured."""
        return self.enabled is True and bool(self.public_key) and bool(self.secret_key)


ClientFactory = Callable[[LangfuseSettings], Any]


def default_client_factory(settings: LangfuseSettings) -> Any:
    """Build the real client; imports ``langfuse`` lazily (an optional dependency)."""
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise ImportError("Langfuse export is enabled but the optional 'langfuse' package is not installed") from exc
    kwargs: dict[str, str] = {"public_key": settings.public_key, "secret_key": settings.secret_key}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return Langfuse(**kwargs)


class LangfuseExporter:
    """Send disclosure-projected traces to Langfuse. Construct via :func:`exporter_from_settings`."""

    def __init__(self, settings: LangfuseSettings, *, projector: DisclosureProjector,
                 client_factory: ClientFactory = default_client_factory) -> None:
        if not isinstance(settings, LangfuseSettings) or not settings.active:
            raise LangfuseDisabled("Langfuse export needs langfuse_export_enabled=True and both keys")
        if not isinstance(projector, DisclosureProjector):
            raise TypeError("the exporter requires a DisclosureProjector; there is no unprojected export")
        self._projector = projector
        self._client = client_factory(settings)

    def trace_id_for(self, run_id: str) -> str:
        """The deterministic Langfuse trace id for ``run_id`` (the client hashes the seed)."""
        return str(self._client.create_trace_id(seed=run_id))

    def export_trace(self, record: Mapping[str, Any], context: RequestContext) -> str | None:
        """Project ``record`` for ``context``'s viewer and export it; returns the trace id or ``None``.

        A failing export is logged without the payload and never raises into the caller's run.
        """
        projected = self._projector.project_trace(record, Viewer.from_context(context))
        if not isinstance(projected, Mapping):
            return None
        turn = str(projected.get("turn_id") or record.get("turn_id") or "unknown")
        try:
            trace_id = self.trace_id_for(turn)
            self._span(trace_id, f"turn_{turn}", "agent", metadata={
                "turn_id": turn, "started_at": projected.get("started_at"), "finished_at": projected.get("finished_at"),
                "status": (projected.get("response") or {}).get("status"), "env": projected.get("env"),
                "disclosure": "projected"},
                input=(projected.get("request") or {}), output=(projected.get("response") or {}))
            for index, call in enumerate(projected.get("llm_calls") or []):
                self._span(trace_id, f"llm_{call.get('label') or index}", "generation", metadata={"model": call.get("model"),
                           "latency_ms": call.get("latency_ms")}, input={"prompt": call.get("prompt"),
                           "message_count": call.get("message_count")}, output=call.get("response"),
                           error=bool(call.get("error")))
            if projected.get("retrieval"):
                self._span(trace_id, "retrieval", "retriever", metadata={"disclosure": "projected"},
                           output=projected["retrieval"])
            for error in projected.get("errors") or []:
                self._span(trace_id, f"error_{error.get('type') or 'unknown'}", "span", metadata=dict(error), error=True)
            return trace_id
        except Exception as exc:  # noqa: BLE001 - telemetry must not break a run
            logger.warning("Langfuse export failed (%s)", type(exc).__name__)
            return None

    def _span(self, trace_id: str, name: str, kind: str, *, metadata: Mapping[str, Any] | None = None,
              input: Any = None, output: Any = None, error: bool = False) -> None:  # noqa: A002 - parameter names mirror the Langfuse observation API (input/output)
        span = self._client.start_observation(
            trace_context={"trace_id": trace_id}, name=name, as_type=kind, input=input, output=output,
            metadata=dict(metadata or {}), level="ERROR" if error else None)
        span.end()

    def flush(self) -> None:
        """Flush queued spans; failures are logged, not raised."""
        try:
            self._client.flush()
        except Exception as exc:  # noqa: BLE001 - telemetry flush must never break the caller; failure is logged
            logger.warning("Langfuse flush failed (%s)", type(exc).__name__)


def exporter_from_settings(settings: LangfuseSettings | None, *, projector: DisclosureProjector,
                           client_factory: ClientFactory = default_client_factory) -> LangfuseExporter | None:
    """The exporter, or ``None`` when export is not explicitly enabled and configured.

    A set switch with missing keys logs one warning and returns ``None`` (a misconfiguration is
    not a license to export, and not a reason to crash the app).
    """
    if settings is None or settings.enabled is not True:
        return None
    if not settings.active:
        logger.warning("langfuse_export_enabled is set but the Langfuse keys are not; no traces will be exported")
        return None
    return LangfuseExporter(settings, projector=projector, client_factory=client_factory)


__all__ = ("ClientFactory", "LangfuseDisabled", "LangfuseExporter", "LangfuseSettings", "REQUIRED_CLIENT_METHODS",
           "default_client_factory", "exporter_from_settings")
