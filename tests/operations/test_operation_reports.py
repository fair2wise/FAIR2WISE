"""T19C: the shared operation report contract, bounds, D19 guard, artifacts and the job wrapper."""

from __future__ import annotations

import json

import pytest

from app.modules import llm_admission
from app.modules.contracts import ContractError, ErrorCode, ToolStatus
from app.modules.contracts.jobs import JobState
from app.modules.jobs.service import JobService
from app.modules.jobs.store import JobStore
from app.modules.jobs.keys import OperationInputs
from app.modules.literature.store import FileDocumentStore
from app.modules.operations.reports import (
    MAX_RESULT_CHARS,
    OPERATION_JOB_KIND,
    Coverage,
    OperationReport,
    ScanControl,
    check_no_source_material,
    fit_report,
    operation_dispatch,
    register_operation_jobs,
    report_to_result,
    store_report_artifact,
)
from tests.runtime.runtime_fakes import local_ctx


def _report(complete=True, preview_items=10, **kw):
    base = dict(operation="kg_compute_statistics", operation_version="1.0.0", method="kg-statistics/1",
                population="synthetic", counting_units={"entities": "distinct node ids"},
                coverage=Coverage.of(scan_complete=complete, records_scanned=40,
                                     reasons=() if complete else ("scan bound of 40 records reached",)),
                metrics={"entities": 1234, "relations": 99},
                preview={"examples": [[f"matkg:E{i:05d}", "x" * 40] for i in range(preview_items)]})
    base.update(kw)
    return OperationReport(**base)


def test_completeness_follows_the_scan_and_cannot_be_set_independently():
    assert Coverage.of(scan_complete=True, records_scanned=3).completeness == "complete"
    partial = Coverage.of(scan_complete=False, records_scanned=3, reasons=["bound"])
    assert partial.completeness == "partial" and partial.records_expected is None  # unknown total is not zero


def test_partial_scan_is_never_ok_and_says_so():
    result = report_to_result("kg_compute_statistics", _report(complete=False))
    assert result.status is ToolStatus.PARTIAL and result.error_code is None
    assert result.data["coverage"]["completeness"] == "partial"
    assert result.warnings[0].startswith("partial:") and "lower bounds" in result.warnings[0]
    ok = report_to_result("kg_compute_statistics", _report())
    assert ok.status is ToolStatus.OK and not any(w.startswith("partial") for w in ok.warnings)


def test_display_truncation_keeps_totals_and_completeness():
    big = _report(preview_items=5000)
    fitted = fit_report(big)
    assert len(json.dumps(fitted.to_data(), sort_keys=True)) <= MAX_RESULT_CHARS
    assert fitted.display_truncated and fitted.metrics == big.metrics and fitted.coverage == big.coverage
    assert fitted.completeness == "complete" and len(fitted.preview["examples"]) <= 50
    assert any("display truncated" in w for w in fitted.warnings)
    small = fit_report(_report(preview_items=3))
    assert not small.display_truncated and len(small.preview["examples"]) == 3
    result = report_to_result("kg_compute_statistics", big)
    assert result.status is ToolStatus.OK and result.data["metrics"]["entities"] == 1234


@pytest.mark.parametrize("payload", [
    {"preview": {"x": [{"definition": "a synthetic definition"}]}},
    {"metrics": {"context_snippets": []}},
    {"preview": {"rows": [["matkg:A", "/Users/someone/papers/x.pdf"]]}},
    {"scope": {"terms_path": "storage/terminology/x.json"}},
])
def test_source_material_and_paths_never_enter_a_report(payload):
    with pytest.raises(ContractError) as err:
        check_no_source_material(payload)
    assert err.value.code is ErrorCode.INTERNAL
    with pytest.raises(ContractError):
        fit_report(_report(**{k: v for k, v in payload.items() if k in ("preview", "metrics", "scope")}))


def test_labels_with_slashes_are_not_paths():
    check_no_source_material({"preview": {"rows": [["PS-b-PMMA", "1/2 ratio", "rel:measured_by", "10.1/abc"]]}})


def test_scan_control_bounds_and_deadline():
    control = ScanControl(3)
    assert [control.tick() for _ in range(4)] == [True, True, True, False]
    assert "scan bound of 3" in control.stopped_reason
    seen = []
    ScanControl(10, checkpoint=seen.append, every=2).tick(5)
    assert seen == [5]
    with pytest.raises(ContractError):
        ScanControl(0)


def test_full_report_is_a_private_content_addressed_artifact(tmp_path):
    store = FileDocumentStore(tmp_path / "store")
    ctx = local_ctx()
    report = _report(preview_items=500)
    ref = store_report_artifact(store, ctx, report)
    assert ref.startswith("sha256:")
    stored = json.loads(store.get(ctx, ref))
    assert stored["metrics"] == report.metrics and len(stored["preview"]["examples"]) == 500
    assert store_report_artifact(None, ctx, report) is None


# -- jobs ------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _admission():
    llm_admission.reset_controllers()
    yield
    llm_admission.reset_controllers()


class _Suite:
    """Operation suite whose scan honours the job's checkpoint (cancellation lands between records)."""

    def __init__(self, records=50, stop_at=None, store=None):
        self.records, self.stop_at, self.store, self.job_id, self.calls = records, stop_at, store, None, []

    def operation_names(self):
        return ("kg_compute_statistics",)

    def compute_operation(self, ctx, operation, args, control):
        self.calls.append((operation, dict(args), ctx.corpus_scope))
        complete = True
        for i in range(self.records):
            if self.stop_at is not None and i == self.stop_at:
                self.store.request_stop(self.job_id, actor="local-operator")
            if not control.tick():
                complete = False
                break
        return _report(complete=complete, coverage=Coverage.of(scan_complete=complete, records_scanned=control.count))


def _jobs(tmp_path, suite, **kw):
    store = JobStore(tmp_path / "jobs.sqlite3")
    service = JobService(store)
    artifacts = FileDocumentStore(tmp_path / "artifacts")
    register_operation_jobs(service, operation_dispatch(suite),
                            lambda job: local_ctx(corpus_scope=frozenset(job.inputs.corpus_scope)),
                            artifact_store=artifacts, **kw)
    return store, service, artifacts


def _submit(service, ctx=None):
    inputs = OperationInputs(corpus_scope=("rsoxs_v4",),
                             params={"operation": "kg_compute_statistics", "args": {"graph_id": "rsoxs_v4"}})
    return service.submit(ctx or local_ctx(capabilities=frozenset({"ops.run", "chat.ask"})), OPERATION_JOB_KIND,
                          inputs)


def test_operation_job_runs_records_progress_and_stores_the_artifact(tmp_path):
    suite = _Suite(records=2500)
    store, service, artifacts = _jobs(tmp_path, suite)
    handle = _submit(service)
    job_id, state = service.work_one()
    assert job_id == handle.job_id and state is JobState.SUCCEEDED
    assert suite.calls == [("kg_compute_statistics", {"graph_id": "rsoxs_v4"}, frozenset({"rsoxs_v4"}))]
    (stage,) = store.stages(job_id)
    assert stage.result["coverage"]["completeness"] == "complete" and stage.result["metrics"]["entities"] == 1234
    assert stage.progress["records_scanned"] == 2500 and len(stage.artifact_refs) == 1
    assert json.loads(artifacts.get(local_ctx(), stage.artifact_refs[0]))["operation"] == "kg_compute_statistics"


def test_operation_job_needs_ops_run(tmp_path):
    _, service, _ = _jobs(tmp_path, _Suite())
    with pytest.raises(ContractError) as err:
        _submit(service, local_ctx(capabilities=frozenset({"chat.ask"})))
    assert err.value.code is ErrorCode.DENIED


def test_cancelling_a_running_operation_job_stops_between_records(tmp_path):
    suite = _Suite(records=5000, stop_at=1500)
    store, service, _ = _jobs(tmp_path, suite)
    suite.store = store
    suite.job_id = _submit(service).job_id
    _, state = service.work_one()
    assert state is JobState.CANCELLED
    assert store.stages(suite.job_id)[0].result is None


def test_unknown_operation_in_a_job_fails_typed(tmp_path):
    store, service, _ = _jobs(tmp_path, _Suite())
    inputs = OperationInputs(corpus_scope=("rsoxs_v4",), params={"operation": "run_python", "args": {}})
    handle = service.submit(local_ctx(capabilities=frozenset({"ops.run"})), OPERATION_JOB_KIND, inputs)
    _, state = service.work_one()
    assert state is JobState.FAILED
    assert store.stages(handle.job_id)[0].error_code is ErrorCode.INVALID_INPUT


def test_dispatch_rejects_duplicate_operation_names():
    with pytest.raises(ValueError):
        operation_dispatch(_Suite(), _Suite())
