"""Fakes for the publication job: an in-memory 'Tiled', a fake PublishStep (T08B's port), index and snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from app.modules.contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt, SnapshotManifest
from app.modules.contracts.context import AuthorizationMode, DeploymentProfile
from app.modules.jobs import JobService, JobStore
from app.modules.jobs.keys import digest
from app.modules.jobs.publication import Publication, PublicationDeps
from app.modules.jobs.publish_ports import ApplyOutcome, FrozenSnapshot, IndexRef, VerifyOutcome
from app.modules.jobs.publish_store import PublishStore
from app.modules.jobs.sqlite import OperationalDB
from app.modules.provenance import AuditLog, AuditRecorder
from app.modules.security.authorization import worker_context
from tests.jobs.jobs_fakes import Clock, Crash, local_ctx, local_settings

TARGET = "http://tiled.example:8000/api#f2w"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_snapshot(ref="rsoxs/v1", nodes=("a", "b", "c"), links=(("a", "b"), ("b", "c")), package_keys=()):
    graph = {"nodes": list(nodes), "links": [list(p) for p in links]}
    digest_ = sha(json.dumps(graph, sort_keys=True))
    manifest = SnapshotManifest(
        snapshot_id=ref.split("/")[1],
        corpus=ref.split("/")[0],
        graph_path=f"storage/kg/{ref}/graph.json",
        graph_sha256=digest_,
        schema_fingerprint="schema-1",
        extraction_fingerprint="ext-1",
        corpus_fingerprint="corp-1",
        created_at=NOW,
    )
    return (
        FrozenSnapshot(
            ref=ref, corpus=ref.split("/")[0], sha256=digest_, manifest=manifest, package_keys=tuple(package_keys)
        ),
        graph,
    )


class FakeSnapshots:
    def __init__(self):
        self.items: Dict[str, FrozenSnapshot] = {}
        self.graphs: Dict[str, Dict[str, Any]] = {}
        self.loads = 0

    def add(self, **kw):
        snap, graph = make_snapshot(**kw)
        self.items[snap.ref], self.graphs[snap.ref] = snap, graph
        return snap

    def load(self, ref):
        self.loads += 1
        if ref not in self.items:
            raise ValueError("snapshot missing")
        return self.items[ref]

    def tamper(self, ref):
        """The bytes on disk no longer match the manifest (load now returns different bytes)."""
        old = self.items[ref]
        self.items[ref] = FrozenSnapshot(
            ref=old.ref,
            corpus=old.corpus,
            sha256=sha("tampered"),
            manifest=old.manifest,
            package_keys=old.package_keys,
        )


class FakeTiled:
    """The 'target graph': entities and links keyed by stable IDs."""

    def __init__(self):
        self.entities: Set[str] = set()
        self.links: Set[str] = set()
        self.writes: List[str] = []

    @property
    def revision(self) -> str:
        return f"rev-{len(self.entities)}-{len(self.links)}-{len(self.writes)}"


class FakeStep:
    """Fulfils the PublishStep port the way T08B will: idempotent by stable IDs, guard before each batch."""

    adapter_version = "fake-1"

    def __init__(self, tiled: FakeTiled, snapshots: FakeSnapshots, clock: Clock):
        self.tiled, self.snapshots, self.clock = tiled, snapshots, clock
        self.crash_after_entities = False
        self.fail_apply: Optional[Exception] = None
        self.corrupt_readback = False
        self.apply_calls = 0
        self.after_entities: Optional[Callable[[], None]] = None

    def _ids(self, plan: ChangePlan):
        ref = plan.snapshot_id if plan.snapshot_id in self.snapshots.graphs else f"rsoxs/{plan.snapshot_id}"
        graph = self.snapshots.graphs[ref]
        ents = [f"{plan.input_sha256[:8]}:{n}" for n in graph["nodes"]]
        links = [f"{plan.input_sha256[:8]}:{a}->{b}" for a, b in graph["links"]]
        return ents, links

    def plan(self, ctx, snapshot, target, *, operation_id):
        graph = self.snapshots.graphs[snapshot.ref]
        ents = [f"{snapshot.sha256[:8]}:{n}" for n in graph["nodes"]]
        links = [f"{snapshot.sha256[:8]}:{a}->{b}" for a, b in graph["links"]]
        body = {
            "snapshot": snapshot.ref,
            "sha": snapshot.sha256,
            "target": target,
            "base": self.tiled.revision,
            "creates": sorted(set(ents) - self.tiled.entities),
            "links": sorted(set(links) - self.tiled.links),
        }
        plan_sha = digest(body)
        return ChangePlan(
            plan_id="plan-" + plan_sha[:12],
            snapshot_id=snapshot.ref,
            input_sha256=snapshot.sha256,
            target=target,
            corpus_scope=(snapshot.corpus,),
            base_revision=self.tiled.revision,
            creates=len(body["creates"]),
            links=len(body["links"]),
            supersessions=0,
            affected_ids=tuple(ents + links),
            validation_report_sha256=sha("report"),
            plan_sha256=plan_sha,
        )

    def target_revision(self, ctx, target):
        return self.tiled.revision

    def apply(self, ctx, plan, approval, *, operation_id, guard):
        self.apply_calls += 1
        if self.fail_apply:
            raise self.fail_apply
        ents, links = self._ids(plan)
        created = reconciled = 0
        guard()
        for e in ents:
            if e in self.tiled.entities:
                reconciled += 1
            else:
                self.tiled.entities.add(e)
                self.tiled.writes.append(e)
                created += 1
        if self.after_entities:
            self.after_entities()
        if self.crash_after_entities:
            raise Crash()
        guard()  # a worker that lost its lease must not write the links
        for link in links:
            if link in self.tiled.links:
                reconciled += 1
            else:
                self.tiled.links.add(link)
                self.tiled.writes.append(link)
                created += 1
        return ApplyOutcome(created, reconciled, sha("mapping-" + operation_id), tuple(ents[:2]), True)

    def reconcile(self, ctx, plan, *, operation_id):
        ents, links = self._ids(plan)
        if set(ents) <= self.tiled.entities and set(links) <= self.tiled.links:
            return ApplyOutcome(0, len(ents) + len(links), sha("mapping-" + operation_id), (), True)
        return None

    def verify(self, ctx, plan, outcome):
        ents, links = self._ids(plan)
        ok = set(ents) <= self.tiled.entities and set(links) <= self.tiled.links and not self.corrupt_readback
        return VerifyOutcome(
            ok, "" if ok else "links missing on readback", len(ents) + len(links), self.tiled.revision
        )

    def receipt(self, ctx, plan, approval, outcome, verification):
        return ImportReceipt(
            receipt_id="rcpt-" + plan.plan_sha256[:12],
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            approval_id=approval.approval_id,
            snapshot_sha256=plan.input_sha256,
            target=plan.target,
            executed_as="writer-svc",
            adapter_version=self.adapter_version,
            id_mapping_sha256=outcome.id_mapping_sha256 or sha("m"),
            outcomes={"created": outcome.created, "reconciled": outcome.reconciled},
            verified=verification.ok,
            completed_at=NOW,
        )


class FakeIndex:
    def __init__(self):
        self.builds = 0
        self.crash_on_build = False
        self.fail_verify = False

    def build(self, ctx, snapshot, receipt):
        self.builds += 1
        if self.crash_on_build:
            raise Crash()
        return IndexRef(
            ref=f"idx-{receipt.receipt_id}", snapshot_sha256=snapshot.sha256, receipt_id=receipt.receipt_id
        )

    def verify(self, ctx, index):
        return not self.fail_verify


class FakeProbe:
    def __init__(self):
        self.schema, self.vocab = "schema-1", "vocab-1"

    def schema_fingerprint(self):
        return self.schema

    def vocabulary_revision(self):
        return self.vocab


def approval_for(plan: ChangePlan, *, approval_id="appr-1", expires_at=None, scope=None) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        profile=DeploymentProfile.LOCAL,
        mode=AuthorizationMode.LOCAL_TRUSTED,
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        target=plan.target,
        scope=tuple(scope or plan.corpus_scope),
        requested_by="local-operator",
        approved_by="local-operator",
        executed_as="writer-svc",
        approved_at=NOW,
        expires_at=expires_at,
    )


class World:
    """Everything wired together for one test."""

    def __init__(self, tmp_path, *, packages=None, lease_seconds=60):
        self.clock = Clock(NOW.timestamp())
        self.jobs_db = OperationalDB(tmp_path / "ops.sqlite3")
        self.jobs_store = JobStore(None, db=self.jobs_db, lease_seconds=lease_seconds, clock=self.clock)
        self.service = JobService(self.jobs_store, settings=local_settings())
        self.snapshots, self.tiled, self.index, self.probe = FakeSnapshots(), FakeTiled(), FakeIndex(), FakeProbe()
        self.step = FakeStep(self.tiled, self.snapshots, self.clock)
        self.log = AuditLog(tmp_path / "audit.sqlite3", clock=self.clock)
        self.audit = AuditRecorder(self.log)
        self.pubstore = PublishStore(self.jobs_db, clock=self.clock)
        self.deps = PublicationDeps(
            snapshots=self.snapshots,
            step=self.step,
            index=self.index,
            probe=self.probe,
            store=self.pubstore,
            audit=self.audit,
            ctx_for=lambda job: worker_context(local_ctx(), run_id=job.job_id),
            clock=self.clock,
            packages=packages,
        )
        self.pub = Publication(self.service, self.deps)
        self.ctx = local_ctx()

    def submit(self, ref="rsoxs/v1", target=TARGET):
        return self.pub.submit(self.ctx, ref, target)

    def work(self, owner="w1"):
        return self.service.work_one(owner)

    def approve(self, job_id, **kw):
        plan = self.pub.current_plan(self.ctx, job_id)
        return self.pub.submit_approval(self.ctx, job_id, approval_for(plan, **kw))

    def state(self, job_id):
        return self.jobs_store.get(job_id).state
