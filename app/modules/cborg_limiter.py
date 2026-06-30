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
"""
from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator, Optional


def _read_cap() -> int:
    raw = os.environ.get("CBORG_MAX_CONCURRENCY", "5")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 5
    return value if value > 0 else 5


CBORG_MAX_CONCURRENCY = _read_cap()

_sync_sem = threading.BoundedSemaphore(CBORG_MAX_CONCURRENCY)

_async_sem: Optional[asyncio.Semaphore] = None
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
    sem = get_async_semaphore()
    async with sem:
        yield
