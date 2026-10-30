"""SQLite store of uncertainty cases, attempts, reviewer decisions and cost approvals (U-01).

This is operational state, not a competing scientific KG. Every case carries its corpus and
every read/claim is scoped by the caller's corpora (tenant scoping). A claim returns a lease
*token*: later mutations present it, so a worker whose lease expired and was re-claimed cannot
record an attempt or release the case (recovery after worker failure).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/scientific_artifacts.py
(``ScientificArtifactStore``: ``enqueue``, ``claim``, ``route_case``, ``release``,
``record_attempt``, ``list_cases``, ``summary``) (BSD-3-Clause). The donor's budget methods live in
:mod:`.budgets`; the CBorg-specific grouping SQL is generalized to a route and group key.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..contracts.errors import ContractError, ErrorCode
from ..jobs.sqlite import OperationalDB
from .budgets import BudgetLedger
from .models import CaseState, ResolutionAttempt, UncertaintyCase

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, corpus TEXT NOT NULL, publication_id TEXT NOT "
    "NULL, "
    "document_hash TEXT NOT NULL, kind TEXT NOT NULL, locator TEXT NOT NULL, content_hash TEXT NOT NULL, "
    "path TEXT, metadata TEXT NOT NULL, created REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS uncertainty_cases (id TEXT PRIMARY KEY, corpus TEXT NOT NULL, "
    "publication_id TEXT NOT NULL, document_hash TEXT NOT NULL, source_kind TEXT NOT NULL, "
    "source_locator TEXT NOT NULL, case_type TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, "
    "payload TEXT NOT NULL, priority INTEGER NOT NULL, vocabulary_revision TEXT NOT NULL DEFAULT '', "
    "schema_revision TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL, state TEXT NOT NULL, "
    "route TEXT NOT NULL DEFAULT 'triage', group_key TEXT NOT NULL DEFAULT '', owner TEXT, lease_token TEXT, "
    "lease_until REAL NOT NULL DEFAULT 0, retry_after REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL "
    "DEFAULT 0, "
    "proposal_attempt_id TEXT, resolution TEXT, resolved_by TEXT, needs_validation INTEGER NOT NULL DEFAULT 0, "
    "reason_detail TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);\n",
    "CREATE INDEX IF NOT EXISTS uncertainty_work ON uncertainty_cases(corpus, state, retry_after, priority);\n",
    "CREATE TABLE IF NOT EXISTS resolution_attempts (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, corpus TEXT "
    "NOT NULL, "
    "provider TEXT NOT NULL, requested_model TEXT NOT NULL, executed_model TEXT NOT NULL, "
    "request_fingerprint TEXT NOT NULL, outcome TEXT NOT NULL, response TEXT NOT NULL, validation TEXT NOT NULL, "
    "cost_usd REAL, cost_known INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER, created REAL "
    "NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS cost_approvals (approval_id TEXT PRIMARY KEY, record TEXT NOT NULL, "
    "status TEXT NOT NULL, created REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS case_events (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, corpus TEXT NOT NULL, "
    "event_type TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);\n",
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class LeaseNotOwned(ContractError):
    """The case lease was lost (expired and re-claimed) or never held."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.CONFLICT, "uncertainty case lease is no longer owned", retryable=False)


class CurationStore:
    """Cases, attempts, approvals and the budget ledger over one operational database."""

    def __init__(
        self, path: Any = ":memory:", *, clock: Callable[[], float] = time.time, db: Optional[OperationalDB] = None
    ) -> None:
        self.db = db or OperationalDB(path)
        self.clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))
        self.ledger = BudgetLedger(self.db, clock=clock)

    # --- artifacts ---------------------------------------------------------------------

    def register_artifact(
        self,
        *,
        corpus: str,
        publication_id: str,
        document_hash: str,
        kind: str,
        locator: str,
        content_hash: str,
        path: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Idempotently register an artifact identity (the bytes stay in the content-addressed store)."""
        artifact_id = hashlib.sha256(
            _json([corpus, document_hash, kind, locator, content_hash]).encode()
        ).hexdigest()
        with self.db.tx() as con:
            con.execute(
                "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    corpus,
                    publication_id,
                    document_hash,
                    kind,
                    locator,
                    content_hash,
                    path or None,
                    _json(metadata or {}),
                    self.clock(),
                ),
            )
        return artifact_id

    # --- cases -------------------------------------------------------------------------

    @staticmethod
    def _row(row: Any) -> Dict[str, Any]:
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        out["resolution"] = json.loads(out["resolution"]) if out["resolution"] else None
        return out

    def enqueue(self, case: UncertaintyCase) -> Tuple[str, bool]:
        """Add a case once.

        Re-enqueueing with a changed revision (new evidence, vocabulary or schema)
        resets it to ``pending`` and discards any earlier decision as stale.

        Returns:
            ``(case_id, created_or_reset)``.
        """
        now = self.clock()
        with self.db.tx() as con:
            row = con.execute("SELECT revision, state FROM uncertainty_cases WHERE id=?", (case.id,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO uncertainty_cases (id, corpus, publication_id, document_hash, source_kind, "
                    "source_locator, case_type, reason, evidence, payload, priority, vocabulary_revision, "
                    "schema_revision, revision, state, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        case.id,
                        case.corpus,
                        case.publication_id,
                        case.document_hash,
                        case.source_kind,
                        case.source_locator,
                        case.case_type,
                        case.reason,
                        case.evidence,
                        _json(case.payload),
                        case.priority,
                        case.vocabulary_revision,
                        case.schema_revision,
                        case.revision,
                        now,
                        now,
                    ),
                )
                return case.id, True
            if row["revision"] == case.revision:
                return case.id, False
            con.execute(
                "UPDATE uncertainty_cases SET evidence=?, vocabulary_revision=?, schema_revision=?, revision=?, "
                "state='pending', route='triage', owner=NULL, lease_token=NULL, lease_until=0, "
                "proposal_attempt_id=NULL, resolution=NULL, resolved_by=NULL, needs_validation=0, updated=? "
                "WHERE id=?",
                (case.evidence, case.vocabulary_revision, case.schema_revision, case.revision, now, case.id),
            )
            self._event(
                con,
                case.id,
                case.corpus,
                "revision_changed",
                {"previous_state": row["state"], "decision_invalidated": True},
            )
            return case.id, True

    def _event(self, con: Any, case_id: str, corpus: str, event_type: str, payload: Dict[str, Any]) -> None:
        con.execute(
            "INSERT INTO case_events VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, case_id, corpus, event_type, _json(payload), self.clock()),
        )

    def claim(
        self,
        owner: str,
        corpora: Iterable[str],
        *,
        limit: int = 1,
        lease_seconds: float = 300.0,
        route: Optional[str] = None,
        case_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Atomically lease ready cases (priority order) within ``corpora`` only."""
        scope = sorted(set(corpora))
        if limit < 1 or not scope:
            return []
        now = self.clock()
        marks = ",".join("?" for _ in scope)
        extra, params = "", []
        if route is not None:
            extra += " AND route=?"
            params.append(route)
        if case_type is not None:
            extra += " AND case_type=?"
            params.append(case_type)
        with self.db.tx() as con:
            rows = con.execute(
                f"SELECT id FROM uncertainty_cases WHERE corpus IN ({marks}) "  # noqa: S608
                "AND ((state IN ('pending','retry_wait') AND retry_after<=?) OR (state='leased' AND "
                "lease_until<?))" + extra + " ORDER BY priority DESC, created, id LIMIT ?",
                (*scope, now, now, *params, limit),
            ).fetchall()
            token = uuid.uuid4().hex
            ids = [r["id"] for r in rows]
            for case_id in ids:
                con.execute(
                    "UPDATE uncertainty_cases SET state='leased', owner=?, lease_token=?, lease_until=?, "
                    "attempts=attempts+1, updated=? WHERE id=?",
                    (owner, token, now + lease_seconds, now, case_id),
                )
            leased = [con.execute("SELECT * FROM uncertainty_cases WHERE id=?", (i,)).fetchone() for i in ids]
        return [self._row(r) for r in leased]

    def _owned(self, con: Any, case_id: str, token: str) -> Any:
        row = con.execute("SELECT * FROM uncertainty_cases WHERE id=?", (case_id,)).fetchone()
        if (
            row is None
            or row["state"] != CaseState.LEASED.value
            or row["lease_token"] != token
            or row["lease_until"] < self.clock()
        ):
            raise LeaseNotOwned()
        return row

    def route_case(
        self, case_id: str, token: str, route: str, group_key: str = "", state: CaseState = CaseState.PENDING
    ) -> None:
        """Route an owned case without changing its source evidence or payload."""
        with self.db.tx() as con:
            self._owned(con, case_id, token)
            con.execute(
                "UPDATE uncertainty_cases SET state=?, route=?, group_key=?, owner=NULL, lease_token=NULL, "
                "lease_until=0, retry_after=0, updated=? WHERE id=?",
                (state.value, route, group_key or case_id, self.clock(), case_id),
            )

    def release(
        self,
        case_id: str,
        token: str,
        *,
        state: CaseState = CaseState.RETRY_WAIT,
        retry_after: float = 0.0,
        reason: str = "",
    ) -> None:
        """Release a lease to a visible state (never back to ``leased``)."""
        if state is CaseState.LEASED:
            raise ValueError("release state cannot be leased")
        with self.db.tx() as con:
            self._owned(con, case_id, token)
            con.execute(
                "UPDATE uncertainty_cases SET state=?, owner=NULL, lease_token=NULL, lease_until=0, "
                "retry_after=?, reason_detail=?, updated=? WHERE id=?",
                (state.value, retry_after, reason[:300], self.clock(), case_id),
            )

    def record_attempt(
        self,
        attempt: ResolutionAttempt,
        token: str,
        *,
        next_state: Optional[CaseState] = None,
        route: Optional[str] = None,
    ) -> str:
        """Append an attempt and move the case.

        ``resolved`` (a deterministic local verdict that passed validation) closes the case. A model
        ``proposed`` outcome moves it to ``needs_review`` with the proposal attached: a proposal never
        resolves a case by itself.
        """
        attempt_id = uuid.uuid4().hex
        with self.db.tx() as con:
            row = self._owned(con, attempt.case_id, token)
            accepted = attempt.outcome == "resolved" and bool(attempt.validation.get("accepted"))
            con.execute(
                "INSERT INTO resolution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    attempt.case_id,
                    row["corpus"],
                    attempt.provider,
                    attempt.requested_model,
                    attempt.executed_model,
                    attempt.request_fingerprint,
                    attempt.outcome,
                    _json(attempt.response),
                    _json(attempt.validation),
                    attempt.cost_usd,
                    int(attempt.cost_usd is not None),
                    attempt.input_tokens,
                    attempt.output_tokens,
                    self.clock(),
                ),
            )
            if accepted:
                state, resolution = CaseState.RESOLVED, attempt.response
            else:
                state, resolution = next_state or CaseState.NEEDS_REVIEW, None
            proposal = attempt_id if attempt.outcome == "proposed" and attempt.validation.get("accepted") else None
            con.execute(
                "UPDATE uncertainty_cases SET state=?, owner=NULL, lease_token=NULL, lease_until=0, "
                "retry_after=?, "
                "route=COALESCE(?, route), proposal_attempt_id=COALESCE(?, proposal_attempt_id), resolution=?, "
                "resolved_by=?, needs_validation=?, updated=? WHERE id=?",
                (
                    state.value,
                    self.clock() if state is CaseState.RETRY_WAIT else 0,
                    route,
                    proposal,
                    _json(resolution) if resolution is not None else None,
                    f"deterministic:{attempt.executed_model}" if accepted else None,
                    int(accepted),
                    self.clock(),
                    attempt.case_id,
                ),
            )
            self._event(
                con,
                attempt.case_id,
                row["corpus"],
                "attempt",
                {"attempt_id": attempt_id, "outcome": attempt.outcome, "state": state.value},
            )
        return attempt_id

    def apply_decision(
        self,
        case_id: str,
        corpora: Iterable[str],
        *,
        bound_revision: str,
        accept: bool,
        resolution: Optional[Dict[str, Any]],
        reviewer: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Record a reviewer's decision. Called only by the trusted reviewer interface.

        Raises:
            ContractError: ``NOT_FOUND`` outside scope; ``STALE_APPROVAL`` when the case revision moved since
                the reviewer saw it; ``CONFLICT`` when the case is not awaiting review.
        """
        scope = set(corpora)
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM uncertainty_cases WHERE id=?", (case_id,)).fetchone()
            if row is None or row["corpus"] not in scope:
                raise ContractError(ErrorCode.NOT_FOUND, "no such case in scope")
            if row["revision"] != bound_revision:
                raise ContractError(
                    ErrorCode.STALE_APPROVAL, "the candidate, schema or vocabulary changed since this was reviewed"
                )
            if row["state"] != CaseState.NEEDS_REVIEW.value:
                raise ContractError(ErrorCode.CONFLICT, f"case is {row['state']}, not awaiting review")
            state = CaseState.RESOLVED if accept else CaseState.REJECTED
            con.execute(
                "UPDATE uncertainty_cases SET state=?, resolution=?, resolved_by=?, needs_validation=?, "
                "reason_detail=?, updated=? WHERE id=?",
                (
                    state.value,
                    _json(resolution) if (accept and resolution is not None) else None,
                    reviewer,
                    int(accept),
                    reason[:300],
                    self.clock(),
                    case_id,
                ),
            )
            self._event(con, case_id, row["corpus"], "reviewed", {"by": reviewer, "accepted": accept})
        return self.get_case(case_id, scope)

    def get_case(self, case_id: str, corpora: Iterable[str]) -> Dict[str, Any]:
        """One case within scope, or ``NOT_FOUND``."""
        row = self.db.one("SELECT * FROM uncertainty_cases WHERE id=?", (case_id,))
        if row is None or row["corpus"] not in set(corpora):
            raise ContractError(ErrorCode.NOT_FOUND, "no such case in scope")
        return self._row(row)

    def list_cases(
        self, corpora: Iterable[str], *, state: Optional[str] = None, route: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Bounded case summaries within scope."""
        scope = sorted(set(corpora))
        if not scope:
            return []
        clauses, params = [f"corpus IN ({','.join('?' for _ in scope)})"], list(scope)
        if state:
            clauses.append("state=?")
            params.append(state)
        if route:
            clauses.append("route=?")
            params.append(route)
        rows = self.db.query(
            "SELECT * FROM uncertainty_cases WHERE "
            + " AND ".join(clauses)  # noqa: S608
            + " ORDER BY updated DESC, id LIMIT ?",
            (*params, max(1, min(limit, 1000))),
        )
        return [self._row(r) for r in rows]

    def awaiting_validation(self, corpora: Iterable[str]) -> List[Dict[str, Any]]:
        """Resolved cases whose candidate must go back through validation (never straight to publication)."""
        scope = sorted(set(corpora))
        if not scope:
            return []
        rows = self.db.query(
            f"SELECT * FROM uncertainty_cases WHERE needs_validation=1 AND corpus IN "  # noqa: S608
            f"({','.join('?' for _ in scope)})",
            scope,
        )
        return [self._row(r) for r in rows]

    def mark_validated(self, case_id: str) -> None:
        """Clear the revalidation flag once validation has accepted the resolved candidate."""
        with self.db.tx() as con:
            con.execute("UPDATE uncertainty_cases SET needs_validation=0 WHERE id=?", (case_id,))

    def attempts(self, case_id: str) -> List[Dict[str, Any]]:
        """Attempts for a case, oldest first."""
        rows = self.db.query("SELECT * FROM resolution_attempts WHERE case_id=? ORDER BY created, id", (case_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["response"], d["validation"] = json.loads(d["response"]), json.loads(d["validation"])
            out.append(d)
        return out

    def attempt_count(self, case_id: str, *, provider: Optional[str] = None) -> int:
        """Completed resolver calls (leases lost before a response are not counted)."""
        sql, params = "SELECT COUNT(*) FROM resolution_attempts WHERE case_id=?", [case_id]
        if provider:
            sql += " AND provider=?"
            params.append(provider)
        return int(self.db.one(sql, params)[0])

    def events(self, case_id: str) -> List[Dict[str, Any]]:
        """Case history."""
        return [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in self.db.query("SELECT * FROM case_events WHERE case_id=? ORDER BY created, rowid", (case_id,))
        ]

    def summary(self, corpora: Sequence[str]) -> Dict[str, Any]:
        """Queue counts by state and route for the given corpora."""
        scope = sorted(set(corpora))
        if not scope:
            return {"cases": {}, "routes": {}}
        marks = ",".join("?" for _ in scope)
        states = {
            r["state"]: r["n"]
            for r in self.db.query(
                f"SELECT state, COUNT(*) n FROM uncertainty_cases WHERE corpus IN ({marks}) GROUP BY state", scope
            )
        }  # noqa: S608
        routes = {
            f"{r['route']}:{r['state']}": r["n"]
            for r in self.db.query(
                f"SELECT route, state, COUNT(*) n FROM uncertainty_cases WHERE corpus IN ({marks}) "  # noqa: S608
                "GROUP BY route, state",
                scope,
            )
        }
        return {"cases": states, "routes": routes}

    # --- cost approvals ----------------------------------------------------------------

    def save_cost_approval(self, approval_id: str, record: Dict[str, Any]) -> None:
        """Persist a cost approval record (created only by the trusted approval interface)."""
        with self.db.tx() as con:
            con.execute(
                "INSERT INTO cost_approvals VALUES (?, ?, 'valid', ?)", (approval_id, _json(record), self.clock())
            )

    def get_cost_approval(self, approval_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
        """``(record, status)`` or ``None``."""
        row = self.db.one("SELECT record, status FROM cost_approvals WHERE approval_id=?", (approval_id,))
        return (json.loads(row["record"]), row["status"]) if row else None

    def revoke_cost_approval(self, approval_id: str) -> None:
        """Revoke; later reservations under it are refused."""
        with self.db.tx() as con:
            con.execute("UPDATE cost_approvals SET status='revoked' WHERE approval_id=?", (approval_id,))

    def close(self) -> None:
        """Close the database."""
        self.db.close()


__all__ = ("CurationStore", "LeaseNotOwned", "SCHEMA")
