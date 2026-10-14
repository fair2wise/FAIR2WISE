"""Versioned vocabulary-quality snapshots (C-16): reports kept as history, never overwritten.

A snapshot is a small JSON summary of a quality report (and optionally a plan and an audit) named by
its timestamp and content hash. Candidate-term counts are kept separate from canonical entity counts.
The donor's background worker loop is not ported (deferred): callers schedule :func:`write_snapshot`
themselves, and :func:`snapshot_history` caps how much it reads so the footprint is bounded.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocab_quality_snapshot.py (BSD-3-Clause).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .migration import sha256_json

HISTORY_CAP = 200


def summarise(
    report: Dict[str, Any], plan: Optional[Dict[str, Any]] = None, audit: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """The compact summary stored per snapshot (counts only; candidate vs canonical kept apart)."""
    return {
        "candidate": {
            "nodes": report.get("candidate_nodes"),
            "terms": report.get("candidate_terms"),
            "by_class": report.get("by_class", {}),
        },
        "canonical": {"vocabulary_entries": report.get("canonical_vocabulary_entries")},
        "source_revision": report.get("source_revision"),
        "vocabulary_revision": report.get("vocabulary_revision"),
        "plan": None if plan is None else {"operations": plan.get("operations"), "held": plan.get("held")},
        "audit": (
            None if audit is None else {"clean": audit.get("clean"), "findings": len(audit.get("findings", []))}
        ),
    }


def write_snapshot(directory: Path, summary: Dict[str, Any], *, taken_at: datetime) -> Path:
    """Write ``quality_<timestamp>_<hash>.json``; an existing identical snapshot is left as is."""
    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256_json(summary)[:12]
    path = directory / f"quality_{taken_at.strftime('%Y%m%dT%H%M%S')}_{digest}.json"
    if not path.exists():
        path.write_text(
            json.dumps({"taken_at": taken_at.isoformat(), "summary": summary}, sort_keys=True, indent=1),
            encoding="utf-8",
        )
    return path


def snapshot_history(directory: Path, *, limit: int = 30) -> List[Dict[str, Any]]:
    """Newest-first snapshots, reading at most ``min(limit, HISTORY_CAP)`` files."""
    files = sorted(directory.glob("quality_*.json"), reverse=True)[: max(0, min(limit, HISTORY_CAP))]
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]
