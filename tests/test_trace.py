"""Per-turn interaction traces: context, redaction, schema, and the off switch."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent.coordinator import CoordinatorConfig
from app.modules.f2w_agent.trace import (
    DETAIL_KEYS,
    ENV_KEYS,
    LLM_CALL_KEYS,
    ORCHESTRATION_KEYS,
    PRECHECK_KEYS,
    REQUEST_KEYS,
    RESPONSE_KEYS,
    RETRIEVAL_KEYS,
    ROUTE_FLAG_KEYS,
    bind_context,
    finish_turn,
    note_decision,
    note_precheck,
    note_request,
    open_turn,
    record_llm_call,
    record_retrieval,
    redact,
    settings_snapshot,
    tracing_enabled,
)


def _enable(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("F2W_TRACE_ENABLED", "1")
    monkeypatch.setenv("F2W_TRACE_DIR", str(root))


def _detail_and_summary(root: Path, turn_id: str) -> tuple[dict, dict]:
    details = list(root.glob(f"*/turns/{turn_id}.json"))
    summaries = list(root.glob("*/turns.jsonl"))
    assert len(details) == 1
    assert len(summaries) == 1
    detail = json.loads(details[0].read_text(encoding="utf-8"))
    lines = [line for line in summaries[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    return detail, json.loads(lines[0])


def test_run_in_executor_sees_bound_collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    seen: dict[str, str | None] = {"unbound": "missing", "bound": "missing"}

    async def scenario() -> None:
        collector = open_turn(
            client_turn_id="turn-exec-1",
            session_id="session-exec",
            request={"message": "executor question"},
        )
        assert collector.turn_id == "turn-exec-1"

        def unbound() -> None:
            from app.modules.f2w_agent.trace import current_trace

            trace = current_trace()
            seen["unbound"] = None if trace is None else trace.turn_id
            record_llm_call(
                label="should-not-land",
                model="m",
                messages=[{"role": "user", "content": "lost"}],
                response="nope",
                latency_ms=1,
            )

        def bound() -> None:
            from app.modules.f2w_agent.trace import current_trace

            trace = current_trace()
            seen["bound"] = None if trace is None else trace.turn_id
            record_llm_call(
                label="orchestrator-classify",
                model="test-model",
                messages=[
                    {"role": "system", "content": "classify this"},
                    {"role": "user", "content": "executor question"},
                ],
                response='{"action":"answer"}',
                latency_ms=4.5,
            )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, unbound)
        await loop.run_in_executor(None, bind_context(bound))
        finish_turn()

    asyncio.run(scenario())

    assert seen["unbound"] is None
    assert seen["bound"] == "turn-exec-1"
    detail, summary = _detail_and_summary(tmp_path, "turn-exec-1")
    labels = [call["label"] for call in detail["llm_calls"]]
    assert labels == ["orchestrator-classify"]
    assert detail["llm_calls"][0]["model"] == "test-model"
    assert detail["llm_calls"][0]["response"] == '{"action":"answer"}'
    assert detail["llm_calls"][0]["system_prompt_sha256"]
    assert summary["n_llm_calls"] == 1
    assert summary["turn_id"] == "turn-exec-1"


def test_redacts_api_key_names_and_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    tiled = "tiled-secret-value-123456"
    cborg = "cborg-secret-value-999999"
    monkeypatch.setenv("TILED_API_KEY", tiled)
    monkeypatch.setenv("CBORG_API_KEY", cborg)
    monkeypatch.setenv("F2W_EXAMPLE_API_KEY", "f2w-key-should-hide")
    monkeypatch.setenv("F2W_BACKEND", "cborg")

    cleaned = redact(
        {
            "F2W_EXAMPLE_API_KEY": "f2w-key-should-hide",
            "Authorization": f"Bearer {cborg}",
            "note": f"called tiled with {tiled} and model key {cborg}",
            "nested": [{"token": tiled}],
        }
    )
    assert cleaned["F2W_EXAMPLE_API_KEY"] == "[REDACTED]"
    assert cleaned["Authorization"] == "[REDACTED]"
    assert tiled not in cleaned["note"]
    assert cborg not in cleaned["note"]
    assert cleaned["note"].count("[REDACTED]") == 2
    assert cleaned["nested"] == [{"token": "[REDACTED]"}]

    snapshot = settings_snapshot()
    assert snapshot["F2W_EXAMPLE_API_KEY"] == "[REDACTED]"
    assert snapshot["F2W_BACKEND"] == "cborg"
    assert "TILED_API_KEY" not in snapshot
    blob = json.dumps(snapshot)
    assert tiled not in blob
    assert cborg not in blob


def test_detail_schema_is_complete_and_unclipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    message = "Q" * 400 + " tail that must survive"
    open_turn(
        client_turn_id="turn-schema-1",
        session_id="session-schema",
        parent_turn_id="turn-parent",
        request={"message": message, "history": [{"role": "user", "content": message}]},
        env={
            "git_sha": "abc123",
            "backend": "cborg",
            "models": {"chat": "lbl/cborg-chat"},
            "settings": {"F2W_BACKEND": "cborg"},
            "graphs": [{"path": "storage/kg/example.json", "sha256": "deadbeef"}],
        },
    )
    note_request(effective_question=message, graph_source="json", source_rag=False, use_live_tiled=True)
    note_precheck("_direct_download_request", False)
    note_precheck("_paper_reference_followup", True)
    note_decision(
        {
            "action": "answer",
            "agent": "retrieval",
            "reason": "evidence",
            "classification": "knowledge",
        }
    )
    record_retrieval(
        {
            "selected_hits": [
                {
                    "id": "n1",
                    "graph_id": "g1",
                    "graph_label": "Graph 1",
                    "score": 0.9,
                    "category": "Thing",
                }
            ],
            "sufficient": True,
            "no_evidence": False,
            "direct_evidence_count": 1,
            "missing_topics": [],
            "live_tiled": {"enabled": True},
            "hybrid_rag": None,
            "route_flags": {
                "_is_ops_layout_question": False,
                "_is_numeric_claim": False,
                "_is_conceptual_question": True,
                "is_experiment_identity_question": False,
            },
            "layout_ops_only_filter": False,
            "merged_context": message,
            "raw_judge_output": '{"sufficient": true}',
            "parsed_verdict": {"sufficient": True},
            "leeway_used": False,
            "leeway_system_prompt": None,
        }
    )
    record_llm_call(
        label="KG-RAG-judge",
        model="lbl/cborg-chat",
        messages=[{"role": "system", "content": "judge"}, {"role": "user", "content": message}],
        response='{"sufficient": true}',
        latency_ms=12,
    )
    finish_turn()

    detail, summary = _detail_and_summary(tmp_path, "turn-schema-1")
    assert set(DETAIL_KEYS) <= set(detail)
    assert detail["schema_version"] == 1
    assert set(ENV_KEYS) <= set(detail["env"])
    assert set(REQUEST_KEYS) <= set(detail["request"])
    assert set(ORCHESTRATION_KEYS) <= set(detail["orchestration"])
    assert set(PRECHECK_KEYS) <= set(detail["orchestration"]["prechecks"])
    assert set(RETRIEVAL_KEYS) <= set(detail["retrieval"])
    assert set(ROUTE_FLAG_KEYS) <= set(detail["retrieval"]["route_flags"])
    assert set(RESPONSE_KEYS) <= set(detail["response"])
    assert set(LLM_CALL_KEYS) <= set(detail["llm_calls"][0])
    assert detail["request"]["message"] == message
    assert detail["retrieval"]["merged_context"] == message
    assert detail["llm_calls"][0]["messages"][1]["content"] == message
    assert summary["question"] == message[:300]
    assert len(summary["question"]) == 300
    assert summary["classification"] == "knowledge"
    assert summary["action"] == "answer"
    assert summary["top_graph_ids"] == ["g1"]
    assert detail["parent_turn_id"] == "turn-parent"
    assert detail["orchestration"]["prechecks"]["_paper_reference_followup"] is True


def test_disabled_tracing_writes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("F2W_TRACE_ENABLED", raising=False)
    monkeypatch.setenv("F2W_TRACE_DIR", str(tmp_path))
    assert tracing_enabled() is False
    open_turn(client_turn_id="turn-off-1", session_id="session-off", request={"message": "silent"})
    record_llm_call(
        label="orchestrator-classify",
        model="m",
        messages=[{"role": "user", "content": "silent"}],
        response="{}",
        latency_ms=1,
    )
    finish_turn()
    assert list(tmp_path.rglob("*")) == []

    monkeypatch.setenv("F2W_TRACE_ENABLED", "0")
    assert tracing_enabled() is False
    open_turn(client_turn_id="turn-off-2", request={"message": "still silent"})
    finish_turn()
    assert list(tmp_path.rglob("*")) == []


def test_trace_endpoints_share_one_turn_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(api_mod, "RetrievalAgent", _SilentAgent)
    monkeypatch.setattr(api_mod, "DownloadAgent", _SilentAgent)
    monkeypatch.setattr(api_mod, "ExtractorAgent", _SilentAgent)
    app = api_mod.create_app(CoordinatorConfig(workdir=tmp_path / "session", max_rounds=1))
    turn_id = "turn-endpoint-1"
    open_turn(
        client_turn_id=turn_id,
        session_id="session-endpoint",
        request={"message": "endpoint question"},
    )
    finish_turn()

    client = TestClient(app)
    detail = client.get(f"/traces/{turn_id}")
    assert detail.status_code == 200
    assert detail.json()["turn_id"] == turn_id
    assert detail.json()["request"]["message"] == "endpoint question"

    missing = client.get("/traces/missing-turn")
    assert missing.status_code == 404

    ui = client.post(
        "/telemetry/ui",
        json={
            "events": [
                {
                    "type": "citation_click",
                    "turn_id": turn_id,
                    "ts": "2026-09-29T00:00:00+00:00",
                    "data": {"cite_type": "kg", "target": "n1"},
                }
            ]
        },
    )
    assert ui.status_code == 200
    assert ui.json()["written"] == 1
    events = list(tmp_path.glob("*/ui_events.jsonl"))
    assert len(events) == 1
    event = json.loads(events[0].read_text(encoding="utf-8").strip())
    assert event["turn_id"] == turn_id

    first = client.post(
        "/annotations",
        json={"turn_id": turn_id, "verdict": "fail", "failure_tags": ["citation"], "note": "wrong node"},
    )
    second = client.post(
        "/annotations",
        json={"turn_id": turn_id, "verdict": "pass", "failure_tags": [], "note": ""},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    labels = (tmp_path / "annotations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(labels) == 2
    assert json.loads(labels[0])["verdict"] == "fail"
    assert json.loads(labels[1])["turn_id"] == turn_id

    monkeypatch.setenv("F2W_TRACE_ENABLED", "0")
    disabled = client.post(
        "/telemetry/ui",
        json={"events": [{"type": "new_chat", "ts": "2026-09-29T00:00:00+00:00"}]},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["written"] == 0


class _SilentAgent:
    def __init__(self, *args, **kwargs):
        pass


def test_write_failure_does_not_fail_the_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    _enable(monkeypatch, blocked)
    open_turn(client_turn_id="turn-write-fail", request={"message": "still answers"})
    with caplog.at_level(logging.WARNING):
        finish_turn()
    assert any("Trace write failed" in record.message for record in caplog.records)
