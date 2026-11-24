"""Content-disclosure rule: no prompt text, key or file content in any span, event, name or resource.

``docs/adr/content-disclosure.md``: spans may carry ids, names, timings, counts, model names, status and
sizes. These tests push sentinel prompt text and a fake API key through every path that records spans.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.modules import tracing
from app.modules.f2w_agent import trace as turn_trace
from app.modules.tracing import httpx_hooks

from .conftest import SECRET, SENTINEL, every_string


@pytest.fixture
def secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CBORG_API_KEY", SECRET)
    monkeypatch.setenv("F2W_TRACE_ENABLED", "0")


def test_fake_chat_turn_with_model_calls_leaks_nothing(exporter, secret_env) -> None:  # noqa: ANN001
    messages = [{"role": "system", "content": f"system {SENTINEL} {SECRET}"}, {"role": "user", "content": SENTINEL}]
    with tracing.span("POST /chat") as request_span:
        collector = turn_trace.open_turn(request={"message": SENTINEL})
        turn_trace.record_llm_call(label="KG-RAG-judge", model="lbl/cborg-chat", messages=messages,
                                   response=f"answer {SENTINEL}", latency_ms=42.5, timeout=60.0)
        turn_trace.record_llm_call(label="KG-RAG-leeway", model="lbl/cborg-chat", messages=messages, response=None,
                                   latency_ms=7.0, error=RuntimeError(f"upstream said {SENTINEL} {SECRET}"))
        turn_trace.finish_turn()
        trace_id = request_span.trace_id
    spans = exporter.get_finished_spans()
    blob = " ".join(every_string(spans))
    assert SENTINEL not in blob and SECRET not in blob and "sk-test" not in blob
    judge = next(s for s in spans if s.name == "llm.call KG-RAG-judge")
    assert judge.attributes["gen_ai.request.model"] == "lbl/cborg-chat"
    assert judge.attributes["f2w.llm.status"] == "ok" and judge.attributes["f2w.llm.latency_ms"] == 42.5
    assert judge.attributes["f2w.llm.prompt_chars"] == sum(len(m["content"]) for m in messages)
    assert judge.attributes["f2w.llm.response_chars"] == len(f"answer {SENTINEL}")
    assert judge.parent is not None  # nested under the request span
    assert (judge.end_time - judge.start_time) / 1e6 == pytest.approx(42.5, abs=1.0)
    leeway = next(s for s in spans if s.name == "llm.call KG-RAG-leeway")
    assert leeway.status.status_code.name == "ERROR" and leeway.status.description == "RuntimeError"
    assert collector.data["otel_trace_id"] == trace_id  # the JSON turn record links to Jaeger


def test_turn_record_has_no_trace_id_when_tracing_is_off(secret_env) -> None:  # noqa: ANN001
    collector = turn_trace.open_turn(request={"message": "hi"})
    assert "otel_trace_id" not in collector.data
    turn_trace.finish_turn()


def test_http_client_spans_record_host_and_status_only(exporter, secret_env) -> None:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if "ok" in request.url.path else 503, json={"echo": SENTINEL})

    url = f"https://api.example.org/v1/ok/{SENTINEL}?token={SECRET}&q={SENTINEL}"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.post(url, headers={"Authorization": f"Bearer {SECRET}"}, json={"prompt": SENTINEL})
        client.get("https://api.example.org/v1/down?x=1")
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["HTTP POST", "HTTP GET"]
    ok, down = spans
    assert ok.attributes["server.address"] == "api.example.org" and ok.attributes["http.response.status_code"] == 200
    assert ok.attributes["server.port"] == 443 and ok.attributes["url.scheme"] == "https"
    assert down.attributes["http.response.status_code"] == 503 and down.status.status_code.name == "ERROR"
    blob = " ".join(every_string(spans))
    assert SENTINEL not in blob and SECRET not in blob and "token=" not in blob and "q=" not in blob


def test_http_errors_record_the_type_not_the_message(exporter, secret_env) -> None:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} {SECRET}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.ConnectError):
        client.get(f"https://h.example/{SENTINEL}")
    span = exporter.get_finished_spans()[0]
    assert span.attributes["error.type"] == "ConnectError"
    assert SENTINEL not in " ".join(every_string([span])) and SECRET not in " ".join(every_string([span]))


def test_async_http_client_is_traced(exporter) -> None:  # noqa: ANN001
    async def run() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(204))) as client:
            return (await client.get("http://tiled.local:8765/healthz?k=v")).status_code

    assert asyncio.run(run()) == 204
    span = exporter.get_finished_spans()[0]
    assert span.attributes["server.address"] == "tiled.local" and span.attributes["server.port"] == 8765


def test_http_hook_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.modules.tracing import runtime

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("F2W_OTEL_TRACE_HTTP", "0")
    assert runtime.init(exporter=InMemorySpanExporter())
    assert httpx_hooks.install() is True  # it was not installed by init; installing now proves the switch worked
    httpx_hooks.uninstall()


def test_resource_carries_no_secret_even_with_secret_headers(monkeypatch: pytest.MonkeyPatch, secret_env) -> None:  # noqa: ANN001
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.modules.tracing import runtime

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization=Bearer {SECRET}")
    memory = InMemorySpanExporter()
    assert runtime.init(exporter=memory)
    with tracing.span("x"):
        pass
    assert SECRET not in " ".join(every_string(memory.get_finished_spans()))
