"""Reconcile one provider call's cost exactly once (U-06): known, unknown, or not billed.

The rules, in order:

1. A usable provider-reported cost is recorded whatever the response status. A *failed* response
   that reports a charge was charged.
2. No usable cost, but the request is known never to have been served (rate-limit rejection,
   connection refused before sending): the reservation is released.
3. Anything else (a 2xx without a cost header, a 5xx, a timeout after sending, a malformed or
   partial response): the call may have been billed, so it is settled as **unknown** and keeps
   its reservation as the charge. Missing cost metadata is never read as zero.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/cborg_ledger.py (``parse_cost_header``,
``settle_from_response``) (BSD-3-Clause), without the donor's credential-bearing httpx client:
transport stays in the existing F2W chat clients.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math

from .budgets import BudgetLedger

logger = logging.getLogger(__name__)

COST_HEADER = "x-litellm-response-cost"


def parse_cost_header(raw: str | None) -> float | None:
    """The cost the provider reported, or ``None`` when it reported none we can use (never 0)."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        cost = float(raw)
    except ValueError:
        cost = math.nan
    if not math.isfinite(cost) or cost < 0:
        logger.warning("provider cost metadata unusable: %r", str(raw)[:40])
        return None
    return cost


@dataclass(frozen=True)
class CallOutcome:
    """What is known about one provider call.

    Attributes:
        cost_usd: Parsed provider-reported cost, if any.
        served: ``False`` only when the request is known never to have been processed by the provider.
        failed: Whether the call produced no usable completion.
    """

    cost_usd: float | None = None
    served: bool = True
    failed: bool = False

    @classmethod
    def from_response(
        cls, status: int | None, cost_header: str | None, *, sent: bool = True
    ) -> CallOutcome:
        """Classify an HTTP outcome. 429 and never-sent requests were not served."""
        cost = parse_cost_header(cost_header)
        not_served = (not sent) or status == 429
        return cls(cost_usd=cost, served=not not_served, failed=bool(status and status >= 400) or not sent)


def settle_outcome(ledger: BudgetLedger, reservation: str, outcome: CallOutcome) -> str:
    """Settle ``reservation`` for ``outcome``; returns the resulting ledger state."""
    if outcome.cost_usd is not None:
        ledger.settle_known(reservation, outcome.cost_usd)
    elif not outcome.served:
        ledger.cancel(reservation)
    else:
        ledger.settle_unknown(reservation)
    return ledger.state_of(reservation) or "missing"


__all__ = ("COST_HEADER", "CallOutcome", "parse_cost_header", "settle_outcome")
