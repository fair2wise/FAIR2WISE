"""``app.modules.deployment.front`` (``F2W_GATEWAY=off``): the same routing table as ``docker/nginx.conf``."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
import pytest
from starlette.testclient import TestClient

from app.modules.deployment.front import FRONT_ENV, FrontApp, front_enabled, ui_root_from_env
from app.modules.deployment.gateway import PublicLayout

PREFIX = "/fair2wise"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    ui = tmp_path / "www" / "fair2wise"
    (ui / "assets").mkdir(parents=True)
    (ui / "docs" / "guide").mkdir(parents=True)
    (ui / "index.html").write_text("<html>ui</html>", encoding="utf-8")
    (ui / "f2w-build.json").write_text("{}", encoding="utf-8")
    (ui / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (ui / "assets" / "app.abc123.js").write_text("console.log(1)", encoding="utf-8")
    (ui / "docs" / "index.html").write_text("<html>docs</html>", encoding="utf-8")
    (ui / "docs" / "guide" / "index.html").write_text("<html>guide</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the UI root", encoding="utf-8")
    return tmp_path / "www"


@pytest.fixture
def seen() -> list[dict]:
    return []


@pytest.fixture
def client(root: Path, seen: list[dict]) -> TestClient:
    api = FastAPI()

    @api.get("/health")
    async def health(request: Request) -> dict:
        seen.append({"path": request.scope["path"], "raw": request.scope["raw_path"].decode(),
                     "root_path": request.scope["root_path"],
                     "xfh": request.headers.get("x-forwarded-host"), "xfp": request.headers.get("x-forwarded-proto"),
                     "xfprefix": request.headers.get("x-forwarded-prefix")})
        return {"status": "ok"}

    @api.get("/graph/neighborhood/{node_id:path}")
    async def node(node_id: str, request: Request) -> dict:
        return {"node": node_id, "raw": request.scope["raw_path"].decode()}

    @api.get("/model-api/ping")
    async def ping(request: Request) -> dict:
        return {"pong": True, "root_path": request.scope["root_path"]}

    @api.post("/chat")
    async def chat() -> dict:
        return {"ok": True}

    return TestClient(FrontApp(api, PublicLayout(PREFIX), root), follow_redirects=False)


def test_requires_a_prefix(tmp_path):
    with pytest.raises(ValueError):
        FrontApp(FastAPI(), PublicLayout(""), tmp_path)


def test_env_helpers(tmp_path):
    assert front_enabled({FRONT_ENV: "1"}) and not front_enabled({}) and not front_enabled({FRONT_ENV: "0"})
    assert ui_root_from_env({"F2W_UI_ROOT": str(tmp_path)}) == tmp_path
    assert str(ui_root_from_env({})) == "/usr/share/nginx/html"


def test_health_endpoints_and_redirects(client):
    for path in ("/healthz", f"{PREFIX}/healthz"):
        response = client.get(path)
        assert response.status_code == 200 and response.text == "ok\n"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/").status_code == 302 and client.get("/").headers["location"] == f"{PREFIX}/"
    moved = client.get(PREFIX + "?a=1")
    assert moved.status_code == 301 and moved.headers["location"] == f"{PREFIX}/?a=1"
    docs = client.get(f"{PREFIX}/docs?x=y")
    assert docs.status_code == 301 and docs.headers["location"] == f"{PREFIX}/docs/?x=y"


def test_api_prefix_is_removed_once_and_root_path_set(client, seen):
    assert client.get(f"{PREFIX}/api/health").json() == {"status": "ok"}
    assert seen[-1]["path"] == "/health" and seen[-1]["root_path"] == f"{PREFIX}/api"
    assert seen[-1]["raw"] == "/health"
    assert len(seen) == 1


def test_encoded_node_ids_keep_their_raw_bytes(client):
    body = client.get(f"{PREFIX}/api/graph/neighborhood/a%2Fb%20c").json()
    assert body == {"node": "a/b c", "raw": "/graph/neighborhood/a%2Fb%20c"}


def test_model_api_keeps_its_mount_segment(client):
    assert client.get(f"{PREFIX}/model-api/ping").json() == {"pong": True, "root_path": f"{PREFIX}/api"}
    assert client.get(f"{PREFIX}/model-api").status_code == 404
    assert client.get(f"{PREFIX}/api").status_code == 404
    assert client.get(f"{PREFIX}/api/model-api/ping").status_code == 404  # one public path for the model API


def test_state_changing_methods_reach_the_api(client):
    assert client.post(f"{PREFIX}/api/chat").json() == {"ok": True}


def test_forwarded_headers_are_sanitised_like_the_gateway(client, seen):
    client.get(f"{PREFIX}/api/health", headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Prefix": "/x",
                                                 "X-Forwarded-Proto": "javascript"})
    assert seen[-1]["xfh"] is None and seen[-1]["xfprefix"] is None
    assert seen[-1]["xfp"] == "http"  # not a literal http/https: the connection's scheme
    client.get(f"{PREFIX}/api/health", headers={"X-Forwarded-Proto": "https"})
    assert seen[-1]["xfp"] == "https"


def test_ui_and_assets(client):
    index = client.get(f"{PREFIX}/")
    assert index.status_code == 200 and index.text == "<html>ui</html>"
    assert index.headers["cache-control"] == "no-cache"
    asset = client.get(f"{PREFIX}/assets/app.abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert client.get(f"{PREFIX}/assets/missing.js").status_code == 404  # never index.html
    assert client.get(f"{PREFIX}/favicon.svg").status_code == 200
    assert client.get(f"{PREFIX}/missing.png").status_code == 404
    deep = client.get(f"{PREFIX}/graph/some/view")
    assert deep.status_code == 200 and deep.text == "<html>ui</html>"  # SPA fallback
    assert client.get(f"{PREFIX}/index.html").headers["cache-control"] == "no-cache"
    head = client.head(f"{PREFIX}/")
    assert head.status_code == 200 and head.content == b""


def test_docs_site(client):
    assert client.get(f"{PREFIX}/docs/").text == "<html>docs</html>"
    assert client.get(f"{PREFIX}/docs/guide/").text == "<html>guide</html>"
    moved = client.get(f"{PREFIX}/docs/guide")
    assert moved.status_code == 301 and moved.headers["location"] == f"{PREFIX}/docs/guide/"
    assert client.get(f"{PREFIX}/docs/nope/").status_code == 404


def test_nothing_outside_the_prefix_and_no_traversal(client, root):
    assert client.get("/other").status_code == 404
    assert client.get("//fair2wise/api/health").status_code == 404
    for path in (f"{PREFIX}/docs/../../secret.txt", f"{PREFIX}/..%2fsecret.txt",
                 f"{PREFIX}/docs/%2e%2e/%2e%2e/secret.txt", f"{PREFIX}/docs/..%5c..%5csecret.txt"):
        response = client.get(path)
        assert "outside the UI root" not in response.text, path
        assert response.status_code in (200, 404), path  # 200 only for the SPA fallback page
    assert client.post(f"{PREFIX}/index.html").status_code == 405


def test_symlink_out_of_the_root_is_not_served(client, root):
    link = root / "fair2wise" / "leak.txt"
    link.symlink_to(root.parent / "secret.txt")
    assert client.get(f"{PREFIX}/leak.txt").status_code == 404


def test_lifespan_reaches_the_api(root):
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        events.append("startup")
        yield
        events.append("shutdown")

    with TestClient(FrontApp(FastAPI(lifespan=lifespan), PublicLayout(PREFIX), root)):
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]
