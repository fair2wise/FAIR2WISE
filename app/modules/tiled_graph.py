"""Live Tiled Graph lookup for experiment identity (ESAF / Proposal / Sample / BlueskyRun).

Chat retrieval pages ``POST {TILED_URI}/api/graphql`` using the cookbook
entityTypes, then falls back to the JSON KG when Tiled is unreachable or
unsigned. Catalog ``tiled.client`` is optional for scan/run questions when
GraphQL has no BlueskyRun entities yet.

.. note:: **2026-09-23 — sim nodes removed from JSON KG fallback**

    ESAF/Proposal/Sample/BlueskyRun nodes were previously baked into the
    bl1101 JSON KG (``matkg_bl1101_v7.json``) by ``tiled_sim.py --from-graph``
    as an offline fallback.  As of ``matkg_bl1101_v8.json`` those nodes are
    **intentionally absent** from the JSON KG.  When Tiled is unreachable or
    disabled, queries for experiment-identity data (ESAF, Proposal, Sample,
    BlueskyRun) will return **empty results** — this is correct behaviour.
    Tiled Graph (``:8765``, configured in ``storage/tiled_config.yml``) is the
    **sole source** of experiment-identity data.

Never log API keys. Missing auth typically returns empty lists, not HTTP 401.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from app.modules.f2w_agent.multi_kg import ScoredHit

LOGGER = logging.getLogger("tiled_graph")

TILED_GRAPH_ID = "tiled"
TILED_GRAPH_LABEL = "Tiled Graph"
DEFAULT_TILED_URI = "http://127.0.0.1:8765"
GRAPHQL_PATH = "/api/graphql"
_PROBE_TYPENAME = "query { __typename }"
_PROBE_ENTITIES = "query { entities(limit: 1) { name entityType } }"
PROBE_TIMEOUT = 3.0
ENTITY_TYPES = ("ESAF", "Proposal", "Sample", "BlueskyRun")
PAGE_SIZE = 100
MAX_PAGES = 8
REQUEST_TIMEOUT = 8.0
CITE_FOCUS_HOPS = 3
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


def _env(name: str, default: str = "") -> str:
    """Return a stripped environment variable, or *default* when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip()


def _truthy(raw: Optional[str]) -> Optional[bool]:
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


def normalize_tiled_uri(value: Optional[str], *, default: str = DEFAULT_TILED_URI) -> str:
    """Strip a trailing GraphQL path and return a scheme+host Tiled base URI."""
    raw = str(value or "").strip() or default
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Tiled URI must be an http(s) URL, for example http://127.0.0.1:8000")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/graphql"):
        path = path[: -len("/api/graphql")]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def is_als_production_tiled_host(host: Optional[str]) -> bool:
    """True when *host* is als.lbl.gov or a subdomain (never used as chat Tiled)."""
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    return hostname == "als.lbl.gov" or hostname.endswith(".als.lbl.gov")


def coerce_local_tiled_uri(
    value: Optional[str],
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
    return f"{normalize_tiled_uri(base_uri)}{GRAPHQL_PATH}"


def auth_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    """JSON headers plus optional Tiled ``Authorization`` from *api_key* or env."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (api_key if api_key is not None else _env("TILED_API_KEY")).strip()
    if key:
        scheme = _env("TILED_API_KEY_SCHEME", "Apikey") or "Apikey"
        headers["Authorization"] = f"{scheme} {key}"
    return headers


def tiled_api_key_set() -> bool:
    """True when ``TILED_API_KEY`` is a non-empty environment value."""
    return bool(_env("TILED_API_KEY"))


def load_tiled_config(
    *,
    uri: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> "TiledGraphConfig":
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


def probe_tiled_status(
    *,
    uri: Optional[str] = None,
    enabled: Optional[bool] = None,
    timeout: float = PROBE_TIMEOUT,
) -> Dict[str, Any]:
    """Live GraphQL probe for Settings: ok / unsigned / unreachable / disabled."""
    cfg = load_tiled_config(uri=uri, enabled=enabled)
    snap = cfg.snapshot()
    if not cfg.enabled:
        snap["tiled_status"] = "disabled"
        return snap
    if cfg.uri_error:
        snap["tiled_status"] = "invalid_uri"
        return snap
    try:
        tiled_graphql(_PROBE_TYPENAME, uri=cfg.uri, timeout=timeout)
    except Exception as exc:
        snap["tiled_status"] = "unreachable"
        snap["tiled_error"] = _safe_error(exc)
        return snap
    try:
        data = tiled_graphql(_PROBE_ENTITIES, uri=cfg.uri, timeout=timeout)
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


@dataclass
class TiledGraphConfig:
    """Resolved live-Tiled settings: enabled flag, URI, and whether an API key is set."""

    enabled: bool
    uri: str
    api_key_set: bool
    uri_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
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


@dataclass
class TiledLookupResult:
    """Hits and overlay graph from a live Tiled identity lookup."""

    hits: List[ScoredHit] = field(default_factory=list)
    graph: Optional["TiledLookupGraph"] = None
    status: str = "disabled"
    error: Optional[str] = None
    fallback: str = "json_kg"
    entity_counts: Dict[str, int] = field(default_factory=dict)
    source: str = "none"

    def meta(self) -> Dict[str, Any]:
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

    def __init__(self, nodes: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        """Store *nodes* by id; outgoing edges start empty."""
        self.nodes: Dict[str, Dict[str, Any]] = dict(nodes or {})
        self.out_edges: Dict[str, List[Dict[str, Any]]] = {}

    def add_node(self, node: Dict[str, Any]) -> None:
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

    def semantic_search(self, q: str, topk: int = 12) -> List[Any]:
        """Rank Tiled nodes by lexical match to *q*; return ``(id, score)`` namespaces."""
        tokens = _query_tokens(q)
        scored: List[Tuple[float, str]] = []
        for nid, node in self.nodes.items():
            score = _match_score(q, node, tokens)
            if score > 0:
                scored.append((score, nid))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [SimpleNamespace(id=nid, score=score) for score, nid in scored[:topk]]

    def build_context(
        self,
        nodes: Sequence[Any],
        include_structured: bool = True,
        char_budget: int = 8000,
        hint_terms: Sequence[str] | None = None,
        include_pdf_snippets: bool = False,
    ) -> str:
        """Render selected Tiled nodes (and optional triples) into judge context."""
        parts: List[str] = [
            "Live Tiled GraphQL identity nodes (ESAF, Proposal, Sample, BlueskyRun). "
            "Cite the entity name, for example [KG:tiled: ESAF 2026-00043]. "
            "Do not cite cookbook query names."
        ]
        chars = len(parts[0])
        rendered = {str(getattr(ni, "id", "")) for ni in nodes}
        if include_structured:
            triples: List[str] = []
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


def is_experiment_identity_question(question: str) -> bool:
    """True when *question* asks about ESAF, proposal, sample, scan, or catalog ids."""
    text = str(question or "").strip()
    if not text:
        return False
    if _ESAF_NUMBER_RE.search(text) or _PROPOSAL_CODE_RE.search(text):
        return True
    return bool(_IDENTITY_RE.search(text))


def _query_tokens(question: str) -> set[str]:
    """Lowercased alphanumeric tokens of length ≥ 3 from *question*."""
    return {tok for tok in re.findall(r"[a-z0-9]+", str(question or "").lower()) if len(tok) >= 3}


def _stem(token: str) -> str:
    """Naive English plural stem used for entity-type alias matching."""
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def requested_entity_types(question: str) -> List[str]:
    """Entity types named in *question*, or all cookbook types if none match."""
    tokens = {_stem(tok) for tok in _query_tokens(question)}
    matched: List[str] = []
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


def _flatten_props(value: Any) -> str:
    """Recursively flatten a nested property dict/list/scalar to a single string."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_props(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_props(v) for v in value)
    return str(value)


def _normalize_identity(value: Any) -> str:
    """Collapse whitespace and punctuation so ESAF/proposal strings compare loosely."""
    return re.sub(r"[\s_\-:]+", " ", str(value or "").strip().lower())


def tiled_node_aliases(nid: str, node: Dict[str, Any]) -> set[str]:
    """Match GraphQL cites like ESAF-2026-00043 onto uri / name / identity props."""
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
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
        props.get("esaf_number") if isinstance(props, dict) else None,
        props.get("proposal_code") if isinstance(props, dict) else None,
        props.get("proposal_id") if isinstance(props, dict) else None,
        props.get("sample_code") if isinstance(props, dict) else None,
        props.get("uid") if isinstance(props, dict) else None,
    ]
    for value in values:
        normalized = _normalize_identity(value)
        if len(normalized) >= 4:
            aliases.add(normalized)
    esaf = str((props.get("esaf_number") if isinstance(props, dict) else None) or node.get("esaf_number") or "")
    if esaf:
        aliases.add(_normalize_identity(f"ESAF {esaf}"))
        aliases.add(_normalize_identity(f"ESAF-{esaf}"))
    tail = str(nid).split(":")[-1]
    if len(_normalize_identity(tail)) >= 4:
        aliases.add(_normalize_identity(tail))
    return aliases


def tiled_lookup_candidates(node_id: str) -> List[str]:
    """Undo query-overlay prefixes like ``tiled:beamline:ESAF-2026-00043``."""
    raw = str(node_id or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    out: List[str] = []
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


def resolve_tiled_node_id(kg: Any, node_id: str) -> Optional[str]:
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
        matches: List[str] = []
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
    kg: Any,
    seeds: Sequence[str],
    hops: int = CITE_FOCUS_HOPS,
) -> List[str]:
    """Undirected BFS so cite-click can show ESAF → proposal → sample/scan."""
    nodes = getattr(kg, "nodes", {}) or {}
    if not isinstance(nodes, dict):
        return []
    resolved: List[str] = []
    seen_seeds: set[str] = set()
    for seed in seeds:
        nid = resolve_tiled_node_id(kg, str(seed))
        if not nid or nid in seen_seeds:
            continue
        seen_seeds.add(nid)
        resolved.append(nid)
    if not resolved:
        return []

    adj: Dict[str, set[str]] = {}
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
        nxt: List[str] = []
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


def entity_to_node(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Tiled GraphQL entity dict to the canonical KG node dict format."""
    props = dict(entity.get("properties") or {}) if isinstance(entity.get("properties"), dict) else {}
    entity_type = str(entity.get("entityType") or props.get("category") or "Thing")
    graphql_id = str(entity.get("id") or "")
    uri = str(entity.get("uri") or "").strip()
    nid = uri or (f"tiled:{entity_type}:{graphql_id}" if graphql_id else f"tiled:{entity_type}:unknown")
    name = str(entity.get("name") or props.get("name") or nid)
    bits = [
        str(props.get("title") or "").strip(),
        str(props.get("description") or "").strip(),
        f"ESAF {props['esaf_number']}" if props.get("esaf_number") else "",
        f"proposal {props['proposal_code'] or props.get('proposal_id')}" if props.get("proposal_code") or props.get("proposal_id") else "",
        f"sample {props['sample_code']}" if props.get("sample_code") else "",
        f"uid {props['uid']}" if props.get("uid") else "",
        f"scan_id {props['scan_id']}" if props.get("scan_id") else "",
        f"plan {props['plan_name']}" if props.get("plan_name") else "",
        f"scientist {props['scientist']}" if props.get("scientist") else "",
    ]
    description = "; ".join(bit for bit in bits if bit) or name
    node: Dict[str, Any] = {
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


def _match_score(question: str, node: Dict[str, Any], tokens: Iterable[str]) -> float:
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


def _safe_error(exc: BaseException) -> str:
    """Format *exc* for logs/UI with any Tiled API key redacted."""
    text = f"{type(exc).__name__}: {exc}"
    key = _env("TILED_API_KEY")
    if key and key in text:
        text = text.replace(key, "<redacted>")
    return text[:300]


class TiledGraphQLError(RuntimeError):
    """Raised when the Tiled GraphQL endpoint returns an ``errors`` list.

    Carries the raw list of error dicts so callers can inspect them
    structurally instead of pattern-matching on the string representation.
    """

    def __init__(self, errors: list) -> None:
        """Store the GraphQL ``errors`` list and a readable message."""
        super().__init__(f"Tiled GraphQL error: {errors}")
        self.errors: list = errors

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


def tiled_graphql(
    query: str,
    variables: Optional[dict] = None,
    *,
    uri: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> dict:
    """POST *query* to Tiled GraphQL and return ``data``, or raise :class:`TiledGraphQLError`."""
    url = graphql_url(uri or _env("TILED_URI") or DEFAULT_TILED_URI)
    resp = requests.post(
        url,
        json={"query": query, "variables": variables or {}},
        headers=auth_headers(api_key),
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise TiledGraphQLError(body["errors"])
    return body.get("data") or {}


def _page_entities(
    entity_type: str,
    *,
    uri: str,
    api_key: Optional[str],
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> List[Dict[str, Any]]:
    """Page all GraphQL entities of *entity_type*, falling back if ``nodeId`` is unsupported."""
    rows: List[Dict[str, Any]] = []
    query = _PAGE_QUERY
    offset = 0
    for _ in range(max_pages):
        try:
            data = tiled_graphql(
                query,
                {"entityType": entity_type, "limit": page_size, "offset": offset},
                uri=uri,
                api_key=api_key,
            )
        except TiledGraphQLError as exc:
            if query is _PAGE_QUERY and exc.mentions_field("nodeId"):
                query = _PAGE_QUERY_NO_NODEID
                data = tiled_graphql(
                    query,
                    {"entityType": entity_type, "limit": page_size, "offset": offset},
                    uri=uri,
                    api_key=api_key,
                )
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


def _count_node(entity_type: str, count: int, source: str) -> Dict[str, Any]:
    """Synthetic inventory node so “how many ESAFs” questions have a match."""
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


def _hits_from_graph(question: str, graph: TiledLookupGraph, *, limit: int = 40) -> List[ScoredHit]:
    """Score *graph* nodes against *question* and return the top *limit* hits."""
    tokens = _query_tokens(question)
    raw: List[ScoredHit] = []
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


def _ingest_entities(graph: TiledLookupGraph, entities: Sequence[Dict[str, Any]]) -> None:
    """Add GraphQL entities and their outgoing-link targets to *graph*."""
    pending_edges: List[Tuple[str, str, Dict[str, Any]]] = []
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
    api_key: Optional[str],
    limit: int = 8,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Walk the Tiled catalog tree for BlueskyRun entries when GraphQL returns none."""
    try:
        from tiled.client import from_uri as tiled_from_uri  # type: ignore
    except Exception:
        return [], "tiled.client not installed; GraphQL-only lookup"
    kwargs: Dict[str, Any] = {}
    key = (api_key if api_key is not None else _env("TILED_API_KEY")).strip()
    if key:
        kwargs["api_key"] = key
    try:
        root = tiled_from_uri(uri, **kwargs)
    except Exception as exc:
        return [], f"catalog unreachable ({_safe_error(exc)})"
    runs: List[Dict[str, Any]] = []
    error: Optional[str] = None

    def _as_run(node: Any, path: List[str]) -> Optional[Dict[str, Any]]:
        """Build a BlueskyRun-shaped dict from a catalog node, or None if not a run."""
        try:
            meta = dict(getattr(node, "metadata", None) or {})
        except Exception:
            return None
        start = meta.get("start") if isinstance(meta.get("start"), dict) else meta
        if not isinstance(start, dict):
            return None
        uid = str(start.get("uid") or meta.get("uid") or "")
        if not uid and "start" not in meta:
            return None
        uid = uid or "/".join(path) or "unknown"
        name = f"scan {start.get('scan_id') or uid[:8]} {start.get('plan_name') or ''}".strip()
        return {
            "id": uid,
            "entityType": "BlueskyRun",
            "name": name,
            "uri": str(getattr(node, "uri", "") or ""),
            "nodeId": getattr(node, "item", {}).get("id") if isinstance(getattr(node, "item", None), dict) else None,
            "properties": {
                "uid": uid,
                "scan_id": start.get("scan_id"),
                "plan_name": start.get("plan_name"),
                "time": start.get("time"),
                "catalog_path": path,
                "proposal": start.get("proposal") or start.get("proposal_id"),
                "sample_name": (start.get("sample") or {}).get("name")
                if isinstance(start.get("sample"), dict)
                else start.get("sample_name"),
            },
        }

    def walk(node: Any, path: List[str], depth: int) -> None:
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
            return
        for key in keys[:20]:
            if len(runs) >= limit:
                return
            try:
                child = node[key]
            except Exception:
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
    uri: Optional[str] = None,
    api_key: Optional[str] = None,
    enabled: Optional[bool] = None,
    include_catalog: bool = True,
) -> TiledLookupResult:
    """Page live Tiled GraphQL for experiment-identity questions."""
    cfg = load_tiled_config(uri=uri, enabled=enabled)
    if not cfg.enabled:
        return TiledLookupResult(status="disabled", fallback="json_kg")
    if not is_experiment_identity_question(question):
        return TiledLookupResult(status="skipped", fallback="json_kg")
    if cfg.uri_error:
        return TiledLookupResult(status="invalid_uri", error=cfg.uri_error, fallback="json_kg")

    types = list(ENTITY_TYPES)
    graph = TiledLookupGraph()
    counts: Dict[str, int] = {}
    key = api_key if api_key is not None else _env("TILED_API_KEY") or None
    source = "graphql"
    try:
        for entity_type in types:
            entities = _page_entities(entity_type, uri=cfg.uri, api_key=key)
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
