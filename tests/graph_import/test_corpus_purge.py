"""T08D: guarded purge and corpus removal (memory double and disposable Tiled 0.2.18).

Acceptance (plan_addenda A6 T08D; binary cases TIL-15, TIL-16):

* a retired record with any foreign link is skipped and reported, never deleted; no foreign link is deleted;
* other scopes are untouched; the committed JSON is untouched;
* retire-all needs the scope typed in, points the release state (and the publication pointer) to none;
* re-import from the committed JSON restores the corpus after a removal and a purge;
* purge has its own approval: a replacement approval never covers it; the previous version is kept by default.
"""
import json

import pytest
from import_fakes import snapshot_with_graph, tree_bytes
from test_corpus_replacement import SCOPE, foreign_rows, graph_of, live, make_v2, owned

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import edit_journal as ej
from app.modules.graph import literature_writer as lw
from app.modules.graph.imports import corpus_purge as cp
from app.modules.graph.imports import corpus_scope as cs
from app.modules.security.authorization import create_approval

OTHER = "othercorp"


def plan_purge(env, **kw):
    return cp.plan_purge(client=env.client, settings=env.settings, scope=SCOPE, state_dir=env.state_dir, **kw)


def apply_purge(env, plan, approval, **kw):
    return cp.apply_purge(env.ctx, plan, approval, client=env.client, settings=env.settings,
                          state_dir=env.state_dir, clock=env.clock, **kw)


def plan_retire(env, confirm=SCOPE, **kw):
    return cp.plan_retire_all(client=env.client, settings=env.settings, scope=SCOPE, confirm_scope=confirm, **kw)


def apply_retire(env, plan, approval, **kw):
    return cp.apply_retire_all(env.ctx, plan, approval, client=env.client, settings=env.settings,
                               state_dir=env.state_dir, clock=env.clock, **kw)


def other_scope(env, snap, tmp_path):
    """A second corpus in the same Tiled, so 'other scopes untouched' is checked against real records."""
    g = graph_of(snap)
    g["things"] = g["things"][:4]
    keep = {t["id"] for t in g["things"]}
    g["associations"] = [a for a in g["associations"] if a["subject"] in keep and a["object"] in keep]
    other = snapshot_with_graph(snap, tmp_path / "other", OTHER, "v1", g)
    env.replace(other)
    return other


def retired_ids(env):
    ents, _ = env.scan()
    return {e["id"] for e in ents if e["entityType"] == lw.RETIRED_TYPE}


# ------------------------------------------------------------------------------- TIL-15: purge ---------------

def test_purge_skips_records_with_foreign_links_deletes_the_rest_and_keeps_the_previous_version(env, snap, tmp_path):
    other_scope(env, snap, tmp_path)
    env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    v2, info = make_v2(snap, tmp_path, revise=2, remove=1, add=0)
    env.replace(v2)                                   # v1's 2 revised + 1 removed + marker are now retired
    v3, info3 = make_v2(snap, tmp_path, revise=1, remove=0, add=1, name="v3", base=info["graph"])
    _plan3, out3 = env.replace(v3)                    # v2's node0 revision and marker: "the previous version"
    linked = uuid_v1[info["removed"][0]]
    forge_link = env.add_foreign_link(env.foreign_entity_id(), linked)
    foreign = foreign_rows(env)
    json_before = tree_bytes(snap.parents[1])
    other_before = owned(env, OTHER)
    plan = plan_purge(env)
    s = plan.summary
    live_tag = out3.receipt.receipt.plan_sha256[:16]
    ents, _ = env.scan()
    retired = {e["id"]: e for e in ents if e["entityType"] == lw.RETIRED_TYPE
               and e["properties"].get("f2w_graph_id") == SCOPE}
    previous = {i for i, e in retired.items() if e["uri"].endswith("/" + live_tag)}   # retired by the live plan
    assert s["live_plan_tag"] == live_tag and s["kept_previous_version"] == len(previous) == 2
    assert set(s["delete"]) == set(retired) - previous - {linked}
    assert set(s["delete"]) == {uuid_v1[n] for n in info["revised"]} | {_marker_retired_by(env)}
    assert [x["entity_id"] for x in s["skip_foreign_link"]] == [linked]
    assert s["skip_foreign_link"][0]["foreign_links"][0]["link_id"] == forge_link
    # Its own approval: the replacement's approval does not cover it.
    with pytest.raises(ContractError) as err:
        apply_purge(env, plan, env.approve(_plan3))
    assert err.value.code is ErrorCode.STALE_APPROVAL
    approval = env.approve(plan)
    out = apply_purge(env, plan, approval)
    assert out.status == "applied" and out.receipt.extras.kind == "purge"
    assert out.receipt.receipt.outcomes["deleted_entities"] == len(s["delete"])
    assert out.receipt.receipt.outcomes["skipped"] == 1
    assert out.receipt.extras.read_probes["skipped"][0]["entity_id"] == linked
    ents, links = env.scan()
    ids = {e["id"] for e in ents}
    assert linked in ids and not any(i in ids for i in s["delete"])
    assert forge_link in {lk["id"] for lk in links}, "no foreign link was deleted"
    assert foreign_rows(env) == foreign and out.receipt.extras.foreign_changes == []
    assert owned(env, OTHER) == other_before and tree_bytes(snap.parents[1]) == json_before
    assert set(live(env)) == {t["id"] for t in info3["graph"]["things"]}, "the live version is untouched"
    assert previous <= {e["id"] for e in ents}, "the previous version is kept"
    # The previous version is still revivable: rolling back to v2 revives rather than creates.
    back = env.replan(v2)
    assert back.summary["operations"].get("revive_node", 0) >= 1
    # Re-planning the purge: only the skipped record remains a candidate (still skipped), so it is a noop.
    again = plan_purge(env)
    assert again.is_noop and [x["entity_id"] for x in again.summary["skip_foreign_link"]] == [linked]


def _marker_retired_by(env):
    """UUID of the scope's v1 release marker (retired when v2 switched in)."""
    ents, _ = env.scan()
    return next(e["id"] for e in ents if e["properties"].get("f2w_id") == lw.RELEASE_NODE
                and e["properties"].get("f2w_graph_id") == SCOPE and e["properties"].get("snapshot_id") == "v1"
                and e["entityType"] == lw.RETIRED_TYPE)


# A retired record never carries owned links (they are deleted when it is retired), so the purge's
# "owned links first" step is defensive and has no fault point here.
@pytest.mark.parametrize("point", [("purge", 0), ("purge_effect", 0), ("purge", 2), ("purge_effect", 4),
                                   ("before_verify", 0), ("before_receipt", 0)])
def test_purge_crash_resumes_without_touching_anything_else(env, snap, tmp_path, point):
    env.replace(snap)
    v2, _info = make_v2(snap, tmp_path, revise=3, remove=1, add=0)
    env.replace(v2)
    plan = plan_purge(env, include_previous=True)
    assert len(plan.summary["delete"]) == 5                    # 3 revised + 1 removed + the v1 marker
    approval = env.approve(plan)
    foreign = foreign_rows(env)

    def crash(stage, i):
        if stage == point[0] and point[1] in (None, i):
            raise cs.InjectedCrash(point)
    with pytest.raises(cs.InjectedCrash):
        apply_purge(env, plan, approval, fault=crash)
    env.clock.advance(1000)
    out = apply_purge(env, plan, approval)
    assert out.status in ("applied", "already_applied") and out.receipt.receipt.verified
    assert retired_ids(env) == set() and foreign_rows(env) == foreign
    assert len(live(env)) == len(graph_of(v2)["things"])


def test_purge_refuses_a_candidate_that_gained_a_foreign_link_after_planning(env, snap, tmp_path):
    env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    v2, info = make_v2(snap, tmp_path, revise=1, remove=0, add=0)
    env.replace(v2)
    plan = plan_purge(env, include_previous=True)
    target = uuid_v1[info["revised"][0]]
    assert target in plan.summary["delete"]
    approval = env.approve(plan)
    env.add_foreign_link(env.foreign_entity_id(), target)     # FORGE links to the retired record meanwhile
    out = apply_purge(env, plan, approval)
    ents, _ = env.scan()
    assert target in {e["id"] for e in ents}, "I3 holds at the moment of deletion"
    skipped = out.receipt.extras.read_probes["skipped"]
    assert [s["reason"] for s in skipped] == ["foreign_link_at_apply"]


# ------------------------------------------------------------------------------- TIL-16: corpus removal -----

def test_retire_all_needs_the_scope_typed_in_blocks_on_open_edits_and_points_to_none(env, snap, tmp_path):
    other_scope(env, snap, tmp_path)
    _plan1, out1 = env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    forge_link = env.add_foreign_link(env.foreign_entity_id(), next(iter(uuid_v1.values())))
    foreign = foreign_rows(env)
    other_before = owned(env, OTHER)
    json_before = tree_bytes(snap.parents[1])
    with pytest.raises(ContractError) as err:
        plan_retire(env, confirm="synthetic_typo")
    assert err.value.code is ErrorCode.INVALID_INPUT
    # An open (acknowledged) edit blocks the removal unless resolved as discarded.
    journal = ej.EditJournal(tmp_path / "edits.jsonl")
    journal.append(ej.EditRecord(edit_id="e1", seq=1, state="acknowledged", target=env.url, scope=(SCOPE,),
                                 base_snapshot_id="v1", base_graph_sha256=_plan1.change.input_sha256,
                                 profile="local", mode="local_trusted", actor="local-operator",
                                 operation="node_patch", ids=(next(iter(uuid_v1)),), recorded_at=env.clock()))
    with pytest.raises(ContractError) as err:
        plan_retire(env, edit_journal=journal)
    assert err.value.code is ErrorCode.CONFLICT and "discarded" in str(err.value)
    carried = ej.ConflictResolution(edit_id="e1", decision="carried", reviewed_by="x", reviewed_at=env.clock(),
                                    note="n")
    with pytest.raises(ContractError):
        plan_retire(env, edit_journal=journal, resolutions=[carried])
    discarded = ej.ConflictResolution(edit_id="e1", decision="discarded", reviewed_by="x", reviewed_at=env.clock(),
                                      note="dropping with the corpus")
    plan = plan_retire(env, edit_journal=journal, resolutions=[discarded])
    k = plan.summary["operations"]
    assert k["retire_node"] == len(uuid_v1) and k["retire_marker"] == 1 and k["retire_link"] > 0
    assert [b["link_id"] for b in plan.summary["boundary_links"]] == [forge_link]
    pointer_calls = []
    out = apply_retire(env, plan, env.approve(plan), pointer_to_none=lambda: pointer_calls.append(1))
    assert out.status == "applied" and out.receipt.extras.kind == "retire-all" and pointer_calls == [1]
    assert live(env) == {} and foreign_rows(env) == foreign
    ents, links = env.scan()
    assert forge_link in {lk["id"] for lk in links}
    assert all(e["entityType"] == lw.RETIRED_TYPE for e in ents
               if (e.get("properties") or {}).get("f2w_graph_id") == SCOPE)
    assert owned(env, OTHER) == other_before and tree_bytes(snap.parents[1]) == json_before
    rs = cs.ReleaseState(env.scope_root() / "release.json").read()
    assert rs["pointer"] == "none" and rs["live_receipt_id"] is None and rs["switching"] is None
    assert rs["retired_by_receipt_id"] == out.receipt.receipt.receipt_id
    assert out1.receipt.receipt.receipt_id in {h["receipt_id"] for h in cs.LedgerHistory(
        env.scope_root() / "ledger_history.jsonl").lookup(next(iter(uuid_v1.values())))}
    # Undo before a purge: a replacement from the committed JSON revives everything with the same UUIDs.
    revive = env.replan(snap)
    assert revive.summary["operations"]["revive_node"] == len(uuid_v1) and "stage_node" not in \
        revive.summary["operations"]
    env.rapply(revive, env.approve(revive), snap)
    assert {n: r["id"] for n, r in live(env).items()} == uuid_v1


@pytest.mark.parametrize("point", [("switch", 0), ("switch_effect", 2), ("switch", 7), ("before_verify", 0),
                                   ("before_receipt", 0)])
def test_retire_all_crash_resumes(env, snap, point):
    env.replace(snap)
    plan = plan_retire(env)
    approval = env.approve(plan)

    def crash(stage, i):
        if (stage, i) == point:
            raise cs.InjectedCrash(point)
    with pytest.raises(cs.InjectedCrash):
        apply_retire(env, plan, approval, fault=crash)
    env.clock.advance(1000)
    out = apply_retire(env, plan, approval)
    assert out.status in ("applied", "already_applied") and live(env) == {}
    assert cs.ReleaseState(env.scope_root() / "release.json").read()["pointer"] == "none"


def test_remove_corpus_purge_everything_and_reimport_from_git_restores_it(env, snap, tmp_path):
    other_scope(env, snap, tmp_path)
    g1 = graph_of(snap)
    env.replace(snap)
    foreign = foreign_rows(env)
    other_before = owned(env, OTHER)
    json_before = tree_bytes(snap.parents[1])
    plan = plan_retire(env)
    apply_retire(env, plan, env.approve(plan))
    purge = plan_purge(env, include_previous=True)
    assert len(purge.summary["delete"]) == len(g1["things"]) + 1 and purge.summary["skip_foreign_link"] == []
    out = apply_purge(env, purge, env.approve(purge))
    assert out.receipt.receipt.outcomes["deleted_entities"] == len(g1["things"]) + 1
    mine, mine_l = owned(env)
    assert mine == [] and mine_l == []
    assert foreign_rows(env) == foreign and owned(env, OTHER) == other_before
    assert tree_bytes(snap.parents[1]) == json_before, "the committed JSON is never touched"
    # Re-import from the committed JSON: the corpus is back (new UUIDs: nothing was left to revive).
    again, out2 = env.replace(snap)
    assert again.summary["operations"]["stage_node"] == len(g1["things"]) and out2.receipt.receipt.verified
    assert len(live(env)) == len(g1["things"])
    assert cs.ReleaseState(env.scope_root() / "release.json").read()["pointer"] == "live"


def test_purge_approval_is_bound_to_the_candidate_list(env, snap, tmp_path):
    env.replace(snap)
    v2, _info = make_v2(snap, tmp_path, revise=1, remove=0, add=0)
    env.replace(v2)
    plan = plan_purge(env, include_previous=True)
    approval = env.approve(plan)
    forged = cp.PurgePlan(plan.change, {**plan.detail, "candidates": plan.detail["candidates"][:1]})
    with pytest.raises(ContractError) as err:
        apply_purge(env, forged, approval)
    assert err.value.code is ErrorCode.STALE_APPROVAL
    with pytest.raises(ContractError) as err:
        apply_purge(env, plan, None)
    assert err.value.code is ErrorCode.DENIED
    roundtrip = cp.PurgePlan.from_json(json.loads(json.dumps(plan.to_json())))
    roundtrip.verify_integrity()
    # The approval record itself is the standard one (curation.approve, human context), nothing bespoke.
    assert approval.plan_sha256 == plan.change.plan_sha256 and approval.approved_by == "local-operator"
    assert create_approval is not None
