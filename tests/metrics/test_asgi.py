"""The HTTP middleware: route templates, unmatched requests, status classes, probes and no raw paths."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.modules.metrics import asgi

from .conftest import points

SECRET_ID = "123e4567-e89b-12d3-a456-426614174000"


def build() -> Starlette:
    async def job(request: Request) -> JSONResponse:
        return JSONResponse({"id": request.path_params["job_id"]})

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def boom(request: Request) -> PlainTextResponse:
        raise RuntimeError("secret detail")

    async def down(request: Request) -> PlainTextResponse:
        return PlainTextResponse("no", status_code=503)

    app = Starlette(routes=[Route("/v1/jobs/{job_id}", job), Route("/livez", ok), Route("/boom", boom),
                            Route("/down", down)])
    assert asgi.install(app) is True
    return app


def labels_of(reader: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return [dict(p.attributes) for p in points(reader, "f2w.http.requests")]


def test_install_is_a_no_op_when_metrics_are_off() -> None:
    app = Starlette()
    assert asgi.install(app) is False
    assert not app.user_middleware


def test_route_template_not_raw_path_and_query_never_recorded(reader: Any) -> None:  # noqa: ANN401
    with TestClient(build()) as client:
        assert client.get(f"/v1/jobs/{SECRET_ID}?token=hunter2&doi=10.1000/xyz").status_code == 200
        assert client.get("/v1/jobs/other-id").status_code == 200
    (point,) = points(reader, "f2w.http.requests")
    assert dict(point.attributes) == {"http_route": "/v1/jobs/{job_id}", "http_method": "GET", "status_class": "2xx"}
    assert point.value == 2
    blob = repr(points(reader, "f2w.http.requests")) + repr(points(reader, "f2w.http.request.duration"))
    for needle in (SECRET_ID, "hunter2", "10.1000", "other-id"):
        assert needle not in blob


def test_404_is_unmatched_and_status_classes(reader: Any) -> None:  # noqa: ANN401
    with TestClient(build(), raise_server_exceptions=False) as client:
        client.get("/nope/10.1000/xyz")
        client.post("/v1/jobs/x")  # 405: the path matches a route, the method does not
        client.get("/down")
        client.get("/boom")
    seen = {(d["http_route"], d["http_method"], d["status_class"]) for d in labels_of(reader)}
    assert ("unmatched", "GET", "4xx") in seen
    assert ("/down", "GET", "5xx") in seen and ("/boom", "GET", "5xx") in seen
    assert not any("nope" in d["http_route"] for d in labels_of(reader))


def test_probes_are_skipped(reader: Any) -> None:  # noqa: ANN401
    with TestClient(build()) as client:
        client.get("/livez")
    assert points(reader, "f2w.http.requests") == []


def test_duration_histogram_counts_each_request(reader: Any) -> None:  # noqa: ANN401
    with TestClient(build()) as client:
        client.get("/down")
        client.get("/down")
    (point,) = points(reader, "f2w.http.request.duration")
    assert point.count == 2 and point.sum >= 0
