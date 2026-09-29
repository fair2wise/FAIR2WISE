"""Multi-KG helpers: identity, defaults, and hit-list merge.

Graphs stay independent (never concatenated). Retrieval fans out per file and
merges scored hit lists tagged with ``graph_id``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple  # noqa: I001

DEFAULT_JSON_GRAPH_PATHS: Tuple[str, ...] = (
    "storage/kg/matkg_rsoxs_v1.json",
    "storage/kg/matkg_bl1101_v1.json",
)
DEFAULT_JSON_GRAPH = DEFAULT_JSON_GRAPH_PATHS[0]
XRAY_DEMO_GRAPH = "storage/kg/matkg_xray_papers_cborg_chat.json"
XRAY_DEMO_NAME = "matkg_xray_papers_cborg_chat.json"
_BL1101_SNAPSHOT_RE = re.compile(r"^matkg_bl1101_v(\d+)\.json$")
_RSOXS_SNAPSHOT_RE = re.compile(r"^matkg_rsoxs_v(\d+)\.json$")

_GRAPH_ID_BY_NAME = {
    "matkg_rsoxs_v1.json": "rsoxs_v1",
    "matkg_bl1101_v1.json": "bl1101",
    XRAY_DEMO_NAME: "xray_demo",
}

_GRAPH_LABEL_BY_ID = {
    "rsoxs_v1": "science",
    "bl1101": "11.0.1.2 ops",
    "xray_demo": "x-ray demo",
    "tiled": "Tiled Graph",
}

_SCHEMA_BY_NAME = {
    "matkg_rsoxs_v1.json": "storage/schema/rsoxs_schema.yaml",
    "matkg_bl1101_v1.json": "storage/schema/bl1101_schema.yaml",
}


def normalize_graph_path(path: str) -> str:
    """Normalize slashes and strip whitespace from a graph file path."""
    return str(path or "").replace("\\", "/").strip()


def split_graph_arg(value: Optional[str]) -> List[str]:
    """Split a comma-separated ``--graph`` value into unique paths."""
    if not value:
        return []
    return unique_graph_paths(part for part in str(value).split(",") if part.strip())


def unique_graph_paths(paths: Iterable[str]) -> List[str]:
    """Deduplicate graph paths while preserving first-seen order."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in paths:
        path = normalize_graph_path(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def collect_graph_paths(
    *groups: Optional[Sequence[str] | str],
) -> List[str]:
    """Flatten mixed string/sequence graph arguments into unique paths."""
    values: List[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            values.extend(split_graph_arg(group))
            continue
        for item in group:
            values.extend(split_graph_arg(str(item)))
    return unique_graph_paths(values)


def graph_filename(path: str) -> str:
    """Return the basename of a graph path."""
    return Path(normalize_graph_path(path)).name


def is_xray_demo_graph(path: str) -> bool:
    """True for the opt-in x-ray demo JSON (never auto-unioned into defaults)."""
    return graph_filename(path) == XRAY_DEMO_NAME


def graph_id_for_path(path: str) -> str:
    """Stable graph_id from filename: rsoxs_vN, bl1101, xray_demo, or stem."""
    name = graph_filename(path)
    if name in _GRAPH_ID_BY_NAME:
        return _GRAPH_ID_BY_NAME[name]
    if _BL1101_SNAPSHOT_RE.match(name):
        return "bl1101"
    rsoxs = _RSOXS_SNAPSHOT_RE.match(name)
    if rsoxs:
        return f"rsoxs_v{rsoxs.group(1)}"
    stem = Path(name).stem or "graph"
    if stem.startswith("matkg_"):
        return stem[len("matkg_") :]
    return stem


def is_ops_graph_id(graph_id: str) -> bool:
    """True for ALS 11.0.1.2 ops snapshots (``bl1101`` prefix)."""
    return str(graph_id or "").startswith("bl1101")


def is_science_graph_id(graph_id: str) -> bool:
    """True for RSoXS literature snapshots or the x-ray demo graph."""
    gid = str(graph_id or "")
    return gid.startswith("rsoxs") or gid == "xray_demo"


def graph_label_for_id(graph_id: str) -> str:
    """Human label for Settings/citations: science, ops, x-ray demo, or the raw id."""
    if graph_id in _GRAPH_LABEL_BY_ID:
        return _GRAPH_LABEL_BY_ID[graph_id]
    if graph_id.startswith("bl1101"):
        return "11.0.1.2 ops"
    if graph_id.startswith("rsoxs"):
        return "science"
    return graph_id


def schema_path_for_graph(path: str) -> Optional[str]:
    """LinkML overlay path for a KG file, or None when no overlay is known."""
    name = graph_filename(path)
    if name in _SCHEMA_BY_NAME:
        return _SCHEMA_BY_NAME[name]
    if _BL1101_SNAPSHOT_RE.match(name) or name.startswith("matkg_bl1101"):
        return "storage/schema/bl1101_schema.yaml"
    if _RSOXS_SNAPSHOT_RE.match(name) or name.startswith("matkg_rsoxs"):
        return "storage/schema/rsoxs_schema.yaml"
    return None


def _latest_snapshot_path(available: Sequence[str], pattern: re.Pattern[str]) -> Optional[str]:
    """Highest ``vN`` snapshot matching *pattern* among *available* paths."""
    best: Optional[str] = None
    best_n = -1
    for raw in available:
        path = normalize_graph_path(raw)
        match = pattern.match(graph_filename(path))
        if not match:
            continue
        version = int(match.group(1))
        if version > best_n:
            best_n = version
            best = path
    return best


def latest_bl1101_path(available: Sequence[str]) -> Optional[str]:
    """Pick the highest matkg_bl1101_vN.json from *available* (v1 is never deleted)."""
    return _latest_snapshot_path(available, _BL1101_SNAPSHOT_RE)


def latest_rsoxs_path(available: Sequence[str]) -> Optional[str]:
    """Pick the highest matkg_rsoxs_vN.json from *available* (v1 is never deleted)."""
    return _latest_snapshot_path(available, _RSOXS_SNAPSHOT_RE)


def default_json_graph_paths(
    *,
    configured_graphs: Optional[Sequence[str]] = None,
    configured_graph: Optional[str] = None,
    available: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return query graphs. Never auto-unions the x-ray demo KG."""
    options = [normalize_graph_path(item) for item in (available or []) if normalize_graph_path(item)]
    option_names = {Path(item).name: item for item in options}
    configured = collect_graph_paths(configured_graphs, configured_graph)
    if configured:
        resolved: List[str] = []
        for path in configured:
            if options:
                if path in options:
                    resolved.append(path)
                    continue
                match = option_names.get(Path(path).name)
                if match:
                    resolved.append(match)
                    continue
            resolved.append(path)
        return unique_graph_paths(resolved)

    selected: List[str] = []
    rsoxs = latest_rsoxs_path(options)
    if rsoxs:
        selected.append(rsoxs)
    else:
        fallback_rsoxs = DEFAULT_JSON_GRAPH_PATHS[0]
        if fallback_rsoxs in options:
            selected.append(fallback_rsoxs)
        else:
            match = option_names.get(Path(fallback_rsoxs).name)
            if match:
                selected.append(match)
    ops = latest_bl1101_path(options)
    if ops:
        selected.append(ops)
    else:
        fallback_ops = DEFAULT_JSON_GRAPH_PATHS[1]
        if fallback_ops in options:
            selected.append(fallback_ops)
        else:
            match = option_names.get(Path(fallback_ops).name)
            if match:
                selected.append(match)
    if selected:
        return selected
    return [path for path in options if not is_xray_demo_graph(path)]


def primary_json_graph_path(paths: Sequence[str]) -> Optional[str]:
    """First non-empty path in *paths* (viewer/backward-compat primary)."""
    for path in paths:
        normalized = normalize_graph_path(path)
        if normalized:
            return normalized
    return None


MAX_KG_QUERY_HOPS = 20
KG_QUERY_HOPS_PRESETS: Tuple[int, ...] = tuple(range(1, MAX_KG_QUERY_HOPS + 1))
KG_QUERY_MAX_NODES_PRESETS: Tuple[int, ...] = (50, 100, 250, 500, 1000)
DEFAULT_KG_QUERY_HOPS = 20
DEFAULT_KG_QUERY_MAX_NODES = 1000


def clamp_kg_query_hops(value: Any, default: int = DEFAULT_KG_QUERY_HOPS) -> int:
    """Clamp neighborhood hops to 1..MAX_KG_QUERY_HOPS."""
    try:
        hops = int(value)
    except (TypeError, ValueError):
        hops = default
    return max(1, min(MAX_KG_QUERY_HOPS, hops))


def clamp_kg_query_max_nodes(value: Any, default: int = DEFAULT_KG_QUERY_MAX_NODES) -> int:
    """Clamp retrieve node cap to 10..1000."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(10, min(1000, count))


@dataclass
class ScoredHit:
    """A retrieval hit tagged with the graph it came from; never smash URIs across KGs."""

    id: str
    graph_id: str
    score: float
    evidence_ct: int = 0
    category: str = ""
    name: str = ""
    graph_label: str = ""
    payload: Any = field(default=None, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe hit for agent response ``selected_hits``."""
        return {
            "id": self.id,
            "graph_id": self.graph_id,
            "graph_label": self.graph_label or graph_label_for_id(self.graph_id),
            "score": float(self.score),
            "label": self.name,
            "category": self.category,
        }


def merge_hits(
    hits: Sequence[ScoredHit],
    *,
    limit: int = 12,
    max_snippets: int = 6,
) -> List[ScoredHit]:
    """Merge per-graph hit lists. Identity is (graph_id, node id), never URI smash."""
    best: Dict[Tuple[str, str], ScoredHit] = {}
    for hit in hits:
        if not hit.id:
            continue
        key = (hit.graph_id, hit.id)
        current = best.get(key)
        if current is None or hit.score > current.score:
            best[key] = hit
    ranked = sorted(
        best.values(),
        key=lambda hit: (hit.score, hit.evidence_ct),
        reverse=True,
    )
    code_ct = 0
    capped: List[ScoredHit] = []
    for hit in ranked:
        if str(hit.category).lower() == "codesnippet":
            code_ct += 1
            if code_ct > max_snippets:
                continue
        capped.append(hit)
        if len(capped) >= limit:
            break
    return capped
