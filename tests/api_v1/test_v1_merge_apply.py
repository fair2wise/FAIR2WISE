"""T07B V81: POST /v1/curation/merges/apply: capability, dry run, closed gate, not wired, stale plan hash."""

from __future__ import annotations

from app.api.policies import API_ROUTE_POLICIES
from app.modules.canonicalization.apply_adapters import MergeApplyService
from tests.canonicalization.canon_helpers import NOW, TARGET, vocab
from tests.canonicalization.test_canon_apply_adapters import CORPUS, FakePort, accept, resolved, term_case

URL = "/v1/curation/merges/apply"


def _wire(local):
    store, port = local.ops.curation, FakePort()
    v = vocab()
    local.ops.merge_apply = MergeApplyService(cases=store, vocabulary=lambda: v, port=lambda: port, target=TARGET,
                                              clock=lambda: NOW)
    return store, port


def test_policy_row_is_graph_publish():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V81")
    assert (row.method, row.path, row.capabilities) == ("POST", URL, frozenset({"graph.publish"}))


def test_not_wired_is_the_typed_501(local):
    r = local.client().post(URL, json={"corpus": CORPUS})
    assert r.status_code == 501 and r.json()["code"] == "unsupported_capability"


def test_dry_run_reports_and_real_apply_is_gate_closed(local):
    store, port = _wire(local)
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    c = local.client()
    r = c.post(URL, json={"corpus": CORPUS})
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["dry_run"] is True and rep["gate_open"] is False and rep["model_calls"] == 0
    assert rep["false_merges"] == 0 and rep["false_splits"] == 0 and rep["plan_sha256"]
    bad = c.post(URL, json={"corpus": CORPUS, "dry_run": False, "plan_sha256": rep["plan_sha256"]})
    assert bad.status_code == 501 and bad.json()["reason"] == "gate_closed" and port.saves == []
    assert c.post(URL, json={"corpus": CORPUS, "route": "nope"}).status_code == 422
    assert c.post(URL, json={"corpus": CORPUS, "plan_sha256": "xyz"}).status_code == 422
