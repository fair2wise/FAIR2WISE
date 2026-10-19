"""Timing and allocation-peak recorder shared by the T19D scale tests (one table per process)."""

from __future__ import annotations

import time
import tracemalloc
from typing import Any, Callable, Dict

#: ``name -> {"population", "seconds", "seconds_traced", "peak_mib"}``.
TIMINGS: Dict[str, Dict[str, Any]] = {}


def measure(name: str, fn: Callable[[], Any], *, population: str = "", memory: bool = False) -> Any:
    """Run ``fn``, record its wall time (and the tracemalloc peak when ``memory``) under ``name``."""
    if memory:
        tracemalloc.start()
    start = time.perf_counter()
    peak = None
    try:
        result = fn()
    finally:
        seconds = time.perf_counter() - start
        if memory:
            _cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
    row = TIMINGS.setdefault(name, {})
    if population:
        row["population"] = population
    row["seconds_traced" if memory else "seconds"] = round(seconds, 3)
    if peak is not None:
        row["peak_mib"] = round(peak / 2**20, 1)
    return result
