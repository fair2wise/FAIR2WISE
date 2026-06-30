import asyncio
import threading
import time

from app.modules import cborg_limiter


def test_default_cap_is_five():
    assert cborg_limiter.CBORG_MAX_CONCURRENCY == 5


def test_sync_slot_disabled_is_noop():
    # Should not touch the semaphore; just run the block.
    with cborg_limiter.sync_slot(enabled=False):
        pass


def test_sync_slot_caps_concurrency_at_five():
    peak = 0
    current = 0
    lock = threading.Lock()

    def worker():
        nonlocal peak, current
        with cborg_limiter.sync_slot():
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak <= cborg_limiter.CBORG_MAX_CONCURRENCY


def test_async_slot_caps_concurrency_at_five():
    async def run():
        peak = 0
        current = 0

        async def worker():
            nonlocal peak, current
            async with cborg_limiter.async_slot():
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.05)
                current -= 1

        await asyncio.gather(*(worker() for _ in range(16)))
        return peak

    peak = asyncio.run(run())
    assert peak <= cborg_limiter.CBORG_MAX_CONCURRENCY
