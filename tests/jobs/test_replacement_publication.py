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
from tests.jobs.replacement_world import SCOPE, RWorld, build_v1, derive, graph_of


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
    assert err.value.reason == "rollback_replacement_required" and "rollback_replacement" in str(err.value)
    assert err.value.retryable is False
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


# --- zero-operation rollback: one pointer per target, several corpora ------------------------------------

OTHER = "othercorpus"


def derive_corpus(v1, corpus: str) -> None:
    """A ``v1`` snapshot of a second corpus next to the synthetic one (same shape, its own node ids)."""
    import copy

    from import_fakes import snapshot_with_graph

    g = copy.deepcopy(graph_of(v1))
    rename = {t["id"]: f"{corpus}:{t['id'].split(':', 1)[-1]}" for t in g["things"]}
    for t in g["things"]:
        t["id"] = rename[t["id"]]
    for a in g["associations"]:
        a["subject"], a["object"] = rename.get(a["subject"], a["subject"]), rename.get(a["object"], a["object"])
    snapshot_with_graph(v1, v1.parents[3], corpus, "v1", g)


def live_of(world, scope: str) -> dict[str, str]:
    from app.modules.graph import identity_adapter as ida

    out = {}
    for e in world.client.scan_entities():
        key = ida.literature_entity_key(e)
        if key and key[0] == scope:
            out[key[1]] = e["id"]
    return out


def counts(world) -> tuple[int, int]:
    return len(list(world.client.scan_entities())), len(list(world.client.scan_links()))


def test_noop_rollback_after_an_unrelated_corpus_moves_the_pointer_and_writes_nothing_to_tiled(world):
    """E2E rehearsal (T13): A/v1, then B/v1 on the same target, then rollback-replacement.

    Nothing of A was retired by B, so the rollback plans zero operations; the job must still receipt and
    activate (pointer A/v1 at version 3) while Tiled, both corpora's records and A's marker stay untouched.
    """
    derive_corpus(world.v1, OTHER)
    a_job = world.publish("v1")
    a_live, a_marker = world.live(), world.step.release_marker(world.ctx, SCOPE)
    a_receipt = world.pubstore.pointer(world.target).receipt_id
    assert a_marker["plan_sha256"] == world.pubstore.current_plan(a_job).plan.plan_sha256
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    b_live, b_marker = live_of(world, OTHER), world.step.release_marker(world.ctx, OTHER)
    assert world.pubstore.pointer(world.target).version == 2
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{OTHER}/v1"
    assert world.live() == a_live, "publishing another corpus retires nothing of A"
    before, n_writes = counts(world), len(world.client.writes)

    # Owner decision 2026-11-09 (nineteenth set): with two corpora live the request must name one, so the
    # corpus-less call is refused; the zero-operation plan itself is submitted the way a named rollback does.
    with pytest.raises(ContractError) as refused:
        world.pub.rollback_replacement(world.ctx, world.target, reason="back to A")
    assert refused.value.reason == "corpus_required"
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target, rollback_reason="back to A")
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = world.pub.current_plan(world.ctx, handle.job_id)
    assert (plan.creates, plan.links, plan.supersessions) == (0, 0, 0) and plan.corpus_scope == (SCOPE,)
    assert plan.plan_sha256 != a_marker["plan_sha256"]
    world.approve(handle.job_id)
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)

    pointer = world.pubstore.pointer(world.target)
    assert (pointer.version, pointer.snapshot_ref) == (3, f"{SCOPE}/v1")
    assert pointer.receipt_id != a_receipt, "the rollback has its own receipt under its own plan hash"
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v1", "marker_snapshot_id": "v1"}
    assert counts(world) == before and len(world.client.writes) == n_writes, "nothing written to Tiled"
    assert world.live() == a_live and live_of(world, OTHER) == b_live
    assert world.step.release_marker(world.ctx, SCOPE) == a_marker, "A's marker still names the covering plan"
    assert world.step.release_marker(world.ctx, OTHER) == b_marker, "a rollback never removes a corpus"
    assert world.store.stage(handle.job_id, "import").result["created"] == 0
    progress = world.service.progress(world.ctx, handle.job_id)["phases"]["import"]["progress"]
    assert progress["import.created"] == 0 and progress["import.reconciled"] == 0
    report = world.store.stage(handle.job_id, "receipt").result["report"]
    assert report["noop"] is True and report["covered_by"] == a_receipt and report["kind"] == "noop"
    assert report["revived_entities"] == 0 and report["snapshot_id"] == "v1"
    assert not world.pubstore.quarantined(handle.job_id)

    # The B28 toggle back is a no-op for B as well: pointer version 4, still nothing written.
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target, rollback_reason="and forward").job_id)
    pointer = world.pubstore.pointer(world.target)
    assert (pointer.version, pointer.snapshot_ref) == (4, f"{OTHER}/v1")
    assert counts(world) == before and len(world.client.writes) == n_writes
    assert world.live() == a_live and live_of(world, OTHER) == b_live


def test_noop_receipt_fails_verify_for_another_revision_target_or_without_its_covering_receipt(world):
    from app.modules.graph import literature_writer as lw
    from app.modules.graph.imports.receipts import receipt_valid_for
    from app.modules.jobs.publish_ports import ApplyOutcome
    from app.modules.jobs.replacement_step import NOOP_KIND

    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    job = world.finish(world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target, rollback_reason="back to A").job_id)
    plan = world.pubstore.current_plan(job).plan
    outcome = ApplyOutcome.from_dict(world.store.stage(job, "import").result)
    thin = world.step.receipts.find(plan_sha256=plan.plan_sha256)[0]
    assert thin.extras.kind == NOOP_KIND and thin.receipt.outcomes["noop"] == 1
    ctx = world.deps.ctx_for(world.service.store.get(job))
    assert world.step.verify(ctx, plan, outcome).ok
    assert world.step.reconcile(ctx, plan, operation_id="x") is not None

    # Another target: the receipt names its own target only.
    assert not receipt_valid_for(thin, target="https://tiled-staging.example/api/v1", revision=thin.extras.resulting_revision)

    # The covering replacement receipt disappears: the thin receipt alone no longer verifies or reconciles.
    cover_id = world.step.covering_receipt(ctx, plan).receipt_id
    cover_path = world.step.receipts.root / f"{cover_id}.json"
    saved = cover_path.read_bytes()
    cover_path.unlink()
    assert world.step.covering_receipt(ctx, plan) is None
    verification = world.step.verify(ctx, plan, outcome)
    assert (verification.ok, verification.detail) == (False, "no receipt matches the target as read back now")
    assert verification.target_revision == thin.extras.resulting_revision
    assert world.step.reconcile(ctx, plan, operation_id="x") is None
    cover_path.write_bytes(saved)
    assert world.step.verify(ctx, plan, outcome).ok

    # The scope moves to another revision (an owned record appears): neither receipt is valid for it.
    spec = lw.entity_spec(SCOPE, {"id": "late", "category": "Material", "name": "late"})
    world.client.create_entity(entity_type=spec.entity_type, name=spec.name, uri=spec.uri,
                               properties=spec.properties, access_blob={})
    verification = world.step.verify(ctx, plan, outcome)
    assert not verification.ok and verification.target_revision != thin.extras.resulting_revision
    assert world.step.reconcile(ctx, plan, operation_id="x") is None
    assert world.step.covering_receipt(ctx, plan) is None


# --- Model C: per-corpus live versions, corpus-aware rollback (owner decision 2026-11-09) ------------------

def derive_other_v2(world) -> None:
    """``othercorpus/v2``: one node revised, next to ``othercorpus/v1``."""
    from import_fakes import snapshot_with_graph

    g = graph_of(world.v1.parents[1] / OTHER / "v1")
    g["things"][0]["description"] = "revised in v2"
    snapshot_with_graph(world.v1, world.v1.parents[3], OTHER, "v2", g)


def submit_finish(world, corpus: str, version: str) -> str:
    return world.finish(world.pub.submit(world.ctx, f"{corpus}/{version}", world.target).job_id)


def test_corpus_rollback_steps_back_within_one_corpus_while_the_other_stays_live(world):
    derive_corpus(world.v1, OTHER)
    derive_other_v2(world)
    world.publish("v1")
    uuid_a1 = world.live()
    world.publish("v2")
    uuid_a2 = world.live()
    submit_finish(world, OTHER, "v1")
    b1_live = live_of(world, OTHER)
    submit_finish(world, OTHER, "v2")
    b2_live, b2_marker = live_of(world, OTHER), world.step.release_marker(world.ctx, OTHER)
    assert b2_live != b1_live
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{OTHER}/v2"

    live = world.pub.live_versions(world.ctx, world.target)
    assert {c: (v.snapshot_ref, v.pointer_version) for c, v in live.items()} == {
        SCOPE: (f"{SCOPE}/v2", 2), OTHER: (f"{OTHER}/v2", 4)}
    # The agreement check compares each corpus's marker with that corpus's own live version.
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v2", "marker_snapshot_id": "v2"}
    assert world.pub.verify_agreement(world.ctx, world.target, OTHER)["agree"]

    # Corpus A back one version: A/v2 -> A/v1; B (the last activated) is untouched.
    a_job = world.finish(world.pub.rollback_replacement(world.ctx, world.target, reason="A back",
                                                        corpus=SCOPE).job_id)
    assert world.live() == uuid_a1, "A's v1 records are live again (same UUIDs)"
    assert live_of(world, OTHER) == b2_live and world.step.release_marker(world.ctx, OTHER) == b2_marker
    pointer = world.pubstore.pointer(world.target)
    assert (pointer.version, pointer.snapshot_ref) == (5, f"{SCOPE}/v1"), "the pointer is 'last activated'"
    assert world.pubstore.pointer_history(world.target)[-1]["action"] == "rollback"
    assert world.agree() == {"agree": True, "pointer_snapshot_id": "v1", "marker_snapshot_id": "v1"}
    assert world.pub.verify_agreement(world.ctx, world.target, OTHER) == {
        "agree": True, "pointer_snapshot_id": "v2", "marker_snapshot_id": "v2"}, "B is judged against B's own version"
    live = world.pub.live_versions(world.ctx, world.target)
    assert live[SCOPE].snapshot_ref == f"{SCOPE}/v1" and live[OTHER].snapshot_ref == f"{OTHER}/v2"
    assert world.store.stage(a_job, "receipt").result["report"]["revived_entities"] == 2

    # B back one version: B/v2 -> B/v1 while A stays at v1.
    world.finish(world.pub.rollback_replacement(world.ctx, world.target, reason="B back", corpus=OTHER).job_id)
    assert live_of(world, OTHER) == b1_live and world.live() == uuid_a1
    live = world.pub.live_versions(world.ctx, world.target)
    assert {c: (v.snapshot_ref, v.pointer_version) for c, v in live.items()} == {
        SCOPE: (f"{SCOPE}/v1", 5), OTHER: (f"{OTHER}/v1", 6)}
    assert all(world.pub.verify_agreement(world.ctx, world.target, c)["agree"] for c in (SCOPE, OTHER))
    # Toggle per corpus: asking for A again names A/v2 (a roll-forward); A/v2 records come back.
    world.finish(world.pub.rollback_replacement(world.ctx, world.target, reason="A fwd", corpus=SCOPE).job_id)
    assert world.live() == uuid_a2 and live_of(world, OTHER) == b1_live


def test_corpus_rollback_is_refused_with_a_typed_error_without_an_earlier_version_or_live_corpus(world):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    submit_finish(world, OTHER, "v1")
    for corpus, code, reason in ((SCOPE, ErrorCode.CONFLICT, "no_previous_corpus_version"),
                                 (OTHER, ErrorCode.CONFLICT, "no_previous_corpus_version"),
                                 ("neverpublished", ErrorCode.NOT_FOUND, "corpus_not_live")):
        with pytest.raises(ContractError) as err:
            world.pub.rollback_replacement(world.ctx, world.target, reason="x", corpus=corpus)
        assert err.value.code is code and err.value.reason == reason and err.value.retryable is False
    assert world.pubstore.pointer(world.target).version == 2, "nothing was submitted or moved"
    assert world.service.store.list_jobs() and len(world.service.store.list_jobs()) == 2


def test_rollback_without_corpus_is_refused_while_several_corpora_are_live(world):
    """Owner decision 2026-11-09 (nineteenth set): V53/V20 with no corpus and several live is 409 ``corpus_required``."""
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    world.publish("v2")
    submit_finish(world, OTHER, "v1")
    jobs_before, pointer_before = len(world.service.store.list_jobs()), world.pubstore.pointer(world.target)
    with pytest.raises(ContractError) as err:
        world.pub.rollback_replacement(world.ctx, world.target, reason="which one?")
    assert err.value.code is ErrorCode.CONFLICT and err.value.reason == "corpus_required"
    assert err.value.retryable is False and SCOPE in str(err.value) and OTHER in str(err.value)
    assert len(world.service.store.list_jobs()) == jobs_before and world.pubstore.pointer(world.target) == pointer_before
    # Naming a corpus is allowed (A has an earlier version: v1).
    handle = world.pub.rollback_replacement(world.ctx, world.target, reason="A back", corpus=SCOPE)
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    assert world.pub.current_plan(world.ctx, handle.job_id).corpus_scope == (SCOPE,)


def test_rollback_without_corpus_still_means_the_previous_pointer_with_one_live_corpus(world):
    world.publish("v1")
    world.publish("v2")
    handle = world.pub.rollback_replacement(world.ctx, world.target, reason="one corpus: unchanged")
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    assert world.pub.current_plan(world.ctx, handle.job_id).corpus_scope == (SCOPE,)


def test_pointer_only_rollback_without_corpus_is_refused_with_several_live_when_no_marker_moves(world, monkeypatch):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    submit_finish(world, OTHER, "v1")
    monkeypatch.setattr(world.pub, "replacement", False)
    with pytest.raises(ContractError) as err:
        world.pub.rollback(world.ctx, world.target, reason="which one?")
    assert err.value.code is ErrorCode.CONFLICT and err.value.reason == "corpus_required"
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{OTHER}/v1"


def test_live_versions_follow_corpus_removal_and_markers(world):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    submit_finish(world, OTHER, "v1")
    retire = world.pub.submit_retire(world.ctx, SCOPE, world.target, confirm_scope=SCOPE)
    assert world.work() == (retire.job_id, JobState.AWAITING_REVIEW)
    world.approve(retire.job_id)
    assert world.work() == (retire.job_id, JobState.SUCCEEDED)
    # Owner decision 2026-11-09 (eighteenth set): the pointer moves to the still-live corpus instead of clearing.
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{OTHER}/v1"
    assert list(world.pub.live_versions(world.ctx, world.target)) == [OTHER]
    assert world.agree() == {"agree": True, "pointer_snapshot_id": None, "marker_snapshot_id": None}
    assert world.pub.verify_agreement(world.ctx, world.target, OTHER)["pointer_snapshot_id"] == "v1"
    # A marker that no longer names the version history says is live drops it from the view.
    world.step.release_marker = lambda ctx, scope: {"snapshot_id": "v9"}  # type: ignore[method-assign]
    assert world.pub.live_versions(world.ctx, world.target) == {}


# --- corpus removal moves the pointer to a still-live corpus (owner decision 2026-11-09, eighteenth set) ---

def retire(world, corpus: str) -> None:
    handle = world.pub.submit_retire(world.ctx, corpus, world.target, confirm_scope=corpus)
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    world.approve(handle.job_id)
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)


def test_corpus_removal_moves_the_pointer_to_the_most_recently_activated_remaining_corpus(world):
    derive_corpus(world.v1, OTHER)
    derive_corpus(world.v1, "thirdcorpus")
    world.publish("v1")
    world.publish("v2")                                    # A/v2: pointer 2
    submit_finish(world, OTHER, "v1")                      # B/v1: pointer 3
    submit_finish(world, "thirdcorpus", "v1")              # C/v1: pointer 4 (the pointer's corpus)
    assert world.pubstore.pointer(world.target).snapshot_ref == "thirdcorpus/v1"

    retire(world, "thirdcorpus")                           # the pointer's own corpus goes: B is the latest remaining
    pointer = world.pubstore.pointer(world.target)
    assert pointer.snapshot_ref == f"{OTHER}/v1" and pointer.version == 6
    history = world.pubstore.pointer_history(world.target)
    assert [h["action"] for h in history[-2:]] == ["retire", "move"]
    assert "corpus removal" in history[-1]["reason"] and "thirdcorpus" in history[-1]["reason"]
    assert set(world.pub.live_versions(world.ctx, world.target)) == {SCOPE, OTHER}
    assert world.pubstore.live_by_corpus(world.target)[OTHER].pointer_version == 3, "liveness of B is unchanged"

    retire(world, OTHER)                                   # then A/v2 is the only one left
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v2"
    assert list(world.pubstore.live_by_corpus(world.target)) == [SCOPE]

    retire(world, SCOPE)                                   # nothing is live: the pointer is cleared
    assert world.pubstore.pointer(world.target) is None and world.pubstore.live_by_corpus(world.target) == {}


def test_removing_a_corpus_that_is_not_the_pointers_keeps_the_pointer_on_a_live_corpus(world):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    submit_finish(world, OTHER, "v1")                      # the pointer names B
    retire(world, SCOPE)
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{OTHER}/v1"
    assert list(world.pubstore.live_by_corpus(world.target)) == [OTHER]


def test_a_resumed_removal_does_not_move_the_pointer_again(world):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    submit_finish(world, OTHER, "v1")
    retire(world, OTHER)
    before = (world.pubstore.pointer(world.target), len(world.pubstore.pointer_history(world.target)))
    assert world.pubstore.clear_pointer(world.target, actor="x", reason="corpus removal again", corpus=OTHER) is None
    assert (world.pubstore.pointer(world.target), len(world.pubstore.pointer_history(world.target))) == before
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v1"


# --- zero-operation rollback auto-approval (owner decision 2026-11-09: :local only) -----------------------

def local_approver(world):
    from app.api.post_extraction import noop_rollback_auto_approver

    return noop_rollback_auto_approver(world.settings, clock=world.step_clock)


@pytest.fixture()
def auto_world(tmp_path, registry, fingerprints):
    v1 = build_v1(tmp_path, registry)
    derive(v1, "v2", revise=2, add=1)
    derive_corpus(v1, OTHER)
    w = RWorld(tmp_path, v1, fingerprints)
    w.pub.deps.auto_approve = local_approver(w)
    return w


def test_noop_rollback_is_auto_approved_on_local_with_an_auto_safe_plan_record(auto_world):
    world = auto_world
    world.publish("v1")
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    before, n_writes = counts(world), len(world.client.writes)
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target, rollback_reason="back to A")
    assert world.work() == (handle.job_id, JobState.SUCCEEDED), "no human approval step"
    plan = world.pubstore.current_plan(handle.job_id).plan
    assert (plan.creates, plan.links, plan.supersessions) == (0, 0, 0)
    approval = world.pubstore.get_approval(world.store.stage(handle.job_id, "import").result["approval_id"])
    assert approval.reason == "auto_safe_plan" and approval.plan_sha256 == plan.plan_sha256
    assert approval.operation_id == handle.job_id and approval.target == world.target
    assert approval.profile.value == "local" and approval.requested_by == approval.approved_by == "local-operator"
    assert approval.executed_as == "f2w-writer"
    assert world.pubstore.pointer(world.target).snapshot_ref == f"{SCOPE}/v1"
    assert counts(world) == before and len(world.client.writes) == n_writes
    # A human approval of the same kind of plan carries no reason, so the two are told apart.
    human = world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    assert world.pubstore.get_approval(world.store.stage(human, "import").result["approval_id"]).reason is None


def test_non_empty_rollback_plan_still_needs_the_human_approval_on_local(auto_world):
    world = auto_world
    world.publish("v1")
    world.publish("v2")
    handle = world.pub.rollback_replacement(world.ctx, world.target, reason="real change")
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = world.pub.current_plan(world.ctx, handle.job_id)
    assert plan.supersessions > 0 or plan.creates > 0
    assert world.pubstore.valid_approvals(handle.job_id, plan) == []
    world.approve(handle.job_id)
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)
    # A plain (non-rollback) publication whose plan is empty is not covered by this decision either.
    again = world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target)
    assert world.work() == (again.job_id, JobState.AWAITING_REVIEW)


def test_noop_rollback_waits_for_a_human_on_any_other_profile(auto_world):
    from app.api.post_extraction import noop_rollback_auto_approver
    from app.modules.security.authorization import DeploymentSettings

    world = auto_world
    als = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target="http://127.0.0.1:59872",
                                                        writer_identity="f2w-writer")
    hook = noop_rollback_auto_approver(als, clock=world.step_clock)
    world.pub.deps.auto_approve = hook
    world.publish("v1")
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target, rollback_reason="back to A")
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = world.pub.current_plan(world.ctx, handle.job_id)
    assert plan.creates == plan.links == plan.supersessions == 0
    assert hook(plan, handle.job_id) is None, "the policy itself refuses off :local"
    assert world.pubstore.valid_approvals(handle.job_id, plan) == []
    world.approve(handle.job_id)  # the human route is unchanged
    assert world.work() == (handle.job_id, JobState.SUCCEEDED)


def test_no_auto_approver_is_wired_by_default(world):
    derive_corpus(world.v1, OTHER)
    world.publish("v1")
    world.finish(world.pub.submit(world.ctx, f"{OTHER}/v1", world.target).job_id)
    handle = world.pub.submit(world.ctx, f"{SCOPE}/v1", world.target, rollback_reason="back to A")
    assert world.work() == (handle.job_id, JobState.AWAITING_REVIEW)
