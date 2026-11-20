"""End-to-end against a real Jaeger (skipped unless a throwaway one is named in the environment).

Start one and point the test at it (nothing here starts a container)::

    docker run -d --name f2w-otel-jaeger-test -p 127.0.0.1:16687:16686 -p 127.0.0.1:4319:4318 jaegertracing/jaeger:2.3.0
    F2W_JAEGER_TEST_OTLP=http://127.0.0.1:4319 F2W_JAEGER_TEST_UI=http://127.0.0.1:16687 \\
        python -m pytest tests/tracing/test_jaeger_live.py -q

It proves the OTLP/HTTP exporter, the endpoint settings and the span shapes (nesting, durations, error status)
against the real collector, which the in-memory tests cannot. Jaeger 2.3.0 still serves the v1 query API
(``/api/traces``); newer 2.x releases serve only ``/api/v3``; the test reads whichever exists. Set
``F2W_JAEGER_TEST_EVIDENCE`` to a file name to write a (secret-free) summary of what arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

import pytest

from app.modules import tracing
from app.modules.f2w_agent import trace as turn_trace
from app.modules.tracing import runtime

OTLP = os.environ.get("F2W_JAEGER_TEST_OTLP", "")
UI = os.environ.get("F2W_JAEGER_TEST_UI", "")
pytestmark = pytest.mark.skipif(not (OTLP and UI), reason="needs a throwaway Jaeger (F2W_JAEGER_TEST_OTLP/_UI)")

JsonDict = dict[str, object]


@dataclass
class Seen:
    """One span as Jaeger returned it, in a shape common to the v1 and v3 query APIs."""

    name: str
    span_id: str
    parent_id: str | None
    duration_us: float
    attributes: dict[str, object]
    error: bool
    start: int = 0


@dataclass
class SeenTrace:
    api: str
    spans: list[Seen] = field(default_factory=list)
    service: str = ""
    resource: dict[str, object] = field(default_factory=dict)


def fetch(path: str) -> JsonDict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=10) as response:  # nosec - loopback test collector
        data: JsonDict = json.loads(response.read())
        return data


def _otlp_value(value: JsonDict) -> object:
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    return int(str(value["intValue"])) if "intValue" in value else None


def trace_v1(trace_id: str) -> SeenTrace | None:
    for trace in fetch("/api/traces?service=f2w&limit=50&lookback=1h").get("data", []):  # type: ignore[attr-defined]
        if trace["traceID"] != trace_id:
            continue
        out = SeenTrace(api="v1")
        for span in trace["spans"]:
            tags = {t["key"]: t["value"] for t in span["tags"]}
            parent = next((r["spanID"] for r in span["references"] if r["refType"] == "CHILD_OF"), None)
            out.spans.append(Seen(span["operationName"], span["spanID"], parent, float(span["duration"]), tags,
                                  tags.get("error") is True, span["startTime"]))
        process = trace["processes"][trace["spans"][0]["processID"]]
        out.service = process["serviceName"]
        out.resource = {t["key"]: t["value"] for t in process["tags"]}
        return out
    return None


def trace_v3(trace_id: str) -> SeenTrace | None:
    try:
        body = fetch(f"/api/v3/traces/{trace_id}")
    except urllib.error.HTTPError:
        return None
    out = SeenTrace(api="v3")
    for resource_spans in body["result"]["resourceSpans"]:  # type: ignore[index]
        resource = {a["key"]: _otlp_value(a["value"]) for a in resource_spans["resource"]["attributes"]}
        out.service, out.resource = str(resource["service.name"]), resource
        for scope in resource_spans["scopeSpans"]:
            for span in scope["spans"]:
                attrs = {a["key"]: _otlp_value(a["value"]) for a in span.get("attributes", [])}
                start, end = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                out.spans.append(Seen(span["name"], span["spanId"], span.get("parentSpanId") or None,
                                      (end - start) / 1000, attrs, span.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR"),
                                      start))
    return out or None


def find(trace_id: str, timeout: float = 20.0) -> SeenTrace:
    use_v1 = True
    try:
        fetch("/api/services")
    except urllib.error.HTTPError:
        use_v1 = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = trace_v1(trace_id) if use_v1 else trace_v3(trace_id)
        if found is not None and len(found.spans) >= 4:
            return found
        time.sleep(0.5)
    raise AssertionError("the trace never reached Jaeger")


def services() -> list[str]:
    try:
        return list(fetch("/api/services")["data"])  # type: ignore[call-overload]
    except urllib.error.HTTPError:
        return list(fetch("/api/v3/services")["services"])  # type: ignore[call-overload]


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
        assert trace_id
        turn_trace.record_llm_call(label="KG-RAG-judge", model="lbl/cborg-chat",
                                   messages=[{"role": "user", "content": "hi"}], response="ok", latency_ms=25.0)
        _off_domain("what is rsoxs", [], {})
        with pytest.raises(ZeroDivisionError):
            call_with_deadline(lambda: 1 / 0)
        time.sleep(0.02)
    assert runtime.flush(10.0)

    found = find(trace_id)
    by_name = {s.name: s for s in found.spans}
    judge, off, failing, root = (by_name["llm.call KG-RAG-judge"], by_name["f2w_agent.retrieval_agent._off_domain"],
                                 by_name["cborg_http.call_with_deadline"], by_name["POST /chat"])
    assert len(found.spans) == 4 and root.parent_id is None
    assert {judge.parent_id, off.parent_id, failing.parent_id} == {root.span_id}
    assert 20_000 <= judge.duration_us <= 40_000                    # the 25 ms model call, in microseconds
    assert root.duration_us >= judge.duration_us + off.duration_us
    assert failing.error and not judge.error and not root.error
    assert failing.attributes["error.type"] == "ZeroDivisionError"
    assert judge.attributes["gen_ai.request.model"] == "lbl/cborg-chat"
    assert root.attributes["f2w.job_id"] == marker
    assert found.service == "f2w"
    assert found.resource["f2w.profile"] == "cluster" and found.resource["f2w.process"] == "api"
    assert str(found.resource["f2w.code_commit"]).startswith("0123456789abcdef")
    assert "f2w" in services()

    note = os.environ.get("F2W_JAEGER_TEST_EVIDENCE")
    if note:
        by_id = {s.span_id: s for s in found.spans}
        lines = [f"{s.name}: {s.duration_us / 1000:.2f} ms, parent "
                 f"{'(root)' if s.parent_id is None else by_id[s.parent_id].name}, error={s.error}"
                 for s in sorted(found.spans, key=lambda s: s.start)]
        Path(note).write_text(json.dumps({
            "query_api": found.api, "services": services(), "spans": lines,
            "resource_attribute_names": sorted(found.resource)}, indent=2), encoding="utf-8")
