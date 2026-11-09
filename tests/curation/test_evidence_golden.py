"""Parity: the ported evidence debate reproduces the goldens recorded from the legacy module (T17B P2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.f2w_agent.debate_agent import EvidenceDebateAgent
from tests.curation.evidence_cases import CASES
from tests.curation.evidence_harness import run_case

GOLDEN = json.loads((Path(__file__).parent / "golden" / "evidence_transcripts.json").read_text(encoding="utf-8"))


def test_every_case_has_a_golden() -> None:
    assert set(GOLDEN) == set(CASES)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_decision_prompt_calls_and_notes_match_legacy(case_id: str) -> None:
    got = run_case(case_id, lambda: EvidenceDebateAgent(backend="cborg", model="m"))
    assert json.loads(json.dumps(got, sort_keys=True)) == GOLDEN[case_id]


def test_debate_call_is_counted_and_refused_over_budget(tmp_path: Path) -> None:
    from app.modules.curation.agent_budget import LedgerAgentBudget
    from app.modules.curation.budgets import BudgetLedger
    from app.modules.curation.control import write_control
    from app.modules.jobs.sqlite import OperationalDB

    ledger = BudgetLedger(OperationalDB(tmp_path / "b.sqlite3"))
    control = tmp_path / "c.json"
    write_control(control, enabled=False, budget_cap_usd=0.05)
    budget = LedgerAgentBudget(ledger, control_path=control, reserve_usd=0.05)
    case = "llm_download_valid"
    first = run_case(case, lambda: EvidenceDebateAgent(backend="cborg", model="m", budget=budget))
    second = run_case(case, lambda: EvidenceDebateAgent(backend="cborg", model="m", budget=budget))
    assert len(first["calls"]) == 1 and second["calls"] == [] and second["factory_calls"] == []
    assert second["decision"]["selected_action"] == "download_selected"  # heuristic fallback, same wire shape
    assert second["notes"][-1][:2] == ["note_error", "EvidenceDebateAgent.decide"]
    assert ledger.summary(cap_usd=0.05)["charged_usd"] == 0.05
