# QA chat readiness for the December 10-11 2026 local demo

Review of 2026-11-10 on branch `forge_merge-demo-qa` (from `forge_merge` a4054b0). Paths are repository-relative; line numbers
are those of the branch after the fixes below. No real model was called: the behaviour below was read from code and exercised
with fake chat clients (`tests/demo_qa/`). Two live turns run by the coordinator on the `:local` container are folded in (section 7).

## 1. How QA works on `:local` today

**Request path.** The UI submits through `queryLiveAgentStream` (`ui/src/app/components/data/liveAgent.ts:515`) to
`POST /chat/stream` (`app/modules/f2w_agent/api.py:7550`, SSE: `progress` events, then one `complete` or `error`); it falls back to
`POST /chat` (`api.py:7479`) only if no stream event was seen (`liveAgent.ts:575`). The model answer is not token-streamed: the
server collects each model call completely, so the UI shows step progress ("Retrieval agent searching the KG") and then the whole
answer (typed out client-side). The request body carries no `graph_source` or graph path (`liveAgent.ts:295`); the server's runtime
settings decide (`api.py:2783`: the `:local` profile starts with `--kg-mode splash`, so the runtime source is the editable one, whose
loader is JSON, `api.py:2925`). Both routes run `ask`/`ask_with_progress` -> `_ask_with_progress_body` (`api.py:4421` ff.) -> orchestrator
decision (`_prepare_orchestrated_question`, `api.py:5010`) -> `_ask_locked` (`api.py:5477`).

**Router with the helper agents off (default).** `F2W_HELPER_AGENT_LLM` unset means `helper_model_calls_enabled()` is false
(`app/modules/model_policy.py:236-243`); the router skips its classifier (`agent_runtime/routing.py:537-541`) and the deterministic
`safe_fallback` decides (`routing.py:171-246`): greetings and "help" go to `direct_response` (one model call, with a fixed text if it
fails, `api.py:5285`), everything else, including off-topic questions, goes to `retrieve_kg`. Effect on quality: none for in-domain
questions (the classifier only ever chose retrieve vs. direct); off-topic non-greeting questions are no longer short-circuited by a
model, which is why the off-domain gate in section 4 was needed. The evidence debate is also deterministic (`curation/evidence.py:295`).
`F2W_RUNTIME` is unset, so the legacy path above is used, not the langgraph supervisor (`runtime_bridge/mode.py:45`).

**Which graph.** Editable mode: `_ask_locked` calls `_refresh_session_graph` (`api.py:5522`, defined `api.py:2937`), which asks the
publication provider for `backend()` with no corpus, i.e. the corpus of the **active publication pointer**
(`app/api/local_save.py:109-130`), and writes its effective record (snapshot plus in-flight journal, JSON) to the session graph file
(`api.py:2897`, `_materialize_effective_graph`). Retrieval reads that file. Before any publication the startup graph files stay
(`F2W_GRAPH` = `matkg_rsoxs_v1.json` and `matkg_bl1101_v2.json`, `docker/profiles/local.json:43`). **With two corpora live, QA answers
from the pointer's corpus only**; the viewer's Source selector (`ui/src/app/components/graphSourceStore.ts`, used by `GraphMockup`,
`GraphSourceSelect`, not by `ChatSidebar`) and `tiled:<corpus>` edits (`graph_sources.py`) do not change what chat reads. If the user opens
Settings and picks "JSON", the chat reads `storage/kg/*.json` files instead (UI default list `agentSettings.ts:108`: rsoxs_v1 and
**bl1101_v1**), and the JSON-mode ingest rules in `_ask_locked` apply (section 3).
Retrieval does not touch `GraphSources` or the Tiled scanner anywhere (`grep GraphSources` in `retrieval_agent.py`: none).

**Retrieval backends.** The image profile sets `KG_RAG_RETRIEVAL_BACKEND=lexical` (`docker/profiles/local.json:55`), so FAISS and
sentence-transformers are not even imported (`kg_rag_api.py:63-82`). The semantic backend is available: the Dockerfile fetches
`all-MiniLM-L6-v2` at a pinned revision at build time and writes `refs/main` so `HF_HUB_OFFLINE=1` works (`Dockerfile:147-159`);
`-e KG_RAG_RETRIEVAL_BACKEND=semantic` uses it (`docker/profiles/README.md:211-214`), with `KG_RAG_FORCE_CPU=1` baked in
(`Dockerfile:119`). Any other embedding model name would fail offline. Not verified by running the image (no docker here).
Lexical scoring (`kg_rag_api.py:1231`) has **no stop-word list**: a question like "What is the capital of France?" still scores 935
nodes of the 7443-node demo graphs through "the"/"what" (offline run, `scripts/demo/qa_expected_nodes.py`), so "no evidence" almost
never fired. The order of the hits is not stable across processes (hash-order ties in the intent-routed fan-out,
`retrieval_agent.py:1000-1060`); membership is stable.
Defaults sent by the UI: `kg_query_max_nodes=1000`, hops 20, `source_rag=false`, `use_live_tiled=false` (`agentSettings.ts:134-141`).

**Model and cost per turn.** Free CBORG ids only: the clients refuse anything else before a key is read
(`kg_rag_api.py:1722`, `term_extractor/clients.py:134`, `model_policy.require_free_cborg_model`); default `lbl/cborg-chat`. An in-domain
turn costs: 1 judge call (`retrieval_agent.py:1470`, label `KG-RAG-judge`, whole 16k-char context) and, when the judge says insufficient
and the question is not numeric, a 2nd "leeway" call (`retrieval_agent.py:1535`, `KG-RAG-leeway`) that writes the answer anyway; plus 1
rewrite call for context-dependent follow-ups (`api.py:5010-5025`) and, rarely, a memory-compression call. Everything else (routing,
debate, retrieval, publications) is deterministic. Latency is the sum of those calls (each is a fully collected CBORG stream) plus
~0.1-0.2 s lexical retrieval and graph load. `GET /traces/{turn_id}` (`api.py:7747`, tracing is on in the profile) lists every call
with `latency_ms`, so the 38 s of the live turn can be split exactly (see section 7).

**Citations and provenance.** The judge and leeway prompts ask for `[KG: graph_id: NodeName]`, `[PDF: file p.N]`, `[OPS: ...]`, `[MP: ...]`
tokens copied from the context's "Allowed citations" list (`prompts.py:60-95`, `retrieval_agent.py:1170`); the UI turns them into numbered
chips with a tooltip and a click that focuses the node (`ChatSidebar.tsx:140-345`, `kgCitations.ts`). Besides the text, `ChatResponse`
carries `node_ids`, `graph` (the query sub-graph, highlighted in the viewer), `publications` (papers behind the cited nodes,
`api.py:6942`) and a heuristic `confidence` (`api.py:2466`: 0.72 + 0.015 per node + 0.04 per evidence node, capped 0.98 whenever the
verdict is sufficient, 0.05 for no evidence).

## 2. Failure behaviour (what the user sees)

| Case | Before | Now |
|---|---|---|
| CBORG unreachable (connection) | `Retrieval failed: CBORG connection failed for <model>. Check CBORG_BASE_URL/network.` (clean, `kg_rag_api.py:1750`), shown as "Agent run failed" in the stream, status `retrieval_error` | the same text is now only the *reason line* of a labelled retrieval-only answer (section 5) |
| HTTP 403 (not on LBLnet) / 429 | **raw SDK text** `Error code: 403 - {...}`: `PermissionDeniedError`/`RateLimitError` are not caught in `CBorgClient.chat` (`kg_rag_api.py:1750-1760` handles connection, timeout, 401 only) | one sentence (`describe_llm_failure`, `retrieval_agent.py:333`); with the fallback off the `retrieval_error` carries that sentence |
| Hung provider | bounded by `asyncio.wait_for(LLM_TIMEOUT 120 s + 5 s)` in `call_llm` (`kg_rag_api.py:1893`), then `RuntimeError("... exceeded 120s")`: clean, but QA does **not** use `call_with_deadline` (that guards the helper/extraction calls). Worst case per turn about 2 x 125 s (judge, then leeway) before an answer or message | unchanged (design gap G9) |
| No `CBORG_API_KEY` | `Retrieval failed: CBORG_API_KEY is not set; refusing to create a CBORG client (the OpenAI SDK would otherwise ...)` (`cborg_http.py:242`) | fallback answer, reason line is that text |
| No publication yet | startup `F2W_GRAPH` seed answers (`api.py:2937-2950` returns quietly) | unchanged; the seed is not the demo corpus |
| Empty or all-missing graph | `RuntimeError("No knowledge graphs loaded; cannot answer questions. Check graph file paths and logs above.")` shown as `Retrieval failed safely` | now `No knowledge graphs loaded; publish or adopt a corpus (or pick a graph in Settings), then ask again.` (prefix kept: `runtime_bridge/qa_tool.py:80` matches it) |
| Question outside the KG | status `answered`, `sufficient=true`, confidence ~0.98, a rambling leeway answer with `[KG: ...]` chips on unrelated nodes (editable mode skips every JSON-mode guard, `api.py:5631`); in JSON mode an OpenAlex candidate search and a paper-download offer | a normal model answer preceded by `*The knowledge graph has no information on this question.*` (owner decision 2026-11-10; `KG_NO_INFORMATION_SENTENCE`, `api.py:_generate_general_answer`), no unrelated nodes, no paper offer, no judge call; without a reachable model the sentence plus a one-line reason (section 4) |
| Very long answers | unbounded text; context is capped at 16k chars (`kg_rag_api.py:172`) but the answer is not | unchanged (G11, low) |
| SSE drops | no event seen: UI silently re-asks via `POST /chat` (`liveAgent.ts:575`) and the turn id dedupes the claim; after a first event: `Agent run failed: Agent stream ended without a complete event` and the server cancels the turn (`api.py:7628`). No resume | unchanged (G10). nginx buffering is off and read timeout 900 s (`docker/nginx.conf:103-109`) |

## 3. Out-of-the-KG questions and JSON mode (design note)

In JSON mode `_ask_locked` treats "nodes do not address the question" as a reason to search OpenAlex and offer a paper download
(`api.py:5614-5700`, `_json_graph_should_search_candidates`, `api.py:2211`). That is the product's ingest loop and needs the internet;
offline it ends in `candidate_search_error` ("Candidate metadata search failed safely"). In the demo, keep Settings on the default
(editable) source; the new off-domain answer applies to both sources and a direct "find papers on X" request still reaches the search.

## 4. Fixes made (commit dfc7c8f)

1. **Off-domain gate** (`retrieval_agent.py:306 _off_domain`, wired in `query`, and `api.py:5614`): when no topic word of the question
   (stop words removed) occurs in the name, description, category or title of any retrieved node (or nothing was retrieved), retrieval
   reports `no_evidence` plus `off_domain`, with `selected=[]`; `_ask_locked` then returns status `answered` whose text is the
   italic sentence `*The knowledge graph has no information on this question.*` followed by one general-knowledge model answer
   (`_generate_general_answer`, label `general-answer`, bounded by `LLM_HELPER_TIMEOUT_SECONDS`), per the owner decision of
   2026-11-10 (not a refusal, never an error; if no model is reachable the sentence is followed by "I also could not reach a
   language model ... (reason)"). No judge/leeway call (one model call instead of two), no unrelated nodes or papers. The response
   shape is unchanged; `sufficient=false` and `confidence=0.05` remain the machine-readable "not from the KG" signal. Never fires for questions with no topic word (follow-ups), Tiled identity questions, or hits without text.
   Limit: a single shared common word defeats it ("current" in "Tesla stock price current" matches "short-circuit current"); the
   three demo refusal questions are chosen not to hit that, and the judge/leeway remain the second line (G2).
2. **Clean provider errors** (`describe_llm_failure`): 403, 429 and other HTTP status errors never show the raw body.
3. **Retrieval-only fallback** (section 5).
4. Empty-graph message made actionable.

## 5. Offline fallback: "answered without a language model"

If the judge call fails (CBORG 403, 429, unreachable, timeout, no key) and retrieval found nodes, `query` returns a **deterministic answer**
(`retrieval_agent.py:358 offline_answer`, wired at the judge `except`): first line
`**Answered without a language model** (<reason>) These are the best-matching knowledge graph entries, not a synthesized answer:` then the top 5
hits as `- [KG: graph: Name] (Category): first 220 chars of the description`. The `[KG: ...]` tokens render as the usual citation chips;
`node_ids`, `graph` and `publications` are filled as for a normal answer, `status=answered`, `sufficient=true`. No second model call, no
paper search while offline (`api.py:2211` returns false for `offline_fallback`). `F2W_QA_OFFLINE_FALLBACK=0` restores the old
`retrieval_error`. About 60 lines plus tests (`tests/demo_qa/test_qa_failure_modes.py`). Demo quality: presentable as a fallback (cited,
labelled, instant), not as the headline answer; it ranks lexically, so for the layout questions the top nodes are stages, not the
asked-for detector. Two existing tests that pin the old behaviour set the flag to `0`.
Owner decision needed: default on (as shipped) or off.

## 6. Demo question set and probe

`docs/migration/demo/qa_questions.json`: 12 questions (4 `e2e_demo`, 3 `bl1101`, 2 `rsoxs_v1`, 3 unanswerable). `bl1101` and
`rsoxs_v1` carry curated `expected_node_ids` checked by `scripts/demo/qa_expected_nodes.py --check` (real lexical retriever over
`storage/kg/matkg_rsoxs_v1.json` + `matkg_bl1101_v4.json`; all within the first 31 hits under every `PYTHONHASHSEED` tried). The
`e2e_demo` graph is **not in the repository** (the owner's container exported it; `papers/e2e_demo` holds only its 5 arXiv PDFs and manifest),
so those four questions have `expected_terms` taken from the five paper titles; run the script with the exported `graph.json` to curate
node ids. `scripts/demo/qa_probe.py` (urllib only, no key) posts each question with a fresh session id, prints answer, sources, status,
`sufficient`, `confidence`, latency and a PASS/WARN (`--stream` uses the UI's route, `--only`, `--slow`, `--strict`). It calls only `/chat`
or `/chat/stream`. Run: `python scripts/demo/qa_probe.py --base http://127.0.0.1:8080/fair2wise/api`.

## 7. Live observations folded in (coordinator, 2026-11-10)

- In-domain, 38.0 s, `answered`, answer opens with a hedge: the hedge comes from the leeway path, whose system prompt tells the model to
  "First say what the graph lacks" (`prompts.py` `LEEWAY_SYSTEM`) and the judge prompt to "disclose graph gaps"; the judge said
  insufficient, so the turn made at least two sequential model calls. Read `GET /traces/<turn_id>` to see `KG-RAG-judge` and
  `KG-RAG-leeway` latencies. Proposal G3: reword the leeway prompt to answer first and put the gap in a closing note (not changed here:
  an untestable-with-fakes prompt change).
- Out-of-domain, 13.5 s, `answered`: lexical retrieval returned hundreds of weak hits, the judge said insufficient, the leeway call wrote a
  rambling answer, and `_confidence` scores any sufficient verdict 0.72-0.98, so `sufficient` and `confidence` said "good". Fixed by the
  gate in section 4 (verify live: the probe's `ooc-*` questions should return the sentence plus a plain model answer in one model call, about 5-15 s).

## 8. Stretch goals (design only, nothing implemented)

**(a) Experiment data of 11.0.1.2 from Tiled.** Code path exists: with `use_live_tiled` on and an identity question
(`is_experiment_identity_question`, `tiled_lookup.py:651`: ESAF number `20xx-nnnn`, Proposal `P20xx...`, or words such as sample/scan/run),
`RetrievalAgent._merge_live_tiled` (`retrieval_agent.py:722`) calls `lookup_experiment_identity` (`tiled_lookup.py:1136`), which pages
GraphQL `POST {TILED_URI}/api/graphql` for entity types `ESAF`, `Proposal`, `Sample`, `BlueskyRun` (`tiled_lookup.py:56`; 100 per page, 8
pages, 8 s timeout), and, when there are no `BlueskyRun` entities, walks the catalog tree with `tiled.client` for runs (`_catalog_runs`,
`tiled_lookup.py:1045`: depth 3, 20 keys per level, 8 runs; reads `start` metadata: uid, scan_id, plan_name, time, proposal, sample name).
"Experiment data" here means **identity and run metadata**, not detector arrays or measured values. The bl1101 JSON KG contains none of
it (note at `tiled_lookup.py:23-28`, sim nodes removed in v8). The key is `TILED_API_KEY` (scheme `TILED_API_KEY_SCHEME`, default `Apikey`),
endpoint `TILED_URI` or the Settings field; `probe_tiled_status` (`tiled_lookup.py:448`) gives ok/unsigned/unreachable/empty/disabled.
Blockers for staging: (1) `coerce_local_tiled_uri` (`tiled_lookup.py:205`) **silently replaces any `*.als.lbl.gov` host, including
`tiled-staging.als.lbl.gov`, with `http://127.0.0.1:8765`** (by design: "never follow ALS production hosts"); a staging read needs an explicit,
tested allowlist of the staging host (S, ~15 lines + tests). (2) The `:local` profile strips `TILED_URI`, `TILED_API_KEY` and
`F2W_LIVE_TILED` from the environment (`docker/profiles/local.json:30-38`) and forbids `F2W_TILED_URL`, so the key must come from the
operator's `docker run -e` on a profile that allows it, or a dedicated read-only setting (M). (3) The UI toggle `useLiveTiled` defaults off
and must be on for the demo (S). (4) Unknown: what ESAF/Proposal/Sample/BlueskyRun entities exist on staging and whether the entity names
match 11.0.1.2 runs; needs the read-only probe (week 3). (5) Answer text: the judge prompt must be allowed to cite the `tiled` graph
(`TILED_GRAPH_ID`, `[KG: tiled: name]`); there is a fixture-based test seam (`client=` transport) so tests need no network. Risk: per-turn
latency of up to 4 types x 8 pages x 8 s on a slow server, and a stale/empty answer when unsigned (the code falls back to the JSON KG with
status `unsigned`/`empty`). Tests: transport-fake cases for staging-host allowlist, unsigned, empty, paging cap, catalog fallback.

**(b) QA over a Tiled-published graph instead of the JSON copy.** Not present: retrieval reads the JSON effective record only
(section 1). Smallest design: a `TiledGraphSource` that exposes the same `KnowledgeGraph` surface retrieval needs (`nodes` dict, `out_edges`,
`ids`, `build_context`, `semantic_search` lexical, `graph_id`/`graph_label`) built from the cached scan of `GraphSources`
(`graph_sources.py:352`: one complete entity scan + link scan cached `TILED_TTL_SECONDS=20`, `MAX_NODES=100_000`, final records only,
`tiled:<corpus>` ids; the wire shape is the viewer's node/edge shape). Steps: (1) convert a scan to the donor `things`/`associations` dict that
`krag.KnowledgeGraph` loads (the same conversion the effective record already inverts) and construct the lexical `KnowledgeGraph` from it,
cached by `(corpus, marker/sha)` so a 7k-node graph is indexed once, not per turn (index build was 0.2 s for 7443 nodes here); (2) add it in
`_ask_locked` as the editable-mode source when the pointer's corpus is Tiled-backed, keeping the JSON record as the automatic fallback when the scan fails
(failed scans are remembered 3 s, `graph_sources.py:60`); (3) choose the corpus by the pointer (as today) and expose `graph_source_used="tiled:<corpus>"`
in the response (the field already exists). Size: ~150-250 lines + tests. Risks: scan cost on first turn (one full read of Tiled), consistency
with in-flight journal entries (the effective record merges the journal; a scan does not, so just-saved edits would be invisible for up to 20 s
unless the journal overlay is applied), `[KG: ...]` name collisions, and dual truth if JSON and Tiled diverge. Tests: a fake scanner returning the e2e_demo
shape (269 entities, 126 links) -> same retrieved ids as the JSON record; scanner failure -> JSON fallback label; journal overlay; TTL; two corpora live -> pointer wins.
Value for the demo: it makes "answers from what is in Tiled" literally true; today it is true only through the JSON record the publication froze.

## 9. Gap list, ranked by demo risk

| # | Risk | Gap | Fix / proposal |
|---|---|---|---|
| G1 | high | CBORG not reachable on demo day (needs LBLnet/VPN; 403 elsewhere): QA returned an error | **Fixed**: labelled retrieval-only answer with citations (section 5). Still verify the machine reaches CBORG; rehearse both |
| G2 | high | Out-of-KG question answered with a rambling leeway answer and confidence ~0.98 | **Fixed** per the owner decision: sentence first, then the model's ordinary answer (section 4). Residual: one shared common word defeats the gate; proposal: make `_confidence` 0.35 for leeway answers |
| G3 | high | Hedging opener on in-domain answers ("The knowledge graph lacks specific slots...") | Proposal: LEEWAY_SYSTEM and JUDGE rule 1 to answer first, one closing line for gaps; needs a live A/B on the 12 questions (cannot be validated with fakes) |
| G4 | high | Chat reads only the active pointer's corpus; a demo that switches the viewer to the other corpus then asks QA gets the first corpus' answers | Demo script: activate the corpus you will ask about (pointer), or implement (b)'s corpus choice from the viewer selection (M). Add to the runbook |
| G5 | high | `e2e_demo` KG file is not in the repo; expected nodes for it cannot be curated here | Owner exports `graph.json`; run `qa_expected_nodes.py --graph <it>`; seed script must publish it before QA |
| G6 | medium | Latency 13-38 s per turn, no token streaming (the UI uses `/chat/stream` but only for progress steps; the answer arrives whole after sequential judge + leeway calls, each a fully collected CBORG stream) | Progress steps hide some; proposals (none implemented): add `token` SSE events from the CBORG stream (needs a UI change, L); run judge and leeway concurrently only when the judge is predicted insufficient; cap context (`KG_RAG_CTX_CHARS`, default 16000) to cut judge time; `--slow` in the probe flags regressions; also skip the leeway call when judge missing_topics are slot gaps (saves one call), stream tokens (L); keep `--slow 45` in probe; rehearse timings |
| G7 | medium | JSON source selected in Settings turns unanswerable/low-evidence questions into OpenAlex searches and download offers (needs internet) | Keep the default source for the demo; runbook line |
| G8 | medium | Lexical retrieval has no stop words and unstable tie order; bluesky question ranks science-KG nodes above the ops node (rank 25) | Stop-word filter in `_lexical_search` (changes ranking, needs eval re-run: not done); semantic backend is in the image (`-e KG_RAG_RETRIEVAL_BACKEND=semantic`, unverified live, slower startup) |
| G9 | medium | Hung CBORG: up to ~250 s per turn (two 125 s waits) before a message | Lower `KG_RAG_LLM_TIMEOUT` (env, e.g. 40) for the demo; proposal: one deadline for the whole turn |
| G10 | low | SSE drop after the first event gives a generic error, no resume | Rare on localhost; proposal: UI retry through `/chat` with the same client turn id |
| G11 | low | No answer-length bound | Proposal: max_tokens on the judge/leeway calls |
| G12 | low | Hit order differs between processes | Cosmetic; membership stable (tested by `--check`) |
| G13 | low | `confidence` is a heuristic of hit counts, not calibrated | Hide or relabel in the UI for the demo |

**Only verifiable live with a real model:** answer quality and citation tokens on the 12 questions (run the probe), the hedging opener and its
fix, the exact split of the 38 s (trace), whether the leeway call fires on the demo questions, semantic-backend startup in the image, and
that the fallback label shows in the UI when CBORG is cut (disconnect VPN and ask).
