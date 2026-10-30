"""T28C / AUD-08: the hash-locked requirement files agree with their version lists.

``requirements/runtime.lock.txt`` is what the images install with ``--require-hashes``;
``requirements/runtime.txt`` stays the developer and CI lock (pip would put a whole
``pip install -r requirements.txt -r requirements/dev.txt`` into hash-checking mode if
runtime.txt carried hashes, and semantic.in is unpinned by design). The same holds for the
Tiled server's ``constraints.txt`` and ``constraints.lock.txt``. These tests keep each pair
in step and every locked line hashed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from app.modules.deployment import tiled_config

REPO = Path(__file__).resolve().parents[2]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?==([^\s\\]+)((?:\s*\\\n\s*--hash=sha256:[0-9a-f]{64})*)", re.M)
PAIRS = [
    ("requirements/runtime.txt", "requirements/runtime.lock.txt", set()),
    ("docker/profiles/tiled-server/constraints.txt", "docker/profiles/tiled-server/constraints.lock.txt", {"tiled"}),
]


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def pins(rel: str) -> Dict[str, Tuple[str, int]]:
    """``{name: (version, hash_count)}`` for every ``name==version`` line."""
    text = (REPO / rel).read_text(encoding="utf-8")
    return {_norm(m.group(1)): (m.group(3), m.group(4).count("--hash=")) for m in PIN.finditer(text)}


def requirement_lines(rel: str) -> List[str]:
    """Non-comment, non-option requirement lines of a requirements file (continuations joined)."""
    joined = (REPO / rel).read_text(encoding="utf-8").replace("\\\n", " ")
    return [l.strip() for l in joined.splitlines() if l.strip() and not l.strip().startswith(("#", "-"))]


@pytest.mark.parametrize("versions, lock, lock_only", PAIRS)
def test_lock_pins_the_same_versions_as_its_version_list(versions, lock, lock_only):
    a, b = pins(versions), pins(lock)
    assert a, versions
    assert set(b) - set(a) == lock_only, f"{lock} pins packages {versions} does not list"
    assert set(a) - set(b) == set(), f"{versions} pins packages missing from {lock}"
    assert {k: v[0] for k, v in a.items()} == {k: b[k][0] for k in a}, "versions differ"


@pytest.mark.parametrize("_, lock, __", PAIRS)
def test_every_locked_requirement_is_pinned_and_hashed(_, lock, __):
    for line in requirement_lines(lock):
        assert "==" in line, f"{lock}: unpinned line {line!r}"
        assert "--hash=sha256:" in line, f"{lock}: no hash on {line!r}"
    for name, (version, count) in pins(lock).items():
        assert count >= 1, (lock, name, version)


def test_version_lists_carry_no_hashes():
    """runtime.txt and constraints.txt stay plain: hashes there would turn every chained
    `pip install -r requirements.txt ...` into hash-checking mode (see module docstring)."""
    for versions, _, _ in PAIRS:
        assert "--hash=" not in (REPO / versions).read_text(encoding="utf-8"), versions
    assert "--hash=" not in (REPO / "requirements/semantic.in").read_text(encoding="utf-8")


def test_tiled_lock_carries_the_owner_pinned_wheel_hash():
    lock = pins("docker/profiles/tiled-server/constraints.lock.txt")
    assert lock["tiled"][0] == tiled_config.PINNED_TILED_VERSION
    text = (REPO / "docker/profiles/tiled-server/constraints.lock.txt").read_text(encoding="utf-8")
    assert f"--hash=sha256:{tiled_config.PINNED_TILED_WHEEL_SHA256}" in text


def test_runtime_lock_is_the_runtime_in_resolution():
    """Every direct requirement of runtime.in is pinned in both runtime.txt and the lock."""
    direct = [re.split(r"[<>=!~\[;]", l)[0] for l in requirement_lines("requirements/runtime.in")]
    for name in direct:
        assert _norm(name) in pins("requirements/runtime.txt"), name
        assert _norm(name) in pins("requirements/runtime.lock.txt"), name
