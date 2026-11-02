"""Versioned error codes shared by tools, jobs, the public client and FastAPI.

Codes are stable strings. Adapters may translate them into legacy response
envelopes (for example the existing ``ChatResponse.status`` values), but the
internal contract always keeps the original code.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable error identifiers.

    Attributes:
        INVALID_INPUT: Arguments failed schema or semantic validation; not retryable.
        DENIED: The request context lacks a required capability or scope.
        UNAUTHENTICATED: No verified caller (ALS sign-in required); never raised on ``:local``.
        UNAVAILABLE: A dependency (provider, Tiled, parser) is down; retryable.
        UNSUPPORTED_CAPABILITY: The backend cannot perform the operation safely.
        NOT_FOUND: The addressed record does not exist in the authorized scope.
        CONFLICT: A revision, lease or approval no longer matches current state.
        STALE_APPROVAL: An approval was granted for a different plan or revision.
        BUDGET_EXHAUSTED: A configured time, cost or call budget is used up.
        CANCELLED: The caller or operator cancelled the work.
        INTERNAL: An unexpected failure; details stay in internal logs.
        WRITER_UNAUTHORIZED: Tiled refused the configured writer credential (HTTP 401/403) on a
            graph write path (access_contract.md section 12); not retryable, never a sign-in prompt.
    """

    INVALID_INPUT = "invalid_input"
    DENIED = "denied"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    STALE_APPROVAL = "stale_approval"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INTERNAL = "internal"
    WRITER_UNAUTHORIZED = "writer_unauthorized"


RETRYABLE_CODES = frozenset({ErrorCode.UNAVAILABLE, ErrorCode.CONFLICT})


class ContractError(Exception):
    """Base exception carrying a stable :class:`ErrorCode`.

    Args:
        code: The stable error code.
        message: Human-readable summary, safe to show to the caller.
        retryable: Override for the code's default retryability.
    """

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = code in RETRYABLE_CODES if retryable is None else retryable


class CapabilityError(ContractError):
    """Raised when a port cannot perform an operation, instead of faking success.

    Args:
        operation: Name of the unsupported operation (for example ``"compare_and_swap"``).
        backend: Backend identifier (for example ``"tiled"`` or ``"json"``).
    """

    def __init__(self, operation: str, backend: str) -> None:
        super().__init__(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"backend {backend!r} does not support {operation!r}",
            retryable=False,
        )
        self.operation = operation
        self.backend = backend
