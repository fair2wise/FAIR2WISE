"""Version and capability checks against a running Tiled server, and external-mode connect.

Implements checks C1-C4 and C6 of ``docs/adr/tiled-deployment.md`` section 3.4 against the
pinned ``tiled==0.2.18`` surface (observed in T20B, ``docs/adr/evidence/tiled/
capabilities_0.2.18.md``):

* C1 ``GET /api/v1/`` -> ``library_version`` must equal the pin exactly.
* C2 ``POST /api/graphql {__typename}`` -> ``Query`` (the graph router is mounted only for
  a catalog-backed tree).
* C3 introspection of ``Mutation``/``Query``/``UpdateEntityInput``/``UpdateLinkInput``:
  required operations present; whether entity/link ``properties`` are updatable.
* C4 writer identity: multi-user servers -> ``GET /api/v1/auth/whoami``; single-user API key
  servers (no providers, whoami is 404 there) -> an authenticated read must not be 401.
* C6 read probe ``entities(limit:1)``: ``ok`` or ``empty``.

Every check is read-only: nothing here mutates the target. External mode uses only this
module and :mod:`.tiled_config`; it must not import :mod:`.tiled_lifecycle` (a test
checks this), so a central outage can never start a local server.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Mapping

from .tiled_config import (
    PINNED_TILED_VERSION,
    ExternalTiledConfig,
    TiledDeploymentError,
    normalize_base_url,
    read_secret_file,
)

GRAPHQL_PATH = "/api/graphql"
ABOUT_PATH = "/api/v1/"
WHOAMI_PATH = "/api/v1/auth/whoami"
HEALTH_PATH = "/healthz"
METADATA_ROOT_PATH = "/api/v1/metadata/"

#: Graph operations F2W depends on (C3).
REQUIRED_MUTATIONS = ("createEntity", "createLink", "updateEntity", "deleteEntity", "deleteLink")
REQUIRED_QUERIES = ("entity", "entities", "link", "links")

#: Behaviour of the pinned build that introspection cannot reveal (observed in T20B).
KNOWN_QUIRKS: dict[str, tuple[str, ...]] = {
    "0.2.18": (
        # schema.py passes strawberry's UNSET to the store, which checks its own sentinel:
        # updateEntity/updateLink without accessBlob fail with "Type is not JSON
        # serializable: UnsetType". Pass accessBlob explicitly (e.g. {} with no policy).
        "update-requires-accessBlob",
        # Entities carry no accessBlob output field; links do.
        "entity-accessBlob-not-readable",
        # No uniqueness on entity uri: two createEntity calls with one uri both succeed.
        "entity-uri-not-unique",
    ),
}


class TiledTargetUnavailable(TiledDeploymentError):
    """Unreachable, timed out or 5xx. Imports stay pending; nothing falls back."""

    code = "target_unavailable"


class TiledUnsupportedVersion(TiledDeploymentError):
    """``library_version`` differs from the pin."""

    code = "unsupported_version"


class TiledGraphUnavailable(TiledDeploymentError):
    """``/api/graphql`` missing or not a GraphQL endpoint."""

    code = "graph_unavailable"


class TiledCapabilityMissing(TiledDeploymentError):
    """A required graph operation is absent."""

    code = "capability_missing"


class TiledWriterUnauthorized(TiledDeploymentError):
    """The writer credential is denied, or lacks the required role."""

    code = "writer_unauthorized"


@dataclass(frozen=True)
class CapabilityReport:
    """Non-secret snapshot attached to import plans and receipts.

    Attributes:
        url: Normalized target URL.
        library_version / api_version: From ``GET /api/v1/``.
        authentication_required: ``about.authentication.required``.
        providers: Authentication provider names (empty in single-user mode).
        mutations / queries: GraphQL root field names.
        update_entity_fields / update_link_fields: Input fields of the update inputs.
        entity_properties_updatable / link_properties_updatable: ``properties`` in the
            update inputs (``False`` on 0.2.18: properties are create-only).
        writer_checked: A writer credential was verified (C4).
        writer_principal: Principal id (uuid) or ``single-user-api-key``; never a token.
        writer_roles: Role names from ``whoami``.
        read_probe: ``ok``, ``empty`` or ``unsigned`` (C6).
        quirks: :data:`KNOWN_QUIRKS` for this version.
        capability_hash: SHA-256 over version and schema surface.
    """

    url: str
    library_version: str
    api_version: Any
    authentication_required: bool
    providers: tuple[str, ...]
    mutations: tuple[str, ...]
    queries: tuple[str, ...]
    update_entity_fields: tuple[str, ...]
    update_link_fields: tuple[str, ...]
    entity_properties_updatable: bool
    link_properties_updatable: bool
    writer_checked: bool = False
    writer_principal: str | None = None
    writer_roles: tuple[str, ...] = ()
    read_probe: str = "unsigned"
    quirks: tuple[str, ...] = ()
    capability_hash: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (contains no credential)."""
        return asdict(self)


def _auth_header(credential: str | None, scheme: str) -> dict[str, str]:
    return {"Authorization": f"{scheme} {credential}"} if credential else {}


class _Http:
    """Tiny wrapper so every request has a timeout and TLS setting, and errors map to codes."""

    def __init__(self, base: str, *, session: Any = None, timeout: float = 10.0, verify: Any = True):
        if session is None:
            import requests

            session = requests.Session()
            session.trust_env = False  # no ambient proxies / netrc credentials
        self.base = base
        self.session = session
        self.timeout = timeout
        self.verify = verify

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        import requests

        try:
            resp = self.session.request(
                method, f"{self.base}{path}", timeout=self.timeout, verify=self.verify, **kwargs
            )
        except requests.RequestException as exc:
            raise TiledTargetUnavailable(
                f"Tiled at {self.base} is unreachable: {type(exc).__name__}", detail={"url": self.base}
            ) from None
        if resp.status_code >= 500:
            raise TiledTargetUnavailable(
                f"Tiled at {self.base} returned HTTP {resp.status_code} for {path}",
                detail={"url": self.base, "status": resp.status_code},
            )
        return resp

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None,
                headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        resp = self.request("POST", GRAPHQL_PATH, json={"query": query, "variables": dict(variables or {})},
                            headers=dict(headers or {}))
        if resp.status_code == 404:
            raise TiledGraphUnavailable(f"{self.base}{GRAPHQL_PATH} is not served (graph not enabled?)")
        if resp.status_code == 401:
            raise TiledWriterUnauthorized(f"Tiled at {self.base} rejected the credential (HTTP 401)")
        try:
            body = resp.json()
        except ValueError:
            raise TiledGraphUnavailable(f"{self.base}{GRAPHQL_PATH} did not return JSON") from None
        if not isinstance(body, dict):
            raise TiledGraphUnavailable(f"{self.base}{GRAPHQL_PATH} returned an unexpected body")
        return body


_INTROSPECT = """query($n: String!) { __type(name: $n) { fields { name } inputFields { name } } }"""


def _type_names(http: _Http, name: str, headers: Mapping[str, str]) -> tuple[str, ...]:
    body = http.graphql(_INTROSPECT, {"n": name}, headers)
    node = (body.get("data") or {}).get("__type") or {}
    rows = node.get("fields") or node.get("inputFields") or []
    return tuple(sorted(str(row.get("name")) for row in rows if isinstance(row, dict)))


def check_health(base_url: str, *, session: Any = None, timeout: float = 2.0) -> bool:
    """``GET /healthz`` returns ``{"status": "ready"}`` (process liveness only)."""
    http = _Http(normalize_base_url(base_url), session=session, timeout=timeout)
    try:
        resp = http.request("GET", HEALTH_PATH)
    except TiledTargetUnavailable:
        return False
    try:
        return resp.status_code == 200 and resp.json().get("status") == "ready"
    except ValueError:
        return False


def check_capabilities(
    base_url: str,
    *,
    expected_version: str = PINNED_TILED_VERSION,
    credential: str | None = None,
    scheme: str = "Apikey",
    require_writer_role: str | None = None,
    session: Any = None,
    timeout: float = 10.0,
    verify: Any = True,
) -> CapabilityReport:
    """Run C1-C4 and C6 against ``base_url`` (read-only).

    Args:
        base_url: Tiled base URL.
        expected_version: Exact required ``library_version``.
        credential: Writer API key or bearer token; ``None`` skips C4 and probes unsigned.
        scheme: ``Apikey`` or ``Bearer``.
        require_writer_role: Role the writer principal must hold (multi-user servers).
        session: Optional ``requests.Session``-compatible object (tests).
        timeout: Per-request timeout.
        verify: ``requests`` TLS verify value (CA bundle path or bool).

    Returns:
        The capability snapshot.

    Raises:
        TiledTargetUnavailable, TiledUnsupportedVersion, TiledGraphUnavailable,
        TiledCapabilityMissing, TiledWriterUnauthorized.
    """
    base = normalize_base_url(base_url)
    http = _Http(base, session=session, timeout=timeout, verify=verify)
    headers = _auth_header(credential, scheme)

    # C1-C3 run unsigned: the service description and schema are public, and a bad
    # credential must surface as writer_unauthorized (C4), not as a version error.
    resp = http.request("GET", ABOUT_PATH)
    try:
        about = resp.json() if resp.status_code == 200 else None
    except ValueError:
        about = None
    if not isinstance(about, dict) or "library_version" not in about:
        raise TiledUnsupportedVersion(
            f"{base}{ABOUT_PATH} did not return a Tiled service description (HTTP {resp.status_code})",
            detail={"url": base, "status": resp.status_code},
        )
    version = str(about.get("library_version"))
    if version != expected_version:
        raise TiledUnsupportedVersion(
            f"Tiled at {base} runs {version}; F2W requires exactly {expected_version}",
            detail={"url": base, "found": version, "expected": expected_version},
        )
    authn = about.get("authentication") or {}
    providers = tuple(str(p.get("provider")) for p in authn.get("providers") or [] if isinstance(p, dict))

    body = http.graphql("{ __typename }")
    if (body.get("data") or {}).get("__typename") != "Query":
        raise TiledGraphUnavailable(f"{base}{GRAPHQL_PATH} is not the Tiled graph schema")

    mutations = _type_names(http, "Mutation", {})
    queries = _type_names(http, "Query", {})
    missing = [m for m in REQUIRED_MUTATIONS if m not in mutations] + [
        q for q in REQUIRED_QUERIES if q not in queries
    ]
    if missing:
        raise TiledCapabilityMissing(
            f"Tiled at {base} lacks graph operations: {', '.join(missing)}",
            code=f"capability_missing:{missing[0]}",
            detail={"url": base, "missing": missing},
        )
    update_entity = _type_names(http, "UpdateEntityInput", {})
    update_link = _type_names(http, "UpdateLinkInput", {})

    writer_principal: str | None = None
    writer_roles: tuple[str, ...] = ()
    if credential:
        writer_principal, writer_roles = _check_writer(http, headers, providers, require_writer_role)

    probe = http.graphql("{ entities(limit: 1) { id } }", headers=headers)
    rows = (probe.get("data") or {}).get("entities")
    if probe.get("errors") or rows is None:
        read_probe = "unsigned"
    else:
        read_probe = "ok" if rows else "empty"

    surface = {
        "version": version,
        "mutations": mutations,
        "queries": queries,
        "update_entity": update_entity,
        "update_link": update_link,
    }
    digest = hashlib.sha256(json.dumps(surface, sort_keys=True).encode()).hexdigest()
    return CapabilityReport(
        url=base,
        library_version=version,
        api_version=about.get("api_version"),
        authentication_required=bool(authn.get("required")),
        providers=providers,
        mutations=mutations,
        queries=queries,
        update_entity_fields=update_entity,
        update_link_fields=update_link,
        entity_properties_updatable="properties" in update_entity,
        link_properties_updatable="properties" in update_link,
        writer_checked=bool(credential),
        writer_principal=writer_principal,
        writer_roles=writer_roles,
        read_probe=read_probe,
        quirks=KNOWN_QUIRKS.get(version, ()),
        capability_hash=digest,
    )


def _check_writer(http: _Http, headers: Mapping[str, str], providers: tuple[str, ...],
                  require_role: str | None) -> tuple[str, tuple[str, ...]]:
    if not providers:
        # Single-user API key mode: tiled 0.2.18 mounts no /auth routes (whoami is 404).
        resp = http.request("GET", METADATA_ROOT_PATH, headers=dict(headers))
        if resp.status_code in (401, 403):
            raise TiledWriterUnauthorized(f"Tiled at {http.base} rejected the writer API key")
        if require_role:
            raise TiledWriterUnauthorized(
                f"Tiled at {http.base} has no principals (single-user mode); "
                f"role {require_role!r} cannot be verified"
            )
        return "single-user-api-key", ()
    resp = http.request("GET", WHOAMI_PATH, headers=dict(headers))
    try:
        principal = resp.json() if resp.status_code == 200 else None
    except ValueError:
        principal = None
    if not isinstance(principal, dict) or not principal.get("uuid"):
        raise TiledWriterUnauthorized(
            f"Tiled at {http.base} did not resolve the writer credential to a principal (HTTP {resp.status_code})"
        )
    roles = tuple(sorted(str(r.get("name")) for r in principal.get("roles") or [] if isinstance(r, dict)))
    if require_role and require_role not in roles:
        raise TiledWriterUnauthorized(
            f"Writer principal {principal['uuid']} lacks the {require_role!r} role on {http.base}",
            detail={"principal": principal["uuid"], "roles": list(roles)},
        )
    return str(principal["uuid"]), roles


def connect_external(cfg: ExternalTiledConfig, *, session: Any = None) -> CapabilityReport:
    """Connect to an external (central or fixture) Tiled: check it, never manage it.

    Reads the writer credential from ``cfg.writer_secret_file`` at call time. On an
    outage this raises :class:`TiledTargetUnavailable`; it never starts, stops, upgrades or
    falls back to any other server.
    """
    credential = read_secret_file(cfg.writer_secret_file) if cfg.writer_secret_file else None
    return check_capabilities(
        cfg.url,
        expected_version=cfg.expected_version,
        credential=credential,
        scheme=cfg.writer_scheme,
        require_writer_role=cfg.require_writer_role,
        session=session,
        timeout=cfg.timeout,
        verify=str(cfg.ca_bundle) if cfg.ca_bundle else True,
    )
