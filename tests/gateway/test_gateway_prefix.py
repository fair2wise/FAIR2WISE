"""T24C: ``/fair2wise`` gateway, prefix contract and the existing UI API behind it.

The real ``docker/nginx.conf`` is rendered by ``app.modules.deployment.gateway`` and
executed by ``nginx_model`` (no nginx binary or image build is available here). Proxied
requests reach the real legacy UI app (T18A harness: fake models/providers, sockets
blocked, no ``.env``) through uvicorn's real ``ProxyHeadersMiddleware`` with the scope
uvicorn builds for ``root_path=/fair2wise/api``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

import _legacy_ui_harness as H
from nginx_model import GatewayASGI, NginxModel, Request

from app.modules.deployment import gateway as gw

REPO = Path(__file__).resolve().parents[2]
CONF = REPO / "docker" / "nginx.conf"
UPSTREAM = "127.0.0.1:8090"
API = "/fair2wise/api"
FIX = REPO / "tests" / "contracts" / "legacy_ui" / "fixtures"
CASES = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((FIX / "http").glob("*.json"))}
LAYOUT = gw.PublicLayout(gw.DEFAULT_PUBLIC_PREFIX)


def rendered(upstream: str = UPSTREAM) -> str:
    return gw.render_nginx_conf(CONF.read_text(encoding="utf-8"), upstream=upstream, layout=LAYOUT)


@pytest.fixture
def static_root(tmp_path):
    """A stand-in for the image's ``/usr/share/nginx/html``: UI build plus MkDocs site."""
    root = tmp_path / "html"
    ui = root / "fair2wise"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text('<!doctype html><script src="/fair2wise/assets/index-abc123.js"></script>')
    (ui / "assets" / "index-abc123.js").write_text("console.log('ui')")
    (ui / "wise_owl.svg").write_text("<svg/>")
    (ui / gw.BUILD_MANIFEST_NAME).write_text(json.dumps(LAYOUT.as_manifest()))
    (ui / "docs" / "agent-api").mkdir(parents=True)
    (ui / "docs" / "index.html").write_text("<h1>FAIR2WISE Documentation</h1>")
    (ui / "docs" / "agent-api" / "index.html").write_text('<a href="../retrieval/">Retrieval</a>')
    return root


@pytest.fixture
def model(static_root):
    return NginxModel(rendered(), root=static_root)


def route(model: NginxModel, uri: str, method: str = "GET", **headers: str):
    return model.handle(Request(method, uri, {k.replace("_", "-").lower(): v for k, v in headers.items()}))


# ------------------------------------------------------------------ prefix contract module


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("/", ""), ("/fair2wise", "/fair2wise"), ("/fair2wise/", "/fair2wise"),
    (" /a/b-c.d_e/ ", "/a/b-c.d_e"),
])
def test_normalize_prefix_accepts_rooted_plain_paths(raw, expected):
    assert gw.normalize_prefix(raw) == expected


@pytest.mark.parametrize("raw", ["fair2wise", "http://x/fair2wise", "/fair2wise/../x", "/a//b", "/a?b", "/a b",
                                 "//evil.example", "/./a", "/%2e%2e"])
def test_normalize_prefix_refuses_anything_else(raw):
    with pytest.raises(gw.PrefixConfigError):
        gw.normalize_prefix(raw)


def test_layout_derives_every_public_url_from_one_prefix():
    assert LAYOUT.as_manifest() == {"public_prefix": "/fair2wise", "ui_base": "/fair2wise/",
                                    "api_base": "/fair2wise/api", "docs_url": "/fair2wise/docs/"}
    assert LAYOUT.cookie_path == "/fair2wise" and LAYOUT.health_path == "/fair2wise/healthz"
    assert gw.uvicorn_options(LAYOUT) == {"root_path": "/fair2wise/api"}
    plain = gw.layout_from_env({})
    assert plain.ui_base == "/" and plain.api_root_path == "" and plain.docs_url == "/docs/"
    assert gw.uvicorn_options(plain) == {}  # unprefixed developer launch: unchanged
    assert gw.layout_from_env({gw.PREFIX_ENV: "/fair2wise"}) == LAYOUT


def test_cookie_path_matches_the_session_store_default():
    """AUTH-09: the ALS session cookie is scoped to the gateway prefix."""
    from app.api.sessions import DEFAULT_COOKIE_PATH

    assert DEFAULT_COOKIE_PATH == LAYOUT.cookie_path


@pytest.mark.parametrize("value", ["127.0.0.1:8090", "agent:8090", "[::1]:8090", "api.internal.example:1"])
def test_upstream_accepts_host_port(value):
    assert gw.validate_upstream(value) == value


@pytest.mark.parametrize("value", ["http://agent:8090", "agent", "agent:8090/x", "agent:0", "agent:70000",
                                   "user@agent:1", "[zz]:1", "-bad:1", ""])
def test_upstream_refuses_urls_and_bad_hosts(value):
    with pytest.raises(gw.PrefixConfigError):
        gw.validate_upstream(value)


def test_render_refuses_unknown_placeholders_and_a_prefix_the_gateway_does_not_route():
    template = CONF.read_text(encoding="utf-8")
    with pytest.raises(gw.PrefixConfigError, match="unknown gateway template placeholders"):
        gw.render_nginx_conf(template + "\n# ${OTHER}\n", layout=LAYOUT)
    with pytest.raises(gw.PrefixConfigError, match="must agree"):
        gw.render_nginx_conf(template, layout=gw.PublicLayout("/other"))
    with pytest.raises(gw.PrefixConfigError):
        gw.render_nginx_conf(template, layout=gw.PublicLayout(""))
    assert "${" not in rendered() and f"server {UPSTREAM};" in rendered()


def test_build_runtime_prefix_mismatch_is_refused_with_a_rebuild_hint(tmp_path):
    manifest = tmp_path / gw.BUILD_MANIFEST_NAME
    manifest.write_text(json.dumps(LAYOUT.as_manifest()))
    gw.check_build_agreement(gw.read_build_manifest(manifest), LAYOUT)
    assert gw.main(["check-build", "--manifest", str(manifest)]) == 0
    with pytest.raises(gw.PrefixConfigError, match="rebuild the UI"):
        gw.check_build_agreement(gw.read_build_manifest(manifest), gw.PublicLayout("/other"))
    unprefixed = gw.PublicLayout("").as_manifest()
    manifest.write_text(json.dumps(unprefixed))
    assert gw.main(["check-build", "--manifest", str(manifest), "--prefix", "/fair2wise"]) == 2


def test_cli_renders_the_gateway_and_the_image_mkdocs_config(tmp_path, capsys):
    out = tmp_path / "default.conf"
    assert gw.main(["render-nginx", "--template", str(CONF), "--upstream", "agent:8090", "--out", str(out)]) == 0
    assert "server agent:8090;" in out.read_text()
    assert gw.main(["render-nginx", "--template", str(CONF), "--upstream", "http://agent:8090"]) == 2
    docs = tmp_path / "mkdocs.image.yml"
    assert gw.main(["mkdocs-config", "--source", str(REPO / "mkdocs" / "mkdocs.yml"), "--out", str(docs)]) == 0
    text = docs.read_text()
    assert re.search(r'^site_url: ""$', text, re.M) and "github.io" not in text.split("repo_url")[0]


# ------------------------------------------------------------------ the gateway file itself


def test_exactly_one_prefix_stripping_boundary():
    """One proxy_pass, in the one API location; no rewrite, alias or second API route."""
    model = NginxModel(rendered())
    proxied = [loc for loc in model.locations if loc.directive("proxy_pass")]
    assert [(loc.modifier, loc.pattern) for loc in proxied] == [("^~", "/fair2wise/api/")]
    names = {d.name for loc in model.locations for d in loc.directives} | {d.name for d in model.server}
    assert not names & {"rewrite", "alias", "proxy_pass_request_headers", "uwsgi_pass", "fastcgi_pass"}
    assert sum(len(loc.directive("proxy_pass")) for loc in model.locations) == 1
    assert not any(loc.pattern.startswith(("/api", "/docs", "/assets")) for loc in model.locations)
    assert list(model.upstreams) == ["f2w_api"] and model.upstreams["f2w_api"] == [UPSTREAM]


def test_api_location_streams_without_buffering_and_keeps_long_streams_open():
    model = NginxModel(rendered())
    (api,) = [loc for loc in model.locations if loc.directive("proxy_pass")]
    value = {d.name: " ".join(d.args) for d in api.directives if d.name != "proxy_set_header"}
    assert value["proxy_buffering"] == "off" and value["proxy_cache"] == "off" and value["gzip"] == "off"
    assert value["proxy_http_version"] == "1.1" and value["proxy_redirect"] == "off"
    assert int(value["proxy_read_timeout"].rstrip("s")) >= 900
    assert int(value["proxy_send_timeout"].rstrip("s")) >= 900
    assert value["client_max_body_size"] == "128m"  # bounded, but large KG JSON uploads still pass


@pytest.mark.parametrize("uri,status,location", [
    ("/", 302, "/fair2wise/"),
    ("/fair2wise", 301, "/fair2wise/"),
    ("/fair2wise?session=s1&x=2", 301, "/fair2wise/?session=s1&x=2"),
    ("/fair2wise/docs", 301, "/fair2wise/docs/"),
    ("/fair2wise/docs/agent-api", 301, "/fair2wise/docs/agent-api/"),
    ("/fair2wise/docs/agent-api?q=1", 301, "/fair2wise/docs/agent-api/?q=1"),
])
def test_redirects_are_relative_and_keep_the_query(model, uri, status, location):
    result = route(model, uri, host="f2w.example:8443")
    assert (result.status, result.location) == (status, location)


@pytest.mark.parametrize("uri,served", [
    ("/fair2wise/", "fair2wise/index.html"),
    ("/fair2wise/index.html", "fair2wise/index.html"),
    ("/fair2wise/chat", "fair2wise/index.html"),
    ("/fair2wise/some/deep/link?session=abc", "fair2wise/index.html"),
    ("/fair2wise/assets/index-abc123.js", "fair2wise/assets/index-abc123.js"),
    ("/fair2wise/wise_owl.svg", "fair2wise/wise_owl.svg"),
    ("/fair2wise/f2w-build.json", "fair2wise/f2w-build.json"),
    ("/fair2wise/docs/", "fair2wise/docs/index.html"),
    ("/fair2wise/docs/agent-api/", "fair2wise/docs/agent-api/index.html"),
])
def test_ui_assets_docs_and_deep_links(model, static_root, uri, served):
    result = route(model, uri)
    assert result.kind == "static" and result.file == static_root / served, result.trace


@pytest.mark.parametrize("uri", [
    "/fair2wise/assets/missing.js",  # missing asset: 404, never index.html
    "/fair2wise/missing.png",
    "/fair2wise/docs/missing/",
    "/fair2wise/docs/missing.html",
    "/fair2wise/api",  # the API root is not a route and never falls into the SPA
    "/fair2wise/model-api/v1/chat/completions",  # not exposed by these images
    "/fair2wise/model-api",
    "/api/health",  # no unprefixed alias of the API
    "/docs/", "/index.html", "/assets/index-abc123.js", "/settings",  # nothing outside the prefix
])
def test_not_found_never_becomes_html(model, uri):
    result = route(model, uri)
    assert result.kind == "return" and result.status == 404, (uri, result.trace)


def test_index_is_not_cached_and_assets_are_immutable(model):
    assert route(model, "/fair2wise/").headers["Cache-Control"] == "no-cache"
    assert route(model, "/fair2wise/assets/index-abc123.js").headers["Cache-Control"] == "public, immutable"


@pytest.mark.parametrize("uri", ["/healthz", "/fair2wise/healthz"])
def test_gateway_health(model, uri):
    result = route(model, uri)
    assert (result.kind, result.status) == ("return", 200) and result.body.startswith("ok")


@pytest.mark.parametrize("uri,upstream_uri", [
    ("/fair2wise/api/health", "/health"),
    ("/fair2wise/api/chat/stream", "/chat/stream"),
    ("/fair2wise/api/graph/neighborhood/beamline%3AESAF-contract?hops=3",
     "/graph/neighborhood/beamline%3AESAF-contract?hops=3"),
    ("/fair2wise/api/graph/node/a%2Fb%20c", "/graph/node/a%2Fb%20c"),  # raw bytes, not re-decoded
    ("/fair2wise/api/graph//x", "/graph//x"),
    ("/fair2wise/api/", "/"),
    ("/fair2wise/api/fair2wise/api/health", "/fair2wise/api/health"),  # stripped once, not twice
    ("/fair2wise/api/docs", "/docs"),
    ("/fair2wise/api/openapi.json", "/openapi.json"),
    ("/fair2wise/api/auth/sign-in", "/auth/sign-in"),
])
def test_api_prefix_is_stripped_exactly_once_and_the_suffix_is_raw(model, uri, upstream_uri):
    result = route(model, uri, "POST")
    assert result.kind == "proxy" and result.upstream == UPSTREAM
    assert result.proxy_uri == upstream_uri


def test_a_non_literal_prefix_does_not_reach_the_api(model):
    """``//fair2wise/api`` normalizes into the API location but has no literal prefix to strip."""
    assert route(model, "//fair2wise/api/health").status == 404
    climbed = route(model, "/fair2wise/api/../index.html")  # dot segments leave the API namespace
    assert climbed.kind == "static" and climbed.location_block.pattern == "/fair2wise/index.html"


def test_forwarded_headers_from_the_client_cannot_add_a_prefix_or_host(model):
    result = route(model, "/fair2wise/api/health", host="f2w.example", x_forwarded_host="evil.example",
                   x_forwarded_prefix="/evil", x_forwarded_proto="javascript", x_forwarded_for="10.9.9.9")
    h = result.proxy_headers
    assert "x-forwarded-host" not in h and "x-forwarded-prefix" not in h
    assert h["host"] == "f2w.example" and h["x-forwarded-proto"] == "http"
    assert h["x-forwarded-for"] == "10.9.9.9, 203.0.113.7" and "connection" not in h
    tls = route(model, "/fair2wise/api/health", x_forwarded_proto="https").proxy_headers
    assert tls["x-forwarded-proto"] == "https"  # the edge terminates TLS


# ------------------------------------------------------------------ the real API behind the gateway


def _prefixed_client(app, static_root, **kwargs) -> TestClient:
    model = NginxModel(rendered(), root=static_root)
    gateway = GatewayASGI(model, {UPSTREAM: (app, gw.uvicorn_options(LAYOUT)["root_path"])})
    return TestClient(gateway, raise_server_exceptions=False, **kwargs)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return H.install(monkeypatch, tmp_path / "ws", REPO)


def _flow_steps():
    return [(name, flow["kg_mode"], step) for name, flow in H.flows().items() for step in flow["steps"]]


def test_every_recorded_legacy_flow_is_unchanged_under_the_prefix(harness, static_root):
    """The 19 UI routes: same status, content type, CORS headers, JSON and SSE frames as T18A."""
    checked = 0
    for name, flow in H.flows().items():
        app = H.make_ui_app(harness, kg_mode=flow["kg_mode"])
        client = _prefixed_client(app, static_root)
        for fid, method, path, kwargs, _notes in flow["steps"]:
            resp = H.perform(client, harness, method, API + path, kwargs)
            actual = H.response_record(resp, harness)
            expected = H.expected_response(fid, CASES[fid]["response"])
            assert actual["status_code"] == expected["status_code"], fid
            assert actual["content_type"] == expected["content_type"], fid
            assert actual.get("cors_headers") == expected.get("cors_headers"), fid
            if "sse_events" in expected:
                H.parse_sse(resp.content)  # strict LF framing, byte for byte
                assert actual["sse_events"] == expected["sse_events"], fid
            elif "json" in expected:
                assert actual["json"] == expected["json"], fid
            else:
                assert actual.get("text") == expected.get("text"), fid
            checked += 1
    assert checked == len([s for s in _flow_steps()]) and checked >= 40


def test_fastapi_docs_and_openapi_carry_the_prefix(harness, static_root):
    client = _prefixed_client(H.make_ui_app(harness), static_root)
    docs = client.get(f"{API}/docs")
    assert docs.status_code == 200 and "'/fair2wise/api/openapi.json'" in docs.text
    assert "/fair2wise/api/docs/oauth2-redirect" in docs.text
    redoc = client.get(f"{API}/redoc")
    assert redoc.status_code == 200 and "/fair2wise/api/openapi.json" in redoc.text
    assert client.get(f"{API}/docs/oauth2-redirect").status_code == 200
    spec = client.get(f"{API}/openapi.json").json()
    assert spec["servers"] == [{"url": "/fair2wise/api"}]
    assert "/chat/stream" in spec["paths"] and not any(p.startswith("/fair2wise") for p in spec["paths"])


def test_api_redirects_point_back_through_the_prefix(harness, static_root):
    client = _prefixed_client(H.make_ui_app(harness), static_root, base_url="http://f2w.example")
    resp = client.get(f"{API}/settings/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "http://f2w.example/fair2wise/api/settings"
    assert client.get(resp.headers["location"]).status_code == 200


def test_https_from_the_edge_reaches_generated_urls(harness, static_root):
    client = _prefixed_client(H.make_ui_app(harness), static_root, base_url="http://f2w.example")
    resp = client.get(f"{API}/settings/", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert resp.headers["location"] == "https://f2w.example/fair2wise/api/settings"


def test_encoded_node_ids_reach_the_route_unchanged(harness, static_root):
    app = H.make_ui_app(harness)
    prefixed = _prefixed_client(app, static_root)
    direct = TestClient(app, raise_server_exceptions=False)
    for path in ("/graph/node/matkg%3ATermA", "/graph/node/matkg:TermA", "/graph/node/matkg%3AMissing"):
        a, b = prefixed.get(API + path), direct.get(path)
        assert (a.status_code, a.json()) == (b.status_code, b.json()), path


def test_the_api_itself_never_strips_the_prefix(harness, static_root):
    """Only the gateway strips: a doubled prefix is a 404, never a second strip."""
    client = _prefixed_client(H.make_ui_app(harness), static_root)
    assert client.get(f"{API}/health").status_code == 200
    assert client.get(f"{API}{API}/health").status_code == 404


def test_unprefixed_direct_api_still_works(harness):
    """``:local`` / developer access straight to the API port keeps the rooted routes."""
    app = H.make_ui_app(harness)
    plain = TestClient(app, raise_server_exceptions=False)
    assert plain.get("/health").json()["status"] == "ok"
    assert "'/openapi.json'" in plain.get("/docs").text
    # The same app process behind root_path still answers a direct, unprefixed request.
    rooted = TestClient(app, root_path="/fair2wise/api", raise_server_exceptions=False)
    assert rooted.get("/health").status_code == 200


def test_static_ui_and_api_share_one_origin(harness, static_root):
    client = _prefixed_client(H.make_ui_app(harness), static_root, base_url="https://f2w.example")
    page = client.get("/fair2wise/some/deep/link")
    assert page.status_code == 200 and "/fair2wise/assets/index-abc123.js" in page.text
    assert client.get("/fair2wise/assets/index-abc123.js").status_code == 200
    assert client.get(f"{API}/health").json()["status"] == "ok"
    assert client.get("/fair2wise/assets/nope.js").status_code == 404


# ------------------------------------------------------------------ run_api wiring


@pytest.mark.parametrize("env,expected", [({}, {}), ({"F2W_PUBLIC_PREFIX": "/fair2wise"},
                                                       {"root_path": "/fair2wise/api"})])
def test_run_api_passes_root_path_only_when_prefixed(monkeypatch, env, expected):
    import uvicorn

    from app.modules.f2w_agent import api as api_mod

    monkeypatch.delenv("F2W_PUBLIC_PREFIX", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(api_mod, "create_app", lambda cfg, cors_origins=None: "app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    api_mod.run_api(object(), host="127.0.0.1", port=1, cors_origins=None)
    assert seen == {"app": "app", "host": "127.0.0.1", "port": 1, **expected}


def test_run_api_refuses_an_invalid_prefix(monkeypatch):
    from app.modules.f2w_agent import api as api_mod

    monkeypatch.setenv("F2W_PUBLIC_PREFIX", "fair2wise")
    monkeypatch.setattr(api_mod, "create_app", lambda cfg, cors_origins=None: pytest.fail("built anyway"))
    with pytest.raises(gw.PrefixConfigError):
        api_mod.run_api(object(), host="127.0.0.1", port=1)


# ------------------------------------------------------------------ AUTH-09: ALS sign-in under the prefix


class ScriptedTiled:
    """Minimal Tiled 0.2.18 auth router over ``httpx.MockTransport`` (no socket)."""

    TARGET = "http://127.0.0.1:59123"

    def __init__(self) -> None:
        self.mode = "internal"
        self.n = 0
        self.paths = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/api/v1/":
            return httpx.Response(200, json={"library_version": "0.2.18", "authentication": {
                "providers": [{"provider": "toy", "mode": self.mode}]}})
        if path == "/api/v1/auth/provider/toy/token":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            if form.get("password") != "pw":
                return httpx.Response(401, json={"detail": "Incorrect username or password"})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"access-{self.n}", "refresh_token": f"refresh-{self.n}",
                                             "expires_in": 900, "token_type": "bearer"})
        if path == "/api/v1/auth/session/revoke":
            return httpx.Response(204)
        if path == "/api/v1/auth/whoami":
            return httpx.Response(200, json={"uuid": "u-1", "type": "user", "roles": [{"name": "user"}],
                                             "identities": [{"id": "bob", "provider": "toy"}]})
        return httpx.Response(404)


@pytest.fixture
def als_prefixed(harness, static_root, monkeypatch):
    from _wiring_support import make_cfg

    from app.api.sessions import als_identity_wiring
    from app.modules.f2w_agent import api as api_mod
    from app.modules.security.authorization import DeploymentSettings

    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    tiled = ScriptedTiled()
    settings = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=tiled.TARGET,
                                                             writer_identity="w")
    port, store = als_identity_wiring(settings, httpx.Client(transport=httpx.MockTransport(tiled.handler)))
    app = api_mod.create_app(make_cfg(harness), deployment=settings, identity_port=port, credential_store=store)
    client = _prefixed_client(app, static_root, base_url="https://f2w.example")
    return client, store, tiled


def test_auth09_sign_in_round_trip_under_the_prefix(als_prefixed):
    from app.api.auth import SESSION_COOKIE

    browser, store, tiled = als_prefixed
    assert browser.get("/fair2wise/").status_code == 200  # the UI that hosts the sign-in form
    assert browser.get(f"{API}/settings").status_code == 401
    assert browser.get(f"{API}/auth/providers").json() == {"providers": [{"provider": "toy", "mode": "internal"}]}
    resp = browser.post(f"{API}/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"})
    assert resp.status_code == 200 and resp.json() == {"status": "signed_in"}
    set_cookie = resp.headers["set-cookie"]
    assert "Path=/fair2wise" in set_cookie and "Secure" in set_cookie and "HttpOnly" in set_cookie
    assert "access-1" not in resp.text + set_cookie and "refresh-1" not in set_cookie
    (cookie,) = [c for c in browser.cookies.jar if c.name == SESSION_COOKIE]
    assert cookie.path == "/fair2wise" and cookie.domain.endswith("f2w.example") and cookie.secure
    # The browser sends it back on prefixed API calls: the round trip works.
    assert browser.get(f"{API}/settings").status_code == 200
    assert len(store) == 1
    out = browser.post(f"{API}/auth/sign-out")
    assert out.json() == {"status": "signed_out"} and "Path=/fair2wise" in out.headers["set-cookie"]
    assert SESSION_COOKIE not in browser.cookies and len(store) == 0
    assert browser.get(f"{API}/settings").status_code == 401


def test_auth09_cookie_is_not_sent_outside_the_prefix(als_prefixed):
    browser, _store, _tiled = als_prefixed
    browser.post(f"{API}/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"})
    jar_request = httpx.Request("GET", "https://f2w.example/other/app")
    browser.cookies.set_cookie_header(jar_request)
    assert "cookie" not in jar_request.headers
    plain = httpx.Request("GET", "http://f2w.example/fair2wise/api/settings")
    browser.cookies.set_cookie_header(plain)
    assert "cookie" not in plain.headers  # Secure: never over plain http


def test_auth09_external_providers_stay_unsupported(als_prefixed):
    browser, store, tiled = als_prefixed
    tiled.mode = "external"
    resp = browser.post(f"{API}/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"})
    assert resp.status_code == 501 and resp.json()["code"] == "unsupported_capability"
    assert len(store) == 0 and "set-cookie" not in resp.headers


def test_auth09_no_tiled_auth_or_admin_proxy_through_the_gateway(als_prefixed):
    browser, _store, tiled = als_prefixed
    before = list(tiled.paths)
    for path in ("/fair2wise/api/api/v1/auth/whoami", "/fair2wise/api/api/v1/", "/fair2wise/tiled/api/v1/",
                 "/fair2wise/api/v1/auth/provider/toy/token", "/api/v1/"):
        resp = browser.get(path)
        ui_fallback = resp.status_code == 200 and resp.text.startswith("<!doctype html>")
        assert resp.status_code in (401, 404) or ui_fallback, path
        assert "library_version" not in resp.text and "access-" not in resp.text, path
    assert tiled.paths == before  # nothing was forwarded to Tiled
