"""T18D (Q13, O-U3): ``GET /v1/capabilities`` (V60). Binary-case equivalent: TIL-22.

TIL-22 (backend half): the capability read says an ALS non-admin cannot edit or save (so the UI shows no Edit or
Save control), and the server still answers 403 with no effect to a forged Save. The UI half is described in the
T18D report (``ui/src`` is frozen; the change is applied after the other session commits).
"""
from __future__ import annotations

from _v1_support import ALS_TILED  # noqa: F401

from app.api.policies import API_ROUTE_POLICIES

FORGED = {"X-Local-Trusted": "1", "X-F2W-Is-Admin": "true", "X-F2W-Mode": "local_trusted",
          "X-Tiled-Roles": "admin", "X-Forwarded-User": "admin", "Authorization": "Bearer forged-admin"}


def test_the_policy_row_exists_and_needs_a_signed_in_reader():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V60")
    assert (row.method, row.path, row.capabilities) == ("GET", "/v1/capabilities", frozenset({"chat.read_support"}))


def test_til22_local_operator_can_edit_and_save(local):
    body = local.client().get("/v1/capabilities").json()
    assert body == {"role": "local_operator", "profile": "local", "can_edit": True, "can_save": True,
                    "can_upload": True, "can_run_operations": True, "can_approve": True}


def test_til22_als_admin_can_edit_and_save(als):
    body = als.client("admin").get("/v1/capabilities").json()
    assert body["role"] == "admin" and body["can_edit"] and body["can_save"] and body["can_upload"]


def test_til22_als_non_admin_sees_no_edit_or_save_and_the_server_still_refuses(als):
    user = als.client("user")
    body = user.get("/v1/capabilities").json()
    assert body == {"role": "user", "profile": "als-dev", "can_edit": False, "can_save": False, "can_upload": False,
                    "can_run_operations": True,  # T19E: every signed-in user holds chat.ask (V25-V29 reads)
                    "can_approve": False}  # Pending Approvals (2026-11-09): graph.publish and a verified admin
    # The capability read never leaks more than the booleans and the role.
    assert set(body) == {"role", "profile", "can_edit", "can_save", "can_upload", "can_run_operations",
                        "can_approve"}
    # Forged headers do not change the answer.
    forged = als.client("user", headers=FORGED).get("/v1/capabilities").json()
    assert forged == body
    # The 403 is the backstop: the same caller's forged Save is refused.
    resp = user.patch("/graph/node/matkg:TermA", json={"label": "forged"})
    assert resp.status_code == 403 and resp.json()["code"] == "denied"


def test_can_run_operations_follows_chat_ask_exactly():
    """T19E frontend closure: the Operations tab's gate is the entry capability of the operation reads."""
    from app.api.routes_capabilities import capabilities_for_caller
    from tests.runtime.runtime_fakes import als_ctx

    assert capabilities_for_caller(als_ctx("u", capabilities=frozenset({"chat.ask"})))["can_run_operations"] is True
    assert capabilities_for_caller(als_ctx("u", capabilities=frozenset({"chat.read_support"})))[
        "can_run_operations"] is False


def test_til22_anonymous_als_caller_gets_401_not_a_capability_answer(als):
    assert als.client("anon").get("/v1/capabilities").status_code == 401


def test_can_save_is_false_in_read_only_json_mode(local):
    c = local.client()
    from _v1_support import H
    assert c.put("/settings", json={"graph_source": "json", "json_graph_path": f"storage/kg/{H.GRAPH_FILENAME}",
                                    "json_graph_paths": [f"storage/kg/{H.GRAPH_FILENAME}"]}).status_code == 200
    body = c.get("/v1/capabilities").json()
    assert body["can_edit"] is True and body["can_save"] is False, "the PATCH would answer 400 in JSON mode"
