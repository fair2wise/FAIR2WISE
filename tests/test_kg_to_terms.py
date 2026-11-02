"""Unit tests for kg_to_terms and the TermStore/TermRecord hardening it relies on."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.modules import json2kg
from app.modules.f2w_agent.coordinator import _load_extraction_manifest
from app.modules.graph.snapshot import build_graph_isolated
from app.modules.term_extractor.kg_to_terms import (
    KgToTermsError,
    app,
    detect_shape,
    kg_to_terms,
    processed_pdfs_for,
    reconstructed_extraction_manifest,
)
from app.modules.term_extractor.models import RelationRecord, TermRecord
from app.modules.term_extractor.store import TermStore

CODE = (
    "import numpy as np\nfrom scipy.signal import find_peaks\n"
    "def find_scattering_peaks(q, intensity):\n    y = np.asarray(intensity, dtype=float)\n"
    "    peaks, props = find_peaks(y, prominence=0.05 * np.max(y))\n    return peaks, props\n"
) * 2


def _terms() -> list[dict[str, Any]]:
    """A small terms list: a hub, a relation to a stub, a relation with evidence, extra keys and snippets."""
    return [
        {
            "term": "P3HT", "definition": "A polymer.", "category": "Polymer", "pages": [1, 2, 3],
            "source_papers": ["a.pdf"], "formula": "C10H14S", "formula_validation": {"status": "ok"},
            "context_snippets": [{"text": "P3HT film", "source_paper": "a.pdf", "page": 1}],
            "properties": [{"property": "Mw", "value": 50, "unit": "kDa", "context": "Mw 50 kDa"}],
            "source_metadata": {"a.pdf": {"paper_title": "Paper A", "doi": "10.1/a"}},
            "relations": [
                {"relation": "used_in", "related_term": "organic solar cell", "evidence": ["ev one", "ev two"]},
                {"relation": "measured_by", "related_term": "GIWAXS", "raw_predicate": "is measured by"},
            ],
            "pv": "SIM:p3ht", "type": "beamline:Polymer",
        },
        {"term": "GIWAXS", "definition": "", "category": "ExperimentalTechnique", "source_papers": ["a.pdf", "b.pdf"],
         "pages": [4]},
        {"term": "Odd Name", "id": "beamline:Odd-id", "definition": "Explicit id.", "category": "Thing",
         "source_papers": ["b.pdf"], "id_prefix": "beamline", "pages": [9],
         "relations": [{"relation": "part_of", "related_term": "P3HT"}]},
    ]


def _snippets() -> list[dict[str, Any]]:
    """One curated code snippet on a paper/page that a term mentions."""
    return [{"function_name": "find_scattering_peaks", "code_snippet": CODE, "code_language": "python",
             "code_description": "Find peaks.", "source_paper": "a.pdf", "page": 1, "curated": True,
             "repo_url": "https://example.org/r", "domain_features": [
                 {"feature_name": "technique", "feature_value": "SAXS", "feature_units": None,
                  "feature_source_text": None}]}]


def _kg() -> dict[str, Any]:
    """The forward conversion of ``_terms``/``_snippets``."""
    return build_graph_isolated(_terms(), _snippets())


def _triples(kg: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Edge triples of a flat KG."""
    return {(e["subject"], e["predicate"], e["object"]) for e in kg["associations"]}


# --- models and store hardening -------------------------------------------------------------------------


def test_term_record_extra_round_trip_keeps_unknown_keys_in_order() -> None:
    """Unknown top-level keys survive from_dict/to_dict, after the known fields, in insertion order."""
    raw = {"term": "X", "zeta": 1, "alpha": {"n": 2}, "definition": "d", "extra": "literal"}
    record = TermRecord.from_dict(raw)
    assert list(record.extra) == ["zeta", "alpha", "extra"]
    out = record.to_dict()
    assert list(out)[-3:] == ["zeta", "alpha", "extra"]
    assert out["extra"] == "literal" and out["alpha"] == {"n": 2}
    assert TermRecord.from_dict(out).to_dict() == out
    assert "extra" not in TermRecord(term="Y", definition="", category="C").to_dict()


def test_relation_record_evidence_and_ids() -> None:
    """RelationRecord reads evidence (list or str) and related ids and omits empty optional keys on output."""
    assert RelationRecord.from_dict({"relation": "r", "related_term": "t", "evidence": "one"}).evidence == ["one"]
    rel = RelationRecord.from_dict({"relation": "r", "related_term": "t", "evidence": ["a", "b"], "related_id": "matkg:T"})
    assert (rel.evidence, rel.related_id) == (["a", "b"], "matkg:T")
    plain = TermRecord(term="X", definition="", category="C", relations=[RelationRecord("r", "t")]).to_dict()
    assert set(plain["relations"][0]) == {"relation", "related_term", "verified", "raw_predicate"}
    full = TermRecord(term="X", definition="", category="C", relations=[rel]).to_dict()
    assert full["relations"][0]["evidence"] == ["a", "b"] and full["relations"][0]["related_id"] == "matkg:T"


def test_extra_keys_reach_the_graph_like_unknown_keys_do_today(tmp_path: Path) -> None:
    """A key carried in TermRecord.extra lands on the node via copy_passthrough_slots, evidence on the edge."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"terms": [{"term": "A", "pv": "SIM:a", "relations": [
        {"relation": "r", "related_term": "B", "evidence": ["e1", "e2"]}]}]}))
    store = TermStore(str(path))
    store.save()
    graph = build_graph_isolated(json.loads(path.read_text())["terms"], [])
    node = next(n for n in graph["things"] if n["id"] == "matkg:A")
    assert node["pv"] == "SIM:a"
    assert graph["associations"][0]["has_evidence"] == "e1; e2"


def test_store_load_skips_a_bad_record_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """One unreadable record is skipped and logged; the others still load."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"terms": [
        {"term": "Good"}, {"definition": "no term key"}, "not a dict",
        {"term": "BadSnippet", "context_snippets": [{"text": "x"}]}, {"term": "Also Good"}]}))
    with caplog.at_level(logging.WARNING, logger="app.modules.term_extractor.store"):
        store = TermStore(str(path))
    assert sorted(r.term for r in store.all_records()) == ["Also Good", "Good"]
    assert sum("Skipping unreadable term record" in r.message for r in caplog.records) == 3


def test_store_load_ignores_unknown_counters_and_increment_handles_none(tmp_path: Path) -> None:
    """None counters (reconstructed files) keep the in-memory default and increment works."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"metadata": {"processed_pages_total": None, "processed_files": 3}, "terms": []}))
    store = TermStore(str(path))
    assert store.metadata["processed_pages_total"] == 0 and store.metadata["processed_files"] == 3
    store.metadata["processed_pages_total"] = None
    store.increment("processed_pages_total", 2)
    assert store.metadata["processed_pages_total"] == 2


# --- kg_to_terms ------------------------------------------------------------------------------------------


def test_round_trip_reproduces_ids_edges_and_fields() -> None:
    """kg_to_terms -> build_graph reproduces node ids, edge triples and the node fields."""
    kg = _kg()
    before = copy.deepcopy(kg)
    result = kg_to_terms(kg, source="unit")
    assert kg == before, "the input KG is never modified"
    rebuilt = build_graph_isolated(result.terms["terms"], result.terms["code_snippets"])
    assert {n["id"] for n in rebuilt["things"]} == {n["id"] for n in kg["things"]}
    assert _triples(rebuilt) == _triples(kg)
    old = {n["id"]: n for n in kg["things"]}
    for node in rebuilt["things"]:
        for key, value in old[node["id"]].items():
            if key != "importance":
                assert node.get(key) == value, (node["id"], key)


def test_term_mapping_and_metadata() -> None:
    """Names, definitions ('N/A' -> ''), importance, explicit ids, extras, evidence and metadata."""
    result = kg_to_terms(_kg(), source="unit.json")
    by_name = {t["term"]: t for t in result.terms["terms"]}
    assert set(by_name) == {"P3HT", "GIWAXS", "Odd Name"}, "stub 'organic solar cell' must not become a term"
    assert by_name["GIWAXS"]["definition"] == "" and by_name["P3HT"]["definition"] == "A polymer."
    assert by_name["P3HT"]["importance"] == "medium" and by_name["GIWAXS"]["importance"] == "high"
    assert "id" not in by_name["P3HT"] and by_name["Odd Name"]["id"] == "Odd-id"
    assert by_name["Odd Name"]["id_prefix"] == "beamline"
    assert by_name["P3HT"]["pv"] == "SIM:p3ht" and by_name["P3HT"]["type"] == "beamline:Polymer"
    rels = {r["relation"]: r for r in by_name["P3HT"]["relations"]}
    assert rels["used_in"]["evidence"] == ["ev one", "ev two"] and rels["used_in"]["related_term"] == "organic solar cell"
    assert rels["measured_by"]["raw_predicate"] == "is measured by"
    assert by_name["P3HT"]["publications"] == [{"source_paper": "a.pdf", "paper_title": "Paper A", "doi": "10.1/a"}]
    assert result.dropped_stubs == 2  # matkg:organicsolarcell stub and the beamline:P3HT stub of 'Odd Name'
    assert result.processed_pdfs == ["a.pdf", "b.pdf"]
    meta = result.terms["metadata"]
    assert meta["kg_to_terms"] == {"source_kg": "unit.json", "reconstructed": True, "extractor": "unknown"}
    assert meta["processed_files"] == 2 and meta["version"] == "2.1"
    assert meta["processed_pages_total"] is None and meta["processed_pages_with_terms"] is None


def test_stubs_are_not_promoted_but_an_orphan_or_a_subject_is_kept() -> None:
    """A stub (no papers, no description, only pointed at) is dropped; a node with edges or no incoming edge stays."""
    kg = {"things": [
        {"id": "matkg:A", "name": "A", "category": "C", "description": "d", "source_papers": ["p.pdf"]},
        {"id": "matkg:stub", "name": "stub", "category": "Unknown", "description": "", "source_papers": []},
        {"id": "matkg:orphan", "name": "orphan", "category": "Unknown", "description": "", "source_papers": []},
        {"id": "matkg:filled", "name": "filled", "category": "Unknown", "description": "", "source_papers": []},
    ], "associations": [
        {"subject": "matkg:A", "predicate": "rel:r", "object": "matkg:stub", "has_evidence": None},
        {"subject": "matkg:A", "predicate": "rel:r", "object": "matkg:filled", "has_evidence": None},
        {"subject": "matkg:filled", "predicate": "rel:r", "object": "matkg:A", "has_evidence": None},
    ]}
    result = kg_to_terms(kg)
    assert {t["term"] for t in result.terms["terms"]} == {"A", "orphan", "filled"}
    assert result.dropped_stubs == 1
    rebuilt = build_graph_isolated(result.terms["terms"], [])
    assert {n["id"] for n in rebuilt["things"]} == {n["id"] for n in kg["things"]}
    assert _triples(rebuilt) == _triples(kg)


def test_code_snippets_are_reversed_and_has_code_snippet_is_regenerated() -> None:
    """CodeSnippet nodes become curated code_snippets; synthesised has_code_snippet edges are not reversed."""
    kg = _kg()
    synthesised = {t for t in _triples(kg) if t[1] == "rel:has_code_snippet"}
    assert synthesised, "fixture must wire a snippet"
    result = kg_to_terms(kg)
    snip = result.terms["code_snippets"][0]
    assert snip["curated"] is True and snip["source_paper"] == "a.pdf" and snip["page"] == 1
    assert snip["function_name"] == "find_scattering_peaks" and snip["domain_features"][0]["feature_value"] == "SAXS"
    assert "id" not in snip
    for term in result.terms["terms"]:
        assert all(r["relation"] != "has_code_snippet" for r in term["relations"])
    assert {t for t in _triples(build_graph_isolated(result.terms["terms"], result.terms["code_snippets"]))
            if t[1] == "rel:has_code_snippet"} == synthesised
    assert result.processed_pdfs == ["a.pdf", "b.pdf"]


def test_explicit_has_code_snippet_relation_with_evidence_is_kept() -> None:
    """A has_code_snippet edge carrying evidence is a real relation (beamline KGs), not a synthesised one."""
    terms = [{"term": "plan", "source_papers": ["r"], "relations": [
        {"relation": "has_code_snippet", "related_id": "beamline:snip-1", "related_term": "snip 1", "evidence": ["repo"]}]}]
    snippets = [{"id": "snip-1", "id_prefix": "beamline", "function_name": "f", "source_paper": "r", "page": 0,
                 "curated": True, "code_snippet": "def f():\n    return 1\n"}]
    kg = build_graph_isolated(terms, snippets)
    result = kg_to_terms(kg)
    rel = result.terms["terms"][0]["relations"][0]
    assert rel["relation"] == "has_code_snippet" and rel["evidence"] == ["repo"] and rel["related_id"] == "beamline:snip-1" and "related_id_prefix" not in rel
    assert result.terms["code_snippets"][0]["id"] == "snip-1"
    rebuilt = build_graph_isolated(result.terms["terms"], result.terms["code_snippets"])
    assert _triples(rebuilt) == _triples(kg)


def test_key_collision_fails_closed_unless_lossy() -> None:
    """Two terms with the same TermStore key are listed in the error; lossy keeps the first and warns."""
    kg = {"things": [
        {"id": "matkg:Bandgap", "name": "Bandgap", "category": "C", "description": "x", "source_papers": ["p"]},
        {"id": "matkg:bandgap", "name": "bandgap", "category": "C", "description": "y", "source_papers": ["q"]},
        {"id": "matkg:other", "name": "other", "category": "C", "description": "z", "source_papers": ["p"]},
    ], "associations": []}
    with pytest.raises(KgToTermsError, match=r"collision.*'bandgap'.*matkg:Bandgap.*matkg:bandgap"):
        kg_to_terms(kg)
    result = kg_to_terms(kg, lossy=True)
    assert [t["term"] for t in result.terms["terms"]] == ["Bandgap", "other"]
    assert result.dropped_terms == ["matkg:bandgap"] and any("collision" in w for w in result.warnings)


def test_reference_order_keeps_data_of_terms_a_relation_would_stub_first() -> None:
    """A full term that appears after the term pointing at it in KG order is emitted first, so no data is lost."""
    kg = {"things": [
        {"id": "matkg:A", "name": "A", "category": "C", "description": "a", "source_papers": ["p"], "pages": [1]},
        {"id": "matkg:B", "name": "B", "category": "C", "description": "b", "source_papers": ["p"], "pages": [2, 3]},
    ], "associations": [{"subject": "matkg:A", "predicate": "rel:r", "object": "matkg:B", "has_evidence": None}]}
    result = kg_to_terms(kg)
    assert [t["term"] for t in result.terms["terms"]] == ["B", "A"]
    rebuilt = {n["id"]: n for n in build_graph_isolated(result.terms["terms"], [])["things"]}
    assert rebuilt["matkg:B"]["pages"] == [2, 3]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "must be an object"),
        ({"foo": 1}, "unrecognised KG shape"),
        ({"things": [], "associations": [], "nodes": [], "edges": []}, "ambiguous"),
        ({"things": {}, "associations": []}, "must be lists"),
        ({"things": [{"name": "no id"}], "associations": []}, "'id'"),
        ({"things": [], "associations": [{"subject": "a"}]}, "subject, predicate and object"),
    ],
)
def test_unreadable_shapes_are_rejected_with_a_clear_message(document: Any, message: str) -> None:
    """Shapes the tool cannot read raise KgToTermsError naming the problem."""
    with pytest.raises(KgToTermsError, match=message):
        kg_to_terms(document)


def test_duplicate_node_ids_fail_closed_unless_lossy() -> None:
    """Repeated node ids are refused; lossy keeps the first."""
    kg = {"things": [{"id": "matkg:A", "name": "A"}, {"id": "matkg:A", "name": "A2"}], "associations": []}
    with pytest.raises(KgToTermsError, match="duplicate node ids"):
        kg_to_terms(kg)
    assert [t["term"] for t in kg_to_terms(kg, lossy=True).terms["terms"]] == ["A"]


def test_non_rel_predicates_are_refused_unless_lossy() -> None:
    """build_graph always writes rel:<predicate>, so other predicates cannot round trip."""
    kg = {"things": [{"id": "matkg:A", "name": "A", "description": "d", "source_papers": ["p"]},
                     {"id": "matkg:B", "name": "B", "description": "d", "source_papers": ["p"]}],
          "associations": [{"subject": "matkg:A", "predicate": "prov:used", "object": "matkg:B", "has_evidence": None}]}
    with pytest.raises(KgToTermsError, match="without the 'rel:' prefix"):
        kg_to_terms(kg)
    assert kg_to_terms(kg, lossy=True).terms["terms"][0]["relations"][0]["relation"] == "prov:used"


def test_nodes_edges_adapter_maps_and_documents_every_field() -> None:
    """The matkg_rsoxs_v3 shape: label/entityType/papers/slots mapped, prov dropped, everything else warned."""
    kg = {
        "graph_id": "g", "prov_metadata": {"type": "prov:Entity"},
        "nodes": [
            {"id": "matkg:PffBT", "label": "PffBT", "entityType": "ConjugatedPolymer", "description": "polymer",
             "aliases": ["PffBT4T"], "papers": ["10.1/x"], "pages": [3],
             "slots": {"photon_energy_eV": 284.5, "geometry": "GIWAXS", "q_range": None}, "graph_id": "g"},
            {"id": "rsoxs:Tech-giwaxs", "label": "GIWAXS", "entityType": "Technique", "description": "t",
             "papers": ["10.1/x"], "pages": [], "slots": {}, "graph_id": "g"},
            {"id": "rsoxs:Activity-run", "label": "run", "entityType": "prov:Activity", "type": "prov:Activity",
             "description": "run", "graph_id": "g"},
        ],
        "edges": [
            {"subject": "matkg:PffBT", "predicate": "rel:measured_by", "object": "rsoxs:Tech-giwaxs", "has_evidence": "ev"},
            {"subject": "matkg:PffBT", "predicate": "rel:used_in", "object": "matkg:missing-target"},
            {"subject": "matkg:PffBT", "predicate": "http://www.w3.org/ns/prov#wasGeneratedBy",
             "object": "rsoxs:Activity-run", "type": "prov:wasGeneratedBy"},
        ],
    }
    assert detect_shape(kg) == "nodes_edges"
    result = kg_to_terms(kg, source="v3")
    terms = {t["term"]: t for t in result.terms["terms"]}
    assert set(terms) == {"PffBT", "GIWAXS"}
    polymer = terms["PffBT"]
    assert polymer["category"] == "ConjugatedPolymer" and polymer["source_papers"] == ["10.1/x"] and polymer["pages"] == [3]
    assert polymer["definition"] == "polymer" and polymer["aliases"] == ["PffBT4T"]
    assert polymer["photon_energy_eV"] == 284.5 and polymer["geometry"] == "GIWAXS" and "q_range" not in polymer
    assert terms["GIWAXS"]["id"] == "Tech-giwaxs" and terms["GIWAXS"]["id_prefix"] == "rsoxs"
    assert result.terms["metadata"]["graph_id"] == "g"
    text = "\n".join(result.warnings)
    for needle in ("label->name", "prov:* nodes", "aliases", "slots:", "graph_id", "DOIs", "edges point at ids with no node"):
        assert needle in text, needle
    rebuilt = build_graph_isolated(result.terms["terms"], [])
    ids = {n["id"] for n in rebuilt["things"]}
    assert {"matkg:PffBT", "rsoxs:Tech-giwaxs", "matkg:missing-target"} == ids
    assert _triples(rebuilt) == {("matkg:PffBT", "rel:measured_by", "rsoxs:Tech-giwaxs"),
                                 ("matkg:PffBT", "rel:used_in", "matkg:missing-target")}
    node = next(n for n in rebuilt["things"] if n["id"] == "matkg:PffBT")
    assert node["aliases"] == ["PffBT4T"] and node["photon_energy_eV"] == 284.5


def test_cross_prefix_relation_targets_use_related_id_prefix() -> None:
    """A matkg: term pointing at an rsoxs: node round trips through related_id + related_id_prefix."""
    terms = [{"term": "a", "source_papers": ["p"], "relations": [
        {"relation": "r", "related_term": "Tech", "related_id": "Tech-x", "related_id_prefix": "rsoxs"}]},
        {"term": "Tech", "id": "Tech-x", "id_prefix": "rsoxs", "source_papers": ["p"], "definition": "d"}]
    kg = build_graph_isolated(terms, [])
    assert ("matkg:a", "rel:r", "rsoxs:Tech-x") in _triples(kg)
    result = kg_to_terms(kg)
    rel = next(t for t in result.terms["terms"] if t["term"] == "a")["relations"][0]
    assert rel["related_id"] == "Tech-x" and rel["related_id_prefix"] == "rsoxs"
    assert _triples(build_graph_isolated(result.terms["terms"], [])) == _triples(kg)


# --- companion outputs and CLI ----------------------------------------------------------------------------


def test_processed_pdfs_and_reconstructed_manifest(tmp_path: Path) -> None:
    """processed_pdfs_for unions terms and snippets; the manifest is 'reconstructed', never 'full'."""
    doc = kg_to_terms(_kg()).terms
    assert processed_pdfs_for(doc) == ["a.pdf", "b.pdf"]
    manifest = reconstructed_extraction_manifest(doc, terms_json="terms.json", timestamp="2026-01-01T00:00:00+00:00")
    assert set(manifest["papers"]) == {"a.pdf", "b.pdf"}
    entry = manifest["papers"]["a.pdf"]
    assert entry["extraction_state"] == "reconstructed" and entry["pdf_sha256"] is None and "full" not in entry
    assert {"filename", "pdf_sha256", "extraction_state", "timestamp", "terms_json",
            "processed_pages_total", "processed_pages_with_terms"} <= set(entry)
    path = tmp_path / "extraction_manifest.json"
    path.write_text(json.dumps(manifest))
    assert _load_extraction_manifest(path) == manifest, "the coordinator reads the file as is"


def test_cli_convert_writes_terms_and_manifests(tmp_path: Path) -> None:
    """`convert KG OUT --manifest-dir DIR` writes the terms, processed_pdfs.json and extraction_manifest.json."""
    kg_path, out = tmp_path / "kg.json", tmp_path / "out" / "terms.json"
    kg_path.write_text(json.dumps(_kg()))
    result = CliRunner().invoke(app, ["convert", str(kg_path), str(out), "--manifest-dir", str(tmp_path / "m")])
    assert result.exit_code == 0, result.output
    assert "Wrote 3 terms and 1 code snippets" in result.output
    doc = json.loads(out.read_text())
    assert doc["metadata"]["kg_to_terms"]["source_kg"] == str(kg_path)
    assert json.loads((tmp_path / "m" / "processed_pdfs.json").read_text()) == ["a.pdf", "b.pdf"]
    manifest = json.loads((tmp_path / "m" / "extraction_manifest.json").read_text())
    assert manifest["papers"]["a.pdf"]["extraction_state"] == "reconstructed"
    assert manifest["papers"]["a.pdf"]["terms_json"] == str(out)
    assert json2kg.build_graph(doc["terms"], doc["code_snippets"])["things"]


def test_cli_exit_codes_and_lossy_flag(tmp_path: Path) -> None:
    """Collisions exit 2 unless --lossy; unreadable input exits 1."""
    kg_path, out = tmp_path / "kg.json", tmp_path / "terms.json"
    kg_path.write_text(json.dumps({"things": [{"id": "matkg:A", "name": "A"}, {"id": "matkg:a", "name": "a"}],
                                   "associations": []}))
    runner = CliRunner()
    refused = runner.invoke(app, ["convert", str(kg_path), str(out)])
    assert refused.exit_code == 2 and "collision" in refused.output and not out.exists()
    assert runner.invoke(app, ["convert", str(kg_path), str(out), "--lossy"]).exit_code == 0
    assert [t["term"] for t in json.loads(out.read_text())["terms"]] == ["A"]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert runner.invoke(app, ["convert", str(bad), str(out)]).exit_code == 1
    assert runner.invoke(app, ["convert", str(tmp_path / "missing.json"), str(out)]).exit_code == 1
