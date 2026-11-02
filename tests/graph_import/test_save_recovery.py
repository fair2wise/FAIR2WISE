"""T08D (Q7/Q12): the multi-write Save sequence and the recovery job (memory double and real Tiled 0.2.18).

Acceptance: a Save interrupted after any Tiled write is reconciled by the recovery job to either the full
effect or the pre-Save state, with the edit journal saying which; the job is idempotent and resumable;
TIL-18 (extended: recovery) - a Save is acknowledged only after the journal write and the Tiled effect.
"""
import copy

import pytest
from test_corpus_replacement import SCOPE, foreign_rows, graph_of, live, owned

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import edit_journal as ej
from app.modules.graph import literature_writer as lw
from app.modules.graph.imports import corpus_scope as cs
from app.modules.graph.imports import save_recovery as sr

CRASH_POINTS = [("create_revision", 0), ("create_revision_effect", 0), ("recreate_links", 0),
                ("recreate_links_effect", 0), ("recreate_links", 1), ("retire_old", 0), ("retire_old_effect", 0),
                ("promote_new", 0), ("promote_new_effect", 0), ("delete_old_links", 0),
                ("delete_old_links_effect", 0), ("delete_old_links", 1)]


def edit_for(env, plan, node, *, edit_id="edit-1", description="edited in the UI"):
    before = next(t for t in env._graph["things"] if t["id"] == node)
    after = copy.deepcopy(before)
    after["description"] = description
    return ej.EditRecord(edit_id=edit_id, seq=1, state="recorded", target=env.url, scope=(SCOPE,),
                         base_snapshot_id="v1", base_graph_sha256=plan.change.input_sha256, profile="local",
                         mode="local_trusted", actor="local-operator", operation="node_patch", ids=(node,),
                         before={"node": before}, after={"node": after}, recorded_at=env.clock())


@pytest.fixture
def world(env, snap, tmp_path):
    env._graph, env._snap = graph_of(snap), snap
    plan, _out = env.replace(snap)
    journal = ej.EditJournal(tmp_path / "edits.jsonl")
    # A node with several owned links in both directions.
    degree = {}
    for a in env._graph["associations"]:
        degree[a["subject"]] = degree.get(a["subject"], 0) + 1
        degree[a["object"]] = degree.get(a["object"], 0) + 1
    node = max(degree, key=lambda n: (degree[n], n))
    assert degree[node] >= 2
    return env, plan, journal, node


def run_save(env, record, journal, **kw):
    return sr.execute_save(env.ctx, record, journal=journal, client=env.client, settings=env.settings,
                           state_dir=env.state_dir, clock=env.clock, **kw)


def recover(env, journal, **kw):
    return sr.recover_failed_saves(env.ctx, journal, client=env.client, settings=env.settings,
                                   state_dir=env.state_dir, clock=env.clock, scope=SCOPE, **kw)


def snapshot_links(env, node_uuid):
    """Owned links touching ``node_uuid`` as (other endpoint, predicate, direction) triples."""
    _ents, links = env.scan()
    out = set()
    for lk in links:
        if (lk.get("properties") or {}).get("f2w_graph_id") != SCOPE:
            continue
        if lk["subjectId"] == node_uuid:
            out.add((lk["objectId"], lk["predicate"], "out"))
        elif lk["objectId"] == node_uuid:
            out.add((lk["subjectId"], lk["predicate"], "in"))
    return out


def test_save_is_applied_on_tiled_before_it_is_acknowledged(world):
    env, plan, journal, node = world
    old = live(env)[node]
    before_links = snapshot_links(env, old["id"])
    record = edit_for(env, plan, node)
    journal.append(record)
    foreign = foreign_rows(env)
    n_links = len(owned(env)[1])
    result = run_save(env, record, journal)
    assert result["state"] == "applied" and result["old_tiled_id"] == old["id"] and result["links"] == len(before_links)
    assert journal.latest()["edit-1"].state == "applied"
    now = live(env)[node]
    assert now["id"] != old["id"] and now["properties"]["description"] == "edited in the UI"
    assert now["entityType"] == old["entityType"]
    assert snapshot_links(env, now["id"]) == before_links and snapshot_links(env, old["id"]) == set()
    assert len(owned(env)[1]) == n_links, "every link moved, none duplicated"
    ents, _ = env.scan()
    assert {e["id"]: e for e in ents}[old["id"]]["entityType"] == lw.RETIRED_TYPE
    assert foreign_rows(env) == foreign
    rows = sr.save_journal_records(env.state_dir, env.url, SCOPE, "edit-1")
    assert [r["event"] for r in rows][0] == "save_started" and rows[-1]["event"] == "save_completed"
    assert recover(env, journal) == []                              # nothing to recover
    # A second Save of the same edit is refused (already applied), and a Save during a switch is refused (Q8).
    with pytest.raises(ContractError) as err:
        run_save(env, record, journal)
    assert err.value.code is ErrorCode.CONFLICT
    cs.ReleaseState(env.scope_root() / "release.json").mark_switching("abcd")
    rec2 = edit_for(env, plan, node, edit_id="edit-2")
    journal.append(rec2)
    with pytest.raises(ContractError) as err:
        run_save(env, rec2, journal)
    assert err.value.code is ErrorCode.CONFLICT and "republished" in str(err.value)
    assert journal.latest()["edit-2"].state == "recorded", "refused before any write: no failed entry"


@pytest.mark.parametrize("point", CRASH_POINTS, ids=[f"{a}-{b}" for a, b in CRASH_POINTS])
@pytest.mark.parametrize("failure", ["crash", "error"])
def test_interrupted_save_is_reconciled_to_full_effect_or_pre_save_state(world, point, failure):
    env, plan, journal, node = world
    old = live(env)[node]
    before_links = snapshot_links(env, old["id"])
    n_links = len(owned(env)[1])
    foreign = foreign_rows(env)
    record = edit_for(env, plan, node)
    journal.append(record)

    def fault(stage, i):
        if (stage, i) == point:
            if failure == "crash":
                raise cs.InjectedCrash(point)
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph is unavailable (ConnectionError)")
    with pytest.raises((cs.InjectedCrash, ContractError)):
        run_save(env, record, journal, fault=fault)
    state_after_failure = journal.latest()["edit-1"].state
    assert state_after_failure == ("recorded" if failure == "crash" else "failed")
    if failure == "error":
        assert "stopped" in journal.latest()["edit-1"].note
    env.clock.advance(1000)                                          # the dead Save's lease expires
    reports = recover(env, journal)
    assert len(reports) == 1 and reports[0]["edit_id"] == "edit-1"
    outcome = reports[0]["outcome"]
    final = journal.latest()["edit-1"]
    assert final.state == outcome and final.note.startswith("recovered:")
    now = live(env)[node]
    promoted_before_failure = point[0] in ("delete_old_links", "delete_old_links_effect") or point == (
        "promote_new_effect", 0)
    if promoted_before_failure:
        assert outcome == "applied"
        assert now["id"] != old["id"] and now["properties"]["description"] == "edited in the UI"
        assert snapshot_links(env, now["id"]) == before_links
    else:
        assert outcome == "reverted"
        assert now["id"] == old["id"] and now["properties"] == old["properties"]
        assert now["entityType"] == old["entityType"] and now["uri"] == old["uri"]
        assert snapshot_links(env, old["id"]) == before_links
        ents, _ = env.scan()
        assert not [e for e in ents if e["entityType"] == lw.STAGED_TYPE], "the staged revision is gone"
    assert len(owned(env)[1]) == n_links, "no link duplicated or lost in either outcome"
    assert len(live(env)) == len(env._graph["things"])
    assert foreign_rows(env) == foreign
    assert recover(env, journal) == [], "idempotent: nothing left to recover"
    replan = env.replan(graph_source(env))                           # what re-publishing the JSON would do
    if outcome == "applied":
        kinds = replan.summary["operations"]
        assert kinds["retire_node"] == 1 and kinds["revive_node"] == 1 and "stage_node" not in kinds
    else:
        assert replan.is_noop, "the pre-Save state is exactly the published snapshot"


def graph_source(env):
    return env._snap


def test_recovery_itself_is_resumable_and_keeps_a_staged_row_with_a_foreign_link(world):
    env, plan, journal, node = world
    old = live(env)[node]
    record = edit_for(env, plan, node)
    journal.append(record)

    def fault(stage, i):
        if (stage, i) == ("retire_old", 0):
            raise cs.InjectedCrash("retire_old")
    with pytest.raises(cs.InjectedCrash):
        run_save(env, record, journal, fault=fault)
    env.clock.advance(1000)
    ents, _ = env.scan()
    staged = next(e for e in ents if e["entityType"] == lw.STAGED_TYPE)
    env.add_foreign_link(env.foreign_entity_id(), staged["id"])      # FORGE already points at the staged row

    def crash_recovery(stage, i):
        if (stage, i) == ("recover_revert_links", 0):
            raise cs.InjectedCrash("recover")
    with pytest.raises(cs.InjectedCrash):
        recover(env, journal, fault=crash_recovery)
    env.clock.advance(1000)
    reports = recover(env, journal)
    assert reports[0]["outcome"] == "reverted" and reports[0]["detail"]["staged_kept_as_retired"] == staged["id"]
    ents, _ = env.scan()
    by_id = {e["id"]: e for e in ents}
    assert by_id[staged["id"]]["entityType"] == lw.RETIRED_TYPE, "retired, never deleted (I3)"
    assert live(env)[node]["id"] == old["id"]
    assert "foreign link" in journal.latest()["edit-1"].note
    assert recover(env, journal) == []


def test_denied_caller_leaves_no_trace(world):
    env, plan, journal, node = world
    from app.modules.contracts.context import RequestContext
    weak = RequestContext(profile=env.ctx.profile, mode=env.ctx.mode, principal=env.ctx.principal, request_id="x",
                          capabilities=frozenset({"chat.read_support"}))
    record = edit_for(env, plan, node)
    journal.append(record)
    n = len(env.writes())
    with pytest.raises(ContractError) as err:
        sr.execute_save(weak, record, journal=journal, client=env.client, settings=env.settings,
                        state_dir=env.state_dir, clock=env.clock)
    assert err.value.code is ErrorCode.DENIED and len(env.writes()) == n
    assert journal.latest()["edit-1"].state == "recorded"
    with pytest.raises(ContractError) as err:
        sr.recover_failed_saves(weak, journal, client=env.client, settings=env.settings, state_dir=env.state_dir,
                                clock=env.clock, scope=SCOPE)
    assert err.value.code is ErrorCode.DENIED
