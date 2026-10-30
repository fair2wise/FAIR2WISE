"""Shared admission control for LLM provider accounts (H1-3).

One :class:`AdmissionController` per provider account bounds in-flight calls
across synchronous threads *and* asyncio tasks with a single counter, so a
CBORG cap of 5 means 5 concurrent requests in the process, not 5 per call
style. A rate-limited response (HTTP 429) puts the provider into a cooldown
that every caller honors before taking a new slot.

Nothing is read or created at import time. The cap for a provider is read from
the environment when that provider's controller is first requested.

Adoption is gated by ``F2W_SHARED_ADMISSION`` (see ``shared_admission_enabled``)
until H2 approves switching the default; ``cborg_limiter`` keeps its legacy
semaphores while the flag is off.
"""
from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
import os
import threading
import time
from typing import AsyncIterator, Callable, Iterator

_TRUTHY = {"1", "true", "yes", "on"}


def shared_admission_enabled() -> bool:
    """Return whether the shared controller replaces the legacy CBORG gates."""
    return os.environ.get("F2W_SHARED_ADMISSION", "").strip().lower() in _TRUTHY


def retry_after_seconds(exc: BaseException) -> float | None:
    """Return a ``Retry-After`` delay from a provider exception, if it carries one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


def is_rate_limited(exc: BaseException) -> bool:
    """Return whether ``exc`` is a provider rate-limit (HTTP 429) error."""
    if type(exc).__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429


class AdmissionController:
    """Bound concurrent calls to one provider account across threads and tasks.

    Args:
        cap: Maximum calls in flight.
        default_cooldown: Seconds to pause after a 429 without ``Retry-After``.
        clock: Monotonic clock, injectable for tests.
    """

    def __init__(
        self, cap: int, *, default_cooldown: float = 1.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self.cap = cap
        self.default_cooldown = default_cooldown
        self._clock = clock
        self._cond = threading.Condition()
        self._in_flight = 0
        self._cooldown_until = 0.0
        self.peak = 0

    @property
    def in_flight(self) -> int:
        """Number of calls currently holding a slot."""
        with self._cond:
            return self._in_flight

    def _try_acquire_locked(self) -> bool:
        if self._in_flight >= self.cap or self._clock() < self._cooldown_until:
            return False
        self._in_flight += 1
        self.peak = max(self.peak, self._in_flight)
        return True

    def try_acquire(self) -> bool:
        """Take a slot without waiting; return whether one was taken."""
        with self._cond:
            return self._try_acquire_locked()

    def acquire(self, timeout: float | None = None) -> bool:
        """Block until a slot is free and no cooldown is active."""
        deadline = None if timeout is None else self._clock() + timeout
        with self._cond:
            while not self._try_acquire_locked():
                wait = max(self._cooldown_until - self._clock(), 0.0) or None
                if deadline is not None:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return False
                    wait = remaining if wait is None else min(wait, remaining)
                self._cond.wait(wait)
            return True

    def release(self) -> None:
        """Give back one slot."""
        with self._cond:
            if self._in_flight <= 0:
                raise RuntimeError("release() without a matching acquire()")
            self._in_flight -= 1
            self._cond.notify_all()

    def note_rate_limited(self, retry_after: float | None = None) -> None:
        """Start (or extend) a cooldown after a 429 response."""
        delay = self.default_cooldown if retry_after is None else retry_after
        with self._cond:
            self._cooldown_until = max(self._cooldown_until, self._clock() + delay)
            self._cond.notify_all()

    def _observe(self, exc: BaseException) -> None:
        if is_rate_limited(exc):
            self.note_rate_limited(retry_after_seconds(exc))

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold one slot for a synchronous call; 429s start a cooldown."""
        self.acquire()
        try:
            yield
        except BaseException as exc:
            self._observe(exc)
            raise
        finally:
            self.release()

    @asynccontextmanager
    async def aslot(self, poll: float = 0.01) -> AsyncIterator[None]:
        """Hold one slot for an async call without blocking the event loop.

        Acquisition is non-blocking and polled, so cancellation while waiting
        never leaks a slot.
        """
        while not self.try_acquire():
            await asyncio.sleep(poll)
        try:
            yield
        except BaseException as exc:
            self._observe(exc)
            raise
        finally:
            self.release()


_CAP_ENV = {"cborg": "CBORG_MAX_CONCURRENCY"}
_controllers: dict[str, AdmissionController] = {}
_controllers_lock = threading.Lock()


def _read_cap(provider: str) -> int:
    raw = os.environ.get(_CAP_ENV.get(provider, f"F2W_{provider.upper()}_MAX_CONCURRENCY"), "5")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 5
    return value if value > 0 else 5


def get_controller(provider: str) -> AdmissionController:
    """Return the process-wide controller for ``provider``, creating it on first use."""
    key = provider.strip().lower()
    with _controllers_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = _controllers[key] = AdmissionController(_read_cap(key))
        return controller


def reset_controllers() -> None:
    """Drop all controllers (tests only)."""
    with _controllers_lock:
        _controllers.clear()


class ProviderAdmission:
    """Facade passed to runtimes and tools: ``slot(provider)`` / ``aslot(provider)``."""

    def slot(self, provider: str) -> AbstractContextManager[None]:
        """Return a sync context manager holding one slot for ``provider``."""
        return get_controller(provider).slot()

    def aslot(self, provider: str) -> AbstractAsyncContextManager[None]:
        """Return an async context manager holding one slot for ``provider``."""
        return get_controller(provider).aslot()
