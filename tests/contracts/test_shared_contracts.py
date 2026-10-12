"""Contract tests for app.modules.contracts (task T01).

Covers import isolation, profile/approval rules, tool result states, evidence
rules, the in-memory fakes, and signature parity of the fake chat clients with
the existing F2W client classes (checked via AST, without importing them).
"""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.contracts import (
    ApprovalRecord,
    AuthorizationMode,
    CapabilityError,
    ChangePlan,
    ContractError,
    DeploymentProfile,
    DocumentStore,
    Effect,
    ErrorCode,
    EvidenceRef,
    GraphCapabilities,
    GraphPort,
    MatchType,
    Principal,
    RequestContext,
    SourceLocator,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from app.modules.contracts.testing import (
    FakeAsyncChatClient,
    FakeSyncChatClient,
    InMemoryDocumentStore,
    InMemoryGraphPort,
)

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
H = "a" * 64


def local_ctx(**kw):
    return RequestContext(
        profile=DeploymentProfile.LOCAL,
        mode=AuthorizationMode.LOCAL_TRUSTED,
        principal=Principal(subject="local-operator"),
        request_id="r1",
        **kw,
    )


def als_ctx(is_admin=True, **kw):
    return RequestContext(
        profile=DeploymentProfile.ALS_DEV,
        mode=AuthorizationMode.TILED_AUTHENTICATED,
        principal=Principal(subject="alice", issuer="https://tiled-staging.als.lbl.gov/", is_admin=is_admin,
                            verified_at=NOW),
        request_id="r2",
        **kw,
    )


def plan(**kw):
    base = dict(plan_id="p1", snapshot_id="s1", input_sha256=H, target="tiled-local", corpus_scope=("rsoxs",),
                base_revision="rev0", creates=2, links=1, supersessions=0, affected_ids=("e1", "e2"),
                validation_report_sha256=H, plan_sha256="b" * 64)
    base.update(kw)
    return ChangePlan(**base)


def local_approval(**kw):
    base = dict(approval_id="a1", profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                plan_id="p1", plan_sha256="b" * 64, target="tiled-local", scope=("rsoxs",),
                requested_by="local-operator", approved_by="local-operator", executed_as="f2w-writer",
                approved_at=NOW)
    base.update(kw)
    return ApprovalRecord(**base)


# --- import isolation -------------------------------------------------------

def test_import_has_no_side_effects():
    """Importing the contracts loads no other app modules, heavy deps, or env changes."""
    code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "env = dict(os.environ); before = set(sys.modules)\n"
        "import app.modules.contracts, app.modules.contracts.testing\n"
        "new = sorted(set(sys.modules) - before)\n"
        "print(json.dumps({'new': new, 'env_changed': dict(os.environ) != env}))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd="/")
    result = json.loads(out.stdout.strip().splitlines()[-1])
    app_mods = [m for m in result["new"] if m.startswith("app")]
    assert all(m in {"app", "app.modules"} or m.startswith("app.modules.contracts") for m in app_mods), app_mods
    heavy = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langgraph", "tiled", "dotenv",
             "academy"}
    assert not heavy & {m.split(".")[0] for m in result["new"]}
    assert result["env_changed"] is False


def test_app_never_imports_forge():
    """F2W must not import FORGE (plan section 4.1)."""
    offenders = []
    for path in (REPO / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(errors="ignore"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            offenders += [f"{path.relative_to(REPO)}:{node.lineno}" for n in names
                          if n.split(".")[0] in {"forge", "als_knowledge_agent", "reduction_api", "tiled_agentic"}]
    assert offenders == []


# --- request context ----------------------------------------------------------

def test_local_context_is_local_operator_without_identity():
    ctx = local_ctx(capabilities=frozenset({"graph.edit"}))
    assert ctx.has("graph.edit") and ctx.principal.subject == "local-operator"
    with pytest.raises(ValidationError):
        RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                       principal=Principal(subject="local-operator", issuer="x", is_admin=True, verified_at=NOW),
                       request_id="r")


@pytest.mark.parametrize("profile", [DeploymentProfile.ALS_DEV, DeploymentProfile.ALS_PROD])
def test_als_profiles_reject_local_trusted(profile):
    with pytest.raises(ValidationError):
        RequestContext(profile=profile, mode=AuthorizationMode.LOCAL_TRUSTED,
                       principal=Principal(subject="local-operator"), request_id="r")


def test_als_context_requires_verified_principal():
    assert als_ctx().principal.is_admin is True
    with pytest.raises(ValidationError):
        RequestContext(profile=DeploymentProfile.ALS_PROD, mode=AuthorizationMode.TILED_AUTHENTICATED,
                       principal=Principal(subject="bob"), request_id="r")
    with pytest.raises(ValidationError):
        RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.TILED_AUTHENTICATED,
                       principal=Principal(subject="bob", issuer="i", is_admin=False, verified_at=NOW),
                       request_id="r")


def test_context_is_immutable():
    ctx = local_ctx()
    with pytest.raises(ValidationError):
        ctx.mode = AuthorizationMode.TILED_AUTHENTICATED


# --- approvals ----------------------------------------------------------------

def test_local_approval_rules_and_binding():
    approval = local_approval(expires_at=NOW + timedelta(hours=1))
    assert approval.authorizes(plan(), NOW)
    assert not approval.authorizes(plan(plan_sha256="c" * 64), NOW)       # different plan bytes
    assert not approval.authorizes(plan(target="tiled-als-prod"), NOW)    # different target
    assert not approval.authorizes(plan(corpus_scope=("other",)), NOW)    # outside scope
    assert not approval.authorizes(plan(), NOW + timedelta(hours=2))      # expired
    with pytest.raises(ValidationError):
        local_approval(approver_issuer="https://tiled.als.lbl.gov/")
    with pytest.raises(ValidationError):
        local_approval(executed_as="local-operator")


def test_als_approval_requires_verified_admin_evidence():
    ok = local_approval(profile=DeploymentProfile.ALS_PROD, mode=AuthorizationMode.TILED_AUTHENTICATED,
                        approved_by="alice", approver_issuer="https://tiled.als.lbl.gov/", role_checked_at=NOW)
    assert ok.approved_by == "alice"
    with pytest.raises(ValidationError):
        local_approval(profile=DeploymentProfile.ALS_PROD, mode=AuthorizationMode.TILED_AUTHENTICATED,
                       approved_by="alice")
    with pytest.raises(ValidationError):
        local_approval(profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.LOCAL_TRUSTED)


# --- tools ----------------------------------------------------------------------

def test_toolspec_guards_write_and_destructive_effects():
    common = dict(version="1.0.0", domain="graph", description="d", when_to_use="w", when_not_to_use="n",
                  input_schema={}, output_schema={})
    ToolSpec(name="kg_find_entities", **common)
    with pytest.raises(ValidationError):
        ToolSpec(name="kg_apply", effects=frozenset({Effect.WRITE_GRAPH}), **common)
    with pytest.raises(ValidationError):
        ToolSpec(name="kg_wipe", effects=frozenset({Effect.DESTRUCTIVE}), required_capabilities=frozenset({"x"}),
                 **common)
    with pytest.raises(ValidationError):
        ToolSpec(name="Bad-Name", **common)


def test_tool_result_distinguishes_empty_from_unavailable():
    assert ToolResult(tool="t", status=ToolStatus.EMPTY).error_code is None
    unavailable = ToolResult(tool="t", status=ToolStatus.UNAVAILABLE, error_code=ErrorCode.UNAVAILABLE,
                             retryable=True)
    assert unavailable.status is not ToolStatus.EMPTY
    with pytest.raises(ValidationError):
        ToolResult(tool="t", status=ToolStatus.UNAVAILABLE)                       # missing code
    with pytest.raises(ValidationError):
        ToolResult(tool="t", status=ToolStatus.DENIED, error_code=ErrorCode.UNAVAILABLE)
    with pytest.raises(ValidationError):
        ToolResult(tool="t", status=ToolStatus.OK, error_code=ErrorCode.INTERNAL)
    with pytest.raises(ValidationError):
        ToolResult(tool="t", status=ToolStatus.EMPTY, data={"hits": [1]})


# --- evidence -------------------------------------------------------------------

def test_evidence_rules():
    loc = SourceLocator(page=3, char_span=(10, 40))
    ref = EvidenceRef(evidence_id="ev1", work_id="w1", source_version="v1", locator=loc,
                      match_type=MatchType.EXACT, quote="q")
    assert ToolResult(tool="t", status=ToolStatus.OK, data={"a": 1}, evidence=(ref,)).evidence_ids == ["ev1"]
    with pytest.raises(ValidationError):
        EvidenceRef(evidence_id="e", work_id="w", source_version="v", locator=loc, match_type=MatchType.EXACT)
    with pytest.raises(ValidationError):
        SourceLocator(bbox=(0, 0, 1, 1))
    with pytest.raises(ValidationError):
        SourceLocator(char_span=(5, 1))


# --- fakes and ports ----------------------------------------------------------

def test_fakes_satisfy_ports():
    assert isinstance(InMemoryGraphPort(), GraphPort)
    assert isinstance(InMemoryDocumentStore(), DocumentStore)


def test_graph_port_is_scoped_and_reports_completeness():
    port = InMemoryGraphPort({"rsoxs": {"n1": {"label": "RSoXS", "links": ["n2"]}, "n2": {"label": "PEDOT"}},
                              "other": {"n1": {"label": "RSoXS"}}})
    ctx = local_ctx(corpus_scope=frozenset({"rsoxs"}))
    hits, complete = port.find_entities(ctx, "rsoxs", "rsoxs", limit=10)
    assert [h["graph_id"] for h in hits] == ["rsoxs"] and complete
    assert port.neighborhood(ctx, "rsoxs", "n1", hops=1, max_nodes=1)["truncated"] is True
    with pytest.raises(ContractError) as exc:
        port.get_entity(ctx, "other", "n1")
    assert exc.value.code is ErrorCode.DENIED


def test_unsupported_write_raises_capability_error():
    port = InMemoryGraphPort()
    with pytest.raises(CapabilityError) as exc:
        port.apply_plan(local_ctx(), plan(), local_approval())
    assert exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY and exc.value.retryable is False


def test_apply_plan_rejects_stale_approval():
    port = InMemoryGraphPort(capabilities=GraphCapabilities(backend="memory", batch_writes=True))
    receipt = port.apply_plan(local_ctx(), plan(), local_approval())
    assert receipt.verified and receipt.executed_as == "f2w-writer"
    with pytest.raises(ContractError) as exc:
        port.apply_plan(local_ctx(), plan(plan_sha256="c" * 64), local_approval())
    assert exc.value.code is ErrorCode.STALE_APPROVAL


def test_document_store_is_content_addressed():
    store, ctx = InMemoryDocumentStore(), local_ctx()
    ref = store.put(ctx, b"%PDF-1.7", media_type="application/pdf", metadata={})
    assert ref.startswith("sha256:") and store.get(ctx, ref) == b"%PDF-1.7"
    assert store.put(ctx, b"%PDF-1.7", media_type="application/pdf", metadata={}) == ref
    with pytest.raises(ContractError):
        store.get(ctx, "sha256:" + "0" * 64)


# --- fake chat clients match existing F2W client signatures --------------------

def _method_args(path: Path, cls: str, method: str):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    fn = next(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method)
    a = fn.args
    return ([x.arg for x in a.args], [x.arg for x in a.kwonlyargs],
            [ast.unparse(d) for d in a.kw_defaults], isinstance(fn, ast.AsyncFunctionDef))


def test_fake_clients_match_existing_signatures():
    sync_real = _method_args(REPO / "app/modules/term_extractor/clients.py", "ChatClient", "chat")
    sync_fake = _method_args(REPO / "app/modules/contracts/testing.py", "FakeSyncChatClient", "chat")
    assert sync_fake == sync_real
    async_real = _method_args(REPO / "app/modules/kg_rag_api.py", "ChatClient", "chat")
    async_fake = _method_args(REPO / "app/modules/contracts/testing.py", "FakeAsyncChatClient", "chat")
    assert async_fake == async_real


def test_fake_clients_are_deterministic():
    sync = FakeSyncChatClient(["first"], default="again")
    assert [sync.chat("p"), sync.chat("p", trace_label="x")] == ["first", "again"]
    assert sync.calls[1]["trace_label"] == "x"
    fake = FakeAsyncChatClient(['{"sufficient": false}'])
    assert json.loads(asyncio.run(fake.chat([{"role": "user", "content": "q"}]))) == {"sufficient": False}
