"""CBORG authorizes by client IP: how a refusal for the network address is recognised and shown.

Off LBLnet (or from an address CBORG has not authorized) the gateway answers HTTP 403 with a JSON
body whose ``type`` is ``ip_not_authorized``. That is a property of the network the app runs on, so
repeating the call cannot help: it is terminal for the call (the SDK does not retry a 403, and the
extraction job stops at the first such refusal instead of failing every page).

The refusal body may name the caller's public address. Nothing here, and nothing built from
:data:`IP_NOT_AUTHORIZED_MESSAGE`, repeats it: the fixed message is the only text that is shown or
logged for this refusal. Importing this module has no side effects.
"""

from __future__ import annotations

import re
from typing import Any

from .contracts.errors import ContractError, ErrorCode

#: The ``type`` CBORG puts in the 403 body.
IP_NOT_AUTHORIZED = "ip_not_authorized"
#: Stable ``reason`` of the API error body and ``error_code`` of a model listing (the UI keys on it).
IP_NOT_AUTHORIZED_REASON = "cborg_ip_not_authorized"
#: What a person sees. Fixed text: never built from the provider body, so it cannot echo an address.
IP_NOT_AUTHORIZED_MESSAGE = (
    "CBORG refused this network address; connect via LBLnet/VPN or authorize the address"
)

_TOKEN = re.compile(r"ip[_-]?not[_-]?authori[sz]ed", re.IGNORECASE)


class CborgIpNotAuthorized(ContractError):
    """CBORG answered 403 ``ip_not_authorized``; not retryable (``UNAVAILABLE`` code, ``retryable=False``)."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.UNAVAILABLE, IP_NOT_AUTHORIZED_MESSAGE, retryable=False,
                         reason=IP_NOT_AUTHORIZED_REASON)


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        value = getattr(holder, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _mentions(value: Any, depth: int = 0) -> bool:  # noqa: ANN401  # Any: provider error body of unknown shape
    """Whether a ``type``/``code`` field anywhere in the (possibly nested) body names the refusal."""
    if depth > 4:
        return False
    if isinstance(value, str):
        return bool(_TOKEN.fullmatch(value.strip()))
    if isinstance(value, dict):
        for key in ("type", "code", "error_type", "error_code"):
            if _mentions(value.get(key), depth + 1):
                return True
        return any(_mentions(value.get(key), depth + 1) for key in ("error", "detail"))
    return False


def is_ip_not_authorized(exc: BaseException) -> bool:
    """Whether ``exc`` is CBORG's HTTP 403 ``ip_not_authorized`` refusal.

    Defensive about the shape: the SDK's parsed ``body`` (flat or under ``error``/``detail``), the
    raw response JSON, and, as a last resort, the exception text. The status must be 403 when the
    exception carries one. Never raises.
    """
    if isinstance(exc, CborgIpNotAuthorized):
        return True
    try:
        status = _status(exc)
        if status is not None and status != 403:
            return False
        if _mentions(getattr(exc, "body", None)):
            return True
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                if _mentions(response.json()):
                    return True
            except Exception:  # noqa: BLE001 - an unread or non-JSON body: fall through to the text check
                pass
        return bool(_TOKEN.search(str(exc)))
    except Exception:  # noqa: BLE001 - a hostile exception object must not break error handling
        return False


def as_ip_not_authorized(exc: BaseException) -> CborgIpNotAuthorized | None:
    """The typed refusal for ``exc`` (raise it ``from None``: the cause may carry the address), or ``None``."""
    if isinstance(exc, CborgIpNotAuthorized):
        return exc
    return CborgIpNotAuthorized() if is_ip_not_authorized(exc) else None


__all__ = ("CborgIpNotAuthorized", "IP_NOT_AUTHORIZED", "IP_NOT_AUTHORIZED_MESSAGE", "IP_NOT_AUTHORIZED_REASON",
           "as_ip_not_authorized", "is_ip_not_authorized")
