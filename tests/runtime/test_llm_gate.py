"""Admission, budget and deadline for the routing/debate model calls (T17B P2). Fake chat models only."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.modules.agent_runtime.admission import NoOpAdmission
from app.modules.agent_runtime.llm_gate import AdmittedChat, LlmBudgetRefused, LlmCallRefused
from app.modules.curation.agent_budget import LedgerAgentBudget
from app.modules.curation.budgets import BudgetLedger
from app.modules.curation.control import write_control
from app.modules.jobs.sqlite import OperationalDB

REPO = Path(__file__).resolve().parents[2]


class Client:
    def __init__(self, reply="{}", boom=None):
        self.reply, self.boom, self.calls = reply, boom, []

    def chat(self, prompt, *, temperature, timeout, trace_label):
        self.calls.append((prompt, temperature, timeout, trace_label))
        if self.boom:
            raise self.boom
        return self.reply


class RecordingAdmission:
    def __init__(self, refuse=False):
        self.refuse, self.slots = refuse, []

    def slot(self, provider):
        admission = self

        class Ctx:
            def __enter__(self):
                if admission.refuse:
                    raise RuntimeError("not admitted")
                admission.slots.append(provider)

            def __exit__(self, *exc):
                return False

        return Ctx()

    def aslot(self, provider):  # pragma: no cover - protocol completeness
        raise NotImplementedError


def deadline(call, seconds):
    deadline.seen.append(seconds)
    return call()


deadline.seen = []


@pytest.fixture
def ledger(tmp_path):
    return BudgetLedger(OperationalDB(tmp_path / "b.sqlite3"))


def chat(client, ledger_budget=None, admission=None, local=False, built=None):
    def factory():
        if built is not None:
            built.append(1)
        return client

    return AdmittedChat(client_factory=factory, model="m", admission=admission or RecordingAdmission(),
                        run_with_deadline=deadline, budget=ledger_budget, local=local)


def test_call_is_admitted_deadline_bounded_and_counted(ledger):
    client, adm = Client("hi"), RecordingAdmission()
    budget = LedgerAgentBudget(ledger, reserve_usd=0.1)
    assert chat(client, budget, adm).complete("p", timeout=60, label="orchestrator-classify") == "hi"
    assert adm.slots == ["agent_llm"] and deadline.seen[-1] == 180.0
    assert client.calls == [("p", 0.0, 60, "orchestrator-classify")]
    summary = ledger.summary()
    assert summary["unknown_calls"] == 1 and summary["charged_usd"] == 0.1  # cost unknown: never zero


def test_over_budget_call_is_refused_with_a_typed_error_and_nothing_runs(tmp_path, ledger):
    control = tmp_path / "control.json"
    write_control(control, enabled=False, budget_cap_usd=0.1)
    budget = LedgerAgentBudget(ledger, control_path=control, reserve_usd=0.06)
    client, adm, built = Client(), RecordingAdmission(), []
    gate = chat(client, budget, adm, built=built)
    gate.complete("one", timeout=10, label="x")
    with pytest.raises(LlmBudgetRefused) as err:
        gate.complete("two", timeout=10, label="x")
    assert isinstance(err.value, LlmCallRefused)
    assert len(client.calls) == 1 and len(built) == 1 and len(adm.slots) == 1  # no slot, no client, no call
    assert ledger.summary(cap_usd=0.1)["charged_usd"] == 0.06


def test_no_call_without_admission(ledger):
    with pytest.raises(TypeError):
        AdmittedChat(client_factory=Client, model="m", admission=None, run_with_deadline=deadline)  # type: ignore[arg-type]
    client, built = Client(), []
    refusing = RecordingAdmission(refuse=True)
    budget = LedgerAgentBudget(ledger)
    with pytest.raises(RuntimeError, match="not admitted"):
        chat(client, budget, refusing, built=built).complete("p", timeout=5, label="x")
    assert client.calls == [] and built == []
    assert ledger.summary()["charged_usd"] == 0.0  # the reservation was released: the call never left


def test_failed_call_stays_charged_as_unknown_but_a_429_is_released(ledger):
    budget = LedgerAgentBudget(ledger, reserve_usd=0.2)
    with pytest.raises(RuntimeError):
        chat(Client(boom=RuntimeError("500")), budget).complete("p", timeout=5, label="x")
    assert ledger.summary()["unknown_calls"] == 1

    class RateLimited(Exception):
        status_code = 429

    with pytest.raises(RateLimited):
        chat(Client(boom=RateLimited()), budget).complete("p", timeout=5, label="x")
    assert ledger.summary()["unknown_calls"] == 1 and ledger.summary()["charged_usd"] == 0.2


def test_local_model_is_admitted_but_not_reserved(ledger):
    adm = RecordingAdmission()
    chat(Client(), LedgerAgentBudget(ledger), adm, local=True).complete("p", timeout=5, label="x")
    assert adm.slots == ["agent_llm"] and ledger.summary()["charged_usd"] == 0.0


def test_default_noop_admission_type_is_accepted():
    assert chat(Client("ok"), admission=NoOpAdmission()).complete("p", timeout=5, label="x") == "ok"


def test_runtime_and_curation_import_no_academy():
    for base in (REPO / "app/modules/agent_runtime", REPO / "app/modules/curation"):
        for path in base.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
                assert not any(n.split(".")[0] == "academy" for n in names), path
