"""T05 slice 6: staged packages, receipts, journal resume and folder intake."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.literature.folder_ingest import ingest_folder
from app.modules.literature.ingested import IngestedPapers, staged_keys
from app.modules.literature.intake import IntakeReport
from app.modules.literature.journal import IngestJournal
from app.modules.literature.parse_service import ParseService
from app.modules.literature.safe_fetch import LocalIntakePolicy
from app.modules.literature.staging import (
    PackageInvalid,
    import_package,
    load_package,
    pending_packages,
    read_staged_import_status,
    save_package,
)
from app.modules.literature.store import FileDocumentStore
from tests.literature.lit_fakes import local_ctx, minimal_pdf_bytes
from tests.literature.test_parsing import FakeParser

PIPELINE = {"parser": "fake-1.0", "extractor": "f2w-default", "schema": "v1"}


@pytest.fixture
def env(tmp_path):
    store = FileDocumentStore(tmp_path / "store")
    return store, ParseService(store, [FakeParser()]), tmp_path / "staged"


def _package(env, text="p", **kw):
    store, parses, root = env
    ref = store.put(
        local_ctx(), minimal_pdf_bytes(text), media_type="application/pdf", metadata={"kind": "source_pdf"}
    )
    record = parses.parse(local_ctx(), ref).as_dict()
    return save_package(
        local_ctx(),
        store,
        root,
        work={"doi": f"10.1/{text}", "title": text},
        source_ref=ref,
        parse_record=record,
        pipeline=PIPELINE,
        **kw,
    )


def test_complete_package_imports_once_with_receipt(env):
    store, _, root = env
    path = _package(env, extraction={"terms": []}, extraction_requested=True)
    payload = load_package(local_ctx(), store, path)
    assert payload["stages"] == {"intake": "complete", "parse": "complete", "extraction": "complete"}
    published = []

    def publish(p):
        published.append(p["work"]["doi"])
        return {"record_id": "cand-1"}

    first = import_package(local_ctx(), store, path, publish=publish)
    again = import_package(local_ctx(), store, path, publish=publish)
    assert first["status"] == "imported" and again["status"] == "already_imported" and published == ["10.1/p"]
    other_target = import_package(local_ctx(), store, path, publish=publish, target="tiled-staging")
    assert other_target["status"] == "imported"  # receipts are per target
    assert pending_packages(root, {}) == []
    assert staged_keys(root) == {"101p"}


def test_incomplete_extraction_is_never_importable(env):
    store, _, root = env
    path = _package(env, "inc", extraction={"terms": []}, extraction_requested=True, incomplete_chunks=[3, 7])
    data = json.loads(path.read_text())
    assert data["complete"] is False and data["incomplete_chunks"] == [3, 7]
    assert data["stages"]["extraction"] == "incomplete" and data["stages"]["parse"] == "complete"
    with pytest.raises(PackageInvalid):
        import_package(local_ctx(), store, path, publish=lambda p: {"record_id": "x"})
    status = read_staged_import_status(root)
    assert status["incomplete_packages"] == 1 and status["incomplete_stages"] == {"extraction": 1}
    parse_only = _package(env, "parseonly")
    assert json.loads(parse_only.read_text())["stages"]["extraction"] == "not_run"
    assert read_staged_import_status(root)["waiting_packages"] == 1


def test_corrupt_asset_and_failed_publish_leave_no_receipt(env):
    store, _, root = env
    path = _package(env, "c")
    payload = json.loads(path.read_text())
    with pytest.raises(ContractError):
        import_package(local_ctx(), store, path, publish=lambda p: {"error": "graph down"})
    assert not path.with_name("imported.json").exists()
    ref = payload["parse"]["asset_refs"][0]
    blob = store.root / "objects" / ref[7:9] / ref[7:]
    os.chmod(blob, 0o644)
    blob.write_bytes(b"bad")
    with pytest.raises(PackageInvalid):
        load_package(local_ctx(), store, path)
    assert len(pending_packages(root, {})) == 1  # still waiting for an explicit retry


def test_pipeline_revision_is_a_new_package(env):
    store, parses, root = env
    first = _package(env, "same")
    ref = json.loads(first.read_text())["source_ref"]
    record = parses.parse(local_ctx(), ref).as_dict()
    second = save_package(
        local_ctx(),
        store,
        root,
        work={"doi": "10.1/same"},
        source_ref=ref,
        parse_record=record,
        pipeline={**PIPELINE, "extractor": "v2"},
    )
    assert first != second and first.exists() and second.exists()


def test_journal_lease_resume_and_stop(tmp_path):
    clock = [1000.0]
    journal = IngestJournal(tmp_path / "j.db", clock=lambda: clock[0])
    key = journal.acquire({"folder": "x"}, "worker-a")
    assert key and journal.acquire({"folder": "y"}, "worker-b") is None  # single live job
    journal.save(key, "op1", "complete", {"filename": "a.pdf", "status": "staged"})
    journal.save(key, "op2", "complete", None, "skipped:openalex_unreachable")
    clock[0] += 500  # worker-a died; lease expired
    assert journal.recoverable()["id"] == key and not journal.heartbeat(key, "worker-b")
    assert journal.acquire({"folder": "x"}, "worker-b") == key
    snap = journal.completion_snapshot(key)
    assert snap["progress"] == 1 and snap["pending"][0]["operation"] == "op2"
    journal.request_stop()
    assert journal.stopped(key)
    journal.finish(key, "worker-b", "stopped")
    assert journal.latest()["state"] == "stopped"


def _intake_fake(statuses):
    def intake(path: Path) -> IntakeReport:
        status = statuses.get(path.name, "ready")
        return IntakeReport(source=str(path), status=status, doi=f"10.1/{path.stem}", title=path.stem, sha256="x")

    return intake


def test_folder_ingest_resumes_and_stops_on_quota(tmp_path):
    root = tmp_path / "intake"
    root.mkdir()
    for name in ("a", "b", "c", "d"):
        (root / f"{name}.pdf").write_bytes(minimal_pdf_bytes(name))
    (root / "link.pdf").symlink_to(root / "a.pdf")
    policy = LocalIntakePolicy.from_paths([root])
    journal = IngestJournal(":memory:")
    job = journal.acquire({"folder": str(root), "apply": True}, "w")
    calls = []

    def ingest_one(report):
        calls.append(report.doi)
        if report.doi.endswith("/c"):
            return {"n_sections": 2, "extraction_complete": False, "incomplete_chunks": [1]}
        return {"n_sections": 3, "n_figures": 1}

    statuses = {"b.pdf": "skipped:openalex_quota_paused"}
    *_, report = ingest_folder(
        root,
        policy=policy,
        intake=_intake_fake(statuses),
        apply=True,
        ingest_one=ingest_one,
        journal=journal,
        job_id=job,
    )
    assert [v.filename for v in report.verdicts] == ["a.pdf", "b.pdf"] and report.stopped_reason == "quota_paused"
    statuses.clear()  # quota resumed
    *_, report = ingest_folder(
        root,
        policy=policy,
        intake=_intake_fake(statuses),
        apply=True,
        ingest_one=ingest_one,
        journal=journal,
        job_id=job,
    )
    by_name = {v.filename: v for v in report.verdicts}
    assert by_name["a.pdf"].resumed and calls.count("10.1/a") == 1  # settled files are not redone
    assert by_name["b.pdf"].status == "staged" and by_name["c.pdf"].status == "staged:incomplete"
    assert "link.pdf" not in by_name
    *_, report = ingest_folder(
        root,
        policy=policy,
        intake=_intake_fake({}),
        apply=True,
        ingest_one=ingest_one,
        journal=journal,
        job_id=job,
    )
    assert calls.count("10.1/c") == 2  # incomplete extraction is retried, not settled


def test_folder_ingest_containment_dry_run_and_already_ingested(tmp_path):
    root, outside = tmp_path / "intake", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "a.pdf").write_bytes(minimal_pdf_bytes("a"))
    (outside / "z.pdf").write_bytes(minimal_pdf_bytes("z"))
    policy = LocalIntakePolicy.from_paths([root])
    with pytest.raises(ValueError):
        list(ingest_folder(outside, policy=policy, intake=_intake_fake({})))
    with pytest.raises(ValueError):
        list(ingest_folder(root, policy=policy, intake=_intake_fake({}), apply=True))
    lookup = IngestedPapers(in_graph={"101a"})
    *_, report = ingest_folder(
        root,
        policy=policy,
        intake=_intake_fake({}),
        files=[outside / "z.pdf", root / "a.pdf"],
        already_ingested=lookup,
    )
    assert [(v.filename, v.status) for v in report.verdicts] == [("a.pdf", "skipped:already_ingested")]
    assert not report.applied and ContractError and ErrorCode
