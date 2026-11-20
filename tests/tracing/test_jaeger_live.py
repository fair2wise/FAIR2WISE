"""End-to-end against a real Jaeger (skipped unless a throwaway one is named in the environment).

Start one and point the test at it (nothing here starts a container)::

    docker run -d --name f2w-otel-jaeger-test -p 127.0.0.1:16687:16686 -p 127.0.0.1:4319:4318 jaegertracing/jaeger:2.3.0
    F2W_JAEGER_TEST_OTLP=http://127.0.0.1:4319 F2W_JAEGER_TEST_UI=http://127.0.0.1:16687 \\
        python -m pytest tests/tracing/test_jaeger_live.py -q

It proves the OTLP/HTTP exporter, the endpoint settings and the span shapes (nesting, durations, error status)
against the real collector, which the in-memory tests cannot. ``F2W_JAEGER_TEST_EVIDENCE`` names a file to
write the (secret-free) evidence note to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
import urllib.request

import pytest

from app.modules import tracing
from app.modules.f2w_agent import trace as turn_trace
from app.modules.tracing import runtime

OTLP = os.environ.get("F2W_JAEGER_TEST_OTLP", "")
UI = os.environ.get("F2W_JAEGER_TEST_UI", "")
pytestmark = pytest.mark.skipif(not (OTLP and UI), reason="needs a throwaway Jaeger (F2W_JAEGER_TEST_OTLP/_UI)")


def fetch(path: str) -> dict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=10) as response:  # nosec - loopback test collector
        return json.loads(response.read())


def tag(span: dict, key: str) -> object:
    return next((t["value"] for t in span["tags"] if t["key"] == key), None)


def test_real_jaeger_receives_nested_timed_spans_with_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", OTLP)  # the base URL: the exporter adds /v1/traces
    monkeypatch.setenv("OTEL_SERVICE_NAME", "f2w")
    monkeypatch.setenv("F2W_CODE_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("F2W_IMAGE_PROFILE", "cluster")
    monkeypatch.delenv("F2W_OTEL_TRACE_FUNCTIONS", raising=False)  # the built-in default list
    assert runtime.init(process="api")

    from app.modules.cborg_http import call_with_deadline
    from app.modules.f2w_agent.retrieval_agent import _off_domain

    marker = f"live-{int(time.time())}"
    with tracing.start("POST /chat", {"http.request.method": "POST", "f2w.job_id": marker}, kind="server") as request:
        trace_id = request.trace_id
        turn_trace.record_llm_call(label="judge", model="lbl/cborg-chat", messages=[{"role": "user", "content": "hi"}],
                                   response="ok", latency_ms=25.0)
        _off_domain("what is rsoxs", [], {})
        with pytest.raises(ZeroDivisionError):
            call_with_deadline(lambda: 1 / 0)
        time.sleep(0.02)
    assert runtime.flush(10.0)

    deadline = time.time() + 20
    found: dict | None = None
    while time.time() < deadline and found is None:
        for trace in fetch("/api/traces?service=f2w&limit=50&lookback=1h").get("data", []):
            if trace["traceID"] == trace_id:
                found = trace
        time.sleep(0.5)
    assert found is not None, "the trace never reached Jaeger"

    spans = {s["spanID"]: s for s in found["spans"]}
    by_name = {s["operationName"]: s for s in found["spans"]}
    assert {"POST /chat", "llm.call judge", "f2w_agent.retrieval_agent._off_domain",
            "cborg_http.call_with_deadline"} <= set(by_name), sorted(by_name)
    root = by_name["POST /chat"]

    def parent(span: dict) -> str | None:
        return next((r["spanID"] for r in span["references"] if r["refType"] == "CHILD_OF"), None)

    assert parent(root) is None
    for name in ("llm.call judge", "f2w_agent.retrieval_agent._off_domain", "cborg_http.call_with_deadline"):
        assert parent(by_name[name]) == root["spanID"], name
    assert 20_000 <= by_name["llm.call judge"]["duration"] <= 40_000          # the 25 ms model call, in microseconds
    assert root["duration"] >= by_name["llm.call judge"]["duration"]
    failing = by_name["cborg_http.call_with_deadline"]
    assert tag(failing, "error") is True and tag(failing, "otel.status_code") == "ERROR"
    assert tag(failing, "error.type") == "ZeroDivisionError"
    assert tag(by_name["llm.call judge"], "gen_ai.request.model") == "lbl/cborg-chat"
    assert tag(root, "f2w.job_id") == marker

    process = found["processes"][root["processID"]]
    assert process["serviceName"] == "f2w"
    ptags = {t["key"]: t["value"] for t in process["tags"]}
    assert ptags["f2w.profile"] == "cluster" and ptags["f2w.process"] == "api"
    assert ptags["f2w.code_commit"].startswith("0123456789abcdef")
    assert "f2w" in fetch("/api/services")["data"]
    ops = fetch("/api/services/f2w/operations")["data"]
    assert "POST /chat" in [o if isinstance(o, str) else o["name"] for o in ops]

    note = os.environ.get("F2W_JAEGER_TEST_EVIDENCE")
    if note:
        rows = sorted(found["spans"], key=lambda s: s["startTime"])
        lines = [f"- `{s['operationName']}`: {s['duration'] / 1000:.2f} ms, parent "
                 f"{'(root)' if parent(s) is None else spans[parent(s)]['operationName']}, "
                 f"error={tag(s, 'error') or False}" for s in rows]
        Path(note).write_text(json.dumps({
            "services": fetch("/api/services")["data"], "trace_id_prefix": trace_id[:8],
            "spans": len(rows), "lines": lines, "process_tags": sorted(ptags)}, indent=2), encoding="utf-8")
