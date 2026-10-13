"""H2: the live legacy UI serves the extracted-term summary through ``lit_summarize_extracted_terms``.

``AgentPipelineService`` builds a ``LegacySummaryPath`` beside the T03 read path
(``runtime_bridge.selection.attach_summary_path``) when ``F2W_RUNTIME=langgraph``;
both ``api.py`` call sites (``_maybe_report_active_extraction`` and
``_resume_extraction``) then run through the Tool. Default mode is unchanged:
no adapter, no Tool run. A spy on ``PaperSummaryTool.run`` proves the route,
and the T19B synthetic fixtures prove the bytes are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from tests.operations import test_summary_legacy_ui as T19B

H = T19B.H


def _spy(mp) -> Dict[str, Any]:
    from app.modules.operations.papers import PaperSummaryTool
    from app.modules.operations.summary_compat import LegacySummaryPath

    record: Dict[str, Any] = {"tool_runs": [], "paths": []}
    run = PaperSummaryTool.run
    summarize = LegacySummaryPath.summarize_extracted_terms

    def spy_run(self, inv):
        record["tool_runs"].append(sorted(inv.args))
        return run(self, inv)

    def spy_summarize(self, *args, **kwargs):
        record["paths"].append(self)
        return summarize(self, *args, **kwargs)

    mp.setattr(PaperSummaryTool, "run", spy_run)
    mp.setattr(LegacySummaryPath, "summarize_extracted_terms", spy_summarize)
    return record


def _set_runtime(mp, runtime: str) -> None:
    if runtime == "langgraph":
        mp.setenv("F2W_RUNTIME", "langgraph")
    else:
        mp.delenv("F2W_RUNTIME", raising=False)


@pytest.mark.parametrize("runtime", ["legacy", "langgraph"])
@pytest.mark.parametrize("name", sorted(T19B.SYNTHETIC))
def test_extraction_reported_flow(monkeypatch, tmp_path, runtime, name):
    """``/chat/stream`` + ``/chat`` ``extraction_reported``: byte-identical, through the Tool in langgraph mode."""
    _set_runtime(monkeypatch, runtime)
    record = _spy(monkeypatch)
    out = T19B._run_report(tmp_path, name, adapter=False)
    expected = T19B._fixture(name)
    assert out["sse_raw"].encode("utf-8") == expected["sse_raw"].encode("utf-8")
    assert out["json"] == expected["json"]
    if runtime == "langgraph":
        assert record["tool_runs"] == [["max_terms"], ["max_terms"]]  # no path or filename argument
        assert len(record["paths"]) == 2 and all(p.fallbacks == 0 for p in record["paths"])
    else:
        assert record == {"tool_runs": [], "paths": []}


@pytest.mark.parametrize("runtime", ["legacy", "langgraph"])
def test_post_extraction_recheck_flow(monkeypatch, tmp_path, runtime):
    """Frozen ``chat_action_stream_extraction_yes`` (``_resume_extraction``): fixture bytes, Tool in langgraph."""
    _set_runtime(monkeypatch, runtime)
    record = _spy(monkeypatch)
    ctx = H.install(monkeypatch, tmp_path / "ws", T19B.REPO_ROOT)
    _set_runtime(monkeypatch, runtime)
    flow = next(f for f in H.flows().values()
                if any(step[0] == "chat_action_stream_extraction_yes" for step in f["steps"]))
    client = H.client_for(H.make_ui_app(ctx, kg_mode=flow["kg_mode"]))
    runs_by_fid: Dict[str, List[Any]] = {}
    for fid, method, path, kwargs, _notes in flow["steps"]:
        start = len(record["tool_runs"])
        resp = H.perform(client, ctx, method, path, kwargs)
        if len(record["tool_runs"]) > start:
            runs_by_fid[fid] = record["tool_runs"][start:]
        if fid == "chat_action_stream_extraction_yes":
            expected = T19B.CASES[fid]["response"]
            raw = (H.FIXTURE_DIR / expected["sse_file"]).read_bytes()
            assert H.sanitize_text(resp.content.decode("utf-8"), ctx).encode() == raw
    if runtime == "langgraph":
        assert runs_by_fid == {"chat_action_stream_extraction_yes": [
            ["max_terms", "missing_topics", "query", "relevant_node_names"]]}
    else:
        assert runs_by_fid == {} and record["paths"] == []


def test_default_mode_builds_no_adapter():
    from app.modules.runtime_bridge.selection import attach_summary_path

    assert attach_summary_path(object(), environ={}) is None
    assert attach_summary_path(object(), environ={"F2W_RUNTIME": "legacy"}) is None


def test_langgraph_adapter_registers_the_tool(tmp_path: Path):
    from app.modules.operations.papers import TOOL_NAME
    from app.modules.runtime_bridge.selection import attach_summary_path

    class Pipeline:
        retrieval = None
        _active_session_key = "__legacy__"

    path = attach_summary_path(Pipeline(), environ={"F2W_RUNTIME": "langgraph"})
    assert path is not None and path.executor.registry.get(TOOL_NAME) is not None
