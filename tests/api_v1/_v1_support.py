"""T14 test wiring: the real ``create_api_app`` over fake job kinds, fake publication and fake identity.

The legacy UI app comes from the T18A harness (fake models/providers/Splash, sockets
blocked, refuses to run next to a ``.env``). Jobs, curation and audit are the real T10
stores in ``tmp_path``. Publication drives the T10 fake ``PublishStep`` against an in-memory
"Tiled": the publication target is a string on a loopback rehearsal URL that no test
connects to. Nothing here contacts a network, a model or a Tiled server.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "security",
              REPO / "tests" / "api_wiring"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402
from security_fakes import (  # noqa: E402
    FakeClock, FakeTiledIdentityPort, FakeUser, ForbiddenIdentityPort, InMemoryCredentialStore, admin_user,
    plain_user)

from app.api.auth import SESSION_COOKIE  # noqa: E402
from app.api.main import create_api_app  # noqa: E402
from app.api.operations import F2WOperations, PublicationGate  # noqa: E402
from app.modules.contracts.context import ActorKind, RequestContext  # noqa: E402
from app.modules.curation.store import CurationStore  # noqa: E402
from app.modules.jobs import JobService, JobStore  # noqa: E402
from app.modules.jobs.pipeline import AwaitReview, Pipeline, StageResult, StageSpec  # noqa: E402
from app.modules.jobs.publication import Publication, PublicationDeps  # noqa: E402
from app.modules.jobs.publish_store import PublishStore  # noqa: E402
from app.modules.jobs.sqlite import OperationalDB  # noqa: E402
from app.modules.provenance import AuditLog, AuditRecorder  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    DeploymentSettings, WorkerAuthority, local_operator_context, verify_approval, worker_context)
from app.modules.security.tiled_identity import TiledCredential  # noqa: E402
from tests.jobs.jobs_fakes import Clock  # noqa: E402
from tests.jobs.publish_fakes import FakeIndex, FakeProbe, FakeSnapshots, FakeStep, FakeTiled  # noqa: E402

WRITER = "f2w-writer"
#: Loopback rehearsal Tiled used as a publication *target string*; nothing connects to it.
LOCAL_TILED = "http://127.0.0.1:59871"
ALS_TILED = "http://127.0.0.1:59872"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
CORPUS = "rsoxs"


def ingest_inputs(**params: Any) -> Dict[str, Any]:
    """Inputs of a fake ingest job."""
    return {"corpus_scope": [CORPUS], "source_sha256": "b" * 64, "source_version": "v1",
            "params": {"chunk": 10, **params}}


def fake_ingest_pipeline(log: List[str]) -> Pipeline:
    """Two stages: ``parse`` re-authorizes ``ingest.extract`` and counts; ``review`` waits for a person."""

    def parse(sc: Any) -> StageResult:
        sc.require("ingest.extract")
        sc.guard()
        log.append(sc.job.job_id)
        return StageResult(data={"pages": 3}, progress={"pages": 3})

    def review(sc: Any) -> StageResult:
        if sc.inputs.params.get("wait_for_review"):
            raise AwaitReview("waiting for a reviewer")
        return StageResult(data={"ok": True})

    return Pipeline([StageSpec("parse", parse, phase="parsing"),
                     StageSpec("review", review, phase="review", depends_on=("parse",))])


@dataclass
class V1App:
    """One ``create_api_app`` instance with its operations and fakes."""

    app: Any
    ops: F2WOperations
    settings: DeploymentSettings
    snapshots: FakeSnapshots
    tiled: FakeTiled
    step: FakeStep
    audit_log: AuditLog
    ingest_log: List[str]
    target: str
    port: Any = None
    store: Any = None
    clients: Dict[str, TestClient] = field(default_factory=dict)

    def client(self, who: str = "local", headers: Optional[Dict[str, str]] = None) -> TestClient:
        """``local``, ``anon``, ``user``, ``user2`` or ``admin``."""
        key = who + repr(sorted((headers or {}).items()))
        if key not in self.clients:
            cookies = {SESSION_COOKIE: f"cookie-{who}"} if who not in ("local", "anon") else None
            self.clients[key] = TestClient(self.app, cookies=cookies, headers=headers,
                                           raise_server_exceptions=False)
        return self.clients[key]

    def work(self, owner: str = "w1") -> Any:
        """Run one queued job the way the worker executor does (``JobService.work_one``)."""
        return self.ops.jobs.work_one(owner)


def _build(monkeypatch: Any, tmp_path: Path, settings: DeploymentSettings, *, port: Any,
           store: Any, worker_ctx: Callable[[Any], RequestContext],
           authority: Optional[Callable[[Any, Any], Any]], clock: Any, rehearsal: bool = True,
           publication: bool = True, after_install: Optional[Callable[[Any], None]] = None,
           credential_lookup: Optional[Callable[[str], Any]] = None, **app_kw: Any) -> V1App:
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    if after_install is not None:
        after_install(monkeypatch)  # live tests re-open 127.0.0.1 after the harness blocked sockets
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    epoch = Clock(NOW.timestamp())
    db = OperationalDB(tmp_path / "ops.sqlite3")
    # The service's default JobAuthority (AUD-01) covers kinds registered without one (publication).
    jobs = JobService(JobStore(None, db=db, clock=epoch), settings=settings, clock=clock,
                      identity_port=None if settings.is_local else port, credential_lookup=credential_lookup)
    ingest_log: List[str] = []
    jobs.register("ingest", fake_ingest_pipeline(ingest_log), authority=authority)
    snapshots, tiled, index, probe = FakeSnapshots(), FakeTiled(), FakeIndex(), FakeProbe()
    snapshots.add()
    step = FakeStep(tiled, snapshots, epoch)
    audit_log = AuditLog(tmp_path / "audit.sqlite3", clock=epoch)
    audit = AuditRecorder(audit_log)
    pub = None
    if publication:
        deps = PublicationDeps(
            snapshots=snapshots, step=step, index=index, probe=probe, store=PublishStore(db, clock=epoch),
            audit=audit, ctx_for=worker_ctx, clock=epoch,
            verify_approval=lambda approval, plan, at: verify_approval(approval, plan, settings, at=at),
        )
        pub = Publication(jobs, deps)
    target = settings.tiled_target + "/api#f2w"
    gate = PublicationGate(frozenset({settings.tiled_target + "/api#f2w"})) if rehearsal else PublicationGate()
    curation = CurationStore(tmp_path / "cur.sqlite3", clock=epoch)
    ops = F2WOperations(settings, jobs, publication=pub, curation=curation,
                        audit=audit, identity_port=port, publication_gate=gate, clock=clock,
                        control_path=tmp_path / "cost_control.json", quality_dir=tmp_path / "vocab_quality")
    app = create_api_app(make_cfg(ctx), deployment=settings, identity_port=port, credential_store=store,
                         clock=clock, operations=ops,
                         **{"sse_poll_seconds": 0.01, "sse_max_seconds": 2.0, **app_kw})
    return V1App(app=app, ops=ops, settings=settings, snapshots=snapshots, tiled=tiled, step=step,
                 audit_log=audit_log, ingest_log=ingest_log, target=target, port=port, store=store)


def local_settings() -> DeploymentSettings:
    return DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=LOCAL_TILED)


def build_local(monkeypatch: Any, tmp_path: Path, **kw: Any) -> V1App:
    """``:local``: the local operator, an identity port that fails the test if called."""
    settings = local_settings()

    def worker_ctx(job: Any) -> RequestContext:
        return local_operator_context(settings, request_id=job.job_id, actor_kind=ActorKind.WORKER,
                                      run_id=job.job_id)

    def authority(job: Any, caps: Any) -> Any:
        return WorkerAuthority(worker_ctx(job), settings, clock=FakeClock(NOW)).check(*caps)

    return _build(monkeypatch, tmp_path, settings, port=ForbiddenIdentityPort(), store=None,
                  worker_ctx=worker_ctx, authority=authority, clock=FakeClock(NOW), **kw)


def other_user() -> FakeUser:
    return FakeUser("uuid-user2", roles=("user",), identity_ids=("carol",))


def als_settings() -> DeploymentSettings:
    return DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=ALS_TILED, writer_identity=WRITER,
                                                         corpus_scope=frozenset({CORPUS}))


def build_als(monkeypatch: Any, tmp_path: Path, **kw: Any) -> V1App:
    """``:als-dev`` bound to a loopback rehearsal target, with a fake identity port.

    Workers re-verify the initiating subject through ``WorkerAuthority`` with the subject's
    credential from the same session store (``credential_provider``).
    """
    settings = als_settings()
    clock = FakeClock(NOW)
    port = FakeTiledIdentityPort(tiled_target=ALS_TILED)
    store = InMemoryCredentialStore()
    by_subject: Dict[str, str] = {}
    for who, user in (("user", plain_user()), ("user2", other_user()), ("admin", admin_user())):
        port.tokens[f"token-{who}"] = user
        store.sessions[f"cookie-{who}"] = TiledCredential(f"token-{who}", tiled_target=ALS_TILED)
        by_subject[user.subject] = f"cookie-{who}"

    def recorded(job: Any) -> RequestContext:
        from app.modules.security.authorization import resolve_context

        cred = store.get(by_subject.get(job.created_by, ""))
        ctx = resolve_context(settings, cred, request_id=job.job_id, identity_port=port, clock=clock,
                              require_fresh=True)
        return worker_context(ctx, run_id=job.job_id)

    def authority(job: Any, caps: Any) -> Any:
        return WorkerAuthority(recorded(job), settings, identity_port=port, clock=clock,
                               credential_provider=lambda: store.get(by_subject.get(job.created_by, ""))
                               ).check(*caps)

    app = _build(monkeypatch, tmp_path, settings, port=port, store=store, worker_ctx=recorded, authority=authority,
                 clock=clock, credential_lookup=lambda subject: store.get(by_subject.get(subject, "")), **kw)
    return app
