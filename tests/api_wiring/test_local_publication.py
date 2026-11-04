"""``:local``-only publication into the image's own Tiled (owner decision 2026-11-04, ninth set).

Pins the H5 gate wiring of :mod:`app.api.local_publication`:

* ``:local`` + the managed loopback Tiled that is the deployment's own target: exactly that target is allowed;
* every other target (remote host, ALS, another port) is still refused (403 outside the deployment);
* ALS profiles always get an empty allowlist, whatever the environment says;
* the allowlist cannot be reached from the environment, from settings or from a request;
* smoke path (real disposable Tiled on 127.0.0.1, no other network): frozen snapshot -> ``:local`` publication ->
  Tiled -> read back.
"""
import inspect
import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "security", REPO / "tests" / "fixtures", REPO / "tests" / "contracts" / "legacy_ui" / "closure", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402
from closure_support import loopback_guard  # noqa: E402
from security_fakes import STAGING, staging_settings  # noqa: E402
from tiled_server import tiled_base, tiled_python, tiled_seeded_pool  # noqa: E402,F401

from app.api import local_publication as lp  # noqa: E402
from app.api.main import create_api_app  # noqa: E402
from app.api.operations import F2WOperations, PublicationGate, build_operations  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.contracts.jobs import JobState  # noqa: E402
from app.modules.deployment.tiled_config import ManagedTiledConfig  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import DeploymentSettings  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

LOCAL = "http://127.0.0.1:8765"
WRITER = "f2w-writer"


def managed(tmp_path, port=8765, profile="local"):
    extra = {"owner": "f2w-test"} if profile == "test" else {}
    return ManagedTiledConfig(profile=profile, data_dir=tmp_path, tiled_python=Path("/nonexistent/python"),
                              port=port, **extra)


def local(target=LOCAL):
    return DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=target)


def env_for(tmp_path, port=8765, profile="local"):
    return {"F2W_DEPLOYMENT_PROFILE": profile, "F2W_TILED_DATA_DIR": str(tmp_path),
            "F2W_TILED_PYTHON": "/nonexistent/python", "F2W_TILED_PORT": str(port)}


# --- the allowlist -----------------------------------------------------------------------------------

def test_local_loopback_target_is_the_one_allowed(tmp_path):
    settings = local()
    gate = lp.local_publication_gate(settings, managed(tmp_path))
    assert gate.rehearsal_targets == frozenset({LOCAL})
    gate.check(LOCAL, settings)                      # allowed (no raise)
    gate.check(LOCAL + "#f2w", settings)             # a namespace of the same Tiled
    assert gate.allows(LOCAL)


@pytest.mark.parametrize("target", [
    "http://127.0.0.1:9999",                         # another port on loopback
    "http://127.0.0.2:8765",                         # another loopback host
    "https://tiled.example.org",                     # a remote host
    "https://tiled.als.lbl.gov",                     # the central ALS Tiled
    "https://tiled-dev.als.lbl.gov/api#f2w",         # ALS staging
    "not a url",
])
def test_every_other_target_is_still_refused_on_local(tmp_path, target):
    settings = local()
    gate = lp.local_publication_gate(settings, managed(tmp_path))
    assert not gate.allows(target)
    with pytest.raises(ContractError) as exc:
        gate.check(target, settings)
    assert exc.value.code is ErrorCode.DENIED         # not this deployment's Tiled (403)


def test_in_deployment_but_unlisted_target_keeps_the_h5_501():
    with pytest.raises(ContractError) as exc:
        PublicationGate().check(LOCAL, local())
    assert exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY and "H5" in str(exc.value)


def test_gate_is_empty_when_the_managed_tiled_is_not_the_deployments_target(tmp_path):
    # the image's Tiled listens on 9999 but the deployment names 8765: nothing is allowed
    assert lp.local_publication_gate(local(), managed(tmp_path, port=9999)) == PublicationGate()
    assert lp.local_publication_gate(local(), None) == PublicationGate()           # no managed Tiled at all
    assert lp.local_tiled_target(local(), managed(tmp_path, profile="test")) is None  # the test profile is not :local


@pytest.mark.parametrize("profile", ["als-dev", "als-prod"])
def test_als_profiles_have_an_empty_allowlist(tmp_path, profile):
    settings = DeploymentSettings(profile=profile, writer_identity=WRITER)
    assert lp.local_publication_gate(settings, managed(tmp_path)).rehearsal_targets == frozenset()
    ops = build_operations(settings, tmp_path / "ops", identity_port=object(), credential_lookup=lambda _s: None)
    assert lp.install_local_publication(ops, environ=env_for(tmp_path), workdir=tmp_path, ops_root=tmp_path) is None
    assert ops.gate.rehearsal_targets == frozenset() and ops.publication is None


def test_allowlist_is_not_reachable_from_environment_settings_or_requests(tmp_path, monkeypatch):
    # The gate's only field is a constructor argument; no environment variable feeds it.
    assert [f.name for f in fields(PublicationGate)] == ["rehearsal_targets"]
    for name in ("F2W_REHEARSAL_TARGETS", "F2W_PUBLICATION_TARGETS", "F2W_PUBLICATION_ALLOWLIST"):
        monkeypatch.setenv(name, "https://tiled.als.lbl.gov,http://127.0.0.1:9999")
    assert PublicationGate().rehearsal_targets == frozenset()
    ops = build_operations(local(), tmp_path / "ops")
    assert ops.gate.rehearsal_targets == frozenset()
    # the environment names a port of the image's own Tiled, never a target: a mismatch allows nothing
    assert lp.local_tiled_target(local(), lp.managed_tiled_from(env_for(tmp_path, port=9999))) is None
    # the module never reads os.environ itself, and the wiring flag is a Python argument of the factory
    assert "os.environ" not in inspect.getsource(lp)
    assert "local_publication" in inspect.signature(create_api_app).parameters


def test_install_wires_publication_and_gate_on_local_only_when_the_managed_tiled_matches(tmp_path):
    ops = build_operations(local(), tmp_path / "ops")
    assert ops.publication is None and ops.gate.rehearsal_targets == frozenset()
    mismatch = lp.install_local_publication(ops, environ=env_for(tmp_path, port=9999), workdir=tmp_path / "w",
                                            ops_root=tmp_path / "ops")
    assert mismatch is None and ops.publication is None and ops.gate.rehearsal_targets == frozenset()
    wired = lp.install_local_publication(ops, environ=env_for(tmp_path), workdir=tmp_path / "w",
                                         ops_root=tmp_path / "ops")
    assert wired is not None and wired.target == LOCAL
    assert ops.publication is wired.publication and ops.gate.rehearsal_targets == frozenset({LOCAL})
    assert ops.jobs.has_kind("publish")
    assert type(wired.step).__name__ == "ReplacementPublishStep"   # the real corpus-scoped importer


def test_create_api_app_wires_local_only_when_trusted_wiring_asks(tmp_path, monkeypatch):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    (tmp_path / "tiled").mkdir()
    startup = {**env_for(tmp_path / "tiled"), "F2W_TILED_WRITER_IDENTITY": WRITER}
    off = create_api_app(make_cfg(ctx), deployment=local(), startup_config=startup, operations_root=tmp_path / "o1")
    assert off.state.f2w_operations.publication is None and not off.state.f2w_operations.gate.rehearsal_targets
    on = create_api_app(make_cfg(ctx), deployment=local(), startup_config=startup, operations_root=tmp_path / "o2",
                        local_publication=True)
    ops = on.state.f2w_operations
    assert ops.publication is not None and ops.gate.rehearsal_targets == frozenset({LOCAL})


def test_create_api_app_on_als_ignores_the_wiring_flag(tmp_path, monkeypatch):
    from security_fakes import FakeTiledIdentityPort, InMemoryCredentialStore

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    (tmp_path / "tiled").mkdir()
    settings = staging_settings(tiled_target=STAGING)
    port = FakeTiledIdentityPort(tiled_target=STAGING)
    prebuilt = build_operations(settings, tmp_path / "o", identity_port=port, credential_lookup=lambda _s: None)
    app = create_api_app(make_cfg(ctx), deployment=settings, identity_port=port,
                         credential_store=InMemoryCredentialStore(), operations=prebuilt,
                         startup_config=env_for(tmp_path / "tiled"), local_publication=True)
    ops = app.state.f2w_operations
    assert ops.publication is None and ops.gate.rehearsal_targets == frozenset()


def test_post_extraction_publisher_needs_exactly_one_scoped_corpus(tmp_path):
    ops = build_operations(local(), tmp_path / "ops")
    wired = lp.install_local_publication(ops, environ=env_for(tmp_path), workdir=tmp_path / "w",
                                         ops_root=tmp_path / "ops")
    assert wired is not None
    assert lp.post_extraction_publisher(ops, wired) is None            # no F2W_CORPUS_SCOPE: phases report skipped
    scoped = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=LOCAL,
                                corpus_scope=frozenset({"synthetic"}))
    ops2 = F2WOperations(scoped, ops.jobs, publication=ops.publication, publication_gate=ops.gate)
    pub = lp.post_extraction_publisher(ops2, wired)
    assert pub is not None and pub.target == LOCAL


# --- smoke path: frozen KG snapshot -> :local publication -> the container's own Tiled -> read back ----------

def test_smoke_local_publication_into_a_loopback_tiled_and_read_back(tmp_path, monkeypatch, request):
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set")
    loopback_guard(monkeypatch)                           # 127.0.0.1 only
    inst = request.getfixturevalue("tiled_seeded_pool").acquire()
    port = int(inst.url.rsplit(":", 1)[1])
    settings = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=inst.url)
    environ = {"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_DATA_DIR": str(inst.root),
               "F2W_TILED_PYTHON": str(tiled_python()), "F2W_TILED_PORT": str(port)}
    ops = build_operations(settings, tmp_path / "ops")
    wired = lp.install_local_publication(ops, environ=environ, workdir=tmp_path / "work", ops_root=tmp_path / "ops")
    assert wired is not None and wired.target == inst.url and ops.gate.rehearsal_targets == frozenset({inst.url})
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=wired.storage_kg, terms_ref="inputs/core_dict.terms.json",
                                    code_commit=COMMIT, registry=SchemaRegistry.discover()))
    ctx = ops.cli_context()
    with pytest.raises(ContractError) as refused:         # not this Tiled: refused before any job exists
        ops.submit_publication(ctx, "synthetic/v1", "https://tiled.als.lbl.gov")
    assert refused.value.code is ErrorCode.DENIED
    handle = ops.submit_publication(ctx, "synthetic/v1", inst.url)
    assert ops.jobs.work_one("w1") == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = ops.current_plan(ctx, handle.job_id)
    ops.approve_publication(ctx, handle.job_id, plan.plan_sha256)
    assert ops.jobs.work_one("w1") == (handle.job_id, JobState.SUCCEEDED)
    pointer = ops.publication_pointer(ctx, inst.url)
    assert pointer is not None and pointer["snapshot_ref"] == "synthetic/v1"
    # read back through Tiled's own GraphQL with the instance's writer key
    names = {e["name"] for e in inst.graphql("{ entities(limit: 500, offset: 0) { id name } }")["entities"]}
    graph = json.loads((wired.storage_kg / "synthetic" / "v1" / "graph.json").read_text(encoding="utf-8"))
    published = {t["name"] for t in graph["things"]}
    assert published and published <= names
