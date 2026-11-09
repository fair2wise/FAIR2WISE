"""Durable scoped edit journal: the contract only (T08B; json_kg_contract.md section 6.2).

T18C builds the edit projection and routes; this module fixes what a journal record is, how it is
stored durably *before* an edit is acknowledged, and the reconciliation hook the importer calls so
that an acknowledged edit is never silently lost by a later import (plan 4.11: "no edit may be
acknowledged and then lost by the next import").

Lifecycle of an entry: ``recorded`` -> ``applied`` (scoped Tiled effect done) -> ``acknowledged``
(the caller was told) -> ``reconciled`` (carried into snapshot ``reconciled_in``). An entry may also
become ``conflict`` or ``resolved`` (a reviewed :class:`ConflictResolution` was attached).

Q7/Q12 (owner, 2026-10-30): a Save performs its multi-write sequence on Tiled immediately and is
acknowledged only after the Tiled effect. A sequence that stops half-way marks the entry ``failed``;
the recovery job (:mod:`.imports.save_recovery`) then finishes it (``applied``) or undoes it
(``reverted``, the pre-Save state). A ``failed`` entry is protected like an acknowledged one: no import
may paper over an edit whose Tiled effect is unresolved.

The store is an append-only JSONL file, one file per (target, scope); every append is fsynced, and
the current state of an edit is its last record (state changes append, never rewrite).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ports import ContractError, ErrorCode

OPERATIONS = ("node_patch", "relationship_add", "relationship_remove", "snippet_upsert", "snippet_unlink")
State = Literal["recorded", "applied", "acknowledged", "reconciled", "conflict", "resolved", "failed", "reverted"]
#: States in which an edit exists in the target but is not yet part of any JSON snapshot.
OPEN_STATES = frozenset({"recorded", "applied", "acknowledged", "conflict", "failed"})
#: A ``recorded``-only edit was never applied or acknowledged; it cannot be "lost" by an import. A
#: ``reverted`` edit has no Tiled effect either. A ``failed`` edit's effect is unknown until recovered.
PROTECTED_STATES = frozenset({"applied", "acknowledged", "conflict", "failed"})


class EditRecord(BaseModel):
    """One journal record (one state of one edit).

    Attributes:
        edit_id: Stable id of the edit.
        seq: Per-edit monotonically increasing record number.
        state: Acknowledgement state at this record.
        target: Normalized Tiled target (with optional ``#namespace``).
        scope: Corpora the edit is scoped to.
        base_snapshot_id / base_graph_sha256: Snapshot revision the edit was made against.
        profile / mode: Deployment profile and authorization mode (``local``/``local_trusted``...).
        actor: ``local-operator`` or a verified admin subject.
        operation: One of :data:`OPERATIONS`.
        ids: Stable JSON ids touched (node ids or edge ids).
        before / after: Field-level values, or hashes for protected text.
        recorded_at: Time of this record.
        reconciled_in: Snapshot id that carries the edit (``reconciled``/``resolved``).
        note: Free text (conflict/resolution reason).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    edit_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    state: State
    target: str
    scope: tuple[str, ...]
    base_snapshot_id: str
    base_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: str
    mode: str
    actor: str
    operation: Literal["node_patch", "relationship_add", "relationship_remove", "snippet_upsert", "snippet_unlink"]
    ids: tuple[str, ...]
    before: dict[str, object] = Field(default_factory=dict)
    after: dict[str, object] = Field(default_factory=dict)
    recorded_at: datetime
    reconciled_in: str | None = None
    note: str | None = None


class ConflictResolution(BaseModel):
    """A reviewed decision about one unreconciled edit, attached to an import plan.

    ``decision`` is ``carried`` (the importing snapshot already contains the edit's effect, reviewer
    confirmed) or ``discarded`` (the edit is intentionally dropped). Neither is implicit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    edit_id: str
    decision: Literal["carried", "discarded"]
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    note: str = Field(min_length=1)


def resolutions_sha256(resolutions: Iterable[ConflictResolution]) -> str:
    """Hash bound into the import plan so a plan changes when its reviewed resolutions change."""
    rows = sorted(json.dumps(r.model_dump(mode="json"), sort_keys=True) for r in resolutions)
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def derive_edit_id(*, target: str, scope: Sequence[str], base_snapshot_id: str, operation: str,
                   ids: Sequence[str], before: dict[str, object], after: dict[str, object]) -> str:
    """Content-derived edit id (T23C): the same request content always yields the same id.

    A client retry of a Save (after a lost response) therefore finds its own entry instead of recording a
    second edit. The id covers target, scope, base snapshot, operation, touched ids and the before/after
    values, never the actor or the time.
    """
    body = json.dumps({"t": target, "s": sorted(scope), "b": base_snapshot_id, "o": operation, "i": list(ids),
                       "x": before, "y": after}, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return "e-" + hashlib.sha256(body.encode()).hexdigest()[:24]


class EditJournal:
    """Append-only JSONL journal for one ``(target, scope)``.

    Args:
        path: Journal file; created on first append.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> list[EditRecord]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    out.append(EditRecord.model_validate_json(line))
                except ValueError:
                    raise ContractError(ErrorCode.INTERNAL, f"edit journal line {n} is corrupt") from None
        return out

    def append(self, record: EditRecord) -> EditRecord:
        """Durably append (fsync) before the caller acknowledges anything."""
        current = self.latest().get(record.edit_id)
        if current is not None and record.seq != current.seq + 1:
            raise ContractError(ErrorCode.CONFLICT, f"edit {record.edit_id} expects seq {current.seq + 1}")
        if current is None and record.seq != 1:
            raise ContractError(ErrorCode.CONFLICT, f"edit {record.edit_id} must start at seq 1")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def record(self, record: EditRecord) -> tuple[EditRecord, bool]:
        """Idempotent first append of a content-derived edit (``seq`` 1).

        Returns ``(current_record, created)``; when the edit id is already journaled (a retried Save) the
        existing current record is returned and nothing is written.
        """
        current = self.latest().get(record.edit_id)
        if current is not None:
            return current, False
        return self.append(record), True

    def latest(self) -> dict[str, EditRecord]:
        """Current record of every edit."""
        return {r.edit_id: r for r in self._read()}

    def advance(self, edit_id: str, state: State, at: datetime, *, reconciled_in: str | None = None,
                note: str | None = None) -> EditRecord:
        """Append the next state of an existing edit."""
        current = self.latest().get(edit_id)
        if current is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"no edit {edit_id!r}")
        return self.append(current.model_copy(update={"seq": current.seq + 1, "state": state, "recorded_at": at,
                                                      "reconciled_in": reconciled_in or current.reconciled_in,
                                                      "note": note if note is not None else current.note}))

    def sha256(self) -> str:
        """Hash of the journal bytes (``edit_reconciliation.journal_sha256`` of a new snapshot)."""
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b"").hexdigest()


def unreconciled(journal: EditJournal, *, target: str, scope: Sequence[str],
                 lineage: Iterable[str], include_reconciled: bool = True) -> list[EditRecord]:
    """List edits for this target/scope that the imported snapshot would not carry.

    * Applied, acknowledged, conflicting and failed edits are listed unless the snapshot carrying them
      (``reconciled_in``) is in ``lineage``.
    * T23C (F4/B26, Q15): a ``reconciled`` edit is listed when its ``reconciled_in`` snapshot is not in
      ``lineage``, so importing a snapshot *below* the reconciliation (a rollback) is refused without a
      reviewed :class:`ConflictResolution`. ``lineage`` must be the full ancestor chain of the imported
      snapshot, nearest first (``SourceSnapshot.lineage``). ``include_reconciled=False`` restores the
      pre-T23C view (open edits only), used where JSON in Git already carries reconciled edits (corpus
      removal).
    """
    ancestry = set(lineage)
    wanted = set(scope)
    states = PROTECTED_STATES | ({"reconciled"} if include_reconciled else frozenset())
    out = []
    for rec in journal.latest().values():
        if rec.target != target or not (set(rec.scope) & wanted) or rec.state not in states:
            continue
        out.append(rec)
    # A ``conflict`` edit's effect is by definition not in the graph: it blocks until resolved.
    return [r for r in out if r.state == "conflict" or r.reconciled_in not in ancestry]


def check_import(journal: EditJournal | None, *, target: str, scope: Sequence[str], lineage: Iterable[str],
                 resolutions: Sequence[ConflictResolution] = ()) -> list[EditRecord]:
    """Reconciliation hook for the importer: refuse when an acknowledged edit would be lost.

    Returns the edits covered by reviewed resolutions (for the plan record).

    Raises:
        ContractError: ``CONFLICT`` naming the edit ids that have neither been reconciled into the
            imported lineage nor carry a reviewed :class:`ConflictResolution`.
    """
    if journal is None:
        return []
    open_edits = unreconciled(journal, target=target, scope=scope, lineage=lineage)
    resolved: dict[str, ConflictResolution] = {r.edit_id: r for r in resolutions}
    missing = sorted(r.edit_id for r in open_edits if r.edit_id not in resolved)
    if missing:
        raise ContractError(ErrorCode.CONFLICT,
                            f"import would lose {len(missing)} acknowledged edit(s) not reconciled into the "
                            f"snapshot lineage: {missing[:10]}; reconcile into a new snapshot or attach a "
                            "reviewed conflict resolution", retryable=False)  # needs a human, not a retry
    return [r for r in open_edits]
