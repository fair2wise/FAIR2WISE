"""The request-span middleware: semantic-convention attributes, probe exclusion, no query strings."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.modules import tracing
from app.modules.tracing import asgi

from .conftest import SECRET, SENTINEL, every_string


def build() -> Starlette:
    async def job(request: Request) -> JSONResponse:
        with tracing.span("inner.work"):
            return JSONResponse({"id": request.path_params["job_id"]})

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def boom(request: Request) -> PlainTextResponse:
        raise RuntimeError(f"leaks {SENTINEL}")

    async def unavailable(request: Request) -> PlainTextResponse:
        return PlainTextResponse("no", status_code=503)

    app = Starlette(routes=[
        Route("/v1/jobs/{job_id}", job), Route("/livez", ok), Route("/readyz", ok), Route("/health", ok),
        Route("/fair2wise/api/health", ok), Route("/boom", boom), Route("/down", unavailable)])
    assert asgi.install(app) is True
    return app


@pytest.fixture
def traced_app(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """The app and its exporter; the httpx client hook is off so the TestClient's own calls are not spans."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.modules.tracing import runtime

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("F2W_OTEL_TRACE_HTTP", "0")
    memory = InMemorySpanExporter()
    assert runtime.init(process="test", exporter=memory)
    return build(), memory


def test_request_span_attributes_and_nesting(traced_app) -> None:  # noqa: ANN001
    app, exporter = traced_app
    with TestClient(app) as client:
        response = client.get(f"/v1/jobs/abc123?token={SECRET}&q={SENTINEL}")
    assert response.status_code == 200
    inner, server = exporter.get_finished_spans()
    assert server.name == "GET /v1/jobs/{job_id}" and inner.name == "inner.work"
    assert inner.parent.span_id == server.context.span_id
    attrs = server.attributes
    assert attrs["http.request.method"] == "GET" and attrs["http.response.status_code"] == 200
    assert attrs["http.route"] == "/v1/jobs/{job_id}" and attrs["url.path"] == "/v1/jobs/{job_id}"
    assert attrs["f2w.http.duration_ms"] >= 0
    assert server.kind.name == "SERVER"
    blob = " ".join(every_string([inner, server]))
    assert SENTINEL not in blob and SECRET not in blob and "token" not in blob and "abc123" not in blob
    assert response.headers["x-f2w-trace-id"] == format(server.context.trace_id, "032x")


@pytest.mark.parametrize("path", ["/livez", "/readyz", "/health", "/fair2wise/api/health", "/health/"])
def test_probes_are_not_traced(traced_app, path: str) -> None:  # noqa: ANN001
    app, exporter = traced_app
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code in (200, 307, 404)
    assert exporter.get_finished_spans() == ()
    assert "x-f2w-trace-id" not in response.headers


def test_exclusion_boundaries() -> None:
    assert asgi.excluded("/livez") and asgi.excluded("/fair2wise/api/readyz") and asgi.excluded("/x/health")
    assert not asgi.excluded("/v1/health_report") and not asgi.excluded("/unhealth") and not asgi.excluded("/v1/jobs")


def test_server_errors_and_exceptions_mark_the_span(traced_app) -> None:  # noqa: ANN001
    app, exporter = traced_app
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/down").status_code == 503
        assert client.get("/boom").status_code == 500
    down, boom = exporter.get_finished_spans()
    assert down.status.status_code.name == "ERROR" and down.attributes["http.response.status_code"] == 503
    assert boom.status.status_code.name == "ERROR" and boom.attributes["error.type"] == "RuntimeError"
    assert SENTINEL not in " ".join(every_string([down, boom]))


def test_incoming_traceparent_becomes_the_parent(traced_app) -> None:  # noqa: ANN001
    app, exporter = traced_app
    tid, sid = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    with TestClient(app) as client:
        client.get("/v1/jobs/x", headers={"traceparent": f"00-{tid}-{sid}-01"})
        client.get("/v1/jobs/x", headers={"traceparent": "garbage"})
    server_ok, server_bad = [s for s in exporter.get_finished_spans() if s.name.startswith("GET")]
    assert format(server_ok.context.trace_id, "032x") == tid and format(server_ok.parent.span_id, "016x") == sid
    assert server_bad.parent is None


def test_disabled_installs_nothing_and_passes_requests_through() -> None:
    app = Starlette(routes=[Route("/v1/jobs/{job_id}", lambda r: PlainTextResponse("x"))])
    assert asgi.install(app) is False
    assert app.user_middleware == []
    app.add_middleware(asgi.TracingMiddleware)  # even when added by hand it is inert while tracing is off
    with TestClient(app) as client:
        response = client.get("/v1/jobs/1")
    assert response.status_code == 200 and "x-f2w-trace-id" not in response.headers
