"""``/livez`` and ``/readyz`` through the real app factory: unauthenticated, no secret, no route policy."""
from __future__ import annotations

import json
import os

import pytest

from app.api.auth import undeclared_routes


def _status(tmp_path, *, state="running", pid=None):
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({"profile": "cluster", "phase": "ready", "services": {
        "worker": {"state": state, "pid": os.getpid() if pid is None else pid, "required": True}}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("who", ["local", "anon"])
def test_livez_answers_without_a_session_and_calls_no_dependency(local, who):
    response = local.client(who).get("/livez")
    assert response.status_code == 200 and response.json() == {"status": "live"}
    assert response.headers["cache-control"] == "no-store"
    assert local.port.calls == 0  # no identity call


def test_livez_and_readyz_are_open_on_als_without_sign_in(als, monkeypatch, tmp_path):
    monkeypatch.setenv("F2W_SUPERVISOR_STATUS", str(_status(tmp_path)))
    monkeypatch.delenv("F2W_TILED_MODE", raising=False)
    anon = als.client("anon")
    assert anon.get("/livez").status_code == 200
    assert anon.get("/readyz").status_code == 200
    assert anon.get("/v1/jobs").status_code in (401, 403)  # the probes are not a hole in the policy


def test_probe_routes_are_not_app_routes(local):
    paths = {r.path for r in local.app.routes}
    assert undeclared_routes(local.app) == []
    assert "/livez" not in paths and "/readyz" not in paths


def test_readyz_reports_ready_then_not_ready_without_leaking_details(local, monkeypatch, tmp_path):
    monkeypatch.setenv("F2W_SUPERVISOR_STATUS", str(_status(tmp_path)))
    monkeypatch.delenv("F2W_TILED_MODE", raising=False)
    ready = local.client().get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"state": "ok", "worker": "ok", "tiled": "skipped"}}
    monkeypatch.setenv("F2W_SUPERVISOR_STATUS", str(_status(tmp_path, state="failed")))
    down = local.client().get("/readyz")
    assert down.status_code == 503 and down.json()["status"] == "not_ready"
    assert down.json()["checks"]["worker"] == "fail"
    assert str(tmp_path) not in down.text and "pid" not in down.text
    head = local.client().head("/readyz")
    assert head.status_code == 503 and head.content == b""


def test_readyz_checks_are_selectable(local, monkeypatch, tmp_path):
    monkeypatch.setenv("F2W_SUPERVISOR_STATUS", str(_status(tmp_path, state="failed")))
    monkeypatch.setenv("F2W_READYZ_CHECKS", "state")
    assert local.client().get("/readyz").json() == {"status": "ready", "checks": {"state": "ok"}}
    monkeypatch.setenv("F2W_READYZ_CHECKS", "state,nonsense")
    assert local.client().get("/readyz").status_code == 503  # an unknown check never passes silently
