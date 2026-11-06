"""Live Tiled Graph lookup and probe for experiment identity (ESAF / Proposal / Sample / BlueskyRun).

Real port of the legacy ``app/modules/tiled_graph.py`` (T17B, H8; owner decision 2026-11-04).
The retained browser contract (class (r), ``f2w_agent/api.py``) pages ``POST {TILED_URI}/api/graphql``
using the cookbook entity types for experiment-identity questions, and the Settings page probes the
same endpoint. When Tiled is unreachable, unsigned or empty the chat falls back to the JSON KG; the
catalog ``tiled.client`` walk is an optional supplement for scan/run questions when GraphQL has no
``BlueskyRun`` entities yet.

Wire behaviour is that of the legacy module: the status strings (``disabled``, ``invalid_uri``,
``unreachable``, ``unsigned``, ``empty``, ``ok``, ``skipped``), the Settings snapshot keys, the node
shape and the error texts are unchanged.

Credentials: the lookup only ever sends the process's own service key, resolved lazily by a
:data:`CredentialProvider` (default :func:`process_api_key`, the ``TILED_API_KEY`` environment value
the retained contract documents); a user's token is never used. The key is never stored on the
client in a loggable form, never appears in ``repr`` and is redacted from error texts by
:func:`_safe_error`. The canonical read path (:mod:`.tiled_client`) keeps its runtime-config
credential; this module is the legacy contract's lookup, not a second GraphPort.

Importing this module has no side effects (no environment reads, no network, no caches).

.. note:: **2026-09-23 — sim nodes removed from the JSON KG fallback**

    As of ``matkg_bl1101_v8.json`` ESAF/Proposal/Sample/BlueskyRun nodes are intentionally absent
    from the JSON KG; when Tiled is unreachable or disabled, experiment-identity queries return
    empty results. Tiled Graph (``storage/tiled_config.yml``) is the sole source of identity data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import logging
import os
import re
from types import SimpleNamespace
from typing import Protocol
from urllib.parse import urlparse, urlunparse

import requests

from app.modules.contracts.json_types import JsonObject, JsonValue, Untyped
from app.modules.f2w_agent.multi_kg import ScoredHit

from .tiled_client import GRAPHQL_PATH, graphql_endpoint_for

LOGGER = logging.getLogger("tiled_graph")

TILED_GRAPH_ID = "tiled"
TILED_GRAPH_LABEL = "Tiled Graph"
DEFAULT_TILED_URI = "http://127.0.0.1:8765"
_PROBE_TYPENAME = "query { __typename }"
_PROBE_ENTITIES = "query { entities(limit: 1) { name entityType } }"
PROBE_TIMEOUT = 3.0
ENTITY_TYPES = ("ESAF", "Proposal", "Sample", "BlueskyRun")
PAGE_SIZE = 100
MAX_PAGES = 8
REQUEST_TIMEOUT = 8.0
CITE_FOCUS_HOPS = 3
#: Environment variable holding the process's own Tiled service key (the retained contract's source).
API_KEY_ENV = "TILED_API_KEY"
#: Environment variable selecting the ``Authorization`` scheme; ``Apikey`` when unset.
API_KEY_SCHEME_ENV = "TILED_API_KEY_SCHEME"
DEFAULT_SCHEME = "Apikey"
_IDENTITY_PROP_KEYS = (
    "esaf_number",
    "esaf_id",
    "esaf",
    "scientist",
    "co_scientist",
    "proposal_code",
    "proposal_id",
    "proposal",
    "sample_code",
    "sample_id",
    "sample_name",
    "sample",
    "plan_name",
    "uid",
    "scan_id",
    "title",
    "description",
)

_ESAF_NUMBER_RE = re.compile(r"\b(20\d{2}[-_]\d{4,6})\b", re.IGNORECASE)
_PROPOSAL_CODE_RE = re.compile(r"\b(P20\d{2}\d{4,6}(?:-\d{2})?)\b", re.IGNORECASE)
_IDENTITY_RE = re.compile(
    r"\b("
    r"esafs?|proposals?|samples?|scans?|runs?|bluesky(?:\s*run)?|"
    r"catalog|tiled|my\s+runs?|last\s+scan|this\s+run|runcard"
    r")\b",
    re.IGNORECASE,
)
_TYPE_ALIASES = {
    "ESAF": ("esaf", "esafs"),
    "Proposal": ("proposal", "proposals"),
    "Sample": ("sample", "samples"),
    "BlueskyRun": ("scan", "scans", "run", "runs", "bluesky", "blueskyrun", "uid"),
}

_PAGE_QUERY = """
query PageTiledEntities($entityType: String, $limit: Int!, $offset: Int!) {
  entities(entityType: $entityType, limit: $limit, offset: $offset) {
    id
    entityType
    name
    uri
    nodeId
    properties
    outgoingLinks(limit: 20) {
      predicate
      object { id name entityType uri }
    }
  }
}
"""

_PAGE_QUERY_NO_NODEID = """
query PageTiledEntities($entityType: String, $limit: Int!, $offset: Int!) {
  entities(entityType: $entityType, limit: $limit, offset: $offset) {
    id
    entityType
    name
    uri
    properties
    outgoingLinks(limit: 20) {
      predicate
      object { id name entityType uri }
    }
  }
}
"""

#: Zero-argument callable returning the service credential to send, or ``None``/"" for no header.
CredentialProvider = Callable[[], str | None]


# --------------------------------------------------------------------------- environment


def _env(name: str, default: str = "") -> str:
    """Return a stripped environment variable, or *default* when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip()


def _truthy(raw: str | None) -> bool | None:
    """Parse a truthy/falsy env string; empty or unknown values return None."""
    if raw is None:
        return None
    cleaned = str(raw).strip().lower()
    if not cleaned:
        return None
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return None


def process_api_key() -> str:
    """The process's own Tiled service key (``TILED_API_KEY``), or "" when unset.

    This is the only credential the legacy lookup ever sends; it is read at call time, never at
    import, and callers must not log the value.
    """
    return _env(API_KEY_ENV)


def tiled_api_key_set() -> bool:
    """True when ``TILED_API_KEY`` is a non-empty environment value."""
    return bool(process_api_key())


# --------------------------------------------------------------------------- URIs


def normalize_tiled_uri(value: str | None, *, default: str = DEFAULT_TILED_URI) -> str:
    """Strip a trailing GraphQL path and return a scheme+host Tiled base URI.

    Raises:
        ValueError: when *value* is not an http(s) URL with a host.
    """
    raw = str(value or "").strip() or default
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Tiled URI must be an http(s) URL, for example http://127.0.0.1:8000")
    path = parsed.path.rstrip("/")
    if path.endswith(GRAPHQL_PATH):
        path = path[: -len(GRAPHQL_PATH)]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def is_als_production_tiled_host(host: str | None) -> bool:
    """True when *host* is als.lbl.gov or a subdomain (never used as chat Tiled)."""
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    return hostname == "als.lbl.gov" or hostname.endswith(".als.lbl.gov")


def coerce_local_tiled_uri(
    value: str | None,
    *,
    default: str = DEFAULT_TILED_URI,
) -> str:
    """Keep chat/settings on a local catalog; never follow ALS production hosts."""
    try:
        normalized = normalize_tiled_uri(value, default=default)
    except ValueError:
        return normalize_tiled_uri(default)
    host = urlparse(normalized).hostname
    if is_als_production_tiled_host(host):
        LOGGER.warning("Ignoring ALS production Tiled host %s; using %s", host, default)
        return normalize_tiled_uri(default)
    return normalized


def graphql_url(base_uri: str) -> str:
    """Join *base_uri* with the Tiled GraphQL path."""
    return graphql_endpoint_for(normalize_tiled_uri(base_uri))


def auth_headers(api_key: str | None = None) -> dict[str, str]:
    """JSON headers plus optional Tiled ``Authorization`` from *api_key* or the process key.

    Args:
        api_key: Explicit credential; ``None`` means :func:`process_api_key`, "" means no header.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (api_key if api_key is not None else process_api_key()).strip()
    if key:
        scheme = _env(API_KEY_SCHEME_ENV, DEFAULT_SCHEME) or DEFAULT_SCHEME
        headers["Authorization"] = f"{scheme} {key}"
    return headers


# --------------------------------------------------------------------------- configuration


@dataclass
class TiledGraphConfig:
    """Resolved live-Tiled settings: enabled flag, URI, and whether an API key is set."""

    enabled: bool
    uri: str
    api_key_set: bool
    uri_error: str | None = None

    def snapshot(self) -> JsonObject:
        """JSON-serializable status dict for Settings and retrieval metadata."""
        status = "disabled"
        error = self.uri_error
        if self.enabled:
            if self.uri_error:
                status = "invalid_uri"
            elif not self.api_key_set:
                status = "unsigned"
            else:
                status = "configured"
        return {
            "use_live_tiled": self.enabled,
            "tiled_uri": self.uri,
            "tiled_api_key_set": self.api_key_set,
            "tiled_status": status,
            "tiled_error": error,
        }


def load_tiled_config(
    *,
    uri: str | None = None,
    enabled: bool | None = None,
) -> TiledGraphConfig:
    """Resolve enabled flag and local URI from arguments and environment."""
    env_enabled = _truthy(_env("F2W_LIVE_TILED") or _env("TILED_GRAPH_ENABLED"))
    if enabled is None:
        if env_enabled is not None:
            enabled = env_enabled
        else:
            enabled = bool(_env("TILED_URI"))
    raw_uri = uri if uri is not None else (_env("TILED_URI") or DEFAULT_TILED_URI)
    try:
        normalized = coerce_local_tiled_uri(raw_uri)
        uri_error = None
    except ValueError as exc:
        normalized = DEFAULT_TILED_URI
        uri_error = str(exc)
    return TiledGraphConfig(
        enabled=bool(enabled),
        uri=normalized,
        api_key_set=tiled_api_key_set(),
        uri_error=uri_error,
    )


# --------------------------------------------------------------------------- transport


class TiledGraphQLError(RuntimeError):
    """Raised when the Tiled GraphQL endpoint returns an ``errors`` list.

    Carries the raw list of error dicts so callers can inspect them structurally instead of
    pattern-matching on the string representation.
    """

    def __init__(self, errors: list[JsonValue]) -> None:
        """Store the GraphQL ``errors`` list and a readable message."""
        super().__init__(f"Tiled GraphQL error: {errors}")
        self.errors: list[JsonValue] = errors

    def mentions_field(self, field: str) -> bool:
        """Return True if any error's message or path mentions *field*."""
        for err in self.errors:
            if not isinstance(err, dict):
                continue
            if field in str(err.get("message") or ""):
                return True
            for path_item in err.get("path") or []:
                if str(path_item) == field:
                    return True
        return False


class _PostSession(Protocol):
    """The slice of ``requests`` (module or ``Session``) the client uses."""

    def post(self, url: str, *, json: JsonObject, headers: Mapping[str, str],
             timeout: float) -> requests.Response: ...


class LookupTransport(Protocol):
    """Anything that answers a GraphQL document with its ``data`` object."""

    def query(self, document: str, variables: Mapping[str, JsonValue] | None = None) -> JsonObject:
        """Return the ``data`` object for *document*; raise on transport or GraphQL errors."""
        ...


class TiledLookupClient:
    """GraphQL reads against one Tiled base URI with the legacy lookup's error semantics.

    Modelled on :class:`.tiled_client.TiledGraphClient`: the credential is a provider evaluated per
    request, never stored as a header and never part of ``repr``. Unlike the canonical client, the
    target is a chat/settings URI (``coerce_local_tiled_uri`` keeps it off ALS production), HTTP
    failures surface as ``requests`` exceptions and GraphQL errors as :class:`TiledGraphQLError`,
    because the retained contract's status mapping and error texts are built on those.

    Args:
        base_uri: Tiled base URI (a trailing ``/api/graphql`` is tolerated).
        credential: Provider of the service key; ``None`` uses :func:`process_api_key`.
        timeout: Per-request timeout in seconds.
        session: ``requests``-compatible object with ``post``; the ``requests`` module when ``None``.
    """

    def __init__(self, base_uri: str, *, credential: CredentialProvider | None = None,
                 timeout: float = REQUEST_TIMEOUT, session: _PostSession | None = None) -> None:
        self.endpoint = graphql_url(base_uri)
        self._credential: CredentialProvider = credential if credential is not None else process_api_key
        self._timeout = timeout
        self._session: _PostSession = session if session is not None else requests

    def __repr__(self) -> str:  # never show the credential
        return f"TiledLookupClient({self.endpoint!r})"

    def query(self, document: str, variables: Mapping[str, JsonValue] | None = None) -> JsonObject:
        """POST *document* and return ``data``.

        Raises:
            requests.HTTPError: for a non-2xx answer (``raise_for_status``).
            requests.RequestException: for transport failures and timeouts.
            TiledGraphQLError: when the body carries an ``errors`` list.
        """
        resp = self._session.post(
            self.endpoint,
            json={"query": document, "variables": dict(variables or {})},
            headers=auth_headers(self._credential()),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise TiledGraphQLError(body["errors"])
        data = body.get("data") or {}
        return dict(data) if isinstance(data, dict) else {}


def tiled_graphql(
    query: str,
    variables: Mapping[str, JsonValue] | None = None,
    *,
    uri: str | None = None,
    api_key: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> JsonObject:
    """POST *query* to Tiled GraphQL and return ``data``, or raise :class:`TiledGraphQLError`.

    Args:
        query: GraphQL document.
        variables: Operation variables.
        uri: Tiled base URI; ``TILED_URI`` or :data:`DEFAULT_TILED_URI` when ``None``.
        api_key: Explicit credential; ``None`` means the process key, "" means unsigned.
        timeout: Request timeout in seconds.
    """
    client = TiledLookupClient(uri or _env("TILED_URI") or DEFAULT_TILED_URI,
                               credential=lambda: api_key, timeout=timeout)
    return client.query(query, variables)


class _FunctionTransport:
    """Default :class:`LookupTransport`: routes through the module-level :func:`tiled_graphql`.

    Going through the module function (looked up at call time) is what lets the retained tests and
    the simulator substitute the GraphQL call in one place.
    """

    def __init__(self, uri: str, api_key: str | None, timeout: float = REQUEST_TIMEOUT) -> None:
        self._uri = uri
        self._api_key = api_key
        self._timeout = timeout

    def query(self, document: str, variables: Mapping[str, JsonValue] | None = None) -> JsonObject:
        """Delegate to :func:`tiled_graphql` with this transport's target and credential."""
        return tiled_graphql(document, variables, uri=self._uri, api_key=self._api_key, timeout=self._timeout)


def _transport_for(uri: str, api_key: str | None, client: LookupTransport | None,
                   timeout: float = REQUEST_TIMEOUT) -> LookupTransport:
    """*client* when given, else the default transport for *uri*."""
    return client if client is not None else _FunctionTransport(uri, api_key, timeout)


def _safe_error(exc: BaseException) -> str:
    """Format *exc* for logs/UI with the process Tiled API key redacted."""
    text = f"{type(exc).__name__}: {exc}"
    key = process_api_key()
    if key and key in text:
        text = text.replace(key, "<redacted>")
    return text[:300]


# --------------------------------------------------------------------------- probe


def probe_tiled_status(
    *,
    uri: str | None = None,
    enabled: bool | None = None,
    timeout: float = PROBE_TIMEOUT,
    client: LookupTransport | None = None,
) -> JsonObject:
    """Live GraphQL probe for Settings: ok / unsigned / unreachable / disabled.

    Args:
        uri: Tiled base URI override.
        enabled: Live-Tiled flag override.
        timeout: Probe timeout in seconds.
        client: Transport to probe through; the default posts to *uri* with the process key.
    """
    cfg = load_tiled_config(uri=uri, enabled=enabled)
    snap = cfg.snapshot()
    if not cfg.enabled:
        snap["tiled_status"] = "disabled"
        return snap
    if cfg.uri_error:
        snap["tiled_status"] = "invalid_uri"
        return snap
    transport = _transport_for(cfg.uri, None, client, timeout)
    try:
        transport.query(_PROBE_TYPENAME)
    except Exception as exc:
        snap["tiled_status"] = "unreachable"
        snap["tiled_error"] = _safe_error(exc)
        return snap
    try:
        data = transport.query(_PROBE_ENTITIES)
        rows = data.get("entities") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            snap["tiled_status"] = "ok"
            snap["tiled_error"] = None
            return snap
        if not cfg.api_key_set:
            snap["tiled_status"] = "unsigned"
            snap["tiled_error"] = (
                "No TILED_API_KEY in .env. GraphQL is reachable but identity lists "
                "are empty — unsigned writes are denied, and this directory catalog "
                "has no ESAF/Proposal/Sample graph until entities are created. "
                "Experiment-identity queries will return empty results until Tiled is populated. "
                "Never paste a key into Settings."
            )
            return snap
        snap["tiled_status"] = "empty"
        snap["tiled_error"] = (
            "API key is loaded. Local Tiled GraphQL has no ESAF/Proposal/Sample/"
            "BlueskyRun entities yet (this catalog is a directory of datasets, not "
            "an ESAF graph). Experiment-identity queries will return empty results "
            "until Tiled is populated (sim nodes were removed from the JSON KG in v8)."
        )
        return snap
    except Exception as exc:
        err = _safe_error(exc)
        if not cfg.api_key_set:
            snap["tiled_status"] = "unsigned"
            snap["tiled_error"] = err
        else:
            snap["tiled_status"] = "unreachable"
            snap["tiled_error"] = err
        return snap


# --------------------------------------------------------------------------- lookup graph


@dataclass
class TiledLookupResult:
    """Hits and overlay graph from a live Tiled identity lookup."""

    hits: list[ScoredHit] = field(default_factory=list)
    graph: TiledLookupGraph | None = None
    status: str = "disabled"
    error: str | None = None
    fallback: str = "json_kg"
    entity_counts: dict[str, int] = field(default_factory=dict)
    source: str = "none"

    def meta(self) -> JsonObject:
        """Compact lookup metadata attached to agent responses."""
        return {
            "enabled": self.status not in {"disabled"},
            "used": bool(self.hits) and self.source != "none",
            "status": self.status,
            "error": self.error,
            "fallback": self.fallback if not self.hits else None,
            "hits": len(self.hits),
            "entity_counts": dict(self.entity_counts),
            "source": self.source,
            "graph_id": TILED_GRAPH_ID if self.hits else None,
        }


class TiledLookupGraph:
    """Minimal KG surface so retrieval can render Tiled entities like JSON nodes."""

    graph_id = TILED_GRAPH_ID
    graph_label = TILED_GRAPH_LABEL
    graph_path = "tiled://graphql"
    graph_source_requested = "tiled"
    graph_source_used = "tiled"
    retrieval_backend = "graphql"

    def __init__(self, nodes: Mapping[str, JsonObject] | None = None) -> None:
        """Store *nodes* by id; outgoing edges start empty."""
        self.nodes: dict[str, JsonObject] = dict(nodes or {})
        self.out_edges: dict[str, list[JsonObject]] = {}

    def add_node(self, node: JsonObject) -> None:
        """Index *node* by its ``id``; ignore records without an id."""
        nid = str(node.get("id") or "")
        if not nid:
            return
        self.nodes[nid] = node

    def add_edge(self, subject: str, predicate: str, obj: str) -> None:
        """Append a directed link from *subject* to *obj*."""
        self.out_edges.setdefault(subject, []).append(
            {"subject": subject, "predicate": predicate, "object": obj, "has_evidence": True}
        )

    def semantic_search(self, q: str, topk: int = 12) -> list[SimpleNamespace]:
        """Rank Tiled nodes by lexical match to *q*; return ``(id, score)`` namespaces."""
        tokens = _query_tokens(q)
        scored: list[tuple[float, str]] = []
        for nid, node in self.nodes.items():
            score = _match_score(q, node, tokens)
            if score > 0:
                scored.append((score, nid))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [SimpleNamespace(id=nid, score=score) for score, nid in scored[:topk]]

    def build_context(
        self,
        nodes: Sequence[object],
        include_structured: bool = True,
        char_budget: int = 8000,
        hint_terms: Sequence[str] | None = None,
        include_pdf_snippets: bool = False,
    ) -> str:
        """Render selected Tiled nodes (and optional triples) into judge context.

        Args:
            nodes: Node-like objects with ``id`` (and optionally ``name``, ``category``,
                ``description``) attributes.
            include_structured: Render the triples between the selected nodes first.
            char_budget: Stop adding node blocks past this many characters.
            hint_terms: Accepted for signature parity with the JSON KG; unused.
            include_pdf_snippets: Accepted for signature parity with the JSON KG; unused.
        """
        parts: list[str] = [
            "Live Tiled GraphQL identity nodes (ESAF, Proposal, Sample, BlueskyRun). "
            "Cite the entity name, for example [KG:tiled: ESAF 2026-00043]. "
            "Do not cite cookbook query names."
        ]
        chars = len(parts[0])
        rendered = {str(getattr(ni, "id", "")) for ni in nodes}
        if include_structured:
            triples: list[str] = []
            for ni in nodes:
                nid = str(getattr(ni, "id", "") or "")
                for edge in self.out_edges.get(nid, []):
                    tgt = str(edge.get("object") or "")
                    if tgt not in rendered:
                        continue
                    triples.append(
                        f"({self.nodes.get(nid, {}).get('name', nid)}) "
                        f"-[{str(edge.get('predicate') or '').split(':')[-1]}]-> "
                        f"({self.nodes.get(tgt, {}).get('name', tgt)})"
                    )
            if triples:
                blk = "Structured_KG_Facts:\n" + "\n".join(triples[:40])
                parts.append(blk)
                chars += len(blk)
        for ni in nodes:
            nid = str(getattr(ni, "id", "") or "")
            raw = self.nodes.get(nid) or {}
            name = str(getattr(ni, "name", None) or raw.get("name") or nid)
            category = str(getattr(ni, "category", None) or raw.get("category") or "?")
            desc = str(getattr(ni, "description", None) or raw.get("description") or "")
            block = [f"## {name} ({category})", f"Tiled_Source: {GRAPHQL_PATH}"]
            if desc:
                block.append(f"Description: {desc}")
            node_id = raw.get("nodeId")
            if node_id:
                block.append(f"nodeId: {node_id}")
            uri = raw.get("uri")
            if uri:
                block.append(f"uri: {uri}")
            text = "\n".join(block)
            if chars + len(text) + 2 > char_budget:
                break
            parts.append(text)
            chars += len(text) + 2
        return "\n\n".join(parts)


# --------------------------------------------------------------------------- question analysis


def is_experiment_identity_question(question: str) -> bool:
    """True when *question* asks about ESAF, proposal, sample, scan, or catalog ids."""
    text = str(question or "").strip()
    if not text:
        return False
    if _ESAF_NUMBER_RE.search(text) or _PROPOSAL_CODE_RE.search(text):
        return True
    return bool(_IDENTITY_RE.search(text))


def _query_tokens(question: str) -> set[str]:
    """Lowercased alphanumeric tokens of length >= 3 from *question*."""
    return {tok for tok in re.findall(r"[a-z0-9]+", str(question or "").lower()) if len(tok) >= 3}


def _stem(token: str) -> str:
    """Naive English plural stem used for entity-type alias matching."""
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def requested_entity_types(question: str) -> list[str]:
    """Entity types named in *question*, or all cookbook types if none match."""
    tokens = {_stem(tok) for tok in _query_tokens(question)}
    matched: list[str] = []
    for entity_type, aliases in _TYPE_ALIASES.items():
        if any(_stem(alias) in tokens for alias in aliases):
            matched.append(entity_type)
    if _ESAF_NUMBER_RE.search(question or "") and "ESAF" not in matched:
        matched.insert(0, "ESAF")
    if _PROPOSAL_CODE_RE.search(question or "") and "Proposal" not in matched:
        matched.insert(0, "Proposal")
    if not matched:
        return list(ENTITY_TYPES)
    return matched


# --------------------------------------------------------------------------- node conversion


def _flatten_props(value: object) -> str:
    """Recursively flatten a nested property dict/list/scalar to a single string."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_props(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_props(v) for v in value)
    return str(value)


def _normalize_identity(value: object) -> str:
    """Collapse whitespace and punctuation so ESAF/proposal strings compare loosely."""
    return re.sub(r"[\s_\-:]+", " ", str(value or "").strip().lower())


def tiled_node_aliases(nid: str, node: JsonObject) -> set[str]:
    """Match GraphQL cites like ESAF-2026-00043 onto uri / name / identity props."""
    raw_props = node.get("properties")
    props: JsonObject = raw_props if isinstance(raw_props, dict) else {}
    aliases: set[str] = set()
    values = [
        nid,
        node.get("name"),
        node.get("uri"),
        node.get("graphql_id"),
        node.get("nodeId"),
        node.get("esaf_number"),
        node.get("proposal_code"),
        node.get("sample_code"),
        node.get("uid"),
        props.get("esaf_number"),
        props.get("proposal_code"),
        props.get("proposal_id"),
        props.get("sample_code"),
        props.get("uid"),
    ]
    for value in values:
        normalized = _normalize_identity(value)
        if len(normalized) >= 4:
            aliases.add(normalized)
    esaf = str(props.get("esaf_number") or node.get("esaf_number") or "")
    if esaf:
        aliases.add(_normalize_identity(f"ESAF {esaf}"))
        aliases.add(_normalize_identity(f"ESAF-{esaf}"))
    tail = str(nid).split(":")[-1]
    if len(_normalize_identity(tail)) >= 4:
        aliases.add(_normalize_identity(tail))
    return aliases


def tiled_lookup_candidates(node_id: str) -> list[str]:
    """Undo query-overlay prefixes like ``tiled:beamline:ESAF-2026-00043``."""
    raw = str(node_id or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for candidate in (raw,):
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    if raw.lower().startswith(f"{TILED_GRAPH_ID}:"):
        rest = raw[len(TILED_GRAPH_ID) + 1 :]
        if rest and rest not in seen:
            seen.add(rest)
            out.append(rest)
    parts = raw.split(":")
    if len(parts) >= 2:
        tail = ":".join(parts[-2:])
        if tail not in seen:
            seen.add(tail)
            out.append(tail)
        last = parts[-1]
        if last and last not in seen:
            seen.add(last)
            out.append(last)
    return out


def resolve_tiled_node_id(kg: object, node_id: str) -> str | None:
    """Map a citation or overlay id onto a node key in *kg*, if one exists."""
    nodes = getattr(kg, "nodes", {}) or {}
    if not isinstance(nodes, dict) or not node_id:
        return None
    for raw_id in tiled_lookup_candidates(node_id):
        if raw_id in nodes:
            return raw_id
        lowered = raw_id.lower()
        for nid in nodes:
            if str(nid).lower() == lowered:
                return str(nid)
        needle = _normalize_identity(raw_id)
        if len(needle) < 4:
            continue
        matches: list[str] = []
        for nid, node in nodes.items():
            if not isinstance(node, dict):
                continue
            if str(nid).startswith("tiled:count:"):
                continue
            if needle in tiled_node_aliases(str(nid), node):
                matches.append(str(nid))
        if not matches:
            continue
        typed = [
            nid for nid in matches
            if str((nodes.get(nid) or {}).get("category") or "") in ENTITY_TYPES
        ]
        return typed[0] if typed else matches[0]
    return None


def tiled_neighborhood_ids(
    kg: object,
    seeds: Sequence[str],
    hops: int = CITE_FOCUS_HOPS,
) -> list[str]:
    """Undirected BFS so cite-click can show ESAF -> proposal -> sample/scan."""
    nodes = getattr(kg, "nodes", {}) or {}
    if not isinstance(nodes, dict):
        return []
    resolved: list[str] = []
    seen_seeds: set[str] = set()
    for seed in seeds:
        nid = resolve_tiled_node_id(kg, str(seed))
        if not nid or nid in seen_seeds:
            continue
        seen_seeds.add(nid)
        resolved.append(nid)
    if not resolved:
        return []

    adj: dict[str, set[str]] = {}
    for source, outgoing in (getattr(kg, "out_edges", {}) or {}).items():
        if not isinstance(outgoing, list):
            continue
        for edge in outgoing:
            if not isinstance(edge, dict):
                continue
            target = str(edge.get("object") or edge.get("target") or "").strip()
            if not source or not target:
                continue
            adj.setdefault(str(source), set()).add(target)
            adj.setdefault(target, set()).add(str(source))

    seen = set(resolved)
    frontier = list(resolved)
    depth = max(0, int(hops))
    for _ in range(depth):
        nxt: list[str] = []
        for current in frontier:
            for neighbor in adj.get(current, ()):
                if neighbor in seen or neighbor not in nodes:
                    continue
                seen.add(neighbor)
                nxt.append(neighbor)
        frontier = nxt

    ordered = list(resolved)
    for nid in seen:
        if nid not in seen_seeds:
            ordered.append(nid)
            seen_seeds.add(nid)
    return ordered


def entity_to_node(entity: JsonObject) -> JsonObject:
    """Convert a Tiled GraphQL entity dict to the canonical KG node dict format."""
    raw_props = entity.get("properties")
    props: JsonObject = dict(raw_props) if isinstance(raw_props, dict) else {}
    entity_type = str(entity.get("entityType") or props.get("category") or "Thing")
    graphql_id = str(entity.get("id") or "")
    uri = str(entity.get("uri") or "").strip()
    nid = uri or (f"tiled:{entity_type}:{graphql_id}" if graphql_id else f"tiled:{entity_type}:unknown")
    name = str(entity.get("name") or props.get("name") or nid)
    proposal = props.get("proposal_code") or props.get("proposal_id")
    bits = [
        str(props.get("title") or "").strip(),
        str(props.get("description") or "").strip(),
        f"ESAF {props['esaf_number']}" if props.get("esaf_number") else "",
        f"proposal {proposal}" if proposal else "",
        f"sample {props['sample_code']}" if props.get("sample_code") else "",
        f"uid {props['uid']}" if props.get("uid") else "",
        f"scan_id {props['scan_id']}" if props.get("scan_id") else "",
        f"plan {props['plan_name']}" if props.get("plan_name") else "",
        f"scientist {props['scientist']}" if props.get("scientist") else "",
    ]
    description = "; ".join(bit for bit in bits if bit) or name
    node: JsonObject = {
        "id": nid,
        "name": name,
        "category": entity_type,
        "entityType": entity_type,
        "description": description,
        "uri": uri or None,
        "nodeId": entity.get("nodeId"),
        "source_papers": ["tiled_graphql"],
        "properties": props,
        "graphql_id": graphql_id or None,
        "haystack": " ".join(
            [
                nid,
                name,
                entity_type,
                description,
                uri,
                str(entity.get("nodeId") or ""),
                _flatten_props(props),
            ]
        ).lower(),
    }
    for key in _IDENTITY_PROP_KEYS:
        value = props.get(key)
        if value not in (None, "", [], {}):
            node[key] = value
    return node


def _match_score(question: str, node: JsonObject, tokens: Iterable[str]) -> float:
    """Return a float relevance score for *node* against *question* and pre-tokenised *tokens*."""
    hay = str(node.get("haystack") or f"{node.get('name', '')} {node.get('description', '')}").lower()
    name = str(node.get("name") or "").lower()
    q = str(question or "").strip().lower()
    score = 0.0
    esaf = _ESAF_NUMBER_RE.search(question or "")
    if esaf and esaf.group(1).lower().replace("_", "-") in hay.replace("_", "-"):
        score += 2.0
    prop = _PROPOSAL_CODE_RE.search(question or "")
    if prop and prop.group(1).lower() in hay:
        score += 1.8
    if q and q in name:
        score += 1.6
    token_hits = 0
    for tok in tokens:
        if tok in hay or _stem(tok) in hay:
            token_hits += 1
        if _stem(tok) in {t.lower() for t in _TYPE_ALIASES.get(str(node.get("category") or ""), ())}:
            token_hits += 1
    if token_hits:
        score += min(1.2, 0.25 * token_hits)
    if str(node.get("id") or "").startswith("tiled:count:"):
        score += 1.4
    return score


# --------------------------------------------------------------------------- paging and assembly


def _page_entities(
    entity_type: str,
    *,
    uri: str,
    api_key: str | None,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    transport: LookupTransport | None = None,
) -> list[JsonObject]:
    """Page all GraphQL entities of *entity_type*, falling back if ``nodeId`` is unsupported.

    Paging stops after *max_pages* pages (OP07: counts are capped at ``PAGE_SIZE * MAX_PAGES``
    per type, as in the baseline).
    """
    gql = _transport_for(uri, api_key, transport)
    rows: list[JsonObject] = []
    query = _PAGE_QUERY
    offset = 0
    for _ in range(max_pages):
        variables: JsonObject = {"entityType": entity_type, "limit": page_size, "offset": offset}
        try:
            data = gql.query(query, variables)
        except TiledGraphQLError as exc:
            if query is _PAGE_QUERY and exc.mentions_field("nodeId"):
                query = _PAGE_QUERY_NO_NODEID
                data = gql.query(query, variables)
            else:
                raise
        batch = data.get("entities") or []
        if not isinstance(batch, list):
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _count_node(entity_type: str, count: int, source: str) -> JsonObject:
    """Synthetic inventory node so "how many ESAFs" questions have a match."""
    return {
        "id": f"tiled:count:{entity_type}",
        "name": f"Tiled {entity_type} inventory",
        "category": entity_type,
        "description": (
            f"Live Tiled {source} currently lists {count} {entity_type} "
            f"entit{'y' if count == 1 else 'ies'}."
        ),
        "source_papers": ["tiled_graphql"],
        "haystack": f"how many {entity_type.lower()}s count inventory {count}",
    }


def _hits_from_graph(question: str, graph: TiledLookupGraph, *, limit: int = 40) -> list[ScoredHit]:
    """Score *graph* nodes against *question* and return the top *limit* hits."""
    tokens = _query_tokens(question)
    raw: list[ScoredHit] = []
    for nid, node in graph.nodes.items():
        score = _match_score(question, node, tokens)
        if score <= 0 and not str(nid).startswith("tiled:count:"):
            continue
        info = SimpleNamespace(
            id=nid,
            name=node.get("name") or nid,
            category=node.get("category") or "Thing",
            description=node.get("description") or "",
            score_prp=max(score, 0.4),
            evidence_ct=max(1, len(graph.out_edges.get(nid, [])) + 1),
        )
        raw.append(
            ScoredHit(
                id=nid,
                graph_id=TILED_GRAPH_ID,
                score=float(info.score_prp),
                evidence_ct=info.evidence_ct,
                category=str(info.category),
                name=str(info.name),
                graph_label=TILED_GRAPH_LABEL,
                payload=info,
            )
        )
    raw.sort(key=lambda hit: hit.score, reverse=True)
    return raw[:limit]


def _ingest_entities(graph: TiledLookupGraph, entities: Sequence[JsonObject]) -> None:
    """Add GraphQL entities and their outgoing-link targets to *graph*."""
    pending_edges: list[tuple[str, str, JsonObject]] = []
    for entity in entities:
        node = entity_to_node(entity)
        graph.add_node(node)
        for link in entity.get("outgoingLinks") or []:
            if not isinstance(link, dict):
                continue
            obj = link.get("object") if isinstance(link.get("object"), dict) else None
            if obj:
                pending_edges.append((node["id"], str(link.get("predicate") or "rel:related_to"), obj))
    for subject, predicate, obj in pending_edges:
        target = entity_to_node(obj)
        if target["id"] not in graph.nodes:
            graph.add_node(target)
        graph.add_edge(subject, predicate, target["id"])


def _catalog_runs(
    question: str,
    *,
    uri: str,
    api_key: str | None,
    limit: int = 8,
) -> tuple[list[JsonObject], str | None]:
    """Walk the Tiled catalog tree for BlueskyRun entries when GraphQL returns none.

    ``tiled.client`` is an optional dependency, imported lazily; the service key (explicit or the
    process key) is passed to the client and never logged.
    """
    try:
        from tiled.client import from_uri as tiled_from_uri
    except Exception:
        return [], "tiled.client not installed; GraphQL-only lookup"
    kwargs: dict[str, str] = {}
    key = (api_key if api_key is not None else process_api_key()).strip()
    if key:
        kwargs["api_key"] = key
    try:
        root = tiled_from_uri(uri, **kwargs)
    except Exception as exc:
        return [], f"catalog unreachable ({_safe_error(exc)})"
    runs: list[JsonObject] = []
    error: str | None = None

    def _as_run(node: object, path: list[str]) -> JsonObject | None:
        """Build a BlueskyRun-shaped dict from a catalog node, or None if not a run."""
        try:
            meta = dict(getattr(node, "metadata", None) or {})
        except Exception:
            LOGGER.debug("catalog node metadata unreadable", exc_info=True)
            return None
        start = meta.get("start") if isinstance(meta.get("start"), dict) else meta
        if not isinstance(start, dict):
            return None
        uid = str(start.get("uid") or meta.get("uid") or "")
        if not uid and "start" not in meta:
            return None
        uid = uid or "/".join(path) or "unknown"
        name = f"scan {start.get('scan_id') or uid[:8]} {start.get('plan_name') or ''}".strip()
        item = getattr(node, "item", None)
        sample = start.get("sample")
        return {
            "id": uid,
            "entityType": "BlueskyRun",
            "name": name,
            "uri": str(getattr(node, "uri", "") or ""),
            "nodeId": item.get("id") if isinstance(item, dict) else None,
            "properties": {
                "uid": uid,
                "scan_id": start.get("scan_id"),
                "plan_name": start.get("plan_name"),
                "time": start.get("time"),
                "catalog_path": path,
                "proposal": start.get("proposal") or start.get("proposal_id"),
                "sample_name": sample.get("name") if isinstance(sample, dict) else start.get("sample_name"),
            },
        }

    def walk(node: Untyped, path: list[str], depth: int) -> None:  # Untyped: Tiled container or array node
        """DFS the catalog tree, collecting up to *limit* BlueskyRun entries."""
        if len(runs) >= limit or depth > 3:
            return
        run = _as_run(node, path)
        if run:
            runs.append(run)
            return
        try:
            keys = list(node)  # mapping-like container
        except Exception:
            LOGGER.debug("catalog node is not iterable", exc_info=True)
            return
        for key in keys[:20]:
            if len(runs) >= limit:
                return
            try:
                child = node[key]
            except Exception:
                LOGGER.debug("catalog child %s unreadable", key, exc_info=True)
                continue
            walk(child, path + [str(key)], depth + 1)

    try:
        walk(root, [], 0)
    except Exception as exc:
        error = _safe_error(exc)
    return runs, error


def lookup_experiment_identity(
    question: str,
    *,
    uri: str | None = None,
    api_key: str | None = None,
    enabled: bool | None = None,
    include_catalog: bool = True,
    client: LookupTransport | None = None,
) -> TiledLookupResult:
    """Page live Tiled GraphQL for experiment-identity questions.

    Args:
        question: The chat question; non-identity questions return ``status="skipped"``.
        uri: Tiled base URI override (``TILED_URI`` or the default when ``None``).
        api_key: Explicit credential; ``None`` means the process key.
        enabled: Live-Tiled flag override.
        include_catalog: Supplement with a ``tiled.client`` catalog walk when GraphQL has no runs.
        client: Transport to page through; the default posts to *uri* with the credential.

    Returns:
        A :class:`TiledLookupResult` whose ``status`` is one of ``disabled``, ``skipped``,
        ``invalid_uri``, ``unreachable``, ``unsigned``, ``empty`` or ``ok``.
    """
    cfg = load_tiled_config(uri=uri, enabled=enabled)
    if not cfg.enabled:
        return TiledLookupResult(status="disabled", fallback="json_kg")
    if not is_experiment_identity_question(question):
        return TiledLookupResult(status="skipped", fallback="json_kg")
    if cfg.uri_error:
        return TiledLookupResult(status="invalid_uri", error=cfg.uri_error, fallback="json_kg")

    types = list(ENTITY_TYPES)
    graph = TiledLookupGraph()
    counts: dict[str, int] = {}
    key = api_key if api_key is not None else process_api_key() or None
    transport = _transport_for(cfg.uri, key, client)
    source = "graphql"
    try:
        for entity_type in types:
            entities = _page_entities(entity_type, uri=cfg.uri, api_key=key, transport=transport)
            counts[entity_type] = len(entities)
            _ingest_entities(graph, entities)
    except Exception as exc:
        err = _safe_error(exc)
        LOGGER.warning("Live Tiled GraphQL lookup failed: %s", err)
        return TiledLookupResult(
            status="unreachable",
            error=err,
            fallback="json_kg",
            entity_counts=counts,
        )

    unsigned_empty = (not cfg.api_key_set) and not any(counts.get(t, 0) for t in types)
    if unsigned_empty:
        return TiledLookupResult(
            status="unsigned",
            error=(
                "Tiled GraphQL returned no ESAF/Proposal/Sample/BlueskyRun entities. "
                "Unsigned requests usually yield empty lists. Set TILED_API_KEY "
                "(Tiled Apikey, or a token from Keycloak/OIDC). "
                "Note: sim nodes were removed from the JSON KG (v8+); experiment-identity "
                "data is only available from Tiled Graph (:8765)."
            ),
            fallback="json_kg",
            entity_counts=counts,
            graph=graph,
            source="graphql",
        )

    for entity_type in types:
        graph.add_node(_count_node(entity_type, counts.get(entity_type, 0), "GraphQL"))

    if (
        include_catalog
        and "BlueskyRun" in types
        and counts.get("BlueskyRun", 0) == 0
        and _IDENTITY_RE.search(question or "")
    ):
        runs, catalog_error = _catalog_runs(question, uri=cfg.uri, api_key=key)
        if runs:
            _ingest_entities(graph, runs)
            counts["BlueskyRun"] = len(runs)
            graph.add_node(_count_node("BlueskyRun", len(runs), "tiled.client catalog"))
            source = "graphql+catalog"
        elif catalog_error:
            LOGGER.info("Tiled catalog supplement skipped: %s", catalog_error)

    hits = _hits_from_graph(question, graph)
    if not hits:
        return TiledLookupResult(
            status="empty",
            error="Live Tiled had no matching experiment-identity entities. "
                  "Note: sim nodes were removed from the JSON KG (v8+) — "
                  "experiment-identity data is only available from Tiled Graph (:8765).",
            fallback="json_kg",
            entity_counts=counts,
            graph=graph,
            source=source,
        )
    return TiledLookupResult(
        hits=hits,
        graph=graph,
        status="ok",
        entity_counts=counts,
        fallback="",
        source=source,
    )


__all__ = [
    "CITE_FOCUS_HOPS",
    "DEFAULT_TILED_URI",
    "ENTITY_TYPES",
    "GRAPHQL_PATH",
    "MAX_PAGES",
    "PAGE_SIZE",
    "PROBE_TIMEOUT",
    "REQUEST_TIMEOUT",
    "TILED_GRAPH_ID",
    "TILED_GRAPH_LABEL",
    "CredentialProvider",
    "LookupTransport",
    "TiledGraphConfig",
    "TiledGraphQLError",
    "TiledLookupClient",
    "TiledLookupGraph",
    "TiledLookupResult",
    "auth_headers",
    "coerce_local_tiled_uri",
    "entity_to_node",
    "graphql_url",
    "is_als_production_tiled_host",
    "is_experiment_identity_question",
    "load_tiled_config",
    "lookup_experiment_identity",
    "normalize_tiled_uri",
    "probe_tiled_status",
    "process_api_key",
    "requested_entity_types",
    "resolve_tiled_node_id",
    "tiled_api_key_set",
    "tiled_graphql",
    "tiled_lookup_candidates",
    "tiled_neighborhood_ids",
    "tiled_node_aliases",
]
