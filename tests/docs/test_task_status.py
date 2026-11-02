"""T28D (AUD-14): the task status overlay covers the companion task graph and names real merges.

``docs/migration/companion/task_graph.json`` stays byte-for-byte as supplied, so task progress
lives in ``docs/migration/task_status.json``. Every task id of the graph must appear there, and
every entry marked ``merged`` must point at a first-parent commit of the integration branch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TASK_GRAPH = REPO / "docs" / "migration" / "companion" / "task_graph.json"
STATUS = REPO / "docs" / "migration" / "task_status.json"
BASELINE = "222b1e7b130567dbf4a3242697ac2b465794a38d"
STATUSES = {"merged", "open", "blocked_on_owner", "superseded"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_parent_log() -> dict[str, str] | None:
    """Full hash -> subject for ``git log --first-parent BASELINE..HEAD``; ``None`` outside a checkout."""
    try:
        done = subprocess.run(
            ["git", "log", "--first-parent", "--format=%H%x09%s", f"{BASELINE}..HEAD"],
            cwd=REPO, capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    out: dict[str, str] = {}
    for line in done.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        out[sha] = subject
    return out


def test_every_task_graph_id_has_a_status_entry():
    graph_ids = {task["id"] for task in _load(TASK_GRAPH)["tasks"]}
    status = _load(STATUS)
    entries = {entry["id"]: entry for entry in status["tasks"]}
    assert graph_ids <= set(entries), sorted(graph_ids - set(entries))
    assert len(entries) == len(status["tasks"]), "duplicate task ids in task_status.json"
    for entry in status["tasks"]:
        assert entry["status"] in STATUSES, entry["id"]
        assert entry["gate"] in {"H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"}, entry["id"]
        if entry["status"] == "merged":
            assert isinstance(entry["merge_commit"], str) and len(entry["merge_commit"]) == 40, entry["id"]
        else:
            assert entry["merge_commit"] is None, entry["id"]
            assert entry.get("note"), entry["id"]


def test_the_companion_task_graph_is_untouched():
    """The overlay exists because the companion bundle is frozen; it must still say not_started."""
    assert {task["status"] for task in _load(TASK_GRAPH)["tasks"]} == {"not_started"}


def test_every_merged_task_names_a_first_parent_commit():
    log = _first_parent_log()
    if log is None:
        pytest.skip("not a git checkout with the forge_merge history")
    status = _load(STATUS)
    assert status["baseline_commit"] == BASELINE
    missing = [entry["id"] for entry in status["tasks"]
               if entry["status"] == "merged" and entry["merge_commit"] not in log]
    assert missing == [], missing
    for entry in status["tasks"]:
        if entry["status"] == "merged":
            assert entry["merge_commit_subject"] == log[entry["merge_commit"]], entry["id"]
