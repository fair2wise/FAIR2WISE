"""T10B routes V53-V55 over a deployment whose publication has no replacement importer (the T10 fake step).

The behaviour with the real importer is covered in ``tests/jobs/test_replacement_publication.py``; here the
routes, their policies and the refusals when the importer is missing.
"""
from app.api.policies import API_ROUTE_POLICIES


def test_routes_exist_with_publish_capability_and_refuse_without_a_replacement_importer(local):
    rows = {p.path: p for p in API_ROUTE_POLICIES if p.route_id in ("V53", "V54", "V55")}
    assert set(rows) == {"/v1/publications/rollback-replacement", "/v1/publications/corpus-retire",
                         "/v1/publications/corpus-purge"}
    assert all(r.capabilities == frozenset({"graph.publish"}) for r in rows.values())
    c = local.client()
    resp = c.post("/v1/publications/rollback-replacement", json={"target": local.target, "reason": "x"})
    assert resp.status_code == 409 and resp.json()["code"] == "conflict"
    resp = c.post("/v1/publications/corpus-retire",
                  json={"scope": "rsoxs", "target": local.target, "confirm_scope": "rsoxs"})
    assert resp.status_code != 200 and resp.json()["code"] == "unsupported_capability"
    resp = c.post("/v1/publications/corpus-purge", json={"scope": "rsoxs", "target": local.target})
    assert resp.status_code != 200 and resp.json()["code"] == "unsupported_capability"
    assert local.ops.jobs.store.list_jobs() == []


def test_v20_pointer_only_rollback_answers_409_with_a_stable_reason_on_a_replacement_deployment(local):
    """Owner decision 2026-11-03: V20 returns 409 there and points callers to rollback_replacement (V53)."""
    c = local.client()
    body = {"target": local.target, "reason": "x"}
    before = c.post("/v1/publications/rollback", json=body)
    assert before.json().get("reason") is None  # without a marker the old behavior and envelope are unchanged
    local.ops._publication().replacement = True  # the deployment keeps a release marker
    resp = c.post("/v1/publications/rollback", json=body)
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict" and resp.json()["reason"] == "rollback_replacement_required"
    assert resp.json()["retryable"] is False
    assert "rollback_replacement" in resp.json()["message"] and "/v1/publications/rollback-replacement" in resp.json()["message"]


def test_the_rehearsal_gate_applies_to_the_new_routes(local):
    other = "http://127.0.0.1:59871/api#other"
    c = local.client()
    for path, body in (("/v1/publications/rollback-replacement", {"target": other, "reason": "x"}),
                       ("/v1/publications/corpus-retire", {"scope": "rsoxs", "target": other, "confirm_scope": "rsoxs"}),
                       ("/v1/publications/corpus-purge", {"scope": "rsoxs", "target": other})):
        resp = c.post(path, json=body)
        assert resp.status_code != 200 and resp.json()["code"] == "unsupported_capability"
