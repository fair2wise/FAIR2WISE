"""T10B world: the publication job over the real corpus-scoped importer (:class:`ReplacementPublishStep`)
on the in-memory Tiled double, with real frozen snapshots."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from datetime import timezone, UTC
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "graph_import"))
sys.path.insert(0, str(HERE.parent / "fixtures"))

from import_fakes import FakeClock, MemoryTiled, snapshot_with_graph  # noqa: E402

from app.modules.contracts.changes import SnapshotManifest  # noqa: E402
from app.modules.graph import identity_adapter as ida  # noqa: E402
from app.modules.graph.imports import tiled_json as tj  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.jobs import JobService, JobStore  # noqa: E402
from app.modules.jobs.publication import Publication, PublicationDeps  # noqa: E402
from app.modules.jobs.publish_ports import FrozenSnapshot  # noqa: E402
from app.modules.jobs.publish_store import PublishStore  # noqa: E402
from app.modules.jobs.replacement_step import ReplacementPublishStep  # noqa: E402
from app.modules.jobs.sqlite import OperationalDB  # noqa: E402
from app.modules.provenance import AuditLog, AuditRecorder  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    DeploymentSettings,
    create_approval,
    local_operator_context,
    verify_approval,
    worker_context,
)
from tests.jobs.jobs_fakes import Clock  # noqa: E402
from tests.jobs.publish_fakes import FakeIndex, FakeProbe  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

SCOPE = "synthetic"


def build_v1(root: Path, registry: Any) -> Path:
    """A real T23B snapshot of the synthetic corpus under ``root/storage/kg``."""
    storage = root / "storage" / "kg"
    storage.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus=SCOPE, terms_path=INPUTS / "core_dict.terms.json", storage_kg=storage,
                                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry))
    return storage / SCOPE / "v1"


def graph_of(snap: Path) -> dict[str, Any]:
    return json.loads((snap / "graph.json").read_text(encoding="utf-8"))


def derive(v1: Path, name: str, *, revise: int = 1, add: int = 1) -> Path:
    """Snapshot ``name`` next to ``v1``: ``revise`` nodes changed and ``add`` added."""
    g = copy.deepcopy(graph_of(v1))
    for t in g["things"][:revise]:
        t["description"] = f"revised in {name}"
    for i in range(add):
        nid = f"matkg:Added{name}{i}"
        g["things"].append({"id": nid, "category": "Material", "name": f"Added {i}", "description": "new"})
        g["associations"].append({"subject": g["things"][0]["id"], "predicate": "rel:RELATED_TO", "object": nid,
                                  "has_evidence": None})
    root = v1.parents[2]  # <root>/storage/kg/<corpus>/<v1>
    dest = snapshot_with_graph(v1, root / ("tmp-" + name), SCOPE, name, g)
    final = v1.parent / name
    shutil.copytree(dest, final)
    return final


class DirSnapshots:
    """``SnapshotSource`` over real snapshot directories."""

    def __init__(self, storage_kg: Path, fingerprints: Any) -> None:
        self.root, self.fp = storage_kg, fingerprints

    def load(self, ref: str) -> FrozenSnapshot:
        corpus, snap_id = ref.split("/")
        src = tj.load_source(self.root / corpus / snap_id, supported_schema_fingerprints=self.fp)
        manifest = SnapshotManifest(
            snapshot_id=snap_id, corpus=corpus, graph_path=f"storage/kg/{ref}/graph.json",
            graph_sha256=src.graph_sha256, schema_fingerprint="schema-1", extraction_fingerprint="ext-1",
            corpus_fingerprint="corp-1", created_at=FakeClock()())
        return FrozenSnapshot(ref=ref, corpus=corpus, sha256=src.graph_sha256, manifest=manifest)


class RWorld:
    """Job service, publication and the real replacement importer over an in-memory Tiled."""

    def __init__(self, tmp_path: Path, v1: Path, fingerprints: Any) -> None:
        self.v1, self.fp = v1, fingerprints
        self.tmp_path = tmp_path
        self.client = MemoryTiled()
        self.client.add_forge_sentinel()
        self.settings = DeploymentSettings(profile="local", writer_identity="f2w-writer",
                                           tiled_target=self.client.base_url)
        self.ctx = local_operator_context(self.settings, request_id="t10b")
        self.step_clock = FakeClock()
        self.clock = Clock(self.step_clock().astimezone(UTC).timestamp())
        self.target = tj.target_for(self.client)
        self.step = ReplacementPublishStep(
            client=self.client, settings=self.settings, supported_schema_fingerprints=fingerprints,
            storage_kg=v1.parents[1], state_dir=tmp_path / "import-state", clock=self.step_clock)
        self.db = OperationalDB(tmp_path / "ops.sqlite3")
        self.store = JobStore(None, db=self.db, lease_seconds=60, clock=self.clock)
        self.service = JobService(self.store, settings=self.settings)
        self.snapshots = DirSnapshots(v1.parents[1], fingerprints)
        self.probe, self.index = FakeProbe(), FakeIndex()
        self.audit = AuditRecorder(AuditLog(tmp_path / "audit.sqlite3", clock=self.clock))
        self.pubstore = PublishStore(self.db, clock=self.clock)
        self.deps = PublicationDeps(
            snapshots=self.snapshots, step=self.step, index=self.index, probe=self.probe, store=self.pubstore,
            audit=self.audit, ctx_for=lambda job: worker_context(self.ctx, run_id=job.job_id), clock=self.clock,
            verify_approval=lambda a, p, at: verify_approval(a, p, self.settings, at=at))
        self.pub = Publication(self.service, self.deps)

    # -- job helpers -------------------------------------------------------------------------------
    def work(self) -> Any:
        return self.service.work_one("w1")

    def approve(self, job_id: str) -> Any:
        plan = self.pub.current_plan(self.ctx, job_id)
        approval = create_approval(self.ctx, plan, self.settings, clock=self.step_clock,
                                   approval_id="appr-" + plan.plan_sha256[:10], operation_id=job_id)
        return self.pub.submit_approval(self.ctx, job_id, approval)

    def run_to_review(self, handle: Any) -> str:
        assert self.work() == (handle.job_id, handle.state.__class__.AWAITING_REVIEW)
        return str(handle.job_id)

    def publish(self, snap_id: str) -> str:
        """Submit, plan, approve and run a publication of ``snap_id`` to success; returns the job id."""
        handle = self.pub.submit(self.ctx, f"{SCOPE}/{snap_id}", self.target)
        return self.finish(handle.job_id)

    def finish(self, job_id: str) -> str:
        from app.modules.contracts.jobs import JobState

        assert self.work() == (job_id, JobState.AWAITING_REVIEW)
        self.approve(job_id)
        assert self.work() == (job_id, JobState.SUCCEEDED)
        return job_id

    # -- target reads ------------------------------------------------------------------------------
    def live(self) -> dict[str, str]:
        """``node_id -> Tiled UUID`` of the live records of the scope."""
        out = {}
        for e in self.client.scan_entities():
            key = ida.literature_entity_key(e)
            if key and key[0] == SCOPE:
                out[key[1]] = e["id"]
        return out

    def agree(self) -> dict[str, Any]:
        return self.pub.verify_agreement(self.ctx, self.target, SCOPE)

    def foreign(self) -> list[str]:
        rows = [*self.client.scan_entities(), *self.client.scan_links()]
        return sorted(json.dumps(r, sort_keys=True) for r in rows
                      if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature")
