"""T19B: the lit_summarize_extracted_terms Tool/Skill contract, executor checks and paper selection."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ContractError, Effect, ErrorCode, ToolStatus
from app.modules.operations.papers import (
    LIT_SUMMARIZE_EXTRACTED_TERMS,
    MAX_RESULT_CHARS,
    SUMMARIZE_LITERATURE_SKILL,
    TOOL_NAME,
    PaperScope,
    PaperSummaryTool,
    pinned_paper_scope,
)
from app.modules.operations.summary_compat import session_paper_resolver
from app.modules.runtime_bridge.bootstrap import build_read_registry, build_read_skills
from app.modules.runtime_bridge.qa_tool import LegacyResultLedger
from tests.operations.summary_fakes import CASES, FILENAME, term, write_case
from tests.runtime.runtime_fakes import als_ctx, local_ctx


def _paths(tmp_path, name="capped"):
    entry, terms = CASES[name]
    return write_case(tmp_path / name, entry, terms)


def _scope(paths, paper_id=None):
    return PaperScope(paths["terms_path"], paths["manifest_path"], paths["filename"], paper_id=paper_id)


def _executor(tool):
    registry = build_read_registry(None, LegacyResultLedger(), paper_summary=tool)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0)), registry


# -- spec ----------------------------------------------------------------------------

def test_spec_is_read_only_free_and_has_no_path_inputs():
    spec = LIT_SUMMARIZE_EXTRACTED_TERMS
    assert spec.name == "lit_summarize_extracted_terms" and spec.domain == "operations"
    assert spec.effects == frozenset({Effect.READ_CORPUS}) and spec.cost_class.value == "free"
    assert spec.required_capabilities == frozenset({"chat.ask"}) and spec.idempotent
    props = spec.input_schema["properties"]
    assert set(props) == {"max_terms", "query", "missing_topics", "relevant_node_names"}
    assert spec.input_schema["additionalProperties"] is False and not spec.input_schema.get("required")
    assert props["max_terms"]["maximum"] == 16
    assert not any(word in json.dumps(spec.input_schema) for word in ("path", "filename", "paper_id", "file"))


def test_default_read_registry_is_unchanged_and_opt_in_adds_tool_and_skill():
    default = build_read_registry(None, LegacyResultLedger())
    assert [s.name for s in default.list()] == ["answer_from_knowledge"]
    assert [s.name for s in build_read_skills(default).list_skills()] == ["answer_literature_question"]

    _, registry = _executor(PaperSummaryTool())
    assert [s.name for s in registry.list()] == ["answer_from_knowledge", TOOL_NAME] and registry.frozen
    skills = build_read_skills(registry)
    assert [e.name for e in skills.resolve("summarize_literature")] == [TOOL_NAME]
    assert SUMMARIZE_LITERATURE_SKILL.required_capabilities == frozenset({"chat.ask"})
    # ordinary signed-in ALS users (chat capabilities) see it; a context without chat.ask does not
    assert "summarize_literature" in [s.name for s in skills.visible_to(als_ctx())]
    assert skills.visible_to(als_ctx(capabilities=frozenset({"chat.read_support"}))) == []


# -- executor -----------------------------------------------------------------------

def test_ok_result_is_typed_bounded_and_cites_eligible_pages(tmp_path):
    paths = _paths(tmp_path)
    tool = PaperSummaryTool(lambda ctx: _scope(paths, "syn-7"))
    executor, _ = _executor(tool)
    result = executor.execute(als_ctx(), TOOL_NAME, {})
    assert result.status is ToolStatus.OK and result.error_code is None
    data = result.data
    assert data["source_paper"] == FILENAME and data["paper_id"] == "syn-7"
    assert (data["term_count"], data["displayed_term_count"], data["completeness"]) == (23, 16, "partial")
    assert data["counting_unit"] == "raw_extracted_term_record" and data["scan_complete"] is True
    assert len(json.dumps(data, sort_keys=True)) <= MAX_RESULT_CHARS
    assert [e.citation for e in result.evidence] == data["citations"]
    assert {e.locator.page for e in result.evidence} == set(data["used_pages"]) <= {1, 2, 3, 4}
    assert all(e.work_id == f"pdf-file:{FILENAME}" for e in result.evidence)
    assert any("partial display" in w for w in result.warnings)
    assert len(tool.ledger) == 1  # legacy payload retained for the adapter only


def test_denied_without_chat_ask_and_nothing_runs(tmp_path):
    calls = []
    tool = PaperSummaryTool(lambda ctx: calls.append(ctx) or _scope(_paths(tmp_path)))
    executor, _ = _executor(tool)
    result = executor.execute(als_ctx(capabilities=frozenset({"chat.read_support"})), TOOL_NAME, {})
    assert result.status is ToolStatus.DENIED and calls == [] and len(tool.ledger) == 0


@pytest.mark.parametrize("args", [
    {"terms_path": "/etc/passwd"}, {"filename": "x.pdf"}, {"paper_id": "p"}, {"max_terms": 17},
    {"max_terms": 0}, {"query": "q" * 16001}, {"missing_topics": ["t"] * 101},
])
def test_model_cannot_choose_a_file_or_exceed_bounds(tmp_path, args):
    tool = PaperSummaryTool(lambda ctx: _scope(_paths(tmp_path)))
    executor, _ = _executor(tool)
    result = executor.execute(local_ctx(), TOOL_NAME, args)
    assert result.status is ToolStatus.INVALID and len(tool.ledger) == 0


def test_no_active_paper_is_not_found_not_empty():
    executor, _ = _executor(PaperSummaryTool(lambda ctx: None))
    result = executor.execute(local_ctx(), TOOL_NAME, {})
    assert result.status is ToolStatus.ERROR and result.error_code is ErrorCode.NOT_FOUND
    executor, _ = _executor(PaperSummaryTool())
    assert executor.execute(local_ctx(), TOOL_NAME, {}).error_code is ErrorCode.NOT_FOUND


def test_insufficient_is_empty_and_unreadable_hides_server_paths(tmp_path):
    executor, _ = _executor(PaperSummaryTool(lambda ctx: _scope(_paths(tmp_path, "insufficient"))))
    empty = executor.execute(local_ctx(), TOOL_NAME, {})
    assert empty.status is ToolStatus.EMPTY and empty.data is None
    paths = _paths(tmp_path, "targeted")
    missing = PaperScope(paths["terms_path"], str(tmp_path / "missing_manifest.json"), FILENAME)
    executor, _ = _executor(PaperSummaryTool(lambda ctx: missing))
    err = executor.execute(local_ctx(), TOOL_NAME, {})
    assert err.status is ToolStatus.ERROR and err.error_code is ErrorCode.NOT_FOUND
    assert str(tmp_path) not in json.dumps(err.model_dump(mode="json"))


def test_exception_is_internal_and_kept_for_the_adapter(tmp_path):
    bad = write_case(tmp_path / "bad", {"selected_pages": [1]}, "not a mapping")
    tool = PaperSummaryTool(lambda ctx: _scope(bad))
    executor, _ = _executor(tool)
    result = executor.execute(local_ctx(), TOOL_NAME, {})
    assert result.error_code is ErrorCode.INTERNAL
    assert isinstance(tool.ledger.pop(result.provenance_id).error, AttributeError)


def test_pinned_scope_wins_and_reaches_the_async_worker_thread(tmp_path):
    resolver_calls = []
    tool = PaperSummaryTool(lambda ctx: resolver_calls.append(1))
    executor, _ = _executor(tool)
    pinned = _scope(_paths(tmp_path, "duplicates"))
    with pinned_paper_scope(pinned):
        result = asyncio.run(executor.aexecute(local_ctx(), TOOL_NAME, {"max_terms": 6}))
    assert result.status is ToolStatus.OK and result.data["term_count"] == 3 and resolver_calls == []
    assert tool.resolve(local_ctx()) is None  # unpinned again afterwards


def test_query_arguments_reach_the_wrapped_function(tmp_path):
    from app.modules.f2w_agent.paper_evidence_agent import summarize_extracted_terms

    paths = write_case(tmp_path / "q", {"selected_pages": [2]},
                       [term("Ionic conductivity", [2]), term("Sample holder", [2])])
    tool = PaperSummaryTool(lambda ctx: _scope(paths))
    executor, _ = _executor(tool)
    args = {"max_terms": 6, "query": "ionic conductivity?", "relevant_node_names": ["Ionic conductivity"]}
    result = executor.execute(local_ctx(), TOOL_NAME, args)
    baseline = summarize_extracted_terms(paths["terms_path"], paths["manifest_path"], FILENAME, **args)
    assert result.data["summary_text"] == baseline["answer"] and result.data["relevance_mode"] == "matched"


# -- session resolver (model-driven path) ----------------------------------------------

class _Workflow:
    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return json.loads(json.dumps(self.state))


def _pipeline(paths, key="__legacy__", active=True):
    paper = {"paper_id": "syn-9", "filename": paths["filename"], "status": "extracted"} if active else None
    return SimpleNamespace(_active_session_key=key, workflow=_Workflow({"active_paper": paper}),
                           coord=SimpleNamespace(session_terms=paths["terms_path"],
                                                 extraction_manifest=paths["manifest_path"]))


def test_session_resolver_uses_the_legacy_session_state(tmp_path):
    paths = _paths(tmp_path, "duplicates")
    resolve = session_paper_resolver(_pipeline(paths))
    assert resolve(local_ctx()) == PaperScope(paths["terms_path"], paths["manifest_path"], FILENAME, "syn-9")
    assert session_paper_resolver(_pipeline(paths, active=False))(local_ctx()) is None
    named = session_paper_resolver(_pipeline(paths, key="s1"))
    assert named(local_ctx(session_id="s1")).filename == FILENAME
    with pytest.raises(ContractError) as err:
        named(local_ctx(session_id="other"))
    assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError):
        resolve(local_ctx(session_id="s1"))  # unnamed legacy session vs a named request


def test_session_mismatch_is_denied_through_the_executor(tmp_path):
    tool = PaperSummaryTool(session_paper_resolver(_pipeline(_paths(tmp_path), key="s1")))
    executor, _ = _executor(tool)
    assert executor.execute(local_ctx(session_id="s2"), TOOL_NAME, {}).status is ToolStatus.DENIED
    assert executor.execute(local_ctx(session_id="s1"), TOOL_NAME, {}).status is ToolStatus.OK
