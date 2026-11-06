# T27 read-through: F2.5 (docstring vs implementation) and F3.8 (magic literals)

Date: 2026-11-06. Branch `forge_merge-T27-sniff` (from `forge_merge` `ce35945`). Evidence for the two
`sniff_findings.md` rows the owner left open for a human read; the H7 gate itself stays with the owner.

## Scope and method

The F3.1 "hot spots" list in `sniff_findings.md` names seven modules. `extract_terms.py` has since moved
under `app/modules/legacy/` (out of T27 scope), so six were read, every line, top to bottom:

| module | lines (before) | symbols read |
|---|---|---|
| `app/modules/f2w_agent/api.py` | 7,435 | 248 functions (92 module-level, 141 methods, 15 nested), 30 classes |
| `app/modules/kg_rag_api.py` | 2,018 | 52 |
| `app/modules/f2w_agent/retrieval_agent.py` | 1,486 | 38 |
| `app/modules/agent_runtime/supervisor.py` | 564 | 24 (`build_literature_supervisor` and its 17 closures) |
| `app/modules/agents/properties.py` | 505 | 8 |
| `app/modules/term_extractor/tools.py` | 282 | 11 |

For each public function, method and class, and every private helper over about 40 lines, the docstring
was compared with the code: parameter names and defaults, return shape (dict keys, `None` cases), raised
exceptions, side effects (files, state, locks, network, logging). Rules applied:

- A wrong **docstring** is corrected (Google style; the module style check `scripts/ci/docstring_style.py`
  still classifies all six as Google, Sphinx fields 0). Code is never changed to match a docstring.
- Code that looks wrong against its docstring, tests or an obvious contract is **recorded, not changed**
  (tables "Suspected code bugs").
- Docstrings that are part of a contract are **not edited** even when they diverge: FastAPI route
  handlers in `f2w_agent/api.py` (their docstrings are the OpenAPI descriptions pinned by
  `tests/read_path/test_legacy_ui_langgraph_mode.py::test_ui_openapi_is_unchanged`) and `@tool`
  functions in `term_extractor/tools.py` (sent to the LLM as tool descriptions). `kg_rag_api.py`'s six
  routes are not pinned by any test; none of them needed a change.
- **Magic literals** (thresholds, limits, timeouts, retry counts, sizes, score cutoffs, sentinel strings)
  used more than once or carrying meaning were hoisted to module-level `UPPER_SNAKE` constants with a
  one-line comment, value and type unchanged, evaluated at the same time (nothing env- or settings-derived,
  nothing inside f-strings or log messages). Literals that are part of a frozen contract (OpenAPI defaults,
  route paths, wire keys and values, LangGraph node names persisted in checkpoints, LLM-visible tool
  vocabulary) were left and are listed with the reason.

## Totals

| | api.py | kg_rag_api.py | retrieval_agent.py | supervisor.py | properties.py | tools.py | total |
|---|---|---|---|---|---|---|---|
| symbols read | 278 | 52 | 38 | 24 | 8 | 11 | 411 |
| docstrings fixed | 31 | 11 | 6 | 1 | 4 | 0 | 53 |
| suspected code bugs (not changed) | 5 | 2 | 3 | 0 | 3 | 0 | 13 |
| contract docstrings that diverge (owner decision) | 1 | 0 | 0 | 0 | 0 | 3 | 4 |
| constants hoisted | 25 | 17 | 13 | 1 | 1 | 1 | 58 |
| literal occurrences replaced | 68 | 22 | 16 | 2 | 2 | 2 | 112 |
| literals justified-left | 22 | 9 | 8 | 6 | 4 | 4 | 53 |

## Verification (no behaviour change)

- `scratchpad/ast_equiv.py` (not committed): for each file, the `HEAD` and edited ASTs were compared with
  docstrings stripped and every newly added module-level `NAME = <constant>` inlined back at its use sites.
  Four files are AST-identical after that. The two that are not differ only as recorded:
  `kg_rag_api.py` lost the function-local `MAX_SNIPPETS = 6` assignment in `retrieve_nodes` (now module-level
  `MAX_CODE_SNIPPETS`); `retrieval_agent.py` `_beam_path_nodeinfos` divides by `BEAM_PATH_INDEX_SPAN = 50`
  (int) where the source had `/ 50.0`: true division of an int by `50` and by `50.0` gives the same float, so
  the score is unchanged.
- `ruff check app` 0, `isort --check-only app` clean, `mypy` (strict) 0 errors in 352 files.
- Full not-slow suite from a clean export: see `sniff_findings.md`, 2026-11-06 log entry.
- `python -m evals.binary.import_companion --check`: up to date (none of the six modules is a binary-case
  oracle source).
- The OpenAPI fixture `tests/contracts/legacy_ui/fixtures/openapi_ui.json` is unchanged (no route docstring,
  response model default or `Query(...)` default was touched; the three OpenAPI-pinned model defaults that
  now duplicate a hoisted constant were deliberately left as literals, see the api.py notes).

## Suspected code bugs, consolidated (owner decision; nothing changed)

| # | file:line | symbol | observation |
|---|---|---|---|
| B1 | `app/modules/f2w_agent/api.py:3939-3963` | `_resolve_runtime_json_graph_path(..., missing_ok=False)` | both `if missing_ok:` branches raise the identical `FileNotFoundError`; the flag is dead. Only caller `_try_resolve_runtime_json_graph_path` passes `True`; no test covers it. The docstring now says so. |
| B2 | `app/modules/f2w_agent/api.py:7130-7152` | route `graph_neighborhood` | the `hops` query parameter is applied only when `source` is given; the Tiled path (`tiled_neighborhood_payload`) always uses `CITE_FOCUS_HOPS` (3). Tests pass `hops=3`, which equals the constant, so it is undetectable; the OpenAPI advertises `hops`. Route docstring frozen, not changed. |
| B3 | `app/modules/f2w_agent/api.py:6468-6475` | `_resume_download` | `download_selected(..., validate_downloads=False)` is hard-coded although `DownloadAgent` was built with `cfg.validate_downloads` (`_rebuild_agents`, line 2870): PDF validation is silently off for every user-approved download. Possibly intentional (the user picked the paper); undocumented. |
| B4 | `app/modules/f2w_agent/api.py:5572-5700` | `_ask_locked` | retrieval-phase responses use `active_graph_path` (request-selected JSON graph); every stop/error/pending response after the candidate search uses `self.graph_path()` (runtime viewer graph). With a request `json_graph_path` that differs from the runtime selection the graph subset is built against a different file than the verdict. |
| B5 | `app/modules/f2w_agent/api.py:729,747` | `_CORE_NODE_KEYS` | `"relations"` listed twice in the set literal (harmless, cosmetic). |
| B6 | `app/modules/kg_rag_api.py:984-990` | `NodeInfo.score_prp` | comment says "papers from last 3 years get up to 0.1 bonus, decaying linearly for older papers"; code is `0.1 * (1 - age/10)`, full bonus only at age 0. Comment or formula is wrong; `tests/test_kg_rag_api_coverage.py` covers the branch, not the plateau. Now expressed via `PRP_RECENCY_*` constants, values unchanged. |
| B7 | `app/modules/kg_rag_api.py:1554-1558` | `retrieve_nodes` | `seeds = kg.semantic_search(q)[: seed_k * 2]` but `semantic_search` defaults to `topk=DEFAULT_K*2` (line 1254), so the slice is a no-op and `max_nodes > 2*DEFAULT_K` cannot be served from seeds; `seed_k` is computed but never passed as `topk`. |
| B8 | `app/modules/f2w_agent/retrieval_agent.py:545-560` | `reload_kg` | the single-`graph_file` branch never resets `self._skipped`, yet the return includes `"skipped": list(self._skipped)`: a later single-file reload reports stale skip records. |
| B9 | `app/modules/f2w_agent/retrieval_agent.py:1406` | `query` | the leeway pass is gated on `(selected or chunk_count)`, not on `has_mp`: a question answered only by Materials-Project context passes `no_evidence` (which treats MP as evidence) but is denied the leeway synthesis. |
| B10 | `app/modules/f2w_agent/retrieval_agent.py:700` | `search_node_scores` | `backend` is overwritten per graph in the loop; the reported `retrieval_backend` is the last non-Tiled graph's (cosmetic). |
| B11 | `app/modules/agents/properties.py:84-90, 384` | `VALUE_UNIT_PATTERN` / `normalize` | the unit class `[A-Za-zµμ/°\^\-0-9]+` excludes `%` but includes digits: `"efficiency of 85%"` parses as value `8`, unit `5`. The `%` branch of `normalize` is unreachable from `extract`; tests feed `raw_unit="%"` to `normalize` directly (`tests/coverage_t27/test_agents_properties.py:112,126,193`). Worth a ticket. |
| B12 | `app/modules/agents/properties.py:279` | `PLAUSIBILITY_RANGES["density"]` | `(0.0, inf)` inclusive while the comment and module docstring say `density > 0`; `0.0` passes. |
| B13 | `app/modules/agents/properties.py:84` | `VALUE_UNIT_PATTERN` | unit class cannot match `²`, `·`, `⁻`, `(`, `)`, `*`: the unit spellings `normalize` accepts (`cm²/vs`, `s·cm⁻¹`, `w/(m*k)`, `g·cm-3`) are unreachable from `extract` unless another producer feeds `normalize`. |

Contract docstrings that diverge (not edited; owner decision):

| file | symbol | docstring claims | code does |
|---|---|---|---|
| `f2w_agent/api.py:7130` | route `graph_neighborhood` | neighbourhood "from the graph source named by ``source``, else from Tiled" (silent on `hops`) | `hops` honoured only with `source` (B2); frozen OpenAPI description |
| `term_extractor/tools.py:149-154` | `@tool repair_formula` | status is one of `'valid'|'corrected'|'invalid'|'missing'|'error'`; always has `formula` | `ChemicalFormulaValidator.validate` returns `"ok"`, never `"valid"` (`chem_checker.py:44`); the error path returns `{"status":"error","details":{...}}` without `formula` |
| `term_extractor/tools.py:193-196` | `@tool lookup_chebi` | "Returns formula, mass, charge, SMILES, InChI, InChIKey when found" | also returns the bare strings `"ChEBI not available"`, `"not_found"`, `"error:<msg>"` |
| `term_extractor/tools.py:219-226` | `@tool register_term` | inputs only | returns `"registered:<key>"` / `"updated:<key>"` |

Changing any of these four changes what the LLM or the OpenAPI document sees; they are left for the owner.

---

## 1. `app/modules/f2w_agent/api.py`

Summary: 278 symbols read; 31 docstrings fixed (29 corrected or extended, 2 added to undocumented inner
helpers of `create_app`); 5 suspected bugs (B1-B5); 25 constants hoisted (lines 130-154) replacing 68
occurrences; 22 literals justified-left. No route-handler docstring touched.

### Divergences fixed

| symbol | line (after edit) | docstring claimed | code does | fix |
|---|---|---|---|---|
| `_source_paper_list` | 751 | (comment sat between `def` and docstring) | same | moved the format comment below the docstring |
| `list_storage_kg_json_files` | 526 | "List relative paths of JSON graphs in storage/kg." | also appends registered KG bundle paths when `root is None`; bundles alone when storage/kg is missing | documented both |
| `_normalize_node_type` | 1335 | strips prefixes | raises `ValueError` on empty type | added Raises |
| `_normalize_relationship_predicate` | 1351 | validates/coerces | raises `ValueError` for empty, malformed CURIE, bad local name | added Raises |
| `graph_payload_from_file` | 1470 | loads file | propagates `OSError` / `json.JSONDecodeError` | added Raises |
| `graph_subset_from_lookup` | 1576 | "Serialize an in-memory lookup graph" | first widens ids to the `CITE_FOCUS_HOPS` neighbourhood, falls back to per-id resolution, returns empty dict otherwise | documented |
| `_history_payload` | 2244 | "trimmed role/content dicts" | keeps last `MAX_HISTORY_MESSAGES`, drops blanks/other roles, cuts content to 4000 chars | documented |
| `AgentPipelineService._session_key` | 2764 | validate or return legacy key | raises `ValueError("Invalid session ID")` (pinned 400 fixture) | added Raises |
| `_sync_session_graph_from_splash` | 2810 | no return described | returns `bool` (written vs. left alone, failures logged) | added Returns |
| `settings_response` | 2940 | "Build the public settings payload" | also mutates `self.runtime.json_graph_paths/json_graph_path` in JSON mode when empty | documented side effect |
| `apply_settings` | 3087 | free-text "``locked``:" line, no Raises | raises `ValueError` (empty model; JSON mode with no graphs) | Google `Args:`/`Raises:` |
| `search_graph_nodes` | 3288 | "Rank graph nodes matching a text query." | asks for `min(25, 3*limit)` matches, drops `unknown` stubs, raises `ValueError` on blank query | documented |
| `ensure_tiled_lookup` | 3330 | implies a graph is returned | may return `None`/empty graph when GraphQL finds nothing | added Returns |
| `update_graph_node` | 3427 | no Raises | `ValueError`, `FileNotFoundError`, `ContractError(DENIED/UNSUPPORTED_CAPABILITY)`, `StaleSave` (each mapped by the route) | added Raises |
| `_apply_linked_snippet_updates` | 3609 | "Upsert or unlink code snippets" | `updates` is the full desired set: snippets not upserted are unlinked at the end; temp ids mint new things; mutates `data`; mirrors to Splash; raises `ValueError` without a code body | documented |
| `search_publications` | 3821 | no Raises | `ValueError` on blank query | added Raises |
| `save_uploaded_graph` | 3878 | "Validate and write" | `ValueError` unless `things`/`associations` are lists | added Raises |
| `_try_resolve_runtime_json_graph_path` | 3926 | "skipping missing files" | returns `None` for missing **or empty** files; `ValueError` (outside roots) propagates | corrected |
| `_resolve_runtime_json_graph_path` | 3939 | "Resolve and validate" | `missing_ok` has no effect (both branches raise the same error); raises `FileNotFoundError`/`ValueError` | documented (B1) |
| `ask` | 4118 | "Run a chat turn without streaming" | also opens/finishes the trace turn; `auto_approve` loops accepting pending offers (candidate 0) | documented |
| `ask_with_progress` | 4270 | same as above | same | documented |
| `_record_download_decline` | 4452 | "Persist the declined question" | keeps newest 5 distinct (case-insensitive) topics | documented |
| `delete_session_context` | 4799 | "Delete a named chat session's on-disk context." | returns `{"status","session_id"}`; activates legacy context first; `ValueError` for invalid id or legacy session | added Returns/Raises |
| `_ask_locked` | 5312 | "Run the orchestrated retrieval, download, and extraction chat loop." | runs retrieval rounds + candidate search + debate and stops at `awaiting_download_decision`; download/extraction run in `_act_locked`; raises `ValueError` without a JSON graph file | rewritten |
| `_pick_candidate` | 5790 | "plus a few alternatives" | up to three (`[:3]`) | made precise |
| `_extract_rebuild_reload` | 5803 | "Returns ChatResponse only on failure/no-op" | also fills `round_info` keys and records extraction on the coordinator | documented |
| `_act_locked` | 6325 | no Raises | `ValueError` for kind mismatch, bad/unavailable `candidate_index`, orchestrator refusal | added Raises |
| `_resume_download` | 6422 | "and continue the workflow" | pauses on `awaiting_extraction_decision`; on zero downloads marks candidate unavailable and re-offers | corrected |
| `_resume_extraction` | 6579 | "Extract ... and continue the workflow" | re-queries KG, builds extraction report + verdict + domain-knowledge fallback answer, finalizes memory on every path | documented |
| `create_app._source_payload` | 7072 | (no docstring) | full wire payload of a source graph | added |
| `create_app._source_failure` | 7080 | (no docstring) | `HTTPException` from a `SourceError` | added |

All other route docstrings (`health`, `get_settings`, `update_settings`, `reset_session`, `delete_session`,
`graph`, `search_graph_nodes`, `graph_node`, `patch_graph_node`, `upload_graph`, `chat`, `chat_action`,
`publication_search`, `chat_stream`, `chat_action_stream`, `get_trace`, `telemetry_ui`, `create_annotation`)
match their bodies.

### Magic literals

| literal | occurrences (lines, after edit) | meaning | action |
|---|---|---|---|
| `60` | 4758 5079 5125 5161 5222 5305 6156 | timeout of every helper LLM completion | hoisted to `LLM_HELPER_TIMEOUT_SECONDS` |
| `4000` | 2259 | max history message content forwarded | hoisted to `HISTORY_CONTENT_MAX_CHARS` |
| `800` | 823-824 | node extra-field string truncation | hoisted to `EXTRA_FIELD_TEXT_MAX_CHARS` |
| `24` | 829 | node extra-field list cap | hoisted to `EXTRA_FIELD_LIST_MAX_ITEMS` |
| `16` | 832 | node extra-field dict cap | hoisted to `EXTRA_FIELD_DICT_MAX_ITEMS` |
| `600` | 6842 | abstract length on pending-paper card | hoisted to `CANDIDATE_ABSTRACT_MAX_CHARS` |
| `1600` | 6876 | abstract length on approved publication | hoisted to `PUBLICATION_ABSTRACT_MAX_CHARS` |
| `5` (`[:5]`) | 4986 5615 5647 | candidates listed to user / in progress event | hoisted to `MAX_CANDIDATE_PREVIEW` |
| `5` (`[-5:]`) | 4465 | declined download topics remembered | hoisted to `MAX_DECLINED_DOWNLOAD_TOPICS` |
| `0.5` | 4484 | declined-topic word-overlap threshold | hoisted to `DECLINED_TOPIC_OVERLAP_MIN` |
| `0.5` | 6115 | candidate title word-overlap threshold | hoisted to `CANDIDATE_TITLE_MATCH_MIN` |
| `6` | 575 2736 | targeted_max_pages default (dataclass + fallback) | hoisted to `DEFAULT_TARGETED_MAX_PAGES`; the `AgentSettingsResponse` default (451) left: frozen OpenAPI |
| `"lbl/cborg-chat"` | 571 649 | default CBORG model | hoisted to `DEFAULT_CBORG_MODEL` |
| `"deepseek-r1:70b"` | 643 | default Ollama model | hoisted to `DEFAULT_OLLAMA_MODEL`; `AgentSettingsResponse` default (468) left: frozen OpenAPI |
| `"rel:related_to"` | 1506 1569 1634 1759 3583 3648 3750 | default edge predicate | hoisted to `DEFAULT_PREDICATE`; `GraphEdge.predicate` default (261) left: frozen OpenAPI |
| `"rel:has_code_snippet"` | 3648 3667 3673 3732 3738 3750 3766 3772 3796 3813 | node-to-snippet predicate | hoisted to `HAS_CODE_SNIPPET_PREDICATE` |
| `"CodeSnippet"` | 1447 3714 3725 3801 | MatKG category | hoisted to `CODE_SNIPPET_CATEGORY` |
| `"unknown"` | 3314 7121 | stub node type hidden from search | hoisted to `UNKNOWN_NODE_TYPE` |
| `"source-title::"` | 1065 2484 2611 | empty publication merge key | hoisted to `EMPTY_PUBLICATION_KEY` |
| `"tiled://graphql"` | 1646 1728 3359 | Tiled payload `source_path` value | hoisted to `TILED_SOURCE_PATH` (value unchanged on the wire) |
| `"Tiled Graph"` | 1610 7172 7175 | Tiled `graph_label` fallback | hoisted to `TILED_GRAPH_LABEL` |
| `"__legacy__"` | 2707 2772 4810 | legacy session key | hoisted to `LEGACY_SESSION_KEY` |
| `"chat_sessions"` | 2795 4816 | per-session context dir | hoisted to `CHAT_SESSIONS_DIRNAME` |
| `"session_memory.json"` | 2701 2796 | memory file name | hoisted to `SESSION_MEMORY_FILENAME` |
| `"workflow_state.json"` | 2702 2797 | workflow file name | hoisted to `WORKFLOW_STATE_FILENAME` |
| `"codesnippet"` | 1111 3476 | lower-cased category comparison | left: compared against `.lower()`; a constant would need `.lower()` at call time |
| `"matkg:CodeSnippet"` | 1448 | typed category | left: once, schema vocabulary |
| `[:40]`, `[:8]` | 1300-1301 | snippet id slug/hash lengths | left: id-format definition, single site |
| `keywords[:10]` | 2555 | OpenAlex keyword cap | left: once, local |
| `min(max_results * 2, 50)` | 2576 | OpenAlex per_page cap | left: once; 50 mirrors `PublicationSearchRequest.max_results le=50` (frozen) |
| `timeout=5` | 2834 | Splash graph load timeout | left: once, single call |
| `0.98 0.72 0.015 0.04 0.05 0.35` | 2400-2404 | confidence heuristic coefficients | left: one formula, one site |
| `0.35 0.2 0.25 0.03` | 2441-2445 | publication relevance weights | left: one formula, one site |
| `12` | 2879 | `max_orchestration_steps` getattr default | left: once |
| `"http://127.0.0.1:8000"` | 3148 | Tiled URI fallback | left: once |
| `max_terms=6` | 6677 | post-extraction term summary size | left: once |
| `[:4]`, `[:3]` | 5735 5739 5806 | alternatives shown (4 cards; best + 3) | left: coupled counts, rewriting as `N-1` would change the expression |
| `range(1, 3)` / `preflight_no == 1` | 5559 | two preflight candidate searches | left: loop bound tied to a condition |
| `max_papers=1` | 6472 | one approved download | left: obvious |
| `"uploaded_graph.json"` | 348 3873 | upload filename default | left: 348 is a frozen OpenAPI default; 3873 mirrors it |
| `min(25, max(limit*3, limit))` | 3301 | search over-fetch cap | left: 25 mirrors `GraphNodeSearchRequest.limit le=25` (frozen) |
| `"idle"` and other phase names | 431 4208 4362 ... | workflow phase vocabulary | left: state/wire vocabulary shared with `workflow_state.py` and the UI |
| `\w{4,}` | 4476 4480 | regex token length | left: inside regex literals |
| `"lexical"`, `"lexical:source"` | 3326 7122 | retrieval_backend wire values | left: frozen UI contract values |
| `"Entity"`, `"Thing"` | 1175 2607 | default categories | left: once each |

### Notes

- `_maybe_report_active_extraction` (about line 4620) sets the workflow phase to `paper_answered` /
  `paper_insufficient` while the response status is `extraction_reported`; the phase vocabulary does not
  distinguish a paper answer from an extraction report. Looks like deliberate reuse; flagged only.
- `_try_resolve_runtime_json_graph_path` has a no-op `except ValueError: raise`; `_apply_relationship_updates`
  re-runs `_validate_relationship_updates` that `update_graph_node` already ran. Harmless; not changed.
- Docstrings left alone because the reader was unsure: `_maybe_query_active_paper` /
  `_maybe_report_active_extraction` take `force`/`decide` positionally and the one-liners do not describe
  them; `default_runtime_model` resolves env vars (`KG_RAG_*`, `F2W_MODEL`) and config before the built-in
  default, which its summary line only implies.
- The three OpenAPI-pinned model defaults (`AgentSettingsResponse.targeted_max_pages=6`,
  `.default_ollama_model="deepseek-r1:70b"`, `GraphEdge.predicate="rel:related_to"`) now duplicate
  `DEFAULT_TARGETED_MAX_PAGES`, `DEFAULT_OLLAMA_MODEL`, `DEFAULT_PREDICATE`. Switching them to the constants is
  a provable no-op for the generated schema but is an owner call under the frozen-OpenAPI rule.

---

## 2. `app/modules/kg_rag_api.py`

Summary: 52 symbols read (module helpers, `ModelTurnSources` + 5 methods, `create_fastapi_app` + 4 inner
helpers + 6 routes, `MissingNodeTracker` + 2, `NodeScore`/`NodeInfo`, `KnowledgeGraph` + 10 methods,
orchestrator, clients, `Conversation`, CLI); 11 docstrings fixed; 2 suspected bugs (B6, B7); 17 constants
hoisted (lines 197-222) replacing 22 occurrences; 9 literals justified-left. The six route docstrings
(`api_chat`, `openai_chat_completions`, `list_models`, `ollama_version`, `openai_models`, `list_processes`)
match their handlers and were not edited.

### Divergences fixed

| symbol | line (after edit) | docstring claimed | code does | fix |
|---|---|---|---|---|
| `create_fastapi_app` | 364-366 | Returns "application with /v1/chat/completions endpoint" | registers six routes | listed them (not a route docstring; no OpenAPI effect) |
| `extract_query_entities` | 651 | "≥3-char tokens" | same, value now a constant | references `MIN_TOKEN_CHARS` |
| `snippet_text` | 698-701 | "centered around hint term occurrences" | window starts `length//4` before the first hit (hit at the quarter mark, not centred); falls back to text start | reworded |
| `_splash_base_url` | 835 | no exception noted | raises `ValueError` for other schemes | noted |
| `_splash_graphql` | 845-849 | "return data payload" | raises `requests.HTTPError` (`raise_for_status`) and `RuntimeError` on GraphQL errors | noted |
| `KnowledgeGraph._lexical_search` | 1231-1234 | "Jaccard-like" | score = overlap/\|q\| + 0.5*name_hits, capped at 1; empty term set returns `[]` uncached | reworded |
| `KnowledgeGraph.build_context` | 1352-1366 | `char_budget: Maximum character budget`; no side effects | soft budget (break after the section that crosses it, triples counted); PDF snippets appended to the `capture_pdf_snippets` ContextVar; CodeSnippet nodes without code skipped; ANSI stripped | Args/Returns corrected |
| `retrieve_nodes` | 1541-1552 | one-liner, `hops`/`max_nodes` undescribed | `None` -> `MAX_BFS_HOPS` / `DEFAULT_K`; `hops=0` disables BFS; `max_nodes<1` clamps to 1; CodeSnippet cap | Google Args/Returns added |
| `CBorgClient.chat` | 1723-1726 | "Send messages and return response" | streams; converts connection/timeout/auth errors to `RuntimeError` | noted |
| `Conversation.build` | 1780 | "optionally prepending a system message" | the extra system message is appended after history, just before the user turn | reworded |
| `call_llm` | 1870-1874 | "timeout handling, printing status" | also records every outcome via `record_llm_call`; timeout = `LLM_TIMEOUT + 5` re-raised as `RuntimeError` | noted |

### Magic literals

| literal | occurrences | meaning | action |
|---|---|---|---|
| `3` | 652, 653, 1238 | min token length (entities, lexical terms) | hoisted `MIN_TOKEN_CHARS` |
| `3` | 1536 (`decompose`) | min sub-question segment length | left: different concept, one-off |
| `0.5` | 1248 | name-match boost per token | hoisted `LEXICAL_NAME_HIT_BOOST` |
| `5.0` | 984 | tanh scale for evidence count | hoisted `PRP_EVIDENCE_TANH_SCALE` |
| `0.1`, `10.0` | 990 | recency max bonus / decay years | hoisted `PRP_RECENCY_MAX_BONUS`, `PRP_RECENCY_DECAY_YEARS` (B6) |
| `"RELATED_TO"` (x2), `1.5`, `1.2` | 1285-1287 | BFS edge weights and the generic predicate suffix | hoisted `RELATED_TO_PREDICATE_SUFFIX`, `BFS_EDGE_W_SPECIFIC`, `BFS_EDGE_W_RELATED` |
| `/ 100.0` | 1287 | rapidfuzz percent scale | left: obvious |
| `MAX_SNIPPETS = 6` (function-local) | was 1539 | CodeSnippet cap | hoisted to module `MAX_CODE_SNIPPETS`; comment updated |
| `"?"` (x2) | 1330, 1393 | unknown category marker rendered into context | hoisted `UNKNOWN_CATEGORY`; value unchanged so prompt text unchanged |
| `16`, `32` | 1138 | encode batch size cuda/cpu | hoisted `ENCODE_BATCH_CUDA`, `ENCODE_BATCH_CPU` |
| `64`, `32` (x2) | 1173, 1186, 1197 | FAISS nlist floor, nprobe ceiling | hoisted `FAISS_MIN_NLIST`, `FAISS_MAX_NPROBE` |
| `* 2`, `// 4` | 1173, 1186, 1197 | nlist/nprobe formula factors | left: one-off formula factors |
| `timeout=30` | 851 | splash GraphQL HTTP timeout | hoisted `SPLASH_GRAPHQL_TIMEOUT_S` |
| `+ 5` | 1880 | grace over `LLM_TIMEOUT` for `wait_for` | hoisted `LLM_TIMEOUT_GRACE_S`; `LLM_TIMEOUT` itself is rebound in `__main__`, the sum is still evaluated at call time |
| `[:16]` | 300 | snippet digest length in the source version string | hoisted `SNIPPET_DIGEST_HEX_CHARS`; the test pins only the `kg-rag-snippet:sha256:` prefix |
| `[:500]` (x2) | 1640-1641 | console preview truncation | left: console output text |
| `limit=2` | 778 | "more than one source?" probe | left: one-off, reads as `<= 1` check |
| `4096` | 690 | CUDA warm-up matmul size | left: one-off |
| `"kg-rag:latest"`, `"0.1.0"`, `"2025-09-17T00:00:00Z"`, `chatcmpl-` | routes | OpenWebUI/OpenAI response bodies | left: frozen wire contract |
| env defaults (`"12"`, `"1_000"`, `0.75`, ...) | 137-199 | configuration | left: already named module constants |

---

## 3. `app/modules/f2w_agent/retrieval_agent.py`

Summary: 38 symbols read (13 module-level, `RetrievalAgent` + 24 methods); 6 docstrings fixed; 3 suspected
bugs (B8-B10); 13 constants hoisted (lines 95-113) replacing 16 occurrences; 8 literals justified-left.

### Divergences fixed

| symbol | line (after edit) | docstring claimed | code does | fix |
|---|---|---|---|---|
| `_has_direct_evidence` | 296-302 | "For conceptual questions, a description or technique category is also enough" | function never sees the question; description/category/evidence-bearing edge count for every question (the conceptual gate lives in `query`'s `no_evidence` expression) | reworded; points to `query` |
| `RetrievalAgent._build_kgs` | 479-482 | "Load each configured JSON KG" | honours `graph_source` (json or splash); also overwrites `self._skipped` | reworded + side effect |
| `RetrievalAgent.reload_kg` | 531-544 | "Missing/empty files are skipped" | skipping only on the `graph_files` branch (`_build_kgs`); the single `graph_file` branch uses `_build_kg`, which raises on a bad file; return dict undocumented | Args/Returns added, branch behaviour stated |
| `RetrievalAgent._ensure_graphs` | 593 | "raise if none can be opened" | raises `RuntimeError` | exception named |
| `RetrievalAgent._merge_live_tiled` | 643-647 | merges into graphs/kg_hits | also rewrites `self._last_tiled_meta` every call and stores the Tiled graph in `self._graphs` | side effects added |
| `RetrievalAgent.query` | 1215-1234 | one-liner | returns a 3-status dict (`success`/`context_error`/`judge_error`) with about 15 keys, `error` on the error statuses, never raises on retrieval/context/judge failure, sets `_trace_*`/`_last_*` attrs | Args/Returns/side effects added |

### Magic literals

| literal | occurrences | meaning | action |
|---|---|---|---|
| `8` | 229 (`history[-8:]`) | chat turns shown to the judge | hoisted `JUDGE_HISTORY_TURNS` |
| `1200` | 234 | per-turn content cap in the judge prompt | hoisted `JUDGE_HISTORY_CONTENT_CHARS` |
| `2.0`, `50` (x2) | 390 | beam-path promoted-stage score base and index span | hoisted `BEAM_PATH_SCORE_BASE`, `BEAM_PATH_INDEX_SPAN` (divisor `50.0` -> int `50`, same float result) |
| `"rel:beam_path_next"` | 115 (set) and 373 | the successor predicate walked for layout order | hoisted `BEAM_PATH_NEXT_PREDICATE`, used in `_BEAM_PATH_PREDICATES` too |
| `4` | 807 | min node-name length for substring fallback | hoisted `SUBSTRING_FALLBACK_MIN_NAME_CHARS` |
| `1.5` (x2) | 818, 828 | fixed score of substring-fallback hits | hoisted `SUBSTRING_FALLBACK_SCORE` |
| `2` | 875 | min papers for popularity supplement | hoisted `POPULAR_NODE_MIN_PAPERS` |
| `0.3`, `100.0` | 884 | popularity score = base + papers/scale | hoisted `POPULAR_NODE_SCORE_BASE`, `POPULAR_NODE_PAPERS_SCALE` |
| `0.6`, `0.4` | 940, 942 | intent-primary / secondary share of candidate slots | hoisted `INTENT_PRIMARY_SHARE`, `INTENT_SECONDARY_SHARE` |
| `0.3` | 963 | supplement threshold as a fraction of max_nodes | hoisted `LITERATURE_SUPPLEMENT_SHARE` |
| `question[:80]` | 946, 976 | log-only truncation | left: inside log args |
| `limit: int = 10` | `search_node_scores`, `_substring_device_fallback` | signature defaults | left: parameter defaults (API) |
| `"BeamlineStage"` | 388 | default category for promoted stages | left: one-off obvious default |
| `"KG-RAG-judge"`, `"KG-RAG-leeway"` | 1367, 1518 | trace labels for `call_llm` | left: trace wire values, one-off each |
| `"LAYOUT_LEEWAY_SYSTEM"/"LEEWAY_SYSTEM"` | 1512 | trace field value | left: trace wire value |
| `suffix = 2` | 456 | collision-suffix start | left: obvious |
| empty hybrid dict (6 keys) | 1181-1188 and 1248-1255 | duplicated literal dict | left (structure, not a scalar; a simplification candidate) |
| `{"true","yes","1"}` | 128 | bool coercion | left: one-off |

---

## 4. `app/modules/agent_runtime/supervisor.py`

Summary: 24 symbols read; 1 docstring fixed (three clauses of `build_literature_supervisor`); 0 suspected
bugs; 1 constant hoisted (`TOOL_MESSAGE_MAX_REFS`, line 119) replacing 2 occurrences; 6 literals
justified-left. LLM prompt strings (`PLACEHOLDER_SYSTEM_PROMPT`, placeholder ToolMessage note, `[runtime] ...`
texts) untouched.

### Divergences fixed

| symbol | line (after edit) | docstring claimed | code does | fix |
|---|---|---|---|---|
| `build_literature_supervisor` | 213 | `limits: RuntimeLimits.` (no default stated) | `limits or RuntimeLimits()` | added "``None`` means ``RuntimeLimits()`` defaults" |
| `build_literature_supervisor` | 219 | `approval_check` default unstated | `approval_check or PlanApprovalCheck()` | added default |
| `build_literature_supervisor` | 225-226 | Returns: compiled graph; Raises only `ValueError` | every node calls `owner_update`, which raises `ContractError(DENIED)` at invoke time for a foreign owner | noted in Returns (an invoke-time raise, not a build-time one) |

Verified against neighbours, no change needed: "without an event sink write tools are refused"
(`NullEventSink.durable=False`, `executor.py:372`), `open_approvals` None-closes-entry reducer (`state.py:27`),
`visible_to`/`render_prompt_section` signatures, `RuntimeLimits` field names.

### Magic literals

| literal | occurrences | meaning | action |
|---|---|---|---|
| `50` | 157, 158 | cap on `evidence_ids` / `artifact_refs` echoed into the ToolMessage body | hoisted `TOOL_MESSAGE_MAX_REFS` |
| node names `"supervisor"`, `"request_approval"`, `"await_approval"`, `"apply_approvals"`, `"execute_tools"` | routers + graph wiring, about 25 uses | LangGraph node ids | left: frozen (persisted in checkpoints / `next` of saved threads) |
| `"approval_required"`, `"awaiting_approval"` | 388, 346 | interrupt payload kind / placeholder status | left: wire format seen by resume callers and tests |
| `"[runtime] Stopped: "`, `"[runtime] Approval outcome: "` | 167, 476 | user-visible message prefixes | left: message text (f-strings) |
| artifact keys `call_key/cost/charged/repeated/result` | 159, 139-145 | `ToolMessage.artifact` schema shared by `tool_message`/`ledger_from_messages` | left: frozen (persisted in checkpoints) |
| `shown` tuple of plan fields | 383 | fields of a plan exposed in the interrupt | left: one-off, already a named local |

---

## 5. `app/modules/agents/properties.py`

Summary: 8 symbols read (2 classes, `__init__`, `_is_proximal`, `_matches_pattern`, `extract`,
`extract_from_relations`, `normalize`); 4 docstrings fixed; 3 suspected bugs (B11-B13); 1 constant hoisted
(`NORMALIZED_DECIMALS`, line 35) replacing 2 occurrences; 4 literals justified-left.

### Divergences fixed

| symbol | line (after edit) | docstring claimed | code does | fix |
|---|---|---|---|---|
| `PhysicalPropertyExtractor` (class) | 54-55 | `verified: True (initial assumption; can be overridden)` | `extract` sets True only when a numeric value was found, False otherwise; `extract_from_relations` copies the relation's flag | stated both rules |
| `PhysicalPropertyExtractor.__init__` | 98-99 | override for `PROPERTY_KEYWORDS` | `property_keywords or self.PROPERTY_KEYWORDS`: an empty mapping falls back to the default | noted |
| `extract_from_relations` | 222-223 | `property: ... else raw related_term lowercased` | `raw_prop.replace(" ", "_")` (spaces -> underscores) | stated |
| `PropertyNormalizer.normalize` | 289-312 | no failure semantics, no raise, "float or None" | copies entries; pass-through when raw_value/raw_unit missing; unknown unit / non-numeric / implausible -> `unit_conversion_failed=True`, normalized fields None, logged at DEBUG; unknown property passes raw unit; rounds to 6 dp; `e["property"]` raises `KeyError` | all stated; `Raises: KeyError` added |

### Magic literals

| literal | occurrences | meaning | action |
|---|---|---|---|
| `6` (x2) | 500, 503 | decimal places on normalized value/uncertainty | hoisted `NORMALIZED_DECIMALS` |
| `273.15` | 333 | Celsius -> Kelvin offset | left: universally known constant, once |
| `1000.0`, `100.0`, `1e-4` | many, in `normalize` | unit conversion ratios, each next to the unit names it converts and a comment | left: ratios obvious from context |
| `PROXIMITY_WINDOW = 30` | 88 | already a named class constant | left |
| `[:1]` | 178 | keep first value only | left: obvious |

---

## 6. `app/modules/term_extractor/tools.py`

Summary: 11 symbols read (`ToolState` + 3 methods, `build_tools`, 6 `@tool` closures); 0 docstrings fixed
(all non-`@tool` docstrings match); 0 suspected bugs; 3 `@tool` descriptions diverge and are recorded above
for the owner; 1 constant hoisted (`POSSIBLE_MATCHES_LIMIT`, line 21) replacing 2 occurrences; 4 literals
justified-left.

For awareness only: `repair_formula` line 172 `.split()[0]` on an empty LLM reply raises `IndexError`, but it
is inside the `except Exception` that returns the original validation, so the behaviour is correct by accident.

### Magic literals

| literal | occurrences | meaning | action |
|---|---|---|---|
| `5` (x2) | 94, 96 (log arg and return) | max candidates in `possible_matches:` | hoisted `POSSIBLE_MATCHES_LIMIT` |
| `"Thing"` | 270 | default category | left: one-off default |
| `"missing"`, `"corrected"`, `"error"`, `"match:"`, `"no_match"`, `"not_found"` etc. | various | tool reply vocabulary read by the LLM | left: frozen tool-output contract |
| `r"[A-Z][a-z]?[\d]"` | 155 | formula sniff regex | left: one-off pattern |
| repair prompt text | 166-170 | LLM prompt | untouched |
