"""T10B: rollback as a replacement plan through revive, marker/pointer agreement (TIL-14), Q14 foreign-change report,
B27 re-plan for a replacement plan, B28 toggle, corpus removal and purge jobs.

Binary-case equivalents: TIL-06 (rollback preserves FORGE-owned records), TIL-14 (marker names what the pointer
reports), TIL-21 (rollback and roll-forward revive the same Tiled records; old ids resolve at every step).
"""
import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph.imports import tiled_json as tj
from app.modules.schema.registry import SchemaRegistry
from tests.jobs.replacement_world import SCOPE, RWorld, build_v1, derive


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


def test_til14_til21_til06_v1_v2_rollback_forward_restores_uuids_and_marker_follows_pointer(world):
    foreign = world.foreign()
    world.publish("v1")
    uuid_v1 = world.live()
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v1", "marker_snapshot_id": "v1"}

    world.publish("v2")
    uuid_v2 = world.live()
    assert uuid_v2 != uuid_v1 and world.agree()["marker_snapshot_id"] == "v2"
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v2"

    # v2 -> v1: a replacement plan to the chosen version through revive (B28 toggle names the previous pointer).
    handle = world.pub.rollback_replacement(world.ctx, world.target, reason="bad extraction")
    plan_job = world.finish(handle.job_id)
    assert world.live() == uuid_v1, "every v1 Tiled UUID is live again (TIL-21)"
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v1", "marker_snapshot_id": "v1"}
    report = world.store.stage(plan_job, "receipt").result["report"]
    assert report["revived_entities"] == 2 and report["snapshot_id"] == "v1"
    assert world.foreign() == foreign, "FORGE-owned records are byte-identical after rollback (TIL-06)"

    # Second rollback is a roll-forward: back to v2, again the same UUIDs.
    world.finish(world.pub.rollback_replacement(world.ctx, world.target, reason="it was fine").job_id)
    assert world.live() == uuid_v2
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v2", "marker_snapshot_id": "v2"}
    actions = [(h["action"], h["record"]["snapshot_ref"]) for h in world.pubstore.pointer_history(world.target)]
    assert actions == [("activate", f"{SCOPE}/v1"), ("activate", f"{SCOPE}/v2"), ("rollback", f"{SCOPE}/v1"),
                       ("rollback", f"{SCOPE}/v2")]
    assert world.foreign() == foreign


def test_pointer_only_rollback_is_refused_when_a_marker_moves_with_the_pointer(world):
    world.publish("v1")
    world.publish("v2")
    with pytest.raises(ContractError) as err:
        world.pub.rollback(world.ctx, world.target, reason="toggle")
    assert err.value.code is ErrorCode.CONFLICT and "marker" in str(err.value)
    assert world.agree()["agree"]


def test_rollback_with_no_previous_pointer_is_refused(world):
    with pytest.raises(ContractError) as err:
        world.pub.rollback_replacement(world.ctx, world.target, reason="nothing yet")
    assert err.value.code is ErrorCode.CONFLICT
    world.publish("v1")
    with pytest.raises(ContractError):
        world.pub.rollback_replacement(world.ctx, world.target, reason="still nothing before v1")


def test_pointer_is_not_moved_when_the_marker_disagrees(world, monkeypatch):
    world.publish("v1")
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v2", world.target)
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    world.approve(handle.job_id)
    monkeypatch.setattr(world.step, "release_marker", lambda ctx, scope: {"snapshot_id": "v1", "plan_sha256": "x"})
    done = world.work()
    assert done is not None and done[1] is not JobState.SUCCEEDED
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v1"
    assert world.pubstore.quarantined(handle.job_id)


def test_q14_foreign_change_during_the_run_is_in_the_receipt_and_the_run_completes(world):
    world.publish("v1")
    forge = next(e["id"] for e in world.client.entities.values() if e["uri"] == "forge:esaf/1")
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v2", world.target)
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    calls = {"n": 0}
    real = world.client.create_entity

    def meddle(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            world.client.entities[forge]["name"] = "renamed by FORGE during the run"
        return real(**kw)

    world.client.create_entity = meddle
    world.approve(handle.job_id)
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)
    report = world.store.stage(handle.job_id, "receipt").result["report"]
    assert report["foreign_changes"] == [f"changed:e:{forge}"]
    assert world.agree()["agree"] and world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v2"


def test_b27_stale_replacement_plan_is_replanned_and_waits_for_a_new_approval(world):
    from app.modules.graph import literature_writer as lw

    world.publish("v1")
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v2", world.target)
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    first = world.pubstore.current_plan(handle.job_id).plan
    world.approve(handle.job_id)
    # An owned record of the scope changes between approval and import (another writer of this scope).
    spec = lw.entity_spec(SCOPE, {"id": "late", "category": "Material", "name": "late"})
    world.client.create_entity(entity_type=spec.entity_type, name=spec.name, uri=spec.uri,
                               properties=spec.properties, access_blob={})
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    second = world.pubstore.current_plan(handle.job_id).plan
    assert second.plan_sha256 != first.plan_sha256
    assert "target_revision_changed" in world.pubstore.approval_status("appr-" + first.plan_sha256[:10])[1]
    assert world.work() is None  # waits for the new approval; nothing written for v2 yet
    world.approve(handle.job_id)
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)
    assert world.agree()["marker_snapshot_id"] == "v2"


def test_legacy_adoption_needs_jobs_admin(world):
    from app.modules.contracts.context import RequestContext

    lowly = RequestContext(**{**world.ctx.model_dump(), "capabilities": frozenset({"graph.publish"})})
    with pytest.raises(ContractError) as err:
        world.pub.submit(lowly, f"{SCOPE}/v1", world.target, adopt_legacy=True)
    assert err.value.code is ErrorCode.DENIED


def test_corpus_removal_and_purge_are_separate_jobs_with_their_own_approvals(world):
    foreign = world.foreign()
    world.publish("v1")
    wrong = world.pub.submit_retire(world.ctx, SCOPE, world.target, confirm_scope="other")
    assert world.work() == (wrong.job_id, JobState.FAILED)           # the corpus name must be typed in
    retire = world.pub.submit_retire(world.ctx, SCOPE, world.target, confirm_scope=SCOPE)
    assert world.work() == (retire.job_id, JobState.AWAITING_REVIEW)
    plan = world.pub.current_plan(world.ctx, retire.job_id)
    assert plan.supersessions > 0
    world.approve(retire.job_id)
    assert world.work() == (retire.job_id, JobState.SUCCEEDED)
    assert world.live() == {} and world.pubstore.pointer(world.target) is None
    assert world.agree() == {"agree": True, "pointer_snapshot_id": None, "marker_snapshot_id": None}
    assert world.foreign() == foreign
    # Purge: its own plan and approval; a full purge after a corpus removal is the explicit include_previous choice.
    purge = world.pub.submit_purge(world.ctx, SCOPE, world.target, include_previous=True)
    assert world.work() == (purge.job_id, JobState.AWAITING_REVIEW)
    assert world.pub.current_plan(world.ctx, purge.job_id).plan_sha256 != plan.plan_sha256
    world.approve(purge.job_id)
    assert world.work() == (purge.job_id, JobState.SUCCEEDED)
    assert not [e for e in world.client.scan_entities() if (e.get("properties") or {}).get("f2w_graph_id") == SCOPE]
    assert world.foreign() == foreign
    assert world.store.stage(purge.job_id, "record").result["report"]["outcomes"]["deleted_entities"] > 0
