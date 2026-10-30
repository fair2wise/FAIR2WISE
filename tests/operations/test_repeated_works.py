"""T19D / OPS-02: repeated Work references count as one Work under the approved rule (H3 K1).

H3 K1 (approved "as recommended"): Tool output counts **distinct Works**
(``work-identity/2``: equal content hash, equal DOI, equal arXiv id, or near-duplicate
title with agreeing first author; two different journal DOIs never merge); the legacy
CLIs keep their filename-key units. These tests make the rule executable on synthetic repeated-paper
populations for every operation that counts papers:

* the preprint / published pair (arXiv file labelled with the journal DOI, or sharing the
  journal title and first author), later arXiv versions and the ``10.48550`` arXiv-DOI stem;
* byte-identical duplicates: one file under several directories, a renamed copy whose
  metadata (extracted from the same bytes) is identical, byte-identical duplicate graph
  and term records;
* the same Work cited by two versions of a graph is not a new Work.

A renamed copy with no DOI, arXiv id or title merges only when its metadata carries the
file's sha256 (the content-hash step added by owner decision D1.9, work-identity/2); a copy
with no hash either stays a separate key-only Work and is disclosed as
``works_identified_by_key_only``. ``use_content_hash=False`` reproduces work-identity/1 and
the parity test shows both methods agree on a population without hashes.

Real-paper review of the rule waits for the H3 B4 pilot labels (papers A-C, owner-run
labelling then human review); these synthetic populations do not stand in for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations.corpus import CorpusSource, audit_corpus
from app.modules.operations.graph import compare_snapshots, compute_statistics, paper_subgraph
from app.modules.operations.reports import ScanControl
from tests.operations import scale_population as sp
from tests.runtime.runtime_fakes import local_ctx

TITLE = "Resonant soft x-ray scattering of a synthetic block copolymer film"
J_KEY, J_DOI = "10.1021_syn.77.pdf", "10.1021/syn.77"


def _entity(i, papers, pubs=None):
    node = {"id": f"syn:R{i}", "name": f"Repeated {i}", "category": "Material", "source_papers": papers,
            "description": sp.CANARY_DESCRIPTION}
    if pubs:
        node["publications"] = pubs
    return node


def _graph(tmp_path, name, things, assoc=()):
    path = sp.write_json(tmp_path / f"{name}.json", {"things": things, "associations": list(assoc)})
    return JsonGraphBackend({name: path})


def _corpus(tmp_path, terms, name="c"):
    path = sp.write_json(tmp_path / f"{name}.json", {"terms": terms})
    return audit_corpus(CorpusSource(name, str(path)))


def _term(i, papers, meta):
    return {"term": f"Repeated term {i}", "category": "Material", "pages": [1], "source_papers": papers,
            "definition": sp.CANARY_DEFINITION, "source_metadata": meta,
            "context_snippets": [{"text": sp.CANARY_SNIPPET, "source_paper": p, "page": 1} for p in papers]}


# -- preprint / published --------------------------------------------------------------------------

def test_preprint_versions_and_published_paper_are_one_work_in_a_graph(tmp_path):
    things = [
        _entity(1, [J_KEY]),
        _entity(2, ["2306.00077v1.pdf"], [{"source_paper": "2306.00077v1.pdf", "doi": J_DOI}]),  # labelled preprint
        _entity(3, ["2306.00077v2.pdf"]),  # later arXiv version: equal arXiv id
        _entity(4, ["10.48550_arXiv.2306.00077.pdf"]),  # arXiv DOI stem
        _entity(5, [J_KEY, "2306.00077v2.pdf"]),  # one entity citing two references of the Work
        _entity(6, ["10.1021_syn.78.pdf"]),  # another Work
    ]
    backend = _graph(tmp_path, "g", things, [{"subject": "syn:R1", "predicate": "rel:X", "object": "syn:R5"}])
    m = compute_statistics(backend, local_ctx(), "g").metrics
    assert (m["paper_keys"], m["distinct_works"], m["entities_with_citations"]) == (5, 2, 6)
    for ref in (J_KEY, J_DOI, "arXiv:2306.00077", "2306.00077v1.pdf", "10.48550/arXiv.2306.00077"):
        sub = paper_subgraph(backend, local_ctx(), "g", ref)
        assert sub.metrics["work_id"] == f"doi:{J_DOI}" and sub.metrics["entities"] == 5, ref
        assert sub.metrics["paper_key_count"] == 4 and sub.metrics["internal_relations"] == 1


def test_preprint_published_pair_by_title_is_one_work_in_the_corpus_audit(tmp_path):
    journal = {"paper_title": TITLE, "authors": ["Doe, J."], "publication_year": 2022, "doi": J_DOI}
    preprint = {"paper_title": TITLE + ".", "authors": ["J. Doe"], "publication_year": 2021}
    pre = "arXiv_2306.00077v1.pdf"
    terms = [_term(i, [J_KEY], {J_KEY: journal}) for i in range(3)]
    terms += [_term(10 + i, [pre], {pre: preprint}) for i in range(2)]
    terms += [_term(20, [J_KEY, pre], {J_KEY: journal, pre: preprint})]
    report = _corpus(tmp_path, terms)
    dup, summary = report.metrics["duplicate_works"], report.metrics["summary"]
    assert dup["legacy"]["unique_doi_keys"] == summary["legacy"]["total_papers"] == 2  # legacy unit: file keys
    assert dup["legacy"]["arxiv_journal_near_dupes"] == 1
    assert dup["distinct_works"] == summary["distinct_works"] == 1 and dup["works_with_multiple_keys"] == 1
    assert summary["papers_per_year_v2"] == {"2022": 1} and summary["works_with_conflicting_years"] == 1
    assert sum(summary["legacy"]["papers_per_year"].values()) == 7  # (term, metadata) increments, not papers


def test_different_journal_dois_with_one_title_stay_two_works(tmp_path):
    meta_a = {"paper_title": TITLE, "authors": ["Doe, J."], "doi": "10.1021/syn.1", "publication_year": 2020}
    meta_b = {"paper_title": TITLE, "authors": ["Doe, J."], "doi": "10.1021/syn.2", "publication_year": 2020}
    report = _corpus(tmp_path, [_term(1, ["10.1021_syn.1.pdf"], {"10.1021_syn.1.pdf": meta_a}),
                                _term(2, ["10.1021_syn.2.pdf"], {"10.1021_syn.2.pdf": meta_b})])
    assert report.metrics["duplicate_works"]["distinct_works"] == 2  # an erratum or comment is not the same Work


# -- byte-identical duplicates ---------------------------------------------------------------------

def test_one_file_under_several_directories_is_one_key_and_one_work(tmp_path):
    copies = ["papers/rsoxs/2021/syn_dup.pdf", "/abs/mirror/syn_dup.pdf", "syn_dup.pdf"]
    m = compute_statistics(_graph(tmp_path, "g", [_entity(i, [p]) for i, p in enumerate(copies)]),
                           local_ctx(), "g").metrics
    assert (m["paper_keys"], m["distinct_works"]) == (1, 1)
    report = _corpus(tmp_path, [_term(i, [p], {p: {"paper_title": TITLE}}) for i, p in enumerate(copies)])
    dup = report.metrics["duplicate_works"]
    assert dup["legacy"]["unique_doi_keys"] == 3 and dup["paper_keys"] == dup["distinct_works"] == 1


@pytest.mark.parametrize("meta", [{"doi": "10.1021/syn.5"}, {"paper_title": TITLE, "authors": ["Doe, J."]}])
def test_renamed_byte_identical_copy_with_the_same_metadata_is_one_work(tmp_path, meta):
    names = ["syn_paper.pdf", "syn_paper (1).pdf", "syn_paper_copy.pdf"]
    pubs = [{"source_paper": n, "doi": meta["doi"]} for n in names] if "doi" in meta else None
    things = [_entity(i, [n], pubs and [pubs[i]]) for i, n in enumerate(names)]
    if "doi" in meta:
        m = compute_statistics(_graph(tmp_path, "g", things), local_ctx(), "g").metrics
        assert (m["paper_keys"], m["distinct_works"]) == (3, 1)
    report = _corpus(tmp_path, [_term(i, [n], {n: dict(meta, publication_year=2020)}) for i, n in enumerate(names)])
    dup = report.metrics["duplicate_works"]
    assert (dup["paper_keys"], dup["distinct_works"], dup["works_with_multiple_keys"]) == (3, 1, 1)
    assert report.metrics["summary"]["papers_per_year_v2"] == {"2020": 1}


def test_renamed_copy_without_any_identifier_is_disclosed_not_merged(tmp_path):
    """Without a content hash an unlabelled renamed copy stays a separate key-only Work (as in work-identity/1)."""
    names = ["scan_0001.pdf", "scan_0001 (1).pdf"]
    report = _corpus(tmp_path, [_term(i, [n], {}) for i, n in enumerate(names)])
    dup = report.metrics["duplicate_works"]
    assert (dup["distinct_works"], dup["works_identified_by_key_only"]) == (2, 2)
    assert "work-identity/2" in report.counting_units["duplicate_works.distinct_works"]


def test_renamed_copy_with_the_same_content_hash_is_one_work(tmp_path):
    """D1.9 (O-S3): byte-identical files count once when their metadata carries the sha256 (work-identity/2)."""
    from app.modules.operations.corpus import WorkIndex, record_for

    names = ["scan_0001.pdf", "scan_0001 (1).pdf", "Downloads/scan_0001 copy.pdf"]
    digest = "ab" * 32
    metas = [{"sha256": digest}, {"source_ref": "sha256:" + digest.upper()}, {"content_sha256": digest}]
    report = _corpus(tmp_path, [_term(i, [n], {n: dict(m, publication_year=2021)}) for i, (n, m) in
                                enumerate(zip(names, metas))])
    dup = report.metrics["duplicate_works"]
    assert (dup["paper_keys"], dup["distinct_works"], dup["works_identified_by_key_only"]) == (3, 1, 0)
    assert report.metrics["summary"]["papers_per_year_v2"] == {"2021": 1}
    records = [record_for(n, [m]) for n, m in zip(names, metas)]
    assert {r.content_sha256 for r in records} == {digest}
    v2, v1 = WorkIndex.build(records), WorkIndex.build(records, use_content_hash=False)
    assert (len(v2), len(v1)) == (1, 3) and list(v2.members) == ["sha256:" + digest]
    other = record_for("scan_0002.pdf", [{"sha256": "cd" * 32}])
    assert len(WorkIndex.build(records + [other])) == 2  # a different hash is a different Work


def test_work_identity_2_agrees_with_1_on_a_population_without_hashes(big_corpus):
    """Parity (D1.9 versioned rule change): without content hashes step 0 never fires, so both methods group alike."""
    from app.modules.operations.corpus import WorkIndex, record_for

    terms = json.loads(Path(big_corpus.path).read_text())["terms"]
    meta = {}
    for term in terms:
        for key, m in (term.get("source_metadata") or {}).items():
            meta.setdefault(key, []).append(m)
    records = [record_for(k, v) for k, v in meta.items()]
    assert records and not any(r.content_sha256 for r in records)
    assert WorkIndex.build(records).members == WorkIndex.build(records, use_content_hash=False).members


def test_byte_identical_duplicate_records_collapse_by_their_unit(tmp_path):
    node = _entity(1, [J_KEY])
    edge = {"subject": "syn:R1", "predicate": "rel:SAME", "object": "syn:R2"}
    things = [node, json.loads(json.dumps(node)), _entity(2, [J_KEY])]
    m = compute_statistics(_graph(tmp_path, "g", things, [edge, dict(edge), dict(edge)]), local_ctx(), "g").metrics
    assert (m["entities"], m["relations"], m["duplicate_edge_records"], m["distinct_works"]) == (2, 1, 2, 1)
    term = _term(1, [J_KEY], {J_KEY: {"doi": J_DOI, "publication_year": 2022}})
    report = _corpus(tmp_path, [term, json.loads(json.dumps(term))])
    assert report.metrics["summary"]["legacy"]["total_entities"] == 2  # raw term entries, the legacy unit
    assert report.metrics["summary"]["distinct_works"] == 1


# -- across versions -------------------------------------------------------------------------------

def test_a_work_reappearing_as_its_preprint_is_not_a_new_work(tmp_path):
    v1 = sp.write_json(tmp_path / "v1.json", {"things": [_entity(1, [J_KEY])], "associations": []})
    v2 = sp.write_json(tmp_path / "v2.json", {"things": [
        _entity(1, [J_KEY]), _entity(2, ["2306.00077v1.pdf"], [{"source_paper": "2306.00077v1.pdf", "doi": J_DOI}]),
        _entity(3, ["10.1021_syn.99.pdf"])], "associations": []})
    steps = compare_snapshots(JsonGraphBackend({"v1": v1, "v2": v2}), local_ctx(), ["v1", "v2"]).metrics["steps"]
    assert [s["distinct_works"] for s in steps] == [1, 2]
    assert steps[1]["new_works"] == 1 and steps[1]["removed_works"] == 0  # only syn.99 is new


# -- at scale --------------------------------------------------------------------------------------

def test_repeated_reference_population_at_scale_counts_each_work_once(big_graph, big_corpus):
    g = compute_statistics(JsonGraphBackend({"g": big_graph.path}), local_ctx(), "g", ScanControl(10_000_000))
    gt = big_graph.truth
    assert g.metrics["distinct_works"] == gt.distinct_works < g.metrics["paper_keys"] == gt.paper_keys
    assert gt.paper_keys / gt.distinct_works > 1.5  # the population really repeats Works
    c = audit_corpus(CorpusSource("c", str(big_corpus.path)), control=ScanControl(10_000_000))
    ct = big_corpus.truth
    dup = c.metrics["duplicate_works"]
    assert dup["distinct_works"] == ct.distinct_works < dup["paper_keys"] == ct.paper_keys
    assert dup["legacy"]["unique_doi_keys"] == ct.legacy_total_papers > ct.paper_keys  # path copies stay legacy keys
    assert dup["works_with_multiple_keys"] == ct.works_with_multiple_keys
