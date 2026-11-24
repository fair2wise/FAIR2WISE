"""Counting wrappers for harvest, Tiled and library operations.

:func:`counted` wraps a function so every call is counted once with an outcome word, without changing its
signature, result or exceptions. Outcomes come from the contract error code (``invalid``, ``denied``,
``unavailable``) or ``error``; no message text is read. While metrics are off the wrapper adds one flag check.
"""

from __future__ import annotations

from collections.abc import Callable
import functools
from typing import ParamSpec, TypeVar

from . import runtime
from ..contracts.errors import ContractError, ErrorCode

P = ParamSpec("P")
R = TypeVar("R")

_BY_CODE = {ErrorCode.INVALID_INPUT: "invalid", ErrorCode.DENIED: "denied", ErrorCode.UNAVAILABLE: "unavailable"}


def operation_outcome(error: BaseException | None) -> str:
    """``ok`` for no error, else ``invalid``/``denied``/``unavailable`` from a ``ContractError`` code, else ``error``."""
    if error is None:
        return "ok"
    if isinstance(error, ContractError):
        return _BY_CODE.get(error.code, "error")
    status = getattr(error, "status_code", None)  # an HTTPException raised by a service
    if isinstance(status, int) and not isinstance(status, bool):
        if status in (401, 403):
            return "denied"
        if 400 <= status < 500:
            return "invalid"
        if status in (502, 503, 504):
            return "unavailable"
    return "error"


def fetch_outcome(status: str) -> str:
    """Harvest download status (``ok``, ``blocked:..``, ``refused:..``, ``failed:..``) to its outcome word."""
    head = status.split(":", 1)[0]
    return head if head in ("ok", "blocked", "refused") else "failed"


def counted(family: str, operation: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: count each call of the function as ``operation`` of ``family`` (``tiled`` or ``library``)."""
    record = runtime.record_tiled_operation if family == "tiled" else runtime.record_library_operation

    def wrap(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            if not runtime.is_enabled():
                return func(*args, **kwargs)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                record(operation=operation, outcome=operation_outcome(exc))
                raise
            record(operation=operation, outcome="ok")
            return result

        return inner

    return wrap
