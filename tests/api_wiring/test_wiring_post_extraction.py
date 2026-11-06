"""T18E / UI12 Tiled stage: Run extraction through the real legacy app publishes to Tiled, no Splash wipe.

Offline equivalent of the T18C ``UI12`` browser stage (``tests/e2e/live_backend/full/pending.full.ts``, "UI12
accept: ... Run extraction rebuilds once"): the same two chat actions (download yes, extraction yes) through the
real ``/chat/action/stream`` route, on the T18A harness (fake models, providers and Splash; sockets blocked), with
the real ``kg_update.publish_extraction`` and a ``PostExtractionPublisher`` over the in-memory Tiled double and the
real publication job. The SSE phase names are the frozen UI's (``splash_reimport_started`` /
``splash_reimport_result``). The Playwright run itself needs a browser and is not run here; the disposable-Tiled
server variant of the publication is covered by ``tests/graph_import`` and ``tests/contracts/legacy_ui/closure``.

Acceptance covered: the phases are kept; a safe plan on ``:local`` is published with an ``auto_safe_plan``
approval by the local operator; an unsafe plan waits for review and writes nothing; no Splash call and no delete.
"""
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "graph_import", REPO / "tests" / "security", REPO / "tests" / "fixtures",
              REPO / "tests" / "contracts" / "legacy_ui", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402

from app.api.operations import F2WOperations, PublicationGate  # noqa: E402
from app.api.post_extraction import AUTO_SAFE_PLAN, PostExtractionPublisher  # noqa: E402
from app.modules.graph import kg_update  # noqa: E402
from app.modules.graph.imports import tiled_json as tj  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from tests.jobs.replacement_world import SCOPE, RWorld, build_v1, derive, graph_of  # noqa: E402

REAL_PUBLISH = kg_update.publish_extraction


@pytest.fixture()
def pub_world(monkeypatch, tmp_path):
    """The legacy app over the harness, with the real publisher over a seeded Tiled double (v1 live, v2 frozen)."""
    from app.modules.f2w_agent import api as api_mod

    registry = SchemaRegistry.discover()
    v1 = build_v1(tmp_path / "pub", registry)
    derive(v1, "v2", revise=2, add=1)
    world = RWorld(tmp_path / "pub", v1, tj.default_supported_schema_fingerprints(registry))
    world.publish("v1")
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.setattr(kg_update, "publish_extraction", REAL_PUBLISH)  # the harness fakes it; this test needs it
    ops = F2WOperations(world.settings, world.service, publication=world.pub, clock=world.step_clock,
                        publication_gate=PublicationGate(frozenset({world.target})))
    publisher = PostExtractionPublisher(ops, freeze=lambda _terms: f"{SCOPE}/v2", is_safe=world.step.plan_is_safe,
                                        target=world.target)
    cfg = make_cfg(ctx)
    cfg.extraction_publisher = publisher
    client = TestClient(api_mod.create_app(cfg), raise_server_exceptions=False)
    world.legacy, world.harness, world.ops = client, ctx, ops
    return world


def _run_extraction(w, sid):
    c = w.legacy
    first = c.post("/chat", json={"message": "Explain the insufficient synthetic topic", "session_id": sid,
                                  "client_turn_id": f"{sid}-1"})
    assert first.json()["status"] == "awaiting_download_decision", first.text
    dl = {"decision": "yes", "kind": "download", "candidate_index": 0, "session_id": sid,
          "client_turn_id": f"{sid}-2", "parent_turn_id": f"{sid}-1"}
    assert c.post("/chat/action", json=dl).json()["status"] == "awaiting_extraction_decision"
    ex = {"decision": "yes", "kind": "extraction", "session_id": sid, "client_turn_id": f"{sid}-3"}
    return H.parse_sse(c.post("/chat/action/stream", json=ex).content)


def _phases(events):
    return [e["data"]["phase"] for e in events if e["event"] == "progress"]


def test_ui12_tiled_stage_run_extraction_publishes_v2_with_the_frozen_phase_names(pub_world):
    w = pub_world
    foreign, writes_before = w.foreign(), len(w.client.writes)
    events = _run_extraction(w, "t18e-safe")
    phases = _phases(events)
    for name in ("extraction_started", "extraction_result", "kg_rebuild_started", "kg_rebuild_result",
                 "splash_reimport_started", "splash_reimport_result", "reload_started", "reload_result"):
        assert name in phases, (name, phases)
    assert phases.index("splash_reimport_started") < phases.index("splash_reimport_result")
    result = next(e["data"] for e in events if e["event"] == "progress" and e["data"]["phase"] == "splash_reimport_result")
    assert result["status"] == "success" and result["message"] == "Splash graph store refreshed"  # frozen wording
    assert result["publish_state"] == "tiled_published"
    assert events[-1]["event"] == "complete"
    # Published to Tiled under the :local auto-approval policy.
    assert w.pubstore.pointer(w.target).snapshot_ref == f"{SCOPE}/v2"
    assert w.agree()["marker_snapshot_id"] == "v2"
    job = next(j for j in w.service.store.list_jobs() if j.kind == "publish" and j.inputs.params["snapshot_ref"].endswith("v2"))
    approval = w.pubstore.get_approval(_approval_ids(w, job.job_id)[0])
    assert approval.reason == AUTO_SAFE_PLAN and approval.approved_by == "local-operator"
    # No Splash wipe, no delete outside the plan, foreign records untouched.
    assert not any(k.startswith("splash.") and "delete" in k for k in w.harness.calls), w.harness.calls
    assert "deleteEntity" not in w.client.writes[writes_before:]
    assert w.foreign() == foreign


def _approval_ids(w, job_id):
    return [r["approval_id"] for r in w.pubstore.db.query("SELECT approval_id FROM pub_approvals WHERE job_id=?", (job_id,))]


def test_ui12_unsafe_plan_on_local_stops_the_phase_at_review_and_writes_nothing(pub_world):
    w = pub_world
    revised = graph_of(w.v1)["things"][0]["id"]
    forge = next(e["id"] for e in w.client.entities.values() if (e.get("properties") or {}).get("owner") == "forge")
    lid = str(uuid.uuid4())
    w.client.links[lid] = {"id": lid, "subjectId": forge, "objectId": w.live()[revised], "predicate": "forge:uses",
                           "properties": None, "accessBlob": None}
    w.client.writes.clear()
    events = _run_extraction(w, "t18e-unsafe")
    result = next(e["data"] for e in events if e["event"] == "progress" and e["data"]["phase"] == "splash_reimport_result")
    assert result["status"] == "awaiting_review" and "awaiting review" in result["message"]
    assert events[-1]["event"] == "complete", "the chat answer is not blocked by a plan that waits for review"
    assert w.pubstore.pointer(w.target).snapshot_ref == f"{SCOPE}/v1"
    assert w.client.writes == []


def test_without_a_publisher_the_phases_run_and_report_skipped(monkeypatch, tmp_path):
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.setattr(kg_update, "publish_extraction", REAL_PUBLISH)
    client = TestClient(api_mod.create_app(make_cfg(ctx)), raise_server_exceptions=False)

    class W:  # the minimum _run_extraction needs
        legacy = client
    events = _run_extraction(W, "t18e-none")
    result = next(e["data"] for e in events if e["event"] == "progress" and e["data"]["phase"] == "splash_reimport_result")
    assert result["status"] == "skipped" and events[-1]["event"] == "complete"  # legacy field kept
    assert result["publish_state"] == "json_available_tiled_unavailable"
    assert result["message"].startswith("JSON available, Tiled unavailable")
    assert not any("delete" in k for k in ctx.calls)
