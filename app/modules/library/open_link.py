"""The "Open at publisher" link of a harvest candidate (design: ``design-manual-pdf-flow.md``, step 3).

The link is a plain string the *user's browser* opens. Nothing here, and nothing that calls it, makes a request: the
optional institutional library proxy is only a prefix put in front of the target URL (the common ``login?url=`` form).
The prefix carries no secret; it is validated at startup and its value is never put in an error message or a log.

``F2W_LIBRARY_PROXY_PREFIX`` (no default): ``https`` only, a host, no user-info, no query-breaking fragment, no
whitespace or control characters, at most :data:`MAX_PREFIX_CHARS` characters, ending in ``=`` or ``/`` so the full
target URL can be appended. Host-rewriting proxies are not supported.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlsplit

PROXY_PREFIX_VARIABLE = "F2W_LIBRARY_PROXY_PREFIX"
MAX_PREFIX_CHARS = 300
#: Characters of a DOI kept as they are in a ``https://doi.org/<doi>`` URL (the rest is percent-encoded).
_DOI_SAFE = "/:()-._~;,@+=$!*'"


class ProxyPrefixError(ValueError):
    """The configured prefix is refused. The message names the reason, never the value."""


def validate_proxy_prefix(value: str) -> str:
    """The prefix as configured, or raise :class:`ProxyPrefixError` naming the rule it breaks (never the value)."""
    if len(value) > MAX_PREFIX_CHARS:
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} is longer than {MAX_PREFIX_CHARS} characters")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} is not a valid URL") from None
    if parts.scheme != "https":
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} must be an https URL")
    if not host:
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} needs a host")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} must not contain user-info (no credentials in a URL)")
    if parts.fragment or "#" in value:
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} must not contain a fragment")
    if not value.endswith(("=", "/")):
        raise ProxyPrefixError(f"{PROXY_PREFIX_VARIABLE} must end with '=' or '/' (the target URL is appended)")
    return value


def proxy_prefix_from_env(env: Mapping[str, str]) -> str | None:
    """The validated prefix from ``env``, or ``None`` when unset or empty (no default).

    Raises:
        ProxyPrefixError: When it is set and invalid.
    """
    value = env.get(PROXY_PREFIX_VARIABLE)
    if value is None or value == "":
        return None
    return validate_proxy_prefix(value)


def target_url(doi: str | None, landing_url: str | None) -> str | None:
    """What the person opens: the DOI URL, else the landing page when it is a plain http(s) URL, else ``None``."""
    if doi and doi.strip():
        return "https://doi.org/" + quote(doi.strip(), safe=_DOI_SAFE)
    landing = (landing_url or "").strip()
    if landing and not any(ch.isspace() or ord(ch) < 0x20 for ch in landing):
        try:
            parts = urlsplit(landing)
            if parts.scheme in ("http", "https") and parts.hostname and parts.username is None:
                return landing
        except ValueError:
            return None
    return None


def open_url(prefix: str | None, doi: str | None, landing_url: str | None) -> str | None:
    """The link for one candidate: the prefix plus the target when a prefix is set, else the plain target.

    Pure string work; the server never requests the result.
    """
    target = target_url(doi, landing_url)
    if target is None:
        return None
    return prefix + target if prefix else target


__all__ = ("MAX_PREFIX_CHARS", "PROXY_PREFIX_VARIABLE", "ProxyPrefixError", "open_url", "proxy_prefix_from_env",
           "target_url", "validate_proxy_prefix")
