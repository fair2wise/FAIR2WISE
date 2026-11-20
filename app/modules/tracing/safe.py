"""The content-disclosure filter every span attribute passes (``docs/adr/content-disclosure.md``).

Spans may carry ids, names, timings, counts, model names, status and sizes. They never carry prompt
text, retrieved passages, document text, API keys, tokens, file contents or e-mail addresses. Rather
than trusting each call site, :func:`sanitize` drops what is not provably safe:

* the key must be in :data:`SAFE_KEYS` or end in a :data:`SAFE_SUFFIXES` entry, and must not contain a
  :data:`FORBIDDEN_FRAGMENTS` word (a count or size such as ``f2w.prompt_chars`` is allowed: it measures
  the content without carrying it);
* the value must be a bool, an int, a float, or a short single-line string without an e-mail address,
  a ``?query`` part, or the value of any secret-named environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import re

logger = logging.getLogger(__name__)

#: Attribute keys allowed as they are (semantic-convention names plus the ``f2w.*`` ones this repo sets).
SAFE_KEYS = frozenset({
    "http.request.method", "http.response.status_code", "http.route", "url.path", "url.scheme",
    "server.address", "server.port", "error.type", "code.function", "code.namespace",
    "gen_ai.request.model", "gen_ai.system", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "f2w.job_id", "f2w.job_kind", "f2w.stage", "f2w.turn_id", "f2w.llm.label", "f2w.llm.status",
    "f2w.llm.latency_ms", "f2w.llm.prompt_chars", "f2w.llm.response_chars", "f2w.corpus", "f2w.status",
    "f2w.process", "f2w.stage_status", "f2w.http.client",
})
#: A key not in :data:`SAFE_KEYS` is allowed only when it ends in one of these (ids, counts, sizes...).
SAFE_SUFFIXES = (".id", "_id", ".count", "_count", ".name", ".status", "_status", ".size", "_bytes", "_chars",
                 ".model", "_ms", ".host", ".kind", ".stage", ".version", "_total", "_n")
#: Words (split on ``.`` and ``_``) that make a key unsafe whatever its suffix.
FORBIDDEN_FRAGMENTS = frozenset({
    "prompt", "text", "content", "passage", "passages", "body", "query", "secret", "password", "token",
    "authorization", "credential", "email", "mail", "document", "message", "answer", "question", "url",
    "key", "apikey", "header", "headers", "cookie",
})
#: Keys ending like this are counts or sizes of the thing they name (``f2w.prompt_chars``), so only
#: :data:`HARD_FORBIDDEN` words block them.
_COUNT_SUFFIXES = ("_count", ".count", "_chars", "_bytes", ".size", "_total", "_n", "_ms")
HARD_FORBIDDEN = frozenset({"secret", "password", "authorization", "credential", "apikey", "cookie", "email"})
MAX_VALUE_CHARS = 128
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")
_SECRET_NAME = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|AUTHORIZATION|CREDENTIAL)", re.IGNORECASE)
_MIN_SECRET_CHARS = 8
_WORDS = re.compile(r"[._-]+")

AttributeValue = bool | int | float | str


def key_allowed(key: str) -> bool:
    """Whether ``key`` may be recorded (see the module docstring)."""
    if key in SAFE_KEYS:
        return True
    lowered = key.lower()
    if not lowered.endswith(SAFE_SUFFIXES):
        return False
    words = set(_WORDS.split(lowered))
    if lowered.endswith(_COUNT_SUFFIXES):  # a count or size of something is not the something
        return not words & HARD_FORBIDDEN
    return not words & FORBIDDEN_FRAGMENTS


def _secret_values() -> list[str]:
    return [v for k, v in os.environ.items() if v and len(v) >= _MIN_SECRET_CHARS and _SECRET_NAME.search(k)]


def value_allowed(value: object) -> bool:
    """Whether ``value`` may be recorded: a bool, number, or short plain string free of secrets and e-mails."""
    if isinstance(value, bool | int | float):
        return True
    if not isinstance(value, str):
        return False
    if len(value) > MAX_VALUE_CHARS or "\n" in value or "\r" in value:
        return False
    if "?" in value and "://" in value:  # a URL with a query string
        return False
    if _EMAIL.search(value) or value.lower().startswith(("sk-", "bearer ", "basic ")):
        return False
    return not any(secret in value for secret in _secret_values())


def sanitize(attributes: Mapping[str, object] | None) -> dict[str, AttributeValue]:
    """The subset of ``attributes`` that passes the key and value rules (dropped keys are logged at debug)."""
    clean: dict[str, AttributeValue] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        if key_allowed(key) and value_allowed(value):
            clean[key] = value  # type: ignore[assignment]  # value_allowed narrowed it to bool | int | float | str
        else:
            logger.debug("span attribute %r dropped by the content-disclosure filter", key)
    return clean


def error_type(exc: BaseException) -> str:
    """The exception class name: all a span ever records of an error (messages can quote content)."""
    return type(exc).__name__
