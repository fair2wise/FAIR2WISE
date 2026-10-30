"""Process-wide concurrency gate for CBORG API calls.

CBORG permits a limited number of concurrent requests (default 5). The extractor
can run more page workers than that (``F2W_WORKERS``), and several code paths
issue CBORG calls, so this module provides a single shared in-flight limiter used
at every CBORG call site.

Two independent gates are exposed because CBORG is reached from both synchronous
worker threads (LangChain page extraction, the sync ``CBorgChatClient``) and the
async KG-RAG client. The pipeline runs these in separate sequential phases, so
each gate is sized to the same cap without stacking past it.

Configure the cap with ``CBORG_MAX_CONCURRENCY`` (default 5).

With ``F2W_SHARED_ADMISSION=1`` both gates delegate to one shared
``llm_admission`` controller instead, so sync and async callers share a single
cap and honor 429 cooldowns (H1-3). The flag is read per call.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import os
import threading
from typing import AsyncIterator, Iterator

try:
    from .llm_admission import get_controller, shared_admission_enabled
except ImportError:  # pragma: no cover - legacy top-level import style (scripts/)
    from llm_admission import (  # type: ignore[import-not-found, no-redef]  # legacy top-level import style, resolved only when run from scripts/
        get_controller,
        shared_admission_enabled,
    )


def _read_cap() -> int:
    raw = os.environ.get("CBORG_MAX_CONCURRENCY", "5")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 5
    return value if value > 0 else 5


CBORG_MAX_CONCURRENCY = _read_cap()

_sync_sem = threading.BoundedSemaphore(CBORG_MAX_CONCURRENCY)

_async_sem: asyncio.Semaphore | None = None
_async_sem_lock = threading.Lock()


@contextmanager
def sync_slot(enabled: bool = True) -> Iterator[None]:
    """Hold one synchronous CBORG slot for the duration of the block.

    When ``enabled`` is False (e.g. non-CBORG backends) this is a no-op so other
    backends are never throttled.
    """
    if not enabled:
        yield
        return
    if shared_admission_enabled():
        with get_controller("cborg").slot():
            yield
        return
    _sync_sem.acquire()
    try:
        yield
    finally:
        _sync_sem.release()


def get_async_semaphore() -> asyncio.Semaphore:
    """Return the lazily-created async CBORG semaphore.

    Created on first use so no running event loop is required at import time.
    """
    global _async_sem
    if _async_sem is None:
        with _async_sem_lock:
            if _async_sem is None:
                _async_sem = asyncio.Semaphore(CBORG_MAX_CONCURRENCY)
    return _async_sem


@asynccontextmanager
async def async_slot(enabled: bool = True) -> AsyncIterator[None]:
    """Hold one async CBORG slot for the duration of the block."""
    if not enabled:
        yield
        return
    if shared_admission_enabled():
        async with get_controller("cborg").aslot():
            yield
        return
    sem = get_async_semaphore()
    async with sem:
        yield
