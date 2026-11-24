"""The content-disclosure filter every span attribute passes (``docs/adr/content-disclosure.md``).

Spans may carry ids, names, timings, counts, model names, status and sizes. They never carry prompt
text, retrieved passages, document text, API keys, tokens, file contents or e-mail addresses. Rather
than trusting each call site, :func:`sanitize` drops what is not provably safe:

* the key must be in the explicit :data:`ALLOWED_KEYS` list (no suffix rule: a key such as ``f2w.paper.name``
  is dropped; a size such as ``f2w.llm.prompt_chars`` is listed because it measures the content without
  carrying it);
* the value must be a bool, an int, a float, or a short single-line string without an e-mail address,
  a ``?query`` part, or the value of any secret-named environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

#: The explicit allowlist of span attribute keys: semantic-convention names plus the ``f2w.*`` ones this repo sets.
#: A key not listed here is dropped whatever its spelling (a suffix such as ``.name`` or ``_id`` proves nothing:
#: ``f2w.paper.name`` would be a paper title). Add a key here, with a test and a line in
#: ``mkdocs/docs/observability.md``, only when its value is a bounded word, an id, a number or a status.
ALLOWED_KEYS = frozenset({
    "http.request.method", "http.response.status_code", "http.route", "url.path", "url.scheme",
    "server.address", "server.port", "error.type", "code.function", "code.namespace",
    "gen_ai.request.model", "gen_ai.system", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "f2w.job_id", "f2w.job_kind", "f2w.stage", "f2w.turn_id", "f2w.llm.label", "f2w.llm.status",
    "f2w.llm.latency_ms", "f2w.llm.prompt_chars", "f2w.llm.response_chars", "f2w.corpus", "f2w.status",
    "f2w.process", "f2w.stage_status", "f2w.http.client", "f2w.http.duration_ms",
})
MAX_VALUE_CHARS = 128
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")
_SECRET_NAME = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|AUTHORIZATION|CREDENTIAL)", re.IGNORECASE)
_MIN_SECRET_CHARS = 8

AttributeValue = bool | int | float | str


def key_allowed(key: str) -> bool:
    """Whether ``key`` may be recorded: it must be in :data:`ALLOWED_KEYS` (exact match, no suffix rule)."""
    return key in ALLOWED_KEYS


_SECRET_CACHE: tuple[float, list[str]] = (0.0, [])
#: Seconds the list of secret values is reused (scanning the environment on every attribute would dominate a span).
SECRET_TTL_S = 5.0


def reset_secret_cache() -> None:
    """Forget the cached secret values (tests; a secret added to the environment is picked up within the TTL)."""
    global _SECRET_CACHE
    _SECRET_CACHE = (0.0, [])


def _secret_values() -> list[str]:
    global _SECRET_CACHE
    now = time.monotonic()
    stamp, values = _SECRET_CACHE
    if stamp and now - stamp < SECRET_TTL_S:
        return values
    values = [v for k, v in os.environ.items() if v and len(v) >= _MIN_SECRET_CHARS and _SECRET_NAME.search(k)]
    _SECRET_CACHE = (now, values)
    return values


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
