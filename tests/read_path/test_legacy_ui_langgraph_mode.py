"""T03: the frozen T18A legacy-UI flows replayed with ``F2W_RUNTIME=langgraph``.

Same harness, fakes and fixtures as ``tests/contracts/legacy_ui`` (unchanged). Every
retrieval call must go through the new read path, and every recorded response,
SSE stream (parsed and raw bytes) must match the baseline fixture.

Both T03B QA routes are replayed: ``supervisor`` (the default, ``F2W_QA_ROUTE`` unset;
the harness's ``FakePlanner`` is the planning model) and ``direct``. The only expected
difference is ``traces_get``, whose langgraph-mode expectation lives in
``fixtures/runtime/traces_get.langgraph[.direct].json`` (H2 Q2): ``env.settings``
records ``F2W_RUNTIME``, and on the supervisor route ``llm_calls`` holds the planning call.
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


@pytest.fixture(scope="module", params=["supervisor-default", "direct"])
def replay(request, tmp_path_factory):
    from app.modules.runtime_bridge import legacy_adapter

    mp = pytest.MonkeyPatch()
    try:
        ctx = H.install(mp, tmp_path_factory.mktemp("legacy-ui-langgraph") / "ws", REPO_ROOT)
        mp.setenv("F2W_RUNTIME", "langgraph")
        env = {"F2W_RUNTIME": "langgraph"}
        if request.param == "direct":
            mp.setenv("F2W_QA_ROUTE", "direct")
            env["F2W_QA_ROUTE"] = "direct"
        else:
            mp.delenv("F2W_QA_ROUTE", raising=False)
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
        results: Dict[str, Any] = {"ctx": ctx, "responses": {}, "raw": {}, "hits": hits, "statuses": statuses,
                                   "route": request.param, "env": env}
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


def test_supervisor_route_plans_every_read_turn_once(replay):
    planned = replay["ctx"].planner_calls
    if replay["route"] == "direct":
        assert planned == []
        return
    # One planning call per read turn, the read tool offered, the user's question passed on.
    assert len(planned) == sum(replay["hits"].values())
    assert all(call["tools"] == ["answer_from_knowledge"] for call in planned)
    assert all(call["question"] for call in planned)


def _without_runtime_flag(record: Dict[str, Any]) -> Dict[str, Any]:
    record = copy.deepcopy(record)
    record["json"]["env"]["settings"].pop("F2W_RUNTIME")
    record["json"]["env"]["settings"].pop("F2W_QA_ROUTE", None)
    record["json"]["llm_calls"] = []
    return record


@pytest.mark.parametrize("fid", [step[0] for step in UI_STEPS])
def test_response_matches_frozen_fixture(replay, fid):
    expected = H.expected_response(fid, CASES[fid]["response"], replay["env"])
    actual = replay["responses"][fid]
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


def test_trace_differs_only_by_the_runtime_flag_and_planning_call(replay):
    actual = replay["responses"]["traces_get"]
    assert actual == H.expected_response("traces_get", CASES["traces_get"]["response"], replay["env"])
    assert actual["json"]["env"]["settings"]["F2W_RUNTIME"] == "langgraph"
    assert _without_runtime_flag(actual) == CASES["traces_get"]["response"]
    labels = [c["label"] for c in actual["json"]["llm_calls"]]
    assert labels == ([] if replay["route"] == "direct" else ["supervisor-plan"])


def test_ui_openapi_is_unchanged(replay):
    expected = json.loads((H.FIXTURE_DIR / "openapi_ui.json").read_text(encoding="utf-8"))
    assert replay["apps"][-1].openapi() == expected
