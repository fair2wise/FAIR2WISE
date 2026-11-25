"""Extraction bounds the schema prompt of a user schema like generation does; shipped schemas render exactly as before."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.modules.contracts.jobs import JobState
from app.modules.library import extraction as X
from app.modules.library.schema_prompt import BUDGET_CBORG, BUDGET_OLLAMA
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.schema import SchemaHelper
from tests.library.test_library_extract import SCHEMA, FakeOrchestrator, World, _three
from tests.library.test_user_schema_selection import user_world
from tests.library.test_user_schema_validation import GOOD


def _big_user_schema(tmp_path: Path, classes: int = 140) -> Path:
    extra = "".join(f"  Fish{i}:\n    is_a: Thing\n    description: Armoured catfish group number {i}, found in the Rio Negro basin. More text.\n"
                    for i in range(classes))
    marker = "classes:\n"
    assert marker in GOOD
    path = tmp_path / "big.yaml"
    path.write_text(GOOD.replace(marker, marker + extra, 1), encoding="utf-8")
    return path


def test_a_context_within_the_budget_is_byte_for_byte_the_full_rendering(tmp_path):
    helper = SchemaHelper(str(_big_user_schema(tmp_path, 3)), sanitize_descriptions=True)
    text, info = helper.bounded_schema_context(100_000)
    assert info is None and text == helper.get_schema_context_for_llm()


def test_a_long_context_is_condensed_within_the_budget_and_reports_the_sizes(tmp_path):
    helper = SchemaHelper(str(_big_user_schema(tmp_path)), sanitize_descriptions=True)
    full = helper.get_schema_context_for_llm()
    assert len(full) > BUDGET_OLLAMA
    for budget in (BUDGET_OLLAMA, BUDGET_CBORG):
        text, info = helper.bounded_schema_context(budget)
        assert len(text) <= budget
        assert info == {"full_chars": len(full), "used_chars": len(text), "budget": budget}
    text, info = helper.bounded_schema_context(BUDGET_OLLAMA)
    assert info is not None and "condensed" in text and "Thing" in text and "more classes omitted" in text
    assert helper.bounded_schema_context(BUDGET_OLLAMA)[0] == text  # deterministic


def test_the_orchestrator_bounds_only_when_given_a_budget(tmp_path):
    helper = SchemaHelper(str(_big_user_schema(tmp_path)), sanitize_descriptions=True)
    orch = Orchestrator.__new__(Orchestrator)
    orch.schema_helper, orch.schema_prompt_budget, orch._bounded_context = helper, None, None
    assert orch._schema_context() == helper.get_schema_context_for_llm() and orch.schema_prompt_condensed() is None
    orch.schema_prompt_budget = BUDGET_OLLAMA
    assert len(orch._schema_context()) <= BUDGET_OLLAMA and orch.schema_prompt_condensed()["budget"] == BUDGET_OLLAMA


def test_the_default_factory_passes_the_budget_only_for_user_schemas(monkeypatch):
    seen: list[dict[str, Any]] = []
    import app.modules.term_extractor as T

    monkeypatch.setattr(T, "Orchestrator", lambda **kw: seen.append(kw) or SimpleNamespace())
    for user, name in ((True, "ollama"), (True, "cborg"), (False, "cborg")):
        X.default_orchestrator_factory(model="m", output_file="o", schema_path="s", backend={"name": name}, user_schema=user)
    assert seen[0]["schema_prompt_budget"] == BUDGET_OLLAMA and seen[1]["schema_prompt_budget"] == BUDGET_CBORG
    assert "schema_prompt_budget" not in seen[2] and "sanitize_schema_descriptions" not in seen[2]


class CondensedFake(FakeOrchestrator):
    def schema_prompt_condensed(self) -> dict[str, int]:
        return {"full_chars": 20_000, "used_chars": 11_900, "budget": 12_000}


def test_a_condensed_run_logs_a_warning_and_the_summary_carries_the_sizes(tmp_path):
    world, _store, ident = user_world(tmp_path)
    world.factory = lambda **kw: CondensedFake(model=kw["model"], output_file=kw["output_file"], schema_path=kw["schema_path"],
                                               backend=kw["backend"])
    world.extractor.factory = world.factory
    handle, state = world.run(world.params(_three(world), schema=ident))
    assert state is JobState.SUCCEEDED, world.job_error(handle)
    from app.modules.jobs.run_summary import run_summary

    summary = run_summary(world.jobs.store, world.jobs.store.get(handle.job_id))
    assert summary["schema_prompt_condensed"] == {"full_chars": 20_000, "used_chars": 11_900, "budget": 12_000}
    assert not any(k.startswith("schema_prompt") for k in summary["counters"])
    logs = [row.message for row in world.jobs.store.logs(handle.job_id)]
    assert any("schema prompt is condensed" in m and "20000" in m for m in logs)


def test_an_unbounded_run_has_no_warning(tmp_path):
    world = World(tmp_path)
    handle, state = world.run(world.params(_three(world), schema=SCHEMA))
    assert state is JobState.SUCCEEDED
    from app.modules.jobs.run_summary import run_summary

    assert run_summary(world.jobs.store, world.jobs.store.get(handle.job_id))["schema_prompt_condensed"] is None


def test_the_estimate_of_a_user_schema_states_the_prompt_size_and_budgets(tmp_path):
    world, _store, ident = user_world(tmp_path)
    ids = world.ids(*_three(world))
    out = world.kg.estimate(ids, "m-1", ident)
    assert out["schema_prompt"]["budgets"] == {"ollama": BUDGET_OLLAMA, "cborg": BUDGET_CBORG}
    assert out["schema_prompt"]["full_chars"] > 100
    assert "schema_prompt" not in world.kg.estimate(ids, "m-1", SCHEMA)
