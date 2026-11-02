"""CBORG transport selection and stream helpers, with sockets and ifconfig replaced by fakes."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from app.modules import cborg_http

GUA = "2620:83:8004:572::1:203"
ULA = "fdfa:1de7:c0f6:4b47:1065:d7f9:64b8:15ea"


class FakeSocket:
    """Stand-in for a UDP socket; records use and can fail connect/bind."""

    def __init__(self, sockname=GUA, fail_connect=False, fail_bind=False):
        self.sockname, self.fail_connect, self.fail_bind = sockname, fail_connect, fail_bind
        self.closed, self.connected, self.bound = False, None, None

    def connect(self, addr):
        if self.fail_connect:
            raise OSError("unreachable")
        self.connected = addr

    def bind(self, addr):
        if self.fail_bind:
            raise OSError("cannot assign")
        self.bound = addr

    def getsockname(self):
        return (self.sockname, 0, 0, 0)

    def close(self):
        self.closed = True


def _patch_socket(monkeypatch, sock):
    monkeypatch.setattr(cborg_http.socket, "socket", lambda *a: sock)
    return sock


def test_probe_returns_routed_global_address_and_closes_socket(monkeypatch):
    sock = _patch_socket(monkeypatch, FakeSocket(GUA))
    assert cborg_http._probe_global_ipv6() == GUA
    assert sock.connected[1] == 53 and sock.closed


def test_probe_falls_back_to_ifconfig_when_route_gives_ula_or_fails(monkeypatch):
    sock = _patch_socket(monkeypatch, FakeSocket(ULA))
    monkeypatch.setattr(cborg_http, "_ifconfig_global_ipv6", lambda: [ULA, GUA])
    assert cborg_http._probe_global_ipv6() == GUA  # the ULA the route picked is skipped
    monkeypatch.setattr(cborg_http, "_ifconfig_global_ipv6", lambda: [])
    assert cborg_http._probe_global_ipv6() is None
    _patch_socket(monkeypatch, FakeSocket(fail_connect=True))
    monkeypatch.setattr(cborg_http, "_ifconfig_global_ipv6", lambda: [GUA])
    assert cborg_http._probe_global_ipv6() == GUA


def test_ifconfig_parsing_keeps_only_unique_global_addresses(monkeypatch):
    out = f"lo0: inet6 ::1 prefixlen 128\nen0: inet6 fe80::1%en0 prefixlen 64\n inet6 {GUA} prefixlen 64\n inet6 {GUA} x\n inet6 {ULA}\n"
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: out)
    assert cborg_http._ifconfig_global_ipv6() == [GUA]

    def boom(*a, **k):
        raise FileNotFoundError("ifconfig")

    monkeypatch.setattr(subprocess, "check_output", boom)
    assert cborg_http._ifconfig_global_ipv6() == []


def test_bind_assignable_with_fake_socket_and_scope_suffix(monkeypatch):
    sock = _patch_socket(monkeypatch, FakeSocket())
    assert cborg_http._ipv6_bind_assignable(f"{GUA}%en0") is True and sock.bound == (GUA, 0) and sock.closed
    _patch_socket(monkeypatch, FakeSocket(fail_bind=True))
    assert cborg_http._ipv6_bind_assignable(GUA) is False
    assert cborg_http._ipv6_bind_assignable("not-an-ip") is False


@pytest.mark.parametrize(
    ("family", "expected"),
    [("auto", None), ("", None), ("ipv4", "0.0.0.0"), ("4", "0.0.0.0")],
)
def test_local_address_by_family(monkeypatch, family, expected):
    monkeypatch.delenv("CBORG_FORCE_IPV6", raising=False)
    monkeypatch.setenv("CBORG_IP_FAMILY", family)
    assert cborg_http._local_address() == expected


def test_local_address_rejects_unknown_family_and_falls_back_to_wildcard(monkeypatch):
    monkeypatch.delenv("CBORG_FORCE_IPV6", raising=False)
    monkeypatch.setenv("CBORG_IP_FAMILY", "carrier-pigeon")
    with pytest.raises(ValueError, match="CBORG_IP_FAMILY"):
        cborg_http._local_address()
    monkeypatch.setenv("CBORG_IP_FAMILY", "ipv6")
    monkeypatch.delenv("CBORG_IPV6_BIND", raising=False)
    monkeypatch.setattr(cborg_http, "_probe_global_ipv6", lambda: None)
    assert cborg_http._local_address() == "::"


def _chunk(delta=None, message=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=delta) if delta is not None else None,
                                                    message=SimpleNamespace(content=message) if message is not None else None)])


def test_stream_chunk_text_prefers_delta_then_message_and_ignores_non_strings():
    assert cborg_http.stream_chunk_text(SimpleNamespace(choices=[])) == ""
    assert cborg_http.stream_chunk_text(SimpleNamespace()) == ""
    assert cborg_http.stream_chunk_text(_chunk(delta="d", message="m")) == "d"
    assert cborg_http.stream_chunk_text(_chunk(delta="", message="m")) == "m"
    assert cborg_http.stream_chunk_text(_chunk(message="m")) == "m"
    assert cborg_http.stream_chunk_text(_chunk(delta=None)) == ""
    assert cborg_http.stream_chunk_text(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=5))])) == ""


def test_collect_stream_text_sync_and_async():
    chunks = [_chunk(delta="He"), _chunk(delta=""), _chunk(delta="llo")]
    assert cborg_http.collect_stream_text(chunks) == "Hello"

    async def agen():
        for c in chunks:
            yield c

    assert asyncio.run(cborg_http.collect_stream_text_async(agen())) == "Hello"


def test_require_cborg_api_key_precedence_and_fail_closed(monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", " env ")
    assert cborg_http.require_cborg_api_key() == "env"
    assert cborg_http.require_cborg_api_key(" given ") == "given"
    monkeypatch.delenv("CBORG_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    with pytest.raises(RuntimeError, match="CBORG_API_KEY"):
        cborg_http.require_cborg_api_key("  ")


def test_openai_http_kwargs_pins_local_address_for_sync_and_async(monkeypatch):
    monkeypatch.setattr(cborg_http, "_local_address", lambda: None)
    assert cborg_http.openai_http_kwargs(asynchronous=False) == {}
    made = []
    monkeypatch.setattr(cborg_http, "_local_address", lambda: "0.0.0.0")
    monkeypatch.setattr(cborg_http.httpx, "HTTPTransport", lambda local_address: made.append(("sync", local_address)) or "T1")
    monkeypatch.setattr(cborg_http.httpx, "AsyncHTTPTransport", lambda local_address: made.append(("async", local_address)) or "T2")
    monkeypatch.setattr(cborg_http.httpx, "Client", lambda transport: ("client", transport))
    monkeypatch.setattr(cborg_http.httpx, "AsyncClient", lambda transport: ("aclient", transport))
    assert cborg_http.openai_http_kwargs(asynchronous=False) == {"http_client": ("client", "T1")}
    assert cborg_http.openai_http_kwargs(asynchronous=True) == {"http_client": ("aclient", "T2")}
    assert made == [("sync", "0.0.0.0"), ("async", "0.0.0.0")]
