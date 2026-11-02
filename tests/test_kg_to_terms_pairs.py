"""kg_to_terms over the (terms file, KG file) pairs and KG-only files on disk.

Three owner-mandated checks:

* round trip: terms -> KG -> ``kg_to_terms`` -> ``TermStore`` -> rebuild -> KG, per pair;
* continuation: reverse a real small KG, add two synthetic papers, rebuild, the old subgraph is unchanged;
* idempotence: KGs without a terms file survive ``kg_to_terms`` -> ``build_graph``.

Pairs that the *current forward code* does not reproduce from the terms file (or that kg_to_terms refuses
by design) are skipped with the reason, never failed.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
import shutil
from typing import Any

import pytest

from app.modules import json2kg
from app.modules.f2w_agent.kg_update import rebuild_kg
from app.modules.graph.snapshot import build_graph_isolated
from app.modules.term_extractor.kg_to_terms import KgToTermsError, kg_to_terms
from app.modules.term_extractor.models import RelationRecord, TermRecord
from app.modules.term_extractor.store import TermStore

STORAGE = Path(__file__).resolve().parents[1] / "storage"
LARGE_BYTES = 20_000_000
#: Keys never compared: ``importance`` is recomputed by the store's rule (asserted separately).
VOLATILE = frozenset({"importance"})
CODE_EDGE = "rel:has_code_snippet"


def _pairs() -> list[Any]:
    """Return one pytest param per stem that has both a KG and a terms file."""
    params: list[Any] = []
    for kg_path in sorted((STORAGE / "kg").glob("matkg_*.json")):
        stem = kg_path.stem.removeprefix("matkg_")
        terms_path = STORAGE / "terminology" / f"extracted_terms_{stem}.json"
        if not terms_path.is_file():
            continue
        marks = [pytest.mark.large] if terms_path.stat().st_size > LARGE_BYTES else []
        params.append(pytest.param(stem, id=stem, marks=marks))
    return params


def _triples(graph: dict[str, Any]) -> collections.Counter[tuple[str, str, str]]:
    """Return the edge triples of a flat graph as a multiset."""
    return collections.Counter((e["subject"], e["predicate"], e["object"]) for e in graph["associations"])


def _node_view(node: dict[str, Any], like: dict[str, Any] | None) -> dict[str, Any]:
    """Project a node onto the keys of ``like`` (when given) and normalise store-induced differences."""
    view: dict[str, Any] = {}
    for key, value in node.items():
        if key in VOLATILE or (like is not None and key not in like):
            continue
        if key == "properties":
            # PropertyRecord re-adds verified/uncertainty: compare the identifying fields only.
            value = [
                (p.get("property"), p.get("value"), p.get("unit"), p.get("context")) if isinstance(p, dict) else p
                for p in value
            ]
        view[key] = value
    if like is not None and like.get("publications") == [] and view.get("publications"):
        # A node created by a relation before its own term has no publication entry; a node built from its
        # own single-paper term gets one derived from source_papers (newer behaviour): not a difference.
        papers = node.get("source_papers") or []
        if len(papers) <= 1 and all(set(pub) <= {"source_paper"} for pub in view["publications"]):
            view["publications"] = []
    return view


def canon(graph: dict[str, Any], like: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Canonical form: nodes sorted by id, edges by triple; with ``like`` only keys present in it are kept."""
    like_nodes = {n["id"]: n for n in like["things"]} if like is not None else {}
    nodes = [
        _node_view(n, like_nodes.get(n["id"]))
        for n in sorted(graph["things"], key=lambda n: str(n["id"]))
    ]
    edges = sorted(
        graph["associations"],
        key=lambda e: (e["subject"], e["predicate"], e["object"], json.dumps(e, sort_keys=True, default=str)),
    )
    return nodes, edges


def _build(terms_doc: dict[str, Any]) -> dict[str, Any]:
    """Run the forward conversion on a terms document the way ``convert_terms_to_graph`` does."""
    meta = terms_doc.get("metadata") or {}
    prefix = str(meta.get("id_prefix") or "matkg") if isinstance(meta, dict) else "matkg"
    return build_graph_isolated(terms_doc["terms"], terms_doc.get("code_snippets") or [], default_id_prefix=prefix)


def _store_roundtrip(terms_doc: dict[str, Any], tmp_path: Path) -> tuple[dict[str, Any], TermStore]:
    """Load terms through ``TermStore`` (load, assign_importance, save) and rebuild a KG with ``rebuild_kg``."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    terms_path = tmp_path / "terms.json"
    terms_path.write_text(json.dumps(terms_doc), encoding="utf-8")
    store = TermStore(str(terms_path))
    store.assign_importance()
    store.save()
    kg_path = tmp_path / "kg.json"
    rebuild_kg(str(terms_path), str(kg_path))
    return json.loads(kg_path.read_text(encoding="utf-8")), store


@pytest.mark.parametrize("stem", _pairs())
def test_round_trip_over_every_pair(stem: str, tmp_path: Path) -> None:
    """terms -> KG -> kg_to_terms -> TermStore -> rebuild equals the KG up to ordering and newer fields."""
    terms_doc = json.loads((STORAGE / "terminology" / f"extracted_terms_{stem}.json").read_text(encoding="utf-8"))
    disk = json.loads((STORAGE / "kg" / f"matkg_{stem}.json").read_text(encoding="utf-8"))
    if not isinstance(terms_doc, dict) or "terms" not in terms_doc or "things" not in disk:
        pytest.skip("terms file or KG file is not in the {terms} / {things, associations} shape")
    kg0 = _build(terms_doc)
    if {n["id"] for n in kg0["things"]} != {n["id"] for n in disk["things"]} or _triples(kg0) != _triples(disk):
        pytest.skip(
            f"current forward conversion does not reproduce the on-disk KG "
            f"(nodes {len(kg0['things'])} vs {len(disk['things'])}, edges {len(kg0['associations'])} vs "
            f"{len(disk['associations'])})"
        )
    del terms_doc, disk
    try:
        result = kg_to_terms(kg0, source=stem)
        dropped: set[str] = set()
    except KgToTermsError as exc:
        # Real KGs hold names that differ only by case ('Bandgap'/'bandgap'); TermStore would merge them, so the
        # default call refuses. Compare the lossy conversion on everything except the dropped terms.
        assert "collision" in str(exc), exc
        result = kg_to_terms(kg0, source=stem, lossy=True)
        dropped = set(result.dropped_terms)
        assert dropped
    cycles = [w for w in result.warnings if "relation cycles" in w]
    if cycles:
        pytest.skip(f"forward build_graph cannot rebuild data of terms in relation cycles: {cycles[0][:100]}")

    kg1, store = _store_roundtrip(result.terms, tmp_path)
    shutil.rmtree(tmp_path, ignore_errors=True)  # the pairs are large: do not keep every rebuilt KG on disk
    assert len(store) == len(result.terms["terms"]), "TermStore skipped or merged a reconstructed term"

    if dropped:
        # the dropped terms' own data and outgoing edges are gone by design (they stay as stubs if referenced)
        for graph in (kg0, kg1):
            graph["things"] = [n for n in graph["things"] if n["id"] not in dropped]
            graph["associations"] = [e for e in graph["associations"] if e["subject"] not in dropped]
        kg0["things"] = [n for n in kg0["things"] if n["id"] in {m["id"] for m in kg1["things"]}]
    assert canon(kg1, like=kg0) == canon(kg0, like=kg0)
    assert _triples(kg1) == _triples(kg0)
    nodes1 = {n["id"]: n for n in kg1["things"]}
    for node in kg0["things"]:
        other = nodes1[node["id"]]
        for key in ("name", "category", "pages", "source_papers", "context_snippets", "formula", "formula_validation"):
            assert other.get(key) == node.get(key), (node["id"], key)
        assert [_p(p) for p in other["properties"]] == [_p(p) for p in node["properties"]], node["id"]
        if "importance" in other:
            assert other["importance"] == TermStore.importance_tier(other["pages"], other["source_papers"])
    if not dropped:
        assert result.processed_pdfs == sorted({p for n in kg0["things"] for p in n.get("source_papers") or []})


def _p(prop: Any) -> Any:
    """Identifying fields of a property dict."""
    return (prop.get("property"), prop.get("value"), prop.get("unit"), prop.get("context")) if isinstance(prop, dict) else prop


SMALL_KG = "deepseek-r1_70b_25_20250925_103657"


def test_continuation_adds_papers_without_changing_the_old_subgraph(tmp_path: Path) -> None:
    """Reverse a real small KG, add two synthetic papers' terms, rebuild: old subgraph unchanged, new one present."""
    disk = json.loads((STORAGE / "kg" / f"matkg_{SMALL_KG}.json").read_text(encoding="utf-8"))
    result = kg_to_terms(disk, source=SMALL_KG)
    base_terms = result.terms
    kg_base, store = _store_roundtrip(base_terms, tmp_path / "base")
    assert _triples(kg_base) == _triples(disk)
    assert canon(kg_base, like=disk) == canon(disk, like=disk)

    existing = next(r for r in store.all_records() if r.relations and r.source_papers)
    old_names = {r.term for r in store.all_records()}
    old_papers = set(result.processed_pdfs)
    new_papers = ["synthetic_paper_A.pdf", "synthetic_paper_B.pdf"]
    additions = [
        TermRecord(
            term="synthetic alpha phase", definition="Added from paper A", category="Material",
            pages=[1, 2], source_papers=[new_papers[0]],
            relations=[
                RelationRecord(relation="related_to", related_term=existing.term, evidence=["synthetic evidence"]),
                RelationRecord(relation="part_of", related_term="synthetic beta film"),
            ],
        ),
        TermRecord(
            term="synthetic beta film", definition="Added from paper B", category="Material",
            pages=[4], source_papers=[new_papers[1]],
            relations=[RelationRecord(relation="uses", related_term="synthetic alpha phase")],
        ),
        # an existing term seen again in a new paper
        TermRecord(
            term=existing.term, definition="", category=existing.category,
            pages=[99], source_papers=[new_papers[1]],
        ),
    ]
    for record in additions:
        store.upsert(record)
    store.assign_importance()
    store.save()
    kg_new_path = tmp_path / "kg_new.json"
    rebuild_kg(store.output_file, str(kg_new_path))
    kg_new = json.loads(kg_new_path.read_text(encoding="utf-8"))

    new_nodes = {n["id"]: n for n in kg_new["things"]}
    touched = {json2kg.make_id(existing.term)}
    for node in kg_base["things"]:
        after = new_nodes[node["id"]]
        if node["id"] in touched:
            assert set(node["pages"]) <= set(after["pages"])
            assert set(node["source_papers"]) <= set(after["source_papers"])
            assert after["description"] == node["description"]
            continue
        assert _node_view(after, node) == _node_view(node, node), node["id"]
    assert set(_triples(kg_base)) <= set(_triples(kg_new))
    added_edges = set(_triples(kg_new)) - set(_triples(kg_base))
    alpha, beta = json2kg.make_id("synthetic alpha phase"), json2kg.make_id("synthetic beta film")
    assert (alpha, "rel:related_to", json2kg.make_id(existing.term)) in added_edges
    assert (alpha, "rel:part_of", beta) in added_edges and (beta, "rel:uses", alpha) in added_edges
    assert new_nodes[alpha]["source_papers"] == [new_papers[0]]
    assert new_nodes[beta]["source_papers"] == [new_papers[1]]
    assert new_papers[1] in new_nodes[json2kg.make_id(existing.term)]["source_papers"]
    assert {n["id"] for n in kg_new["things"]} - {n["id"] for n in kg_base["things"]} == {alpha, beta}
    assert old_names <= {n["name"] for n in kg_new["things"]}
    assert old_papers.isdisjoint(new_papers)
    edge = next(e for e in kg_new["associations"] if (e["subject"], e["object"]) == (alpha, json2kg.make_id(existing.term)))
    assert edge["has_evidence"] == "synthetic evidence"


KG_ONLY = ["rsoxs_v2", "rsoxs_v3", "bl1101_v6", "bl1101_v7", "bl1101_v8", "bl1101_v9", "with_code"]


@pytest.mark.parametrize("stem", KG_ONLY)
def test_idempotence_for_kgs_without_a_terms_file(stem: str) -> None:
    """kg_to_terms -> build_graph reproduces the KG; unrepresentable KGs are refused unless lossy.

    Every KG without a terms file has TermStore key collisions (``JSC``/``Jsc``, beamline ``Software-X`` and
    ``repo-X`` with the same name), duplicate node ids, or predicates without ``rel:``. The default call
    refuses them with the documented message; ``lossy=True`` keeps the first colliding term and must then
    reproduce every node and every edge except the dropped terms' own. Beamline fields outside ``TermRecord``
    (``pv``, ``ophyd_*``, ``type`` ...) pass through ``TermRecord.extra`` and come back on the nodes.
    """
    kg = json.loads((STORAGE / "kg" / f"matkg_{stem}.json").read_text(encoding="utf-8"))
    with pytest.raises(KgToTermsError, match=r"collision|duplicate node ids|without the 'rel:' prefix"):
        kg_to_terms(kg, source=stem)
    result = kg_to_terms(kg, source=stem, lossy=True)
    assert result.warnings, "lossy conversion must say what it changed"
    meta = result.terms["metadata"]
    assert meta["kg_to_terms"] == {"source_kg": stem, "reconstructed": True, "extractor": "unknown"}
    rebuilt = build_graph_isolated(
        result.terms["terms"], result.terms["code_snippets"], default_id_prefix=str(meta.get("id_prefix") or "matkg")
    )

    nodes = kg.get("things") or kg["nodes"]
    edges = kg.get("associations") or kg["edges"]
    adapted = "nodes" in kg
    if adapted:  # prov nodes/edges describe the earlier conversion run and are dropped by the adapter
        nodes = [n for n in nodes if not str(n.get("entityType", "")).startswith("prov:")]
        edges = [e for e in edges if not str(e.get("type", "")).startswith("prov:")]
    dropped = set(result.dropped_terms)
    old_ids = {n["id"] for n in nodes}
    new_ids = {n["id"] for n in rebuilt["things"]}
    # a stub only reachable from a dropped term disappears with it
    referenced_elsewhere = {
        e["object"] for e in edges if e["subject"] not in dropped and e["predicate"] != "rel:has_code_snippet"
    }
    assert old_ids - new_ids <= dropped | {i for i in old_ids - new_ids if i not in referenced_elsewhere}
    # build_graph de-duplicates edges, so compare sets of triples (the nodes/edges file repeats some)
    old_edges = {
        (e["subject"], e["predicate"] if e["predicate"].startswith("rel:") else f"rel:{e['predicate']}", e["object"])
        for e in edges
    }
    new_edges = set(_triples(rebuilt))
    lost = {k for k in old_edges - new_edges if k[1] != CODE_EDGE}
    assert all(k[0] in dropped for k in lost), sorted(lost)[:5]
    gained = {k for k in new_edges - old_edges if k[1] != CODE_EDGE}
    assert not gained, sorted(gained)[:5]
    if adapted:
        # edges to ids that have no node in the nodes/edges file come back as stub nodes
        assert new_ids - old_ids <= {k[2] for k in old_edges}
    else:
        assert new_ids <= old_ids
    # has_code_snippet wiring follows node order (the first node of a paper gets a paper-level snippet), so the
    # wired snippets are compared, not the nodes they hang off
    old_code = {k[2] for k in old_edges if k[1] == CODE_EDGE}
    new_code = {k[2] for k in new_edges if k[1] == CODE_EDGE}
    assert new_code <= old_code and len(old_code - new_code) <= 2, sorted(old_code ^ new_code)[:5]
    if stem.startswith("bl1101"):
        node_by_id = {n["id"]: n for n in rebuilt["things"]}
        pv_nodes = [n for n in nodes if "pv" in n and n["id"] in node_by_id and n["id"] not in dropped]
        assert pv_nodes and all(node_by_id[n["id"]].get("pv") == n["pv"] for n in pv_nodes)
