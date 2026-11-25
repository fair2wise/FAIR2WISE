"""Located relation evidence: the locator for relation endpoints, the data model, store, KG build and round trip, extraction time.

No model is called anywhere: evidence is a sentence of the paper that names both endpoints of a relation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules import json2kg
from app.modules.term_extractor import passages as P
from app.modules.term_extractor.kg_to_terms import kg_to_terms
from app.modules.term_extractor.models import RelationRecord, TermRecord, evidence_entry, union_evidence
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.schema import SchemaHelper
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools

BOTH = ("Resonant soft X-ray scattering measures the average domain size and the domain purity of a polymer blend film "
        "at the carbon edge of the sample.")
ONLY_A = ("The domain purity of the neat acceptor films was found to be high in all of the samples that were prepared "
          "for this particular study of ours.")
ONLY_B = ("Resonant soft X-ray scattering was used at the beamline in transmission geometry with a calibrated detector "
          "for every sample that was measured.")
FIRST = "The film thickness was measured with an ellipsometer at several positions across every sample prepared for this study."
SECOND = "A calibration of the instrument was carried out against a silicon reference before every measurement session."


def ev(pages: list[str], a: str, b: str) -> list[P.Passage]:
    return P.relation_evidence(pages, a, b)


# ------------------------------------------------------------------------------------------------------ the locator


def test_both_endpoints_in_one_sentence():
    (hit,) = ev([BOTH], "domain purity", "resonant soft X-ray scattering")
    assert (hit.text, hit.page, hit.form) == (BOTH, 1, P.FORM_BOTH)


def test_two_adjacent_sentences_form_a_window_when_neither_names_both():
    (hit,) = ev([f"{FIRST} {SECOND}"], "ellipsometer", "silicon reference")
    assert hit.form == P.FORM_ADJACENT and hit.text == f"{FIRST} {SECOND}"
    # the order of the endpoints does not matter
    assert [h.text for h in ev([f"{FIRST} {SECOND}"], "silicon reference", "ellipsometer")] == [f"{FIRST} {SECOND}"]


def test_a_window_is_not_built_when_a_sentence_already_names_both():
    page = f"{BOTH} {ONLY_A}"
    assert [h.form for h in ev([page], "domain purity", "resonant soft X-ray scattering")] == [P.FORM_BOTH]


def test_non_adjacent_sentences_and_other_pages_are_not_evidence():
    assert ev([f"{FIRST} {ONLY_A} {SECOND}"], "ellipsometer", "silicon reference") == []
    assert ev([FIRST, SECOND], "ellipsometer", "silicon reference") == []     # different pages


def test_one_endpoint_only_is_not_evidence_and_the_context_sentence_is_not_reused():
    assert ev([ONLY_A, ONLY_B], "domain purity", "carbon edge") == []
    cands = P.relation_candidates([ONLY_A], "domain purity", "carbon edge")
    assert cands == []                    # a sentence with one endpoint is not even a candidate
    # the term's own context sentence counts only when it names both endpoints
    assert [h.text for h in ev([BOTH], "domain purity", "carbon edge")] == [BOTH]


def test_alias_plural_abbreviation_and_hyphen_rules_of_the_locator_apply_to_endpoints():
    page = ("Domain purities were obtained from R-SoXS profiles of the blend films that were cast from chlorobenzene on "
            "silicon substrates and annealed for ten minutes.")
    assert ev([page], "domain purity", "RSoXS") == []        # 'purities' is not a plural tail of 'purity' (no alias guessing)
    page2 = page.replace("Domain purities", "Domain purity")
    assert len(ev([page2], "domain purity", "RSoXS")) == 1   # R-SoXS squash match
    page3 = ("The domain purities were obtained by resonant soft X-ray scattering (RSoXS) from the blend films cast from "
             "chlorobenzene. Later the RSoXS profiles of the domain purities were fitted with a model of the annealed films of the "
             "whole series of samples.")
    long_term = "resonant soft X-ray scattering (RSoXS)"
    hits = ev([page3], "domain purities", long_term)
    assert len(hits) == 2 and all(h.form == P.FORM_BOTH for h in hits)      # the abbreviation counts: the paper defines it
    undefined = ev([page3.replace(" (RSoXS)", "")], "domain purities", long_term)
    assert [h.text.startswith("The domain") for h in undefined] == [True]       # not defined: the bare abbreviation does not count


def test_short_terms_need_a_case_sensitive_whole_token():
    page = ("The value of D2 was fitted together with the exponent of the power law for every film of the series that was "
            "measured at the beamline.")
    assert len(ev([page], "D2", "power law")) == 1
    assert ev([page.replace("D2", "d2")], "D2", "power law") == []


def test_endpoints_only_in_references_or_captions_give_none():
    refs = "References\\n1. Doe, J. Domain purity from resonant soft X-ray scattering of blends. Nature 1, 2-3 (2019)."
    body = "Intro words of a body page that names neither endpoint at all in any sentence."
    assert ev([body, body, refs.replace("\\\\n", "\\n")], "domain purity", "resonant soft X-ray scattering") == []
    caption = "Fig. 2 Domain purity from resonant soft X-ray scattering of the blend films measured at the carbon edge."
    assert ev([caption], "domain purity", "resonant soft X-ray scattering") == []
    rejected = P.relation_candidates([caption], "domain purity", "resonant soft X-ray scattering")
    assert [c.reason for c in rejected] == [P.REASON_CAPTION]


def test_every_sentence_is_kept_best_first_without_a_cap_and_deduplicated():
    sentences = [BOTH.replace("sample.", f"sample number {i}.") for i in range(12)]
    window = f"{FIRST} {SECOND}"
    pages = [" ".join(sentences[:6]), " ".join(sentences[6:]) + " " + window]
    hits = ev(pages, "domain purity", "resonant soft X-ray scattering")
    assert len(hits) == 12                                     # no cap
    assert len(ev(pages + pages, "domain purity", "resonant soft X-ray scattering")) == 24   # other pages: distinct (page, text)
    mixed = f"{BOTH} {window}"
    both = ev([mixed], "domain purity", "resonant soft X-ray scattering")
    assert both[0].form == P.FORM_BOTH
    pair = ev([f"{window} {BOTH}"], "ellipsometer", "silicon reference")
    assert [h.form for h in pair] == [P.FORM_ADJACENT]


def test_both_endpoint_sentences_rank_before_windows():
    page = f"{FIRST} {SECOND} Other words here. " + (
        "The ellipsometer was calibrated against a silicon reference sample, and the resulting thickness values were then "
        "compared between all of the films of the series.")
    forms = [h.form for h in ev([page], "ellipsometer", "silicon reference")]
    assert forms == [P.FORM_BOTH, P.FORM_ADJACENT]


def test_a_long_sentence_gives_a_clause_window_holding_both_mentions_or_nothing():
    long_sentence = ("Resonant soft X-ray scattering was carried out on all the films that had been prepared on silicon "
                     "substrates, which were cleaned by sonication in several solvents for ten minutes each; the patterns "
                     "collected with a two-dimensional detector at a fixed incidence angle show that the domain purity of "
                     "the blend rises with the annealing temperature and that the average domain size grows as well, as "
                     "discussed in the main text of this paper and in its supporting information.")
    assert len(long_sentence) > 400
    hits = ev([long_sentence], "resonant soft X-ray scattering", "domain purity")
    assert len(hits) == 1 and hits[0].clause_window and len(hits[0].text) <= 400
    assert "Resonant soft X-ray scattering" in hits[0].text and "domain purity" in hits[0].text
    far = long_sentence.replace("domain purity", "zq")
    far = far.replace("supporting information", "domain purity of the end")
    assert ev([far], "resonant soft X-ray scattering", "domain purity") == []   # 400 characters cannot hold both mentions


def test_relation_evidence_for_maps_page_numbers_and_uses_the_page_text_when_alone():
    entries = P.relation_evidence_for(subject="domain purity", obj="carbon edge", source_paper="a.pdf", page=7,
                                      page_text=BOTH, paper_pages=None)
    assert entries == [{"text": BOTH, "source_paper": "a.pdf", "page": 7, "kind": "located", "verified": True}]
    entries = P.relation_evidence_for(subject="domain purity", obj="carbon edge", source_paper="a.pdf", page=1,
                                      page_text=None, paper_pages=[ONLY_A, BOTH])
    assert [e["page"] for e in entries] == [2]
    assert P.relation_evidence_for(subject="a", obj="b", source_paper="a.pdf", page=1, page_text=None,
                                   paper_pages=None) == []


# ------------------------------------------------------------------------------------------------------ the data model


def test_evidence_reads_old_strings_and_writes_the_old_shape_without_new_evidence():
    old = {"relation": "is_a", "related_term": "polymer", "evidence": ["a sentence", "another"]}
    record = RelationRecord.from_dict(old)
    assert record.evidence == ["a sentence", "another"]
    term = TermRecord(term="P3HT", definition="d", category="Thing", relations=[record])
    assert term.to_dict()["relations"][0]["evidence"] == ["a sentence", "another"]      # unchanged
    bare = TermRecord(term="x", definition="d", category="Thing", relations=[RelationRecord("is_a", "y")])
    assert "evidence" not in bare.to_dict()["relations"][0]
    assert evidence_entry("a sentence") == {"text": "a sentence", "kind": "unknown"}     # a string has no paper, page or kind
    mapping = {"text": "t", "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True}
    rebuilt = TermRecord.from_dict(TermRecord(term="x", definition="d", category="Thing",
                                              relations=[RelationRecord("is_a", "y", evidence=[mapping])]).to_dict())
    assert rebuilt.relations[0].evidence == [mapping]


def test_union_evidence_by_paper_page_text():
    held = [{"text": "t", "source_paper": "a.pdf", "page": 1}]
    assert not union_evidence(held, [{"text": "t", "source_paper": "a.pdf", "page": 1}])
    assert union_evidence(held, [{"text": "t", "source_paper": "b.pdf", "page": 1}, {"text": "t", "source_paper": "a.pdf",
                                                                                  "page": 2}])
    assert len(held) == 3
    assert not union_evidence(held, [])


def test_the_store_unions_the_evidence_of_a_repeated_relation(tmp_path: Path):
    store = TermStore(str(tmp_path / "t.json"))
    one = {"text": "s1", "source_paper": "a.pdf", "page": 1, "kind": "located", "verified": True}
    two = {"text": "s2", "source_paper": "b.pdf", "page": 4, "kind": "located", "verified": True}

    def rec(*evidence):
        return TermRecord(term="P3HT", definition="d", category="Thing",
                          relations=[RelationRecord("is_a", "polymer", evidence=list(evidence))])

    store.upsert(rec(one))
    store.upsert(rec(one, two))
    store.upsert(rec())
    assert store.get("p3ht").relations[0].evidence == [one, two]


# ------------------------------------------------------------------------------------------------------ KG build


def term_dict(*evidence, relation="measured_by", obj="X-ray scattering"):
    return {"term": "P3HT", "definition": "d", "category": "Thing", "source_papers": ["a.pdf"], "pages": [1],
            "relations": [{"relation": relation, "related_term": obj, **({"evidence": list(evidence)} if evidence else {})}]}


def edge_of(graph):
    return next(e for e in graph["associations"] if e["predicate"] == "rel:measured_by")


def test_string_evidence_keeps_the_exact_old_edge_shape_in_both_paths():
    for merge in (False, True):
        edge = edge_of(json2kg.build_graph([term_dict("one", "two")], [], merge_existing=merge))
        assert edge == {"subject": "matkg:P3HT", "predicate": "rel:measured_by", "object": "matkg:X-rayscattering",
                        "has_evidence": "one; two"}
        bare = edge_of(json2kg.build_graph([term_dict()], [], merge_existing=merge))
        assert bare["has_evidence"] is None and "evidence" not in bare


def test_structured_evidence_gives_an_evidence_list_and_the_best_text_as_has_evidence():
    first = {"text": "best sentence", "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True}
    second = {"text": "next sentence", "source_paper": "b.pdf", "page": 1, "kind": "located", "verified": True}
    edge = edge_of(json2kg.build_graph([term_dict(first, second)], []))
    assert edge["evidence"] == [first, second] and edge["has_evidence"] == "best sentence"


def test_a_repeated_edge_keeps_the_union_of_the_evidence_in_the_merge_path_only():
    a = {"text": "from a", "source_paper": "a.pdf", "page": 1, "kind": "located", "verified": True}
    b = {"text": "from b", "source_paper": "b.pdf", "page": 3, "kind": "located", "verified": True}
    twice = [term_dict(a), term_dict(b)]
    merged = edge_of(json2kg.build_graph(twice, [], merge_existing=True))
    assert merged["evidence"] == [a, b] and merged["has_evidence"] == "from a"
    assert len([e for e in json2kg.build_graph(twice, [], merge_existing=True)["associations"]
                if e["predicate"] == "rel:measured_by"]) == 1
    frozen = edge_of(json2kg.build_graph(twice, []))
    assert frozen["evidence"] == [a]                   # the frozen default path keeps the first edge, as before


def test_kg_to_terms_reads_structured_evidence_first_and_round_trips():
    a = {"text": "from a", "source_paper": "a.pdf", "page": 1, "kind": "located", "verified": True}
    graph = json2kg.build_graph([term_dict(a)], [], merge_existing=True)
    result = kg_to_terms(graph)
    relation = next(t for t in result.terms["terms"] if t["term"] == "P3HT")["relations"][0]
    assert relation["evidence"] == [a]
    rebuilt = json2kg.build_graph(result.terms["terms"], [], merge_existing=True)
    assert edge_of(rebuilt) == edge_of(graph)
    # an older KG with only has_evidence still reads as before
    old = json2kg.build_graph([term_dict("one", "two")], [], merge_existing=True)
    assert next(t for t in kg_to_terms(old).terms["terms"] if t["term"] == "P3HT")["relations"][0]["evidence"] == ["one", "two"]
    assert not [w for w in result.warnings if "other than" in w]


# ------------------------------------------------------------------------------------------------------ extraction time


class FakeSchema:
    def validate_and_fix_term(self, raw):
        return raw

    def check_relation_validity(self, *a):
        return True


@pytest.fixture
def tool_world(tmp_path: Path):
    store = TermStore(str(tmp_path / "t.json"))
    state = ToolState(store=store, schema=FakeSchema(), services=SimpleNamespace(formula_checker=None, chebi_lookup=None))
    return state, {t.name: t for t in build_tools(state)}, store


def test_register_term_attaches_evidence_for_relations_whose_endpoints_share_a_sentence(tool_world, caplog):
    state, tools, store = tool_world
    pages = [ONLY_A, BOTH, f"{FIRST} {SECOND}"]
    state.paper_pages = ("a.pdf", pages)
    state.set_page_context("a.pdf", 1, pages[0])
    tools["register_term"].invoke({"term": "domain purity", "definition": "d", "category": "Thing",
                                   "context": ONLY_A, "relations": [
                                       {"relation": "measured_by", "related_term": "resonant soft X-ray scattering"},
                                       {"relation": "related_to", "related_term": "carbon edge"},
                                       {"relation": "related_to", "related_term": "nothing at all here"}]})
    relations = {r.related_term: r for r in store.get("domain purity").relations}
    got = relations["resonant soft X-ray scattering"].evidence
    assert got == [{"text": BOTH, "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True}]
    assert [e["page"] for e in relations["carbon edge"].evidence] == [2]       # found on page 2, not the page being read
    assert relations["nothing at all here"].evidence == []
    # the context sentence names only one endpoint: it is never stored as evidence
    assert all(e["text"] != ONLY_A for r in relations.values() for e in r.evidence)
    stats = store.run_passage_stats()["a.pdf"]
    assert (stats["relations"], stats["relations_with_evidence"], stats["evidence_sentences"],
            stats["relations_without"]) == (3, 2, 2, 1)


def test_register_term_without_the_paper_pages_uses_the_page_being_read(tool_world):
    state, tools, store = tool_world
    state.set_page_context("a.pdf", 5, BOTH)
    tools["register_term"].invoke({"term": "domain purity", "definition": "d", "category": "Thing", "relations": [
        {"relation": "measured_by", "related_term": "resonant soft X-ray scattering"}]})
    (rel,) = store.get("domain purity").relations
    assert [(e["page"], e["text"]) for e in rel.evidence] == [(5, BOTH)]
    state.clear_page_context()
    state.paper_pages = None
    tools["register_term"].invoke({"term": "second", "definition": "d", "category": "Thing", "relations": [
        {"relation": "measured_by", "related_term": "resonant soft X-ray scattering"}]})
    assert store.get("second").relations[0].evidence == []                    # no page text at all: nothing located
    assert store.run_passage_stats()["a.pdf"]["relations_without"] == 1


def test_a_repeated_registration_unions_the_evidence_and_counts_the_relation_once(tool_world):
    state, tools, store = tool_world
    state.paper_pages = ("a.pdf", [BOTH])
    state.set_page_context("a.pdf", 1, BOTH)
    for _ in range(2):
        tools["register_term"].invoke({"term": "domain purity", "definition": "d", "category": "Thing", "relations": [
            {"relation": "measured_by", "related_term": "resonant soft X-ray scattering"}]})
    assert len(store.get("domain purity").relations[0].evidence) == 1
    stats = store.run_passage_stats()["a.pdf"]
    assert (stats["relations"], stats["relations_with_evidence"]) == (1, 1)
