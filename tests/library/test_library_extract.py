"""T39: the ``library_extract`` job (fake LLM only; no network, no real Orchestrator)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
import threading
from typing import Any

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph import kg_update
from app.modules.jobs import JobService, JobStore
from app.modules.library import LIBRARY_EXTRACT_KIND, library_inputs, register_library_jobs
from app.modules.library import bundles as B
from app.modules.library.extraction import EXTRACTOR_REVISION, LibraryExtractor, bundle_lock, resolve_backend
from app.modules.library.handlers import library_handlers
from app.modules.library.kg_service import KgLibrary
from app.modules.library.probe import ProbeResult
from app.modules.library.schemas import list_schemas
from app.modules.library.service import LibraryService
from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.store import TermStore
from tests.jobs.jobs_fakes import Clock, Crash
from tests.security.security_fakes import local_settings

SCHEMAS = sorted(c["name"] for c in list_schemas())
SCHEMA, OTHER_SCHEMA = SCHEMAS[0], SCHEMAS[-1]
KEY_ENV = {"CBORG_API_KEY": "test-not-a-real-key"}


class FakeOrchestrator:
    """A fake LLM: each line of a "PDF" is a page, ``term|category`` the terms found on it."""

    instances: list[FakeOrchestrator] = []

    def __init__(self, *, model: str, output_file: str, schema_path: str, backend: Any = None, hook: Any = None) -> None:
        self.model, self.schema_path, self.backend, self.hook = model, schema_path, backend, hook
        self.store = TermStore(output_file)
        self.calls: list[str] = []
        FakeOrchestrator.instances.append(self)

    def process_pdf(self, pdf_path: str) -> int:
        name = Path(pdf_path).name
        self.calls.append(name)
        if self.hook:
            self.hook(self, name)
        raw = Path(pdf_path).read_text(encoding="utf-8")
        if raw.startswith("BAD"):
            logging.getLogger("app.modules.term_extractor.orchestrator").error("Cannot open PDF %s", name)
            return 0
        pages = raw.splitlines()
        self.store.increment("processed_pages_total", len(pages))
        with_terms = 0
        for number, line in enumerate(pages, start=1):
            if "|" not in line:
                continue
            term, category = line.split("|", 1)
            self.store.upsert(TermRecord(term=term, definition=f"{term} defined by {self.model}", category=category,
                                         pages=[number], source_papers=[name]))
            with_terms += 1
        logging.getLogger("app.modules.term_extractor.orchestrator").info("Finished '%s': %d pages", name, len(pages))
        self.store.save()
        self.store.increment("processed_files")
        self.store.increment("processed_pages_with_terms", with_terms)
        return with_terms

    def process_directory(self, data_dir: str) -> dict[str, Any]:  # the CLI path
        for pdf in sorted(Path(data_dir).rglob("*.pdf")):
            self.process_pdf(str(pdf))
        self.store.assign_importance()
        self.store.save()
        return {"status": "success"}


class World:
    def __init__(self, tmp_path: Path, *, env: dict[str, str] | None = None, hook: Any = None,
                 clock: Clock | None = None) -> None:
        self.tmp = tmp_path
        self.lib = tmp_path / "papers"
        self.lib.mkdir()
        self.service = LibraryService(self.lib, tmp_path / "state" / "library" / "catalog.sqlite",
                                      settings=local_settings(),
                                      probe=lambda p: ProbeResult(status="ok", pages=2, page_text="x"))
        self.store = B.BundleStore(tmp_path / "state" / "kgs")
        self.hook = hook
        self.factory_calls: list[dict[str, Any]] = []
        self.extractor = LibraryExtractor(self.service, self.store, env=KEY_ENV if env is None else env,
                                          factory=self.factory)
        settings = local_settings()
        kwargs = {"lease_seconds": 60, "clock": clock} if clock else {}
        self.jobs = JobService(JobStore(tmp_path / "jobs.sqlite3", **kwargs), settings=settings)
        register_library_jobs(self.jobs, settings, handlers=library_handlers(self.service, extractor=self.extractor))
        self.kg = KgLibrary(self.store, papers=self.service)

    def factory(self, **kw: Any) -> FakeOrchestrator:
        self.factory_calls.append(kw)
        return FakeOrchestrator(model=kw["model"], output_file=kw["output_file"], schema_path=kw["schema_path"],
                                backend=kw["backend"], hook=self.hook)

    def put(self, name: str, text: str) -> None:
        (self.lib / name).write_text(text, encoding="utf-8")

    def ids(self, *names: str) -> list[str]:
        self.service.refresh(max_probe=0)
        by_rel = {r["relpath"]: r["id"] for r in self.service.catalog.rows()}
        return [by_rel[n] for n in names]

    def params(self, names: list[str], *, bundle: str = "run1", model: str = "m-1", schema: str = SCHEMA,
               **extra: Any) -> dict[str, Any]:
        ids = self.ids(*names)
        return {"bundle": bundle, "papers": ids, "model": model, "schema": schema, "topic": "polymers",
                "confirmation": self.kg.estimate(ids, model, schema)["confirmation"], **extra}

    def submit(self, params: dict[str, Any], nonce: str = "1") -> Any:
        return self.jobs.submit(self.service.worker_context("s"), LIBRARY_EXTRACT_KIND,
                                library_inputs(params, nonce=nonce))

    def run(self, params: dict[str, Any], nonce: str = "1") -> tuple[Any, JobState]:
        handle = self.submit(params, nonce)
        job_id, state = self.jobs.work_one("w")
        assert job_id == handle.job_id
        return handle, state

    def bundle(self, name: str = "run1") -> B.Bundle:
        return self.store.get(name)

    def job_error(self, handle: Any) -> str:
        record = self.jobs.store.get(handle.job_id)
        return json.dumps(record.__dict__, default=str)


@pytest.fixture(autouse=True)
def _reset_instances():
    FakeOrchestrator.instances.clear()


def _no_date(kg: dict[str, Any]) -> dict[str, Any]:
    """The KG without the wall-clock extraction date (the only field that differs between two runs)."""
    out = json.loads(json.dumps(kg))
    out.get("metadata", {}).pop("extraction_date", None)
    return out


def _three(world: World) -> list[str]:
    world.put("a.pdf", "P3HT|Polymer\nGIWAXS|ExperimentalTechnique")
    world.put("b.pdf", "P3HT|Polymer\nPCBM|Molecule")
    world.put("c.pdf", "RSoXS|ExperimentalTechnique")
    return ["a.pdf", "b.pdf", "c.pdf"]


def test_end_to_end_bundle_kg_equals_the_cli_path_and_bookkeeping_has_the_coordinator_shape(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    handle, state = world.run(world.params(names))
    assert state is JobState.SUCCEEDED
    bundle = world.bundle()
    # The CLI path for the same inputs: process_directory, then rebuild_kg.
    cli = tmp_path / "cli"
    shutil.copytree(world.lib, cli / "pdfs")
    orch = FakeOrchestrator(model="m-1", output_file=str(cli / "terms.json"), schema_path="x")
    orch.process_directory(str(cli / "pdfs"))
    kg_update.rebuild_kg(str(cli / "terms.json"), str(cli / "kg.json"))
    assert _no_date(json.loads(bundle.file(B.KG_FILE).read_text())) == _no_date(json.loads((cli / "kg.json").read_text()))
    ours, theirs = json.loads(bundle.file(B.TERMS_FILE).read_text()), json.loads((cli / "terms.json").read_text())
    assert ours["terms"] == theirs["terms"]
    assert json.loads(bundle.file(B.PROCESSED_FILE).read_text()) == names
    manifest = json.loads(bundle.file(B.EXTRACTION_MANIFEST_FILE).read_text())
    assert sorted(manifest["papers"]) == names
    entry = manifest["papers"]["a.pdf"]
    assert set(entry) == {"filename", "pdf_sha256", "extraction_state", "timestamp", "terms_json",
                          "processed_pages_total", "processed_pages_with_terms", "full"}
    assert entry["extraction_state"] == "full" and entry["processed_pages_total"] == 2
    assert set(entry["full"]) == {"timestamp", "terms_json", "processed_pages_total", "processed_pages_with_terms"}
    assert bundle.manifest["model"] == "m-1" and bundle.manifest["backend"] == "cborg"
    assert [p["name"] for p in bundle.manifest["papers"]] == names
    assert bundle.manifest["schema"]["id"] == SCHEMA and bundle.manifest["topic"] == "polymers"
    assert {t["importance"] for t in ours["terms"]} >= {"high"}  # assign_importance ran (P3HT is in two papers)


def test_progress_and_logs_are_visible(tmp_path):
    world = World(tmp_path)
    handle, state = world.run(world.params(_three(world)))
    ctx = world.service.worker_context("s")
    progress = world.jobs.progress(ctx, handle.job_id)
    assert state is JobState.SUCCEEDED and "run" in json.dumps(progress) and "units_total" in json.dumps(progress)
    messages = [log.message for log in world.jobs.logs(ctx, handle.job_id)]
    assert any("extracting 3 papers" in m for m in messages)
    assert any("Finished 'a.pdf'" in m for m in messages)  # the extractor's own logging reached the job log
    assert any("extracting b.pdf" in m for m in messages)


def test_killed_worker_resumes_without_redoing_finished_pdfs(tmp_path):
    clock = Clock()

    def hook(orch: FakeOrchestrator, name: str) -> None:
        if name == "c.pdf" and not crashed:
            crashed.append(1)
            raise Crash

    crashed: list[int] = []
    world = World(tmp_path, hook=hook, clock=clock)
    names = _three(world)
    handle = world.submit(world.params(names))
    with pytest.raises(Crash):
        world.jobs.work_one("w")
    first = FakeOrchestrator.instances[-1]
    assert first.calls == ["a.pdf", "b.pdf", "c.pdf"]
    clock.advance(500)  # the dead worker's lease expires
    job_id, state = world.jobs.work_one("w2")
    assert (job_id, state) == (handle.job_id, JobState.SUCCEEDED)
    assert FakeOrchestrator.instances[-1].calls == ["c.pdf"]
    bundle = world.bundle()
    assert json.loads(bundle.file(B.PROCESSED_FILE).read_text()) == names
    assert len(json.loads(bundle.file(B.KG_FILE).read_text())["things"]) >= 4


def test_unit_key_changes_with_model_and_schema_and_pdf_bytes(tmp_path):
    world = World(tmp_path)
    world.put("a.pdf", "x|Polymer")
    world.service.refresh(max_probe=0)
    row = world.service.catalog.rows()[0]
    block = {"id": SCHEMA, "sha256": "h1"}
    base = LibraryExtractor.unit_key(row, block, "m-1")
    assert row["sha256"] in base and EXTRACTOR_REVISION in base
    assert LibraryExtractor.unit_key(row, block, "m-2") != base
    assert LibraryExtractor.unit_key(row, {"id": OTHER_SCHEMA, "sha256": "h1"}, "m-1") != base
    assert LibraryExtractor.unit_key(row, {"id": SCHEMA, "sha256": "h2"}, "m-1") != base
    assert LibraryExtractor.unit_key({**row, "sha256": "other"}, block, "m-1") != base


def test_a_changed_model_reruns_finished_pdfs_in_a_new_job(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    world.run(world.params(names, model="m-1"))
    assert len(FakeOrchestrator.instances[-1].calls) == 3
    _, state = world.run(world.params(names, model="m-2"), nonce="2")
    assert state is JobState.SUCCEEDED and FakeOrchestrator.instances[-1].calls == names
    assert world.bundle().manifest["model"] == "m-2"
    _, state = world.run(world.params(names, model="m-2"), nonce="3")  # same model and bytes: nothing to redo
    assert state is JobState.SUCCEEDED and FakeOrchestrator.instances[-1].calls == []


def test_a_failed_pdf_is_recorded_and_the_others_finish(tmp_path):
    world = World(tmp_path)
    world.put("a.pdf", "P3HT|Polymer")
    world.put("b.pdf", "BAD")
    world.put("c.pdf", "RSoXS|ExperimentalTechnique")
    handle, state = world.run(world.params(["a.pdf", "b.pdf", "c.pdf"]))
    assert state is JobState.FAILED  # incomplete coverage: the job says so, a retry resumes
    bundle = world.bundle()
    assert json.loads(bundle.file(B.PROCESSED_FILE).read_text()) == ["a.pdf", "c.pdf"]
    assert sorted(json.loads(bundle.file(B.EXTRACTION_MANIFEST_FILE).read_text())["papers"]) == ["a.pdf", "c.pdf"]
    assert [p["name"] for p in bundle.manifest["papers"]] == ["a.pdf", "c.pdf"]
    ctx = world.service.worker_context("s")
    progress = world.jobs.progress(ctx, handle.job_id)
    assert progress["phases"]["other"]["counts"] == {"failed": 1} and "incomplete coverage" in json.dumps(progress)
    assert FakeOrchestrator.instances[-1].calls == ["a.pdf", "b.pdf", "c.pdf"]


def test_cancel_stops_between_pdfs_and_keeps_the_finished_ones(tmp_path):
    holder: dict[str, Any] = {}

    def hook(orch: FakeOrchestrator, name: str) -> None:
        if name == "b.pdf":
            world.jobs.store.request_stop(holder["id"], actor="op")

    world = World(tmp_path, hook=hook)
    names = _three(world)
    handle = world.submit(world.params(names))
    holder["id"] = handle.job_id
    assert world.jobs.work_one("w") == (handle.job_id, JobState.CANCELLED)
    assert FakeOrchestrator.instances[-1].calls == ["a.pdf", "b.pdf"]  # c.pdf never started
    bundle = world.bundle()
    assert json.loads(bundle.file(B.PROCESSED_FILE).read_text()) == ["a.pdf", "b.pdf"]
    kg = json.loads(bundle.file(B.KG_FILE).read_text())
    assert kg["things"]  # the KG was rebuilt from what finished
    assert [p["name"] for p in bundle.manifest["papers"]] == ["a.pdf", "b.pdf"]


def test_no_backend_fails_closed_before_any_client_or_bundle(tmp_path):
    world = World(tmp_path, env={})
    names = _three(world)
    handle, state = world.run(world.params(names))
    assert state is JobState.FAILED
    assert world.factory_calls == [] and not world.store.exists("run1")
    assert "no LLM backend is configured" in world.job_error(handle)
    assert resolve_backend({}) is None and resolve_backend({"CBORG_API_KEY": "  "}) is None
    assert resolve_backend({"F2W_EXTRACT_BACKEND": "ollama"})["name"] == "ollama"
    with pytest.raises(ContractError) as err:
        world.extractor.backend()
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_default_factory_is_not_reached_without_a_backend(tmp_path, monkeypatch):
    import app.modules.library.extraction as X

    monkeypatch.setattr(X, "default_orchestrator_factory",
                        lambda **kw: pytest.fail("a client must not be built without a backend"))
    world = World(tmp_path, env={})
    world.extractor.factory = X.default_orchestrator_factory
    _, state = world.run(world.params(_three(world)))
    assert state is JobState.FAILED


def test_a_wrong_or_stale_confirmation_is_refused(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    params = world.params(names)
    handle, state = world.run({**params, "confirmation": "0" * 64})
    assert state is JobState.FAILED and world.factory_calls == [] and not world.store.exists("run1")
    assert "cost confirmation does not match" in world.job_error(handle)
    # a token for another model does not carry over
    other = world.params(names, model="m-other")["confirmation"]
    _, state = world.run({**params, "confirmation": other}, nonce="2")
    assert state is JobState.FAILED and world.factory_calls == []


def test_invalid_inputs_fail_without_side_effects(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    good = world.params(names)
    for bad in ({**good, "bundle": "../x"}, {**good, "model": ""}, {**good, "papers": []},
                {**good, "papers": ["nope"]}, {**good, "schema": "unknown@9"}):
        _, state = world.run(bad, nonce=str(len(str(bad))) + str(hash(json.dumps(bad, sort_keys=True))))
        assert state is JobState.FAILED
    assert world.factory_calls == [] and not world.store.exists("run1")


def test_schema_is_locked_per_bundle(tmp_path):
    if OTHER_SCHEMA == SCHEMA:
        pytest.skip("only one schema offered")
    world = World(tmp_path)
    names = _three(world)
    world.run(world.params(names))
    handle, state = world.run(world.params(names, schema=OTHER_SCHEMA, model="m-2"), nonce="2")
    assert state is JobState.FAILED and world.bundle().schema == SCHEMA


def test_continue_from_forks_the_parent_and_keeps_its_terms(tmp_path):
    world = World(tmp_path)
    world.put("a.pdf", "P3HT|Polymer")
    world.put("b.pdf", "PCBM|Molecule")
    world.run(world.params(["a.pdf"], bundle="parent"))
    _, state = world.run(world.params(["b.pdf"], bundle="child", continue_from="parent", topic="more"), nonce="2")
    child = world.bundle("child")
    assert state is JobState.SUCCEEDED and child.manifest["parent"] == "parent"
    assert sorted(p["name"] for p in child.manifest["papers"]) == ["a.pdf", "b.pdf"]
    assert {t["term"] for t in json.loads(child.file(B.TERMS_FILE).read_text())["terms"]} == {"P3HT", "PCBM"}
    assert len(world.bundle("parent").manifest["papers"]) == 1  # the parent is untouched
    assert FakeOrchestrator.instances[-1].calls == ["b.pdf"]


def test_per_bundle_lock_allows_one_extraction_and_frees_on_exit(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    with bundle_lock(world.store, "run1"):
        with pytest.raises(ContractError) as err:
            with bundle_lock(world.store, "run1"):
                pass
        assert err.value.code is ErrorCode.CONFLICT and err.value.retryable
        with bundle_lock(world.store, "other"):  # a different bundle is independent
            pass
        handle, state = world.run(world.params(names))
        assert state is JobState.FAILED and world.factory_calls == []
        assert "another extraction is running" in world.job_error(handle)
    _, state = world.run(world.params(names), nonce="2")
    assert state is JobState.SUCCEEDED


def test_a_busy_log_capture_is_a_retryable_conflict(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        from app.modules.jobs.log_capture import _ACTIVE_LOCK  # the module-level one-capture guard

        with _ACTIVE_LOCK:
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    entered.wait(5)
    try:
        handle, state = world.run(world.params(names))
    finally:
        release.set()
        thread.join()
    assert state is JobState.FAILED and "already being captured" in world.job_error(handle)


def test_the_stub_remains_without_an_extractor(tmp_path):
    settings = local_settings()
    jobs = JobService(JobStore(tmp_path / "j.sqlite3"), settings=settings)
    register_library_jobs(jobs, settings, handlers=library_handlers(LibraryService(None, tmp_path / "c.sqlite")))
    handle = jobs.submit(local_ctx(settings), LIBRARY_EXTRACT_KIND, library_inputs(nonce="1"))
    assert jobs.work_one("w") == (handle.job_id, JobState.FAILED)


def local_ctx(settings: Any):
    from app.modules.security.authorization import local_operator_context

    return local_operator_context(settings, request_id="r")


def test_a_second_claim_while_the_first_run_holds_the_bundle_requeues_instead_of_failing(tmp_path):
    world = World(tmp_path)
    names = _three(world)
    world.jobs.requeue_backoff = 0
    handle = world.submit(world.params(names))
    with bundle_lock(world.store, "run1", handle.job_id):  # the earlier run of this very job, still alive
        job_id, state = world.jobs.work_one("w2")
        assert (job_id, state) == (handle.job_id, JobState.QUEUED)
        assert world.factory_calls == []
    job_id, state = world.jobs.work_one("w2")
    assert (job_id, state) == (handle.job_id, JobState.SUCCEEDED)


def test_finished_papers_survive_a_crash_in_the_manifest(tmp_path):
    clock = Clock()

    def hook(orch: FakeOrchestrator, name: str) -> None:
        if name == "c.pdf":
            raise Crash

    world = World(tmp_path, hook=hook, clock=clock)
    names = _three(world)
    world.submit(world.params(names))
    with pytest.raises(Crash):
        world.jobs.work_one("w")
    # no finalize ran, yet the bundle already lists the two finished papers (paper_count is not 0)
    assert [p["name"] for p in world.bundle().manifest["papers"]] == ["a.pdf", "b.pdf"]
    assert world.store.list()[0].summary()["paper_count"] == 2


def test_a_run_that_lost_its_lease_records_nothing_and_stops_calling_the_llm(tmp_path):
    clock = Clock()
    seen: list[bool] = []

    def hook(orch: FakeOrchestrator, name: str) -> None:
        if name == "b.pdf":
            clock.advance(61)  # the lease (60 s) lapses while this run is alive
            assert world.jobs.store.claim_next("w2") is not None  # and a second owner takes the job over
            seen.append(orch.cancel_check())  # type: ignore[attr-defined]

    world = World(tmp_path, hook=hook, clock=clock)
    names = _three(world)
    handle = world.submit(world.params(names))
    job_id, state = world.jobs.work_one("w")
    assert (job_id, state) == (handle.job_id, None)  # the stale owner stopped without applying anything
    assert seen == [True]  # the orchestrator was told to stop
    assert [p["name"] for p in world.bundle().manifest["papers"]] == ["a.pdf"]  # b.pdf was not recorded by the stale run
    assert json.loads(world.bundle().file(B.PROCESSED_FILE).read_text()) == ["a.pdf"]
    record = world.jobs.store.get(handle.job_id)
    assert record.state is JobState.RUNNING  # still the new owner's: the stale one never marked it failed
