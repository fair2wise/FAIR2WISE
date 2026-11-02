"""SessionMemory: topic segmentation, prompt context, turn recording and constrained compression."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("academy")

from app.modules.f2w_agent import session_memory as sm
from app.modules.f2w_agent.session_memory import SessionMemory


@pytest.fixture
def mem(tmp_path):
    return SessionMemory(tmp_path / "wd" / "memory.json")


def record(m, question, *, answer="An answer.", status="answered", sufficient=True, **kw):
    m.record_turn(user_message=question, answer=answer, status=status, sufficient=sufficient, rounds=kw.pop("rounds", []), **kw)


# ------------------------------------------------------------------ helpers
def test_clip_unique_and_entity_extraction():
    assert sm._clip("a   b\n c", 100) == "a b c" and sm._clip("abcdefgh", 5) == "abcd…" and sm._clip(None, 5) == ""
    assert sm._unique(["Alpha", "alpha", "", "Beta", "Gamma"], limit=2) == ["Alpha", "Beta"]
    ents = sm._extract_entities("Tell me about P3HT and silicon_wafer plus ultrafastdynamics in the sample/film", "ignored word")
    assert "P3HT" in ents and "silicon_wafer" in ents and "ultrafastdynamics" in ents and "sample/film" in ents
    assert "ignored" not in [e.lower() for e in ents] and "Tell" in ents


def test_publication_refs_and_growth_events():
    pubs = [{"paper_title": "T", "doi": "10.1/x"}, {"source_paper": "s.pdf"}, {}, "junk", {"doi": ""}]
    assert sm._publication_refs(pubs) == [{"paper_title": "T", "doi": "10.1/x"}, {"paper_title": "s.pdf", "doi": ""}]
    assert sm._publication_refs(None) == []
    assert len(sm._publication_refs([{"doi": str(i)} for i in range(50)])) == sm.MAX_PUB_REFS
    assert sm._round_growth_event(query="q", round_info={"retrieval": {}}) is None
    event = sm._round_growth_event(query="q" * 400, round_info={
        "round": 2, "retrieval": {"missing_topics": ["a", "A", "b"]}, "pending_pdfs": ["x.pdf"],
        "extraction": {"unique_terms": 9, "processed_files": 1}, "kg": {"nodes": 5, "edges": 7, "status": "ok"}})
    assert event["missing_topics"] == ["a", "b"] and event["unique_terms"] == 9 and event["kg_edges"] == 7
    assert event["extraction_mode"] == "targeted" and len(event["query"]) == 300 and "processed_pages_total" not in event
    only_kg = sm._round_growth_event(query="q", round_info={"kg": {"nodes": 1}})
    assert only_kg["kg_nodes"] == 1 and "extraction_mode" not in only_kg
    assert sm._parse_json_object('x {"a": 1} y') == {"a": 1} and sm._parse_json_object("{bad}") == {}
    assert sm._parse_json_object("") == {} and sm._parse_json_object("[1]") == {} and sm._parse_json_object("none") == {}


# ------------------------------------------------------------------ persistence
def test_load_tolerates_missing_corrupt_and_malformed_files(tmp_path):
    assert SessionMemory(tmp_path / "none.json").data["version"] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert SessionMemory(bad).data["recent_turns"] == []
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2]")
    assert SessionMemory(lst).data["summary"] == ""
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"version": 1, "summary": "s" * (sm.MAX_SUMMARY_CHARS + 50), "topics": "nope", "open_questions": {}}))
    m = SessionMemory(odd)
    assert m.data["version"] == 2 and m.data["topics"] == [] and m.data["open_questions"] == []
    assert len(m.data["summary"]) <= sm.MAX_SUMMARY_CHARS


def test_save_clear_and_has_context_round_trip(mem):
    assert mem.has_context() is False and mem.context_block() == ""
    assert mem.memory_section() == "SESSION_MEMORY:\n(none)\n"
    record(mem, "What is P3HT crystallinity?")
    assert mem.has_context() and SessionMemory(mem.path).data["recent_turns"][0]["content"] == "What is P3HT crystallinity?"
    mem.clear()
    assert not mem.has_context() and SessionMemory(mem.path).data["recent_turns"] == []


# ------------------------------------------------------------------ topics
def test_follow_ups_stay_on_topic_and_a_shift_starts_a_new_one(mem):
    record(mem, "How does P3HT crystallinity change with annealing?")
    first = mem.data["current_topic_id"]
    record(mem, "why?")  # no comparable entities: same topic
    assert mem.data["current_topic_id"] == first and mem._current_topic()["turn_count"] == 2
    record(mem, "And P3HT crystallinity at 150C annealing?")
    assert mem.data["current_topic_id"] == first and mem._current_topic()["turn_count"] == 3
    record(mem, "Describe the Zeolite Framework synthesis route")
    assert mem.data["current_topic_id"] != first and len(mem.data["topics"]) == 2
    scoped = mem._current_topic_turns()
    assert [t["role"] for t in scoped] == ["user", "assistant"]


def test_topic_list_is_capped_and_unknown_current_topic_is_ignored(mem):
    for i in range(sm.MAX_TOPICS + 3):
        record(mem, f"Quantum{i}Widget{i} Gadget{i}Thing{i}")
    assert len(mem.data["topics"]) == sm.MAX_TOPICS
    mem.data["current_topic_id"] = "topic-missing"
    assert mem._current_topic() is None
    mem.data["current_topic_id"] = None
    assert mem._current_topic() is None and len(mem._current_topic_turns()) == len(mem.data["recent_turns"])


def test_turns_without_matching_topic_tags_fall_back_to_all_turns(mem):
    mem.data["recent_turns"] = [{"role": "user", "content": "legacy", "topic_id": "old"}, "junk"]
    mem.data["current_topic_id"] = "new"
    assert [t["content"] for t in mem._current_topic_turns()] == ["legacy"]


# ------------------------------------------------------------------ prompt context
def test_context_block_includes_summary_questions_entities_growth_and_turns(mem):
    record(mem, "Does the Silicon_Nitride membrane fit?", answer="Unknown.", status="insufficient", sufficient=False,
           publications=[{"paper_title": "Paper One", "doi": "10.1/p"}],
           rounds=[{"round": 1, "extraction": {"unique_terms": 4}, "kg": {"nodes": 8}, "pending_pdfs": ["a.pdf"],
                    "retrieval": {"missing_topics": ["membranes"]}}])
    block = mem.context_block()
    for needle in ("Summary:", "Open questions: Does the Silicon_Nitride membrane fit?", "Important entities:",
                   "Recent KG growth: query=Does the Silicon_Nitride", "pdfs=1", "terms=4", "kg_nodes=8", "Recent turns:",
                   "user: Does the Silicon_Nitride"):
        assert needle in block, needle
    assert mem.memory_section().startswith("SESSION_MEMORY:\nUse this only for conversational continuity")
    assert mem.enrich_followup_question("and then?").startswith("and then?\n\nSession context for resolving references only")
    assert len(mem.context_block(max_chars=50)) <= 50
    assert SessionMemory(mem.path.with_name("empty.json")).enrich_followup_question("plain") == "plain"


def test_open_questions_are_only_added_for_unanswered_non_direct_turns(mem):
    record(mem, "Resolved Question One", status="answered", sufficient=False)
    record(mem, "Direct Question Two", status="direct_response", sufficient=False)
    record(mem, "Pending Question Three", status="insufficient", sufficient=False)
    assert mem.data["open_questions"] == ["Pending Question Three"]


def test_turn_and_event_buffers_are_bounded(mem):
    for i in range(sm.MAX_RECENT_TURNS + 5):
        record(mem, f"Question{i} about Subject{i}", rounds=[{"kg": {"nodes": i}}, "junk"])
    assert len(mem.data["recent_turns"]) == sm.MAX_RECENT_TURNS and len(mem.data["kg_growth"]) <= sm.MAX_KG_EVENTS
    assert len(mem.data["summary"]) <= sm.MAX_SUMMARY_CHARS


# ------------------------------------------------------------------ compression
def test_compression_trigger_and_constrained_prompt(mem):
    assert mem.needs_compression() is False and mem.compress(lambda p: "{}") is False  # nothing to compress
    mem.data["summary"] = "x" * sm.COMPRESS_TRIGGER_CHARS
    assert mem.needs_compression() is True
    prompts = []

    def chat(prompt):
        prompts.append(prompt)
        return 'sure {"summary": "short digest", "open_questions": ["still open"], "entities": ["Alpha"]}'

    mem.data["important_entities"] = ["Beta"]
    assert mem.compress(chat) is True
    assert "NEVER invent or add authors, DOIs" in prompts[0] and "CURRENT_SUMMARY" in prompts[0]
    assert mem.data["summary"] == "short digest" and mem.data["open_questions"] == ["still open"]
    assert mem.data["important_entities"][:2] == ["Alpha", "Beta"] and SessionMemory(mem.path).data["summary"] == "short digest"


@pytest.mark.parametrize("reply", ["not json", '{"summary": ""}', '{"other": 1}'])
def test_compression_keeps_the_existing_summary_on_unusable_replies(mem, reply):
    mem.data["summary"] = "keep me"
    assert mem.compress(lambda p: reply) is False and mem.data["summary"] == "keep me"


def test_compression_swallows_llm_errors(mem):
    mem.data["summary"] = "keep me"

    def boom(prompt):
        raise TimeoutError("llm")

    assert mem.compress(boom) is False and mem.data["summary"] == "keep me"
    assert mem.compress(lambda p: '{"summary": "new"}') is True and mem.data["open_questions"] == []
