"""W3C ``traceparent`` from the submitting request to the worker's job spans (side table, no migration)."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

import pytest

from app.modules import tracing
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore, Pipeline, StageSpec
from app.modules.jobs.pipeline import AwaitReview, StageResult
from app.modules.jobs.store import SCHEMA
from tests.jobs.jobs_fakes import inputs, local_ctx


def build(tmp_path: Path, *stages: StageSpec) -> tuple[JobStore, JobService]:
    store = JobStore(tmp_path / "jobs.sqlite3")
    service = JobService(store)
    service.register("ingest", Pipeline(list(stages)))
    return store, service


def ok_stage(name: str) -> StageSpec:
    def run(sc: object) -> StageResult:
        with tracing.span(f"inside.{name}"):
            return StageResult()

    return StageSpec(name, run)


def test_worker_spans_are_children_of_the_submitting_request(exporter, tmp_path) -> None:  # noqa: ANN001
    store, service = build(tmp_path, ok_stage("parse"), ok_stage("extract"))
    with tracing.span("POST /v1/jobs") as request:
        job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
        request_trace = request.trace_id
    assert store.trace_context(job_id) is not None

    # A second store object and a fresh thread stand in for the worker process: no shared context.
    worker_store = JobStore(tmp_path / "jobs.sqlite3")
    worker = JobService(worker_store)
    worker.register("ingest", Pipeline([ok_stage("parse"), ok_stage("extract")]))
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(worker.work_one()))
    thread.start()
    thread.join()
    assert result and result[0] is not None and result[0][1] is JobState.SUCCEEDED

    spans = {s.name: s for s in exporter.get_finished_spans()}
    run = spans["job.run"]
    request_span = spans["POST /v1/jobs"]
    assert format(run.context.trace_id, "032x") == request_trace
    assert run.parent.span_id == request_span.context.span_id and run.parent.is_remote
    assert run.attributes["f2w.job_id"] == job_id and run.attributes["f2w.job_kind"] == "ingest"
    assert run.attributes["f2w.status"] == "succeeded"
    stages = [s for s in exporter.get_finished_spans() if s.name == "job.stage"]
    assert [s.attributes["f2w.stage"] for s in stages] == ["parse", "extract"]
    assert all(s.parent.span_id == run.context.span_id for s in stages)
    inside = [s for s in exporter.get_finished_spans() if s.name.startswith("inside.")]
    assert {s.parent.span_id for s in inside} == {s.context.span_id for s in stages}


def test_submit_without_a_span_stores_nothing_and_run_starts_a_root(exporter, tmp_path) -> None:  # noqa: ANN001
    store, service = build(tmp_path, ok_stage("parse"))
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    assert store.trace_context(job_id) is None
    service.work_one()
    run = next(s for s in exporter.get_finished_spans() if s.name == "job.run")
    assert run.parent is None


def test_pauses_are_not_errors(exporter, tmp_path) -> None:  # noqa: ANN001
    def review(sc: object) -> StageResult:
        raise AwaitReview("needs a human")

    store, service = build(tmp_path, StageSpec("review", review))
    service.submit(local_ctx(), "ingest", inputs())
    service.work_one()
    stage = next(s for s in exporter.get_finished_spans() if s.name == "job.stage")
    assert stage.status.status_code.name == "UNSET" and stage.attributes["f2w.status"] == "AwaitReview"


def test_failed_stage_marks_the_span_with_the_exception_type(exporter, tmp_path) -> None:  # noqa: ANN001
    def explode(sc: object) -> StageResult:
        raise KeyError("secret-looking message")

    store, service = build(tmp_path, StageSpec("explode", explode))
    service.submit(local_ctx(), "ingest", inputs())
    service.work_one()
    spans = exporter.get_finished_spans()
    stage = next(s for s in spans if s.name == "job.stage")
    assert stage.status.status_code.name == "ERROR" and stage.status.description == "KeyError"
    assert "secret-looking" not in " ".join(f"{k}={v}" for s in spans for k, v in s.attributes.items())


def test_tracing_off_leaves_the_job_tables_empty(tmp_path) -> None:
    store, service = build(tmp_path, ok_stage("parse"))
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    service.work_one()
    assert store.trace_context(job_id) is None
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as con:
        assert con.execute("SELECT COUNT(*) FROM job_trace_context").fetchone() == (0,)


def test_an_existing_database_without_the_table_opens_and_gains_it(tmp_path) -> None:
    """No migration: the schema script creates the side table on open; old rows are untouched."""
    path = tmp_path / "old.sqlite3"
    old_schema = [stmt for stmt in SCHEMA if "job_trace_context" not in stmt]
    with sqlite3.connect(path) as con:
        for stmt in old_schema:
            con.executescript(stmt)
    store = JobStore(path)
    assert store.trace_context("nothing") is None
    with sqlite3.connect(path) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "job_trace_context" in tables


def test_the_table_inventory_is_additive_only() -> None:
    """The persistence inventory: the original seven job tables are unchanged; two side tables were added.

    Additions so far: ``job_trace_context`` (OpenTelemetry traceparent) and ``job_status`` (live run feedback, Extract).
    Each is documented beside its ``CREATE`` in ``app/modules/jobs/store.py``; a new table is appended here, never reordered.
    """
    names = [stmt.split("EXISTS ")[1].split(" ")[0] for stmt in SCHEMA]
    assert names == ["leases", "jobs", "job_stages", "job_units", "job_effects", "job_events", "job_logs",
                     "job_trace_context", "job_status"]
    original = {"leases", "jobs", "job_stages", "job_units", "job_effects", "job_events", "job_logs"}
    assert original <= set(names) and len(names) == len(set(names))  # nothing dropped, nothing duplicated
    assert "traceparent" not in next(s for s in SCHEMA if " jobs (" in s)  # the jobs table itself is untouched
    assert "phase" not in next(s for s in SCHEMA if " jobs (" in s)


@pytest.mark.parametrize("value,ok", [
    ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", True),
    ("00-00000000000000000000000000000000-00f067aa0ba902b7-01", False),
    ("garbage", False), ("", False), (None, False),
])
def test_traceparent_validation(value: str | None, ok: bool) -> None:
    from app.modules.tracing import runtime

    assert runtime.valid_traceparent(value) is ok
