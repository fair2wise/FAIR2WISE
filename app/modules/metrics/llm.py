"""Model-call metrics: which provider answered and how the call ended, from the call site's own objects.

There is no model-name label and no text of any kind: only the provider (``cborg`` or ``ollama``) and a fixed
outcome word. ``ip_not_authorized`` comes from :func:`app.modules.cborg_errors.is_ip_not_authorized`, the single
classification of CBORG's network refusal; nothing here matches error text.
"""

from __future__ import annotations

from . import labels, runtime
from ..cborg_errors import is_ip_not_authorized

_MAX_CHAIN = 5


def provider_of(client: object) -> str:
    """``cborg`` or ``ollama`` from the client's class name or base URL; ``other`` when neither is recognised."""
    base = getattr(client, "base", None) or getattr(client, "base_url", None) or getattr(client, "openai_api_base", None)
    hint = f"{type(client).__name__} {type(client).__module__} {base or ''}".lower()
    if "ollama" in hint or ":11434" in hint:
        return "ollama"
    if "cborg" in hint:
        return "cborg"
    return labels.OTHER


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in seen and len(seen) < _MAX_CHAIN:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return seen


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        value = getattr(holder, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def outcome_of(error: BaseException | None) -> str:
    """How a call ended: ``ok``, ``ip_not_authorized``, ``auth``, ``rate_limited``, ``timeout`` or ``error``."""
    if error is None:
        return "ok"
    try:
        if is_ip_not_authorized(error):
            return "ip_not_authorized"
        chain = _chain(error)
        statuses = [s for s in (_status(e) for e in chain) if s is not None]
        if 429 in statuses:
            return "rate_limited"
        if any(isinstance(e, TimeoutError) or "timeout" in type(e).__name__.lower() for e in chain):
            return "timeout"
        if any(s in (401, 403) for s in statuses):
            return "auth"
        if any(s in (408, 504) for s in statuses):
            return "timeout"
    except Exception:  # noqa: BLE001 - a hostile exception object must not break the call path
        return "error"
    return "error"


def record_llm_call(*, provider: object, error: BaseException | None, seconds: float) -> None:
    """Record one finished model call (a cheap no-op while metrics are off; never raises)."""
    if not runtime.is_enabled():
        return
    try:
        runtime.record_llm(provider=provider, outcome=outcome_of(error), seconds=seconds)
    except Exception:  # noqa: BLE001 - recording must never fail the call
        return
