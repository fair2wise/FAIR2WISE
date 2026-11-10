"""QA chat over every corpus live on the ``:local`` target (owner decision 2026-11-10, twenty-second set).

Real retrieval agent (lexical), real publication jobs and Save provider over the in-memory Tiled double, a fake chat
model only: no network, no key, no real model. Two small synthetic corpora share the node id ``matkg:TermA``.
"""
import asyncio
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_local_save import NODE, SCOPE, World, freeze_as, publish_as, template  # noqa: E402,F401
from app.modules.f2w_agent import api as api_mod  # noqa: E402
from app.modules.f2w_agent import retrieval_agent as ra  # noqa: E402

REAL_RETRIEVAL = ra.RetrievalAgent
OTHER = "othercorpus"


class FakeLLM:
    """Scripted ``krag.call_llm``: records the context it was shown and cites the first allowed citation."""

    def __init__(self) -> None:
        self.contexts: list[str] = []
        self.labels: list[str] = []
        self.fail: BaseException | None = None

    async def __call__(self, cli, messages, label):
        self.labels.append(label)
        if self.fail is not None:
            raise self.fail
        self.contexts.append("\n".join(str(m.get("content")) for m in messages))
        return json.dumps({"sufficient": True, "answer": "Both corpora describe Term A.", "missing_topics": []})


def other_payload(base, *, description="Organic photovoltaic donor blended with a fullerene acceptor."):
    """Corpus B: the same node ids as the harness graph (a shared id), different content, one extra node."""
    data = json.loads(json.dumps(base))
    for thing in data["things"]:
        if thing["id"] == NODE:
            thing["description"] = description
            thing["publications"] = [{"source_paper": "opv_paper.pdf", "paper_title": "OPV morphology paper",
                                      "doi": "10.0000/opv.1"}]
            thing["source_papers"] = ["opv_paper.pdf"]
    return data


@pytest.fixture
def chat(monkeypatch, tmp_path, template):
    import _legacy_ui_harness as H

    original_install = H.install

    def install(mp, ws, repo):
        ctx = original_install(mp, ws, repo)
        mp.setattr(api_mod, "RetrievalAgent", REAL_RETRIEVAL)       # real lexical retrieval, fake model
        return ctx

    monkeypatch.setattr(H, "install", install)
    llm = FakeLLM()
    monkeypatch.setattr(ra.krag, "call_llm", llm)
    monkeypatch.setattr(ra.krag, "make_chat_client", lambda *a, **k: type("C", (), {"model": "fake"})())
    monkeypatch.setenv("F2W_QA_OFFLINE_FALLBACK", "0")
    w = World(monkeypatch, tmp_path, template, kg_mode="splash")
    w.llm = llm
    w.ask = lambda q: w.client.post("/chat", json={"message": q}).json()
    return w


def publish_two(w, other=None):
    w.freeze("v1", w.payload)
    w.publish("v1")
    freeze_as(w, OTHER, "v1", other or other_payload(w.payload))
    publish_as(w, OTHER)


def graph_ids(body):
    return {h["graph_id"] for h in body["rounds"][0]["retrieval"]["selected_hits"]}


def test_chat_answers_over_both_live_corpora_with_the_corpus_on_every_node_and_publication(chat):
    w = chat
    publish_two(w)
    body = w.ask("Tell me about Term A")
    assert body["status"] == "answered" and body["sufficient"] is True
    retrieval = body["rounds"][0]["retrieval"]
    assert {c["corpus"] for c in retrieval["corpora"]} == {SCOPE, OTHER}
    assert graph_ids(body) == {SCOPE, OTHER}                         # hits of both corpora, tagged with the corpus
    # The shared id is told apart (existing query-graph rule): prefixed with the corpus, never merged.
    assert f"{SCOPE}:{NODE}" in body["node_ids"] and f"{OTHER}:{NODE}" in body["node_ids"]
    by_id = {n["id"]: n for n in body["graph"]["nodes"]}
    assert by_id[f"{SCOPE}:{NODE}"]["graph_id"] == SCOPE and by_id[f"{OTHER}:{NODE}"]["graph_id"] == OTHER
    assert "fullerene" in by_id[f"{OTHER}:{NODE}"]["description"] and "resonant" in by_id[f"{SCOPE}:{NODE}"]["description"]
    pubs = {p["paper_title"]: p for p in body["publications"]}
    assert pubs["OPV morphology paper"]["corpus"] == OTHER
    assert pubs["Synthetic contract paper on resonant scattering"]["corpus"] == SCOPE
    assert {n["graph_id"] for n in pubs["OPV morphology paper"]["supporting_nodes"]} == {OTHER}
    # The model saw both corpora and the citation syntax the UI renders: [KG: <corpus>: <name>].
    context = w.llm.contexts[-1]
    assert f"[KG: {SCOPE}: Term A]" in context and f"[KG: {OTHER}: Term A]" in context
    assert "fullerene" in context and "resonant scattering" in context


def test_progress_stream_and_trace_name_the_corpora(chat):
    w = chat
    publish_two(w)
    from _legacy_ui_harness import parse_sse
    events = parse_sse(w.client.post("/chat/stream", json={"message": "Tell me about Term A"}).content)
    picked = [e for e in events if e["event"] == "progress" and e["data"].get("phase") == "corpora_selected"]
    assert len(picked) == 1
    assert {c["corpus"] for c in picked[0]["data"]["corpora"]} == {SCOPE, OTHER}
    assert events[-1]["event"] == "complete"


def test_the_viewer_and_the_session_graph_are_not_affected_by_a_union_turn(chat):
    w = chat
    publish_two(w)
    before = {n["id"] for n in w.client.get("/graph").json()["nodes"]}
    w.ask("Tell me about Term A")
    after = {n["id"] for n in w.client.get("/graph").json()["nodes"]}
    assert before == after
    assert set(w.service.retrieval._graphs) <= {"kg", "session", Path(str(w.service._session_graph_path())).stem}
    assert w.service._union_set is None                                 # the set lives for one turn only
    assert w.client.get(f"/graph/node/{NODE}").json()["description"].startswith("Organic")   # pointer corpus = OTHER


def test_single_corpus_path_is_unchanged(chat):
    w = chat
    w.freeze("v1", w.payload)
    w.publish("v1")
    body = w.ask("Tell me about Term A")
    assert body["status"] == "answered"
    assert "corpora" not in body["rounds"][0]["retrieval"]
    assert w.service._union_agent is None and w.service._corpus_union is not None
    assert f"{SCOPE}:{NODE}" not in body["node_ids"] and NODE in body["node_ids"]
    assert all(not str(p.get("corpus")) or "corpus" not in p for p in body["publications"])
    assert {h["graph_id"] for h in body["rounds"][0]["retrieval"]["selected_hits"]} == {
        Path(str(w.service._session_graph_path())).stem}


def test_no_publication_yet_keeps_the_startup_seed(chat):
    body = chat.ask("Tell me about Term A")
    assert body["status"] == "answered" and "corpora" not in body["rounds"][0]["retrieval"]
    assert chat.service._union_agent is None


def test_json_mode_is_unchanged_by_live_corpora(chat):
    w = chat
    publish_two(w)
    assert w.client.put("/settings", json={"graph_source": "json"}).status_code == 200
    body = w.ask("Tell me about Term A")
    assert "corpora" not in body["rounds"][0]["retrieval"] and w.service._union_agent is None


def test_a_new_publication_and_a_retirement_are_picked_up_without_a_restart(chat):
    w = chat
    w.freeze("v1", w.payload)
    w.publish("v1")
    assert "corpora" not in w.ask("Tell me about Term A")["rounds"][0]["retrieval"]       # one corpus: single path
    freeze_as(w, OTHER, "v1", other_payload(w.payload))
    publish_as(w, OTHER)
    first = w.ask("Tell me about Term A")                                                  # two corpora: union
    assert {c["corpus"] for c in first["rounds"][0]["retrieval"]["corpora"]} == {SCOPE, OTHER}
    snap = {c["corpus"]: c["snapshot"] for c in first["rounds"][0]["retrieval"]["corpora"]}
    assert snap[OTHER] == f"{OTHER}/v1"
    # A second version of the other corpus replaces its entry; the model sees the new text, not the old.
    freeze_as(w, OTHER, "v2", other_payload(w.payload, description="Perovskite solar absorber with a halide layer."))
    publish_as(w, OTHER, "v2")
    second = w.ask("Tell me about Term A")
    assert {c["snapshot"] for c in second["rounds"][0]["retrieval"]["corpora"]} == {f"{SCOPE}/v1", f"{OTHER}/v2"}
    assert "Perovskite solar absorber" in w.llm.contexts[-1] and "fullerene" not in w.llm.contexts[-1]
    # Removing the corpus leaves one: the single-corpus path again.
    from app.modules.contracts.jobs import JobState
    ctx = w.ops.cli_context()
    handle = w.ops.submit_corpus_retire(ctx, OTHER, w.target, confirm_scope=OTHER)
    assert w.ops.jobs.work_one("w1") == (handle.job_id, JobState.AWAITING_REVIEW)
    w.ops.approve_publication(ctx, handle.job_id, w.ops.current_plan(ctx, handle.job_id).plan_sha256)
    assert w.ops.jobs.work_one("w1") == (handle.job_id, JobState.SUCCEEDED)
    third = w.ask("Tell me about Term A")
    assert "corpora" not in third["rounds"][0]["retrieval"]
    assert not (Path(w.cfg.workdir) / "chat_corpora" / OTHER).exists()                    # no leak of the retired file


def test_an_unchanged_corpus_is_not_reloaded_and_a_changed_one_is(chat):
    w = chat
    publish_two(w)
    w.ask("Tell me about Term A")
    agent = w.service._union_agent
    kgs = dict(agent._graphs)
    w.ask("Tell me about Term A")
    assert {gid: kg is kgs[gid] for gid, kg in agent._graphs.items()} == {SCOPE: True, OTHER: True}   # cached objects
    freeze_as(w, OTHER, "v2", other_payload(w.payload, description="Changed text."))
    publish_as(w, OTHER, "v2")
    w.ask("Tell me about Term A")
    assert agent._graphs[SCOPE] is kgs[SCOPE] and agent._graphs[OTHER] is not kgs[OTHER]


def test_a_corpus_over_the_limit_is_left_out_and_the_answer_says_so(chat, monkeypatch):
    w = chat
    monkeypatch.setenv("F2W_QA_MAX_CORPUS_NODES", "1")        # every harness corpus has more than one thing
    publish_two(w)
    body = w.ask("Tell me about Term A")
    assert body["status"] == "retrieval_error"                   # nothing can be searched: say so, do not answer from one
    assert body["answer"].startswith("No knowledge graphs loaded")
    assert "Not searched" in body["answer"] and SCOPE in body["answer"] and OTHER in body["answer"]
    assert w.llm.labels == []
    # Only the second corpus over the limit: the first is searched and the notice names the other.
    monkeypatch.setenv("F2W_QA_MAX_CORPUS_NODES", "100")
    w.service._corpus_union = None
    big = other_payload(w.payload)
    big["things"] += [{"id": f"matkg:Extra{i}", "name": f"Extra {i}", "category": "Material"} for i in range(150)]
    freeze_as(w, OTHER, "v2", big)
    publish_as(w, OTHER, "v2")
    body = w.ask("Tell me about Term A")
    assert [c["corpus"] for c in body["rounds"][0]["retrieval"]["corpora"]] == [SCOPE]
    assert body["rounds"][0]["retrieval"]["corpora_skipped"][0]["corpus"] == OTHER
    assert "*Not searched: othercorpus (" in body["answer"]


def test_off_domain_gate_and_offline_fallback_work_over_the_union(chat, monkeypatch):
    w = chat
    publish_two(w)
    body = w.ask("What is the capital of France?")
    assert body["answer"].startswith(api_mod.KG_NO_INFORMATION_SENTENCE)
    assert body["node_ids"] == [] and body["sufficient"] is False and w.llm.labels == []
    monkeypatch.delenv("F2W_QA_OFFLINE_FALLBACK")
    w.llm.fail = RuntimeError("CBORG connection failed for lbl/cborg-chat.")
    body = w.ask("Tell me about Term A")
    assert body["answer"].startswith("**Answered without a language model**")
    assert f"[KG: {SCOPE}: Term A]" in body["answer"] or f"[KG: {OTHER}: Term A]" in body["answer"]
    assert body["status"] == "answered" and body["publications"]
