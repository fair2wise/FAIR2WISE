"""T10: replay-safe pipeline, compatible-stage reuse, pauses, quarantine, crash matrix (jobs part)."""

from __future__ import annotations

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.jobs import (
    AwaitReview,
    JobService,
    JobStore,
    Pipeline,
    PauseBudget,
    PauseProvider,
    QuarantineStage,
    StageResult,
    StageSpec,
)
from app.modules.jobs.stages import BudgetExhausted, WorkUnit, acquire_stage, chunked_stage
from app.modules.jobs.telemetry import job_progress
from tests.jobs.jobs_fakes import Clock, Crash, inputs, local_ctx, local_settings
from tests.literature.lit_fakes import local_ctx as lit_ctx, minimal_pdf_bytes


class Calls(dict):
    def hit(self, name):
        self[name] = self.get(name, 0) + 1


def make(tmp_path, specs, clock=None):
    clock = clock or Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    service = JobService(store)
    service.register("ingest", Pipeline(specs))
    return store, service, clock


def three_stage(calls, fail=None):
    def stage(name):
        def run(sc):
            calls.hit(name)
            if fail and fail.get(name):
                raise fail[name]
            return StageResult(effects=(f"{name}:e",), artifact_refs=(f"art:{name}",), progress={"n": 1})

        return run

    return [
        StageSpec("parse", stage("parse"), phase="parsing", relevant=("source_sha256", "parser_version")),
        StageSpec(
            "extract",
            stage("extract"),
            phase="extraction",
            depends_on=("parse",),
            relevant=("extractor_version", "model", "prompt_revision", "schema_revision"),
        ),
        StageSpec(
            "canonicalize",
            stage("canonicalize"),
            phase="validation",
            depends_on=("extract",),
            relevant=("vocabulary_revision",),
        ),
    ]


def submit(service, **kw):
    return service.submit(local_ctx(), "ingest", inputs(**kw))


def test_full_run_then_rerun_skips_every_compatible_stage(tmp_path):
    calls = Calls()
    store, service, _ = make(tmp_path, three_stage(calls))
    handle = submit(service)
    assert service.work_one() == (handle.job_id, JobState.SUCCEEDED)
    assert calls == {"parse": 1, "extract": 1, "canonicalize": 1}
    store.transition_without_lease(handle.job_id, JobState.QUEUED, reason="x", actor="t") if False else None
    # A job with identical inputs maps to the same job: it is already succeeded, nothing to run.
    assert submit(service).job_id == handle.job_id and service.work_one() is None


def test_changed_vocabulary_invalidates_only_canonicalization(tmp_path):
    calls = Calls()
    store, service, _ = make(tmp_path, three_stage(calls))
    h = submit(service)
    service.work_one()
    # Same job id would change with new inputs, so emulate a revision bump on the stored job: resume a clone.
    h2 = submit(service, vocabulary_revision="v2")
    assert h2.job_id != h.job_id
    # Resume semantics: copy completed rows of the first job into the second via the shared stage keys.
    pipeline = service.kind("ingest").pipeline
    k1 = pipeline.keys(inputs())
    k2 = pipeline.keys(inputs(vocabulary_revision="v2"))
    assert k1["parse"] == k2["parse"] and k1["extract"] == k2["extract"]
    assert k1["canonicalize"] != k2["canonicalize"]
    k3 = pipeline.keys(inputs(parser_version="p9"))
    assert all(k3[s] != k1[s] for s in k1)  # parser change invalidates everything downstream of parse


def test_resume_after_input_change_reruns_only_affected_stages(tmp_path):
    calls = Calls()
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    pipeline = Pipeline(three_stage(calls))
    service = JobService(store)
    service.register("ingest", pipeline)
    h = submit(service)
    service.work_one()
    # Mutate the stored job inputs in place (what a re-resolved vocabulary revision would do on resume).
    import json

    row = store.db.one("SELECT inputs FROM jobs WHERE job_id=?", (h.job_id,))
    data = json.loads(row["inputs"])
    data["vocabulary_revision"] = "v2"
    with store.db.tx() as con:
        con.execute("UPDATE jobs SET inputs=?, state='queued' WHERE job_id=?", (json.dumps(data), h.job_id))
    calls.clear()
    assert service.work_one() == (h.job_id, JobState.SUCCEEDED)
    assert dict(calls) == {"canonicalize": 1}
    stages = {s.stage: s.status for s in store.stages(h.job_id)}
    assert stages["parse"] is StageOutcome.SKIPPED_COMPATIBLE
    assert stages["extract"] is StageOutcome.SKIPPED_COMPATIBLE
    assert stages["canonicalize"] is StageOutcome.COMPLETED
    calls.clear()
    data["parser_version"] = "p2"
    with store.db.tx() as con:
        con.execute("UPDATE jobs SET inputs=?, state='queued' WHERE job_id=?", (json.dumps(data), h.job_id))
    service.work_one()
    assert dict(calls) == {"parse": 1, "extract": 1, "canonicalize": 1}


@pytest.mark.parametrize(
    "exc,state,code",
    [
        (PauseBudget("cap reached"), JobState.PAUSED_BUDGET, ErrorCode.BUDGET_EXHAUSTED),
        (PauseProvider("429"), JobState.PAUSED_PROVIDER, ErrorCode.UNAVAILABLE),
        (AwaitReview("needs a human"), JobState.AWAITING_REVIEW, None),
    ],
)
def test_pauses_are_job_states_not_stage_failures(tmp_path, exc, state, code):
    calls = Calls()
    store, service, _ = make(tmp_path, three_stage(calls, fail={"extract": exc}))
    h = submit(service)
    assert service.work_one() == (h.job_id, state)
    rows = {s.stage: s for s in store.stages(h.job_id)}
    assert rows["parse"].status is StageOutcome.COMPLETED
    assert rows["extract"].status is StageOutcome.PENDING and rows["extract"].error_code is code
    assert "canonicalize" not in rows
    # resume: parse is reused, extract runs again
    calls.clear()
    service.resume(local_ctx(), h.job_id)
    service.kind("ingest").pipeline["extract"]  # still registered
    calls["x"] = 0
    assert service.work_one()[1] in (state, JobState.SUCCEEDED)
    assert "parse" not in calls


def test_quarantine_and_failure_leave_a_reason(tmp_path):
    store, service, _ = make(tmp_path, three_stage(Calls(), fail={"extract": QuarantineStage("coverage lies")}))
    h = submit(service)
    assert service.work_one() == (h.job_id, JobState.FAILED)
    row = store.stage(h.job_id, "extract")
    assert row.status is StageOutcome.QUARANTINED and "coverage lies" in row.error
    assert "quarantined" in store.get(h.job_id).pause_reason


def test_unexpected_error_fails_job_without_leaking_message(tmp_path):
    store, service, _ = make(
        tmp_path, three_stage(Calls(), fail={"parse": RuntimeError("secret sk-abc123456789012")})
    )
    h = submit(service)
    assert service.work_one() == (h.job_id, JobState.FAILED)
    row = store.stage(h.job_id, "parse")
    assert row.error_code is ErrorCode.INTERNAL and "sk-" not in row.error


def test_verify_failure_blocks_completion(tmp_path):
    spec = StageSpec("parse", lambda sc: StageResult(), phase="parsing", verify=lambda sc, r: "output missing")
    store, service, _ = make(tmp_path, [spec])
    h = submit(service)
    assert service.work_one() == (h.job_id, JobState.FAILED)
    assert store.stage(h.job_id, "parse").status is StageOutcome.QUARANTINED


def test_crash_mid_stage_then_recovery_after_lease_expiry(tmp_path):
    calls = Calls()
    clock = Clock()
    store, service, _ = make(tmp_path, three_stage(calls, fail={"extract": Crash()}), clock)
    h = submit(service)
    with pytest.raises(Crash):
        service.work_one("w1")
    assert store.get(h.job_id).state is JobState.RUNNING  # a dead process leaves the job running
    assert service.work_one("w2") is None  # lease still live: nobody steals it early
    clock.advance(61)
    service.kind("ingest").pipeline.specs[1].__class__  # noqa
    # Replace the crashing handler: the retry succeeds.
    store2 = store
    svc2 = JobService(store2)
    svc2.register("ingest", Pipeline(three_stage(calls)))
    calls.clear()
    assert svc2.work_one("w2") == (h.job_id, JobState.SUCCEEDED)
    assert "parse" not in calls  # the completed stage was reused
    events = store.event_details(h.job_id)
    assert any(e.get("recovered_after_expiry") for e in events)


def test_lease_expiry_with_live_worker_cannot_apply_later_mutations(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    state = {}

    def slow(sc):
        sc.progress(n=1)
        clock.advance(120)  # the worker stalls past its lease while still alive
        taken = store.claim_next("rescuer")
        state["rescuer"] = taken
        sc.progress(n=2)  # stale owner: refused
        return StageResult()

    service = JobService(store)
    service.register("ingest", Pipeline([StageSpec("parse", slow, phase="parsing")]))
    h = submit(service)
    assert service.work_one("w1") == (h.job_id, None)  # LeaseLost: worker stops, applies nothing
    assert state["rescuer"] is not None
    assert store.stage(h.job_id, "parse").status is StageOutcome.RUNNING  # no completion by the stale owner
    assert store.get(h.job_id).state is JobState.RUNNING


def test_stop_request_cancels_at_boundary(tmp_path):
    store, service, _ = make(tmp_path, [])
    holder = {}

    def first(sc):
        service.cancel(local_ctx(), sc.job.job_id)  # operator cancels while running
        return StageResult()

    service2 = JobService(store)
    service2.register(
        "ingest",
        Pipeline([StageSpec("a", first), StageSpec("b", lambda sc: holder.setdefault("b", 1) and StageResult())]),
    )
    h = service2.submit(local_ctx(), "ingest", inputs())
    assert service2.work_one() == (h.job_id, JobState.CANCELLED)
    assert "b" not in holder


# --- chunked stage / coverage ------------------------------------------------------------------------------


def chunk_pipeline(processed, fail_ids=(), crash_on=None, budget_on=None):
    def units(sc):
        return [WorkUnit(f"c{i}", f"k{i}-{sc.inputs.extractor_version}") for i in range(4)]

    def process(sc, unit):
        if unit.unit_id == crash_on:
            raise Crash()
        if unit.unit_id == budget_on:
            raise BudgetExhausted("cap")
        if unit.unit_id in fail_ids:
            raise RuntimeError("bad chunk")
        processed.append(unit.unit_id)
        return {"claims": 1}

    return [
        StageSpec("extract", chunked_stage(units, process), phase="extraction", relevant=("extractor_version",))
    ]


def test_crash_during_extraction_retains_chunks_and_blocks_publication_on_incomplete(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    done = []
    svc = JobService(store)
    svc.register("ingest", Pipeline(chunk_pipeline(done, crash_on="c2")))
    h = svc.submit(local_ctx(), "ingest", inputs())
    with pytest.raises(Crash):
        svc.work_one("w1")
    assert done == ["c0", "c1"]
    clock.advance(61)
    # restart: c3 fails permanently this time; c0,c1 are reused (not reprocessed)
    done2 = []
    svc2 = JobService(store)
    svc2.register("ingest", Pipeline(chunk_pipeline(done2, fail_ids=("c3",))))
    assert svc2.work_one("w2") == (h.job_id, JobState.FAILED)
    assert done2 == ["c2"]  # only the unfinished chunk ran; c3 failed
    row = store.stage(h.job_id, "extract")
    assert row.status is StageOutcome.FAILED and "incomplete coverage" in row.error
    assert store.unit_counts(h.job_id, "extract") == {"done": 3, "failed": 1}
    # the job never reached succeeded: nothing downstream can treat it as publishable
    assert store.get(h.job_id).state is JobState.FAILED
    # retry with a healthy processor completes only the missing chunk
    store.transition_without_lease(h.job_id, JobState.QUEUED, reason="retry", actor="op")
    done3 = []
    svc3 = JobService(store)
    svc3.register("ingest", Pipeline(chunk_pipeline(done3)))
    assert svc3.work_one("w3") == (h.job_id, JobState.SUCCEEDED)
    assert done3 == ["c3"]


def test_unit_keys_change_with_extractor_so_stale_chunks_are_not_reused(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3", clock=Clock())
    done = []
    svc = JobService(store)
    svc.register("ingest", Pipeline(chunk_pipeline(done)))
    h = svc.submit(local_ctx(), "ingest", inputs())
    svc.work_one()
    assert len(done) == 4
    assert store.get_unit(h.job_id, "extract", "c0", "k0-e1") == {"claims": 1}
    assert store.get_unit(h.job_id, "extract", "c0", "k0-e2") is None


def test_budget_exhaustion_pauses_job_and_keeps_finished_chunks(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3", clock=Clock())
    done = []
    svc = JobService(store)
    svc.register("ingest", Pipeline(chunk_pipeline(done, budget_on="c2")))
    h = svc.submit(local_ctx(), "ingest", inputs())
    assert svc.work_one() == (h.job_id, JobState.PAUSED_BUDGET)
    assert store.unit_counts(h.job_id, "extract")["done"] == 2


# --- acquisition crash (plan 11.3 case 1) -------------------------------------------------------------------


class FakeAcquirer:
    """Same contract as PaperAcquirer: checks the store first, downloads otherwise."""

    def __init__(self, store, data):
        self.store, self.data, self.downloads = store, data, 0

    def acquire(self, ctx, doi):
        from app.modules.literature.acquire import AcquireResult

        for ref in self.store.list_refs(ctx):
            if self.store.describe(ctx, ref).metadata.get("doi") == doi:
                return AcquireResult(doi=doi, status="cached", ref=ref)
        self.downloads += 1
        ref = self.store.put(
            ctx, self.data, media_type="application/pdf", metadata={"doi": doi, "kind": "source_pdf"}
        )
        if getattr(self, "crash_after_download", False):
            raise Crash()
        return AcquireResult(doi=doi, status="acquired", ref=ref)


def test_crash_after_pdf_download_before_manifest_update_recovers_without_second_download(tmp_path):
    from app.modules.literature.store import FileDocumentStore

    docs = FileDocumentStore(tmp_path / "docs")
    acq = FakeAcquirer(docs, minimal_pdf_bytes("paper"))
    acq.crash_after_download = True
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    svc = JobService(store, settings=local_settings())
    spec = StageSpec(
        "acquire",
        acquire_stage(acq, docs, doi_of=lambda sc: sc.inputs.params["doi"], ctx_for=lambda sc: lit_ctx()),
        phase="parsing",
    )
    svc.register("ingest", Pipeline([spec]))
    h = svc.submit(local_ctx(), "ingest", inputs(params={"doi": "10.1/x"}))
    with pytest.raises(Crash):
        svc.work_one("w1")
    assert acq.downloads == 1 and store.stage(h.job_id, "acquire").status is StageOutcome.RUNNING
    clock.advance(61)
    acq.crash_after_download = False
    assert svc.work_one("w2") == (h.job_id, JobState.SUCCEEDED)
    assert acq.downloads == 1  # the verified asset was adopted, not downloaded again
    assert store.stage(h.job_id, "acquire").artifact_refs[0].startswith("sha256:")


def test_corrupt_stored_asset_is_quarantined_not_trusted(tmp_path):
    from app.modules.literature.store import FileDocumentStore

    docs = FileDocumentStore(tmp_path / "docs")
    acq = FakeAcquirer(docs, minimal_pdf_bytes("paper"))
    ref = docs.put(
        lit_ctx(), acq.data, media_type="application/pdf", metadata={"doi": "10.1/x", "kind": "source_pdf"}
    )
    obj = docs._object_path(ref.split(":", 1)[1])
    obj.unlink()
    obj.write_bytes(b"corrupted")
    store = JobStore(tmp_path / "j.sqlite3", clock=Clock())
    svc = JobService(store, settings=local_settings())
    svc.register(
        "ingest",
        Pipeline(
            [
                StageSpec(
                    "acquire",
                    acquire_stage(acq, docs, doi_of=lambda sc: "10.1/x", ctx_for=lambda sc: lit_ctx()),
                    phase="parsing",
                )
            ]
        ),
    )
    h = svc.submit(local_ctx(), "ingest", inputs())
    assert svc.work_one() == (h.job_id, JobState.FAILED)
    assert store.stage(h.job_id, "acquire").status is StageOutcome.QUARANTINED


# --- effects with stable IDs ---


def test_effects_with_stable_ids_are_not_repeated_after_crash(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    world = []
    state = {"crash": True}

    def run(sc):
        sc.run_effect("write-entities", lambda: world.append("entities") or {"n": 1})
        if state["crash"]:
            raise Crash()
        sc.run_effect("write-links", lambda: world.append("links") or {"n": 1})
        return StageResult(effects=("write-entities", "write-links"))

    svc = JobService(store)
    svc.register("ingest", Pipeline([StageSpec("import", run, phase="import")]))
    h = svc.submit(local_ctx(), "ingest", inputs())
    with pytest.raises(Crash):
        svc.work_one("w1")
    clock.advance(61)
    state["crash"] = False
    assert svc.work_one("w2") == (h.job_id, JobState.SUCCEEDED)
    assert world == ["entities", "links"]  # entities were not written twice


def test_effect_reconcile_adopts_landed_effect(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    world = {"entities": 0}
    state = {"crash": True}

    def run(sc):
        def effect():
            world["entities"] += 1
            if state["crash"]:
                raise Crash()  # lands, then dies before the done record
            return {"n": 1}

        sc.run_effect("e", effect, reconcile=lambda: {"n": 1} if world["entities"] else None)
        return StageResult()

    svc = JobService(store)
    svc.register("ingest", Pipeline([StageSpec("import", run, phase="import")]))
    h = svc.submit(local_ctx(), "ingest", inputs())
    with pytest.raises(Crash):
        svc.work_one("w1")
    clock.advance(61)
    state["crash"] = False
    assert svc.work_one("w2") == (h.job_id, JobState.SUCCEEDED)
    assert world["entities"] == 1  # reconcile found it; no duplicate


# --- service layer / authorization / telemetry ------------------------------------------------------------


def test_submit_requires_capability_and_scope(tmp_path):
    from tests.literature.lit_fakes import als_user_ctx

    store, service, _ = make(tmp_path, three_stage(Calls()))
    with pytest.raises(ContractError) as err:
        service.submit(als_user_ctx(), "ingest", inputs())
    assert err.value.code is ErrorCode.DENIED
    ctx = als_user_ctx(capabilities=frozenset({"ingest.extract"}), corpus_scope=frozenset({"other"}))
    with pytest.raises(ContractError):
        service.submit(ctx, "ingest", inputs())
    ok = als_user_ctx(capabilities=frozenset({"ingest.extract"}), corpus_scope=frozenset({"rsoxs"}))
    handle = service.submit(ok, "ingest", inputs())
    assert service.get(ok, handle.job_id).state is JobState.QUEUED
    stranger = als_user_ctx(
        capabilities=frozenset({"ingest.extract"}),
        corpus_scope=frozenset({"rsoxs"}),
        principal=ok.principal.model_copy(update={"subject": "bob"}),
    )
    with pytest.raises(ContractError) as hidden:
        service.get(stranger, handle.job_id)
    assert hidden.value.code is ErrorCode.NOT_FOUND


def test_authority_revocation_stops_job_and_notifies_scope_hooks(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3", clock=Clock())
    service = JobService(store)
    seen = []
    service.on_scope_change(seen.append)

    def authority(job, caps):
        raise ContractError(ErrorCode.DENIED, "worker stopped: credential revoked")

    spec = StageSpec("parse", lambda sc: StageResult(), phase="parsing", required_capability="ingest.extract")
    service.register("ingest", Pipeline([spec]), authority=authority)
    h = service.submit(local_ctx(), "ingest", inputs())
    assert service.work_one() == (h.job_id, JobState.FAILED)
    assert (
        store.stage(h.job_id, "parse") is None
        or store.stage(h.job_id, "parse").status is not StageOutcome.COMPLETED
    )
    assert seen and seen[0]["job_id"] == h.job_id


def test_telemetry_separates_phases(tmp_path):
    store, service, _ = make(tmp_path, three_stage(Calls(), fail={"extract": PauseBudget("cap")}))
    h = submit(service)
    service.work_one()
    report = job_progress(store, service.kind("ingest").pipeline, h.job_id)
    assert set(report["phases"]) == {"parsing", "extraction", "validation"}
    assert report["phases"]["parsing"]["counts"] == {"completed": 1}
    assert report["phases"]["extraction"]["counts"] == {"pending": 1}
    assert report["state"] == "paused_budget" and report["stalled"] is False
    assert "percent" not in report


def test_stalled_only_when_running(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", clock=clock, lease_seconds=10_000)
    service = JobService(store)
    pipe = Pipeline([StageSpec("parse", lambda sc: StageResult(), phase="parsing")])
    service.register("ingest", pipe)
    h = submit(service)
    store.claim_next("w")
    clock.advance(400)
    assert job_progress(store, pipe, h.job_id)["stalled"] is True
