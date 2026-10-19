"""T08B: authorization refusals, preflight, unavailability, scans, edit journal, receipts, D21."""
import json
from datetime import timedelta

import pytest
from import_fakes import CountingClient, MemoryTiled, snapshot_with_graph, tree_bytes

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import edit_journal as ej
from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.imports.receipts import ReceiptStore, verify_artifact
from app.modules.graph.ports import TruncatedGraphRead
from app.modules.security.authorization import DeploymentSettings, create_approval, local_operator_context
from test_import_flow import foreign_fp, graph_of, owned


def refused(env, plan, approval, snap, code):
    before = env.writes()
    with pytest.raises(ContractError) as err:
        env.apply(plan, approval, snap)
    assert err.value.code is code, err.value
    assert env.writes() == before
    assert not list(env.state_dir.rglob("journal.jsonl")) if env.state_dir.exists() else True


def test_no_approval_wrong_plan_expired_and_modified_plans_are_refused_without_writes(env, snap, tmp_path):
    plan = env.plan(snap)
    refused(env, plan, None, snap, ErrorCode.DENIED)
    expired = env.approve(plan, expires_at=env.clock() + timedelta(seconds=60))
    env.clock.advance(61)
    refused(env, plan, expired, snap, ErrorCode.STALE_APPROVAL)
    env.clock.advance(-61)
    g = graph_of(snap)
    g["things"][0]["description"] = "different"
    other_snap = snapshot_with_graph(snap, tmp_path / "o", "synthetic", "v2", g)
    other = env.plan(other_snap)
    assert other.change.plan_sha256 != plan.change.plan_sha256
    refused(env, plan, env.approve(other), snap, ErrorCode.STALE_APPROVAL)           # approval of another plan
    tampered = tj.ImportPlan(plan.change, {**plan.detail, "ops": plan.detail["ops"][:-1]})
    refused(env, tampered, env.approve(plan), snap, ErrorCode.STALE_APPROVAL)         # detail edited
    refused(env, plan, env.approve(plan), other_snap, ErrorCode.CONFLICT)  # source is not the planned one


def test_stale_base_revision_is_refused_without_writes(env, snap):
    plan = env.plan(snap)
    approval = env.approve(plan)
    graph_id = plan.detail["graph_id"]
    # Someone else (a Tiled-only edit) lands an owned record after the plan was computed.
    env.client.create_entity(entity_type="Material", name="late", uri=f"f2w:kg/{graph_id}/node/late",
                             properties={"f2w_owner": "f2w-literature", "f2w_graph_id": graph_id,
                                         "f2w_id": "late"},
                             access_blob={})
    before = env.writes()
    refused(env, plan, approval, snap, ErrorCode.STALE_APPROVAL)
    assert env.writes() == before


def test_missing_capability_and_scope_are_denied(env, snap):
    plan = env.plan(snap)
    approval = env.approve(plan)
    no_cap = env.ctx.model_copy(update={"capabilities": frozenset({"chat.use"} & env.ctx.capabilities)})
    with pytest.raises(ContractError) as err:
        tj.apply_import(no_cap, plan, approval, source=snap, client=env.client, settings=env.settings,
                        supported_schema_fingerprints=env.fingerprints, state_dir=env.state_dir, clock=env.clock)
    assert err.value.code is ErrorCode.DENIED
    other_scope = env.ctx.model_copy(update={"corpus_scope": frozenset({"other"})})
    with pytest.raises(ContractError) as err:
        tj.apply_import(other_scope, plan, approval, source=snap, client=env.client, settings=env.settings,
                        supported_schema_fingerprints=env.fingerprints, state_dir=env.state_dir, clock=env.clock)
    assert err.value.code is ErrorCode.DENIED
    assert env.writes() == []


class ForbiddenClient(MemoryTiled):
    def _guard(self):
        raise AssertionError("an ALS target must never be contacted by these tests")


def test_local_approval_cannot_authorize_an_als_target(snap, clock, fingerprints):
    local = DeploymentSettings(profile="local", writer_identity="f2w-writer",
                               tiled_target="http://127.0.0.1:59999")
    ctx = local_operator_context(local, request_id="x")
    client = MemoryTiled()
    plan = tj.plan_import(snap, client=client, settings=local, supported_schema_fingerprints=fingerprints)
    approval = create_approval(ctx, plan.change, local, approval_id="a", clock=clock)
    staging = DeploymentSettings(profile="als-dev", writer_identity="f2w-writer")
    # An ALS plan: same content, target is the ALS staging Tiled; the local approval is for another target.
    als_plan = tj.ImportPlan(plan.change.model_copy(update={"target": staging.tiled_target}), plan.detail)
    with pytest.raises(ContractError) as err:
        tj.apply_import(ctx, als_plan, approval, source=snap, client=client, settings=local,
                        supported_schema_fingerprints=fingerprints, state_dir=snap.parent / "st", clock=clock)
    assert err.value.code in (ErrorCode.DENIED, ErrorCode.STALE_APPROVAL, ErrorCode.INVALID_INPUT)
    assert client.writes == []
    with pytest.raises(ContractError) as err:   # planning an ALS target from a local deployment is refused
        tj.plan_import(snap, client=client, settings=staging, supported_schema_fingerprints=fingerprints)
    assert err.value.code is ErrorCode.DENIED and client.writes == []


def test_unavailable_tiled_leaves_json_intact_and_import_retryable(snap, clock, fingerprints, tmp_path):
    client = MemoryTiled()
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=client.base_url)
    ctx = local_operator_context(settings, request_id="u")
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    approval = create_approval(ctx, plan.change, settings, approval_id="a", clock=clock)
    before = tree_bytes(snap.parents[1])
    client.down = True
    with pytest.raises(ContractError) as err:
        tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    with pytest.raises(ContractError) as err:
        tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                        supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "st", clock=clock)
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    assert tree_bytes(snap.parents[1]) == before and not ReceiptStore(tmp_path / "st" / "receipts").all()
    assert client.entities == {} and client.writes == []
    client.down = False                                    # Tiled returns: the same plan and approval complete
    out = tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                          supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "st", clock=clock)
    assert out.status == "applied"


def test_server_outage_mid_import_is_retryable_and_resumes(snap, clock, fingerprints, tmp_path):
    client = MemoryTiled()
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=client.base_url)
    ctx = local_operator_context(settings, request_id="u")
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    approval = create_approval(ctx, plan.change, settings, approval_id="a", clock=clock)

    def outage(stage, i):
        if (stage, i) == ("links", 2):
            client.down = True
    kw = dict(source=snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints,
              state_dir=tmp_path / "st", clock=clock)
    with pytest.raises(ContractError) as err:
        tj.apply_import(ctx, plan, approval, fault=outage, **kw)
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    assert not ReceiptStore(tmp_path / "st" / "receipts").all()          # nothing reported published
    client.down = False
    out = tj.apply_import(ctx, plan, approval, **kw)
    assert out.status == "applied" and out.resumed
    assert len(client.links) == len(graph_of(snap)["associations"])


def test_partial_scan_blocks_planning(snap, clock, fingerprints):
    client = MemoryTiled()
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=client.base_url)
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    approval = create_approval(local_operator_context(settings, request_id="p"), plan.change, settings,
                               approval_id="a", clock=clock)
    tj.apply_import(local_operator_context(settings, request_id="p"), plan, approval, source=snap, client=client,
                    settings=settings, supported_schema_fingerprints=fingerprints, state_dir=snap.parent / "st",
                    clock=clock)
    client.max_rows = 3
    with pytest.raises(TruncatedGraphRead):
        tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)


def test_real_scan_aborts_at_the_bound_and_on_concurrent_change():
    client = TiledMutationClient("http://127.0.0.1:59999", profile="test", credential="x")
    rows = [{"id": str(i)} for i in range(10)]
    client.query = lambda doc, v: {"entities": rows[v["o"]:v["o"] + v["l"]]}
    assert len(client.scan_entities(page_size=4, max_rows=100)) == 10
    with pytest.raises(TruncatedGraphRead):
        client.scan_entities(page_size=4, max_rows=10)
    client.query = lambda doc, v: {"entities": rows[:v["l"]]}      # offset ignored: duplicate ids across pages
    with pytest.raises(ContractError) as err:
        client.scan_entities(page_size=4, max_rows=100)
    assert err.value.code is ErrorCode.CONFLICT


# ---- preflight -------------------------------------------------------------------------------

def test_preflight_refusals(env, snap, tmp_path):
    def attempt(source, **kw):
        with pytest.raises(ContractError) as err:
            env.plan(source, **kw)
        assert err.value.code is ErrorCode.INVALID_INPUT
        return str(err.value)
    with pytest.raises(ContractError) as err:
        tj.plan_import(snap, client=env.client, settings=env.settings, supported_schema_fingerprints={"0" * 64})
    assert "supported set" in str(err.value)
    g = graph_of(snap)
    man = json.loads((snap / "manifest.json").read_text())
    failing = snapshot_with_graph(snap, tmp_path / "f", "synthetic", "v3", g,
                                  patch_manifest={"disclosure": {**man["disclosure"], "status": "fail"}})
    assert "disclosure" in attempt(failing)
    bad_val = snapshot_with_graph(snap, tmp_path / "v", "synthetic", "v4", g,
                                  patch_manifest={"validation": {**man["validation"], "status": "fail"}})
    assert "validation" in attempt(bad_val)
    (snap / "graph.json").write_bytes((snap / "graph.json").read_bytes() + b" ")     # tampered after freezing
    assert "verification failed" in attempt(snap)
    assert env.writes() == []


def test_owner_waiver_is_accepted(env, snap, tmp_path):
    g = graph_of(snap)
    man = json.loads((snap / "manifest.json").read_text())
    waived = snapshot_with_graph(snap, tmp_path / "w", "synthetic", "v5", g, patch_manifest={
        "disclosure": {**man["disclosure"], "status": "owner_waiver", "waiver_ref": "owner:2026-10-14"}})
    assert env.plan(waived).change.creates == len(g["things"])


def test_importer_never_parses_calls_a_model_or_downloads(env, snap, monkeypatch):
    from app.modules import json2kg
    for name in ("build_graph", "convert_terms_to_graph"):
        monkeypatch.setattr(json2kg, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("D21")))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("download")))
    import ast
    import app.modules.graph.imports.tiled_json as mod
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for banned in ("json2kg", "openai", "anthropic", "extract", "download", "enrich", "llm", "requests",
                   "inference"):
        assert not any(banned in name for name in imported), banned
    plan = env.plan(snap)
    env.apply(plan, env.approve(plan), snap)


# ---- edit journal ----------------------------------------------------------------------------

def _edit(env, plan_snap_sha, state="acknowledged", edit_id="e1", **kw):
    return ej.EditRecord(edit_id=edit_id, seq=1, state=state, target=env.url, scope=("synthetic",),
                         base_snapshot_id="v1", base_graph_sha256=plan_snap_sha, profile="local",
                         mode="local_trusted", actor="local-operator", operation="node_patch", ids=("matkg:A",),
                         before={"description": "a"}, after={"description": "b"}, recorded_at=env.clock(), **kw)


def test_acknowledged_edit_is_never_lost_by_an_import(env, snap, tmp_path):
    journal = ej.EditJournal(tmp_path / "journal.jsonl")
    sha = json.loads((snap / "manifest.json").read_text())["graph"]["sha256"]
    journal.append(_edit(env, sha, edit_id="e1"))
    journal.append(_edit(env, sha, state="recorded", edit_id="e2"))     # never applied or acknowledged
    with pytest.raises(ContractError) as err:
        env.plan(snap, edit_journal=journal)
    assert err.value.code is ErrorCode.CONFLICT and "e1" in str(err.value) and "e2" not in str(err.value)
    assert env.writes() == []
    res = ej.ConflictResolution(edit_id="e1", decision="discarded", reviewed_by="local-operator",
                                reviewed_at=env.clock(), note="superseded by rebuild")
    plan = env.plan(snap, edit_journal=journal, resolutions=[res])
    assert plan.detail["resolutions_sha256"] == ej.resolutions_sha256([res])
    assert env.plan(snap).detail["resolutions_sha256"] != plan.detail["resolutions_sha256"]   # bound into the hash
    journal.advance("e1", "reconciled", env.clock(), reconciled_in="v1")   # carried by a snapshot
    assert env.plan(snap, edit_journal=journal).change.creates > 0
    journal2 = ej.EditJournal(tmp_path / "j2.jsonl")
    journal2.append(_edit(env, sha, edit_id="e9", reconciled_in="v0"))
    with pytest.raises(ContractError):
        env.plan(snap, edit_journal=journal2)
    assert env.plan(snap, edit_journal=journal2, lineage=["v0"]).change.creates > 0


def test_edit_journal_is_durable_append_only_and_ordered(env, tmp_path):
    journal = ej.EditJournal(tmp_path / "j.jsonl")
    rec = _edit(env, "a" * 64)
    journal.append(rec)
    with pytest.raises(ContractError):
        journal.append(rec)                                   # same seq twice
    journal.advance("e1", "reconciled", env.clock(), reconciled_in="v2")
    assert journal.latest()["e1"].state == "reconciled" and len(journal.path.read_text().splitlines()) == 2
    assert len(journal.sha256()) == 64
    other_scope = ej.EditJournal(tmp_path / "j3.jsonl")
    other_scope.append(rec.model_copy(update={"scope": ("another",)}))
    assert ej.unreconciled(other_scope, target=env.url, scope=("synthetic",), lineage=["v1"]) == []


# ---- receipts and readback ---------------------------------------------------------------------

def test_receipt_is_a_separate_hashed_immutable_artifact(env, snap):
    plan = env.plan(snap)
    out = env.apply(plan, env.approve(plan), snap)
    art = out.receipt
    assert verify_artifact(art) and out.receipt_path.parent == env.state_dir / "receipts"
    assert snap not in out.receipt_path.parents
    store = ReceiptStore(env.state_dir / "receipts")
    tampered = art.model_copy(update={"extras": art.extras.model_copy(update={"approved_by": "mallory"})})
    assert not verify_artifact(tampered)
    with pytest.raises(ContractError):
        store.write(tampered)                                 # same id, different content: never replaced
    assert store.read(art.receipt.receipt_id).receipt_sha256 == art.receipt_sha256


def test_forge_sentinels_and_links_survive_and_t08a_reader_sees_the_import(env, snap):
    foreign = foreign_fp(env)
    assert foreign                                           # the target holds FORGE records
    plan = env.plan(snap)
    out = env.apply(plan, env.approve(plan), snap)
    assert foreign_fp(env) == foreign
    assert out.receipt.receipt.outcomes["created_entities"] == len(graph_of(snap)["things"])
    if env.instance is None:
        return
    backend = TiledGraphBackend(TiledMutationClient.from_managed(env.instance.server))
    first = graph_of(snap)["things"][0]["id"]
    rec = backend.get_entity(env.ctx, plan.detail["graph_id"], first)
    assert rec["data"]["name"] == graph_of(snap)["things"][0]["name"]
    hood = backend.neighborhood(env.ctx, plan.detail["graph_id"], first, hops=1, max_nodes=50)
    assert hood["edges"]
    mine, _ = owned(env)
    assert len(mine) == len(graph_of(snap)["things"])


def test_stopped_real_server_is_unavailable_not_empty(tmp_path, clock, fingerprints, snap, tiled_base):
    from tiled_server import start_tiled_instance, tiled_python
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        client = CountingClient.from_managed(inst.server)
        settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=inst.url)
        plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
        approval = create_approval(local_operator_context(settings, request_id="s"), plan.change, settings,
                                   approval_id="a", clock=clock)
        before = tree_bytes(snap.parents[1])
        inst.stop()
        for call in (lambda: tj.plan_import(snap, client=client, settings=settings,
                                            supported_schema_fingerprints=fingerprints),
                     lambda: tj.apply_import(local_operator_context(settings, request_id="s"), plan, approval,
                                             source=snap, client=client, settings=settings,
                                             supported_schema_fingerprints=fingerprints,
                                             state_dir=tmp_path / "st", clock=clock)):
            with pytest.raises(ContractError) as err:
                call()
            assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
        assert tree_bytes(snap.parents[1]) == before and client.writes == []
    finally:
        inst.destroy()


# --------------------------------------------------------------------------- C1: writer 401/403


class _RejectingWriter(MemoryTiled):
    """Memory double whose writer credential is refused with ``status`` on the first write."""

    def __init__(self, status):
        super().__init__()
        self.status = status
        self.reject = False

    def create_entity(self, **kw):
        from app.modules.graph.backends.tiled_client import TiledCredentialRejected
        if self.reject:
            raise TiledCredentialRejected(f"Tiled graph rejected the writer credential (HTTP {self.status})",
                                          self.status)
        return super().create_entity(**kw)


@pytest.mark.parametrize("status", [401, 403])
def test_writer_rejected_mid_apply_is_writer_unauthorized(status, snap, clock, fingerprints, tmp_path):
    """C1: a writer 401/403 during apply is ``writer_unauthorized`` (not retryable), and no receipt is written."""
    client = _RejectingWriter(status)
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=client.base_url)
    ctx = local_operator_context(settings, request_id="w")
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    approval = create_approval(ctx, plan.change, settings, approval_id="a", clock=clock)
    client.reject = True
    with pytest.raises(ContractError) as err:
        tj.apply_import(ctx, plan, approval, source=snap, client=client, settings=settings,
                        supported_schema_fingerprints=fingerprints, state_dir=tmp_path / "st", clock=clock)
    assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and not err.value.retryable
    assert f"HTTP {status}" in str(err.value)
    assert client.writes == [] and not ReceiptStore(tmp_path / "st" / "receipts").all()
    failed = [json.loads(line) for p in (tmp_path / "st").rglob("journal.jsonl")
              for line in p.read_text(encoding="utf-8").splitlines() if '"run_failed"' in line]
    assert failed and all(r["code"] == "writer_unauthorized" for r in failed)


@pytest.mark.parametrize("status", [401, 403])
def test_writer_rejected_on_scan_is_writer_unauthorized_at_plan(status, snap, fingerprints):
    """C1: a 401/403 to the import client's read scan is the writer's, so planning reports it as such."""
    import requests

    class _Session:
        def post(self, *_a, **_kw):
            resp = requests.Response()
            resp.status_code, resp._content = status, b'{"detail": "Invalid API key"}'
            return resp

    client = TiledMutationClient("http://127.0.0.1:59999", profile="test", credential="bad", session=_Session())
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=client.base_url)
    with pytest.raises(ContractError) as err:
        tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and "bad" not in str(err.value)


def test_wrong_writer_key_on_real_server_is_writer_unauthorized(snap, fingerprints, tiled_base):
    """C1 on the disposable Tiled: a key it never issued fails planning with ``writer_unauthorized``."""
    from tiled_server import start_tiled_instance, tiled_python
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        client = CountingClient(inst.url, profile="test", credential="f2w-not-the-writer-key")
        settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=inst.url)
        with pytest.raises(ContractError) as err:
            tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
        assert err.value.code is ErrorCode.WRITER_UNAUTHORIZED and client.writes == []
    finally:
        inst.destroy()
