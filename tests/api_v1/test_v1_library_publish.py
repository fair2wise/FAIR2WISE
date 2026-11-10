"""Publish to Tiled (owner decision 2026-11-10): ``POST /v1/library/kgs/{name}/publish`` (V83) and the ``library_publish`` job.

The world is the real ``create_api_app`` over the real replacement importer on the in-memory Tiled double, with
real adopted releases under ``<workdir>/storage_kg`` (``DirectorySnapshotSource``). Nothing here approves a plan:
the tests assert that no approval exists, and the one approval-shaped call is the existing V18 route used by the
cancel/approve checks.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "security", REPO / "tests" / "api_wiring",
              REPO / "tests" / "graph_import", REPO / "tests" / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402
from import_fakes import FakeClock as ImportClock  # noqa: E402
from import_fakes import MemoryTiled  # noqa: E402
from security_fakes import ForbiddenIdentityPort  # noqa: E402

from app.api.auth import undeclared_routes  # noqa: E402
from app.api.main import create_api_app  # noqa: E402
from app.api.operations import F2WOperations, PublicationGate  # noqa: E402
from app.api.policies import API_ROUTE_POLICIES  # noqa: E402
from app.modules.contracts.jobs import JobState  # noqa: E402
from app.modules.curation.store import CurationStore  # noqa: E402
from app.modules.graph.imports import tiled_json as tj  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.jobs import JobService, JobStore  # noqa: E402
from app.modules.jobs.publication import Publication, PublicationDeps  # noqa: E402
from app.modules.jobs.publish_store import PublishStore  # noqa: E402
from app.modules.jobs.replacement_step import ReplacementPublishStep  # noqa: E402
from app.modules.jobs.snapshot_source import DirectorySnapshotSource  # noqa: E402
from app.modules.jobs.sqlite import OperationalDB  # noqa: E402
from app.modules.library import bundles as B  # noqa: E402
from app.modules.library import publish as P  # noqa: E402
from app.modules.library.schemas import list_schemas, manifest_block, resolve_schema  # noqa: E402
from app.modules.provenance import AuditLog, AuditRecorder  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    DeploymentSettings, local_operator_context, verify_approval, worker_context)
from tests.jobs.jobs_fakes import Clock  # noqa: E402
from tests.jobs.publish_fakes import FakeIndex, FakeProbe  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

REGISTRY = SchemaRegistry.discover()


class World:
    """One ``:local`` app with a real publication over a memory Tiled and a bundle store."""

    def __init__(self, monkeypatch: Any, tmp_path: Path) -> None:
        ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
        monkeypatch.delenv("F2W_RUNTIME", raising=False)
        monkeypatch.setenv("F2W_CODE_COMMIT", COMMIT)
        self.tmp = tmp_path
        self.tiled = MemoryTiled()
        self.settings = DeploymentSettings(profile="local", writer_identity="f2w-writer",
                                           tiled_target=self.tiled.base_url)
        epoch = Clock(ImportClock()().astimezone(UTC).timestamp())
        db = OperationalDB(tmp_path / "ops.sqlite3")
        self.jobs = JobService(JobStore(None, db=db, clock=epoch), settings=self.settings)
        self.workdir = ctx.workspace / "run"
        self.storage_kg = self.workdir / "storage_kg"
        self.storage_kg.mkdir(parents=True, exist_ok=True)
        fingerprints = tj.default_supported_schema_fingerprints(REGISTRY)
        step = ReplacementPublishStep(client=self.tiled, settings=self.settings, supported_schema_fingerprints=fingerprints,
                                      storage_kg=self.storage_kg, state_dir=tmp_path / "import-state", clock=ImportClock())
        self.pubstore = PublishStore(db, clock=epoch)
        operator = local_operator_context(self.settings, request_id="t")
        deps = PublicationDeps(
            snapshots=DirectorySnapshotSource(self.storage_kg), step=step, index=FakeIndex(), probe=FakeProbe(),
            store=self.pubstore, audit=AuditRecorder(AuditLog(tmp_path / "audit.sqlite3", clock=epoch)),
            ctx_for=lambda job: worker_context(operator, run_id=job.job_id), clock=epoch,
            verify_approval=lambda a, p, at: verify_approval(a, p, self.settings, at=at))
        self.pub = Publication(self.jobs, deps)
        self.ops = F2WOperations(self.settings, self.jobs, publication=self.pub,
                                 publication_gate=PublicationGate(frozenset({self.tiled.base_url})),
                                 curation=CurationStore(tmp_path / "cur.sqlite3", clock=epoch),
                                 control_path=tmp_path / "cost_control.json")
        self.app = create_api_app(make_cfg(ctx), deployment=self.settings, identity_port=ForbiddenIdentityPort(),
                                  operations=self.ops, sse_poll_seconds=0.01, sse_max_seconds=2.0)
        self.release = tmp_path / "release"
        self.release.mkdir()
        self.library = self.app.state.f2w_kg_library
        self.library._release = self.release
        self.publisher = self.app.state.f2w_library_publisher
        self.publisher.plan_wait_seconds = 5.0
        self.publisher._idle = lambda: self.jobs.work_one("w-pub")  # the second worker thread, inline
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self._legacy = self._legacy_bytes()

    def _legacy_bytes(self) -> bytes:
        kg = self.tmp / "legacy-kg"
        kg.mkdir()
        shutil.copy2(INPUTS / "core_dict.terms.json", self.tmp / "core_dict.terms.json")
        result = create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=self.tmp / "core_dict.terms.json",
                                                 storage_kg=kg, terms_ref="inputs/core_dict.terms.json",
                                                 code_commit=COMMIT, registry=REGISTRY))
        return result.graph_path.read_bytes()

    def bundle(self, name: str, *, kg: dict[str, Any] | None = None, **node_fields: Any) -> None:
        doc = kg if kg is not None else json.loads(self._legacy)
        if node_fields:
            doc["things"][0].update(node_fields)
        schema = manifest_block(resolve_schema(list_schemas(REGISTRY)[0]["name"], REGISTRY))
        self.library.store.create(name, schema=schema, kg=doc)

    def run_job(self, job_id: str) -> JobState | None:
        done = self.jobs.work_one("w1")
        assert done is not None and done[0] == job_id, done
        return done[1]

    def publish(self, name: str, **body: Any) -> Any:
        return self.client.post(f"/v1/library/kgs/{name}/publish", json={"confirm": True, **body})

    def view(self, job_id: str) -> dict[str, Any]:
        out = self.client.get(f"/v1/jobs/{job_id}/view")
        assert out.status_code == 200, out.text
        return out.json()


@pytest.fixture
def w(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


def test_policy_row_is_local_only_publish_capability_and_routes_are_declared(w):
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V83")
    assert (row.method, row.path, row.capabilities, row.on_als) == (
        "POST", "/v1/library/kgs/{name}/publish", frozenset({"graph.publish"}), False)
    assert undeclared_routes(w.app) == []


def test_als_has_no_route_and_no_can_publish(als):
    admin = als.client("admin")
    assert admin.post("/v1/library/kgs/x/publish", json={"confirm": True}).status_code == 404
    assert admin.post("/v1/library/kgs/x/publish", json={}).status_code == 404
    caps = admin.get("/v1/capabilities").json()
    assert caps["can_publish"] is False
    assert als.app.state.__dict__.get("_state", {}).get("f2w_library_publisher") is None


def test_can_publish_is_true_on_local_with_a_wired_publication(w):
    assert w.client.get("/v1/capabilities").json()["can_publish"] is True


def test_can_publish_is_false_when_no_publication_is_wired(local):
    assert local.client().get("/v1/capabilities").json()["can_publish"] is False


def test_preview_writes_nothing_and_names_the_next_version(w):
    w.bundle("demo")
    out = w.client.post("/v1/library/kgs/demo/publish", json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["confirmed"] is False and body["corpus"] == "demo" and body["snapshot_ref"] == "demo/v1"
    assert body["release_file"] == "matkg_demo.json" and body["problems"] == [] and body["previous"] is None
    assert "_problems" not in body and "_inputs" not in body
    assert list(w.release.iterdir()) == [] and w.jobs.store.list_jobs() == []
    assert w.client.post("/v1/library/kgs/ghost/publish", json={}).status_code == 404


def test_publish_runs_export_adopt_stage_and_waits_for_the_owner(w):
    w.bundle("Demo")
    resp = w.publish("Demo")
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert resp.json()["reused"] is False and resp.json()["snapshot_ref"] == "demo/v1"
    assert w.run_job(job_id) is JobState.SUCCEEDED
    view = w.view(job_id)["library_publish"]
    assert view["corpus"] == "demo" and view["snapshot_ref"] == "demo/v1" and view["review_page"] == "Pending Approvals"
    assert set(view["stages"]) == {"export", "adopt", "stage", "plan"} and view["failure"] is None
    assert view["publication_state"] == "staged" and len(view["plan"]["plan_sha256"]) == 64
    # export wrote the release file; adopt wrote a frozen release with its own flat copy
    assert (w.release / "matkg_Demo.json").is_file()
    assert (w.storage_kg / "demo" / "v1" / "manifest.json").is_file()
    assert (w.storage_kg / "matkg_demo_v1.json").read_bytes() == (w.storage_kg / "demo" / "v1" / "graph.json").read_bytes()
    manifest = json.loads((w.storage_kg / "demo" / "v1" / "manifest.json").read_text())
    assert manifest["created"]["code_commit"] == COMMIT and manifest["created"]["run_id"] == job_id
    # the publication job waits in awaiting_review on the Pending Approvals page; nothing was approved
    pub_id = view["publication_job_id"]
    assert w.jobs.store.get(pub_id).state is JobState.AWAITING_REVIEW and w.jobs.store.get(pub_id).kind == "publish"
    plan = w.pub.current_plan(local_operator_context(w.settings, request_id="t"), pub_id)
    assert plan.plan_sha256 == view["plan"]["plan_sha256"]
    assert w.pubstore.valid_approvals(pub_id, plan) == []
    pending = w.client.get("/v1/approvals/pending").json()
    item = next(i for i in pending["items"] if i["kind"] == "publication")
    assert item["job_id"] == pub_id and item["snapshot_ref"] == "demo/v1" and item["plan"]["plan_sha256"] == plan.plan_sha256
    assert w.tiled.scan_entities() == [] or all(
        (e.get("properties") or {}).get("f2w_owner") != "f2w-literature" for e in w.tiled.scan_entities())


def test_double_click_is_idempotent_and_a_pending_plan_blocks_a_second_publish(w):
    w.bundle("demo")
    first = w.publish("demo").json()
    again = w.publish("demo").json()  # identical inputs before the job ran: the same job
    assert again["job_id"] == first["job_id"] and again["reused"] is True
    w.run_job(first["job_id"])
    blocked = w.publish("demo")
    assert blocked.status_code == 409 and blocked.json()["reason"] == "plan_pending"
    preview = w.client.post("/v1/library/kgs/demo/publish", json={}).json()
    assert [p["reason"] for p in preview["problems"]] == ["plan_pending"]


def test_republish_gets_the_next_free_version_and_never_overwrites(w):
    w.bundle("demo")
    first = w.publish("demo").json()
    w.run_job(first["job_id"])
    v1_before = (w.storage_kg / "demo" / "v1" / "graph.json").read_bytes()
    pub_id = w.view(first["job_id"])["library_publish"]["publication_job_id"]
    assert w.client.post(f"/v1/jobs/{pub_id}/cancel").status_code == 200  # reject the first plan (V08)
    assert w.jobs.store.get(pub_id).state is JobState.CANCELLED
    same = w.publish("demo").json()  # unchanged bundle, no "again": the finished job comes back
    assert same["job_id"] == first["job_id"] and same["reused"] is True
    preview = w.client.post("/v1/library/kgs/demo/publish", json={"again": True}).json()
    assert preview["snapshot_ref"] == "demo/v2"
    second = w.publish("demo", again=True).json()
    assert second["job_id"] != first["job_id"]
    assert w.run_job(second["job_id"]) is JobState.SUCCEEDED
    assert w.view(second["job_id"])["library_publish"]["snapshot_ref"] == "demo/v2"
    assert (w.storage_kg / "demo" / "v2" / "graph.json").is_file()
    assert (w.storage_kg / "demo" / "v1" / "graph.json").read_bytes() == v1_before


def test_a_release_adopted_by_the_cli_counts_for_the_version(w):
    w.bundle("demo")
    (w.storage_kg / "demo" / "v1").mkdir(parents=True)
    (w.storage_kg / "demo" / "v3").mkdir()
    assert P.adopted_versions(w.storage_kg, "demo") == [1, 3] and P.next_version(w.storage_kg, "demo") == 4
    assert w.client.post("/v1/library/kgs/demo/publish", json={}).json()["snapshot_ref"] == "demo/v4"
    assert P.next_version(w.storage_kg, "other") == 1


def test_adoption_refusal_names_the_rule_and_leaves_nothing_adopted_or_staged(w):
    w.bundle("leaky", full_text="whole page body")  # R7
    job_id = w.publish("leaky").json()["job_id"]
    assert w.run_job(job_id) is JobState.FAILED
    failure = w.view(job_id)["library_publish"]["failure"]
    assert failure["stage"] == "adopt" and failure["reason"] == "disclosure_refused" and "R7" in failure["message"]
    assert not (w.storage_kg / "leaky").exists() and not (w.storage_kg / "matkg_leaky_v1.json").exists()
    assert w.jobs.store.list_jobs(kind="publish") == []
    assert (w.release / "matkg_leaky.json").is_file()  # the export stays; only adoption was refused


def test_image_without_a_commit_label_is_a_typed_error(w, monkeypatch):
    monkeypatch.delenv("F2W_CODE_COMMIT")
    monkeypatch.setenv("GIT_COMMIT", "unknown")
    w.bundle("demo")
    out = w.publish("demo")
    assert out.status_code == 501 and out.json()["reason"] == "no_commit_label"
    assert "F2W_CODE_COMMIT" in out.json()["message"]
    assert [p["reason"] for p in w.client.post("/v1/library/kgs/demo/publish", json={}).json()["problems"]] == [
        "no_commit_label"]
    assert w.jobs.store.list_jobs() == [] and list(w.release.iterdir()) == []


def test_an_empty_bundle_has_nothing_to_publish(w):
    w.library.store.create("empty", schema=manifest_block(resolve_schema(list_schemas(REGISTRY)[0]["name"], REGISTRY)))
    out = w.publish("empty")
    assert out.status_code == 422 or out.status_code == 400
    assert out.json()["reason"] == "nothing_to_publish"
    assert w.jobs.store.list_jobs() == []


def test_the_route_needs_a_valid_name_and_the_body_is_strict(w):
    assert w.client.post("/v1/library/kgs/bad%20name/publish", json={"confirm": True}).status_code in (400, 404, 422)
    assert w.client.post("/v1/library/kgs/demo/publish", json={"confirm": True, "x": 1}).status_code == 422


def test_commit_label_resolution_invents_nothing():
    good = "b" * 40
    assert P.resolve_code_commit({"F2W_CODE_COMMIT": good.upper()}) == good
    assert P.resolve_code_commit({"GIT_COMMIT": good}) == good
    for bad in ("unknown", "", "abc", "g" * 40, "b" * 39):
        assert P.resolve_code_commit({"F2W_CODE_COMMIT": bad}) is None
    assert P.resolve_code_commit({}) is None
    assert P.corpus_for_bundle("MyKG_1") == "mykg_1"


def test_no_approval_call_exists_in_the_publish_module():
    source = (REPO / "app" / "modules" / "library" / "publish.py").read_text(encoding="utf-8")
    for forbidden in ("create_approval", "submit_approval", "approve_publication", "record_approval"):
        assert forbidden not in source
    assert B.NAME_RE.pattern  # the bundle name rule feeds the corpus rule
