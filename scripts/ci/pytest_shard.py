"""pytest plugin: run one deterministic slice of the collected tests (CI sharding).

Enable with ``python -m pytest -p scripts.ci.pytest_shard`` (from the repository root; no PYTHONPATH, the
supervisor tests refuse one), and select the slice with
``F2W_SHARD=<index>/<count>`` (1-based, for example ``2/3``). Every test belongs to exactly one
shard: ``crc32(nodeid) % count``. The assignment is stable between runs and between xdist
workers (each worker collects the same ids), needs no timing file, and spreads the slow
subprocess tests of one directory across shards. Unset, the plugin does nothing.
"""

from __future__ import annotations

import os
from typing import Any
import zlib


def parse_shard(spec: str) -> tuple[int, int]:
    """Return ``(index, count)`` from ``"i/n"``; raise ValueError for anything else."""
    index_text, sep, count_text = spec.partition("/")
    if not sep:
        raise ValueError(f"F2W_SHARD must look like 2/3, got {spec!r}")
    index, count = int(index_text), int(count_text)
    if count < 1 or not 1 <= index <= count:
        raise ValueError(f"F2W_SHARD must look like 2/3 with 1 <= index <= count, got {spec!r}")
    return index, count


def shard_of(nodeid: str, count: int) -> int:
    """Return the 1-based shard that owns a test id."""
    return zlib.crc32(nodeid.encode("utf-8")) % count + 1


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Deselect every test that belongs to another shard."""
    spec: str | None = os.environ.get("F2W_SHARD")
    if not spec:
        return
    index, count = parse_shard(spec)
    keep = [item for item in items if shard_of(item.nodeid, count) == index]
    dropped = [item for item in items if shard_of(item.nodeid, count) != index]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep
