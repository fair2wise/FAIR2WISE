"""Job metrics: outcome counter and duration at the single choke point (``JobService.work_one``), queue depth gauge."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules import metrics
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from tests.jobs.jobs_fakes import inputs, local_ctx

from .conftest import points


def build(tmp_path: Any, handler: Any) -> JobService:  # noqa: ANN401
    service = JobService(JobStore(tmp_path / "j.sqlite3"))
    service.register("ingest", Pipeline([StageSpec("extract", handler, phase="extraction")]))
    return service


def submit(service: JobService, n: int) -> list[str]:
    return [service.submit(local_ctx(), "ingest", inputs(source_sha256=f"{i:064x}")).job_id for i in range(n)]


def job_counts(reader: Any) -> dict[tuple[str, str], int]:  # noqa: ANN401
    return {(p.attributes["kind"], p.attributes["outcome"]): p.value for p in points(reader, "f2w.jobs")}


def test_succeeded_and_failed_runs_are_counted_with_duration(tmp_path: Any, reader: Any) -> None:  # noqa: ANN401
    def handler(sc: Any) -> StageResult:  # noqa: ANN401
        if sc.job.job_id == boom_id[0]:
            raise ValueError("secret text in the message")
        return StageResult()

    boom_id: list[str] = [""]
    service = build(tmp_path, handler)
    ids = submit(service, 2)
    boom_id[0] = ids[1]
    while service.work_one("t") is not None:
        pass
    assert job_counts(reader) == {("ingest", "succeeded"): 1, ("ingest", "failed"): 1}
    durations = points(reader, "f2w.job.duration")
    assert sorted(p.count for p in durations) == [1, 1]
    assert list(durations[0].explicit_bounds) == [1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600]
    assert "secret" not in repr(points(reader, "f2w.jobs"))


@pytest.mark.parametrize("state,outcome", [
    (JobState.SUCCEEDED, "succeeded"), (JobState.FAILED, "failed"), (JobState.CANCELLED, "cancelled"),
    (JobState.PAUSED_BUDGET, "paused"), (JobState.PAUSED_PROVIDER, "paused"), (JobState.AWAITING_REVIEW, "paused"),
])
def test_outcome_mapping(reader: Any, state: JobState, outcome: str) -> None:  # noqa: ANN401
    metrics.record_job(kind="ingest", state=state, seconds=1.0, kinds=("ingest",))
    assert job_counts(reader) == {("ingest", outcome): 1}


def test_requeue_and_lease_loss_are_not_finishes_and_unknown_kind_is_other(reader: Any) -> None:  # noqa: ANN401
    metrics.record_job(kind="ingest", state=JobState.QUEUED, seconds=1.0, kinds=("ingest",))
    metrics.record_job(kind="ingest", state=None, seconds=1.0, kinds=("ingest",))
    assert job_counts(reader) == {}
    metrics.record_job(kind="10.1000/paper title", state=JobState.FAILED, seconds=1.0, kinds=("ingest",))
    assert job_counts(reader) == {("other", "failed"): 1}


def test_queue_depth_is_reported_from_the_source(tmp_path: Any, reader: Any) -> None:  # noqa: ANN401
    service = build(tmp_path, lambda sc: StageResult())
    submit(service, 3)
    metrics.set_queue_depth_source(service.queue_depth)
    depth = {(p.attributes["kind"], p.attributes["state"]): p.value for p in points(reader, "f2w.jobs.queue.depth")}
    assert depth == {("ingest", "queued"): 3}


def test_queue_depth_callback_never_raises_and_api_reports_nothing(reader: Any) -> None:  # noqa: ANN401
    assert points(reader, "f2w.jobs.queue.depth") == []  # no source: the API process owns no queue

    def broken() -> list[tuple[str, str, int]]:
        raise RuntimeError("database is locked")

    metrics.set_queue_depth_source(broken)
    assert points(reader, "f2w.jobs.queue.depth") == []


def test_job_metrics_are_free_when_off(tmp_path: Any) -> None:  # noqa: ANN401
    service = build(tmp_path, lambda sc: StageResult())
    submit(service, 1)
    assert service.work_one("t") is not None and not metrics.is_enabled()
