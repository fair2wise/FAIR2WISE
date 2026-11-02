"""Immutable resolution plans and activities with source-to-canonical links (U-10).

A :class:`ResolutionPlan` captures everything that determines what a resolution round produced
(prompt, schema, model, decoding, vocabulary revision, and both the imported donor commit and
the F2W commit). Its ``plan_id`` is a content hash, so one plan is one node. Activities record
counts and cost, where **unknown cost stays unknown** (``None``), never zero. Links connect each
source term to the canonical target it resolved to. Everything is written through the audit
recorder (durable) so the optional graph projection can lag without losing provenance.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_provenance.py (``ResolutionPlan``,
``write_plan``, ``write_activity``, ``link_resolved_terms``, ``git_sha``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .events import GraphWriteEvent
from .recorder import AuditRecorder

STAGE = "term_resolution"
_CODE_DIR = Path(__file__).resolve().parent


def git_sha(cwd: Path | None = None) -> str:
    """HEAD of *this* checkout (not the process's cwd), or ``""`` when git cannot say."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd or _CODE_DIR,
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


class ResolutionPlan(BaseModel):
    """Everything that determines what a resolution round produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str = STAGE
    prompt_sha256: str
    schema_sha256: str
    model_ref: str
    model_revision: str = ""
    decoding: dict[str, Any] = Field(default_factory=dict)
    vocabulary_revision: str = ""
    f2w_commit: str = ""
    donor_commit: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def plan_id(self) -> str:
        """Content address of the plan."""
        body = self.model_dump()
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def plan_node_id(plan_id: str) -> str:
    """Node id of a plan: its content address, so one plan is one node."""
    return f"plan:term_resolution_{plan_id[:32]}"


def activity_node_id(round_key: str) -> str:
    """Node id of a round's activity."""
    return "activity:term_resolution_" + hashlib.sha256(round_key.encode()).hexdigest()[:32]


def write_plan(recorder: AuditRecorder, plan: ResolutionPlan, *, actor: str, run_id: str | None = None) -> str:
    """Record the plan (idempotent by content) and return its node id."""
    node = plan_node_id(plan.plan_id)
    recorder.record(
        GraphWriteEvent.build(
            kind="provenance",
            phase="note",
            operation_id=node,
            actor=actor,
            run_id=run_id,
            target="provenance",
            payload={"node": node, "type": "plan", **plan.model_dump()},
        )
    )
    return node


def write_activity(
    recorder: AuditRecorder,
    *,
    round_key: str,
    plan: ResolutionPlan,
    started: float,
    finished: float,
    counts: Mapping[str, int],
    cost_usd: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    actor: str,
    run_id: str | None = None,
) -> str:
    """Record one resolution round. ``cost_usd=None`` means the cost is unknown (never recorded as 0)."""
    node = activity_node_id(round_key)
    recorder.record(
        GraphWriteEvent.build(
            kind="provenance",
            phase="note",
            operation_id=node,
            actor=actor,
            run_id=run_id,
            target="provenance",
            payload={
                "node": node,
                "type": "activity",
                "plan": plan_node_id(plan.plan_id),
                "round_key": round_key,
                "started": started,
                "finished": finished,
                "counts": dict(counts),
                "cost_usd": cost_usd,
                "cost_known": cost_usd is not None,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
    )
    return node


def link_resolved_terms(
    recorder: AuditRecorder,
    activity_id: str,
    targets: Mapping[str, str],
    *,
    actor: str,
    run_id: str | None = None,
) -> int:
    """Link each source term id to the canonical id it resolved to; returns the number of links."""
    for source, target in sorted(targets.items()):
        recorder.record(
            GraphWriteEvent.build(
                kind="provenance",
                phase="note",
                operation_id=f"{activity_id}:{source}",
                actor=actor,
                run_id=run_id,
                target="provenance",
                payload={"type": "resolved_term", "activity": activity_id, "source": source, "canonical": target},
            )
        )
    return len(targets)


__all__ = (
    "ResolutionPlan",
    "STAGE",
    "activity_node_id",
    "git_sha",
    "link_resolved_terms",
    "plan_node_id",
    "write_activity",
    "write_plan",
)
