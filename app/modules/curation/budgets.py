"""Budget reservation and settlement, including failed and partial work (U-01, U-06).

The ledger is operational state in the curation database. Rules:

* a reservation is the *upper-bound estimate* of one call and is held against the cap while the
  call is in flight, so concurrent workers cannot together exceed a configured limit;
* settlement with a provider-reported cost records that cost, whatever the response status
  (an error response that reports a charge was charged);
* settlement without a usable cost is ``settled_unknown``: the reservation stays charged and the
  amount is reported as *unknown*, never as zero. Only a call known not to have reached the
  provider is ``cancelled`` and released;
* reservations left ``reserved`` by a dead worker are reaped into ``settled_unknown``, never freed;
* an actual cost above its reservation is recorded as an overrun and is visible in :meth:`summary`
  (a configured cap can be exceeded by the provider's real cost, but never silently).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/scientific_artifacts.py (``reserve_budget``,
``settle_budget``, ``summary``) (BSD-3-Clause).
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable
import uuid

from ..jobs.sqlite import OperationalDB

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS budget_ledger (id TEXT PRIMARY KEY, corpus TEXT NOT NULL, case_id TEXT, "
    "model TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', reserved_usd REAL NOT NULL, "
    "actual_usd REAL, state TEXT NOT NULL, created REAL NOT NULL, settled REAL);\n",
    "CREATE INDEX IF NOT EXISTS budget_scope ON budget_ledger(scope, state);\n",
)
_CHARGED = "('reserved','settled','settled_unknown')"


class BudgetLedger:
    """Reserve and settle spend against global and per-scope caps."""

    def __init__(self, db: OperationalDB, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self._clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    def reserve(
        self,
        *,
        corpus: str,
        case_id: str | None,
        model: str,
        amount_usd: float,
        cap_usd: float | None,
        scope: str = "default",
        scope_cap_usd: float | None = None,
        owner: str = "",
    ) -> str | None:
        """Atomically reserve ``amount_usd``; ``None`` when a cap would be exceeded.

        Args:
            corpus: Corpus the spend is attributed to.
            case_id: Curation case the spend is for, if any.
            model: Model the spend is for.
            amount_usd: Amount to reserve; must be finite and non-negative.
            cap_usd: Global ceiling over all scopes, or ``None`` when no cost limit is configured
                (provider limits still apply through admission).
            scope: Budget scope the per-scope cap applies to.
            scope_cap_usd: Ceiling for ``scope``, or ``None`` for no per-scope cap.
            owner: Free-text owner recorded on the reservation.

        Returns:
            The reservation id, or ``None`` when a cap would be exceeded.

        Raises:
            ValueError: ``scope`` is blank or ``amount_usd`` is not finite and non-negative.
        """
        if not scope.strip():
            raise ValueError("scope must be a non-empty string")
        if not math.isfinite(amount_usd) or amount_usd < 0:
            raise ValueError("amount_usd must be a finite non-negative number")
        with self.db.tx() as con:
            if cap_usd is not None:
                spent = con.execute(
                    f"SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)), 0) FROM budget_ledger "
                    f""  # noqa: S608
                    f"WHERE state IN {_CHARGED}"
                ).fetchone()[0]
                if float(spent) + amount_usd > cap_usd:
                    return None
            if scope_cap_usd is not None:
                scoped = con.execute(
                    f"SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)), 0) FROM budget_ledger "
                    f""  # noqa: S608
                    f"WHERE scope=? AND state IN {_CHARGED}",
                    (scope,),
                ).fetchone()[0]
                if float(scoped) + amount_usd > scope_cap_usd:
                    return None
            reservation = uuid.uuid4().hex
            con.execute(
                "INSERT INTO budget_ledger (id, corpus, case_id, model, scope, owner, reserved_usd, state, "
                "created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?)",
                (reservation, corpus, case_id, model, scope, owner, amount_usd, self._clock()),
            )
        return reservation

    def settle_known(self, reservation: str, actual_usd: float) -> None:
        """Settle on a provider-reported cost (finite, non-negative)."""
        if (
            isinstance(actual_usd, bool)
            or not isinstance(actual_usd, (int, float))
            or not math.isfinite(actual_usd)
        ):
            raise TypeError("actual_usd must be a finite number; use settle_unknown when no cost was reported")
        if actual_usd < 0:
            raise ValueError("cost cannot be negative")
        with self.db.tx() as con:
            con.execute(
                "UPDATE budget_ledger SET actual_usd=?, state='settled', settled=? WHERE id=? AND "
                "state='reserved'",
                (float(actual_usd), self._clock(), reservation),
            )

    def settle_unknown(self, reservation: str) -> None:
        """No usable cost: the reservation stays as the charge and is reported as unknown."""
        with self.db.tx() as con:
            con.execute(
                "UPDATE budget_ledger SET actual_usd=NULL, state='settled_unknown', settled=? "
                "WHERE id=? AND state='reserved'",
                (self._clock(), reservation),
            )

    def cancel(self, reservation: str) -> None:
        """The request never reached the provider: release the reservation."""
        with self.db.tx() as con:
            con.execute(
                "UPDATE budget_ledger SET actual_usd=NULL, state='cancelled', settled=? WHERE id=? AND "
                "state='reserved'",
                (self._clock(), reservation),
            )

    def reap_stale(self, older_than_seconds: float) -> int:
        """Reservations a dead worker left open become ``settled_unknown`` (charged, never freed)."""
        cutoff = self._clock() - older_than_seconds
        with self.db.tx() as con:
            cur = con.execute(
                "UPDATE budget_ledger SET state='settled_unknown', settled=? WHERE state='reserved' AND created<?",
                (self._clock(), cutoff),
            )
            return cur.rowcount

    def state_of(self, reservation: str) -> str | None:
        """Ledger state of one reservation."""
        row = self.db.one("SELECT state FROM budget_ledger WHERE id=?", (reservation,))
        return row[0] if row else None

    def summary(self, *, cap_usd: float | None = None) -> dict[str, Any]:
        """Spend figures that keep known, reserved and unknown apart.

        ``charged_usd`` counts unknown-cost calls at their reservation (a conservative bound);
        ``unknown_calls`` says how many of them there were. ``overrun_usd`` is actual cost above
        what was reserved; ``cap_exceeded`` is true when charged spend is above ``cap_usd``.
        """
        row = self.db.one(
            "SELECT "
            "COALESCE(SUM(CASE WHEN state='settled' THEN actual_usd ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_usd ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN state='settled_unknown' THEN reserved_usd ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN state='settled_unknown' THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN state='settled' AND actual_usd > reserved_usd THEN actual_usd - reserved_usd "
            "ELSE 0 END), 0) FROM budget_ledger"
        )
        known, reserved, unknown_charge, unknown_calls, overrun = (
            float(row[0]),
            float(row[1]),
            float(row[2]),
            int(row[3]),
            float(row[4]),
        )
        charged = known + reserved + unknown_charge
        return {
            "known_usd": round(known, 6),
            "reserved_usd": round(reserved, 6),
            "unknown_charge_usd": round(unknown_charge, 6),
            "unknown_calls": unknown_calls,
            "charged_usd": round(charged, 6),
            "overrun_usd": round(overrun, 6),
            "cap_usd": cap_usd,
            "cap_exceeded": cap_usd is not None and charged > cap_usd + 1e-9,
        }


__all__ = ("BudgetLedger", "SCHEMA")
