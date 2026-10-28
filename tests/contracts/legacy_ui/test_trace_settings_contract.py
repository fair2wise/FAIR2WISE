"""The frozen trace ``env.settings`` block: harness inputs are ignored, contract keys are not.

``trace.settings_snapshot()`` records every ``F2W_*`` / ``KG_RAG_*`` variable of the
process. ``H.response_record`` restricts that block to ``H.TRACE_SETTINGS_CONTRACT``, so a
variable that only configures the test run (``F2W_TILED_PYTHON``, ``F2W_TIMING``, ...)
does not change the comparison. These tests pin both halves: such variables are ignored,
and any change to a contract key, or to any other trace field, still fails.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _legacy_ui_harness as H  # noqa: E402

REPO_ROOT = HERE.parents[2]
FIX = H.FIXTURE_DIR
TRACE_CASE = json.loads((FIX / "http" / "traces_get.json").read_text(encoding="utf-8"))
FROZEN = TRACE_CASE["response"]["json"]

# Variables that only configure a test run. Not part of the trace contract.
HARNESS_ENV = {
    "F2W_TILED_PYTHON": "/opt/harness/venv-tiled/bin/python",
    "F2W_TIMING": "1",
    "F2W_TIMING_REPORT": "timing.json",
    "F2W_HARNESS_X": "1",
    "KG_RAG_HARNESS_X": "1",
    "PLAYWRIGHT_BROWSERS_PATH": "/opt/harness/ms-playwright",
}


def _with_settings(payload: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    out = copy.deepcopy(payload)
    settings = out["env"]["settings"]
    for key, value in changes.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    return out


def _matches_frozen(payload: Dict[str, Any]) -> bool:
    """The comparison the replay tests make, applied to a candidate trace payload."""
    return H.normalize_trace_settings(payload) == FROZEN


def test_contract_keys_match_the_frozen_trace_fixtures():
    recorded = set(FROZEN["env"]["settings"])
    for path in sorted((FIX / "runtime").glob("traces_get.*.json")):
        recorded |= set(json.loads(path.read_text(encoding="utf-8"))["json"]["env"]["settings"])
    # F2W_QA_ROUTE is added to the runtime snapshot by expected_response when it is set.
    assert H.TRACE_SETTINGS_CONTRACT == recorded | {"F2W_QA_ROUTE"}
    assert not any(name in H.TRACE_SETTINGS_CONTRACT for name in HARNESS_ENV)


def test_frozen_fixture_is_a_fixed_point():
    assert H.normalize_trace_settings(FROZEN) == FROZEN
    for path in sorted((FIX / "runtime").glob("traces_get.*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))["json"]
        assert H.normalize_trace_settings(payload) == payload


def test_harness_variables_do_not_change_the_comparison():
    assert _matches_frozen(_with_settings(FROZEN, **HARNESS_ENV))


@pytest.mark.parametrize("change", [
    {"F2W_TRACE_ENABLED": "0"},                    # frozen value changed
    {"KG_RAG_RETRIEVAL_BACKEND": "embedding"},
    {"F2W_TRACE_DIR": "<WORKSPACE>/elsewhere"},
    {"F2W_LIVE_TILED": None},                      # frozen key missing
    {"KG_RAG_GRAPH_SOURCE": None},
    {"F2W_RUNTIME": "langgraph"},                  # contract key not in the default snapshot
    {"F2W_QA_ROUTE": "direct"},
])
def test_a_contract_settings_difference_still_fails(change):
    candidate = _with_settings(FROZEN, **HARNESS_ENV, **change)
    assert not _matches_frozen(candidate)


def test_a_difference_outside_settings_still_fails():
    for mutate in (
        lambda p: p["env"].__setitem__("backend", "ollama"),
        lambda p: p["env"]["models"].__setitem__("chat", "other-model"),
        lambda p: p["env"]["graphs"][0].__setitem__("sha256", "0" * 64),
        lambda p: p["env"].__setitem__("extra", True),
        lambda p: p.__setitem__("turn_id", "turn-other"),
        lambda p: p["request"].__setitem__("message", "changed"),
    ):
        candidate = _with_settings(FROZEN, **HARNESS_ENV)
        mutate(candidate)
        assert not _matches_frozen(candidate)


def test_normalization_keeps_settings_key_order():
    # Kept keys are not re-sorted, so the serialized order stays what the app wrote.
    reordered = copy.deepcopy(FROZEN)
    reordered["env"]["settings"] = dict(reversed(list(FROZEN["env"]["settings"].items())))
    normalized = H.normalize_trace_settings(reordered)
    assert json.dumps(normalized) != json.dumps(FROZEN)


def test_non_trace_payloads_are_untouched():
    payload = {"env": {"settings": {"F2W_HARNESS_X": "1"}}, "status": "ok"}
    assert H.normalize_trace_settings(payload) is payload
    assert H.normalize_trace_settings([FROZEN]) == [FROZEN]


@pytest.fixture(scope="module")
def replay_with_harness_env(tmp_path_factory):
    """Replay the flow that serves /traces with harness variables in the process env."""
    flow_name = TRACE_CASE["flow"]
    flow = H.flows()[flow_name]
    mp = pytest.MonkeyPatch()
    try:
        for name, value in HARNESS_ENV.items():
            mp.setenv(name, value)
        ctx = H.install(mp, tmp_path_factory.mktemp("legacy-ui-trace-env") / "ws", REPO_ROOT)
        client = H.client_for(H.make_ui_app(ctx, kg_mode=flow["kg_mode"]))
        out: Dict[str, Any] = {}
        for fid, method, path, kwargs, _notes in flow["steps"]:
            resp = H.perform(client, ctx, method, path, kwargs)
            if fid == "traces_get":
                out["raw"] = resp.json()
                out["record"] = H.response_record(resp, ctx)
                out["environ"] = dict(os.environ)
        yield out
    finally:
        mp.undo()


def test_live_trace_records_harness_variables(replay_with_harness_env):
    # The app does record them; without the normalization the comparison would break.
    settings = replay_with_harness_env["raw"]["env"]["settings"]
    assert settings["F2W_TILED_PYTHON"] == HARNESS_ENV["F2W_TILED_PYTHON"]
    assert settings["F2W_HARNESS_X"] == "1"
    assert "PLAYWRIGHT_BROWSERS_PATH" not in settings  # not an F2W_/KG_RAG_ name


def test_live_trace_matches_frozen_fixture_despite_harness_variables(replay_with_harness_env):
    expected = H.expected_response("traces_get", TRACE_CASE["response"], replay_with_harness_env["environ"])
    assert replay_with_harness_env["record"] == expected
