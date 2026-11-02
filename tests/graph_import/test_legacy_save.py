"""T18D: the legacy UI Save as a journaled multi-write on Tiled (memory double and real Tiled 0.2.18).

Binary-case equivalents (the companion inventory is frozen, so these pytest names stand in for it):

* TIL-05 (extend): the node, relationship and snippet edits of one Save read back on Tiled with the same ids.
* TIL-18: a Save is acknowledged only after the journal write and the Tiled effect; a retry after a crash
  returns the same readback once the recovery job has run
  (``test_til18_*``).
* TIL-19: a denied caller leaves no journal entry and no Tiled access (``test_til19_*``).
"""
import copy
import hashlib

import pytest
from test_corpus_replacement import SCOPE, foreign_rows, graph_of, live, owned

from app.modules.contracts.context import CHAT_CAPABILITIES, RequestContext
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import edit_journal as ej
from app.modules.graph import edit_replay as er
from app.modules.graph.imports import corpus_scope as cs
from app.modules.graph.legacy_save import SaveTarget, StaleSave, TiledSaveBackend, build_edits


@pytest.fixture
def world(env, snap, tmp_path):
    env._graph, env._snap = graph_of(snap), snap
    env.replace(snap)
    journal = ej.EditJournal(tmp_path / "edits.jsonl")
    target = SaveTarget(client=env.client, settings=env.settings, state_dir=env.state_dir, clock=env.clock,
                        scope=SCOPE, journal=journal, base_graph_path=snap / "graph.json", base_snapshot_id="v1",
                        base_graph_sha256=hashlib.sha256((snap / "graph.json").read_bytes()).hexdigest())
    return env, TiledSaveBackend(target), journal


def pick(graph):
    """A node with two or more owned links, and two other nodes."""
    degree = {}
    for a in graph["associations"]:
        for end in (a["subject"], a["object"]):
            degree[end] = degree.get(end, 0) + 1
    node = max(degree, key=lambda n: (degree[n], n))
    others = [t["id"] for t in graph["things"] if t["id"] != node]
    return node, others


def thing(graph, node):
    return next(t for t in graph["things"] if t["id"] == node)


def edited(graph, node, others, *, description="edited in the UI", add_edge=True, snippet=True):
    after = copy.deepcopy(graph)
    thing(after, node)["description"] = description
    if add_edge:
        after["associations"].append({"subject": node, "predicate": "rel:affects", "object": others[0]})
    if snippet:
        after["things"].append({"id": "matkg:snippetdelta1", "name": "delta snippet", "category": "CodeSnippet",
                                "type": "matkg:CodeSnippet", "description": "", "function_name": "delta",
                                "code_language": "python", "code_snippet": "def delta():\n    return 4",
                                "publications": [], "source_papers": [], "context_snippets": [], "properties": []})
        after["associations"].append({"subject": node, "predicate": "rel:has_code_snippet",
                                      "object": "matkg:snippetdelta1"})
    return after


def owned_triples(env):
    ents, links = env.scan()
    by_id = {e["id"]: e for e in ents}
    out = set()
    for lk in links:
        if (lk.get("properties") or {}).get("f2w_graph_id") == SCOPE:
            s, o = by_id[lk["subjectId"]], by_id[lk["objectId"]]
            out.add((s["properties"]["f2w_id"], lk["predicate"], o["properties"]["f2w_id"]))
    return out


def triples(graph):
    return {(a["subject"], a["predicate"], a["object"]) for a in graph["associations"]}


def test_til05_one_save_writes_node_relationship_and_snippet_and_reads_them_back(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    foreign = foreign_rows(env)
    old_ids = {k: v["id"] for k, v in live(env).items()}
    after = edited(base, node, others)
    out = backend.save(env.ctx, backend.effective_graph(), after)
    assert len(out.edit_ids) == 3
    assert sorted(r.operation for r in journal.latest().values()) == ["node_patch", "relationship_add", "snippet_upsert"]
    assert {r.state for r in journal.latest().values()} == {"applied"}
    now = live(env)
    assert now[node]["properties"]["description"] == "edited in the UI"
    assert now[node]["id"] != old_ids[node], "0.2.18: a node edit is a new revision"
    assert {k: v["id"] for k, v in now.items() if k not in (node, "matkg:snippetdelta1")} == \
        {k: v for k, v in old_ids.items() if k != node}, "every other record keeps its Tiled id"
    assert "matkg:snippetdelta1" in now
    assert owned_triples(env) == triples(after), "node links moved, new relationship and snippet link exist"
    assert foreign_rows(env) == foreign
    assert triples(backend.effective_graph()) == triples(after)
    # The effective record equals what the legacy handler computed (same node ids, same fields).
    eff = backend.effective_graph()
    assert thing(eff, node)["description"] == "edited in the UI" and "matkg:snippetdelta1" in {t["id"] for t in eff["things"]}
    # Saving the effective graph again changes nothing (idempotent, no new journal entries).
    n = len(env.writes())
    assert backend.save(env.ctx, eff, eff).edit_ids == [] and len(env.writes()) == n


def test_removals_unlink_the_snippet_but_keep_its_node_and_remove_only_owned_links(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    first = edited(base, node, others)
    backend.save(env.ctx, backend.effective_graph(), first)
    current = backend.effective_graph()
    # UI17 inverse: omit the snippet (implicit unlink) and remove the relationship.
    second = copy.deepcopy(current)
    second["associations"] = [a for a in second["associations"]
                              if a["predicate"] != "rel:affects" and a["object"] != "matkg:snippetdelta1"]
    out = backend.save(env.ctx, current, second)
    assert sorted(r.operation for r in (journal.latest()[i] for i in out.edit_ids)) == \
        ["relationship_remove", "snippet_unlink"]
    assert owned_triples(env) == triples(second)
    assert "matkg:snippetdelta1" in live(env), "the snippet node stays"
    assert "matkg:snippetdelta1" in {t["id"] for t in backend.effective_graph()["things"]}


def test_q9_removing_a_link_f2w_does_not_own_is_refused_with_no_journal_entry(world):
    env, backend, journal = world
    base = backend.base_graph()
    edge = next(a for a in base["associations"] if a["predicate"] != "rel:has_code_snippet")
    st = cs.read_scope_state(env.client, SCOPE, double_read=False)
    sid, oid = st.final[edge["subject"]]["id"], st.final[edge["object"]]["id"]
    for lk in [lk for lk in st.link_rows.values() if lk["subjectId"] == sid and lk["objectId"] == oid
               and lk["predicate"] == edge["predicate"]]:
        env.client.delete_link(lk, SCOPE)
    env.add_foreign_link(sid, oid, edge["predicate"])      # now only a foreign link carries that triple
    after = copy.deepcopy(base)
    after["associations"] = [a for a in after["associations"] if a is not edge and a != edge]
    before_rows = foreign_rows(env)
    with pytest.raises(ValueError, match="created by F2W"):
        backend.save(env.ctx, base, after)
    assert not journal.path.exists() and foreign_rows(env) == before_rows


def test_q10_a_save_from_an_out_of_date_copy_is_a_409_with_no_journal_entry(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    backend.save(env.ctx, backend.effective_graph(), edited(base, node, others, add_edge=False, snippet=False))
    n_entries = len(journal.latest())
    stale_copy = base                                       # a browser tab that still shows the old node
    after = copy.deepcopy(stale_copy)
    thing(after, node)["description"] = "from the stale tab"
    with pytest.raises(StaleSave):
        backend.save(env.ctx, stale_copy, after)
    assert len(journal.latest()) == n_entries
    # A stale implicit unlink: the link is already gone on Tiled.
    snippet_graph = edited(base, node, others, add_edge=False)
    eff = backend.effective_graph()
    backend.save(env.ctx, eff, edited(eff, node, others, description=thing(eff, node)["description"], add_edge=False))
    eff2 = backend.effective_graph()
    unlinked = copy.deepcopy(eff2)
    unlinked["associations"] = [a for a in unlinked["associations"] if a["object"] != "matkg:snippetdelta1"]
    backend.save(env.ctx, eff2, unlinked)
    del snippet_graph
    with pytest.raises(StaleSave):
        backend.save(env.ctx, eff2, unlinked)


def test_q8_a_save_during_a_switch_is_refused_before_anything_is_journaled(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    cs.ReleaseState(env.scope_root() / "release.json").mark_switching("abcd")
    n = len(env.writes())
    with pytest.raises(ContractError) as err:
        backend.prepare(env.ctx)
    assert err.value.code is ErrorCode.CONFLICT and err.value.retryable
    with pytest.raises(ContractError) as err:
        backend.save(env.ctx, base, edited(base, node, others))
    assert err.value.code is ErrorCode.CONFLICT and "republished" in str(err.value)
    assert not journal.path.exists() and len(env.writes()) == n


def test_til19_a_caller_without_graph_edit_leaves_no_journal_entry_and_no_tiled_access(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    weak = RequestContext(profile=env.ctx.profile, mode=env.ctx.mode, principal=env.ctx.principal, request_id="x",
                          capabilities=CHAT_CAPABILITIES)
    n = len(env.writes())
    for call in (lambda: backend.prepare(weak), lambda: backend.save(weak, base, edited(base, node, others))):
        with pytest.raises(ContractError) as err:
            call()
        assert err.value.code is ErrorCode.DENIED
    assert not journal.path.exists() and len(env.writes()) == n


CRASH_POINTS = [("create_revision", 0), ("recreate_links", 0), ("retire_old_effect", 0), ("promote_new_effect", 0),
                ("delete_old_links", 1)]


@pytest.mark.parametrize("point", CRASH_POINTS, ids=[f"{a}-{b}" for a, b in CRASH_POINTS])
@pytest.mark.parametrize("failure", ["crash", "error"])
def test_til18_a_crash_after_the_fsync_is_answered_on_retry_with_the_same_readback(world, point, failure):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    after = edited(base, node, others, add_edge=False, snippet=False)

    def fault(stage, i):
        if (stage, i) == point:
            if failure == "crash":
                raise cs.InjectedCrash(point)
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph is unavailable (ConnectionError)")
    backend.t.fault = fault
    with pytest.raises((cs.InjectedCrash, ContractError)):
        backend.save(env.ctx, backend.effective_graph(), after)       # the client gets an error: never acknowledged
    assert [r.state for r in journal.latest().values()] == ["recorded" if failure == "crash" else "failed"]
    # Reads already include the in-flight entry (the effective-record overlay).
    assert thing(backend.effective_graph(), node)["description"] == "edited in the UI"
    # The process restarts: fresh backend, no fault. The retry first runs the recovery job.
    env.clock.advance(1000)
    restarted = TiledSaveBackend(SaveTarget(**{**backend.t.__dict__, "fault": None}))
    out = restarted.prepare(env.ctx)
    assert len(out.recovered) == 1 and out.recovered[0]["outcome"] in ("applied", "reverted")
    state = [r.state for r in journal.latest().values()][0]
    assert state == out.recovered[0]["outcome"]
    # Same request again: the readback is the edited record whichever way the recovery went.
    retry = restarted.save(env.ctx, restarted.effective_graph(), after)
    if state == "reverted":
        assert len(retry.edit_ids) == 1, "the reverted edit is applied again by the retry (same content id)"
    assert thing(restarted.effective_graph(), node)["description"] == "edited in the UI"
    assert live(env)[node]["properties"]["description"] == "edited in the UI"
    assert owned_triples(env) == triples(base)
    assert len(live(env)) == len(base["things"])
    assert set(journal.latest()) == set(journal.latest()) and len(journal.latest()) == 1, "one edit id, never two"
    assert restarted.prepare(env.ctx).recovered == []


def test_til18_a_retry_of_a_completed_save_is_a_no_op_on_tiled(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    after = edited(base, node, others, add_edge=False, snippet=False)
    backend.save(env.ctx, base, after)
    writes = len(env.writes())
    again = backend.save(env.ctx, base, after)       # the client lost the response and sends the same request
    assert again.edit_ids == [] and len(env.writes()) == writes and len(journal.latest()) == 1


def test_a_repeated_content_after_the_graph_moved_on_is_a_new_edit(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    a_to_b = edited(base, node, others, description="B", add_edge=False, snippet=False)
    b_to_a = base
    backend.save(env.ctx, base, a_to_b)
    backend.save(env.ctx, a_to_b, b_to_a)
    again = backend.save(env.ctx, base, a_to_b)
    assert len(again.edit_ids) == 1 and len(journal.latest()) == 3
    assert live(env)[node]["properties"]["description"] == "B"


def test_a_link_edit_that_stops_after_its_write_is_marked_failed_and_rolled_forward(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    after = edited(base, node, others, description=thing(base, node)["description"], snippet=False)

    def fault(stage, i):
        if stage == "link_effect":
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph is unavailable (ConnectionError)")
    backend.t.fault = fault
    with pytest.raises(ContractError):
        backend.save(env.ctx, base, after)
    assert [r.state for r in journal.latest().values()] == ["failed"]
    backend.t.fault = None
    reports = backend.recover(env.ctx)
    assert reports[0]["outcome"] == "applied" and backend.recover(env.ctx) == []
    assert owned_triples(env) == triples(after), "the link exists once"
    assert len([lk for lk in owned(env)[1] if lk["predicate"] == "rel:affects"]) == 1


def test_the_effective_record_shows_reverted_nothing_and_in_flight_everything(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    assert backend.effective_graph() == base
    after = edited(base, node, others, add_edge=False, snippet=False)
    backend.save(env.ctx, base, after)
    assert thing(backend.effective_graph(), node)["description"] == "edited in the UI"
    cur = journal.latest()
    for edit_id in cur:
        journal.advance(edit_id, "reverted", env.clock(), note="test")
    assert backend.effective_graph() == base


def test_build_edits_classifies_the_difference(world):
    env, backend, _ = world
    base = backend.base_graph()
    node, others = pick(base)
    ops = [e.operation for e in build_edits(base, edited(base, node, others))]
    assert ops == ["node_patch", "snippet_upsert", "relationship_add"]
    stray = copy.deepcopy(base)
    stray["things"].append({"id": "x", "name": "x"})
    with pytest.raises(ValueError):
        build_edits(base, stray)


def test_snippet_and_link_edits_replay_onto_a_rebuilt_graph(world):
    env, backend, journal = world
    base = backend.base_graph()
    node, others = pick(base)
    backend.save(env.ctx, base, edited(base, node, others))
    records = list(journal.latest().values())
    result = er.replay_edits(base, records, states=er.IN_FLIGHT_STATES)
    assert sorted(result.kept) == sorted(r.edit_id for r in records) and not result.conflicts
    again = er.replay_edits(result.graph, records, states=er.IN_FLIGHT_STATES)
    assert sorted(again.already) == sorted(r.edit_id for r in records) and not again.kept
