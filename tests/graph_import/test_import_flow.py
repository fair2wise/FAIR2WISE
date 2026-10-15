"""T08B: plan -> approve -> apply -> verify -> receipt, replay after crashes, supersession (memory + server)."""
import json

import pytest
from import_fakes import snapshot_with_graph, tree_bytes

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import identity_adapter as ida
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.imports.receipts import ReceiptStore, receipt_valid_for


def graph_of(snap):
    return json.loads((snap / "graph.json").read_text(encoding="utf-8"))


def owned(env):
    ents, links = env.scan()
    mine = [e for e in ents if (e.get("properties") or {}).get("f2w_owner") == "f2w-literature"]
    mine_l = [lk for lk in links if (lk.get("properties") or {}).get("f2w_owner") == "f2w-literature"]
    return mine, mine_l


def foreign_fp(env):
    ents, links = env.scan()
    return sorted(json.dumps(r, sort_keys=True) for r in [*ents, *links]
                  if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature")


def test_end_to_end_import_receipt_and_second_import_is_a_noop(env, snap):
    g = graph_of(snap)
    before_snapshot = tree_bytes(snap)
    foreign = foreign_fp(env)
    plan = env.plan(snap)
    assert env.writes() == [] and not env.state_dir.exists()          # dry run writes nothing anywhere
    assert plan.change.creates == len(g["things"]) and plan.change.links == len(g["associations"])
    assert plan.change.target == env.url and plan.change.corpus_scope == ("synthetic",)
    assert plan.change.base_revision == "none" and plan.summary["dangling"] == []
    out = env.apply(plan, env.approve(plan), snap)
    assert out.status == "applied" and out.receipt.receipt.verified
    mine, mine_l = owned(env)
    assert len(mine) == len(g["things"]) and len(mine_l) == len(g["associations"])
    assert foreign_fp(env) == foreign                                  # FORGE sentinels untouched
    r = out.receipt.receipt
    assert r.plan_sha256 == plan.change.plan_sha256 and r.snapshot_sha256 == plan.change.input_sha256
    assert r.executed_as == "f2w-writer" and out.receipt.extras.approved_by == "local-operator"
    assert r.adapter_version == tj.ADAPTER_VERSION and len(r.id_mapping_sha256) == 64
    assert tree_bytes(snap) == before_snapshot                         # source snapshot never mutated
    state = tj.read_state(env.client, plan.detail["graph_id"])
    assert receipt_valid_for(out.receipt, target=env.url, revision=state.revision)
    assert not receipt_valid_for(out.receipt, target="https://tiled-staging.als.lbl.gov", revision=state.revision)
    assert not receipt_valid_for(out.receipt, target=env.url, revision="scan:other")
    n_writes = len(env.writes())
    again = env.plan(snap)
    assert again.is_noop
    out2 = env.apply(again, None, snap)
    assert out2.status == "noop" and out2.receipt.receipt_sha256 == out.receipt.receipt_sha256
    assert len(env.writes()) == n_writes
    assert len(ReceiptStore(env.state_dir / "receipts").all()) == 1


CRASHES = [("entities", 0), ("entities", 5), ("entities_effect", 3), ("after_entities", 0), ("links", 0),
           ("links", 4), ("links_effect", 2), ("after_links", 0), ("before_verify", 0), ("before_receipt", 0)]


@pytest.mark.parametrize("point", CRASHES, ids=[f"{a}-{b}" for a, b in CRASHES])
def test_replay_after_injected_crash_has_no_duplicates(env, snap, point):
    g = graph_of(snap)
    foreign = foreign_fp(env)
    plan = env.plan(snap)
    approval = env.approve(plan)

    def crash(stage, i):
        if (stage, i) == point:
            raise tj.InjectedCrash(point)
    with pytest.raises(tj.InjectedCrash):
        env.apply(plan, approval, snap, fault=crash)
    with pytest.raises(ContractError) as err:                          # the dead owner's lease still holds
        env.apply(plan, approval, snap)
    assert err.value.code is ErrorCode.CONFLICT
    env.clock.advance(1000)
    out = env.apply(plan, approval, snap)
    assert out.status == "applied" and out.resumed and out.receipt.receipt.verified
    mine, mine_l = owned(env)
    assert len(mine) == len(g["things"]) and len(mine_l) == len(g["associations"])
    assert len({e["uri"] for e in mine}) == len(mine)
    assert foreign_fp(env) == foreign
    assert env.plan(snap).is_noop


def test_stale_lease_owner_cannot_write_after_takeover(env, snap):
    plan = env.plan(snap)
    approval = env.approve(plan)
    other = {}

    def takeover(stage, i):
        if (stage, i) == ("entities", 2) and not other:
            env.clock.advance(1000)
            from app.modules.graph.imports.journal import Lease
            lease_file = next(env.state_dir.rglob("lease.json"))
            other["lease"] = Lease(lease_file, env.clock).acquire()
    with pytest.raises(ContractError) as err:
        env.apply(plan, approval, snap, fault=takeover)
    assert err.value.code is ErrorCode.CONFLICT and "lease" in str(err.value)
    assert env.writes().count("createEntity") == 2


def test_dangling_stub_targets_are_reported_not_dropped_silently(env, snap, tmp_path):
    g = graph_of(snap)
    g["associations"].append({"subject": g["things"][0]["id"], "predicate": "rel:RELATED_TO",
                              "object": "matkg:Ghost", "has_evidence": None})
    dang = snapshot_with_graph(snap, tmp_path / "d", "synthetic", "v2", g)
    plan = env.plan(dang)
    assert [d["object"] for d in plan.summary["dangling"]] == ["matkg:Ghost"]
    assert plan.summary["dangling"][0]["missing"] == ["object"]
    out = env.apply(plan, env.approve(plan), dang)
    assert out.receipt.receipt.outcomes["dangling_unimported"] == 1
    assert out.receipt.extras.dangling_unimported == 1
    mine, mine_l = owned(env)
    assert len(mine_l) == len(g["associations"]) - 1


def test_changed_snapshot_uses_create_new_revision_and_retires_old(env, snap, tmp_path):
    plan1 = env.plan(snap)
    env.apply(plan1, env.approve(plan1), snap)
    g = graph_of(snap)
    changed, removed = g["things"][0]["id"], g["things"][-1]["id"]
    g["things"][0]["description"] = "revised description"
    g["things"].pop()
    g["associations"] = [a for a in g["associations"] if removed not in (a["subject"], a["object"])]
    g["associations"][0]["has_evidence"] = "new evidence"
    v2 = snapshot_with_graph(snap, tmp_path / "v2", "synthetic", "v2", g)
    ents_before, _ = env.scan()
    old_id = next(e["id"] for e in ents_before if e["uri"] == ida.entity_uri(plan1.detail["graph_id"], changed))
    plan2 = env.plan(v2)
    kinds = {o.kind for o in plan2.ops}
    assert {"supersede_node", "retire_node", "create_link", "retire_link"} <= kinds
    assert plan2.change.supersessions >= 3
    out = env.apply(plan2, env.approve(plan2), v2)
    assert out.receipt.receipt.verified
    ents, links = env.scan()
    by_id = {e["id"]: e for e in ents}
    assert by_id[old_id]["uri"].split("/")[2] == "retired"             # old revision kept, not deleted
    assert by_id[old_id]["properties"]["description"] != "revised description"
    live = [e for e in ents if ida.parse_entity_uri(e["uri"])]
    assert len([e for e in live if e["properties"].get("f2w_graph_id") == plan2.detail["graph_id"]]) == len(g["things"])
    assert env.plan(v2).is_noop


@pytest.mark.parametrize("point", [("supersede_retired_old", 0), ("entities_effect", 0), ("entities", 1),
                                   ("after_links", 0)])
def test_supersession_replays_from_every_intermediate_state(env, snap, tmp_path, point):
    plan1 = env.plan(snap)
    env.apply(plan1, env.approve(plan1), snap)
    g = graph_of(snap)
    for t in g["things"][:3]:
        t["description"] = "revised " + t["id"]
    v2 = snapshot_with_graph(snap, tmp_path / "v2", "synthetic", "v2", g)
    plan2 = env.plan(v2)
    approval = env.approve(plan2)

    def crash(stage, i):
        if (stage, i) == point:
            raise tj.InjectedCrash(point)
    with pytest.raises(tj.InjectedCrash):
        env.apply(plan2, approval, v2, fault=crash)
    env.clock.advance(1000)
    out = env.apply(plan2, approval, v2)
    assert out.receipt.receipt.verified and out.resumed
    ents, _ = env.scan()
    graph_id = plan2.detail["graph_id"]
    live = [e for e in ents if ida.parse_entity_uri(e["uri"]) and e["properties"].get("f2w_graph_id") == graph_id]
    assert len(live) == len(g["things"]) and len({e["uri"] for e in live}) == len(live)
    assert env.plan(v2).is_noop
