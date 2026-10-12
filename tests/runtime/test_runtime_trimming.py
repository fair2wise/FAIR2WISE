"""T02: context trimming keeps tool-call pairs valid and evidence references intact (R-06)."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from app.modules.agent_runtime.message_trimming import (
    count_media_blocks,
    current_turn_start,
    drop_orphaned_tool_results,
    is_orphan_repair,
    prepare_for_model,
    repair_orphaned_tool_calls,
    trim_stale_media,
    trim_stale_tool_results,
)


def call(name, cid, **args):
    return AIMessage(id=f"ai-{cid}", content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def result(cid, content, **kw):
    return ToolMessage(id=f"tm-{cid}", tool_call_id=cid, name="lit_search", content=content, **kw)


def test_orphaned_call_repaired_in_place():
    msgs = [HumanMessage(id="h1", content="q"), call("lit_search", "c1", q="x"), HumanMessage(id="h2", content="q2")]
    for_model, replacements = repair_orphaned_tool_calls(msgs)
    fixed = for_model[1]
    assert fixed.id == "ai-c1" and fixed.tool_calls == [] and "Cancelled before it returned" in fixed.content
    assert is_orphan_repair(fixed) and replacements == [fixed]
    # calls kept pending (awaiting approval) are left alone
    same, none = repair_orphaned_tool_calls(msgs, keep_pending_ids=["c1"])
    assert none == [] and same[1].tool_calls


def test_orphaned_result_removed():
    msgs = [HumanMessage(id="h1", content="q"), result("ghost", "{}")]
    for_model, removals = drop_orphaned_tool_results(msgs)
    assert [m.id for m in for_model] == ["h1"]
    assert len(removals) == 1 and isinstance(removals[0], RemoveMessage) and removals[0].id == "tm-ghost"


def test_media_trimmed_only_before_current_turn():
    image = {"type": "image", "base64": "A" * 1000, "mime_type": "image/png"}
    msgs = [HumanMessage(id="h1", content=[{"type": "text", "text": "see"}, image]), AIMessage(id="a1", content="ok"),
            HumanMessage(id="h2", content=[{"type": "text", "text": "again"}, image])]
    for_model, replacements = trim_stale_media(msgs)
    assert count_media_blocks(msgs) == 2 and count_media_blocks(for_model) == 1
    assert "image/png" in for_model[0].content and [r.id for r in replacements] == ["h1"]
    assert current_turn_start(msgs) == 2


def test_stale_tool_output_stubbed_keeping_evidence_and_artifact():
    big = json.dumps({"data": "x" * 5000, "evidence_ids": ["ev-1", "ev-2"], "artifact_refs": ["sha256:abc"]})
    msgs = [HumanMessage(id="h1", content="q1"), call("lit_search", "c1"),
            result("c1", big, artifact={"result": {"evidence": ["kept"]}}),
            AIMessage(id="a1", content="answer"), HumanMessage(id="h2", content="q2"),
            call("lit_search", "c2"), result("c2", big)]
    for_model, replacements = trim_stale_tool_results(msgs, budget_chars=1000)
    stub = json.loads(for_model[2].content)
    assert stub["trimmed"] and stub["evidence_ids"] == ["ev-1", "ev-2"] and stub["artifact_refs"] == ["sha256:abc"]
    assert for_model[2].artifact == {"result": {"evidence": ["kept"]}}  # archival evidence is not trimmed
    assert for_model[2].tool_call_id == "c1"  # pair stays valid
    assert for_model[6].content == big  # current turn untouched
    assert [r.id for r in replacements] == ["tm-c1"]


def test_prepare_for_model_combines_without_duplicates():
    msgs = [HumanMessage(id="h1", content="q"), call("lit_search", "c1"), result("ghost", "{}"),
            HumanMessage(id="h2", content="next")]
    for_model, updates = prepare_for_model(msgs)
    assert [m.id for m in for_model] == ["h1", "ai-c1", "h2"] and for_model[1].tool_calls == []
    assert sorted(m.id for m in updates) == ["ai-c1", "tm-ghost"]
    clean = [HumanMessage(id="h1", content="q"), AIMessage(id="a", content="x")]
    assert prepare_for_model(clean) == (clean, [])
