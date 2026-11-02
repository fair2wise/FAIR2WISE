"""T08C: corpus-scoped replacement import (stage, switch, retire, revive) on the memory double and a real server.

Acceptance (plan_addenda A6 T08C; binary cases TIL-09..TIL-14):

* v(N) -> v(N+1) on a seeded server with FORGE links leaves every foreign record byte-identical (TIL-09);
* unchanged nodes keep their Tiled UUIDs (TIL-10);
* boundary links are listed in the plan, inside the plan hash, so a changed list makes the approval stale (TIL-11);
* a crash at every stage and switch point resumes without duplicates (TIL-12);
* F2W readers pinned to the active ledger never observe a mixed graph; type-filtered listings never return
  staged or retired F2W records (TIL-13);
* the release marker names exactly the version F2W's release state reports (TIL-14);
* plus: revive-from-retired restores the same UUIDs; one-time adoption of legacy per-version imports;
  abandon-plan; Q14 foreign change recorded, not fatal; ledger history per scope.
"""
import copy
import json

import pytest
from import_fakes import snapshot_with_graph, tree_bytes

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import identity_adapter as ida
from app.modules.graph import literature_writer as lw
from app.modules.graph.identity_adapter import IdentityLedger
from app.modules.graph.imports import corpus_scope as cs
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.imports.receipts import ReceiptStore, receipt_valid_for

SCOPE = "synthetic"


def graph_of(snap):
    return json.loads((snap / "graph.json").read_text(encoding="utf-8"))


def foreign_rows(env):
    ents, links = env.scan()
    return sorted(json.dumps(r, sort_keys=True) for r in [*ents, *links]
                  if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature")


def live(env, scope=SCOPE):
    """``node_id -> row`` of the live (final) records of ``scope``."""
    ents, _ = env.scan()
    return {ida.parse_entity_uri(e["uri"])[1]: e for e in ents
            if ida.literature_entity_key(e) and ida.literature_entity_key(e)[0] == scope}


def owned(env, scope=SCOPE):
    ents, links = env.scan()
    mine = [e for e in ents if (e.get("properties") or {}).get("f2w_graph_id") == scope]
    mine_l = [lk for lk in links if (lk.get("properties") or {}).get("f2w_graph_id") == scope]
    return mine, mine_l


def make_v2(snap, tmp_path, *, revise=1, remove=1, add=1, name="v2"):
    """A second version: ``revise`` nodes changed, ``remove`` nodes dropped, ``add`` nodes added."""
    g = graph_of(snap)
    things = g["things"]
    revised = [t["id"] for t in things[:revise]]
    for t in things[:revise]:
        t["description"] = f"revised in {name}"
    removed = [t["id"] for t in things[len(things) - remove:]] if remove else []
    g["things"] = [t for t in things if t["id"] not in removed]
    g["associations"] = [a for a in g["associations"] if a["subject"] not in removed and a["object"] not in removed]
    added = []
    for i in range(add):
        nid = f"matkg:Added{name}{i}"
        added.append(nid)
        g["things"].append({"id": nid, "category": "Material", "name": f"Added {i}", "description": "new"})
        g["associations"].append({"subject": g["things"][0]["id"], "predicate": "rel:RELATED_TO", "object": nid,
                                  "has_evidence": None})
    return snapshot_with_graph(snap, tmp_path / name, SCOPE, name, g), {"revised": revised, "removed": removed,
                                                                            "added": added, "graph": g}


# ------------------------------------------------------------------------------- TIL-09 / TIL-10 / TIL-14 ---

def test_first_import_into_the_corpus_scope_writes_marker_and_receipt(env, snap):
    g = graph_of(snap)
    before = tree_bytes(snap)
    foreign = foreign_rows(env)
    plan = env.replan(snap)
    assert env.writes() == [] and not env.state_dir.exists()                 # dry run writes nothing anywhere
    assert plan.scope == SCOPE and plan.change.corpus_scope == (SCOPE,) and plan.change.base_revision == "none"
    kinds = plan.summary["operations"]
    assert kinds["stage_node"] == len(g["things"]) and kinds["promote_node"] == len(g["things"])
    assert kinds["stage_link"] == len(g["associations"]) and kinds.get("switch_link", 0) == 0
    assert kinds["stage_marker"] == 1 and kinds["promote_marker"] == 1 and "retire_marker" not in kinds
    assert plan.is_safe and plan.summary["boundary_links"] == []
    out = env.rapply(plan, env.approve(plan), snap)
    assert out.status == "applied" and out.receipt.receipt.verified
    assert out.receipt.extras.kind == "replacement" and out.receipt.receipt.adapter_version == cs.ADAPTER_VERSION
    mine, mine_l = owned(env)
    finals = live(env)
    assert len(finals) == len(g["things"]) and len(mine_l) == len(g["associations"])
    assert all(e["entityType"] not in (lw.STAGED_TYPE, lw.RETIRED_TYPE) for e in finals.values())
    assert all(e["properties"]["f2w_graph_id"] == SCOPE for e in mine)
    assert foreign_rows(env) == foreign and tree_bytes(snap) == before
    # Release marker (Q5 / TIL-14): names the live version; F2W's release state agrees.
    marker = [e for e in mine if e["uri"] == lw.release_uri(SCOPE)]
    assert len(marker) == 1 and marker[0]["entityType"] == lw.RELEASE_TYPE
    props = marker[0]["properties"]
    assert props["snapshot_id"] == "v1" and props["graph_sha256"] == plan.change.input_sha256
    assert props["plan_sha256"] == plan.change.plan_sha256 and props["receipt_id"] == out.receipt.receipt.receipt_id
    assert out.receipt.extras.release_marker_id == marker[0]["id"]
    rs = cs.ReleaseState(env.scope_root() / "release.json").read()
    assert rs["pointer"] == "live" and rs["live_receipt_id"] == out.receipt.receipt.receipt_id
    assert rs["live_snapshot_id"] == "v1" and rs["switching"] is None
    # Second plan is a noop answered from the receipt without writes.
    n = len(env.writes())
    again = env.replan(snap)
    assert again.is_noop and not again.summary["requires_approval"]
    out2 = env.rapply(again, None, snap)
    assert out2.status == "noop" and out2.receipt.receipt_sha256 == out.receipt.receipt_sha256
    assert len(env.writes()) == n


def test_v1_to_v2_keeps_foreign_records_byte_identical_and_unchanged_uuids(env, snap, tmp_path):
    g1 = graph_of(snap)
    env.replace(snap)
    finals_v1 = live(env)
    uuid_v1 = {n: r["id"] for n, r in finals_v1.items()}
    v2, info = make_v2(snap, tmp_path)
    revised, removed, added = info["revised"][0], info["removed"][0], info["added"][0]
    forge = env.foreign_entity_id()
    kept_node = next(n for n in uuid_v1 if n not in (revised, removed))
    link_on_revised = env.add_foreign_link(forge, uuid_v1[revised])
    link_on_kept = env.add_foreign_link(forge, uuid_v1[kept_node])
    link_from_removed = env.add_foreign_link(uuid_v1[removed], forge, "forge:about")
    foreign = foreign_rows(env)
    plan = env.replan(v2)
    # TIL-11: boundary links (foreign links on records we retire) are listed and bound into the hash.
    boundary = {(b["link_id"], b["node_id"], b["reason"], b["direction"]) for b in plan.summary["boundary_links"]}
    assert boundary == {(link_on_revised, revised, "revised", "in"), (link_from_removed, removed, "removed", "out")}
    assert plan.summary["requires_approval"] and not plan.is_safe
    kinds = plan.summary["operations"]
    assert kinds["stage_node"] == 2 and kinds["retire_node"] == 2 and kinds["promote_node"] == 2
    assert kinds["retire_marker"] == 1 and kinds["stage_marker"] == 1
    assert plan.summary["unchanged"]["unchanged_entities"] == len(g1["things"]) - 2
    out = env.rapply(plan, env.approve(plan), v2)
    assert out.status == "applied" and out.receipt.receipt.verified
    # TIL-09: every foreign record (FORGE entities and links, including those on retired records) is byte-identical.
    assert foreign_rows(env) == foreign
    assert out.receipt.extras.foreign_changes == [] and out.receipt.extras.boundary_links == 2
    # TIL-10: unchanged nodes keep their UUIDs; the revised node got a new one; the old is retired, not deleted.
    finals_v2 = live(env)
    for n, tid in uuid_v1.items():
        if n not in (revised, removed):
            assert finals_v2[n]["id"] == tid
    assert finals_v2[revised]["id"] != uuid_v1[revised] and finals_v2[revised]["properties"]["description"] == \
        "revised in v2"
    assert removed not in finals_v2 and added in finals_v2
    ents, _ = env.scan()
    by_id = {e["id"]: e for e in ents}
    for old in (uuid_v1[revised], uuid_v1[removed]):
        assert by_id[old]["entityType"] == lw.RETIRED_TYPE and f"/{lw.RETIRED}/" in by_id[old]["uri"]
        assert by_id[old]["uri"].endswith("/" + plan.tag)
    # The I7 probes: every other reader's type listing and other scopes read the same before and after.
    assert out.receipt.extras.read_probes["equal"] is True
    assert env.replan(v2).is_noop
    # Ledger history maps every UUID ever issued to its node and state.
    history = cs.LedgerHistory(env.scope_root() / "ledger_history.jsonl")
    assert [r["snapshot_id"] for r in history.records()] == ["v1", "v2"]
    assert {h["state"] for h in history.lookup(uuid_v1[revised])} == {"live", "retired"}
    assert history.lookup(finals_v2[revised]["id"]) == [{"node_id": revised, "receipt_id": out.receipt.receipt.receipt_id,
                                                         "snapshot_id": "v2", "state": "live"}]
    assert history.ledger_for(out.receipt.receipt.receipt_id).tiled_id(SCOPE, kept_node) == uuid_v1[kept_node]


def test_changed_boundary_links_make_the_approval_stale(env, snap, tmp_path):
    env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    v2, info = make_v2(snap, tmp_path)
    plan = env.replan(v2)
    approval = env.approve(plan)
    # FORGE links to the node we are about to retire after the plan was approved: the boundary report changes,
    # so the hash changes and the approval no longer covers the plan (STALE_APPROVAL at apply).
    env.add_foreign_link(env.foreign_entity_id(), uuid_v1[info["revised"][0]])
    replanned = env.replan(v2)
    assert replanned.change.plan_sha256 != plan.change.plan_sha256
    assert len(replanned.summary["boundary_links"]) == 1 and plan.summary["boundary_links"] == []
    with pytest.raises(ContractError) as err:
        env.rapply(plan, approval, v2)
    assert err.value.code is ErrorCode.STALE_APPROVAL
    assert env.writes().count("createEntity") == len(graph_of(snap)["things"]) + 1   # nothing new was written
    with pytest.raises(ContractError) as err:
        env.rapply(replanned, approval, v2)                       # the old approval does not cover the new hash
    assert err.value.code is ErrorCode.STALE_APPROVAL
    # Editing the boundary report out of an approved plan is refused too.
    forged = cs.ReplacementPlan(replanned.change, {**replanned.detail, "boundary_links": []})
    with pytest.raises(ContractError) as err:
        env.rapply(forged, env.approve(replanned), v2)
    assert err.value.code is ErrorCode.STALE_APPROVAL


# ------------------------------------------------------------------------------- TIL-12: crash matrix -------

CRASHES = [("stage", 0), ("stage", 3), ("stage_effect", 1), ("stage", 6), ("after_stage", 0), ("verify_staged", 0),
           ("switch", 0), ("switch_effect", 0), ("switch", 1), ("switch", 2), ("switch_effect", 3), ("switch", 5),
           ("switch", 8), ("switch_effect", 9), ("before_verify", 0), ("before_receipt", 0), ("before_history", 0)]


@pytest.mark.parametrize("point", CRASHES, ids=[f"{a}-{b}" for a, b in CRASHES])
def test_crash_at_every_stage_and_switch_point_resumes_without_duplicates(env, snap, tmp_path, point):
    env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    v2, info = make_v2(snap, tmp_path, revise=2, remove=1, add=2)
    env.add_foreign_link(env.foreign_entity_id(), uuid_v1[info["revised"][0]])
    foreign = foreign_rows(env)
    plan = env.replan(v2)
    switch_ops = [o for o in plan.ops if o.phase == "switch"]
    assert len(switch_ops) > 9, "the crash matrix must cover every switch step kind"
    approval = env.approve(plan)
    hit = []

    def crash(stage, i):
        if (stage, i) == point and not hit:
            hit.append(point)
            raise cs.InjectedCrash(point)
    with pytest.raises(cs.InjectedCrash):
        env.rapply(plan, approval, v2, fault=crash)
    with pytest.raises(ContractError) as err:                          # the dead owner's lease still holds
        env.rapply(plan, approval, v2)
    assert err.value.code is ErrorCode.CONFLICT
    env.clock.advance(1000)
    out = env.rapply(plan, approval, v2)
    assert out.status in ("applied", "already_applied") and out.resumed and out.receipt.receipt.verified
    g2 = info["graph"]
    finals = live(env)
    mine, mine_l = owned(env)
    assert len(finals) == len(g2["things"]) and len(mine_l) == len(g2["associations"])
    assert len({e["uri"] for e in mine}) == len(mine), "no duplicate URIs anywhere in the scope"
    assert sum(1 for e in mine if e["uri"] == lw.release_uri(SCOPE)) == 1
    assert not [e for e in mine if e["entityType"] == lw.STAGED_TYPE], "nothing of the plan stays staged"
    for n, tid in uuid_v1.items():
        if n not in info["revised"] and n not in info["removed"]:
            assert finals[n]["id"] == tid
    assert foreign_rows(env) == foreign
    assert env.replan(v2).is_noop
    rs = cs.ReleaseState(env.scope_root() / "release.json").read()
    assert rs["switching"] is None and rs["live_snapshot_id"] == "v2"
    assert len(ReceiptStore(env.state_dir / "receipts").all()) == 2


def test_stale_lease_owner_cannot_write_after_takeover(env, snap):
    plan = env.replan(snap)
    approval = env.approve(plan)
    other = {}

    def takeover(stage, i):
        if (stage, i) == ("stage", 2) and not other:
            env.clock.advance(1000)
            from app.modules.graph.imports.journal import Lease
            other["lease"] = Lease(env.scope_root() / "lease.json", env.clock).acquire()
    with pytest.raises(ContractError) as err:
        env.rapply(plan, approval, snap, fault=takeover)
    assert err.value.code is ErrorCode.CONFLICT and "lease" in str(err.value)
    assert env.writes().count("createEntity") == 2


# ------------------------------------------------------------------------------- TIL-13: pinned reads --------

def test_pinned_reader_never_sees_a_mixed_graph_and_type_listings_hide_staged_and_retired(env, snap, tmp_path):
    g1 = graph_of(snap)
    _plan1, out1 = env.replace(snap)
    finals_v1 = live(env)
    ledger_v1 = IdentityLedger.from_json(json.loads((env.scope_root() / "plans" / _plan1.tag / "ledger.json")
                                                    .read_text()))
    v2, info = make_v2(snap, tmp_path, revise=3, remove=2, add=2)
    plan = env.replan(v2)
    approval = env.approve(plan)
    switch_ops = [o for o in plan.ops if o.phase == "switch"]
    mid = len(switch_ops) // 2
    seen = {}

    def crash(stage, i):
        if stage == "switch" and i == mid:
            # Half-way through the switch: what does each reader see?
            seen["raw"] = live(env)
            seen["pinned"] = cs.PinnedReader(env.client, SCOPE, ledger_v1).entities()
            seen["release_state"] = cs.ReleaseState(env.scope_root() / "release.json").read()
            raise cs.InjectedCrash((stage, i))
    with pytest.raises(cs.InjectedCrash):
        env.rapply(plan, approval, v2, fault=crash)
    raw = seen["raw"]
    assert seen["release_state"]["switching"] == plan.tag, "readers can tell a switch is in flight"
    assert seen["release_state"]["live_receipt_id"] == out1.receipt.receipt.receipt_id
    assert set(raw) != set(finals_v1) and set(raw) != set(n["id"] for n in info["graph"]["things"]), \
        "the raw Tiled view is a mix of v1 and v2 at this point"
    pinned = {r["id"]: r for r in seen["pinned"]}
    assert set(pinned) == set(finals_v1), "the pinned reader shows exactly v1's nodes"
    for node in g1["things"]:
        rec = pinned[node["id"]]
        assert rec["tiled_id"] == finals_v1[node["id"]]["id"] and rec["pinned"] is True
        assert rec["data"].get("description") == node.get("description"), "v1 content, even for retired rows"
        assert rec["category"] == (node.get("category") or ""), "the real category, never f2w:Retired"
    # Type-filtered listings (what other readers use) never return staged or retired F2W records.
    ents, _ = env.scan()
    hidden = {e["id"] for e in ents if e["entityType"] in (lw.STAGED_TYPE, lw.RETIRED_TYPE)}
    assert hidden, "the half-switched scope has staged and retired records"
    for entity_type in sorted({t["category"] for t in g1["things"]}):
        listed = {e["id"] for e in env.client.scan_entities(entity_type=entity_type)}
        assert not (listed & hidden)
    # Resume completes the switch; after it the pinned reader of the new receipt sees exactly v2.
    env.clock.advance(1000)
    out2 = env.rapply(plan, approval, v2)
    assert out2.receipt.receipt.verified
    ledger_v2 = cs.LedgerHistory(env.scope_root() / "ledger_history.jsonl").ledger_for(out2.receipt.receipt.receipt_id)
    assert {r["id"] for r in cs.PinnedReader(env.client, SCOPE, ledger_v2).entities()} == \
        {t["id"] for t in info["graph"]["things"]}
    assert cs.PinnedReader(env.client, SCOPE, ledger_v2).get_entity("matkg:Nope") is None
    assert cs.ReleaseState(env.scope_root() / "release.json").switching is None


# ------------------------------------------------------------------------------- revive / adoption / abandon ---

def test_rollback_revives_retired_records_with_their_original_uuids(env, snap, tmp_path):
    g1 = graph_of(snap)
    env.replace(snap)
    uuid_v1 = {n: r["id"] for n, r in live(env).items()}
    v2, info = make_v2(snap, tmp_path, revise=2, remove=1, add=1)
    env.replace(v2)
    uuid_v2 = {n: r["id"] for n, r in live(env).items()}
    back = env.replan(snap)                                   # roll back: v1 is the source again
    kinds = back.summary["operations"]
    assert kinds["revive_node"] == 3 and "stage_node" not in kinds   # 2 revised + 1 removed come back as they were
    assert kinds["retire_node"] == 3                                 # 2 v2 revisions + the added node
    assert back.summary["unchanged"]["revived_entities"] == 3
    n_create = env.writes().count("createEntity")
    out = env.rapply(back, env.approve(back), snap)
    assert out.receipt.receipt.verified and out.receipt.receipt.outcomes["revived_entities"] == 3
    assert env.writes().count("createEntity") == n_create + 1        # only the new release marker
    finals = live(env)
    assert {n: r["id"] for n, r in finals.items()} == uuid_v1, "every v1 UUID is live again"
    assert len(finals) == len(g1["things"])
    ents, _ = env.scan()
    by_id = {e["id"]: e for e in ents}
    for n in info["revised"] + info["added"]:
        assert by_id[uuid_v2[n]]["entityType"] == lw.RETIRED_TYPE
    # Roll forward again: v2's own retired revisions are revived, same UUIDs as the first v2.
    forward = env.replan(v2)
    assert forward.summary["operations"]["revive_node"] == 3
    env.rapply(forward, env.approve(forward), v2)
    assert {n: r["id"] for n, r in live(env).items()} == uuid_v2


def test_legacy_per_version_import_needs_adoption_and_is_retired_into_the_scope_once(env, snap, tmp_path):
    # The T08B per-version importer wrote graph id "synthetic_v1" (P8).
    plan_old = env.plan(snap)
    env.apply(plan_old, env.approve(plan_old), snap)
    legacy_gid = plan_old.detail["graph_id"]
    assert legacy_gid == "synthetic_v1"
    legacy_rows = {n: r for n, r in live(env, legacy_gid).items()}
    forge_link = env.add_foreign_link(env.foreign_entity_id(), next(iter(legacy_rows.values()))["id"])
    foreign = foreign_rows(env)
    with pytest.raises(ContractError) as err:
        env.replan(snap)
    assert err.value.code is ErrorCode.CONFLICT and "adopt_legacy" in str(err.value)
    plan = env.replan(snap, adopt_legacy=True)
    assert plan.detail["adopted_graph_ids"] == [legacy_gid] and not plan.is_safe
    assert [b["reason"] for b in plan.summary["boundary_links"]] == ["adopted"]
    assert plan.summary["boundary_links"][0]["link_id"] == forge_link
    out = env.rapply(plan, env.approve(plan), snap)
    assert out.receipt.receipt.verified and out.receipt.extras.adopted_graph_ids == [legacy_gid]
    assert foreign_rows(env) == foreign                      # FORGE's link to the legacy record is untouched
    assert live(env, legacy_gid) == {}                        # nothing of the legacy graph is live
    ents, links = env.scan()
    by_id = {e["id"]: e for e in ents}
    for row in legacy_rows.values():
        assert by_id[row["id"]]["entityType"] == lw.RETIRED_TYPE and f"/{legacy_gid}/retired/" in by_id[row["id"]]["uri"]
    assert not [lk for lk in links if (lk.get("properties") or {}).get("f2w_graph_id") == legacy_gid]
    assert len(live(env)) == len(graph_of(snap)["things"])
    assert env.replan(snap).is_noop and tj.plan_import(snap, client=env.client, settings=env.settings,
                                                        supported_schema_fingerprints=env.fingerprints) \
        .change.creates == len(graph_of(snap)["things"])  # the old importer would start over; it is not used


def test_abandon_plan_deletes_only_its_own_staged_records_and_refuses_after_the_switch_started(env, snap, tmp_path):
    env.replace(snap)
    v2, info = make_v2(snap, tmp_path, revise=2, remove=0, add=1)
    plan = env.replan(v2)
    approval = env.approve(plan)
    foreign = foreign_rows(env)
    before = live(env)

    def crash(stage, i):
        if (stage, i) == ("after_stage", 0):
            raise cs.InjectedCrash(stage)
    with pytest.raises(cs.InjectedCrash):
        env.rapply(plan, approval, v2, fault=crash)
    ents, _ = env.scan()
    staged = [e for e in ents if e["entityType"] == lw.STAGED_TYPE]
    assert len(staged) == 4                                   # 2 revised + 1 added + the marker
    # One staged record acquires a foreign link in the meantime: it must be skipped, not deleted (I3).
    linked = next(e for e in staged if e["properties"].get("f2w_id") == info["added"][0])
    env.add_foreign_link(env.foreign_entity_id(), linked["id"])
    env.clock.advance(1000)
    report = env.abandon(plan)
    assert report["deleted_entities"] == 3 and report["skipped"] == [
        {"node_id": info["added"][0], "entity_id": linked["id"], "foreign_links": report["skipped"][0]["foreign_links"]}]
    ents, _ = env.scan()
    assert [e["id"] for e in ents if e["entityType"] == lw.STAGED_TYPE] == [linked["id"]]
    assert live(env) == before and foreign_rows(env) != foreign   # only the extra FORGE link differs
    # After a switch has started, abandon is refused: the plan must be resumed.
    plan2 = env.replan(v2)
    approval2 = env.approve(plan2)

    def crash2(stage, i):
        if (stage, i) == ("switch", 1):
            raise cs.InjectedCrash(stage)
    with pytest.raises(cs.InjectedCrash):
        env.rapply(plan2, approval2, v2, fault=crash2)
    env.clock.advance(1000)
    with pytest.raises(ContractError) as err:
        env.abandon(plan2)
    assert err.value.code is ErrorCode.CONFLICT and "resume" in str(err.value)
    out = env.rapply(plan2, approval2, v2)
    assert out.receipt.receipt.verified and env.replan(v2).is_noop


def test_foreign_change_during_the_run_is_recorded_in_the_receipt_not_fatal(env, snap):
    """Q14 (owner, revised): a foreign record changing under a run never stops it; the receipt records it."""
    forge = env.foreign_entity_id()
    plan = env.replan(snap)
    approval = env.approve(plan)

    def meddle(stage, i):
        if (stage, i) == ("after_stage", 0):
            env.rename_foreign_entity(forge, "renamed by FORGE during the run")
    out = env.rapply(plan, approval, snap, fault=meddle)
    assert out.status == "applied" and out.receipt.receipt.verified
    assert out.receipt.extras.foreign_changes == [f"changed:e:{forge}"]
    assert out.receipt.receipt.outcomes["foreign_changes"] == 1
    assert out.receipt.extras.read_probes["equal"] is True   # a rename changes no listing membership
    state = cs.read_scope_state(env.client, SCOPE)
    assert receipt_valid_for(out.receipt, target=env.url, revision=state.revision)


def test_refusals_write_nothing(env, snap, tmp_path):
    plan = env.replan(snap)
    with pytest.raises(ContractError) as err:
        env.rapply(plan, None, snap)
    assert err.value.code is ErrorCode.DENIED
    v2, _info = make_v2(snap, tmp_path)
    other = env.replan(v2)
    with pytest.raises(ContractError) as err:                 # approval for another plan
        env.rapply(plan, env.approve(other), snap)
    assert err.value.code is ErrorCode.STALE_APPROVAL
    with pytest.raises(ContractError) as err:                 # a modified plan detail
        env.rapply(cs.ReplacementPlan(plan.change, {**plan.detail, "ops": plan.detail["ops"][:-1]}),
                   env.approve(plan), snap)
    assert err.value.code is ErrorCode.STALE_APPROVAL
    from app.modules.contracts.context import RequestContext
    ctx = env.ctx
    weak = RequestContext(profile=ctx.profile, mode=ctx.mode, principal=ctx.principal, request_id="x",
                          capabilities=frozenset({"chat.read_support"}))
    with pytest.raises(ContractError) as err:
        cs.apply_replacement(weak, plan, env.approve(plan), source=snap, client=env.client, settings=env.settings,
                             supported_schema_fingerprints=env.fingerprints, state_dir=env.state_dir,
                             clock=env.clock)
    assert err.value.code is ErrorCode.DENIED
    assert env.writes() == [] and not (env.state_dir / "receipts").exists()
    # The plan serializes and verifies; a stranger's edit is caught.
    again = cs.ReplacementPlan.from_json(json.loads(json.dumps(plan.to_json())))
    again.verify_integrity()
    assert again.change == plan.change and again.tag == plan.tag
