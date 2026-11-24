"""One model-call failure in plain words, for a job log (never the provider's own text).

A job log is read by a person watching a run, and it travels to the browser. The text of a provider error can
carry the caller's address, a request id, a prompt fragment or a header, so none of it is repeated here:
:func:`plain_call_failure` classifies the exception and answers one fixed sentence, naming only the provider
(``cborg`` or ``ollama``), the model id the caller already knows and a number of seconds for a timeout.
Importing this module has no side effects.
"""

from __future__ import annotations

from .cborg_errors import IP_NOT_AUTHORIZED_LOG_TEXT, is_ip_not_authorized
from .cborg_http import LLM_READ_TIMEOUT, LLM_TOTAL_DEADLINE


def provider_label(backend: str | None) -> str:
    """``cborg`` or ``ollama`` for a backend name (``cborg-openai`` is CBORG too)."""
    return "ollama" if (backend or "").lower() == "ollama" else "cborg"


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        value = getattr(holder, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _named(exc: BaseException, *names: str) -> bool:
    """Whether the class or a base class of ``exc`` has one of these names (no import of the SDKs needed)."""
    return any(klass.__name__ in names for klass in type(exc).__mro__)


def plain_call_failure(exc: BaseException, *, provider: str, model: str, timeout_s: float | None = None) -> str:
    """A plain-words sentence for a failed model call.

    Args:
        exc: What the call raised.
        provider: ``cborg`` or ``ollama`` (see :func:`provider_label`).
        model: Model id used for the call (shown as is).
        timeout_s: The limit that applied, shown for a timeout; defaults to the read limit (or the whole-call
            deadline for the ``TimeoutError`` of ``call_with_deadline``).

    Returns:
        For example ``"CBORG refused this machine's address; check network egress"``, ``"model call timed out
        after 120 s (cborg, model m)"``, a rate-limit, authentication, connection or server-error sentence,
        or ``"model call failed (ValueError) (cborg, model m)"``. Never contains text from ``exc``'s message.
    """
    where = f"{provider}, model {model}"
    if provider == "cborg" and is_ip_not_authorized(exc):
        return IP_NOT_AUTHORIZED_LOG_TEXT
    status = _status(exc)
    if isinstance(exc, TimeoutError) or _named(exc, "APITimeoutError", "ReadTimeout", "ConnectTimeout", "Timeout",
                                               "TimeoutException", "PoolTimeout", "WriteTimeout"):
        seconds = timeout_s if timeout_s is not None else (
            LLM_TOTAL_DEADLINE if isinstance(exc, TimeoutError) else LLM_READ_TIMEOUT)
        return f"model call timed out after {seconds:g} s ({where})"
    if status == 429 or _named(exc, "RateLimitError"):
        return f"{provider} rate limit reached (HTTP 429); too many requests ({where})"
    if status in (401, 403) or _named(exc, "AuthenticationError", "PermissionDeniedError"):
        return f"{provider} rejected the credentials (authentication failed); check the configured key ({where})"
    if status is not None and status >= 500:
        return f"{provider} reported a server error (HTTP {status}) ({where})"
    if isinstance(exc, ConnectionError) or _named(exc, "APIConnectionError", "ConnectError", "ConnectionError"):
        return f"could not reach {provider} ({where})"
    return f"model call failed ({type(exc).__name__}) ({where})"


__all__ = ("plain_call_failure", "provider_label")
