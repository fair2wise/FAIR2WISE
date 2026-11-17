"""Proxy-aware ``SafeFetcher`` (``F2W_SAFE_FETCH_TRUST_PROXY``): direct path unchanged, proxied path guarded.

The peer is faked through the ``network_stream`` response extension; one test runs a real loopback
HTTP proxy so ``httpx``'s own proxy selection is exercised. No test uses the network.
"""

from __future__ import annotations

from collections.abc import Iterator
import http.server
import logging
from pathlib import Path
import threading

import httpx
import pytest

from app.modules.literature.safe_fetch import (
    NetworkPolicy, SafeFetcher, no_proxy_matches, proxy_for_url, trust_proxy_from_env,
)
from app.modules.literature.store import FileDocumentStore
from tests.literature.lit_fakes import local_ctx, minimal_pdf_bytes

PDF = minimal_pdf_bytes("proxied paper")
PROXY_IP = "10.43.0.5"
SECRET = "s3cr3t-proxy-pass"
PROXY_URL = f"http://user:{SECRET}@squid.cluster:3128"
PROXY_ENV = {"HTTP_PROXY": PROXY_URL, "HTTPS_PROXY": PROXY_URL}

DNS = {
    "squid.cluster": [PROXY_IP], "publisher.example": ["93.184.216.34"], "internal.example": ["10.0.0.5"],
    "loop.example": ["127.0.0.1"], "linklocal.example": ["169.254.1.1"], "meta.example": ["169.254.169.254"],
    "direct.example": ["93.184.216.35"], "mixed.example": ["93.184.216.34", "10.0.0.5"],
}


def resolver(host: str) -> list[str]:
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


class FakeStream:
    def __init__(self, peer: str) -> None:
        self.peer = peer

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return (self.peer, 3128) if name == "server_addr" else None


def client(peer: str, calls: list[str], redirects: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        ext = {"network_stream": FakeStream(peer)}
        target = (redirects or {}).get(str(request.url))
        if target:
            return httpx.Response(302, headers={"location": target}, extensions=ext)
        return httpx.Response(200, content=PDF, extensions=ext)
    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(peer: str, url: str, tmp_path: Path, *, trust: bool, env: dict[str, str] | None = None,
          redirects: dict[str, str] | None = None) -> tuple[str, list[str]]:
    calls: list[str] = []
    policy = NetworkPolicy(resolver=resolver, proxy_resolver=resolver, trust_proxy=trust,
                           proxy_environ=PROXY_ENV if env is None else env)
    result = SafeFetcher(client(peer, calls, redirects), policy).fetch_pdf(
        local_ctx(), url, FileDocumentStore(tmp_path / "store"))
    return result.status, calls


# -- direct path: unchanged ------------------------------------------------------------------------------

def test_direct_public_peer_allowed(tmp_path):
    status, _ = fetch("93.184.216.34", "https://publisher.example/a.pdf", tmp_path, trust=False, env={})
    assert status == "stored"


@pytest.mark.parametrize("peer", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "100.64.0.1", "::1"])
def test_direct_private_peer_refused(tmp_path, peer):
    status, _ = fetch(peer, "https://publisher.example/a.pdf", tmp_path, trust=False, env={})
    assert status == "blocked:private_address"


def test_opt_in_off_keeps_refusing_proxied_fetch(tmp_path):
    status, _ = fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=False)
    assert status == "blocked:private_address"


def test_opt_in_default_follows_env(monkeypatch):
    monkeypatch.delenv("F2W_SAFE_FETCH_TRUST_PROXY", raising=False)
    assert NetworkPolicy().trust_proxy is False
    monkeypatch.setenv("F2W_SAFE_FETCH_TRUST_PROXY", "1")
    assert NetworkPolicy().trust_proxy is True
    assert trust_proxy_from_env({"F2W_SAFE_FETCH_TRUST_PROXY": "off"}) is False


# -- proxied path ----------------------------------------------------------------------------------------

def test_proxied_public_target_allowed(tmp_path):
    status, calls = fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=True)
    assert status == "stored" and calls == ["https://publisher.example/a.pdf"]


def test_proxied_other_private_peer_still_refused(tmp_path):
    """Only the proxy's own address is excused: a private peer that is not the proxy is refused."""
    status, _ = fetch("10.0.0.99", "https://publisher.example/a.pdf", tmp_path, trust=True)
    assert status == "blocked:private_address"


@pytest.mark.parametrize("host", ["internal.example", "loop.example", "linklocal.example", "meta.example",
                                  "mixed.example"])
def test_proxied_target_resolving_private_refused_before_any_request(tmp_path, host):
    status, calls = fetch(PROXY_IP, f"https://{host}/a.pdf", tmp_path, trust=True)
    assert status == "blocked:private_address" and calls == []


@pytest.mark.parametrize("literal", ["10.1.2.3", "127.0.0.1", "169.254.169.254", "[::1]", "[fe80::1]"])
def test_proxied_private_ip_literal_refused(tmp_path, literal):
    status, calls = fetch(PROXY_IP, f"http://{literal}/a.pdf", tmp_path, trust=True)
    assert status == "blocked:private_address" and calls == []


def test_redirect_from_public_to_private_refused(tmp_path):
    status, calls = fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=True,
                          redirects={"https://publisher.example/a.pdf": "http://169.254.169.254/latest/meta-data"})
    assert status == "blocked:redirect_target" and calls == ["https://publisher.example/a.pdf"]


def test_redirect_to_host_resolving_private_refused(tmp_path):
    status, _ = fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=True,
                      redirects={"https://publisher.example/a.pdf": "https://internal.example/b.pdf"})
    assert status == "blocked:redirect_target"


def test_no_proxy_host_goes_direct_and_keeps_peer_check(tmp_path):
    env = {**PROXY_ENV, "NO_PROXY": "direct.example,.cluster"}
    url = "https://direct.example/a.pdf"
    # Not the proxy hop: a private peer is refused even with the opt-in, the proxy's address included.
    assert fetch("10.43.0.9", url, tmp_path, trust=True, env=env)[0] == "blocked:private_address"
    assert fetch(PROXY_IP, url, tmp_path, trust=True, env=env)[0] == "blocked:private_address"
    assert fetch("93.184.216.35", url, tmp_path, trust=True, env=env)[0] == "stored"


def test_proxy_credentials_never_logged_or_returned(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    refused, _ = fetch("10.0.0.99", "https://publisher.example/a.pdf", tmp_path, trust=True)
    blocked, _ = fetch(PROXY_IP, "https://internal.example/a.pdf", tmp_path, trust=True)
    assert SECRET not in caplog.text and SECRET not in refused and SECRET not in blocked


def test_unresolvable_proxy_host_trusts_nothing(tmp_path):
    env = {"HTTPS_PROXY": "http://nxproxy.invalid:3128"}
    status, _ = fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=True, env=env)
    assert status == "blocked:private_address"


def test_proxy_ip_literal_in_proxy_url(tmp_path):
    env = {"https_proxy": f"http://{PROXY_IP}:3128"}
    assert fetch(PROXY_IP, "https://publisher.example/a.pdf", tmp_path, trust=True, env=env)[0] == "stored"


# -- environment parsing ---------------------------------------------------------------------------------

@pytest.mark.parametrize("host,port,rule,expected", [
    ("a.example.com", 443, "*", True), ("a.example.com", 443, "example.com", True),
    ("example.com", 443, ".example.com", True), ("a.example.com", 443, "*.example.com", True),
    ("badexample.com", 443, "example.com", False), ("example.org", 443, "example.com", False),
    ("10.1.2.3", 80, "10.0.0.0/8", True), ("11.1.2.3", 80, "10.0.0.0/8", False),
    ("10.1.2.3", 80, "10.1.2.3", True), ("fd00::5", 80, "fd00::/8", True), ("[fd00::5]", 80, "fd00::/8", True),
    ("EXAMPLE.com", 80, "example.COM", True), ("example.com", 8080, "example.com:8080", True),
    ("example.com", 80, "example.com:8080", False), ("x.example", 80, "a.example, x.example", True),
    ("x.example", 80, "", False), ("10.1.2.3", 80, "not-a-cidr/99", False),
])
def test_no_proxy_semantics(host, port, rule, expected):
    assert no_proxy_matches(host, port, rule) is expected


def test_proxy_for_url_selection():
    env = {"http_proxy": "http://lower:1", "HTTP_PROXY": "http://upper:2", "HTTPS_PROXY": "http://s:3",
           "NO_PROXY": "skip.example"}
    assert proxy_for_url("http://x.example/", env) == "http://lower:1"
    assert proxy_for_url("https://x.example/", env) == "http://s:3"
    assert proxy_for_url("https://skip.example/", env) is None
    assert proxy_for_url("https://x.example/", {}) is None
    assert proxy_for_url("https://x.example/", {"ALL_PROXY": "http://all:4"}) == "http://all:4"
    assert proxy_for_url("ftp://x.example/", env) is None


# -- real loopback proxy (httpx's own proxy selection) ---------------------------------------------------

@pytest.fixture
def loopback_proxy() -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(PDF)))
            self.end_headers()
            self.wfile.write(PDF)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def real_fetch(monkeypatch, tmp_path: Path, proxy: str, *, trust: bool) -> str:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("http_proxy", proxy)
    policy = NetworkPolicy(resolver=resolver, trust_proxy=trust, connect_timeout=1.0, read_timeout=1.0)
    fetcher = SafeFetcher(httpx.Client(), policy)  # trust_env: httpx reads the same variables
    return fetcher.fetch_pdf(local_ctx(), "http://publisher.example/a.pdf", FileDocumentStore(tmp_path / "s")).status


def test_real_loopback_proxy_allowed_only_with_opt_in(monkeypatch, tmp_path, loopback_proxy):
    proxy, seen = loopback_proxy
    assert real_fetch(monkeypatch, tmp_path, proxy, trust=False) == "blocked:private_address"
    assert real_fetch(monkeypatch, tmp_path, proxy, trust=True) == "stored"
    assert seen and seen[-1] == "http://publisher.example/a.pdf"
