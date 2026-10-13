"""Shared fixtures for the T18B wiring tests: the real legacy UI app on a simulated profile.

Reuses the T18A harness (fake models, providers, Splash, Tiled lookup; sockets blocked;
refuses to run next to a ``.env``) and the T25B fake Tiled identity port. Nothing here
contacts a network, a model or a Tiled server.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "security"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from security_fakes import (  # noqa: E402
    STAGING, FakeClock, FakeTiledIdentityPort, FakeUser, ForbiddenIdentityPort, InMemoryCredentialStore,
    admin_user, local_settings, plain_user, staging_settings)

from app.api.auth import SESSION_COOKIE  # noqa: E402
from app.modules.security.tiled_identity import TiledCredential  # noqa: E402

FORGED_HEADERS = {
    "X-Local-Trusted": "1", "X-F2W-Mode": "local_trusted", "X-F2W-Authorization-Mode": "local_trusted",
    "X-F2W-Is-Admin": "true", "X-F2W-Profile": "local", "X-Tiled-Roles": "admin", "X-Forwarded-User": "admin",
    "X-Remote-User": "admin", "Authorization": "Bearer forged-admin", "Host": "127.0.0.1",
}
UPLOAD = {"filename": "wiring_upload", "graph": {
    "things": [{"id": "matkg:U1", "name": "Uploaded node", "category": "Thing"}], "associations": []}}


def other_user() -> FakeUser:
    """A second ordinary ALS user."""
    return FakeUser("uuid-user2", roles=("user",), identity_ids=("carol",))


def make_cfg(ctx: "H.Ctx", kg_mode: str = "splash"):
    """The launcher's configuration (as ``_legacy_ui_harness.make_ui_app``)."""
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    return CoordinatorConfig(
        backend="cborg", model="lbl/cborg-chat", graph=str(Path("storage/kg") / H.GRAPH_FILENAME),
        kg_mode=kg_mode, workdir=ctx.workspace / "run", max_rounds=3, workflow_mode="agentic",
        extraction_mode="targeted", targeted_max_pages=6, download_delay_seconds=0.0,
        validate_downloads=False, workers=1,
    )


@dataclass
class Deployment:
    """One app instance plus the principals that can call it."""

    app: Any
    ctx: "H.Ctx"
    port: Any
    store: InMemoryCredentialStore = field(default_factory=InMemoryCredentialStore)
    clients: Dict[str, TestClient] = field(default_factory=dict)

    def client(self, who: str, headers: Optional[Dict[str, str]] = None) -> TestClient:
        """``anon``, ``user``, ``user2`` or ``admin`` (cookie from the server-side store)."""
        key = who + ("+forged" if headers else "")
        if key not in self.clients:
            cookies = {SESSION_COOKIE: f"cookie-{who}"} if who != "anon" else None
            self.clients[key] = TestClient(self.app, cookies=cookies, headers=headers,
                                           raise_server_exceptions=False)
        return self.clients[key]

    @property
    def workdir(self) -> Path:
        return self.ctx.workspace / "run"


def build_als(monkeypatch, tmp_path, *, settings=None, kg_mode: str = "splash") -> Deployment:
    """The legacy UI app on ``:als-dev`` with a fake identity port and three signed-in principals."""
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    # T03: the langgraph read path is local-only, so ALS apps run with the default runtime here.
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    port = FakeTiledIdentityPort(tiled_target=STAGING)
    store = InMemoryCredentialStore()
    for who, user in (("user", plain_user()), ("user2", other_user()), ("admin", admin_user())):
        port.tokens[f"token-{who}"] = user
        store.sessions[f"cookie-{who}"] = TiledCredential(f"token-{who}", tiled_target=STAGING)
    app = api_mod.create_app(make_cfg(ctx, kg_mode), deployment=settings or staging_settings(),
                             identity_port=port, credential_store=store, clock=FakeClock())
    return Deployment(app=app, ctx=ctx, port=port, store=store)


def build_local(monkeypatch, tmp_path, *, kg_mode: str = "splash") -> Deployment:
    """The legacy UI app on ``:local`` with an identity port that fails the test if called."""
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    port = ForbiddenIdentityPort()
    app = api_mod.create_app(make_cfg(ctx, kg_mode), deployment=local_settings(), identity_port=port)
    return Deployment(app=app, ctx=ctx, port=port)


@pytest.fixture
def als(monkeypatch, tmp_path) -> Deployment:
    return build_als(monkeypatch, tmp_path)


@pytest.fixture
def local(monkeypatch, tmp_path) -> Deployment:
    return build_local(monkeypatch, tmp_path)
