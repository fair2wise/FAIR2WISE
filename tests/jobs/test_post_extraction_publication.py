"""T18E: post-extraction publication replaces the Splash wipe-and-reimport (owner decision Q11).

Binary-case equivalent of TIL-20 (the companion case inventory is frozen): ``test_til20_*`` below. Acceptance:

* ``:local`` auto-approves a safe plan, recorded as the local operator with ``reason = auto_safe_plan``;
* an unsafe plan on ``:local`` waits for review (and is published only after a person approves it);
* ALS never auto-approves, even for a plan the importer calls safe;
* no delete outside the plan: the publication never calls ``deleteEntity`` and foreign records stay byte-identical.
"""
import uuid

import pytest

from app.api.operations import F2WOperations, PublicationGate
from app.api.post_extraction import (
    AUTO_SAFE_PLAN, PostExtractionPublisher, decide_approval, snapshot_freezer)
from app.modules.contracts.changes import ApprovalRecord
from app.modules.graph.imports import tiled_json as tj
from app.modules.schema.registry import SchemaRegistry
from tests.jobs.replacement_world import SCOPE, RWorld, build_v1, derive, graph_of
from tests.snapshots.snapshot_fakes import INPUTS


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


@pytest.fixture(scope="module")
def fingerprints(registry):
    return tj.default_supported_schema_fingerprints(registry)


@pytest.fixture()
def world(tmp_path, registry, fingerprints):
    v1 = build_v1(tmp_path, registry)
    derive(v1, "v2", revise=2, add=1)
    return RWorld(tmp_path, v1, fingerprints)


def _publisher(world, *, freeze=None):
    ops = F2WOperations(world.settings, world.service, publication=world.pub, clock=world.step_clock,
                        publication_gate=PublicationGate(frozenset({world.target})))
    return PostExtractionPublisher(ops, freeze=freeze or (lambda _terms: f"{SCOPE}/v2"),
                                   is_safe=world.step.plan_is_safe, target=world.target)


def _foreign_link(world, entity_id):
    forge = next(e["id"] for e in world.client.entities.values() if (e.get("properties") or {}).get("owner") == "forge")
    lid = str(uuid.uuid4())
    world.client.links[lid] = {"id": lid, "subjectId": forge, "objectId": entity_id, "predicate": "forge:uses",
                               "properties": None, "accessBlob": None}
    return lid


def test_til20_local_safe_plan_is_auto_approved_by_the_local_operator_and_published(world):
    world.publish("v1")
    foreign = world.foreign()
    world.client.writes.clear()
    result = _publisher(world).publish(world.ctx, "terms.json")
    assert result["status"] == "success" and result["auto_approved"] is True, result
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v2"
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v2", "marker_snapshot_id": "v2"}
    approval = world.pubstore.get_approval(result["approval_ids"][0])
    assert isinstance(approval, ApprovalRecord)
    assert approval.reason == AUTO_SAFE_PLAN
    assert approval.approved_by == approval.requested_by == "local-operator"  # never anonymous
    assert approval.operation_id == result["job_id"] and approval.approval_id in result["approval_ids"]
    # No delete outside the plan: a replacement retires, it never deletes; foreign rows are byte-identical.
    assert "deleteEntity" not in world.client.writes
    assert world.foreign() == foreign


def test_unsafe_plan_on_local_waits_for_review_until_a_person_approves(world):
    world.publish("v1")
    revised = graph_of(world.v1)["things"][0]["id"]  # derive() revises the first nodes of v1
    _foreign_link(world, world.live()[revised])  # a FORGE link on a record v2 retires is a boundary link
    foreign = world.foreign()
    world.client.writes.clear()
    result = _publisher(world).publish(world.ctx, "terms.json")
    assert result["status"] == "awaiting_review" and result["auto_approved"] is False, result
    assert "boundary links" in result["message"]
    job_id = result["job_id"]
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v1", "nothing moved"
    assert world.client.writes == [], "an unreviewed plan writes nothing"
    assert world.service.get(world.ctx, job_id).state.value == "awaiting_review"
    # A person reviews and approves (no policy reason on that record); only then is it published.
    world.approve(job_id)
    assert world.work() == (job_id, world.service.get(world.ctx, job_id).state)
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v2"
    person = world.pubstore.get_approval("appr-" + world.pub.current_plan(world.ctx, job_id).plan_sha256[:10])
    assert person is not None and person.reason is None
    assert world.foreign() == foreign


def test_policy_table(world):
    world.publish("v1")
    job = world.pub.submit(world.ctx, f"{SCOPE}/v2", world.target)
    world.work()
    real = world.pub.current_plan(world.ctx, job.job_id)
    assert decide_approval(world.settings, real, lambda _p: True).auto_approve
    no = decide_approval(world.settings, real, lambda _p: False)
    assert not no.auto_approve and "review required" in no.why
    als = world.settings.__class__.for_disposable_test_target(
        "als-dev", tiled_target="http://127.0.0.1:59872", writer_identity="f2w-writer")
    def boom(_p):
        raise AssertionError("ALS must not even ask whether the plan is safe")
    assert not decide_approval(als, real, boom).auto_approve


def test_publisher_freezes_the_next_snapshot_from_the_terms(world, tmp_path):
    world.publish("v1")
    freeze = snapshot_freezer(storage_kg=world.v1.parents[1], corpus=SCOPE, registry=SchemaRegistry.discover(),
                              code_commit="0" * 40, terms_ref="inputs/core_dict.terms.json")
    result = _publisher(world, freeze=freeze).publish(world.ctx, INPUTS / "core_dict.terms.json")
    # v2 already exists in this world (derive), so the freezer's next number is v3 with parent v2.
    assert result["snapshot_ref"] == f"{SCOPE}/v3", result
    assert result["status"] in ("success", "awaiting_review")


def test_a_failed_freeze_or_refused_submit_is_an_error_status_not_an_exception(world):
    def bad(_terms):
        raise ValueError("no terms")
    out = _publisher(world, freeze=bad).publish(world.ctx, "terms.json")
    assert out["status"] == "error" and out["stage"] == "freeze"
    ops = F2WOperations(world.settings, world.service, publication=world.pub)  # default gate allows no target
    closed = PostExtractionPublisher(ops, freeze=lambda _t: f"{SCOPE}/v2", is_safe=world.step.plan_is_safe,
                                     target=world.target)
    out = closed.publish(world.ctx, "terms.json")
    assert out["status"] == "error" and out["stage"] == "submit" and "H5" in out["message"]
