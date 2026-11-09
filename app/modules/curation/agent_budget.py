"""Ledger-backed budget for the routing and debate agents' remote LLM calls (T17B P2).

Implements :class:`app.modules.agent_runtime.llm_gate.BudgetPort` over the curation
:class:`~app.modules.curation.budgets.BudgetLedger`, so class (r) agent turns appear in the same
``budget`` block as case resolution (``/v1/ops/status``, ``GET`` cost control under ``jobs.admin``).

The cap is the deployment's ``budget_cap_usd`` (re-read per call from the control file; ``None`` means
no cost limit is configured, and admission still bounds concurrency). The remote switch ``enabled`` is
not consulted: it gates case resolution, and consulting it here would turn chat off by default.
The chat clients return text only, so cost is unknown: each call stays charged at ``reserve_usd``.
"""

from __future__ import annotations

from pathlib import Path

from .budgets import BudgetLedger
from .control import read_control
from .cost_ledger import CallOutcome, settle_outcome

AGENT_SCOPE = "agent_runtime"


class LedgerAgentBudget:
    """Reserve and settle one agent call against the curation ledger."""

    def __init__(self, ledger: BudgetLedger, *, control_path: Path | None = None, corpus: str = "legacy_ui",
                 scope: str = AGENT_SCOPE, reserve_usd: float = 0.05) -> None:
        self._ledger = ledger
        self._control_path = control_path
        self._corpus = corpus
        self._scope = scope
        self._reserve = reserve_usd

    def reserve(self, *, model: str, label: str) -> str | None:
        """Reserve ``reserve_usd``; ``None`` when the configured cap would be exceeded."""
        cap = read_control(self._control_path)["budget_cap_usd"] if self._control_path is not None else None
        return self._ledger.reserve(corpus=self._corpus, case_id=None, model=model, amount_usd=self._reserve,
                                    cap_usd=cap, scope=self._scope, owner=label)

    def settle(self, reservation: str, *, cost_usd: float | None, served: bool) -> None:
        """Settle once (known, unknown, or released when never served)."""
        settle_outcome(self._ledger, reservation, CallOutcome(cost_usd=cost_usd, served=served, failed=not served))


__all__ = ("AGENT_SCOPE", "LedgerAgentBudget")
