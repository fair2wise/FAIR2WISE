"""Supporting passages survive the terms -> KG build (pilot 2026-11-24: 231 of 291 passage-bearing shipped terms lost them)."""
from __future__ import annotations

import json
import random

from app.modules.json2kg import build_graph, merge_term_into_node


def snip(paper: str, page: int, text: str) -> dict:
    return {"text": text, "source_paper": paper, "page": page}


def term(name: str, *, snippets=(), relations=(), papers=("a.pdf",), pages=(1,), **extra) -> dict:
    return {"term": name, "definition": f"{name} definition", "category": "Method", "source_papers": list(papers),
            "pages": list(pages), "context_snippets": list(snippets), "relations": list(relations), **extra}


def node_of(graph: dict, name: str) -> dict:
    return next(n for n in graph["things"] if n["name"] == name)


def total_passages(items) -> int:
    return sum(len({(s["source_paper"], s["page"], s["text"]) for s in x.get("context_snippets") or []}) for x in items)


def test_a_term_first_named_as_a_relation_target_keeps_its_passages_pages_and_data():
    terms = [
        term("cloud tomography", relations=[{"relation": "uses", "related_term": "ray tracing"}]),   # creates a stub for B
        term("ray tracing", snippets=[snip("a.pdf", 3, "Ray tracing follows each photon.")], pages=(3,), papers=("a.pdf", "b.pdf"),
             properties=[{"property": "speed", "value": "fast"}], publication_year=2020, formula="x=1",
             source_metadata={"a.pdf": {"paper_title": "Paper A"}}),
    ]
    node = node_of(build_graph(terms, [], merge_existing=True), "ray tracing")
    assert node["context_snippets"] == [snip("a.pdf", 3, "Ray tracing follows each photon.")]
    assert node["pages"] == [3] and node["source_papers"] == ["a.pdf", "b.pdf"]
    assert node["properties"] == [{"property": "speed", "value": "fast"}]
    assert node["publication_year"] == 2020 and node["formula"] == "x=1" and node["description"] == "ray tracing definition"
    assert [(p["source_paper"], p.get("paper_title")) for p in node["publications"]] == [("a.pdf", "Paper A")]
    assert node["source_metadata"] == {"a.pdf": {"paper_title": "Paper A"}}


def test_two_records_of_the_same_id_union_their_passages_without_duplicates_or_overwrite():
    first = term("DDPM", snippets=[snip("a.pdf", 1, "one"), snip("a.pdf", 2, "two")], pages=(1, 2))
    second = term("DDPM", snippets=[snip("a.pdf", 2, "two"), snip("b.pdf", 5, "three")], papers=("b.pdf",), pages=(5,))
    node = node_of(build_graph([first, second], [], merge_existing=True), "DDPM")
    assert [(s["source_paper"], s["page"], s["text"]) for s in node["context_snippets"]] == [
        ("a.pdf", 1, "one"), ("a.pdf", 2, "two"), ("b.pdf", 5, "three")]
    assert node["pages"] == [1, 2, 5] and node["source_papers"] == ["a.pdf", "b.pdf"]
    assert node["description"] == "DDPM definition"           # an existing value is never replaced


def test_merge_never_overwrites_what_the_node_already_has():
    node = {"id": "x", "context_snippets": [snip("a.pdf", 1, "keep")], "pages": [1], "publication_year": 2019,
            "properties": [], "publications": [], "source_metadata": {"a.pdf": {"title": "A"}}}
    merge_term_into_node(term("x", snippets=[snip("a.pdf", 1, "keep")], pages=(1,), publication_year=2024,
                              source_metadata={"a.pdf": {"title": "other"}, "b.pdf": {"title": "B"}}), node)
    assert node["context_snippets"] == [snip("a.pdf", 1, "keep")] and node["publication_year"] == 2019
    assert node["source_metadata"] == {"a.pdf": {"title": "A"}, "b.pdf": {"title": "B"}}


def test_the_build_loses_no_passage_counted_before_and_after_per_paper():
    """The count the owner asked for: passages in the terms vs passages on the KG nodes, in total and per paper."""
    rng = random.Random(7)
    names = [f"term {i}" for i in range(120)]
    terms = []
    for name in names * 2:   # every name appears twice, in random order, with relations to other names (stubs first)
        paper = rng.choice(["a.pdf", "b.pdf", "c.pdf"])
        page = rng.randint(1, 9)
        relations = [{"relation": "related_to", "related_term": rng.choice(names)} for _ in range(3)]
        terms.append(term(name, snippets=[snip(paper, page, f"{name} appears on page {page} of {paper}.")], relations=relations,
                          papers=(paper,), pages=(page,)))
    rng.shuffle(terms)
    graph = build_graph(terms, [], merge_existing=True)
    unique_in_terms = {(s["source_paper"], s["page"], s["text"]) for t in terms for s in t["context_snippets"]}
    in_kg = {(s["source_paper"], s["page"], s["text"]) for n in graph["things"] for s in n["context_snippets"]}
    assert in_kg == unique_in_terms and total_passages(graph["things"]) == len(unique_in_terms)
    for paper in ("a.pdf", "b.pdf", "c.pdf"):
        assert {s for s in in_kg if s[0] == paper} == {s for s in unique_in_terms if s[0] == paper}
    named = {n["name"] for n in graph["things"] if n["context_snippets"]}
    assert set(names) <= named          # every real term has at least one passage; only stubs may have none


def test_stubs_that_never_get_a_record_stay_empty_and_nothing_is_invented():
    graph = build_graph([term("a", relations=[{"relation": "uses", "related_term": "ghost"}])], [], merge_existing=True)
    ghost = node_of(graph, "ghost")
    assert ghost["context_snippets"] == [] and ghost["publications"] == [] and ghost["category"] == "Unknown"


def test_the_default_converter_is_unchanged_and_the_bundle_path_opts_in(tmp_path):
    """The frozen T23A contract keeps the legacy behaviour by default; kg_update.rebuild_kg (extracted bundles) merges."""
    from app.modules.graph.kg_update import rebuild_kg

    terms = [term("cloud tomography", relations=[{"relation": "uses", "related_term": "ray tracing"}]),
             term("ray tracing", snippets=[snip("a.pdf", 3, "Ray tracing follows each photon.")], pages=(3,))]
    legacy = node_of(build_graph([dict(t) for t in terms], []), "ray tracing")
    assert legacy["context_snippets"] == [] and legacy["pages"] == []          # frozen legacy bytes: stub-first loses them
    terms_path = tmp_path / "terms.json"
    terms_path.write_text(json.dumps({"terms": terms, "code_snippets": []}), encoding="utf-8")
    result = rebuild_kg(str(terms_path), str(tmp_path / "kg.json"))
    kg = json.loads((tmp_path / "kg.json").read_text(encoding="utf-8"))
    assert result["nodes"] == 2 and node_of(kg, "ray tracing")["context_snippets"] == [snip("a.pdf", 3, "Ray tracing follows each photon.")]
