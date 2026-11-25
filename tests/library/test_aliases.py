"""Aliases: the other names a paper gives a term, recorded by shape (no word list, no model) and searched like the name.

Owner decision 2026-11-25: no hard-coded common-name map. A term gains ``aliases`` from the model's register_term call and
from the text itself (``term (X)``, ``X (term)``, ``also known as``), at extraction and in the recovery job; the KG node carries
them and retrieval resolves an alias like a name.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules import kg_rag_api
from app.modules.library.passages_job import LibraryPassages
from app.modules.term_extractor import passages as P
from app.modules.term_extractor.kg_to_terms import kg_to_terms
from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.schema import SchemaHelper
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools

PAGE = ("The family Loricariidae (armoured catfish, commonly called plecos) is the largest catfish family. Resonant soft "
        "X-ray scattering (RSoXS) was used. The species Hypostomus plecostomus, also known as the suckermouth catfish, "
        "reaches 50 cm. Values are given in Table 2 (see Fig. 3) and were reported earlier (Regan, 1904).")


@pytest.mark.parametrize(("term", "expected"), [
    ("Loricariidae", ["armoured catfish", "plecos"]),
    ("Resonant soft X-ray scattering", ["RSoXS"]),
    ("RSoXS", ["Resonant soft X-ray scattering"]),
    ("Resonant soft X-ray scattering (RSoXS)", ["RSoXS"]),
    ("Hypostomus plecostomus", ["suckermouth catfish"]),
    ("Values", []),            # "(see Fig. 3)" is a figure reference
    ("reported earlier", []),  # "(Regan, 1904)" is a citation
    ("catfish family", []),
])
def test_aliases_are_found_by_the_shape_of_the_text(term, expected):
    assert P.aliases_in_text(term, PAGE) == expected


def test_alias_candidates_are_bounded_and_never_the_term_itself():
    text = "Thing (Thing) and Thing (a very long bracketed explanation of the thing that runs on and on for many words here)"
    assert P.aliases_in_text("Thing", text) == []
    assert P.aliases_in_pages("Loricariidae", [PAGE, PAGE, "Loricariidae (loricariids) again."]) == [
        "armoured catfish", "plecos", "loricariids"]


def test_register_term_unions_the_models_aliases_with_the_pages_and_the_store_merges_them(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    state = ToolState(store=store, schema=SchemaHelper("storage/schema/v0.3.1/matkg_schema.yaml"),
                      services=SimpleNamespace(chebi_lookup=None))
    tools = {t.name: t for t in build_tools(state)}
    state.set_page_context("p.pdf", 1, PAGE)
    tools["register_term"].invoke({"term": "Loricariidae", "definition": "A family.", "category": "Thing",
                                   "aliases": ["plecos", "Loricariidae", "  plecos "]})
    record = store.get("loricariidae")
    assert record.aliases == ["plecos", "armoured catfish"]
    tools["register_term"].invoke({"term": "Loricariidae", "definition": "A family of catfish.", "category": "Thing",
                                   "aliases": ["Plecos", "suckermouth armoured catfish"]})
    assert store.get("loricariidae").aliases == ["plecos", "armoured catfish", "suckermouth armoured catfish"]
    store.save()
    saved = json.loads((tmp_path / "terms.json").read_text())["terms"]
    assert saved[0]["aliases"][0] == "plecos"
    # A record without aliases keeps the old shape on disk.
    assert "aliases" not in TermRecord(term="x", definition="", category="Thing").to_dict()
    assert TermRecord.from_dict({"term": "x", "aliases": ["y", ""]}).aliases == ["y"]


def test_the_kg_node_carries_aliases_only_when_there_are_any_and_retrieval_resolves_them(tmp_path, monkeypatch):
    from app.modules.json2kg import convert_terms_to_graph

    terms = {"metadata": {}, "code_snippets": [], "terms": [
        {"term": "Loricariidae", "definition": "A family.", "category": "Thing", "aliases": ["plecos", "armoured catfish"],
         "source_papers": ["p.pdf"], "pages": [1], "relations": [], "context_snippets": []},
        {"term": "Ancistrus", "definition": "A genus.", "category": "Thing", "source_papers": ["p.pdf"], "pages": [1],
         "relations": [], "context_snippets": []}]}
    terms_path, kg_path = tmp_path / "terms.json", tmp_path / "kg.json"
    terms_path.write_text(json.dumps(terms), encoding="utf-8")
    graph = convert_terms_to_graph(terms_path, kg_path)
    by = {n["name"]: n for n in graph["things"]}
    assert by["Loricariidae"]["aliases"] == ["plecos", "armoured catfish"] and "aliases" not in by["Ancistrus"]
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    kg = kg_rag_api.KnowledgeGraph(str(kg_path), graph_source="json")
    assert kg._canon_to_id["plecos"] == by["Loricariidae"]["id"]
    assert kg._canon_to_id["armouredcatfish"] == by["Loricariidae"]["id"]
    assert kg._canon_to_id["loricariidae"] == by["Loricariidae"]["id"]
    hits = kg._lexical_search("tell me about plecos", 5)
    assert hits and hits[0].id == by["Loricariidae"]["id"]
    # The reverse build keeps them.
    rebuilt = kg_to_terms(json.loads(kg_path.read_text())).terms
    assert next(t for t in rebuilt["terms"] if t["term"] == "Loricariidae")["aliases"] == ["plecos", "armoured catfish"]


def test_the_recovery_job_records_aliases_from_the_whole_paper_once():
    terms = [{"term": "Loricariidae", "aliases": ["plecos"]}, {"term": "Ancistrus"}]
    added = LibraryPassages._recover_aliases(terms, [PAGE])
    assert added == 1 and terms[0]["aliases"] == ["plecos", "armoured catfish"]
    assert "aliases" not in terms[1]
    assert LibraryPassages._recover_aliases(terms, [PAGE]) == 0
