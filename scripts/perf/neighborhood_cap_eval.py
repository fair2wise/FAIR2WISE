"""Measure what the 500-node neighbourhood cap costs and saves, on real graphs.

Background (owner question, 2026-11-09): ``GET /graph/neighborhood/{id}`` caps the answer at
``NEIGHBORHOOD_MAX_NODES`` (500) nodes and clamps ``hops`` to 1..``MAX_HOPS`` (4), on the JSON
graph-source path (``GraphSources.neighborhood``) and, since the T27 B2 fix, on the Tiled path
(``graph_neighborhood_from_lookup``). The owner asked whether the cap is needed at all. This script
produces the evidence: neighbourhood sizes per hop with and without the cap, the server-side work
(BFS + serialisation) and the response size, for a random sample of nodes plus the top-degree hubs.

It reads only. Offline it opens JSON KG files through the same code path the route uses
(``normalize_json_graph`` / ``build_source_graph`` / ``GraphSources.neighborhood``) and lifts the
cap by rebinding the module constant for the uncapped run. Online (``--container-url``) it issues
plain HTTP GETs to a running container, never a POST or PATCH, to time the real route for the
capped case; the uncapped counts for a container source come from the same BFS run offline over
the whole graph the container returns for ``GET /graph?source=...``.

Usage:
    python scripts/perf/neighborhood_cap_eval.py --graph storage/kg/matkg_rsoxs_v4.json
    python scripts/perf/neighborhood_cap_eval.py --graph storage/kg/matkg_rsoxs_v1.json \\
        --graph storage/kg/matkg_rsoxs_v4.json --graph storage/kg/matkg_bl1101_v7.json \\
        --container-url http://127.0.0.1:8080/fair2wise/api \\
        --container-source tiled:e2e_demo --container-source tiled:bl1101 \\
        --container-source json:rsoxs_v4 --out-json out.json --out-md out.md

Only the standard library and the app itself are imported. Importing the app's wire serialiser
(``app.modules.f2w_agent.api``) takes ~20 s because that module pulls in torch; pass
``--raw-bytes`` to skip it and size the raw node dicts instead.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.modules.f2w_agent import graph_sources as gs  # noqa: E402

HOPS = (1, 2, 3, 4)
THRESHOLDS = (500, 2000, 10000)
UNCAPPED = 10**9
#: FastAPI's JSONResponse settings; the byte counts here are what the wire carries (before gzip).
_JSON_KW: dict[str, Any] = {"ensure_ascii": False, "allow_nan": False, "separators": (",", ":")}


# --------------------------------------------------------------------------- data classes

@dataclass
class HopStats:
    """Neighbourhood sizes at one hop count over the sampled nodes."""

    hops: int
    n: int
    nodes_p50: int
    nodes_p90: int
    nodes_p99: int
    nodes_max: int
    edges_p50: int
    edges_p90: int
    edges_max: int
    share_over: dict[str, float]
    hub_nodes_max: int
    hub_edges_max: int
    sample_nodes_max: int


@dataclass
class Timing:
    """One measured request or offline route emulation."""

    node_id: str
    kind: str  # hub | sample
    hops: int
    capped: bool
    nodes: int
    edges: int
    bytes: int
    bfs_ms: float
    serialize_ms: float
    total_ms: float
    status: int | None = None
    cap: int = 0


@dataclass
class GraphReport:
    """Everything measured for one graph."""

    name: str
    origin: str
    node_count: int
    edge_count: int
    degree_p50: int
    degree_p99: int
    degree_max: int
    hubs: list[tuple[str, int]]
    uncapped: list[HopStats]
    capped: list[HopStats]
    timings_offline: list[Timing] = field(default_factory=list)
    timings_online: list[Timing] = field(default_factory=list)
    legacy_timings: list[Timing] = field(default_factory=list)
    default_tiled_probe: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers

def percentile(values: Sequence[int], pct: float) -> int:
    """Nearest-rank percentile of ``values`` (0 for an empty list)."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, int(round(pct / 100.0 * len(ordered) + 0.5)))
    return ordered[min(rank, len(ordered)) - 1]


def load_json_graph(path: Path) -> gs.SourceGraph:
    """Open a KG file exactly as ``GraphSources._load_json`` does (whole-graph cut at ``MAX_NODES``)."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodes, edges = gs.normalize_json_graph(data)
    return gs.build_source_graph(f"json:{path.stem}", gs.KIND_JSON, path.stem, path.stem, "offline", nodes, edges)


def graph_from_payload(source_id: str, payload: dict[str, Any]) -> gs.SourceGraph:
    """Rebuild a ``SourceGraph`` from a ``GET /graph?source=`` wire payload (nodes + source/target edges)."""
    nodes = [dict(n) for n in payload.get("nodes", []) if isinstance(n, dict) and n.get("id")]
    edges = [{"subject": e["source"], "predicate": e.get("predicate") or gs.DEFAULT_PREDICATE, "object": e["target"]}
             for e in payload.get("edges", []) if isinstance(e, dict) and e.get("source") and e.get("target")]
    return gs.build_source_graph(source_id, source_id.split(":", 1)[0], source_id, source_id, "container", nodes, edges)


def degrees(graph: gs.SourceGraph) -> dict[str, int]:
    """Undirected degree (number of incident edges) of every node."""
    return {node_id: len(graph.adjacency.get(node_id, ())) for node_id in graph.by_id}


def pick_nodes(graph: gs.SourceGraph, *, sample: int, hubs: int, seed: int) -> tuple[list[str], list[str]]:
    """The top-``hubs`` nodes by degree and a seeded random sample of ``sample`` other nodes."""
    deg = degrees(graph)
    ranked = sorted(deg, key=lambda i: (-deg[i], i))
    hub_ids = ranked[:hubs]
    rest = [i for i in ranked[hubs:] if deg[i] > 0]  # isolated nodes have a 1-node neighbourhood at any hop
    rng = random.Random(seed)
    sample_ids = rng.sample(rest, min(sample, len(rest)))
    return hub_ids, sample_ids


def neighborhood(graph: gs.SourceGraph, node_id: str, hops: int, cap: int) -> gs.SourceGraph:
    """``GraphSources.neighborhood`` with the module cap rebound to ``cap`` for this call."""
    previous = gs.NEIGHBORHOOD_MAX_NODES
    gs.NEIGHBORHOOD_MAX_NODES = cap
    try:
        around = gs.GraphSources.neighborhood(graph, node_id, hops)
    finally:
        gs.NEIGHBORHOOD_MAX_NODES = previous
    assert around is not None
    return around


def hop_stats(sizes: dict[int, list[tuple[str, int, int]]], hub_ids: set[str]) -> list[HopStats]:
    """Percentiles and threshold shares per hop from ``{hops: [(node_id, nodes, edges), ...]}``."""
    out: list[HopStats] = []
    for hops, rows in sorted(sizes.items()):
        nodes = [n for _, n, _ in rows]
        edges = [e for _, _, e in rows]
        hub_rows = [(n, e) for i, n, e in rows if i in hub_ids]
        sample_rows = [n for i, n, _ in rows if i not in hub_ids]
        out.append(HopStats(
            hops=hops, n=len(rows),
            nodes_p50=percentile(nodes, 50), nodes_p90=percentile(nodes, 90), nodes_p99=percentile(nodes, 99),
            nodes_max=max(nodes, default=0),
            edges_p50=percentile(edges, 50), edges_p90=percentile(edges, 90), edges_max=max(edges, default=0),
            share_over={str(t): round(sum(1 for n in nodes if n > t) / max(1, len(nodes)), 4) for t in THRESHOLDS},
            hub_nodes_max=max((n for n, _ in hub_rows), default=0),
            hub_edges_max=max((e for _, e in hub_rows), default=0),
            sample_nodes_max=max(sample_rows, default=0),
        ))
    return out


# --------------------------------------------------------------------------- serialisation

Serializer = Callable[[gs.SourceGraph], bytes]


def raw_serializer(around: gs.SourceGraph) -> bytes:
    """Size the raw node dicts (no wire conversion); a lower bound on the wire payload."""
    return json.dumps({"nodes": list(around.nodes), "edges": list(around.edges), "source_path": around.source_id},
                      **_JSON_KW).encode("utf-8")


def wire_serializer() -> Serializer:
    """The route's own conversion: ``_source_payload`` -> ``GraphPayload`` -> ``model_dump`` -> JSON bytes."""
    from app.modules.f2w_agent import api  # heavy import (torch); deferred on purpose

    def serialize(around: gs.SourceGraph) -> bytes:
        nodes = []
        for raw in around.nodes:
            node = api._graph_node_from_raw(raw)
            node.graph_id = node.graph_id or around.graph_id
            node.graph_label = node.graph_label or around.graph_label
            nodes.append(node)
        payload = api.GraphPayload(
            nodes=nodes,
            edges=[api.GraphEdge(source=e["subject"], target=e["object"], predicate=e["predicate"])
                   for e in around.edges],
            source_path=f"source:{around.source_id}")
        return json.dumps(api._model_to_jsonable(payload), **_JSON_KW).encode("utf-8")

    return serialize


class _LookupKG:
    """Duck-typed stand-in for the Tiled lookup graph (``nodes`` dict + ``out_edges`` dict)."""

    graph_id = "tiled"
    graph_label = "Tiled Graph"

    def __init__(self, graph: gs.SourceGraph) -> None:
        self.nodes: dict[str, dict[str, Any]] = {i: dict(n) for i, n in graph.by_id.items()}
        self.out_edges: dict[str, list[dict[str, Any]]] = {}
        for edge in graph.edges:
            self.out_edges.setdefault(edge["subject"], []).append(
                {"subject": edge["subject"], "predicate": edge["predicate"], "object": edge["object"]})


def legacy_timings(graph: gs.SourceGraph, hub_ids: Sequence[str]) -> list[Timing]:
    """Time the legacy Tiled helper (``graph_subset_from_lookup``) over the same graph, capped and not."""
    from app.modules.f2w_agent import api

    kg = _LookupKG(graph)
    out: list[Timing] = []
    for node_id in hub_ids:
        for hops in HOPS:
            for cap in (None, gs.NEIGHBORHOOD_MAX_NODES):
                t0 = time.perf_counter()
                subset = api.graph_subset_from_lookup(kg, [node_id], hops=hops, max_nodes=cap)
                t1 = time.perf_counter()
                body = json.dumps(subset, **_JSON_KW).encode("utf-8")
                t2 = time.perf_counter()
                out.append(Timing(node_id, "hub", hops, cap is not None, len(subset["nodes"]), len(subset["edges"]),
                                  len(body), round((t1 - t0) * 1000, 2), round((t2 - t1) * 1000, 2),
                                  round((t2 - t0) * 1000, 2)))
    return out


# --------------------------------------------------------------------------- measurement

def measure_graph(graph: gs.SourceGraph, *, name: str, origin: str, sample: int, hubs: int, seed: int,
                  timing_sample: int, serializer: Serializer, legacy: bool,
                  extra_caps: Sequence[int] = ()) -> GraphReport:
    """Sizes for every sampled node at every hop; route emulation timings for the hubs and a subset."""
    hub_ids, sample_ids = pick_nodes(graph, sample=sample, hubs=hubs, seed=seed)
    deg = degrees(graph)
    deg_values = list(deg.values())
    hub_set = set(hub_ids)
    sizes_uncapped: dict[int, list[tuple[str, int, int]]] = {h: [] for h in HOPS}
    sizes_capped: dict[int, list[tuple[str, int, int]]] = {h: [] for h in HOPS}
    for node_id in hub_ids + sample_ids:
        for hops in HOPS:
            full = neighborhood(graph, node_id, hops, UNCAPPED)
            sizes_uncapped[hops].append((node_id, len(full.nodes), len(full.edges)))
            cut = neighborhood(graph, node_id, hops, gs.NEIGHBORHOOD_MAX_NODES)
            sizes_capped[hops].append((node_id, len(cut.nodes), len(cut.edges)))

    timed = [(i, "hub") for i in hub_ids] + [(i, "sample") for i in sample_ids[:timing_sample]]
    timings: list[Timing] = []
    for node_id, kind in timed:
        for hops in HOPS:
            for cap in (UNCAPPED, gs.NEIGHBORHOOD_MAX_NODES, *extra_caps):
                t0 = time.perf_counter()
                around = neighborhood(graph, node_id, hops, cap)
                t1 = time.perf_counter()
                body = serializer(around)
                t2 = time.perf_counter()
                timings.append(Timing(node_id, kind, hops, cap != UNCAPPED, len(around.nodes), len(around.edges),
                                      len(body), round((t1 - t0) * 1000, 2), round((t2 - t1) * 1000, 2),
                                      round((t2 - t0) * 1000, 2), cap=cap))
    report = GraphReport(
        name=name, origin=origin, node_count=len(graph.nodes), edge_count=len(graph.edges),
        degree_p50=percentile(deg_values, 50), degree_p99=percentile(deg_values, 99), degree_max=max(deg_values, default=0),
        hubs=[(i, deg[i]) for i in hub_ids],
        uncapped=hop_stats(sizes_uncapped, hub_set), capped=hop_stats(sizes_capped, hub_set),
        timings_offline=timings)
    if legacy:
        report.legacy_timings = legacy_timings(graph, hub_ids)
    return report


def http_get(url: str, timeout: float) -> tuple[int, bytes, float]:
    """One GET: status, body and wall-clock milliseconds. Never raises for an HTTP error status."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # http(s) URL from the CLI
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    return status, body, (time.perf_counter() - t0) * 1000


def measure_container_source(base: str, source_id: str, *, sample: int, hubs: int, seed: int, timing_sample: int,
                             timeout: float, probe_default: bool, extra_caps: Sequence[int] = ()) -> GraphReport:
    """Whole graph by GET, sizes offline (uncapped and capped), the real route timed for the capped case."""
    status, body, _ = http_get(f"{base}/graph?{urllib.parse.urlencode({'source': source_id})}", timeout)
    if status != 200:
        raise SystemExit(f"{source_id}: GET /graph returned {status}")
    graph = graph_from_payload(source_id, json.loads(body))
    report = measure_graph(graph, name=source_id, origin=f"container {base}", sample=sample, hubs=hubs, seed=seed,
                           timing_sample=timing_sample, serializer=raw_serializer, legacy=False,
                           extra_caps=extra_caps)
    hub_ids = [i for i, _ in report.hubs]
    _, sample_ids = pick_nodes(graph, sample=sample, hubs=hubs, seed=seed)
    timed = [(i, "hub") for i in hub_ids] + [(i, "sample") for i in sample_ids[:timing_sample]]
    probe: dict[str, int] = {}
    for node_id, kind in timed:
        for hops in HOPS:
            query = urllib.parse.urlencode({"source": source_id, "hops": hops})
            url = f"{base}/graph/neighborhood/{urllib.parse.quote(node_id, safe='')}?{query}"
            status, body, ms = http_get(url, timeout)
            nodes = edges = 0
            if status == 200:
                payload = json.loads(body)
                nodes, edges = len(payload.get("nodes", [])), len(payload.get("edges", []))
            report.timings_online.append(Timing(node_id, kind, hops, True, nodes, edges, len(body), 0.0, 0.0,
                                                round(ms, 2), status))
        if probe_default:
            url = f"{base}/graph/neighborhood/{urllib.parse.quote(node_id, safe='')}?hops=3"
            status, _, _ = http_get(url, timeout)
            probe[str(status)] = probe.get(str(status), 0) + 1
    report.default_tiled_probe = probe
    return report


# --------------------------------------------------------------------------- rendering

def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _agg(rows: Sequence[Timing], key: str) -> tuple[float, float, float]:
    values = [float(getattr(r, key)) for r in rows]
    if not values:
        return 0.0, 0.0, 0.0
    return statistics.median(values), percentile([int(v * 1000) for v in values], 90) / 1000, max(values)


def render_markdown(reports: Sequence[GraphReport]) -> str:
    """Markdown tables of the measurements (the report under docs/ quotes from this)."""
    lines: list[str] = []
    for rep in reports:
        lines += [f"### {rep.name}", "",
                  f"{rep.node_count:,} nodes, {rep.edge_count:,} edges ({rep.origin}); degree p50 {rep.degree_p50}, "
                  f"p99 {rep.degree_p99}, max {rep.degree_max}. Hubs: "
                  + ", ".join(f"`{i}` ({d})" for i, d in rep.hubs[:5]) + ".", ""]
        lines += ["| hops | n | nodes p50 | p90 | p99 | max | edges p50 | p90 | max | >500 | >2000 | >10000 | hub max nodes/edges |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for st in rep.uncapped:
            lines.append(f"| {st.hops} | {st.n} | {st.nodes_p50} | {st.nodes_p90} | {st.nodes_p99} | {st.nodes_max} | "
                         f"{st.edges_p50} | {st.edges_p90} | {st.edges_max} | {_pct(st.share_over['500'])} | "
                         f"{_pct(st.share_over['2000'])} | {_pct(st.share_over['10000'])} | "
                         f"{st.hub_nodes_max}/{st.hub_edges_max} |")
        lines += ["", "Capped (500):", "", "| hops | nodes p50 | p90 | max | edges p50 | p90 | max |", "|---|---|---|---|---|---|---|"]
        for st in rep.capped:
            lines.append(f"| {st.hops} | {st.nodes_p50} | {st.nodes_p90} | {st.nodes_max} | {st.edges_p50} | "
                         f"{st.edges_p90} | {st.edges_max} |")
        for title, rows in (("Offline route emulation (BFS + wire serialisation)", rep.timings_offline),
                            ("Legacy Tiled helper, offline replica", rep.legacy_timings)):
            if not rows:
                continue
            lines += ["", f"{title}:", "",
                      "| hops | kind | capped | nodes med/max | bytes med/max | bfs ms med/max | serialize ms med/max | total ms med/p90/max |",
                      "|---|---|---|---|---|---|---|---|"]
            for hops in HOPS:
                for kind in ("hub", "sample"):
                    for capped in (False, True):
                        sel = [r for r in rows if r.hops == hops and r.kind == kind and r.capped == capped
                               and r.cap in (0, UNCAPPED, gs.NEIGHBORHOOD_MAX_NODES)]
                        if not sel:
                            continue
                        n_med, _, n_max = _agg(sel, "nodes")
                        b_med, _, b_max = _agg(sel, "bytes")
                        f_med, _, f_max = _agg(sel, "bfs_ms")
                        s_med, _, s_max = _agg(sel, "serialize_ms")
                        t_med, t_p90, t_max = _agg(sel, "total_ms")
                        lines.append(f"| {hops} | {kind} | {'yes' if capped else 'no'} | {n_med:.0f}/{n_max:.0f} | "
                                     f"{b_med:,.0f}/{b_max:,.0f} | {f_med:.1f}/{f_max:.1f} | {s_med:.1f}/{s_max:.1f} | "
                                     f"{t_med:.1f}/{t_p90:.1f}/{t_max:.1f} |")
        sweep = sorted({r.cap for r in rep.timings_offline if r.cap not in (0, gs.NEIGHBORHOOD_MAX_NODES)})
        if len(sweep) > 1:
            lines += ["", "Cap sweep at the hubs (offline route emulation, median over hubs):", "",
                      "| hops | cap | nodes | bytes | total ms |", "|---|---|---|---|---|"]
            for hops in (2, 3, 4):
                for cap in (gs.NEIGHBORHOOD_MAX_NODES, *[c for c in sweep if c != UNCAPPED], UNCAPPED):
                    sel = [r for r in rep.timings_offline if r.hops == hops and r.kind == "hub" and r.cap == cap]
                    if sel:
                        lines.append(f"| {hops} | {'none' if cap == UNCAPPED else cap} | {_agg(sel, 'nodes')[0]:.0f} | "
                                     f"{_agg(sel, 'bytes')[0]:,.0f} | {_agg(sel, 'total_ms')[0]:.1f} |")
        if rep.timings_online:
            lines += ["", "Live route (`GET /graph/neighborhood/{id}?source=...&hops=N`, capped as deployed):", "",
                      "| hops | kind | requests | status | nodes med/max | bytes med/max | wall ms med/p90/max |",
                      "|---|---|---|---|---|---|---|"]
            for hops in HOPS:
                for kind in ("hub", "sample"):
                    sel = [r for r in rep.timings_online if r.hops == hops and r.kind == kind]
                    if not sel:
                        continue
                    statuses = sorted({str(r.status) for r in sel})
                    n_med, _, n_max = _agg(sel, "nodes")
                    b_med, _, b_max = _agg(sel, "bytes")
                    t_med, t_p90, t_max = _agg(sel, "total_ms")
                    lines.append(f"| {hops} | {kind} | {len(sel)} | {'/'.join(statuses)} | {n_med:.0f}/{n_max:.0f} | "
                                 f"{b_med:,.0f}/{b_max:,.0f} | {t_med:.1f}/{t_p90:.1f}/{t_max:.1f} |")
        if rep.default_tiled_probe:
            lines += ["", "Default Tiled path (no `source`), same ids, hops=3: status counts "
                      + ", ".join(f"{k}: {v}" for k, v in sorted(rep.default_tiled_probe.items()))]
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI

def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation; print Markdown, optionally write JSON and Markdown files."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", action="append", type=Path, default=[], help="JSON KG file (repeatable)")
    parser.add_argument("--container-url", default=None,
                        help="base URL of a running container API, e.g. http://127.0.0.1:8080/fair2wise/api (GETs only)")
    parser.add_argument("--container-source", action="append", default=[],
                        help="graph source id to measure through the container (repeatable, e.g. tiled:bl1101)")
    parser.add_argument("--probe-default-tiled", action="store_true",
                        help="also GET the route without `source` for the sampled ids (status counts only)")
    parser.add_argument("--sample", type=int, default=200, help="random non-hub nodes per graph (default 200)")
    parser.add_argument("--hubs", type=int, default=20, help="top-degree nodes per graph (default 20)")
    parser.add_argument("--timing-sample", type=int, default=25,
                        help="random nodes (besides all hubs) that get the timing/bytes runs (default 25)")
    parser.add_argument("--extra-cap", action="append", type=int, default=[],
                        help="also time the offline route at this cap (repeatable, e.g. 2000 5000)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")
    parser.add_argument("--raw-bytes", action="store_true",
                        help="size raw node dicts instead of importing the app's wire serialiser (fast start)")
    parser.add_argument("--no-legacy", action="store_true", help="skip the legacy Tiled helper replica")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.graph and not args.container_source:
        parser.error("give at least one --graph or --container-source")
    if args.container_source and not args.container_url:
        parser.error("--container-source needs --container-url")

    serializer: Serializer = raw_serializer if args.raw_bytes else wire_serializer()
    reports: list[GraphReport] = []
    for path in args.graph:
        t0 = time.perf_counter()
        graph = load_json_graph(path)
        load_ms = (time.perf_counter() - t0) * 1000
        print(f"[{path.name}] loaded {len(graph.nodes):,} nodes / {len(graph.edges):,} edges in {load_ms:.0f} ms",
              file=sys.stderr)
        reports.append(measure_graph(graph, name=path.name, origin=f"file {path}", sample=args.sample, hubs=args.hubs,
                                     seed=args.seed, timing_sample=args.timing_sample, serializer=serializer,
                                     legacy=not args.raw_bytes and not args.no_legacy,
                                     extra_caps=args.extra_cap))
    base = (args.container_url or "").rstrip("/")
    for source_id in args.container_source:
        print(f"[{source_id}] measuring through {base}", file=sys.stderr)
        reports.append(measure_container_source(base, source_id, sample=args.sample, hubs=args.hubs, seed=args.seed,
                                                timing_sample=args.timing_sample, timeout=args.timeout,
                                                probe_default=args.probe_default_tiled,
                                                extra_caps=args.extra_cap))

    markdown = render_markdown(reports)
    print(markdown)
    if args.out_md:
        args.out_md.write_text(markdown, encoding="utf-8")
    if args.out_json:
        args.out_json.write_text(json.dumps([asdict(r) for r in reports], indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
