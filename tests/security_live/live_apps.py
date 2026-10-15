"""The legacy UI app on an ALS profile bound to a disposable Tiled, with real sign-in (T25C).

``create_app`` runs with ``DeploymentSettings.for_disposable_test_target``, the real
:class:`~app.modules.security.tiled_identity.TiledWhoamiAdapter` and the real
:class:`~app.api.sessions.TiledSessionStore`. Models, providers and Splash are the T18A
fakes; the only reachable network is 127.0.0.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi.testclient import TestClient

from live_support import CountingPort, OffsetClock, loopback_only

import _legacy_ui_harness as H
from _wiring_support import make_cfg

from app.api.sessions import TiledSessionStore, als_identity_wiring
from app.modules.security.authorization import DeploymentSettings

REPO = Path(__file__).resolve().parents[2]
BASE_URL = "https://testserver"


@dataclass
class LiveAls:
    """One ALS app bound to a disposable Tiled, plus signed-in browser clients."""

    app: Any
    ctx: Any
    inst: Any
    settings: DeploymentSettings
    port: CountingPort
    store: TiledSessionStore
    clock: OffsetClock
    clients: Dict[str, TestClient] = field(default_factory=dict)

    def browser(self, headers: Optional[Dict[str, str]] = None) -> TestClient:
        return TestClient(self.app, base_url=BASE_URL, headers=headers, raise_server_exceptions=False)

    def sign_in(self, user: str, headers: Optional[Dict[str, str]] = None) -> TestClient:
        client = self.browser(headers)
        resp = client.post("/auth/sign-in", json={"provider": self.inst.provider, "username": user,
                                                  "password": self.inst.passwords[user]})
        assert resp.status_code == 200, resp.text
        return client

    def client(self, who: str) -> TestClient:
        if who not in self.clients:
            self.clients[who] = self.browser() if who == "anon" else self.sign_in(who)
        return self.clients[who]

    @property
    def workdir(self) -> Path:
        return self.ctx.workspace / "run"


def build_live_als(monkeypatch, tmp_path, inst, *, profile: str = "als-dev", writer: Any = None) -> LiveAls:
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    loopback_only(monkeypatch)  # the harness blocks every socket; re-open only 127.0.0.1
    settings = DeploymentSettings.for_disposable_test_target(profile, tiled_target=inst.url,
                                                             writer_identity=(writer or inst.writer).uuid)
    clock = OffsetClock()
    http = httpx.Client(follow_redirects=False)
    port, store = als_identity_wiring(settings, http, cookie_path="/", clock=clock)
    counting = CountingPort(port)
    app = api_mod.create_app(make_cfg(ctx), deployment=settings, identity_port=counting, credential_store=store,
                             clock=clock)
    return LiveAls(app=app, ctx=ctx, inst=inst, settings=settings, port=counting, store=store, clock=clock)
