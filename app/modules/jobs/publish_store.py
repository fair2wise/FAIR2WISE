"""Operational tables of the publication job: plans, approvals, receipts, quarantine and the active pointer.

All state here is operational (not the scientific KG) and none of it can alter a frozen snapshot:
the snapshot files are outside this database and a failed or quarantined publication only adds rows.

The **active pointer** names the verified (snapshot, receipt, index) triple served for a target.
It moves only by compare-and-swap on its version, only to a triple whose receipt and index were
verified, and every move is kept in history so a rollback restores the previous pointer without
rebuilding anything. Until a new triple is activated the old one stays in place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any, cast

from ..contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from ..contracts.errors import ContractError, ErrorCode
from .keys import canonical_json
from .sqlite import OperationalDB

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pub_plans (plan_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, plan TEXT NOT NULL, "
    "schema_fingerprint TEXT NOT NULL, vocabulary_revision TEXT NOT NULL, created REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS pub_approvals (approval_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, "
    "plan_id TEXT NOT NULL, plan_sha256 TEXT NOT NULL, record TEXT NOT NULL, requested_by TEXT NOT NULL, "
    "approved_by TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
    "created REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS pub_receipts (receipt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, "
    "plan_sha256 TEXT NOT NULL, target TEXT NOT NULL, receipt TEXT NOT NULL, created REAL NOT NULL, "
    "UNIQUE(plan_sha256, target));\n",
    "CREATE TABLE IF NOT EXISTS pub_quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
    "subject TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS pub_pointers (name TEXT PRIMARY KEY, version INTEGER NOT NULL, record TEXT "
    "NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS pub_pointer_history (name TEXT NOT NULL, version INTEGER NOT NULL, record "
    "TEXT NOT NULL, "
    "action TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(name, "
    "version));\n",
)


@dataclass(frozen=True)
class PlanRecord:
    """A plan with the revisions it was computed under (what an approval is bound to)."""

    plan: ChangePlan
    schema_fingerprint: str
    vocabulary_revision: str
    job_id: str


@dataclass(frozen=True)
class Pointer:
    """The active publication for a target."""

    name: str
    version: int
    snapshot_ref: str
    snapshot_sha256: str
    receipt_id: str
    index_ref: str

    def as_dict(self) -> dict[str, Any]:
        """Return the pointer fields as a plain dict."""
        return {
            "snapshot_ref": self.snapshot_ref,
            "snapshot_sha256": self.snapshot_sha256,
            "receipt_id": self.receipt_id,
            "index_ref": self.index_ref,
        }


class PublishStore:
    """Plans, approvals, receipts, quarantine and pointers over one operational database."""

    def __init__(self, db: OperationalDB, *, clock: Callable[[], float]) -> None:
        self.db = db
        self.clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    # --- plans -----------------------------------------------------------------------------

    def save_plan(
        self, job_id: str, plan: ChangePlan, *, schema_fingerprint: str, vocabulary_revision: str
    ) -> None:
        """Record the plan with the revisions in force when it was computed."""
        with self.db.tx() as con:
            con.execute(
                "INSERT OR REPLACE INTO pub_plans VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    job_id,
                    plan.model_dump_json(),
                    schema_fingerprint,
                    vocabulary_revision,
                    self.clock(),
                ),
            )

    def current_plan(self, job_id: str) -> PlanRecord | None:
        """The most recent plan of a job."""
        row = self.db.one("SELECT * FROM pub_plans WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (job_id,))
        if row is None:
            return None
        return PlanRecord(
            ChangePlan.model_validate_json(row["plan"]),
            row["schema_fingerprint"],
            row["vocabulary_revision"],
            job_id,
        )

    # --- approvals -------------------------------------------------------------------------

    def record_approval(self, job_id: str, approval: ApprovalRecord) -> None:
        """Store an approval for the job's plan (valid until invalidated)."""
        with self.db.tx() as con:
            con.execute(
                "INSERT OR REPLACE INTO pub_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'valid', '', ?)",
                (
                    approval.approval_id,
                    job_id,
                    approval.plan_id,
                    approval.plan_sha256,
                    approval.model_dump_json(),
                    approval.requested_by,
                    approval.approved_by,
                    canonical_json(list(approval.scope)),
                    self.clock(),
                ),
            )

    def valid_approvals(self, job_id: str, plan: ChangePlan) -> list[ApprovalRecord]:
        """Approvals still valid for exactly this plan hash."""
        rows = self.db.query(
            "SELECT record FROM pub_approvals WHERE job_id=? AND plan_sha256=? AND status='valid' ORDER BY "
            "created",
            (job_id, plan.plan_sha256),
        )
        return [ApprovalRecord.model_validate_json(r["record"]) for r in rows]

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """An approval by id whatever its status (a consumed approval still documents what was imported)."""
        row = self.db.one("SELECT record FROM pub_approvals WHERE approval_id=?", (approval_id,))
        return ApprovalRecord.model_validate_json(row["record"]) if row else None

    def consume_approval(self, approval_id: str) -> None:
        """Mark an approval used once its plan has a verified receipt."""
        with self.db.tx() as con:
            con.execute(
                "UPDATE pub_approvals SET status='consumed' WHERE approval_id=? AND status='valid'", (approval_id,)
            )

    def invalidate_approvals(
        self,
        *,
        reason: str,
        job_id: str | None = None,
        subject: str | None = None,
        corpora: Iterable[str] = (),
    ) -> int:
        """Invalidate valid approvals by job, by requester or approver, or by corpus overlap."""
        corpora_set = set(corpora)
        count = 0
        with self.db.tx() as con:
            for row in con.execute(
                "SELECT approval_id, job_id, requested_by, approved_by, scope FROM pub_approvals "
                "WHERE status='valid'"
            ).fetchall():
                hit = (
                    (job_id is not None and row["job_id"] == job_id)
                    or (subject is not None and subject in (row["requested_by"], row["approved_by"]))
                    or (corpora_set and corpora_set & set(json.loads(row["scope"])))
                )
                if hit:
                    con.execute(
                        "UPDATE pub_approvals SET status='invalidated', reason=? WHERE approval_id=?",
                        (reason[:300], row["approval_id"]),
                    )
                    count += 1
        return count

    def approval_status(self, approval_id: str) -> tuple[str, str] | None:
        """``(status, reason)`` of an approval."""
        row = self.db.one("SELECT status, reason FROM pub_approvals WHERE approval_id=?", (approval_id,))
        return (row["status"], row["reason"]) if row else None

    # --- receipts --------------------------------------------------------------------------

    def save_receipt(self, job_id: str, receipt: ImportReceipt) -> ImportReceipt:
        """Idempotently store a verified receipt; an existing receipt for (plan, target) wins."""
        if not receipt.verified:
            raise ContractError(ErrorCode.INVALID_INPUT, "only a verified receipt can be stored")
        with self.db.tx() as con:
            existing = con.execute(
                "SELECT receipt FROM pub_receipts WHERE plan_sha256=? AND target=?",
                (receipt.plan_sha256, receipt.target),
            ).fetchone()
            if existing is not None:
                return ImportReceipt.model_validate_json(existing["receipt"])
            con.execute(
                "INSERT INTO pub_receipts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt.receipt_id,
                    job_id,
                    receipt.plan_sha256,
                    receipt.target,
                    receipt.model_dump_json(),
                    self.clock(),
                ),
            )
        return receipt

    def receipt_for(self, plan_sha256: str, target: str) -> ImportReceipt | None:
        """The receipt of a plan on a target (receipts are valid only for the target they name)."""
        row = self.db.one(
            "SELECT receipt FROM pub_receipts WHERE plan_sha256=? AND target=?", (plan_sha256, target)
        )
        return ImportReceipt.model_validate_json(row["receipt"]) if row else None

    # --- quarantine ------------------------------------------------------------------------

    def quarantine(self, job_id: str, subject: str, reason: str, detail: dict[str, Any] | None = None) -> None:
        """Record why a package, snapshot or step output must not be published."""
        with self.db.tx() as con:
            con.execute(
                "INSERT INTO pub_quarantine (job_id, subject, reason, detail, created) VALUES (?, ?, ?, ?, ?)",
                (job_id, subject, reason[:300], canonical_json(detail or {}), self.clock()),
            )

    def quarantined(self, job_id: str | None = None) -> list[dict[str, Any]]:
        """Quarantine entries, newest last."""
        sql, params = "SELECT * FROM pub_quarantine", cast(tuple[str, ...], ())
        if job_id is not None:
            sql, params = sql + " WHERE job_id=?", (job_id,)
        return [
            {**dict(r), "detail": json.loads(r["detail"])} for r in self.db.query(sql + " ORDER BY id", params)
        ]

    # --- active pointer --------------------------------------------------------------------

    def pointer(self, name: str) -> Pointer | None:
        """The active pointer for ``name`` (a target), or ``None`` before the first activation."""
        row = self.db.one("SELECT version, record FROM pub_pointers WHERE name=?", (name,))
        if row is None:
            return None
        return Pointer(name, row["version"], **json.loads(row["record"]))

    def activate(
        self,
        name: str,
        *,
        snapshot_ref: str,
        snapshot_sha256: str,
        receipt: ImportReceipt,
        index_ref: str,
        expected_version: int,
        actor: str,
        reason: str = "activate",
        action: str = "activate",
    ) -> Pointer:
        """Switch the pointer by compare-and-swap, only to a verified receipt.

        Raises:
            ContractError: ``INVALID_INPUT`` for an unverified receipt or one for another target/snapshot;
                ``CONFLICT`` when the pointer moved since ``expected_version`` was read.
        """
        if not receipt.verified or receipt.target != name or receipt.snapshot_sha256 != snapshot_sha256:
            raise ContractError(
                ErrorCode.INVALID_INPUT, "pointer may only move to a verified receipt for this target"
            )
        record = {
            "snapshot_ref": snapshot_ref,
            "snapshot_sha256": snapshot_sha256,
            "receipt_id": receipt.receipt_id,
            "index_ref": index_ref,
        }
        with self.db.tx() as con:
            row = con.execute("SELECT version, record FROM pub_pointers WHERE name=?", (name,)).fetchone()
            current = row["version"] if row else 0
            if row is not None and json.loads(row["record"]) == record:
                return Pointer(name, current, **record)  # already active: crash after the switch is harmless
            if current != expected_version:
                raise ContractError(
                    ErrorCode.CONFLICT, "active pointer changed since the job read it", retryable=True
                )
            last = con.execute(
                "SELECT MAX(version) AS v FROM pub_pointer_history WHERE name=?", (name,)
            ).fetchone()
            version = max(current, int(last["v"] or 0)) + 1
            con.execute(
                "INSERT OR REPLACE INTO pub_pointers VALUES (?, ?, ?)", (name, version, canonical_json(record))
            )
            con.execute(
                "INSERT INTO pub_pointer_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, version, canonical_json(record), action, reason[:300], actor, self.clock()),
            )
        return Pointer(name, version, **record)

    def previous_record(self, name: str) -> dict[str, Any] | None:
        """The pointer record one step back (what a rollback toggle restores), or ``None``.

        The toggle is over the last two pointers (owner decision B28): after a rollback, asking again names the
        pointer that was rolled back from, so a second rollback is a roll-forward.
        """
        row = self.db.one("SELECT version FROM pub_pointers WHERE name=?", (name,))
        if row is None:
            return None
        hist = self.db.one(
            "SELECT record FROM pub_pointer_history WHERE name=? AND version<? ORDER BY version DESC LIMIT 1",
            (name, row["version"]),
        )
        return cast(dict[str, Any], json.loads(hist["record"])) if hist else None

    def rollback(self, name: str, *, actor: str, reason: str) -> Pointer:
        """Restore the previous pointer (new candidate packages, receipts and history are retained)."""
        with self.db.tx() as con:
            row = con.execute("SELECT version FROM pub_pointers WHERE name=?", (name,)).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, "no active pointer to roll back")
            history = con.execute(
                "SELECT version, record FROM pub_pointer_history WHERE name=? AND version<? ORDER BY version DESC "
                "LIMIT 1",
                (name, row["version"]),
            ).fetchone()
            if history is None:
                raise ContractError(ErrorCode.CONFLICT, "there is no previous pointer to restore")
            version = row["version"] + 1
            con.execute(
                "UPDATE pub_pointers SET version=?, record=? WHERE name=?", (version, history["record"], name)
            )
            con.execute(
                "INSERT INTO pub_pointer_history VALUES (?, ?, ?, 'rollback', ?, ?, ?)",
                (name, version, history["record"], reason[:300], actor, self.clock()),
            )
        return Pointer(name, version, **json.loads(history["record"]))

    def clear_pointer(self, name: str, *, actor: str, reason: str) -> Pointer | None:
        """Point ``name`` at nothing after a corpus removal (T08D); history keeps the retired record.

        Returns the pointer that was active, or ``None`` when nothing was (then nothing is recorded). A later
        publication starts again at version ``history + 1``; ``rollback`` needs an active pointer and so
        cannot undo a removal: re-import from the committed JSON instead.
        """
        with self.db.tx() as con:
            row = con.execute("SELECT version, record FROM pub_pointers WHERE name=?", (name,)).fetchone()
            if row is None:
                return None
            version = row["version"] + 1
            con.execute("DELETE FROM pub_pointers WHERE name=?", (name,))
            con.execute(
                "INSERT INTO pub_pointer_history VALUES (?, ?, ?, 'retire', ?, ?, ?)",
                (name, version, row["record"], reason[:300], actor, self.clock()),
            )
        return Pointer(name, row["version"], **json.loads(row["record"]))

    def pointer_history(self, name: str) -> list[dict[str, Any]]:
        """All pointer movements for ``name``."""
        return [
            {**dict(r), "record": json.loads(r["record"])}
            for r in self.db.query("SELECT * FROM pub_pointer_history WHERE name=? ORDER BY version", (name,))
        ]


def utc(moment: float) -> datetime:
    """Epoch seconds as an aware UTC datetime."""
    return datetime.fromtimestamp(moment, tz=UTC)


__all__ = ("PlanRecord", "Pointer", "PublishStore", "SCHEMA", "utc")
