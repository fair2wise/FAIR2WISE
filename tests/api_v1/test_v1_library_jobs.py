"""T34: library kinds on the ``/v1`` surface: registered on ``:local``, absent on ALS."""
from __future__ import annotations

from app.modules.library import LIBRARY_KIND_CAPABILITIES, LIBRARY_PROBE_KIND


def _body(**params):
    return {"kind": LIBRARY_PROBE_KIND, "inputs": {"corpus_scope": ["library"], "params": params}}


def test_local_api_registers_library_kinds_and_a_nonce_rebuilds(local):
    assert all(local.ops.jobs.has_kind(kind) for kind in LIBRARY_KIND_CAPABILITIES)
    c = local.client()
    first = c.post("/v1/jobs", json=_body()).json()
    again = c.post("/v1/jobs", json=_body()).json()
    rebuilt = c.post("/v1/jobs", json=_body(nonce="2")).json()
    assert first["job_id"] == again["job_id"] != rebuilt["job_id"]
    local.work()
    assert c.get(f"/v1/jobs/{first['job_id']}").json()["state"] == "succeeded"


def test_api_refuses_secrets_in_library_params(local):
    resp = local.client().post("/v1/jobs", json=_body(api_key="x"))
    assert resp.status_code in (400, 422), resp.text


def test_als_api_registers_no_library_kind_and_refuses_submission(als):
    assert not any(als.ops.jobs.has_kind(kind) for kind in LIBRARY_KIND_CAPABILITIES)
    resp = als.client("admin").post("/v1/jobs", json=_body())
    assert resp.status_code in (400, 422), resp.text
