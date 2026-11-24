"""Live feedback of a ``library_extract`` run: phase, current paper, papers done of total, KG growth, outcome, logs.

Fake LLM only (the ``FakeOrchestrator`` of ``test_library_extract``); the pipeline, the store and the summary are real.
"""

from __future__ import annotations

import json
import logging

from app.modules.contracts.jobs import JobState
from app.modules.jobs.run_summary import run_summary
from tests.library.test_library_extract import FakeOrchestrator, World, _three


def _summary(world: World, handle, *, detail: bool = True) -> dict:
    return run_summary(world.jobs.store, world.jobs.store.get(handle.job_id), detail=detail)


def test_succeeded_run_reports_papers_growth_and_logs(tmp_path):
    world = World(tmp_path)
    handle, state = world.run(world.params(_three(world)))
    assert state is JobState.SUCCEEDED
    summary = _summary(world, handle)
    assert summary["outcome"] == "succeeded" and summary["state"] == "succeeded"
    assert summary["papers"] == {"total": 3, "done": 3, "failed": 0, "skipped": 0, "failed_list": []}
    # a.pdf: P3HT, GIWAXS; b.pdf: P3HT (merged), PCBM; c.pdf: RSoXS
    assert summary["growth"]["entities_new"] == 4 and summary["growth"]["entities_merged"] == 1
    assert summary["growth"]["entities_total"] == 4
    assert summary["params"] == {"bundle": "run1", "model": "m-1", "schema": summary["params"]["schema"],
                                 "topic": "polymers"}
    assert "confirmation" not in json.dumps(summary)
    assert summary["current_paper"] is None and summary["elapsed_seconds"] >= 0
    logs = summary["logs"]
    assert logs["kept"] == logs["total"] > 3 and logs["cap"] == 2000


def test_partially_failed_is_derived_from_the_per_paper_outcomes(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    world.put("b.pdf", "BAD")
    handle, state = world.run(world.params(names))
    assert state is JobState.FAILED  # incomplete coverage: the job state set has no "partially failed"
    summary = _summary(world, handle)
    assert summary["outcome"] == "partially_failed"
    assert summary["papers"]["done"] == 2 and summary["papers"]["failed"] == 1
    failed = summary["papers"]["failed_list"]
    assert failed == [{"name": "b.pdf", "reason": "the PDF could not be read"}]
    assert summary["growth"]["entities_new"] >= 1  # counters survive the failed end
    # the log has a warning line naming the paper, in plain words
    ctx = world.service.worker_context("s")
    warnings = [log for log in world.jobs.logs(ctx, handle.job_id) if log.level == "warning"]
    assert any("b.pdf" in log.message and "could not be read" in log.message for log in warnings)
    # compact form leaves the list out
    assert "failed_list" not in _summary(world, handle, detail=False)["papers"]


def test_every_paper_failing_is_failed_not_partially_failed(tmp_path):
    world = World(tmp_path)
    world.put("a.pdf", "BAD")
    world.put("b.pdf", "BAD")
    handle, state = world.run(world.params(["a.pdf", "b.pdf"]))
    assert state is JobState.FAILED
    summary = _summary(world, handle)
    assert summary["outcome"] == "failed" and summary["papers"]["done"] == 0 and summary["papers"]["failed"] == 2


def test_current_paper_and_phase_are_visible_while_running_from_the_store(tmp_path):
    seen: list[dict] = []

    def hook(orch: FakeOrchestrator, name: str) -> None:
        # runs inside the worker, mid-paper: read the store the way the API process would
        record = world.jobs.store.get(world.jobs.store.list_jobs()[0].job_id)
        seen.append(run_summary(world.jobs.store, record))

    world = World(tmp_path, hook=hook)
    world.run(world.params(_three(world)))
    assert [s["current_paper"] for s in seen] == ["a.pdf", "b.pdf", "c.pdf"]
    assert all(s["state"] == "running" and s["stage"]["phase"] == "parsing" for s in seen)
    assert [s["papers"]["done"] for s in seen] == [0, 1, 2] and all(s["papers"]["total"] == 3 for s in seen)
    assert seen[2]["growth"]["entities_new"] == 3  # growth so far, counters written after each paper


def test_job_log_lines_name_papers_and_never_carry_paths(tmp_path):
    world = World(tmp_path)
    logging.getLogger("app.modules.term_extractor.store").info("Loaded 1 terms from %s", tmp_path / "kgs" / "x" / "terms.json")
    handle, _ = world.run(world.params(_three(world)))
    ctx = world.service.worker_context("s")
    messages = [log.message for log in world.jobs.logs(ctx, handle.job_id)]
    assert any(m.startswith("paper 1 of 3: extracting a.pdf") for m in messages)
    assert any(m.startswith("paper 3 of 3 done: c.pdf") for m in messages)
    assert any("merging the extracted terms into the KG" in m for m in messages)
    assert not any(str(tmp_path) in m for m in messages)
