"""T25B: shared effect guard and the per-route policy (access_contract.md sections 6-7)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.modules.contracts.context import ADMIN_CAPABILITIES, CAPABILITIES, CHAT_CAPABILITIES
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.tools import ToolSpec
from app.modules.security.authorization import (
    AGENT_SURFACE,
    ROUTE_CAPABILITIES,
    ROUTE_POLICIES,
    chat_action_capabilities,
    chat_offers_ingestion,
    graph_node_read_capabilities,
    health_projection,
    require,
    require_any,
    require_owner_or,
    requires,
    resolve_context,
    route_capabilities,
    settings_update_capabilities,
)
from security_fakes import (
    FakeClock,
    FakeTiledIdentityPort,
    admin_user,
    local_settings,
    plain_user,
    staging_settings,
)

REPO = Path(__file__).resolve().parents[2]
ROUTES = json.loads((REPO / "docs/migration/companion/f2w_http_routes.json").read_text(encoding="utf-8"))


def _ctx(kind):
    if kind == "local":
        return resolve_context(local_settings(), None, request_id="r", clock=FakeClock())
    port = FakeTiledIdentityPort(tokens={"a": admin_user(), "u": plain_user()})
    return resolve_context(staging_settings(), kind, request_id="r", identity_port=port, clock=FakeClock())


# --- require / requires ----------------------------------------------------------

def test_require_denies_before_effect():
    effects = []
    user = _ctx("u")
    with pytest.raises(ContractError) as info:
        require(user, "graph.edit")
        effects.append("write")
    assert info.value.code is ErrorCode.DENIED and effects == []
    assert require(_ctx("a"), "graph.edit", "graph.upload").principal.is_admin
    assert require(_ctx("local"), *CAPABILITIES)


def test_require_without_context_is_denied_and_unknown_names_are_errors():
    with pytest.raises(ContractError):
        require(None, "chat.ask")
    with pytest.raises(ContractError):
        require({"capabilities": ["graph.edit"], "is_admin": True}, "graph.edit")  # forged dict
    with pytest.raises(ValueError):
        require(_ctx("local"), "graph.everything")
    with pytest.raises(ValueError):
        requires("not.a.capability")


def test_require_any():
    assert require_any(_ctx("u"), "graph.edit", "chat.ask")
    with pytest.raises(ContractError):
        require_any(_ctx("u"), "graph.edit", "graph.upload")


def test_requires_decorator_guards_services_tools_and_async_workers():
    """AUTH-03/AUTH-04/LOCAL-04: one guard for SDK, Tools and workers."""
    effects = []

    @requires("graph.edit")
    def edit_node(ctx, node_id):
        effects.append(("edit", node_id))
        return "ok"

    @requires("ingest.acquire", ctx_arg="context")
    async def acquire(paper, *, context):
        effects.append(("acquire", paper))
        return "ok"

    assert edit_node.required_capabilities == frozenset({"graph.edit"})
    with pytest.raises(ContractError):
        edit_node(_ctx("u"), "n1")
    with pytest.raises(ContractError):
        asyncio.run(acquire("p1", context=_ctx("u")))
    with pytest.raises(ContractError):
        edit_node("n1")  # no context at all
    assert effects == []
    assert edit_node(_ctx("a"), "n1") == "ok" and asyncio.run(acquire("p1", context=_ctx("local"))) == "ok"
    assert effects == [("edit", "n1"), ("acquire", "p1")]
    # The decorator's capability set plugs into the T01 ToolSpec field.
    spec = ToolSpec(name="edit_node", version="1.0.0", domain="graph", description="d", when_to_use="w",
                    when_not_to_use="n", input_schema={}, output_schema={},
                    required_capabilities=edit_node.required_capabilities)
    assert set(spec.required_capabilities) <= CAPABILITIES


# --- route table -------------------------------------------------------------------

def test_route_table_covers_every_inventoried_route_exactly():
    """DEP-19 / AUTH-04: every route in f2w_http_routes.json has a declared policy, and nothing else."""
    inventoried = {(r["file"], r["method"], r["path"]) for r in ROUTES}
    assert len(ROUTES) == 34 == len(inventoried) == len(ROUTE_POLICIES)
    assert set(ROUTE_CAPABILITIES) == inventoried
    assert sorted(p.route_id for p in ROUTE_POLICIES) == [f"R{i:02d}" for i in range(1, 35)]


def test_route_policy_matches_contract_section_7():
    def caps(path, method="GET"):
        return route_capabilities(method, path).capabilities

    assert route_capabilities("GET", "/health").public
    assert [p for p in ROUTE_POLICIES if p.public] == [route_capabilities("GET", "/health")]
    assert caps("/settings", "PUT") == {"settings.personal"}
    assert caps("/graph/node/{node_id}", "PATCH") == {"graph.edit"}
    assert caps("/graph/upload", "POST") == {"graph.upload"}
    assert caps("/chat", "POST") == caps("/chat/stream", "POST") == {"chat.ask"}
    assert caps("/chat/action", "POST") == caps("/chat/action/stream", "POST") == {"chat.decline_pending"}
    assert caps("/traces/{turn_id}") == {"trace.read_own"}
    for path in ("/graph", "/graph/neighborhood/{node_id:path}", "/graph/node/{node_id}"):
        assert caps(path) == {"chat.read_support"}
    # Ordinary users can enter exactly the chat-group routes on the agent surface.
    agent = [p for p in ROUTE_POLICIES if p.surface == AGENT_SURFACE]
    user_routes = {p.route_id for p in agent if p.capabilities <= CHAT_CAPABILITIES}
    assert {p.route_id for p in agent} - user_routes == {"R10", "R11"}
    assert all(p.needs_fresh_role_check == bool(p.capabilities & ADMIN_CAPABILITIES) for p in ROUTE_POLICIES)


def test_route_exposure_by_profile():
    local, als = local_settings(), staging_settings()
    by_id = {p.route_id: p for p in ROUTE_POLICIES}
    for rid in [f"R{i:02d}" for i in range(26, 32)]:
        assert not by_id[rid].exposed_on(local) and not by_id[rid].exposed_on(als)
    for rid in ("R20", "R21", "R32", "R33", "R34"):
        assert by_id[rid].exposed_on(local) and not by_id[rid].exposed_on(als)
    assert route_capabilities("GET", "/not/declared") is None


def test_conditional_route_rules():
    assert settings_update_capabilities({"model", "backend"}) == (frozenset({"settings.personal"}), frozenset())
    caps, ignored = settings_update_capabilities({"model", "tiled_uri"})
    assert caps == {"settings.personal"} and ignored == {"tiled_uri"}
    for field in ("json_graph_paths", "source_rag", "use_live_tiled", "kg_query_hops", "something_new"):
        assert "settings.global" in settings_update_capabilities({field})[0]
    assert chat_action_capabilities("no", "download") == {"chat.decline_pending"}
    assert chat_action_capabilities("yes", "download") == {"ingest.acquire"}
    assert chat_action_capabilities("yes", "extraction") == {"ingest.extract"}
    assert chat_action_capabilities("yes", None) == {"ingest.acquire", "ingest.extract"}
    with pytest.raises(ContractError):
        chat_action_capabilities("maybe", None)
    assert graph_node_read_capabilities("storage/kg/x.json") == {"chat.read_support"}  # O-A5
    assert graph_node_read_capabilities(None) == {"chat.read_support"}


def test_user_chat_action_yes_denied_before_job_creation():
    """AUTH-03: non-admin yes is refused before job creation/download/extraction."""
    jobs = []
    user = _ctx("u")
    require(user, *chat_action_capabilities("no", "download"))
    with pytest.raises(ContractError):
        require(user, *chat_action_capabilities("yes", "download"))
        jobs.append("job")
    assert jobs == [] and not chat_offers_ingestion(user)
    assert chat_offers_ingestion(_ctx("a")) and chat_offers_ingestion(_ctx("local"))


def test_owner_rules_for_sessions_and_traces():
    user, admin, local = _ctx("u"), _ctx("a"), _ctx("local")
    require_owner_or(user, user.principal.subject, "trace.read_own", "trace.read_any")
    with pytest.raises(ContractError):
        require_owner_or(user, "uuid-someone-else", "trace.read_own", "trace.read_any")
    with pytest.raises(ContractError):
        require_owner_or(user, None, "session.own", "session.any")
    require_owner_or(admin, "uuid-someone-else", "session.own", "session.any")
    require_owner_or(local, None, "session.own", "session.any")


def test_health_projection():
    assert health_projection(None) == "reduced"
    assert health_projection(_ctx("u")) == "reduced"
    assert health_projection(_ctx("a")) == "full" == health_projection(_ctx("local"))


def test_tiled_uri_editable_on_local_only():
    """Owner decision 2026-10-12: tiled_uri is a global setting on :local, ignored on ALS."""
    caps, ignored = settings_update_capabilities({"tiled_uri"}, local=True)
    assert ignored == frozenset() and "settings.global" in caps
    caps, ignored = settings_update_capabilities({"tiled_uri"})
    assert ignored == frozenset({"tiled_uri"}) and caps == frozenset({"settings.personal"})
