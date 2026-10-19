"""T25C item 4: the writer service account writes; user tokens never do; a revoked writer fails truthfully.

On the live multi-user server the F2W technical writer is a Tiled *service* principal with
the admin role and an API key (D27). An ALS admin approves with their own fresh whoami;
the T08B importer then writes with the writer key only. Every ``Authorization`` header the
import client sends is recorded and checked.

Test-only detail: the graph client validates its URL per profile and accepts only the fixed
central URL for ``als-dev``. The client is therefore built for the ``test`` profile (loopback)
and then labelled ``als-dev`` so the importer's profile binding check runs as on ALS; no
production code path builds a client this way.

Binary cases: AUTH-05 (verified requester/approver, separate ``executed_as``), AUTH-06 (the
writer never establishes a user), AUTH-11 (no user token or chat recording reaches Tiled
writes), LOCAL-06 (a missing or revoked writer is a graph-operation error, never sign-in).
"""
from __future__ import annotations

import pytest

from live_apps import build_live_als
from live_support import (
    RecordingSession, access_token, all_tokens_in, create_service_account, entity_count, principal_uuid,
    revoke_service_key, utcnow,
)
from tiled_server import tiled_python

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.deployment import tiled_capabilities as caps
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.schema.registry import SchemaRegistry
from app.modules.security.authorization import (
    DeploymentSettings, Unauthenticated, create_approval, resolve_context,
)
from app.modules.security.tiled_identity import TiledCredential
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


def _snapshot(tmp_path, registry):
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=storage, terms_ref="inputs/core_dict.terms.json",
                                    code_commit=COMMIT, registry=registry))
    return storage / "synthetic" / "v1"


def _als_writer_client(inst, key, session=None):
    client = TiledMutationClient(inst.url, profile="test", credential=key, session=session)
    client.profile = "als-dev"  # see the module docstring
    return client


def _als(inst, writer):
    return DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=inst.url,
                                                         writer_identity=writer.uuid)


def test_admin_approved_import_is_executed_by_the_writer_only(tmp_path, registry, live_fresh):
    """Fresh server: the import is the only writer of graph records on it."""
    from app.modules.security.tiled_identity import TiledWhoamiAdapter
    import httpx

    inst, writer = live_fresh, live_fresh.writer
    settings = _als(inst, writer)
    admin_token, reader_token = access_token(inst, "admin"), access_token(inst, "reader")
    with httpx.Client() as http:
        port = TiledWhoamiAdapter(inst.url, http)
        admin = TiledCredential(admin_token, tiled_target=inst.url)
        ctx = resolve_context(settings, admin, request_id="r-imp", identity_port=port, clock=utcnow)
        snap = _snapshot(tmp_path, registry)
        fingerprints = tj.default_supported_schema_fingerprints(registry)
        session = RecordingSession()
        client = _als_writer_client(inst, writer.key, session)
        plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
        approval = create_approval(ctx, plan.change, settings, approval_id="a-imp", clock=utcnow,
                                   identity_port=port, approver_credential=admin)
        # A reader cannot publish even with a valid approval in hand (checked before any write).
        reader_ctx = resolve_context(settings, TiledCredential(reader_token, tiled_target=inst.url),
                                     request_id="r-rd", identity_port=port, clock=utcnow)
        writes_before = len(session.authorizations)
        with pytest.raises(ContractError) as err:
            tj.apply_import(reader_ctx, plan, approval, source=snap, client=client, settings=settings,
                            supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "state", clock=utcnow)
        assert err.value.code is ErrorCode.DENIED and len(session.authorizations) == writes_before
        out = tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                              supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "state",
                              clock=utcnow)
    admin_uuid = principal_uuid(inst, "admin")
    assert out.status == "applied" and out.receipt.receipt.verified
    assert out.receipt.receipt.executed_as == writer.uuid
    assert out.receipt.extras.approved_by == out.receipt.extras.requested_by == admin_uuid != writer.uuid
    assert approval.approver_issuer == inst.url and approval.approver_is_admin is True
    # Every request the import client sent carried the writer key, never a user token.
    assert session.authorizations and set(session.authorizations) == {f"Apikey {writer.key}"}
    assert not all_tokens_in(session.authorizations, admin_token, reader_token)
    assert entity_count(inst) == plan.change.creates


def test_writer_credential_never_signs_a_user_in(live_auth):
    """AUTH-06: the writer key resolves to the writer service principal and is refused as a caller."""
    import httpx
    from app.modules.security.tiled_identity import TiledWhoamiAdapter

    settings = _als(live_auth, live_auth.writer)
    with httpx.Client() as http:
        with pytest.raises(Unauthenticated) as exc:
            resolve_context(settings, TiledCredential(live_auth.writer.key, tiled_target=live_auth.url,
                                                      scheme="Apikey"),
                            request_id="r", identity_port=TiledWhoamiAdapter(live_auth.url, http), clock=utcnow)
    assert exc.value.reason == "writer_credential"


def test_revoked_writer_key_fails_graph_operations_and_leaves_chat(monkeypatch, tmp_path, registry, live_auth):
    writer = create_service_account(live_auth)
    # The app is configured with this writer; its key is revoked while the app is running.
    als = build_live_als(monkeypatch, tmp_path, live_auth, writer=writer)
    reader = als.client("reader")
    assert caps.check_capabilities(live_auth.url, credential=writer.key, require_writer_role="admin") \
        .writer_principal == writer.uuid
    revoke_service_key(live_auth, writer)

    with pytest.raises(caps.TiledWriterUnauthorized) as exc:
        caps.check_capabilities(live_auth.url, credential=writer.key, require_writer_role="admin")
    assert exc.value.code == "writer_unauthorized"
    snap = _snapshot(tmp_path, registry)
    before = entity_count(live_auth)
    with pytest.raises(ContractError) as err:
        tj.plan_import(snap, client=_als_writer_client(live_auth, writer.key), settings=als.settings,
                       supported_schema_fingerprints=tj.default_supported_schema_fingerprints(registry))
    # C1: a writer 401/403 is ``writer_unauthorized``, not a generic ``denied`` (access contract section 12).
    assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and "HTTP 401" in str(err.value)
    assert err.value.retryable is False and writer.key not in str(err.value)
    assert entity_count(live_auth) == before
    # Chat and sign-in are unaffected: the writer is never part of the caller's identity.
    resp = reader.post("/chat", json={"message": "What is Term A?", "session_id": "s-w",
                                      "client_turn_id": "t-w"})
    assert resp.status_code == 200 and "sign_in_required" not in resp.text
    assert als.client("admin").get("/settings").status_code == 200


def test_wrong_writer_key_is_writer_unauthorized_and_writes_nothing(tmp_path, registry, live_auth):
    """C1: a key Tiled never issued is ``writer_unauthorized`` at plan time; no fallback, no write."""
    writer = create_service_account(live_auth)
    settings = _als(live_auth, writer)
    snap = _snapshot(tmp_path, registry)
    before = entity_count(live_auth)
    wrong = "f2w-wrong-writer-key-" + writer.key[::-1]
    session = RecordingSession()
    with pytest.raises(ContractError) as err:
        tj.plan_import(snap, client=_als_writer_client(live_auth, wrong, session), settings=settings,
                       supported_schema_fingerprints=tj.default_supported_schema_fingerprints(registry))
    assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and err.value.retryable is False
    assert wrong not in str(err.value)
    # Exactly the wrong key was sent: no retry with another key, no anonymous request.
    assert session.authorizations and set(session.authorizations) == {f"Apikey {wrong}"}
    assert entity_count(live_auth) == before


def test_writer_revoked_after_approval_fails_apply_with_writer_unauthorized(tmp_path, registry, live_auth):
    """C1: the key is revoked between an approved plan and apply: no write and no receipt."""
    import httpx
    from app.modules.security.tiled_identity import TiledWhoamiAdapter

    writer = create_service_account(live_auth)
    settings = _als(live_auth, writer)
    snap = _snapshot(tmp_path, registry)
    fingerprints = tj.default_supported_schema_fingerprints(registry)
    client = _als_writer_client(live_auth, writer.key)
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    with httpx.Client() as http:
        port = TiledWhoamiAdapter(live_auth.url, http)
        admin = TiledCredential(access_token(live_auth, "admin"), tiled_target=live_auth.url)
        ctx = resolve_context(settings, admin, request_id="r-rev", identity_port=port, clock=utcnow)
        approval = create_approval(ctx, plan.change, settings, approval_id="a-rev", clock=utcnow,
                                   identity_port=port, approver_credential=admin)
    revoke_service_key(live_auth, writer)
    before = entity_count(live_auth)
    state = tmp_path / "state"
    with pytest.raises(ContractError) as err:
        tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                        supported_schema_fingerprints=fingerprints, state_dir=state, clock=utcnow)
    assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and err.value.retryable is False
    assert entity_count(live_auth) == before
    assert not [p for p in state.rglob("*") if p.is_file() and "receipts" in p.parts]


def test_missing_local_writer_is_a_graph_error_not_sign_in(tmp_path, registry, live_single):
    """LOCAL-06: no writer credential on :local fails the graph operation; nothing asks for sign-in."""
    from app.modules.security.authorization import local_operator_context

    settings = DeploymentSettings(profile="local", writer_identity="f2w-local-writer",
                                  tiled_target=live_single.url)
    ctx = local_operator_context(settings, request_id="r")
    snap = _snapshot(tmp_path, registry)
    fingerprints = tj.default_supported_schema_fingerprints(registry)
    for credential in (None, "not-the-writer-key"):
        client = TiledMutationClient(live_single.url, profile="test", credential=credential)
        with pytest.raises(ContractError) as err:
            # 0.2.18 lists graph records to anonymous callers (capabilities item 18), so a
            # missing key may get through the dry run; the first write is then refused.
            plan = tj.plan_import(snap, client=client, settings=settings,
                                  supported_schema_fingerprints=fingerprints)
            approval = create_approval(ctx, plan.change, settings, approval_id="a-miss", clock=utcnow)
            tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                            supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "state", clock=utcnow)
        if credential:  # C1: a wrong writer key is a writer 401, reported as writer_unauthorized
            assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED, err.value
        else:
            assert err.value.code in (ErrorCode.DENIED, ErrorCode.UNAVAILABLE,
                                      ErrorCode.WRITER_UNAUTHORIZED), err.value
        assert "sign" not in str(err.value).lower()
    assert ctx.has("graph.publish")  # the local operator still holds every capability
