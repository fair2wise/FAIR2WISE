"""The "Open at publisher" link and the library proxy prefix (design-manual-pdf-flow.md, step 3)."""

from __future__ import annotations

import socket

import pytest

from app.modules.library.open_link import (
    MAX_PREFIX_CHARS,
    PROXY_PREFIX_VARIABLE,
    ProxyPrefixError,
    open_url,
    proxy_prefix_from_env,
    target_url,
    validate_proxy_prefix,
)

GOOD = "https://ezproxy.example.edu/login?url="


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing here may open a socket: the link is only ever a string for the person's browser."""
    def refuse(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.mark.parametrize("prefix", [GOOD, "https://proxy.example.edu/", "https://proxy.example.edu:8443/go?u=",
                                    "https://a.b/" + "x" * (MAX_PREFIX_CHARS - len("https://a.b/") - 1) + "="])
def test_valid_prefixes_are_accepted_unchanged(prefix):
    assert validate_proxy_prefix(prefix) == prefix


@pytest.mark.parametrize(("prefix", "word"), [
    ("http://ezproxy.example.edu/login?url=", "https"),
    ("ftp://ezproxy.example.edu/", "https"),
    ("//ezproxy.example.edu/login?url=", "https"),
    ("ezproxy.example.edu/login?url=", "https"),
    ("https://", "host"),
    ("https:///login?url=", "host"),
    ("https://user:secret@ezproxy.example.edu/login?url=", "user-info"),
    ("https://user@ezproxy.example.edu/login?url=", "user-info"),
    ("https://ezproxy.example.edu/login url=", "whitespace"),
    ("https://ezproxy.example.edu/login?url=\n", "whitespace"),
    ("https://ezproxy.example.edu/login?url=\x00", "control"),
    (" https://ezproxy.example.edu/login?url=", "whitespace"),
    ("https://ezproxy.example.edu/login?url", "end with"),
    ("https://ezproxy.example.edu/login", "end with"),
    ("https://ezproxy.example.edu/login?url=#frag=", "fragment"),
    ("https://a.b/" + "x" * MAX_PREFIX_CHARS, "300"),
])
def test_invalid_prefixes_are_refused_with_a_reason_and_never_the_value(prefix, word):
    with pytest.raises(ProxyPrefixError) as err:
        validate_proxy_prefix(prefix)
    message = str(err.value)
    assert PROXY_PREFIX_VARIABLE in message and word in message
    assert "secret" not in message and "ezproxy" not in message


def test_unset_or_empty_means_no_prefix_and_there_is_no_default():
    assert proxy_prefix_from_env({}) is None and proxy_prefix_from_env({PROXY_PREFIX_VARIABLE: ""}) is None
    assert proxy_prefix_from_env({PROXY_PREFIX_VARIABLE: GOOD}) == GOOD
    with pytest.raises(ProxyPrefixError):
        proxy_prefix_from_env({PROXY_PREFIX_VARIABLE: "http://x.example/"})


def test_the_target_is_the_doi_url_else_a_plain_landing_page():
    assert target_url("10.1021/acs.macromol.0c01782", "https://example.org/landing") == "https://doi.org/10.1021/acs.macromol.0c01782"
    assert target_url(None, "https://arxiv.org/abs/2101.00001") == "https://arxiv.org/abs/2101.00001"
    assert target_url("", "http://example.org/x") == "http://example.org/x"
    assert target_url("10.1002/(SICI)1097-4628(19960104)59:1<1::AID-APP1>3.0.CO;2-X", None) == (
        "https://doi.org/10.1002/(SICI)1097-4628(19960104)59:1%3C1::AID-APP1%3E3.0.CO;2-X")
    assert target_url("10.1/a b#c?d", None) == "https://doi.org/10.1/a%20b%23c%3Fd"
    for bad in (None, "", "javascript:alert(1)", "https://user:pw@example.org/x", "https://example.org/a b", "file:///etc/passwd",
                "ftp://example.org/x"):
        assert target_url(None, bad) is None


def test_open_url_puts_the_prefix_in_front_of_the_full_target():
    assert open_url(GOOD, "10.1/abc", None) == "https://ezproxy.example.edu/login?url=https://doi.org/10.1/abc"
    assert open_url(GOOD, None, "https://arxiv.org/abs/1") == GOOD + "https://arxiv.org/abs/1"
    assert open_url(None, "10.1/abc", None) == "https://doi.org/10.1/abc"
    assert open_url(GOOD, None, None) is None and open_url(None, None, "") is None
