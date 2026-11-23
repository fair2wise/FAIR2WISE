"""Label safety: every metric label value is mapped into a closed set or becomes ``"other"``.

Nothing a person or a source typed can reach a label: not a DOI, URL, query string, uuid, file name, node
id, paper title, user id or prompt. Callers pass what they have; this module keeps only values that are
members of a fixed vocabulary (or, for HTTP routes, a route template the application itself declared).
"""

from __future__ import annotations

from collections.abc import Iterable
import re

OTHER = "other"
UNMATCHED = "unmatched"

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
JOB_OUTCOMES = ("succeeded", "failed", "cancelled", "paused")
QUEUE_STATES = ("queued", "running")
LLM_PROVIDERS = ("cborg", "ollama")
LLM_OUTCOMES = ("ok", "ip_not_authorized", "auth", "rate_limited", "timeout", "error")
STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx")


def enum(value: object, allowed: Iterable[str]) -> str:
    """``value`` when it is exactly one of ``allowed`` (a string), else ``"other"``. Never raises."""
    if isinstance(value, str) and value in allowed:
        return value
    return OTHER


def http_method(value: object) -> str:
    """Upper-cased HTTP method from the standard set, else ``"other"``."""
    return enum(value.upper() if isinstance(value, str) else None, HTTP_METHODS)


def status_class(status: object) -> str:
    """``2xx``..``5xx`` for an integer status in 100..599, else ``"other"`` (a bool is not a status)."""
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return f"{status // 100}xx"
    return OTHER


def http_route(template: object, known: Iterable[str] | None) -> str:
    """The matched route template when it is in ``known`` (the application's declared templates), else ``"unmatched"``.

    ``template`` is ``None`` when no route matched (a 404); a raw request path is not in ``known`` and so can never
    be returned.
    """
    if isinstance(template, str) and known is not None and template in known:
        return template
    return UNMATCHED

_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_PROFILE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")


def build_commit(value: object) -> str:
    """A lower-case hex commit id (7 to 40 digits), else ``"unknown"``."""
    return value if isinstance(value, str) and _COMMIT.fullmatch(value) else "unknown"


def build_profile(value: object) -> str:
    """An image profile name (lower-case word characters, at most 40), else ``"unknown"``."""
    return value if isinstance(value, str) and _PROFILE.fullmatch(value) else "unknown"
