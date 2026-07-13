"""Durable state for the canonical FAIR2WISE orchestration workflow.

The state file contains control-plane metadata only.  PDF text and prompts are
never persisted here; scientific claims must continue to come from retrieval or
from :class:`PaperEvidenceAgent` reading an approved PDF on demand.
"""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


STATE_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_workflow_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "current_query": "",
        "current_topic": "",
        "phase": "idle",
        "last_route": None,
        "candidates": [],
        "unavailable_candidate_indices": [],
        "active_paper": None,
        "approved_action": None,
        "extraction": None,
        "post_extraction_sufficient": None,
        "pending": None,
        "round_no": 0,
        "orchestration_steps": 0,
        "updated_at": None,
    }


class WorkflowStateStore:
    """JSON-backed workflow state with atomic replacement writes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        state = default_workflow_state()
        if not self.path.exists():
            return state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return state
        if not isinstance(raw, dict):
            return state
        state.update(raw)
        if not isinstance(state.get("candidates"), list):
            state["candidates"] = []
        if not isinstance(state.get("unavailable_candidate_indices"), list):
            state["unavailable_candidate_indices"] = []
        if not isinstance(state.get("pending"), (dict, type(None))):
            state["pending"] = None
        state["version"] = STATE_VERSION
        return state

    def snapshot(self) -> Dict[str, Any]:
        return deepcopy(self.data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["version"] = STATE_VERSION
        self.data["updated_at"] = _now()
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def update(self, **values: Any) -> Dict[str, Any]:
        self.data.update(values)
        self.save()
        return self.snapshot()

    def clear(self) -> None:
        self.data = default_workflow_state()
        self.save()

    @property
    def pending(self) -> Optional[Dict[str, Any]]:
        value = self.data.get("pending")
        return deepcopy(value) if isinstance(value, dict) else None

