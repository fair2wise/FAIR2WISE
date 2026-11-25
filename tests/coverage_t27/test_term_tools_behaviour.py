"""LangChain term tools: lookup, fuzzy merge, formula repair, ChEBI and registration (fakes only)."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools


class FakeSchema:
    def __init__(self, invalid_pairs=()):
        self.invalid_pairs = set(invalid_pairs)

    def validate_and_fix_term(self, raw):
        return dict(raw)

    def check_relation_validity(self, subj, rel, obj):
        return (subj, rel, obj) not in self.invalid_pairs


class FakeChecker:
    def __init__(self, results, boom_on=()):
        self.results, self.boom_on, self.calls = results, set(boom_on), []

    def validate(self, formula):
        self.calls.append(formula)
        if formula in self.boom_on:
            raise RuntimeError("mp down")
        return dict(self.results.get(formula, {"status": "invalid"}))


class FakeMerge:
    def __init__(self, candidates):
        self.candidates, self.added, self.ensured = candidates, [], None

    def ensure(self, names):
        self.ensured = list(names)

    def query(self, term):
        return list(self.candidates)

    def add(self, name):
        self.added.append(name)


@pytest.fixture
def make(tmp_path):
    def _make(*, schema=None, checker=None, chebi=None, llm=None, merge=None, existing=()):
        store = TermStore(str(tmp_path / "t.json"))
        for term, cat in existing:
            store.upsert(TermRecord(term=term, definition="d", category=cat))
        services = SimpleNamespace(formula_checker=checker, chebi_lookup=chebi)
        state = ToolState(store=store, schema=schema or FakeSchema(), services=services,
                          llm_invoke=llm, merge_index=merge)
        return state, {t.name: t for t in build_tools(state)}

    return _make


def test_page_context_is_thread_local_but_paper_falls_back_to_shared(make):
    state, _ = make()
    state.set_page_context("a.pdf", 3)
    seen = {}
    t = threading.Thread(target=lambda: seen.update(ctx=state.page_context()))
    t.start()
    t.join()
    assert seen["ctx"] == ("a.pdf", None) and state.page_context() == ("a.pdf", 3)
    state.clear_page_context()
    assert state.page_context() == ("a.pdf", None)


def test_check_existing_term_exact_possible_and_missing(make):
    _, tools = make(existing=[("Polystyrene", "Chemical"), ("PS-b-PMMA", "Chemical")])
    check = tools["check_existing_term"]
    assert check.invoke({"name": "polystyrene"}).startswith("exact_match:")
    assert check.invoke({"name": "poly"}) == "possible_matches:Polystyrene"
    assert check.invoke({"name": "Polystyrene film"}) == "possible_matches:Polystyrene"
    assert check.invoke({"name": "zeolite"}) == "not_found"


def test_fuzzy_merge_guards_and_verified_candidate_only(make):
    merge = FakeMerge(["Polystyrene"])
    _, tools = make(llm=lambda p: " Polystyrene ", merge=merge, existing=[("Polystyrene", "Chemical")])
    fuzzy = tools["fuzzy_merge_term"]
    assert fuzzy.invoke({"name": "PS"}) == "match:" + TermStore.normalize("Polystyrene")
    assert merge.ensured == ["Polystyrene"]

    # an LLM answer outside the candidate list is never trusted
    _, t2 = make(llm=lambda p: "Invented", merge=FakeMerge(["Polystyrene"]))
    assert t2["fuzzy_merge_term"].invoke({"name": "PS"}) == "no_match"
    _, t3 = make(llm=None, merge=FakeMerge(["x"]))
    assert t3["fuzzy_merge_term"].invoke({"name": "PS"}) == "no_match"
    _, t4 = make(llm=lambda p: "x", merge=None)
    assert t4["fuzzy_merge_term"].invoke({"name": "PS"}) == "no_match"
    _, t5 = make(llm=lambda p: "x", merge=FakeMerge([]))
    assert t5["fuzzy_merge_term"].invoke({"name": "PS"}) == "no_match"
    _, t6 = make(llm=lambda p: "x", merge=FakeMerge(["x"]))
    assert t6["fuzzy_merge_term"].invoke({"name": "   "}) == "no_match"

    def boom(prompt):
        raise TimeoutError("llm")

    _, t7 = make(llm=boom, merge=FakeMerge(["x"]))
    assert t7["fuzzy_merge_term"].invoke({"name": "PS"}) == "no_match"


def test_validate_formula_serialises_result_or_error(make):
    checker = FakeChecker({"NaCl": {"status": "valid"}}, boom_on={"Bad"})
    _, tools = make(checker=checker)
    assert json.loads(tools["validate_formula"].invoke({"formula": "NaCl"})) == {"status": "valid"}
    err = json.loads(tools["validate_formula"].invoke({"formula": "Bad"}))
    assert err == {"status": "error", "error": "mp down"}


def test_repair_formula_paths(make):
    checker = FakeChecker({"H2O": {"status": "valid"}, "Na2O": {"status": "valid"}}, boom_on={"Boom2"})
    llm_answers = {"ctx-a": "Na2O extra words", "ctx-b": "NaX1", "ctx-c": "Bd1", "ctx-d": "Boom2"}
    _, tools = make(checker=checker, llm=lambda p: llm_answers[p.split("CONTEXT:\n")[1]])
    repair = tools["repair_formula"]

    def call(formula, ctx="ctx-a"):
        return json.loads(repair.invoke({"formula": formula, "context": ctx}))

    assert call("", "x") == {"status": "missing", "formula": None}
    assert call("word", "x") == {"status": "missing", "formula": None}  # no element+digit pattern
    assert call("H2O") == {"status": "valid", "formula": "H2O"}  # valid: no LLM involved
    fixed = call("Na0O")  # invalid -> LLM proposes Na2O -> valid -> "corrected"
    assert fixed == {"status": "corrected", "formula": "Na2O", "original": "Na0O"}
    assert call("Bd1", "ctx-c") == {"status": "invalid", "formula": "Bd1"}  # candidate == original
    assert call("Na9", "ctx-b") == {"status": "invalid", "formula": "Na9"}  # candidate still invalid
    assert call("Zz9", "ctx-d") == {"status": "invalid", "formula": "Zz9"}  # re-validation raised


def test_repair_formula_validation_error_and_llm_failures(make):
    boom = FakeChecker({}, boom_on={"Na2"})
    _, tools = make(checker=boom, llm=lambda p: "x")
    out = json.loads(tools["repair_formula"].invoke({"formula": "Na2", "context": "c"}))
    assert out == {"status": "error", "details": {"error": "mp down"}}

    def failing_llm(prompt):
        raise RuntimeError("llm down")

    _, t2 = make(checker=FakeChecker({}), llm=failing_llm)
    assert json.loads(t2["repair_formula"].invoke({"formula": "Na2", "context": "c"})) == {
        "status": "invalid", "formula": "Na2"}
    _, t3 = make(checker=FakeChecker({}), llm=None)
    assert json.loads(t3["repair_formula"].invoke({"formula": "Na2", "context": "c"}))["formula"] == "Na2"
    _, t4 = make(checker=FakeChecker({}), llm=lambda p: "   ")  # blank answer: split()[0] raises IndexError
    assert json.loads(t4["repair_formula"].invoke({"formula": "Na2", "context": "c"}))["status"] == "invalid"


def test_lookup_chebi_variants(make):
    _, none = make(chebi=None)
    assert none["lookup_chebi"].invoke({"name": "x"}) == "ChEBI not available"

    class Chebi:
        def lookup(self, name):
            if name == "boom":
                raise RuntimeError("bad obo")
            return {"formula": "H2O"} if name == "water" else None

    _, tools = make(chebi=Chebi())
    assert json.loads(tools["lookup_chebi"].invoke({"name": "water"})) == {"formula": "H2O"}
    assert tools["lookup_chebi"].invoke({"name": "nothing"}) == "not_found"
    assert tools["lookup_chebi"].invoke({"name": "boom"}) == "error:bad obo"


def test_register_term_new_then_update_uses_page_context_and_merge_index(make):
    merge = FakeMerge([])
    state, tools = make(merge=merge)
    # Grounding: the sentence must be in the page text the tool is given (changed: the tool used to store any context).
    state.set_page_context("p.pdf", 7, "Some other words come first. PS is a polymer. Then more words follow here.")
    reg = tools["register_term"]
    out = reg.invoke({"term": "Polystyrene", "definition": "polymer", "category": "Chemical",
                      "context": "PS is a polymer.", "relations": [{"relation": "is_a", "related_term": "Polymer"}]})
    key = TermStore.normalize("Polystyrene")
    assert out == f"registered:{key}" and merge.added == ["Polystyrene"]
    rec = state.store.get(key)
    assert rec.pages == [7] and rec.source_papers == ["p.pdf"]
    assert rec.context_snippets[0].text == "PS is a polymer." and rec.context_snippets[0].page == 7
    assert (rec.context_snippets[0].kind, rec.context_snippets[0].verified) == ("quoted", True)
    assert reg.invoke({"term": "Polystyrene", "definition": "polymer", "category": "Chemical",
                       "source_paper": "q.pdf", "page": 2}) == f"updated:{key}"
    assert merge.added == ["Polystyrene"]  # not re-added
    assert set(state.store.get(key).source_papers) == {"p.pdf", "q.pdf"}


def test_register_term_without_context_has_no_snippet(make):
    state, tools = make()
    tools["register_term"].invoke({"term": "X", "definition": "d", "category": "Thing", "context": "has context"})
    rec = state.store.get(TermStore.normalize("X"))
    assert rec.context_snippets == [] and rec.pages == [] and rec.source_papers == []


def test_register_term_downgrades_verified_relations_that_violate_domain_range(make):
    schema = FakeSchema(invalid_pairs={("Chemical", "measured_by", "Process")})
    state, tools = make(schema=schema, existing=[("Annealing", "Process"), ("Water", "Chemical")])
    tools["register_term"].invoke({
        "term": "Polystyrene", "definition": "d", "category": "Chemical",
        "relations": [
            {"relation": "measured_by", "related_term": "Annealing", "verified": "true"},  # violates
            {"relation": "measured_by", "related_term": "Water", "verified": "true"},  # ok
            {"relation": "measured_by", "related_term": "Unknown", "verified": "true"},  # object unknown
            {"relation": "measured_by", "related_term": "Annealing", "verified": ""},  # already unverified
        ],
    })
    rels = state.store.get(TermStore.normalize("Polystyrene")).relations
    assert [(r.related_term, r.verified) for r in rels] == [
        ("Annealing", False), ("Water", "true"), ("Unknown", "true"), ("Annealing", "")]


# --- pilot 2026-11-24: nodes of an extracted KG carried no supporting passages --------------------------------------


def test_the_page_reaches_tools_that_run_on_worker_threads_of_the_agent_graph(make):
    """LangGraph runs tools on pool threads that get a COPY of the calling context (not its thread-locals)."""
    from langchain_core.runnables.config import ContextThreadPoolExecutor

    state, tools = make()
    state.set_page_context("tomo.pdf", 5, "Intro words. Cloud tomography images clouds. Closing words here.")
    with ContextThreadPoolExecutor() as pool:
        assert pool.submit(state.page_context).result() == ("tomo.pdf", 5)
        assert pool.submit(state.page_text).result().startswith("Intro words.")  # the page text reaches the tool thread
        pool.submit(tools["register_term"].invoke, {"term": "cloud tomography", "definition": "3D imaging of clouds.",
                                                    "category": "Thing", "context": "Cloud tomography images clouds."}).result()
    record = state.store.get("cloud tomography")
    assert record is not None and record.pages == [5]
    assert [(s.text, s.source_paper, s.page) for s in record.context_snippets] == [
        ("Cloud tomography images clouds.", "tomo.pdf", 5)]


def test_pages_processed_in_parallel_keep_their_own_page(make):
    from langchain_core.runnables.config import ContextThreadPoolExecutor

    state, _ = make()
    results = {}

    def page(n):
        state.set_page_context("a.pdf", n)
        with ContextThreadPoolExecutor() as pool:   # the tool node of this page's graph
            results[n] = pool.submit(state.page_context).result()
        state.clear_page_context()

    threads = [threading.Thread(target=page, args=(n,)) for n in range(1, 9)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == {n: ("a.pdf", n) for n in range(1, 9)}


def test_with_no_page_known_the_sentence_is_found_in_the_paper_and_the_found_page_is_stored(make):
    """No page number: the sentence is searched in every page of the paper; the page where it is found is stored."""
    state, tools = make()
    state.clear_page_context()
    state.current_source_paper = "a.pdf"
    state.paper_pages = ("a.pdf", ["Nothing here.", "Other words. A voxel grid divides the\nvolume. Close."])
    tools["register_term"].invoke({"term": "voxel grid", "definition": "d", "category": "Thing",
                                   "context": "A voxel grid divides the volume."})
    record = state.store.get("voxel grid")
    assert [(s.text, s.source_paper, s.page) for s in record.context_snippets] == [("A voxel grid divides the volume.", "a.pdf", 2)]
    assert record.pages == [] and record.source_papers == ["a.pdf"]      # what is known is recorded, nothing invented
    tools["register_term"].invoke({"term": "no context term", "definition": "d", "category": "Thing"})
    assert state.store.get("no context term").context_snippets == []      # no sentence, no findable mention: nothing


def test_a_sentence_that_cannot_be_verified_is_not_stored_and_is_counted(make):
    """Changed: with no page text at all the old code stored the model's sentence with the page unknown."""
    state, tools = make()
    state.clear_page_context()
    state.current_source_paper = "a.pdf"
    tools["register_term"].invoke({"term": "voxel grid", "definition": "d", "category": "Thing",
                                   "context": "A voxel grid divides the volume."})
    assert state.store.get("voxel grid").context_snippets == []
    assert state.store.run_passage_stats()["a.pdf"] == {"terms": 1, "with_passage": 0, "quoted": 0, "located": 0,
                                                         "rejected": 1, "uncovered": 1}


def test_a_second_passage_of_the_same_term_on_the_same_page_is_kept(make):
    state, tools = make()
    state.set_page_context("a.pdf", 2, "First mention of P3HT. Second mention of P3HT. Done.")
    for text in ("First mention of P3HT.", "Second mention of P3HT.", "First mention of P3HT."):
        tools["register_term"].invoke({"term": "P3HT", "definition": "d", "category": "Thing", "context": text})
    assert [s.text for s in state.store.get("p3ht").context_snippets] == ["First mention of P3HT.", "Second mention of P3HT."]


def test_a_rejected_sentence_is_counted_and_never_stored_and_a_mention_is_located_instead(make):
    page = ("Opening words of the page. P3HT films were annealed at 150 degrees for ten minutes before the measurement "
            "and then cooled slowly to room temperature in the glove box. Closing words of the page.")
    state, tools = make()
    state.set_page_context("a.pdf", 1, page)
    tools["register_term"].invoke({"term": "P3HT", "definition": "d", "category": "Thing",
                                   "context": "P3HT is the most studied polymer in the world."})   # not in the page
    record = state.store.get("p3ht")
    (snippet,) = record.context_snippets
    assert (snippet.kind, snippet.verified, snippet.page) == ("located", True, 1)
    assert snippet.text.startswith("P3HT films were annealed")
    assert "most studied" not in snippet.text
    assert state.store.run_passage_stats()["a.pdf"] == {"terms": 1, "with_passage": 1, "quoted": 0, "located": 1,
                                                         "rejected": 1, "uncovered": 0}


def test_the_stored_page_is_where_the_sentence_is_found_not_the_claimed_page(make):
    state, tools = make()
    state.set_page_context("a.pdf", 1, "Page one has nothing about it at all in this text.")
    state.paper_pages = ("a.pdf", ["Page one has nothing about it at all in this text.",
                                   "Page two states that the scattering vector is the momentum transfer."])
    tools["register_term"].invoke({"term": "scattering vector", "definition": "d", "category": "Thing",
                                   "context": "The scattering vector is the momentum transfer."})
    (snippet,) = state.store.get("scattering vector").context_snippets
    assert (snippet.page, snippet.kind) == (2, "quoted")


def test_no_sentence_and_no_mention_stores_nothing_and_counts_the_term_uncovered(make):
    state, tools = make()
    state.set_page_context("a.pdf", 1, "A page that never names the term the model invented for it, in plain words.")
    tools["register_term"].invoke({"term": "unrelated notion", "definition": "d", "category": "Thing"})
    assert state.store.get("unrelated notion").context_snippets == []
    assert state.store.run_passage_stats()["a.pdf"]["uncovered"] == 1


def test_a_hyphenated_line_broken_sentence_is_verified_by_the_squash_match(make):
    state, tools = make()
    state.set_page_context("a.pdf", 3, "The inter-\nface layer was grown on silicon.\nNext line.")
    tools["register_term"].invoke({"term": "interface layer", "definition": "d", "category": "Thing",
                                   "context": "The interface layer was grown on silicon."})
    (snippet,) = state.store.get("interface layer").context_snippets
    assert (snippet.text, snippet.page, snippet.verified) == ("The interface layer was grown on silicon.", 3, True)
