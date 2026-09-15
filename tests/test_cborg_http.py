from app.modules import cborg_http


def test_is_global_ipv6_rejects_ula_and_loopback():
    assert cborg_http._is_global_ipv6("2620:83:8004:572::1:203")
    assert not cborg_http._is_global_ipv6("fdfa:1de7:c0f6:4b47:1065:d7f9:64b8:15ea")
    assert not cborg_http._is_global_ipv6("::1")
    assert not cborg_http._is_global_ipv6("fe80::1")


def test_local_address_prefers_explicit_bind(monkeypatch):
    monkeypatch.setenv("CBORG_FORCE_IPV6", "1")
    monkeypatch.setenv("CBORG_IPV6_BIND", "2620:83:8004:572::1:203")
    monkeypatch.setattr(cborg_http, "_ipv6_bind_assignable", lambda _addr: True)
    assert cborg_http._local_address() == "2620:83:8004:572::1:203"


def test_local_address_ignores_unassignable_bind(monkeypatch):
    monkeypatch.setenv("CBORG_FORCE_IPV6", "1")
    monkeypatch.setenv("CBORG_IPV6_BIND", "2001:db8::1")
    monkeypatch.setattr(cborg_http, "_ipv6_bind_assignable", lambda _addr: False)
    monkeypatch.setattr(cborg_http, "_probe_global_ipv6", lambda: "2620:83:8004:572::1:203")
    assert cborg_http._local_address() == "2620:83:8004:572::1:203"


def test_ipv6_bind_assignable_rejects_documentation_prefix():
    assert not cborg_http._ipv6_bind_assignable("2001:db8::1")
    assert not cborg_http._ipv6_bind_assignable("")


def test_local_address_probes_when_bind_unset(monkeypatch):
    monkeypatch.setenv("CBORG_FORCE_IPV6", "1")
    monkeypatch.delenv("CBORG_IPV6_BIND", raising=False)
    monkeypatch.setattr(cborg_http, "_probe_global_ipv6", lambda: "2620:83:8004:572::1:203")
    assert cborg_http._local_address() == "2620:83:8004:572::1:203"
