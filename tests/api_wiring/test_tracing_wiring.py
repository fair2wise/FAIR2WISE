"""The real API app: the tracing middleware is added only when tracing is on, and then it is outermost."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "security", REPO / "tests" / "fixtures", REPO / "tests" / "contracts" / "legacy_ui", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402

from app.api.main import create_api_app  # noqa: E402
from app.modules.security.authorization import DeploymentSettings  # noqa: E402
from app.modules.tracing import runtime  # noqa: E402


def stack(app: object) -> list[str]:
    return [m.cls.__name__ for m in app.user_middleware]  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _tracing_off_afterwards():
    yield
    runtime.reset()


def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str):
    ctx = H.install(monkeypatch, tmp_path / f"ws-{name}", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    return create_api_app(make_cfg(ctx), deployment=DeploymentSettings(profile="local", writer_identity="f2w-writer"),
                          operations_root=tmp_path / f"o-{name}")


def test_default_stack_is_unchanged(tmp_path, monkeypatch):
    for var in ("F2W_OTEL_ENABLED", "OTEL_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    off = stack(build(tmp_path, monkeypatch, "off"))
    assert "TracingMiddleware" not in off and off[0] == "ProbeMiddleware"


def test_tracing_adds_one_outermost_middleware_and_probes_stay_untraced(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("F2W_OTEL_TRACE_HTTP", "0")
    memory = InMemorySpanExporter()
    assert runtime.init(process="api", exporter=memory)
    app = build(tmp_path, monkeypatch, "on")
    on = stack(app)
    assert on.count("TracingMiddleware") == 1 and on[0] == "TracingMiddleware"
    with TestClient(app) as client:
        client.get("/livez")
        assert memory.get_finished_spans() == ()
        response = client.get("/health")  # the legacy UI health route is excluded too
        assert response.status_code == 200 and memory.get_finished_spans() == ()
        client.get("/v1/status")
    names = [s.name for s in memory.get_finished_spans()]
    assert names and all(n.startswith("GET") for n in names)
