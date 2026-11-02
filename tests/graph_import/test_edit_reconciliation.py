"""T23C: full-lineage check, reconciled edits protect rollbacks (F4/B26, Q15), journal replay, idempotent ids.

Binary-case equivalents: TIL-17 (rollback below a reconciliation snapshot needs a reviewed resolution) and
JSON-10 (a rebuild keeps every edit or records a conflict).
"""
import json
from datetime import datetime, timezone, UTC

import pytest
from import_fakes import snapshot_with_graph

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import edit_journal as ej
from app.modules.graph import edit_replay as er
from app.modules.graph.imports import tiled_json as tj

NOW = datetime(2026, 11, 2, tzinfo=UTC)


def graph_of(snap):
    return json.loads((snap / "graph.json").read_text(encoding="utf-8"))


def child(base, root, sid, parent, graph, relation):
    m = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    return snapshot_with_graph(base, root, "synthetic", sid, graph, patch_manifest={"parent": {
        "snapshot_id": parent, "graph_sha256": m["graph"]["sha256"], "relation": relation}})


def rec(edit_id="e1", state="reconciled", reconciled_in="v2", node="n1", ids=None, before=None, after=None,
        op="node_patch", at=NOW):
    return ej.EditRecord(edit_id=edit_id, seq=1, state=state, target="T", scope=("synthetic",),
                         base_snapshot_id="v1", base_graph_sha256="0" * 64, profile="local", mode="local_trusted",
                         actor="local-operator", operation=op, ids=tuple(ids or (node,)),
                         before=before or {}, after=after or {}, recorded_at=at, reconciled_in=reconciled_in)


@pytest.fixture()
def chain(snap, tmp_path):
    """v1 <- v2 (edit_reconciliation) <- v3 (rebuild) <- v4 (rebuild), siblings in one storage tree."""
    g = graph_of(snap)
    v2 = child(snap, tmp_path, "v2", "v1", g, "edit_reconciliation")
    v3 = child(v2, tmp_path, "v3", "v2", g, "rebuild")
    v4 = child(v3, tmp_path, "v4", "v3", g, "rebuild")
    return {"v1": snap, "v2": v2, "v3": v3, "v4": v4}


def journal_with(env, tmp_path, state="reconciled", reconciled_in="v2"):
    j = ej.EditJournal(tmp_path / "edits.jsonl")
    base = rec(state=state, reconciled_in=reconciled_in)
    j.append(base.model_copy(update={"target": tj.target_for(env.client)}))
    return j


def test_lineage_is_the_full_ancestor_chain(env, chain):
    src = tj.load_source(chain["v4"], supported_schema_fingerprints=env.fingerprints)
    assert src.lineage == ["v4", "v3", "v2", "v1"]
    assert tj.load_source(chain["v1"], supported_schema_fingerprints=env.fingerprints).lineage == ["v1"]


def test_reconciled_edit_does_not_block_descendants_of_its_snapshot(env, chain, tmp_path):
    j = journal_with(env, tmp_path)
    for sid in ("v2", "v3", "v4"):  # v4 has v2 only as a grandparent: the full chain finds it
        env.replan(chain[sid], edit_journal=j)


def test_til17_rollback_below_a_reconciliation_snapshot_needs_a_reviewed_resolution(env, chain, tmp_path):
    j = journal_with(env, tmp_path)
    with pytest.raises(ContractError) as err:
        env.replan(chain["v1"], edit_journal=j)
    assert err.value.code is ErrorCode.CONFLICT and "e1" in str(err.value)
    plans = {}
    for decision in ("discarded", "carried"):
        res = ej.ConflictResolution(edit_id="e1", decision=decision, reviewed_by="local-operator",
                                    reviewed_at=NOW, note="reviewed")
        plans[decision] = env.replan(chain["v1"], edit_journal=j, resolutions=[res])
    # the reviewed decision is bound into the plan hash
    assert plans["discarded"].change.plan_sha256 != plans["carried"].change.plan_sha256
    # a resolution for another edit does not help
    other = ej.ConflictResolution(edit_id="zzz", decision="discarded", reviewed_by="x", reviewed_at=NOW, note="n")
    with pytest.raises(ContractError):
        env.replan(chain["v1"], edit_journal=j, resolutions=[other])


def test_resolved_and_reverted_edits_protect_nothing(env, chain, tmp_path):
    for state in ("resolved", "reverted", "recorded"):
        j = journal_with(env, tmp_path / state, state=state)
        env.replan(chain["v1"], edit_journal=j)


def test_edit_ids_are_content_derived_and_a_retry_is_a_noop(tmp_path):
    kw = {"target": "T", "scope": ("synthetic",), "base_snapshot_id": "v1", "operation": "node_patch",
          "ids": ("n1",), "before": {"a": 1}, "after": {"a": 2}}
    a = ej.derive_edit_id(**kw)
    assert a == ej.derive_edit_id(**kw) and a != ej.derive_edit_id(**{**kw, "after": {"a": 3}})
    j = ej.EditJournal(tmp_path / "j.jsonl")
    first, created = j.record(rec(edit_id=a, state="acknowledged", reconciled_in=None))
    again, created2 = j.record(rec(edit_id=a, state="acknowledged", reconciled_in=None, at=NOW.replace(year=2027)))
    assert created and not created2 and again == first
    assert len((tmp_path / "j.jsonl").read_text().splitlines()) == 1


def g(nodes, edges=()):
    return {"things": [dict(n) for n in nodes], "associations": [dict(e) for e in edges]}


def test_json10_replay_keeps_every_edit_or_records_a_conflict(tmp_path):
    rebuilt = g([{"id": "a", "description": "old"}, {"id": "b", "description": "rebuilt-b"},
                 {"id": "c", "description": "same"}], [{"subject": "a", "predicate": "p", "object": "b"}])
    records = [
        rec("e1", "acknowledged", None, "a", before={"description": "old"}, after={"description": "edited"}),
        rec("e2", "reconciled", "v2", "gone", before={"description": "x"}, after={"description": "y"}),
        rec("e3", "applied", None, "b", before={"description": "orig-b"}, after={"description": "edited-b"}),
        rec("e4", "acknowledged", None, "c", before={"node": {"id": "c", "description": "was"}},
            after={"node": {"id": "c", "description": "same"}}),
        rec("e5", "acknowledged", None, "x", ids=("a|q|c|0",), op="relationship_add",
            after={"edge": {"subject": "a", "predicate": "q", "object": "c"}}),
        rec("e6", "acknowledged", None, "x", ids=("a|p|b|0",), op="relationship_remove",
            before={"edge": {"subject": "a", "predicate": "p", "object": "b"}}),
        rec("e7", "failed", None, "x", ids=("a|q|zz|0",), op="relationship_add",
            after={"edge": {"subject": "a", "predicate": "q", "object": "zz"}}),
        rec("e8", "acknowledged", None, "a", op="snippet_upsert"),
        rec("skip", "reverted", None, "a", before={"description": "old"}, after={"description": "never"}),
    ]
    out = er.replay_edits(rebuilt, records)
    assert out.accounted == sorted(f"e{i}" for i in range(1, 9))          # every edit has an outcome
    assert sorted(out.kept) == ["e1", "e5", "e6"] and out.already == ["e4"]
    assert {c.edit_id: c.reason for c in out.conflicts} == {
        "e2": "node_missing", "e3": "field_changed", "e7": "endpoint_missing", "e8": "unsupported_operation"}
    nodes = {n["id"]: n for n in out.graph["things"]}
    assert nodes["a"]["description"] == "edited" and nodes["b"]["description"] == "rebuilt-b"
    assert [(e["predicate"]) for e in out.graph["associations"]] == ["q"]
    assert rebuilt["things"][0]["description"] == "old"                 # the input is not mutated


def test_replay_conflicts_are_journaled_and_then_block_imports(env, chain, tmp_path):
    target = tj.target_for(env.client)
    j = ej.EditJournal(tmp_path / "edits.jsonl")
    j.append(rec("e2", "reconciled", "v2", "gone", before={"description": "x"},
                 after={"description": "y"}).model_copy(update={"target": target}))
    out = er.replay_journal(graph_of(chain["v1"]), j, target=target, scope=("synthetic",))
    assert er.record_conflicts(j, out, NOW) == ["e2"] and j.latest()["e2"].state == "conflict"
    assert er.record_conflicts(j, out, NOW) == []                       # idempotent
    with pytest.raises(ContractError) as err:   # a conflict is protected even inside the lineage
        env.replan(chain["v4"], edit_journal=j)
    assert err.value.code is ErrorCode.CONFLICT
