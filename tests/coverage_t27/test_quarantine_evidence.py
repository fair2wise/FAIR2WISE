"""Quarantine ordering and structural row checks; Evidence construction and intent routing."""

from __future__ import annotations

import pytest

from app.modules.canonicalization.quarantine import (
    MAX_SURFACE_CHARS, check_context_row, classify_surface,
)
from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary
from app.modules.hybrid_rag import evidence as ev


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(1, [VocabEntry(slug="rsoxs", display_name="RSoXS", category="technique", aliases=("resonant soft x-ray",))])


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("", None), ("RSoXS", None),                                  # vocabulary matches are never quarantined
        ("Grazing Incidence (GIWAXS)", "junk:fragment"),
        ("one two three four five six seven", "junk:sentence"), ("a, b", "junk:sentence"),
        ("ImageJ", "software"), ("IGOR Pro", "software"), ("custom tool", "software"),
        ("power conversion efficiency", "junk:property_or_result"), ("spin coating", "procedure"),
        ("film", "junk:generic"), ("polystyrene", None), ("two words", None),
    ],
)
def test_classify_surface_follows_the_documented_rule_order(vocab, surface, expected):
    out = classify_surface(surface, vocab, software_extra=["Custom Tool"], generic_stoplist=["film", "sample"])
    assert out == expected


def test_property_rule_outranks_procedure_and_generic_applies_only_to_single_words(vocab):
    assert classify_surface("annealing peak", vocab) == "junk:property_or_result"
    assert classify_surface("film", vocab) is None  # no stoplist given
    assert classify_surface("thin film", vocab, generic_stoplist=["film"]) is None


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("text", "malformed:not_a_mapping"), ({"surface": 5}, "malformed:surface_not_text"),
        ({"surface": "  "}, "malformed:empty_surface"), ({"surface": "x" * (MAX_SURFACE_CHARS + 1)}, "malformed:surface_too_long"),
        ({"surface": "a\x00b", "node_id": "n"}, "malformed:control_characters"),
        ({"surface": "ok"}, "malformed:missing_node_id"),
        ({"surface": "ok", "node_id": "n", "category": "weird"}, "unsupported:category"),
    ],
)
def test_check_context_row_codes(row, code):
    assert check_context_row(row)["code"] == code


def test_check_context_row_accepts_well_formed_rows():
    assert check_context_row({"surface": "ok", "node_id": "n"}) is None
    assert check_context_row({"surface": "ok", "node_id": "n", "category": "unknown"}) is None
    assert check_context_row({"surface": "ok", "node_id": "n", "category": "technique"}) is None
    assert check_context_row({"surface": "ok", "node_id": "n"}) is None


# ------------------------------------------------------------------ evidence
def test_evidence_validates_kinds_and_defaults_corpus_by_type():
    with pytest.raises(ValueError, match="source_kind"):
        ev.Evidence(source_kind="web", source_id="x", score=1, text="t")
    with pytest.raises(ValueError, match="source_type"):
        ev.Evidence(source_kind="kg", source_id="x", score=1, text="t", source_type="blog")
    ops = ev.Evidence(source_kind="paper_chunk", source_id="c", score=1, text="t", source_type="ops")
    lit = ev.Evidence(source_kind="paper_chunk", source_id="c", score=1, text="t", source_type="literature")
    kept = ev.Evidence(source_kind="paper_chunk", source_id="c", score=1, text="t", source_type="ops",
                       provenance={"corpus": "custom"})
    assert (ops.corpus, lit.corpus, kept.corpus) == ("bl1101", "literature", "custom")
    assert ev.Evidence(source_kind="kg", source_id="k", score=1, text="t").corpus == ""


def test_as_dict_drops_the_raw_payload():
    e = ev.Evidence(source_kind="kg", source_id="k", score=0.5, text="t", payload=object(), entity_ids=["n"])
    data = e.as_dict()
    assert "payload" not in data and data["entity_ids"] == ["n"] and data["score"] == 0.5


@pytest.mark.parametrize(
    ("question", "kwargs", "intent"),
    [
        ("anything", {"layout": True, "numeric": True}, "layout"), ("anything", {"numeric": True}, "factual"),
        ("Show the LAST SCAN", {}, "scan"), ("why does it peak", {}, "why_how"),
        ("how do authors derive it", {}, "why_how"), ("what is the energy", {}, "factual"), (None, {}, "factual"),
    ],
)
def test_infer_intent(question, kwargs, intent):
    assert ev.infer_intent(question, **kwargs) == intent


def test_kg_evidence_reads_hit_or_payload_attributes():
    from types import SimpleNamespace

    hit = SimpleNamespace(id="n1", graph_id="rsoxs", name="Silicon", category="Material", score=0.7,
                          payload=SimpleNamespace(id="n1", description="desc", score_prp=0.2))
    e = ev.kg_evidence_from_hit(hit)
    assert (e.source_id, e.text, e.score, e.locator) == ("kg:rsoxs:n1", "desc", 0.7, "KG:rsoxs:Silicon")
    assert e.entity_ids == ["n1"] and e.provenance == {"graph_id": "rsoxs", "corpus": "rsoxs"} and e.payload is hit.payload
    bare = ev.kg_evidence_from_hit(SimpleNamespace(id="x", description="d", score_prp=0.4, category=None))
    assert bare.score == 0.4 and bare.locator == "x" and bare.graph_id == "" and bare.name == "x"
    nameless = ev.kg_evidence_from_hit(SimpleNamespace())
    assert nameless.entity_ids == [] and nameless.score == 0.0


def test_chunk_evidence_classifies_corpus_and_tolerates_odd_pages():
    chunk = {"chunk_id": "c1", "doc_id": "d1", "work_id": "doi:1", "corpus": "bl1101", "page": "7", "text": "t",
             "locator": "l", "heading": "h", "entity_ids": ["e"], "matched_slots": {"k": 1}, "title": "T", "doi": "10.1/x",
             "url": "u", "path": "p", "corpus_revision": "r", "chunker_version": "1"}
    e = ev.evidence_from_chunk(chunk, score=2, retrieval_method="bm25")
    assert e.source_type == "ops" and e.page == 7 and e.paper_id == "d1" and e.work_id == "doi:1"
    assert e.provenance["revision"] == "r" and e.provenance["index_version"] == "1" and e.name == "T"
    lit = ev.evidence_from_chunk({"text": "t", "page": "n/a"}, score=1, retrieval_method="exact_token")
    assert lit.source_type == "literature" and lit.page is None and lit.source_id == "chunk" and lit.work_id is None
    assert ev.evidence_from_chunk({"page": ""}, score=1, retrieval_method="x").page is None
    assert ev.tagged_source_types([e, lit]) == ["ops", "literature"]
