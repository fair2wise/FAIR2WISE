"""T26C rehearsal (gate H5): promote the exact tested Git snapshot to a second production-profile DISPOSABLE target.

What this module does, in the order of plan T26C ("Rehearse exact Git snapshot promotion and rollback"):

1. build two committed snapshots (v1, v2) of one corpus, plus a second corpus, in a throwaway Git repository;
2. select each by ``(data_commit, path, sha256)`` and export identical bytes into two disposable targets' mounts
   (a staging-profile target and a production-profile target);
3. reject, with typed errors, a tampered file, a wrong hash, a wrong commit and a path outside the allowlist;
4. promote v1 then v2 to the production-profile target with their own approvals and receipts (a receipt binds one
   target; the first target's receipt never covers the second);
5. crash in the middle of an import, replay, and end with no duplicate facts;
6. edit reconciliation: a Save-style edit is journaled, blocks a re-import, is reconciled into a new committed
   version (v3) which is then promoted to both targets;
7. rollback by selecting the earlier committed snapshot, and by the replacement-rollback route;
8. read-back equality and ownership isolation of a second corpus on the same target;
9. zero inference throughout (every provider and network entry point is a counted tripwire).

SAFETY. Nothing here talks to a real, central or ALS target. The "targets" are an in-memory Tiled double (and, when
``F2W_TILED_PYTHON`` is set, one disposable server the fixtures start on an ephemeral 127.0.0.1 port). The ALS
profiles are the real ones; the only override is ``DeploymentSettings.for_disposable_test_target`` (loopback only,
Python-only entry point, not reachable from environment or requests). Approvals are created by this harness with the
explicit TEST approver identities named below, through the same ``create_approval`` the product uses. They are not
the owner's approvals and authorize nothing outside this test world. The publication allowlist is untouched.

Evidence lines are printed as ``T26C|<label>|<json>`` (run with ``-s``); see docs/migration/reviews/H5/t26c_rehearsal.md.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
from collections import Counter
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from tests.jobs.replacement_world import DirSnapshots  # first: puts tests/fixtures + tests/graph_import on sys.path

from import_fakes import CountingClient, FakeClock, MemoryTiled  # noqa: E402  (path set by replacement_world)

from app.modules.commands import build_kg_snapshot
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph import edit_journal as ej
from app.modules.graph import identity_adapter as ida
from app.modules.graph import literature_writer as lw
from app.modules.graph import releases
from app.modules.graph.imports import corpus_scope as cs
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.imports.receipts import receipt_valid_for
from app.modules.graph.legacy_save import SaveTarget, TiledSaveBackend
from app.modules.graph.releases import ReleaseVerificationError
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.jobs import JobService, JobStore
from app.modules.jobs.publication import Publication, PublicationDeps
from app.modules.jobs.publish_store import PublishStore
from app.modules.jobs.replacement_step import ReplacementPublishStep
from app.modules.jobs.sqlite import OperationalDB
from app.modules.provenance import AuditLog, AuditRecorder
from app.modules.schema.registry import SchemaRegistry
from app.modules.security.authorization import (
    DeploymentSettings,
    create_approval,
    resolve_context,
    verify_approval,
    worker_context,
)
from app.modules.security.tiled_identity import TiledCredential
from tests.jobs.jobs_fakes import Clock
from tests.jobs.publish_fakes import FakeIndex, FakeProbe
from tests.security.security_fakes import FakeTiledIdentityPort, FakeUser
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

CORPUS = "synthetic"
OTHER = "othercorpus"
STAGING_PORT, PROD_PORT = 59811, 59822   # never bound: the in-memory double has no socket
#: Explicit TEST approver identities (never a real person, never the owner).
STAGING_APPROVER = FakeUser("t26c-test-approver-staging", roles=("admin",), identity_ids=("t26c-staging",))
PROD_APPROVER = FakeUser("t26c-test-approver-prod-disposable", roles=("admin",), identity_ids=("t26c-prod",))

GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t26c-fixture-owner",
    "GIT_AUTHOR_EMAIL": "owner@example.invalid", "GIT_COMMITTER_NAME": "t26c-fixture-owner",
    "GIT_COMMITTER_EMAIL": "owner@example.invalid", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
}


def emit(label: str, **fields: Any) -> None:
    """One evidence line for the H5 packet (``pytest -s``)."""
    print(f"T26C|{label}|{json.dumps(fields, sort_keys=True, default=str)}")


# ------------------------------------------------------------------------------------------------ zero inference

class InferenceTripwires:
    """Counts every attempt to reach a model provider or a non-loopback network address."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def trip(self, what: str) -> None:
        self.calls.append(what)
        raise AssertionError(f"T26C zero-inference violated: {what}")


@pytest.fixture(autouse=True)
def tripwires(monkeypatch):
    """Provider admission, key lookup and outbound sockets all fail the test (and are counted) if touched."""
    from app.modules import cborg_http, llm_admission

    guard = InferenceTripwires()
    monkeypatch.setattr(llm_admission.AdmissionController, "acquire", lambda self, timeout=None: guard.trip("llm_admission.acquire"))
    monkeypatch.setattr(llm_admission.AdmissionController, "try_acquire", lambda self: guard.trip("llm_admission.try_acquire"))
    monkeypatch.setattr(llm_admission.ProviderAdmission, "slot", lambda self, provider: guard.trip(f"provider slot {provider}"))
    monkeypatch.setattr(cborg_http, "require_cborg_api_key", lambda explicit=None: guard.trip("cborg api key"))
    monkeypatch.setattr(cborg_http, "openai_http_kwargs", lambda **kw: guard.trip("openai client kwargs"))
    real_connect = socket.socket.connect

    def connect(sock, address, *a, **kw):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else str(address)
        if isinstance(address, tuple) and host not in ("127.0.0.1", "::1", "localhost"):
            guard.trip(f"non-loopback connect {host}")
        return real_connect(sock, address, *a, **kw)

    monkeypatch.setattr(socket.socket, "connect", connect)
    yield guard
    assert guard.calls == [], guard.calls


# ------------------------------------------------------------------------------------------------ the Git release

def git(repo: Path, *args: str) -> str:
    """Owner-side Git in the THROWAWAY fixture repository only (the code under test only reads Git)."""
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV, check=True,
                          stdin=subprocess.DEVNULL)
    return done.stdout.strip()


def git_bytes(repo: Path, commit: str, path: str) -> bytes:
    """``git show <commit>:<path>`` computed independently of the application's release module."""
    return subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"], capture_output=True, env=GIT_ENV,
                          check=True, stdin=subprocess.DEVNULL).stdout


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def terms_doc(variant: int) -> dict[str, Any]:
    """The synthetic extracted-terms input; variant 2 revises one term and adds one (a new version of the corpus)."""
    doc = json.loads((INPUTS / "core_dict.terms.json").read_text(encoding="utf-8"))
    if variant >= 2:
        doc["terms"][0]["definition"] = "Synthetic placeholder polymer, revised for v2."
        added = copy.deepcopy(doc["terms"][0])
        added["term"] = "Synth Polymer Added In V2"
        added["definition"] = "A term only v2 has."
        added["relations"] = []
        doc["terms"].append(added)
    return doc


class Release:
    """A throwaway Git repository holding committed snapshots, played by a fixture 'owner'."""

    def __init__(self, root: Path, registry: SchemaRegistry) -> None:
        self.root, self.registry = root, registry
        self.storage = root / "storage" / "kg"
        self.storage.mkdir(parents=True)
        git(root, "init", "-q", "-b", "main")
        (root / "README.md").write_text("t26c fixture\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-q", "-m", "base")
        self.commits: dict[str, str] = {}
        self._inputs = root.parent / f"{root.name}-inputs"
        self._inputs.mkdir()

    def clone(self, dest: Path) -> Release:
        """An independent copy of the repository (Git repositories are relocatable), keeping the commit names."""
        twin = Release.__new__(Release)
        twin.registry, twin.commits = self.registry, dict(self.commits)
        twin.root = dest / "source-repo"
        shutil.copytree(self.root, twin.root, symlinks=True)
        twin.storage = twin.root / "storage" / "kg"
        twin._inputs = dest / "source-repo-inputs"
        shutil.copytree(self._inputs, twin._inputs)
        return twin

    def build(self, corpus: str, variant: int, **overrides: Any) -> Any:
        terms = self._inputs / f"{corpus}.v{variant}.terms.json"
        terms.write_text(json.dumps(terms_doc(variant)), encoding="utf-8")
        args: dict[str, Any] = dict(corpus=corpus, terms_path=terms, storage_kg=self.storage,
                                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=self.registry)
        args.update(overrides)
        return create_snapshot(SnapshotRequest(**args))

    def commit(self, name: str, message: str) -> str:
        git(self.root, "add", "-f", "storage/kg")  # flat copies are gitignored upstream: the owner uses add -f
        git(self.root, "commit", "-q", "-m", message)
        self.commits[name] = git(self.root, "rev-parse", "HEAD")
        return self.commits[name]

    def items(self, commit: str, corpus: str, snap: str) -> list[dict[str, str]]:
        """The selection tuples ``(role, path, sha256, git_blob)`` of one snapshot at one commit, hashed by Git+hashlib."""
        base = f"storage/kg/{corpus}/{snap}"
        out = []
        for role, rel in (("graph", f"{base}/graph.json"), ("manifest", f"{base}/manifest.json"),
                          ("validation", f"{base}/validation.json"),
                          ("compat_copy", f"storage/kg/matkg_{corpus}_{snap}.json")):
            data = git_bytes(self.root, commit, rel)
            out.append({"role": role, "path": rel, "sha256": sha(data),
                        "git_blob": git(self.root, "rev-parse", f"{commit}:{rel}")})
        return out


def make_release(tmp_path: Path, registry: SchemaRegistry) -> Release:
    rel = Release(tmp_path / "source-repo", registry)
    rel.build(CORPUS, 1)
    rel.build(OTHER, 1)
    rel.commit("c1", "data: synthetic/v1 and othercorpus/v1")
    rel.build(CORPUS, 2, snapshot_id="v2", parent_snapshot_id="v1", parent_data_commit=rel.commits["c1"])
    rel.commit("c2", "data: synthetic/v2")
    return rel


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


@pytest.fixture(scope="module")
def fingerprints(registry):
    return tj.default_supported_schema_fingerprints(registry)


@pytest.fixture(scope="module")
def base_release(tmp_path_factory, registry):
    """The tested release: synthetic/v1 + othercorpus/v1 at c1, synthetic/v2 at c2 (built once per worker)."""
    return make_release(tmp_path_factory.mktemp("t26c-base"), registry)


@pytest.fixture(scope="module")
def shared_release(tmp_path_factory, base_release):
    """The base release plus a tamper commit c3 and committed decoys outside the allowlist. Read only."""
    rel = base_release.clone(tmp_path_factory.mktemp("t26c-shared"))
    graph = rel.storage / CORPUS / "v1" / "graph.json"
    graph.chmod(0o644)
    graph.write_bytes(graph.read_bytes().replace(b"Synth", b"Evil_", 1))      # a file changed after the tested commit
    for name in ("storage/source_index/index.json", "papers/leak.pdf", ".env"):   # committed, outside the allowlist
        decoy = rel.root / name
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("{}", encoding="utf-8")
    git(rel.root, "add", "-f", "storage/source_index", "papers", ".env", "storage/kg")
    git(rel.root, "commit", "-q", "-m", "tamper v1 graph + decoys outside allowlist")
    rel.commits["c3"] = git(rel.root, "rev-parse", "HEAD")
    return rel


@pytest.fixture()
def release(tmp_path, base_release):
    """A private copy of the release repository per test (tests that add commits)."""
    return base_release.clone(tmp_path)


# ------------------------------------------------------------------------------------------------ disposable targets

class Target:
    """One disposable, ALS-profile target: in-memory Tiled double, its own job service, publication and mount.

    The profile is real (``als-dev`` or ``als-prod``); only the base URL is a loopback fixture address, through
    ``DeploymentSettings.for_disposable_test_target``. Approvals use the explicit TEST approver of this target.
    """

    def __init__(self, tmp_path: Path, name: str, profile: str, port: int, approver: FakeUser, fingerprints: Any,
                 client: Any = None, url: str | None = None) -> None:
        self.name, self.profile, self.fp, self.approver = name, profile, fingerprints, approver
        self.root = tmp_path / name
        self.mount = self.root / "mount"
        self.storage_kg = self.mount / "storage" / "kg"
        if client is None:
            client = MemoryTiled(f"http://127.0.0.1:{port}")
            client.add_forge_sentinel()          # a FORGE-owned record the rehearsal must never touch
        self.client = client
        self.settings = DeploymentSettings.for_disposable_test_target(
            profile, tiled_target=url or self.client.base_url, writer_identity="t26c-disposable-writer",
            corpus_scope=frozenset({CORPUS, OTHER}))
        self.client.profile = profile      # the importer refuses a client whose profile is not the deployment's
        self.clock = FakeClock()
        self.epoch = Clock(self.clock().astimezone(UTC).timestamp())
        self.port = FakeTiledIdentityPort(tiled_target=self.settings.tiled_target)
        self.token = f"token-{name}"
        self.port.tokens[self.token] = approver
        self.credential = TiledCredential(self.token, tiled_target=self.settings.tiled_target)
        self.ctx = resolve_context(self.settings, self.credential, request_id=f"t26c-{name}",
                                   identity_port=self.port, clock=self.clock, require_fresh=True)
        self.target = tj.target_for(self.client)
        self.journals: dict[str, ej.EditJournal] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    # -- importer-level (direct) --------------------------------------------------------------------
    def journal(self, scope: str) -> ej.EditJournal:
        return self.journals.setdefault(scope, ej.EditJournal(self.root / f"edits-{scope}.jsonl"))

    def approve_plan(self, plan: Any, *, job_id: str | None = None, approval_id: str | None = None) -> Any:
        change = getattr(plan, "change", plan)       # a ReplacementPlan wraps the ChangePlan the approval covers
        return create_approval(self.ctx, change, self.settings, clock=self.clock, identity_port=self.port,
                               approver_credential=self.credential, operation_id=job_id,
                               approval_id=approval_id or "t26c-appr-" + change.plan_sha256[:10])

    @property
    def state_dir(self) -> Path:
        return self.root / "import-state"

    def replan(self, source: Any, **kw: Any) -> Any:
        kw.setdefault("state_dir", self.state_dir)
        return cs.plan_replacement(source, client=self.client, settings=self.settings,
                                   supported_schema_fingerprints=self.fp, **kw)

    def rapply(self, plan: Any, approval: Any, source: Any, **kw: Any) -> Any:
        return cs.apply_replacement(self.ctx, plan, approval, source=source, client=self.client,
                                    settings=self.settings, supported_schema_fingerprints=self.fp,
                                    state_dir=self.state_dir, clock=self.clock, **kw)

    # -- job-level (the product's publication job) --------------------------------------------------
    def start_jobs(self) -> Target:
        self.step = ReplacementPublishStep(
            client=self.client, settings=self.settings, supported_schema_fingerprints=self.fp,
            storage_kg=self.storage_kg, state_dir=self.state_dir, clock=self.clock,
            edit_journals=self.journal)
        self.db = OperationalDB(self.root / "ops.sqlite3")
        self.store = JobStore(None, db=self.db, lease_seconds=60, clock=self.epoch)
        self.service = JobService(
            self.store, settings=self.settings, identity_port=self.port, clock=self.clock,
            credential_lookup=lambda subject: self.credential if subject == self.approver.subject else None)
        self.pubstore = PublishStore(self.db, clock=self.epoch)
        self.audit = AuditRecorder(AuditLog(self.root / "audit.sqlite3", clock=self.epoch))
        self.deps = PublicationDeps(
            snapshots=DirSnapshots(self.storage_kg, self.fp), step=self.step, index=FakeIndex(), probe=FakeProbe(),
            store=self.pubstore, audit=self.audit,
            ctx_for=lambda job: worker_context(self.ctx, run_id=job.job_id), clock=self.epoch,
            verify_approval=lambda a, p, at: verify_approval(a, p, self.settings, at=at))
        self.pub = Publication(self.service, self.deps)
        return self

    def work(self) -> Any:
        return self.service.work_one("t26c-worker")

    def approve(self, job_id: str) -> Any:
        plan = self.pub.current_plan(self.ctx, job_id)
        return self.pub.submit_approval(self.ctx, job_id, self.approve_plan(plan, job_id=job_id))

    def finish(self, job_id: str) -> str:
        got = self.work()
        if got != (job_id, JobState.AWAITING_REVIEW):
            print("WHY", self.why(job_id))
        assert got == (job_id, JobState.AWAITING_REVIEW)
        self.approve(job_id)
        got = self.work()
        assert got == (job_id, JobState.SUCCEEDED), (got, self.why(job_id))
        return job_id

    def why(self, job_id: str) -> Any:
        job = self.store.get(job_id)
        return {"state": job.state, "stages": [repr(st)[:900] for st in self.store.stages(job_id)],
                "events": [repr(e)[:300] for e in self.store.events(job_id)][-3:]}

    def publish(self, ref: str) -> str:
        """Submit, plan, approve (with this target's TEST approver) and run to success. Returns the job id."""
        return self.finish(self.pub.submit(self.ctx, ref, self.target).job_id)

    # -- reads --------------------------------------------------------------------------------------
    def live(self, scope: str = CORPUS) -> dict[str, str]:
        out = {}
        for e in self.client.scan_entities():
            key = ida.literature_entity_key(e)
            if key and key[0] == scope:
                out[key[1]] = e["id"]
        return out

    def rows(self, scope: str) -> list[str]:
        """Every entity and link row owned by ``scope`` (live, staged or retired), canonicalized."""
        out = []
        for r in [*self.client.scan_entities(), *self.client.scan_links()]:
            if (r.get("properties") or {}).get("f2w_graph_id") == scope:
                out.append(json.dumps(r, sort_keys=True))
        return sorted(out)

    def foreign(self) -> list[str]:
        rows = [*self.client.scan_entities(), *self.client.scan_links()]
        return sorted(json.dumps(r, sort_keys=True) for r in rows
                      if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature")

    def readback(self, scope: str = CORPUS) -> dict[str, Any]:
        """What a reader gets from the target: node id -> (name, description) and the owned link triples."""
        ents = {e["id"]: e for e in self.client.scan_entities()}
        nodes = {}
        for e in ents.values():
            key = ida.literature_entity_key(e)
            if key and key[0] == scope:
                nodes[key[1]] = (e["name"], (e.get("properties") or {}).get("description"))
        links = set()
        for lk in self.client.scan_links():
            props = lk.get("properties") or {}
            if props.get("f2w_graph_id") == scope and lk["subjectId"] in ents and lk["objectId"] in ents:
                links.add((ents[lk["subjectId"]]["properties"]["f2w_id"], lk["predicate"],
                           ents[lk["objectId"]]["properties"]["f2w_id"]))
        return {"nodes": nodes, "links": links}

    def mount_selection(self, release: Release, commit: str, corpus: str, snap: str) -> list[dict[str, Any]]:
        """Export the ``(commit, path, sha256)`` selection into this target's mount, byte for byte."""
        return releases.export_snapshot_selection(release.root, commit, release.items(commit, corpus, snap),
                                                  self.mount)

    def mounted(self, corpus: str, snap: str) -> Path:
        return self.storage_kg / corpus / snap


def expected_readback(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = {t["id"]: (t.get("name"), t.get("description")) for t in graph["things"]}
    links = {(a["subject"], a["predicate"], a["object"]) for a in graph["associations"]}
    return {"nodes": nodes, "links": links}


def graph_in_git(release: Release, commit: str, corpus: str, snap: str) -> dict[str, Any]:
    return json.loads(git_bytes(release.root, commit, f"storage/kg/{corpus}/{snap}/graph.json"))


@pytest.fixture()
def staging(tmp_path, fingerprints):
    return Target(tmp_path, "staging", "als-dev", STAGING_PORT, STAGING_APPROVER, fingerprints)


@pytest.fixture()
def prod(tmp_path, fingerprints):
    """The second, production-profile DISPOSABLE target (profile als-prod, loopback fixture address)."""
    return Target(tmp_path, "prod-disposable", "als-prod", PROD_PORT, PROD_APPROVER, fingerprints)


# ================================================================================================ 1. selection + mount

def test_t26c_01_select_by_commit_path_sha256_and_mount_identical_bytes_into_two_targets(
        shared_release, staging, prod):
    rel = shared_release
    head_before, refs_before = git(rel.root, "rev-parse", "HEAD"), git(rel.root, "for-each-ref")
    v1, v2 = rel.items(rel.commits["c1"], CORPUS, "v1"), rel.items(rel.commits["c2"], CORPUS, "v2")
    graph1 = next(i for i in v1 if i["role"] == "graph")
    graph2 = next(i for i in v2 if i["role"] == "graph")
    assert graph1["sha256"] != graph2["sha256"] and rel.commits["c1"] != rel.commits["c2"]

    # The application resolves the same snapshot from (commit, corpus, id) and agrees with plain git + hashlib.
    for commit, snap, graph in ((rel.commits["c1"], "v1", graph1), (rel.commits["c2"], "v2", graph2)):
        found = releases.lookup_snapshot(rel.root, commit, CORPUS, snap)
        assert found.data_commit == commit and found.graph.sha256 == graph["sha256"]
        assert found.graph.git_blob == graph["git_blob"]

    for snap, commit in (("v1", rel.commits["c1"]), ("v2", rel.commits["c2"])):
        first = staging.mount_selection(rel, commit, CORPUS, snap)
        second = prod.mount_selection(rel, commit, CORPUS, snap)
        assert {f["status"] for f in first + second} == {"written"}
        for item in rel.items(commit, CORPUS, snap):
            want = sha(git_bytes(rel.root, commit, item["path"]))
            got_a = sha((staging.mount / item["path"]).read_bytes())
            got_b = sha((prod.mount / item["path"]).read_bytes())
            assert got_a == got_b == want == item["sha256"], item["path"]
    again = prod.mount_selection(rel, rel.commits["c1"], CORPUS, "v1")
    assert {f["status"] for f in again} == {"already_present"}, "re-mounting the same selection is idempotent"
    assert git(rel.root, "rev-parse", "HEAD") == head_before and git(rel.root, "for-each-ref") == refs_before
    assert git(rel.root, "status", "--porcelain", "--untracked-files=no") == "", "the tooling wrote nothing to Git"
    emit("select_mount", data_commit_v1=rel.commits["c1"], data_commit_v2=rel.commits["c2"],
         v1_graph_sha256=graph1["sha256"], v2_graph_sha256=graph2["sha256"],
         identical_files_per_target=len(v1) + len(v2))


# ================================================================================================ 2. rejections

def codes(fn: Any) -> str:
    with pytest.raises(ReleaseVerificationError) as err:
        fn()
    return str(err.value.code)


def test_t26c_02_tampered_file_wrong_hash_wrong_commit_and_paths_outside_the_allowlist_are_rejected_typed(
        shared_release, staging, tmp_path):
    rel = shared_release
    c1, c2, c3 = rel.commits["c1"], rel.commits["c2"], rel.commits["c3"]
    good = rel.items(c1, CORPUS, "v1")
    graph = next(i for i in good if i["role"] == "graph")
    seen: dict[str, str] = {}

    def refused(label: str, fn: Any, expected: str) -> None:
        mount = tmp_path / f"reject-{label}"
        assert codes(lambda: fn(mount)) == expected, label
        assert not mount.exists() or not [p for p in mount.rglob("*") if p.is_file()], f"{label}: nothing exported"
        seen[label] = expected

    def export(commit: str, items: list[dict[str, Any]]) -> Any:
        return lambda mount: releases.export_snapshot_selection(rel.root, commit, items, mount)

    # Tampered file: the v1 graph was changed in a later commit. The pinned hash of the tested bytes no longer
    # matches at c3, so promoting "the same path" from the tampered commit is refused.
    refused("tampered_file_at_later_commit", export(c3, good), "wrong_hash")
    tampered = [dict(i, sha256=sha(git_bytes(rel.root, c3, i["path"])), git_blob=git(rel.root, "rev-parse", f"{c3}:{i['path']}"))
                if i["role"] == "graph" else i for i in good]
    refused("tampered_graph_vs_manifest", export(c3, tampered), "manifest_mismatch")

    # Wrong hashes.
    flipped = graph["sha256"][:-1] + ("0" if graph["sha256"][-1] != "0" else "1")
    refused("wrong_sha256", export(c1, [dict(i, sha256=flipped) if i["role"] == "graph" else i for i in good]),
            "wrong_hash")
    refused("wrong_git_blob", export(c1, [dict(i, git_blob="1" * 40) if i["role"] == "graph" else i for i in good]),
            "wrong_hash")
    refused("malformed_sha256", export(c1, [dict(i, sha256="abc") if i["role"] == "graph" else i for i in good]),
            "wrong_hash")
    other_snapshot_hash = next(i for i in rel.items(c2, CORPUS, "v2") if i["role"] == "graph")["sha256"]
    refused("v2_hash_for_v1_path", export(c1, [dict(i, sha256=other_snapshot_hash) if i["role"] == "graph" else i
                                              for i in good]), "wrong_hash")

    # Wrong commit: unknown object, a branch, a tag-like name, an abbreviation, an upper-case id; a real commit
    # that does not contain the path (v2 did not exist at c1).
    refused("unknown_commit", export("0" * 40, good), "git")
    for selector in ("main", "HEAD", c1[:12], c1.upper()):
        refused(f"selector_{selector[:6]}", export(selector, good), "bad_commit")
    refused("v2_path_at_c1", export(c1, rel.items(c2, CORPUS, "v2")), "missing")
    refused("tree_id_is_not_a_commit",
            export(git(rel.root, "rev-parse", f"{c1}^{{tree}}"), good), "bad_commit")

    # Paths outside the allowlist: traversal, operational stores, secrets, papers, a committed decoy, and a
    # layout that is not storage/kg/<corpus>/<id>/graph.json.
    # Existing files get their true hash so only the allowlist/layout rule can stop them.
    for label, path, expected in (
            ("dotdot", "storage/kg/../../etc/passwd", "bad_path"), ("env", ".env", "layout"),
            ("papers", "papers/leak.pdf", "layout"), ("readme_as_graph", "README.md", "layout"),
            ("committed_decoy", "storage/source_index/index.json", "layout")):
        try:
            true_hash = sha(git_bytes(rel.root, c3, path))
        except subprocess.CalledProcessError:
            true_hash = "a" * 64
        bad = [dict(i, path=path, sha256=true_hash, git_blob=None) if i["role"] == "graph" else i for i in good]
        code = codes(lambda: releases.verify_git_release(rel.root, c3, bad))
        assert code == expected, (label, code)
        seen[f"path_{label}"] = code
    outside = {f.location for f in releases.check_paths(
        ["storage/kg/synthetic/v1/graph.json", "storage/source_index/index.json", "papers/x.pdf", ".env",
         "storage/kg/synthetic/v1/notes.db"])}
    assert outside == {"storage/source_index/index.json", "papers/x.pdf", ".env", "storage/kg/synthetic/v1/notes.db"}, \
        "the R1 allowlist reports every outside path and accepts the frozen layout"

    # Mount-side tamper: bytes edited on the mount after export never import, and are never overwritten.
    staging.mount_selection(rel, c1, CORPUS, "v1")
    on_mount = staging.mounted(CORPUS, "v1") / "graph.json"
    original = on_mount.read_bytes()
    on_mount.chmod(0o644)
    on_mount.write_bytes(original.replace(b"Synth", b"Evil_", 1))
    assert codes(lambda: staging.mount_selection(rel, c1, CORPUS, "v1")) == "export_conflict"
    staging.start_jobs()
    with pytest.raises(ContractError) as err:
        staging.pub.submit(staging.ctx, f"{CORPUS}/v1", staging.target)
    assert err.value.code is ErrorCode.INVALID_INPUT and "snapshot verification failed" in str(err.value)
    mount_tamper_message = str(err.value)
    assert "missing" not in mount_tamper_message, "refused because the bytes differ, not because a file is absent"
    on_mount.write_bytes(original)
    assert {f["status"] for f in staging.mount_selection(rel, c1, CORPUS, "v1")} == {"already_present"}
    emit("rejections", **seen, mount_tamper="export_conflict + import INVALID_INPUT", mount_tamper_message=mount_tamper_message)


# ================================================================================================ 3. promotion

def receipt_of(target: Target, job_id: str) -> Any:
    plan = target.pubstore.current_plan(job_id).plan
    found = target.step.receipts.find(plan_sha256=plan.plan_sha256)
    assert len(found) == 1
    return plan, found[0]


def test_t26c_03_promote_v1_then_v2_with_distinct_target_approvals_and_receipts(shared_release, staging, prod):
    rel = shared_release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    g1, g2 = graph_in_git(rel, c1, CORPUS, "v1"), graph_in_git(rel, c2, CORPUS, "v2")
    pinned = {s: next(i for i in rel.items(c, CORPUS, s) if i["role"] == "graph")["sha256"]
              for s, c in (("v1", c1), ("v2", c2))}
    commit_of = {"v1": c1, "v2": c2}
    jobs: dict[str, dict[str, str]] = {}
    plans: dict[str, dict[str, Any]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    approvals: dict[str, dict[str, Any]] = {}
    for tgt in (staging, prod):
        tgt.start_jobs()
        for snap, commit in (("v1", c1), ("v2", c2)):
            tgt.mount_selection(rel, commit, CORPUS, snap)
        foreign = tgt.foreign()
        for snap, graph in (("v1", g1), ("v2", g2)):
            job = tgt.publish(f"{CORPUS}/{snap}")
            plan, art = receipt_of(tgt, job)
            jobs.setdefault(tgt.name, {})[snap], plans.setdefault(tgt.name, {})[snap] = job, plan
            receipts.setdefault(tgt.name, {})[snap] = art
            approvals.setdefault(tgt.name, {})[snap] = tgt.pubstore.approval_for_job(job) if hasattr(
                tgt.pubstore, "approval_for_job") else None
            # the plan and the receipt are bound to the Git-selected bytes and to this target only
            assert plan.input_sha256 == pinned[snap] == art.receipt.snapshot_sha256
            assert plan.target == tgt.target and art.receipt.target == tgt.target and art.receipt.verified
            # H5 finding 1 (closed): the export left `<vN>.release_commit.json` on the mount, hash-bound to the
            # files, so the job path records the data commit too
            assert art.extras.data_commit == commit_of[snap]
            assert art.extras.profile == tgt.profile and art.extras.approved_by == tgt.approver.subject
            assert tgt.readback() == expected_readback(graph), "read-back equals the committed snapshot"
            assert tgt.pubstore.pointer(tgt.target).snapshot_ref == f"{CORPUS}/{snap}"
            assert tgt.pub.verify_agreement(tgt.ctx, tgt.target, CORPUS)["agree"]
        assert tgt.foreign() == foreign, "FORGE-owned records are untouched"

    for snap in ("v1", "v2"):
        a, b = receipts["staging"][snap], receipts["prod-disposable"][snap]
        pa, pb = plans["staging"][snap], plans["prod-disposable"][snap]
        # identical bytes, different target: different plan, approval, receipt
        assert a.receipt.snapshot_sha256 == b.receipt.snapshot_sha256 == pinned[snap]
        assert a.extras.manifest_sha256 == b.extras.manifest_sha256
        assert pa.plan_sha256 != pb.plan_sha256 and a.receipt.receipt_id != b.receipt.receipt_id
        assert a.receipt.approval_id != b.receipt.approval_id and a.receipt.target != b.receipt.target
        assert a.extras.approved_by != b.extras.approved_by and a.extras.profile != b.extras.profile
        assert receipt_valid_for(b, target=prod.target, revision=b.extras.resulting_revision)
        assert not receipt_valid_for(a, target=prod.target, revision=a.extras.resulting_revision), \
            "the staging receipt never covers the production-profile target"
        # the staging approval does not authorize the production-profile plan (and v1's does not authorize v2)
        approval_a = staging.approve_plan(pa)
        with pytest.raises(ContractError) as err:
            verify_approval(approval_a, pb, prod.settings, at=prod.clock())
        assert err.value.code is ErrorCode.DENIED
    v1_approval_on_v2 = prod.approve_plan(plans["prod-disposable"]["v1"])
    with pytest.raises(ContractError) as err:
        verify_approval(v1_approval_on_v2, plans["prod-disposable"]["v2"], prod.settings, at=prod.clock())
    assert err.value.code is ErrorCode.STALE_APPROVAL
    emit("promotion", target_staging=staging.target, target_prod_disposable=prod.target,
         profiles=[staging.profile, prod.profile],
         approvers=[staging.approver.subject, prod.approver.subject],
         **{f"{t}_{s}": {"job": jobs[t][s], "plan_sha256": plans[t][s].plan_sha256,
                         "receipt_id": receipts[t][s].receipt.receipt_id,
                         "receipt_sha256": receipts[t][s].receipt_sha256,
                         "approval_id": receipts[t][s].receipt.approval_id,
                         "data_commit_recorded_in_receipt": receipts[t][s].extras.data_commit}
            for t in jobs for s in jobs[t]},
         entities_links_v1=[len(g1["things"]), len(g1["associations"])],
         entities_links_v2=[len(g2["things"]), len(g2["associations"])])


# ================================================================================================ 4. crash and replay

CRASH_POINTS = [("stage", 0), ("stage_effect", 1), ("verify_staged", 0), ("switch_effect", 0),
                ("before_receipt", 0), ("before_history", 0)]   # the full 17-point matrix is tests/graph_import


@pytest.mark.parametrize("point", CRASH_POINTS, ids=[f"{a}-{b}" for a, b in CRASH_POINTS])
def test_t26c_04_crash_mid_import_then_replay_leaves_no_duplicate_facts(shared_release, prod, point):
    """The commit-selected release object is the importer source, so the receipt can record the data commit."""
    rel = shared_release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    g2 = graph_in_git(rel, c2, CORPUS, "v2")
    v1_src = releases.lookup_snapshot(rel.root, c1, CORPUS, "v1")
    v2_src = releases.lookup_snapshot(rel.root, c2, CORPUS, "v2")
    plan1 = prod.replan(v1_src)
    prod.rapply(plan1, prod.approve_plan(plan1), v1_src)
    foreign, uuids_v1 = prod.foreign(), prod.live()

    plan = prod.replan(v2_src)
    approval = prod.approve_plan(plan)
    hit: list[tuple[str, int]] = []

    def crash(stage: str, i: int) -> None:
        if (stage, i) == point and not hit:
            hit.append(point)
            raise cs.InjectedCrash(point)

    with pytest.raises(cs.InjectedCrash):
        prod.rapply(plan, approval, v2_src, fault=crash)
    assert hit == [point]
    with pytest.raises(ContractError) as err:                 # the dead owner's lease still holds
        prod.rapply(plan, approval, v2_src)
    assert err.value.code is ErrorCode.CONFLICT
    prod.clock.advance(1000)                                   # the lease expires; a second worker replays
    out = prod.rapply(plan, approval, v2_src)
    assert out.status in ("applied", "already_applied") and out.resumed and out.receipt.receipt.verified

    mine = [e for e in prod.client.scan_entities() if (e.get("properties") or {}).get("f2w_graph_id") == CORPUS]
    mine_links = [lk for lk in prod.client.scan_links() if (lk.get("properties") or {}).get("f2w_graph_id") == CORPUS]
    uris = Counter(e["uri"] for e in mine)
    assert max(uris.values()) == 1, "no duplicate URIs: no duplicate facts"
    assert not [e for e in mine if e["entityType"] == lw.STAGED_TYPE], "nothing of the plan stays staged"
    assert sum(1 for e in mine if e["uri"] == lw.release_uri(CORPUS)) == 1, "exactly one release marker"
    assert len(prod.live()) == len(g2["things"]) and len(mine_links) == len(g2["associations"])
    assert prod.readback() == expected_readback(g2)
    assert sum(prod.live()[k] == v for k, v in uuids_v1.items() if k in prod.live()) >= len(uuids_v1) - 1, \
        "unrevised records keep their Tiled ids"
    assert prod.foreign() == foreign
    assert prod.replan(v2_src).is_noop, "replay converged: re-planning the same release is a no-op"
    receipt = out.receipt
    assert receipt.extras.data_commit == c2 and receipt.receipt.snapshot_sha256 == v2_src.graph.sha256
    if point == CRASH_POINTS[0]:
        emit("crash_replay", point=point, resumed=out.resumed, receipt_id=receipt.receipt.receipt_id,
             data_commit_in_receipt=receipt.extras.data_commit, entities=len(prod.live()), links=len(mine_links),
             duplicate_uris=0)


# ================================================================================================ 5. edit reconciliation

def test_t26c_05_a_save_is_reconciled_into_a_new_committed_version_before_cross_environment_promotion(
        release, staging, prod, capsys):
    """plan 4.14: "An admin edit is journaled, reconciled into a new JSON version, tested and committed before
    cross-environment promotion." The operator drives it with ``f2w-kg-snapshot --reconcile-edits`` (step 1:
    the new version from the live snapshot plus the journal) and ``--confirm-commit`` (step 2: the commit that
    holds it, recorded on the journal entries after the fixture owner committed); see the H5 record."""
    rel = release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    staging.start_jobs()
    for snap, commit in (("v1", c1), ("v2", c2)):
        staging.mount_selection(rel, commit, CORPUS, snap)
    staging.publish(f"{CORPUS}/v1")
    staging.publish(f"{CORPUS}/v2")
    v2_graph = graph_in_git(rel, c2, CORPUS, "v2")

    # (a) a Save-style edit on the live v2 (node patch + new relationship), journaled and applied on Tiled
    degree: Counter[str] = Counter()
    for a in v2_graph["associations"]:
        degree[a["subject"]] += 1
        degree[a["object"]] += 1
    node = max(degree, key=lambda n: (degree[n], n))
    other = next(t["id"] for t in v2_graph["things"] if t["id"] != node)
    after = copy.deepcopy(v2_graph)
    next(t for t in after["things"] if t["id"] == node)["description"] = "edited in the UI on staging"
    after["associations"].append({"subject": node, "predicate": "rel:affects", "object": other})
    journal = staging.journal(CORPUS)
    save = TiledSaveBackend(SaveTarget(
        client=staging.client, settings=staging.settings, state_dir=staging.state_dir, clock=staging.clock,
        scope=CORPUS, journal=journal, base_graph_path=staging.mounted(CORPUS, "v2") / "graph.json",
        base_snapshot_id="v2", base_graph_sha256=next(i for i in rel.items(c2, CORPUS, "v2") if i["role"] == "graph")["sha256"]))
    out = save.save(staging.ctx, save.effective_graph(), after)
    assert len(out.edit_ids) == 2
    assert {r.state for r in journal.latest().values()} == {"applied"}
    assert staging.readback()["nodes"][node][1] == "edited in the UI on staging"

    # (b) until reconciled, the edit protects itself: re-importing v2 or rolling back to v1 is refused
    for snap in ("v2", "v1"):
        with pytest.raises(ContractError) as err:
            staging.replan(staging.mounted(CORPUS, snap), edit_journal=journal)
        assert err.value.code is ErrorCode.CONFLICT and "not reconciled" in str(err.value), snap
    rollback = staging.pub.rollback_replacement(staging.ctx, staging.target, reason="would lose the edit")
    assert staging.work() == (rollback.job_id, JobState.FAILED), "the job route refuses too (plan stage CONFLICT)"
    assert staging.readback()["nodes"][node][1] == "edited in the UI on staging", "nothing was overwritten"

    # (c) the operator reconciles into a NEW version v3 (parent v2, relation edit_reconciliation) with the
    # command, from the live snapshot plus the journal: no terms, no json2kg, no Tiled, no Git write
    assert build_kg_snapshot.main([
        "--corpus", CORPUS, "--reconcile-edits", "--parent", "v2", "--parent-data-commit", c2, "--snapshot-id", "v3",
        "--storage-kg", str(rel.storage), "--journal", str(journal.path), "--target", staging.target,
        "--code-commit", COMMIT]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["snapshot_id"] == "v3" and sorted(result["reconciled_edits"]) == sorted(out.edit_ids)
    assert result["edit_conflicts"] == [] and result["relation"] == "edit_reconciliation"
    assert result["graph_sha256"] == sha((rel.storage / CORPUS / "v3" / "graph.json").read_bytes())
    assert {r.state for r in journal.latest().values()} == {"reconciled"}
    assert {r.reconciled_in for r in journal.latest().values()} == {"v3"}
    assert {r.data_commit for r in journal.latest().values()} == {None}, "no commit exists yet; none is claimed"
    # the owner commits it (c4) ...
    c4 = rel.commit("c4", "data: synthetic/v3 (edit reconciliation of v2)")
    assert c4 != c2 and git(rel.root, "merge-base", "--is-ancestor", c2, c4) == ""
    assert codes(lambda: releases.lookup_snapshot(rel.root, c2, CORPUS, "v3")) == "missing", "v3 is a new commit's data"
    v3 = releases.lookup_snapshot(rel.root, c4, CORPUS, "v3")
    assert v3.manifest.parent.relation == "edit_reconciliation" and v3.manifest.parent.snapshot_id == "v2"
    assert v3.manifest.edit_reconciliation.applied_edit_count == 2
    v3_graph = graph_in_git(rel, c4, CORPUS, "v3")
    assert v3.graph.sha256 != next(i for i in rel.items(c2, CORPUS, "v2") if i["role"] == "graph")["sha256"]
    assert next(t for t in v3_graph["things"] if t["id"] == node)["description"] == "edited in the UI on staging"
    # ... and records the commit on the reconciled entries (step 2), only after the commit verifiably holds v3
    assert build_kg_snapshot.main([
        "--corpus", CORPUS, "--confirm-commit", c2, "--snapshot-id", "v3", "--repo", str(rel.root),
        "--storage-kg", str(rel.storage), "--journal", str(journal.path)]) == 1, "c2 does not hold v3"
    assert build_kg_snapshot.main([
        "--corpus", CORPUS, "--confirm-commit", c4, "--snapshot-id", "v3", "--repo", str(rel.root),
        "--storage-kg", str(rel.storage), "--journal", str(journal.path)]) == 0
    confirmed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert confirmed["data_commit"] == c4 and sorted(confirmed["recorded"]) == sorted(out.edit_ids)
    assert {(r.state, r.reconciled_in, r.data_commit) for r in journal.latest().values()} == {("reconciled", "v3", c4)}

    # (d) only now is anything promoted: staging first (same journal, edit now inside the lineage) ...
    staging.mount_selection(rel, c4, CORPUS, "v3")
    staging_job = staging.publish(f"{CORPUS}/v3")
    assert staging.readback() == expected_readback(v3_graph), "the edit survived the re-import"
    # ... then the cross-environment promotion of the identical committed bytes to the production-profile target
    prod.start_jobs()
    prod_files = prod.mount_selection(rel, c4, CORPUS, "v3")
    for item in rel.items(c4, CORPUS, "v3"):
        assert sha((prod.mount / item["path"]).read_bytes()) == sha((staging.mount / item["path"]).read_bytes()) == item["sha256"]
    prod_job = prod.publish(f"{CORPUS}/v3")
    assert prod.readback() == expected_readback(v3_graph) == staging.readback()
    plan_s, art_s = receipt_of(staging, staging_job)
    plan_p, art_p = receipt_of(prod, prod_job)
    assert art_s.receipt.snapshot_sha256 == art_p.receipt.snapshot_sha256 == v3.graph.sha256
    assert plan_s.plan_sha256 != plan_p.plan_sha256 and art_s.receipt.receipt_id != art_p.receipt.receipt_id
    assert art_p.receipt.target == prod.target != art_s.receipt.target
    assert art_s.extras.data_commit == art_p.extras.data_commit == c4, "both job-path receipts record the commit"
    emit("edit_reconciliation", edits=out.edit_ids, reconciled_in="v3", new_commit=c4, parent_commit=c2,
         v3_graph_sha256=v3.graph.sha256, staging_receipt=art_s.receipt.receipt_id,
         prod_disposable_receipt=art_p.receipt.receipt_id, files_mounted_on_prod=len(prod_files),
         command="f2w-kg-snapshot --reconcile-edits (v3 from live v2 + journal), then --confirm-commit c4",
         journal_data_commit=c4)


# ================================================================================================ 6. rollback

def test_t26c_06_rollback_by_selecting_the_earlier_committed_snapshot_and_by_the_replacement_rollback_route(
        shared_release, prod):
    rel = shared_release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    g1, g2 = graph_in_git(rel, c1, CORPUS, "v1"), graph_in_git(rel, c2, CORPUS, "v2")
    prod.start_jobs()
    for snap, commit in (("v1", c1), ("v2", c2)):
        prod.mount_selection(rel, commit, CORPUS, snap)
    foreign = prod.foreign()
    job_v1 = prod.publish(f"{CORPUS}/v1")
    uuid_v1 = prod.live()
    _plan_v1, receipt_v1 = receipt_of(prod, job_v1)
    prod.publish(f"{CORPUS}/v2")
    uuid_v2 = prod.live()
    assert uuid_v2 != uuid_v1 and prod.readback() == expected_readback(g2)

    # (a) rollback = select the earlier committed snapshot again, by (commit, path, sha256). The mount still
    # holds exactly the committed v1 bytes; nothing is taken on trust.
    for item in rel.items(c1, CORPUS, "v1"):
        assert sha((prod.mount / item["path"]).read_bytes()) == item["sha256"]
    releases.verify_git_release(rel.root, c1, rel.items(c1, CORPUS, "v1"))
    handle = prod.pub.submit(prod.ctx, f"{CORPUS}/v1", prod.target)
    job_back = prod.finish(handle.job_id)
    plan_back, receipt_back = receipt_of(prod, job_back)
    assert prod.live() == uuid_v1, "every v1 Tiled id is live again (retired records revived)"
    assert prod.readback() == expected_readback(g1)
    assert prod.pub.verify_agreement(prod.ctx, prod.target, CORPUS) == {
        "agree": True, "pointer_snapshot_id": "v1", "marker_snapshot_id": "v1"}
    assert receipt_back.receipt.receipt_id != receipt_v1.receipt.receipt_id, "the rollback has its own receipt"
    assert receipt_back.receipt.approval_id != receipt_v1.receipt.approval_id, "and its own approval"
    assert receipt_back.receipt.snapshot_sha256 == receipt_v1.receipt.snapshot_sha256 == next(
        i for i in rel.items(c1, CORPUS, "v1") if i["role"] == "graph")["sha256"]
    assert prod.foreign() == foreign

    # (b) the replacement-rollback route toggles the last two pointers: forward to v2, then back to v1
    for want, expected_graph, uuids in (("v2", g2, uuid_v2), ("v1", g1, uuid_v1)):
        job = prod.finish(prod.pub.rollback_replacement(prod.ctx, prod.target, reason=f"toggle to {want}").job_id)
        assert prod.live() == uuids and prod.readback() == expected_readback(expected_graph)
        assert prod.pub.verify_agreement(prod.ctx, prod.target, CORPUS)["marker_snapshot_id"] == want
        assert prod.store.get(job).state is JobState.SUCCEEDED
    history = [(h["action"], h["record"]["snapshot_ref"]) for h in prod.pubstore.pointer_history(prod.target)]
    assert history == [("activate", f"{CORPUS}/v1"), ("activate", f"{CORPUS}/v2"), ("activate", f"{CORPUS}/v1"),
                       ("rollback", f"{CORPUS}/v2"), ("rollback", f"{CORPUS}/v1")]
    assert prod.foreign() == foreign
    # the pointer-only rollback (no replacement) is refused while a marker moves with the pointer
    with pytest.raises(ContractError) as err:
        prod.pub.rollback(prod.ctx, prod.target, reason="pointer only")
    assert err.value.reason == "rollback_replacement_required"
    emit("rollback", selected_commit=c1, rollback_job=job_back, rollback_plan_sha256=plan_back.plan_sha256,
         rollback_receipt=receipt_back.receipt.receipt_id, first_v1_receipt=receipt_v1.receipt.receipt_id,
         pointer_history=history, uuids_restored=len(uuid_v1))


# ================================================================================================ 7. ownership isolation

def test_t26c_07_a_rollback_of_one_corpus_never_touches_the_other_on_the_same_target(shared_release, prod):
    """The E2E finding (owner_decisions 2026-11-06): "the rollback only touches one KG, not the other".

    Both corpora deliberately use the SAME node ids (same terms), so any cross-scope write would collide.
    """
    rel = shared_release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    g1, g2 = graph_in_git(rel, c1, CORPUS, "v1"), graph_in_git(rel, c2, CORPUS, "v2")
    go = graph_in_git(rel, c1, OTHER, "v1")
    prod.start_jobs()
    for corpus, snap, commit in ((CORPUS, "v1", c1), (CORPUS, "v2", c2), (OTHER, "v1", c1)):
        prod.mount_selection(rel, commit, corpus, snap)
    foreign = prod.foreign()

    prod.publish(f"{CORPUS}/v1")
    uuid_s1 = prod.live()
    prod.publish(f"{OTHER}/v1")
    assert set(prod.live(OTHER)) == {t["id"] for t in go["things"]} and set(prod.live(OTHER)) & set(uuid_s1), \
        "the two corpora share node ids"
    assert all(prod.live(OTHER)[n] != uuid_s1[n] for n in set(prod.live(OTHER)) & set(uuid_s1)), "but never records"
    other_rows, other_marker = prod.rows(OTHER), prod.step.release_marker(prod.ctx, OTHER)
    assert prod.readback(OTHER) == expected_readback(go)

    # (1) the E2E case: pointer now names OTHER; the route's "previous pointer" is the synthetic v1 that is live
    writes = len(prod.client.writes)
    all_rows = (prod.rows(CORPUS), prod.rows(OTHER), prod.foreign())
    handle = prod.pub.rollback_replacement(prod.ctx, prod.target, reason="back to synthetic")
    assert prod.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = prod.pub.current_plan(prod.ctx, handle.job_id)
    noop_plan = (plan.creates, plan.links, plan.supersessions)
    assert noop_plan == (0, 0, 0) and plan.corpus_scope == (CORPUS,)
    prod.approve(handle.job_id)
    assert prod.work() == (handle.job_id, JobState.SUCCEEDED)
    assert (prod.rows(CORPUS), prod.rows(OTHER), prod.foreign()) == all_rows and len(prod.client.writes) == writes
    assert prod.pubstore.pointer(prod.target).snapshot_ref == f"{CORPUS}/v1"
    assert prod.step.release_marker(prod.ctx, OTHER) == other_marker, "a rollback never removes or edits a corpus"

    # (2) move synthetic to v2, then roll it back by selecting the earlier committed v1: OTHER is untouched
    prod.publish(f"{CORPUS}/v2")
    assert prod.readback() == expected_readback(g2)
    assert prod.rows(OTHER) == other_rows and prod.step.release_marker(prod.ctx, OTHER) == other_marker
    job = prod.finish(prod.pub.submit(prod.ctx, f"{CORPUS}/v1", prod.target).job_id)
    plan = prod.pub.current_plan(prod.ctx, job)
    assert plan.corpus_scope == (CORPUS,) and plan.supersessions > 0
    assert prod.live() == uuid_s1 and prod.readback() == expected_readback(g1)
    assert prod.rows(OTHER) == other_rows
    assert prod.readback(OTHER) == expected_readback(go), "the other corpus reads back exactly its committed snapshot"
    assert prod.step.release_marker(prod.ctx, OTHER) == other_marker
    assert prod.foreign() == foreign, "FORGE-owned records are byte-identical after every step"
    ents, links = list(prod.client.scan_entities()), list(prod.client.scan_links())
    emit("isolation", shared_node_ids=len(set(prod.live(OTHER)) & set(prod.live(CORPUS))),
         other_corpus_entities=len(prod.live(OTHER)), other_corpus_rows=len(other_rows),
         noop_rollback_plan_creates_links_supersessions=noop_plan, noop_rollback_writes=0,
         target_entities=len(ents), target_links=len(links), other_marker_plan=other_marker["plan_sha256"])


# ================================================================================================ 8. zero inference

def test_t26c_08_zero_inference_the_tripwires_are_armed_and_nothing_tripped(tripwires, shared_release, prod):
    from app.modules import cborg_http, llm_admission

    with pytest.raises(AssertionError):
        llm_admission.get_controller("cborg").acquire()
    with pytest.raises(AssertionError):
        cborg_http.require_cborg_api_key()
    with pytest.raises(AssertionError):
        with llm_admission.ProviderAdmission().slot("cborg"):
            pass
    with pytest.raises(AssertionError), socket.socket() as sock:
        sock.connect(("203.0.113.9", 443))                           # TEST-NET-3, never routed
    armed = list(tripwires.calls)
    tripwires.calls.clear()                                          # the autouse check then sees a clean run
    assert len(armed) == 4

    prod.start_jobs()
    prod.mount_selection(shared_release, shared_release.commits["c1"], CORPUS, "v1")
    prod.publish(f"{CORPUS}/v1")                                     # a whole promotion with the tripwires armed
    assert tripwires.calls == []
    emit("zero_inference", armed_tripwires=len(armed), model_calls=0, non_loopback_connects=0)


# ================================================================================================ 9. a real disposable Tiled server

def test_t26c_09_second_production_profile_target_on_a_real_disposable_tiled_server(
        shared_release, tmp_path, fingerprints, tiled_seeded_pool):
    """Promote v1, v2, roll back to v1 on a REAL Tiled 0.2.18 the fixtures start on an ephemeral 127.0.0.1 port.

    Skipped without ``F2W_TILED_PYTHON``. The server is the test harness's own (isolated storage, random writer
    key, torn down by the fixture); the settings are the real ``als-prod`` rules with the loopback override.
    """
    from tiled_server import tiled_python

    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    rel = shared_release
    c1, c2 = rel.commits["c1"], rel.commits["c2"]
    g1, g2 = graph_in_git(rel, c1, CORPUS, "v1"), graph_in_git(rel, c2, CORPUS, "v2")
    inst = tiled_seeded_pool.acquire()
    assert inst.url.startswith("http://127.0.0.1:")
    tgt = Target(tmp_path, "prod-live-disposable", "als-prod", 0, PROD_APPROVER, fingerprints,
                 client=CountingClient.from_managed(inst.server), url=inst.url)
    foreign = tgt.foreign()
    assert foreign, "the seeded FORGE records are present"
    receipts = {}
    for label, snap, commit, graph in (("v1", "v1", c1, g1), ("v2", "v2", c2, g2), ("rollback_v1", "v1", c1, g1)):
        src = releases.lookup_snapshot(rel.root, commit, CORPUS, snap)
        plan = tgt.replan(src)
        out = tgt.rapply(plan, tgt.approve_plan(plan, approval_id=f"t26c-live-{label}"), src)
        assert out.status == "applied" and out.receipt.receipt.verified
        assert out.receipt.extras.data_commit == commit and out.receipt.receipt.target == tgt.target
        assert tgt.readback() == expected_readback(graph), label
        assert tgt.foreign() == foreign
        receipts[label] = out.receipt.receipt.receipt_id
    assert len(set(receipts.values())) == 3, "three plans, three receipts"
    emit("real_disposable_server", target=tgt.target, profile="als-prod", receipts=receipts,
         entities_after_rollback=len(tgt.live()), foreign_rows_unchanged=len(foreign))
