"""ReplacementPublishStep edges (T27f): refusals, plan persistence across a restart, receipts and operation plans.

Runs the real importer over the in-memory Tiled double of ``tests/jobs/replacement_world``.
"""
from __future__ import annotations

import json
import shutil

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph.imports import tiled_json as tj
from app.modules.jobs.replacement_step import OPERATION_KINDS, ReplacementPublishStep
from app.modules.schema.registry import SchemaRegistry
from tests.jobs.replacement_world import SCOPE, RWorld, build_v1, derive


@pytest.fixture(scope="module")
def fingerprints():
    return tj.default_supported_schema_fingerprints(SchemaRegistry.discover())


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """The frozen snapshots are built once (slow) and copied per test."""
    root = tmp_path_factory.mktemp("rstep-template")
    v1 = build_v1(root, SchemaRegistry.discover())
    derive(v1, "v2", revise=1, add=1)
    return root


@pytest.fixture()
def world(tmp_path, template, fingerprints):
    shutil.copytree(template / "storage", tmp_path / "storage")
    return RWorld(tmp_path, tmp_path / "storage" / "kg" / SCOPE / "v1", fingerprints)


def _plan(world, snap="v1"):
    return world.step.plan(world.ctx, world.snapshots.load(f"{SCOPE}/{snap}"), world.target, operation_id="op-1")


def _code(excinfo) -> ErrorCode:
    return excinfo.value.code


def test_operation_kinds_and_flags():
    assert OPERATION_KINDS == ("retire", "purge") and ReplacementPublishStep.has_release_marker is True


def test_a_plan_for_another_target_is_refused(world):
    with pytest.raises(ContractError) as err:
        world.step.plan(world.ctx, world.snapshots.load(f"{SCOPE}/v1"), "http://other.example#x", operation_id="op")
    assert _code(err) is ErrorCode.INVALID_INPUT


def test_a_fresh_replacement_plan_is_safe_and_persisted_by_hash(world):
    plan = _plan(world)
    assert world.step.plan_is_safe(plan) is True
    on_disk = world.tmp_path / "import-state" / "job-plans" / f"{plan.plan_sha256}.json"
    assert json.loads(on_disk.read_text(encoding="utf-8"))["kind"] == "replacement"


def test_unknown_plans_are_not_safe_and_not_recalled(world):
    plan = _plan(world)
    other = ReplacementPublishStep(client=world.client, settings=world.settings, supported_schema_fingerprints=world.fp,
                                   storage_kg=world.v1.parents[1], state_dir=world.tmp_path / "elsewhere",
                                   clock=world.step_clock)
    assert other.plan_is_safe(plan) is False
    with pytest.raises(ContractError) as err:
        other.plan_revision(world.ctx, plan)
    assert _code(err) is ErrorCode.NOT_FOUND and "re-plan" in str(err.value)


def test_a_restarted_step_recalls_the_plan_from_disk(world):
    plan = _plan(world)
    revision = world.step.plan_revision(world.ctx, plan)
    restarted = ReplacementPublishStep(client=world.client, settings=world.settings,
                                       supported_schema_fingerprints=world.fp, storage_kg=world.v1.parents[1],
                                       state_dir=world.tmp_path / "import-state", clock=world.step_clock)
    assert restarted.plan_revision(world.ctx, plan) == revision
    assert restarted.plan_is_safe(plan) is True and restarted.operation_revision(world.ctx, plan) == revision


def test_a_plan_that_differs_from_the_one_on_record_is_stale(world):
    plan = _plan(world)
    forged = plan.model_copy(update={"supersessions": plan.supersessions + 5})
    with pytest.raises(ContractError) as err:
        world.step.plan_revision(world.ctx, forged)
    assert _code(err) in (ErrorCode.STALE_APPROVAL, ErrorCode.INVALID_INPUT, ErrorCode.DENIED)


def test_a_damaged_plan_file_is_unreadable_not_trusted(world):
    plan = _plan(world)
    path = world.tmp_path / "import-state" / "job-plans" / f"{plan.plan_sha256}.json"
    path.write_text("{not json", encoding="utf-8")
    fresh = ReplacementPublishStep(client=world.client, settings=world.settings, supported_schema_fingerprints=world.fp,
                                   storage_kg=world.v1.parents[1], state_dir=world.tmp_path / "import-state",
                                   clock=world.step_clock)
    with pytest.raises(ContractError) as err:
        fresh.plan_revision(world.ctx, plan)
    assert _code(err) is ErrorCode.NOT_FOUND


def test_a_retire_plan_is_recalled_from_disk_after_a_restart(world):
    world.publish("v1")
    retire = world.step.plan_operation(world.ctx, "retire", {"scope": SCOPE, "confirm_scope": SCOPE})
    restarted = ReplacementPublishStep(client=world.client, settings=world.settings,
                                       supported_schema_fingerprints=world.fp, storage_kg=world.v1.parents[1],
                                       state_dir=world.tmp_path / "import-state", clock=world.step_clock)
    assert restarted.operation_revision(world.ctx, retire) == world.step.operation_revision(world.ctx, retire)
    purge = world.step.plan_operation(world.ctx, "purge", {"scope": SCOPE, "include_previous": True})
    assert restarted.operation_revision(world.ctx, purge)


def test_target_revision_is_per_scope_only(world):
    with pytest.raises(ContractError) as err:
        world.step.target_revision(world.ctx, world.target)
    assert _code(err) is ErrorCode.INVALID_INPUT and "plan_revision" in str(err.value)


def test_unknown_scope_operation_is_refused(world):
    with pytest.raises(ContractError) as err:
        world.step.plan_operation(world.ctx, "explode", {"scope": SCOPE})
    assert _code(err) is ErrorCode.INVALID_INPUT and "explode" in str(err.value)


def test_plans_are_applied_only_by_their_own_entry_point(world):
    replacement = _plan(world)
    noop = lambda: None  # noqa: E731
    with pytest.raises(ContractError) as err:
        world.step.apply_operation(world.ctx, replacement, object(), guard=noop)
    assert _code(err) is ErrorCode.INVALID_INPUT and "apply" in str(err.value)
    world.publish("v1")
    retire = world.step.plan_operation(world.ctx, "retire", {"scope": SCOPE, "confirm_scope": SCOPE})
    assert world.step.plan_is_safe(retire) is False
    with pytest.raises(ContractError) as err:
        world.step.apply(world.ctx, retire, object(), operation_id="op", guard=noop)
    assert _code(err) is ErrorCode.INVALID_INPUT and "apply_operation" in str(err.value)


def test_reconcile_and_verify_before_and_after_the_apply(world):
    plan = _plan(world)
    assert world.step.reconcile(world.ctx, plan, operation_id="op") is None
    bad = world.step.verify(world.ctx, plan, None)  # type: ignore[arg-type]
    assert bad.ok is False and "no receipt" in bad.detail
    with pytest.raises(ContractError) as err:
        world.step.report(world.ctx, plan, None)  # type: ignore[arg-type]
    assert _code(err) is ErrorCode.NOT_FOUND
    job_id = world.publish("v1")
    live_plan = world.pub.current_plan(world.ctx, job_id)
    outcome = world.step.reconcile(world.ctx, live_plan, operation_id="op")
    assert outcome is not None and outcome.complete and outcome.effects[0].startswith("receipt:")
    assert world.step.verify(world.ctx, live_plan, outcome).ok is True
    receipt = world.step.receipt(world.ctx, live_plan, None, outcome, world.step.verify(world.ctx, live_plan, outcome))  # type: ignore[arg-type]
    assert receipt.verified is not False
    unverified = world.step.receipt(world.ctx, live_plan, None, outcome, type(bad)(False, "x", 0, ""))  # type: ignore[arg-type]
    assert unverified.verified is False
    report = world.step.report(world.ctx, live_plan, receipt)
    assert report["kind"] and report["snapshot_id"] == "v1" and report["read_probes_equal"] is True
    assert report["foreign_changes"] == [] and report["revived_entities"] == 0


def test_release_marker_is_absent_until_published_then_names_the_snapshot(world):
    assert world.step.release_marker(world.ctx, SCOPE) is None
    world.publish("v1")
    marker = world.step.release_marker(world.ctx, SCOPE)
    assert marker is not None and marker["snapshot_id"] == "v1" and marker["graph_sha256"] and marker["receipt_id"]


def test_retire_and_purge_report_their_receipt_extras(world):
    world.publish("v1")
    retire = world.pub.submit_retire(world.ctx, SCOPE, world.target, confirm_scope=SCOPE)
    world.finish(retire.job_id)
    plan = world.pub.current_plan(world.ctx, retire.job_id)
    extras = world.step.operation_report(plan)
    assert extras["kind"] and extras["foreign_changes"] == [] and extras["outcomes"]
    missing = _plan(world, "v2")
    with pytest.raises(ContractError) as err:
        world.step.operation_report(missing)
    assert _code(err) is ErrorCode.NOT_FOUND


def test_plan_options_reach_the_importer(world):
    plan = world.step.plan(world.ctx, world.snapshots.load(f"{SCOPE}/v1"), world.target, operation_id="op",
                           options={"adopt_legacy": True, "resolutions": []})
    assert world.step.plan_revision(world.ctx, plan)  # recorded and readable like any other plan
    assert world.step.plan_is_safe(plan) is True  # nothing to adopt in this scope, so still safe
