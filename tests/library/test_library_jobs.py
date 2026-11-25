"""T34: library job kinds, capability gating, nonce identity, worker factory and profile wiring."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from app.modules.contracts.context import (
    CAPABILITIES,
    LOCAL_ONLY_CAPABILITIES,
    AuthorizationMode,
    DeploymentProfile,
    Principal,
    RequestContext,
)
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore
from app.modules.jobs.keys import SecretInKey
from app.modules.jobs.process_support import worker_command
from app.modules.library import (
    LIBRARY_EXTRACT_KIND,
    LIBRARY_INDEX_KIND,
    LIBRARY_KIND_CAPABILITIES,
    LIBRARY_PROBE_KIND,
    LIBRARY_VERIFY_KIND,
    library_inputs,
    register_library_jobs,
)
from app.modules.security.authorization import (
    DeploymentSettings,
    StartupConfigError,
    capabilities_for,
    local_operator_context,
)
from app.modules.security.tiled_identity import VerifiedIdentity
from app.modules.workers.factories import library_service
from tests.security.security_fakes import local_settings, staging_settings

REPO = Path(__file__).resolve().parents[2]
KINDS = tuple(LIBRARY_KIND_CAPABILITIES)


def _local_ctx(settings: DeploymentSettings | None = None) -> RequestContext:
    return local_operator_context(settings or local_settings(), request_id="r-lib")


def _service(tmp_path: Path, settings: DeploymentSettings | None = None) -> JobService:
    settings = settings or local_settings()
    service = JobService(JobStore(tmp_path / "jobs.sqlite3"), settings=settings)
    register_library_jobs(service, settings)
    return service


def test_capabilities_are_local_only() -> None:
    assert LOCAL_ONLY_CAPABILITIES == {"library.read", "library.upload", "library.index", "library.extract",
                                        "library.harvest", "library.author_schema"}
    # library_publish (2026-11-10) is submitted with graph.publish, the capability of the other publish rows
    assert {v for k, v in LIBRARY_KIND_CAPABILITIES.items() if k != "library_publish"} <= LOCAL_ONLY_CAPABILITIES
    assert LIBRARY_KIND_CAPABILITIES["library_publish"] == "graph.publish"
    assert _local_ctx().capabilities >= LOCAL_ONLY_CAPABILITIES
    assert not capabilities_for(_identity(admin=True)) & LOCAL_ONLY_CAPABILITIES
    assert not capabilities_for(_identity(admin=False)) & LOCAL_ONLY_CAPABILITIES
    assert LOCAL_ONLY_CAPABILITIES <= CAPABILITIES


def test_als_context_refuses_library_capabilities() -> None:
    settings = staging_settings()
    with pytest.raises(ValueError, match="local-operator only"):
        RequestContext(profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
                       principal=Principal(subject="u", issuer="https://t", verified_at=_now(), is_admin=True),
                       capabilities=frozenset({"library.read"}), request_id="r", tiled_target=settings.tiled_target)


def test_local_registers_every_kind_and_probe_completes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert all(service.has_kind(kind) for kind in KINDS)
    handle = service.submit(_local_ctx(), LIBRARY_PROBE_KIND, library_inputs())
    assert service.work_one("w") == (handle.job_id, JobState.SUCCEEDED)
    assert [log.message for log in service.store.logs(handle.job_id)] == ["library probe ok"]


@pytest.mark.parametrize("kind,task", [(LIBRARY_VERIFY_KIND, "T35"), (LIBRARY_INDEX_KIND, "T36"),
                                       (LIBRARY_EXTRACT_KIND, "T39")])
def test_stub_kinds_fail_closed(tmp_path: Path, kind: str, task: str) -> None:
    service = _service(tmp_path)
    handle = service.submit(_local_ctx(), kind, library_inputs())
    assert service.work_one("w") == (handle.job_id, JobState.FAILED)
    stage = service.store.stage(handle.job_id, "run")
    assert stage is not None and stage.error_code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert f"not implemented until {task}" in stage.error


def test_handlers_override_stubs_and_unknown_kind_is_rejected(tmp_path: Path) -> None:
    from app.modules.jobs import StageResult

    settings = local_settings()
    service = JobService(JobStore(tmp_path / "j.sqlite3"), settings=settings)
    register_library_jobs(service, settings, handlers={LIBRARY_INDEX_KIND: lambda ctx: StageResult(data={"n": 1})})
    handle = service.submit(_local_ctx(), LIBRARY_INDEX_KIND, library_inputs())
    assert service.work_one("w") == (handle.job_id, JobState.SUCCEEDED)
    with pytest.raises(ValueError, match="unknown library job kinds"):
        register_library_jobs(JobService(JobStore(tmp_path / "k.sqlite3"), settings=settings), settings,
                              handlers={"library_nope": lambda ctx: None})


def test_registration_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert register_library_jobs(service, local_settings()) == ()


def test_missing_capability_is_denied(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ctx = _local_ctx()
    narrow = ctx.model_copy(update={"capabilities": frozenset({"library.read"})})
    service.submit(narrow, LIBRARY_PROBE_KIND, library_inputs())
    with pytest.raises(ContractError) as err:
        service.submit(narrow, LIBRARY_INDEX_KIND, library_inputs())
    assert err.value.code is ErrorCode.DENIED


def test_als_registers_nothing_and_refuses_submission(tmp_path: Path) -> None:
    settings = staging_settings()
    service = JobService(JobStore(tmp_path / "j.sqlite3"))
    assert register_library_jobs(service, settings) == ()
    assert not any(service.has_kind(kind) for kind in KINDS)
    ctx = _local_ctx()
    with pytest.raises(ContractError) as err:
        service.submit(ctx, LIBRARY_PROBE_KIND, library_inputs())
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_secrets_in_inputs_are_refused() -> None:
    with pytest.raises(SecretInKey):
        library_inputs({"api_key": "x"})
    with pytest.raises(SecretInKey):
        library_inputs({"note": "Bearer abcdef"})


def test_identical_submissions_share_a_job_and_a_nonce_makes_a_new_one(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ctx = _local_ctx()
    first = service.submit(ctx, LIBRARY_PROBE_KIND, library_inputs({"scope": "all"}))
    again = service.submit(ctx, LIBRARY_PROBE_KIND, library_inputs({"scope": "all"}))
    rebuilt = service.submit(ctx, LIBRARY_PROBE_KIND, library_inputs({"scope": "all"}, nonce="2"))
    other = service.submit(ctx, LIBRARY_PROBE_KIND, library_inputs({"scope": "all"}, nonce="3"))
    assert first.job_id == again.job_id
    assert len({first.job_id, rebuilt.job_id, other.job_id}) == 3
    assert library_inputs(nonce="2").params["nonce"] == "2"


def test_worker_factory_registers_kinds_on_local_and_refuses_als(tmp_path: Path) -> None:
    env = {"F2W_DEPLOYMENT_PROFILE": "local"}
    service = library_service(str(tmp_path / "j.sqlite3"), env)
    assert all(service.has_kind(kind) for kind in KINDS)
    with pytest.raises(StartupConfigError):
        library_service(str(tmp_path / "k.sqlite3"), {"F2W_DEPLOYMENT_PROFILE": "als-dev"})


def test_worker_process_claims_and_completes_a_library_job(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite3"
    store = JobStore(db)
    handle = store.submit(LIBRARY_PROBE_KIND, library_inputs(), created_by="local-operator").handle()
    store.close()
    cmd = worker_command(str(db), factory="app.modules.workers.factories:library_service", extra=["--once"])
    done = subprocess.run(cmd, cwd=str(REPO), env={"PATH": "/usr/bin:/bin", "F2W_DEPLOYMENT_PROFILE": "local",
                                                    "F2W_DOTENV": "off"},
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode()[-600:]
    check = JobStore(db)
    assert check.get(handle.job_id).state is JobState.SUCCEEDED
    check.close()


def test_profiles_wire_the_factory_on_local_only() -> None:
    def factory(name: str) -> str:
        data = json.loads((REPO / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))
        return str(data["env"]["F2W_WORKER_FACTORY"])

    assert factory("local") == "app.modules.workers.factories:library_service"
    assert factory("als-dev") == factory("als-prod") == "app.modules.workers.factories:idle_service"


# --- helpers -------------------------------------------------------------------------------------------


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _identity(*, admin: bool) -> VerifiedIdentity:
    return VerifiedIdentity(subject="u", issuer="https://t", principal_type="user", verified_at=_now(),
                            roles=("admin",) if admin else ())
