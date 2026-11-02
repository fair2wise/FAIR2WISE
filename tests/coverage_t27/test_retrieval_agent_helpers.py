"""RetrievalAgent pure helpers: judge parsing/prompting, intent routing, beam-path promotion and popularity hits."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("academy")

from app.modules.f2w_agent import retrieval_agent as ra
from app.modules.f2w_agent.retrieval_agent import RetrievalAgent


def test_flag_and_topic_coercion():
    assert ra._coerce_bool(True) is True and ra._coerce_bool(" YES ") is True and ra._coerce_bool("no") is False
    assert ra._coerce_bool("maybe") is False and ra._coerce_bool(1) is True and ra._coerce_bool(None) is False
    assert ra._coerce_missing_topics("one") == ["one"] and ra._coerce_missing_topics([" a ", "", 3]) == ["a", "3"]
    assert ra._coerce_missing_topics({"a": 1}) == [] and ra._coerce_missing_topics(None) == []


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("", "general"), ("which runs used ESAF 2026-00043", "tiled"), ("what detectors does the beamline have", "ops"),
        ("tell me about polymer blends", "literature"), ("hello there", "general"),
    ],
)
def test_query_intent_buckets(query, intent):
    assert ra._classify_query_intent(query) == intent


def test_question_kinds_and_leeway_prompt_selection():
    assert ra._is_ops_layout_question("what comes after M101?") and ra._is_ops_layout_question("how is the AXIS-SXR-40 connected?")
    assert not ra._is_ops_layout_question("what is RSoXS") and not ra._is_ops_layout_question(None)
    assert ra.leeway_system_for("beam path please") == ra.LAYOUT_LEEWAY_SYSTEM
    assert ra.leeway_system_for("what is RSoXS") == ra.LEEWAY_SYSTEM
    assert ra._is_conceptual_question("beamline layout of the optics") is True
    assert ra._is_conceptual_question("") is False


def test_history_formatting_keeps_last_eight_nonempty_turns():
    assert ra._format_history_for_judge(None) == "" and ra._format_history_for_judge([{"role": "user", "content": " "}]) == ""
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)] + [{"content": "no role"}, {"role": "a", "content": "x" * 2000}]
    text = ra._format_history_for_judge(history)
    assert text.startswith("Conversation so far:\n") and "q0" not in text and "q1" not in text and "q5" in text
    assert "user: no role" in text and "a: " + "x" * 1200 in text and "x" * 1201 not in text and text.endswith("\n\n")


def test_judge_prompt_picks_a_question_type_hint_and_includes_history():
    assert "(empty)" in ra.build_judge_prompt("what is it", "  ")
    layout = ra.build_judge_prompt("what comes after M101?", "ctx")
    assert "hardware layout" in layout and "Retrieved Context:\nctx" in layout
    assert "conceptual/overview" in ra.build_judge_prompt("explain how RSoXS works", "c", [{"role": "user", "content": "hi"}])
    assert ra.build_judge_prompt("explain how RSoXS works", "c", [{"role": "user", "content": "hi"}]).startswith(
        "Conversation so far:")
    assert "non-numeric question" in ra.build_judge_prompt("x", "c")


def test_parse_judge_is_tolerant_and_never_trusts_an_answerless_sufficient_verdict():
    assert ra._parse_judge("") == {"sufficient": False, "answer": None, "missing_topics": []}
    ok = ra._parse_judge('noise {"sufficient": "yes", "answer": "42", "missing_topics": "none"} tail')
    assert ok["sufficient"] is True and ok["answer"] == "42" and ok["missing_topics"] == ["none"]
    blank = ra._parse_judge('{"sufficient": true, "answer": "  "}')
    assert blank["sufficient"] is False and blank["answer"] == "  "
    assert ra._parse_judge('{"a": 1} {broken')["raw"] == '{"a": 1} {broken'
    nested = ra._parse_judge('{"sufficient": false, "meta": {"x": {"y": 1}}, "missing_topics": ["t"]}')
    assert nested["missing_topics"] == ["t"] and nested["answer"] is None


class KG:
    def __init__(self, nodes, edges=None):
        self.nodes, self.out_edges = nodes, edges or {}


def test_direct_evidence_rules():
    info = SimpleNamespace(id="n", description="", category="")
    assert ra._has_direct_evidence(None, info) is False
    assert ra._has_direct_evidence(KG({"n": {"source_papers": ["p"]}}), info) is True
    assert ra._has_direct_evidence(KG({"n": {"code_snippet": "x=1"}}), info) is True
    assert ra._has_direct_evidence(KG({"n": {"description": " d "}}), info) is True
    assert ra._has_direct_evidence(KG({"n": {}}), SimpleNamespace(id="n", description="from info", category="")) is True
    assert ra._has_direct_evidence(KG({"n": "notadict"}), info) is False
    assert ra._has_direct_evidence(KG({"n": {"category": "ExperimentalTechnique"}}), info) is True
    assert ra._has_direct_evidence(KG({"n": {}}, {"n": [{"predicate": "p"}]}), info) is False
    assert ra._has_direct_evidence(KG({"n": {}}, {"n": [{"predicate": "p", "has_evidence": True}]}), info) is True


def test_retrieve_nodes_wrapper_retries_with_old_signature(monkeypatch):
    calls = []

    def new_style(question, kg, hops, max_nodes):
        calls.append(("new", hops, max_nodes))
        return ["x"]

    monkeypatch.setattr(ra.krag, "retrieve_nodes", new_style)
    assert ra._call_retrieve_nodes("q", object(), 2, 9) == ["x"]

    def old_style(question, kg):
        calls.append(("old",))
        return ["y"]

    monkeypatch.setattr(ra.krag, "retrieve_nodes", old_style)
    assert ra._call_retrieve_nodes("q", object(), 2, 9) == ["y"] and calls[-1] == ("old",)


def test_beam_path_promotion_by_index_and_by_directed_successors():
    indexed = KG({"b": {"name": "B", "beam_path_index": "2", "evidence_ct": 3},
                  "a": {"name": "A", "beam_path_index": 1, "description": "first"},
                  "bad": {"beam_path_index": "x"}, "skip": "notadict", "other": {"beam_path_index": ""}})
    infos = ra._beam_path_nodeinfos(indexed)
    assert [i.id for i in infos] == ["a", "b"] and infos[0].score_prp > infos[1].score_prp
    assert infos[1].evidence_ct == 3 and infos[0].category == "BeamlineStage" and infos[0].description == "first"

    nodes = {n: {"name": n.upper()} for n in ("s", "m", "e", "z")}
    edges = {"s": [{"predicate": "rel:beam_path_next", "object": "m"}],
             "m": [{"predicate": "rel:beam_path_next", "object": "e"}, {"predicate": "rel:other", "object": "z"}]}
    walked = ra._beam_path_nodeinfos(KG(nodes, edges))
    assert [i.id for i in walked] == ["s", "m", "e"] and walked[0].score_prp > walked[2].score_prp
    cyclic = dict(edges, e=[{"predicate": "rel:beam_path_next", "object": "s"}])
    assert ra._beam_path_nodeinfos(KG(nodes, cyclic)) == []  # a pure cycle has no start node: nothing is invented
    assert ra._beam_path_nodeinfos(KG({})) == [] and ra._beam_path_nodeinfos(KG({"x": {"name": "x"}})) == []


def test_substring_device_fallback_matches_named_devices_only():
    kg = KG({"d1": {"name": "AXIS-SXR-40", "category": "Detector", "evidence_ct": 4},
             "d2": {"name": "M1"},  # shorter than four characters
             "d3": "junk", "d4": {"name": "AXIS-SXR-40"}, "d5": {"name": "Not mentioned"}})
    hits = RetrievalAgent._substring_device_fallback("how is the axis-sxr-40 connected?", {"ops": kg}, limit=5)
    assert [h.id for h in hits] == ["d1", "d4"] and hits[0].score == 1.5 and hits[0].evidence_ct == 4
    assert hits[0].graph_id == "ops" and hits[0].payload.category == "Detector"
    assert len(RetrievalAgent._substring_device_fallback("axis-sxr-40", {"ops": kg}, limit=1)) == 1


def test_rsoxs_popular_nodes_rank_by_paper_count_and_ignore_thin_nodes():
    kg = KG({"a": {"source_papers": ["p1", "p2", "p3"], "name": "A", "entityType": "Material"},
             "b": {"papers": ["p1", "p2"], "label": "Bee", "description": "d"},
             "c": {"papers": ["p1"]}, "d": {"papers": "notalist"}, "e": "junk"})
    hits = RetrievalAgent._rsoxs_popular_nodes(kg, "rsoxs", 5)
    assert [h.id for h in hits] == ["a", "b"] and hits[0].score == pytest.approx(0.33) and hits[1].name == "Bee"
    assert hits[0].category == "Material" and hits[1].payload.description == "d" and hits[0].evidence_ct == 3
    assert [h.id for h in RetrievalAgent._rsoxs_popular_nodes(kg, "rsoxs", 1)] == ["a"]
    as_list = SimpleNamespace(nodes=[{"id": "x", "papers": ["1", "2"]}, "junk"])
    assert [h.id for h in RetrievalAgent._rsoxs_popular_nodes(as_list, "g", 3)] == ["x"]
    assert RetrievalAgent._rsoxs_popular_nodes(SimpleNamespace(nodes=None), "g", 3) == []
