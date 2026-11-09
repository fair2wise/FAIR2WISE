"""Parity: the ported orchestrator routing reproduces the goldens recorded from the legacy module (T17B P2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.f2w_agent.orchestrator_agent import WorkflowOrchestratorAgent
from tests.runtime.routing_cases import CASES
from tests.runtime.routing_harness import run_case

GOLDEN = json.loads((Path(__file__).parent / "golden" / "routing_transcripts.json").read_text(encoding="utf-8"))


def test_every_case_has_a_golden() -> None:
    assert set(GOLDEN) == set(CASES)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_decision_prompt_calls_and_notes_match_legacy(case_id: str) -> None:
    got = run_case(case_id, lambda: WorkflowOrchestratorAgent(max_steps=12))
    assert json.loads(json.dumps(got, sort_keys=True)) == GOLDEN[case_id]


def test_orchestrator_classifier_is_counted_and_refused_over_budget(tmp_path: Path) -> None:
    """A fresh turn over budget: the model is never called and the decision is the deterministic fallback."""
    from app.modules.curation.agent_budget import LedgerAgentBudget
    from app.modules.curation.budgets import BudgetLedger
    from app.modules.curation.control import write_control
    from app.modules.jobs.sqlite import OperationalDB

    ledger = BudgetLedger(OperationalDB(tmp_path / "b.sqlite3"))
    control = tmp_path / "c.json"
    write_control(control, enabled=False, budget_cap_usd=0.05)
    budget = LedgerAgentBudget(ledger, control_path=control, reserve_usd=0.05)
    case = "fresh_science_llm_scientific"
    first = run_case(case, lambda: WorkflowOrchestratorAgent(backend="cborg", budget=budget))
    second = run_case(case, lambda: WorkflowOrchestratorAgent(backend="cborg", budget=budget))
    assert len(first["calls"]) == 1 and second["calls"] == []  # second refused before any client exists
    assert second["factory_calls"] == []
    keys = ("action", "agent", "classification")  # fail-closed to the deterministic fallback; only the reason text differs
    assert {k: second["decision"][k] for k in keys} == {k: first["decision"][k] for k in keys}
    assert ["note_error", "WorkflowOrchestratorAgent.decide"] == second["notes"][-1][:2]
    assert ledger.summary(cap_usd=0.05)["charged_usd"] == 0.05
