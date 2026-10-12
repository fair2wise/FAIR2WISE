"""T03: the frozen T18A legacy-UI flows replayed with ``F2W_RUNTIME=langgraph``.

Same harness, fakes and fixtures as ``tests/contracts/legacy_ui`` (unchanged). Every
retrieval call must go through the new read path, and every recorded response,
SSE stream (parsed and raw bytes) must match the baseline fixture. The single
expected difference is the trace detail's ``env.settings`` snapshot (all ``F2W_*``
environment variables, trace.py ``settings_snapshot``), which now also records
``F2W_RUNTIME``.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

LEGACY_UI = Path(__file__).resolve().parents[1] / "contracts" / "legacy_ui"
sys.path.insert(0, str(LEGACY_UI))

import _legacy_ui_harness as H  # noqa: E402

REPO_ROOT = LEGACY_UI.parents[2]
CASES = {p.stem: json.loads(p.read_text(encoding="utf-8"))
         for p in sorted((H.FIXTURE_DIR / "http").glob("*.json"))}
UI_STEPS = [step for flow in H.flows().values() for step in flow["steps"]]

#: Fixtures whose turn reaches AgentPipelineService._query_retrieval (so the new path).
EXPECTED_READ_PATH_FIXTURES = {
    "chat_stream_answered": 1,
    "chat_stream_insufficient_to_download": 1,
    "chat_action_stream_extraction_yes": 1,  # post-extraction re-check (api.py _resume_extraction)
    "chat_stream_error_malformed_verdict": 1,
    "chat_stream_json_override_missing_file_falls_back": 1,
    "chat_stream_retrieval_error": 1,
    "chat_answered": 1,
    "chat_insufficient_to_download": 1,
    "chat_500_malformed_verdict": 1,
    "chat_insufficient_for_failed_download": 1,
}


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    from app.modules.runtime_bridge import legacy_adapter

    mp = pytest.MonkeyPatch()
    try:
        ctx = H.install(mp, tmp_path_factory.mktemp("legacy-ui-langgraph") / "ws", REPO_ROOT)
        mp.setenv("F2W_RUNTIME", "langgraph")
        current = {"fid": None}
        hits: Dict[str, int] = {}
        statuses: Dict[str, List[str]] = {}
        original = legacy_adapter.LegacyReadPath.query

        async def counting(self, question, **kw):
            hits[current["fid"]] = hits.get(current["fid"], 0) + 1
            try:
                verdict = await original(self, question, **kw)
            except Exception:
                statuses.setdefault(current["fid"], []).append("raised")
                raise
            statuses.setdefault(current["fid"], []).append("returned")
            return verdict

        mp.setattr(legacy_adapter.LegacyReadPath, "query", counting)
        results: Dict[str, Any] = {"ctx": ctx, "responses": {}, "raw": {}, "hits": hits, "statuses": statuses}
        apps = []
        for flow in H.flows().values():
            app = H.make_ui_app(ctx, kg_mode=flow["kg_mode"])
            apps.append(app)
            client = H.client_for(app)
            for fid, method, path, kwargs, _notes in flow["steps"]:
                current["fid"] = fid
                resp = H.perform(client, ctx, method, path, kwargs)
                results["responses"][fid] = H.response_record(resp, ctx)
                results["raw"][fid] = resp.content
        results["apps"] = apps
        results["calls"] = dict(ctx.calls)
        yield results
    finally:
        mp.undo()


def test_every_retrieval_call_went_through_the_new_read_path(replay):
    assert replay["hits"] == EXPECTED_READ_PATH_FIXTURES
    assert sum(replay["hits"].values()) == replay["calls"]["retrieval.query"]
    assert replay["statuses"]["chat_stream_retrieval_error"] == ["raised"]  # original exception re-raised


def _without_runtime_flag(record: Dict[str, Any]) -> Dict[str, Any]:
    record = copy.deepcopy(record)
    settings = record["json"]["env"]["settings"]
    assert settings.pop("F2W_RUNTIME") == "langgraph"
    return record


@pytest.mark.parametrize("fid", [step[0] for step in UI_STEPS])
def test_response_matches_frozen_fixture(replay, fid):
    expected = CASES[fid]["response"]
    actual = replay["responses"][fid]
    if fid == "traces_get":
        actual = _without_runtime_flag(actual)
    assert actual["status_code"] == expected["status_code"]
    assert actual["content_type"] == expected["content_type"]
    assert actual.get("cors_headers") == expected.get("cors_headers")
    if "sse_events" in expected:
        assert actual["sse_events"] == expected["sse_events"]
        raw = (H.FIXTURE_DIR / expected["sse_file"]).read_bytes()
        assert H.sanitize_text(replay["raw"][fid].decode("utf-8"), replay["ctx"]).encode("utf-8") == raw
    elif "json" in expected:
        assert actual["json"] == expected["json"]
    else:
        assert actual.get("text") == expected.get("text")


def test_trace_differs_only_by_the_recorded_runtime_flag(replay):
    actual = replay["responses"]["traces_get"]
    assert actual != CASES["traces_get"]["response"]
    assert _without_runtime_flag(actual) == CASES["traces_get"]["response"]


def test_ui_openapi_is_unchanged(replay):
    expected = json.loads((H.FIXTURE_DIR / "openapi_ui.json").read_text(encoding="utf-8"))
    assert replay["apps"][-1].openapi() == expected
