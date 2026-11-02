"""C2: ``F2WClient`` job and publication methods forward to the shared ``F2WOperations``.

Each case mirrors an HTTP permission test in ``test_v1_als.py`` / ``test_v1_local.py``: the
same caller gets the same refusal (``denied`` / ``not_found`` / ``stale_approval`` / H5) or
the same success through the SDK as through ``/v1``, because both call one service.
"""
from __future__ import annotations

import pytest

from _v1_support import ingest_inputs, local_settings

from app import public
from app.modules.application import F2WApplication
from app.modules.contracts.context import ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.runtime_bridge.retrieval import LegacyRetrievalService
from app.modules.security.authorization import resolve_context
from tests.read_path.test_read_tool import SUCCESS, StubAgent


def _client(v1):
    app = F2WApplication(settings=v1.settings, retrieval=LegacyRetrievalService(StubAgent(SUCCESS)))
    return public.F2WClient(app, owns_application=True, operations=v1.ops)


def _ctx(als, who, **kw):
    cred = als.store.get(f"cookie-{who}")
    return resolve_context(als.settings, cred, request_id="sdk", identity_port=als.port, clock=als.ops.clock, **kw)


def _refused(call, code=ErrorCode.DENIED):
    with pytest.raises(ContractError) as err:
        call()
    assert code is None or err.value.code is code, err.value
    return err.value


def _to_review(als, client, admin):
    handle = client.plan_publication(admin, "rsoxs/v1", als.target)
    assert als.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    return handle.job_id


def test_ordinary_user_is_refused_before_effects_as_over_http(als):
    """Mirrors test_ordinary_user_is_refused_every_privileged_action_before_effects."""
    with _client(als) as client:
        user = _ctx(als, "user")
        _refused(lambda: client.submit_ingest(user, ingest_inputs()))
        _refused(lambda: client.plan_publication(user, "rsoxs/v1", als.target))
        assert als.ops.jobs.store.list_jobs() == [] and als.tiled.writes == []
        job_id = _to_review(als, client, _ctx(als, "admin"))
        plan = als.ops.current_plan(_ctx(als, "admin"), job_id)
        # Over HTTP the route policy answers 403 before the service runs; the service itself refuses the
        # user too (the admin's job is invisible to them, as on GET /v1/jobs/{id}). Nothing is approved.
        err = _refused(lambda: client.approve_plan(user, plan, job_id=job_id,
                                                   approver_credential=als.store.get("cookie-user")),
                       code=None)
        assert err.code in (ErrorCode.DENIED, ErrorCode.NOT_FOUND)
        assert als.ops.publication.store.valid_approvals(job_id, plan) == [] and als.tiled.writes == []


def test_other_users_do_not_see_an_admins_job(als):
    """Mirrors the HTTP 404s of test_other_users_do_not_see_an_admins_job."""
    with _client(als) as client:
        handle = client.submit_ingest(_ctx(als, "admin"), ingest_inputs())
        for who in ("user", "user2"):
            ctx = _ctx(als, who)
            _refused(lambda: client.get_job(ctx, handle.job_id), ErrorCode.NOT_FOUND)
            _refused(lambda: client.cancel_job(ctx, handle.job_id), ErrorCode.NOT_FOUND)
        assert als.ops.jobs.store.get(handle.job_id).state is JobState.QUEUED


def test_admin_submits_reads_and_cancels_a_job(als):
    with _client(als) as client:
        admin = _ctx(als, "admin")
        handle = client.submit_ingest(admin, ingest_inputs())
        assert client.get_job(admin, handle.job_id) == als.ops.get_job(admin, handle.job_id)
        assert client.get_job(admin, handle.job_id).state is JobState.QUEUED
        assert client.cancel_job(admin, handle.job_id).state is JobState.CANCELLED
        # Same idempotency as POST /v1/jobs: identical inputs return the same job.
        assert client.submit_ingest(admin, ingest_inputs()).job_id == handle.job_id


def test_invalid_ingest_inputs_are_invalid_input(als):
    """Mirrors test_unknown_kind_and_bad_inputs_are_invalid_input (a secret in the inputs)."""
    with _client(als) as client:
        err = _refused(lambda: client.submit_ingest(_ctx(als, "admin"), ingest_inputs(api_key="sk-abcdefabcdefabcd")),
                       ErrorCode.INVALID_INPUT)
        assert "sk-abc" not in str(err) and als.ops.jobs.store.list_jobs() == []


def test_admin_publishes_with_an_als_approval_through_the_sdk(als):
    """Mirrors test_admin_publishes_to_the_rehearsal_target_with_an_als_approval."""
    with _client(als) as client:
        admin = _ctx(als, "admin")
        job_id = _to_review(als, client, admin)
        plan = als.ops.current_plan(admin, job_id)
        before = len(als.port.calls)
        result = client.approve_plan(admin, plan, job_id=job_id, approver_credential=als.store.get("cookie-admin"))
        approval = result["approval"]
        assert approval["approved_by"] == "uuid-admin" and approval["approver_is_admin"] is True
        assert approval["executed_as"] == "f2w-writer" and approval["expires_at"] is not None
        assert len(als.port.calls) > before  # fresh role check inside create_approval
        assert als.work() == (job_id, JobState.SUCCEEDED)
        assert len(als.tiled.entities) == 3


def test_sdk_approval_needs_a_human_the_approvers_credential_and_the_shown_plan(als):
    """Mirrors test_sdk_approval_needs_a_human_and_the_approvers_own_credential and the stale-plan 409."""
    with _client(als) as client:
        admin = _ctx(als, "admin")
        job_id = _to_review(als, client, admin)
        plan = als.ops.current_plan(admin, job_id)
        worker = admin.model_copy(update={"actor_kind": ActorKind.WORKER})
        own = als.store.get("cookie-admin")
        _refused(lambda: client.approve_plan(worker, plan, job_id=job_id, approver_credential=own))
        _refused(lambda: client.approve_plan(admin, plan, job_id=job_id,
                                             approver_credential=als.store.get("cookie-user")))
        _refused(lambda: client.approve_plan(admin, "0" * 64, job_id=job_id, approver_credential=own),
                 ErrorCode.STALE_APPROVAL)
        assert als.tiled.writes == []
        ok = client.approve_plan(admin, plan.plan_sha256, job_id=job_id, approver_credential=own)
        assert ok["approval"]["approved_by"] == "uuid-admin"


def test_h5_gate_and_deployment_target_apply_to_the_sdk(als):
    with _client(als) as client:
        admin = _ctx(als, "admin")
        _refused(lambda: client.plan_publication(admin, "rsoxs/v1", "http://127.0.0.1:1/api#f2w"))
        _refused(lambda: client.plan_publication(admin, "rsoxs/v1", als.settings.tiled_target + "/api#other"),
                 ErrorCode.UNSUPPORTED_CAPABILITY)
        assert als.ops.jobs.store.list_jobs() == []


def test_local_operator_runs_the_same_flow(local):
    with _client(local) as client:
        ctx = client.context_for()
        handle = client.submit_ingest(ctx, ingest_inputs())
        assert client.get_job(ctx, handle.job_id).state is JobState.QUEUED
        assert client.cancel_job(ctx, handle.job_id).state is JobState.CANCELLED
        job_id = _to_review(local, client, ctx)
        plan = local.ops.current_plan(ctx, job_id)
        assert client.approve_plan(ctx, plan, job_id=job_id)["approval"]["approved_by"] == "local-operator"


def test_without_operations_the_methods_are_unsupported_and_mismatched_settings_are_refused(als):
    app = F2WApplication(settings=als.settings, retrieval=LegacyRetrievalService(StubAgent(SUCCESS)))
    with public.F2WClient(app, owns_application=True) as client:
        ctx = _ctx(als, "admin")
        for call in (lambda: client.submit_ingest(ctx, ingest_inputs()), lambda: client.get_job(ctx, "j"),
                     lambda: client.cancel_job(ctx, "j"),
                     lambda: client.plan_publication(ctx, "rsoxs/v1", als.target),
                     lambda: client.approve_plan(ctx, "a" * 64, job_id="j")):
            _refused(call, ErrorCode.UNSUPPORTED_CAPABILITY)
    local_app = F2WApplication(settings=local_settings(), retrieval=LegacyRetrievalService(StubAgent(SUCCESS)))
    with pytest.raises(ValueError):
        public.F2WClient(local_app, operations=als.ops)


def test_t31_methods_apply_the_same_policy_as_http(als):
    """Mirrors the T31 rows of test_ordinary_user_is_refused_every_privileged_action_before_effects."""
    with _client(als) as client:
        user, admin = _ctx(als, "user"), _ctx(als, "admin")
        _refused(lambda: client.cost_control(user))
        _refused(lambda: client.set_cost_control(user, enabled=True, budget_cap_usd=1.0))
        _refused(lambda: client.bulk_review(user, [{"case_id": "c", "bound_revision": "r", "accept": False}]))
        _refused(lambda: client.record_figure_scale(user, corpus="rsoxs", work_id="w", figure_id="f",
                                                    panel_index=0, nm_per_px=1.0))
        _refused(lambda: client.folder_roots(admin))  # T32: never off :local
        assert client.figure_scales(user) == [] and client.vocabulary_quality(user)["count"] == 0
        record = client.record_figure_scale(admin, corpus="rsoxs", work_id="w", figure_id="f", panel_index=0,
                                            nm_per_px=1.0)
        assert client.figure_scale(user, record["id"]) == record
        _refused(lambda: client.figure_scales(user, corpus="other"))
        saved = client.set_cost_control(admin, enabled=True, budget_cap_usd=2.0)
        assert saved["enabled"] is True and client.cost_control(admin)["budget_cap_usd"] == 2.0


def test_local_operator_browses_folders_through_the_sdk(monkeypatch, tmp_path):
    from _v1_support import build_local

    from app.api.folders import FolderRoots

    (tmp_path / "papers" / "sub").mkdir(parents=True)
    local = build_local(monkeypatch, tmp_path, folder_roots=FolderRoots.from_paths({"papers": tmp_path / "papers"}))
    with _client(local) as client:
        ctx = client.context_for()
        assert client.folder_roots(ctx) == {"roots": [{"root": "papers", "exists": True}]}
        assert [e["name"] for e in client.list_folder(ctx, "papers")["entries"]] == ["sub"]
        assert client.folder_index(ctx, "papers", "sub")["documents"] == []
        _refused(lambda: client.list_folder(ctx, "papers", ".."), ErrorCode.INVALID_INPUT)
