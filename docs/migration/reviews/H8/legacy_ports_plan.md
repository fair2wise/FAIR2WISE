# Real ports of the retained legacy modules (T17B, H8)

Status: branch `forge_merge-T17B-ports` (not merged). Owner decisions: 2026-11-04 "reclassify as retained, stay in
place. add a note in the addendum we want real ports"; 2026-11-06 "start the work now". Addendum section
"Legacy modules retained for real ports" in `docs/migration/plan_addenda.md`; target homes from
`legacy_audit.md` section 8.

Rules every port follows: the class (r) wire behaviour of `f2w_agent/api.py` is unchanged
(`tests/contracts/legacy_ui` passes unchanged); no new network reachability; no new secrets paths; the old import
path stays importable as a deprecated alias shim until the H8 shim removal; mypy strict, `ruff check app`,
`isort --check-only app`, full not-slow suite green in a clean export.

**Shim mechanism.** The old module file assigns `sys.modules[__name__]` to the new module, so the old path *is* the
new module object. This is deliberate: the contract tests and the closure harness patch attributes on the old path
(`monkeypatch.setattr(api_mod.kg_update, ...)`, `tiled_graph.tiled_graphql`), and a plain re-export shim would
leave those patches unobserved by the callers. New code imports the new path only.

## Ported in this run

### 1. `tiled_graph.py` -> `app/modules/graph/backends/tiled_lookup.py`

| | |
|---|---|
| What | Live experiment-identity lookup (`lookup_experiment_identity`), Settings probe (`probe_tiled_status`), config resolution (`load_tiled_config`, `coerce_local_tiled_uri`), the overlay graph (`TiledLookupGraph`, `TiledLookupResult`) and the cite-click helpers (`resolve_tiled_node_id`, `tiled_neighborhood_ids`), plus the question classifier `is_experiment_identity_question`. |
| Callers moved | `f2w_agent/api.py`, `f2w_agent/retrieval_agent.py`, `tiled_sim.py` (lazy). Tests repointed: `tests/test_tiled_graph.py`, `tests/coverage_t27/test_tiled_graph_lookup.py`, `tests/evals/test_route_regex.py`, `tests/test_f2w_api.py`. Unchanged: `tests/contracts/legacy_ui/**` (alias). |
| Semantics | Unchanged: status strings, Settings snapshot keys, node shape, error texts, OP07 paging cap (`PAGE_SIZE * MAX_PAGES = 800` per type), the `nodeId` fallback query, the optional `tiled.client` catalog walk. `invalid_uri` remains unreachable through `load_tiled_config` (malformed URIs coerce to the default), as in the baseline. |
| Typing / structure | `JsonObject`/`JsonValue`/`Untyped` aliases instead of bare `Any`; Google docstrings; no module-level side effects (environment is read at call time). `TiledLookupClient` modelled on `TiledGraphClient`: credential is a `CredentialProvider` evaluated per request, never stored as a header, absent from `repr`, redacted from error text (`_safe_error`). Default provider is the process `TILED_API_KEY` (the contract's documented source; never a user token). `lookup_experiment_identity` and `probe_tiled_status` take an optional `client: LookupTransport`; the default transport routes through the module-level `tiled_graphql`, which keeps the substitution point the retained tests and `tiled_sim` use. |
| Not reused | `TiledGraphClient` itself: it validates the target against a deployment profile (rejects hosts the chat Settings page accepts), reads credentials from runtime config only, and maps GraphQL/HTTP failures to `ContractError`; all three would change the Settings statuses and error texts the contract pins. The canonical read path keeps `TiledGraphClient`; the lookup reuses its `GRAPHQL_PATH`/`graphql_endpoint_for` only. |
| Tests added | `tests/graph/test_tiled_lookup.py`: alias identity and patch-through, client never exposes the credential, structural GraphQL errors, paging and overlay graph via a fake transport, the `nodeId` fallback, every status, key redaction, probe statuses, default-transport routing, and one run against a disposable Tiled 0.2.18 server (injected client and the environment-key default path). |

### 2. `f2w_agent/kg_update.py` -> `app/modules/graph/kg_update.py`

| | |
|---|---|
| What | `rebuild_kg` (terms -> MatKG JSON via `json2kg`), `publish_extraction` (T18E; `publish_state` mapping), the non-destructive Splash operations (`splash_find_entity_by_matkg_id`, `splash_update_entity`, `splash_create_entity`, `splash_create_link`, `splash_find_links`, `splash_delete_link`, `load_splash_graph`, `export_splash_graph_to_json`). |
| Duplication check | None found. `graph/releases.py` is release verification, `graph/imports/*` is the JSON -> Tiled publication path, `library/bundles.py` is bundle manifests, `graph/legacy_save.py` covers only the Save target. `kg_rag_api.py` has its own `_splash_base_url` for retrieval reads (class (r), different call shape); not merged in this run. |
| Callers moved | `f2w_agent/api.py`, `f2w_agent/coordinator.py`, `library/extraction.py`. Tests repointed: `tests/coverage_t27/test_kg_update_graphql.py`, `tests/api_wiring/test_wiring_post_extraction.py`, `tests/library/test_library_extract.py`, `tests/test_kg_to_terms_pairs.py`. Unchanged: `tests/contracts/legacy_ui/**`, `tests/ci/test_workflows.py` (alias). |
| Semantics | Unchanged. Reachability unchanged: the Splash operations still talk only to the configured `splash://` endpoint (`KG_RAG_SPLASH_URI` / `SPLASH_LINKS_URI`, loopback default) over `urllib`, with no credential; nothing was added. |
| Tests added | `tests/graph/test_kg_update.py`: new home, alias pin, callers share the module object, `splash_uri_default` read at call time, `publish_state` mapping (including an unmapped status passing through). |

### 3. `download_agent.py`: audit result, then P1 (done, see below)

The audit found no direct `requests`/`urllib` fetch in the agent: PDF bytes flow only through
`literature.legacy_download.hardened_download_pdf` (T29). Three *metadata* hops sat outside the hardened egress
policy: OpenAlex through `pyalex.Works`, arXiv through the `arxiv` package, Unpaywall through
`download_pdfs.fetch_unpaywall_oa_pdfs` (`requests`). They are ported by P1 below; `tests/literature/test_download_agent_egress.py`
now pins the hop set as empty.

### 4. `bl1101_ingest.py` slice a: `specs.py` -> `app/modules/graph/imports/bl1101/specs.py` (P3 slice a, done)

| | |
|---|---|
| What | Data-definition slice only: identity constants (`SCHEMA_VERSION`, `INGEST_VERSION`, `NAMESPACE`, `BEAMLINE_ID`, `ALS_*`), `RepoSpec` and `REPO_SPECS` (eight upstream repos), `BlueprintDevice`/`BlueprintStage`, `STAGE_NAME_ALIASES`, `KIND_CATEGORY`, `SOFTWARE_ALIAS`, beam-path constants, person/email patterns and skip lists, `USER_AGENT`. Imports only `dataclasses` and `re`; no paths, no I/O. Paths (`REPO_ROOT`, `KG_DIR`, `CACHE_DIR`, ...) stay with the code that touches files. |
| Wiring | `bl1101_ingest` imports every name from `specs` and re-exports it (`X as X`), so the legacy path exposes the same objects (the T27 tests that `monkeypatch.setattr(bi, "REPO_SPECS", ...)` keep working). `tiled_sim` now imports `BEAMLINE_ID`/`NAMESPACE` from `specs`. `hybrid_rag/sources.py` still reads `REPO_SPECS` through `bl1101_ingest`: `tests/coverage_t27/test_hybrid_rag_pipeline_extra.py` patches it there, so repointing waits for slice b/an owner-approved test edit. |
| Wire behaviour | None. `matkg_bl1101_v*.json` frozen history untouched; nothing regenerated. |
| Tests added | `tests/graph/test_bl1101_specs.py` with `tests/graph/fixtures/bl1101_specs_golden.json`, captured from the pre-port module at `forge_merge` (every definition compared, including regex flags and dict order), re-export identity, frozen `RepoSpec`, independent dataclass defaults, alias helper through the legacy path, no-side-effect import (fresh interpreter) and an imports-only-`dataclasses`/`re` check. |

**Remaining P3 (not started):** `fetch.py` (hardened fetch, `git` sync behind subprocess guards), `parse_als.py`, `parse_blueprint.py`, `symbols.py`, `terms.py` (`make_term`, `merge_terms`, `attach_repo_terms`, motor-stage linking), `snapshot.py` (via `graph.snapshot.create_snapshot`), `cli.py`, then the old path becomes the `sys.modules` alias shim, `hybrid_rag` repointed, and the v7 -> v8 `--from-graph` golden replay plus HTML fixture parser tests. Estimate now 3-4.5 days.

## Proposals (owner scheduling needed; P1 is done, P3 slice a is done)

### P1. `download_agent` metadata hops onto `hardened_get_json` (DONE, branch `forge_merge-T17B-p1`, not merged)

| | |
|---|---|
| Scope | Replace `pyalex.Works` (search with and without the OA filter, work refresh by id), `arxiv.Client` (Atom API) and `download_pdfs.fetch_unpaywall_oa_pdfs` with calls through `literature.legacy_download.hardened_get_json` (policy on every hop, 8 MiB cap). OpenAlex: `GET https://api.openalex.org/works?search=...&filter=open_access.is_oa:true&per-page=N&mailto=...`; arXiv: `GET https://export.arxiv.org/api/query?...` with the existing Atom parsing moved into a small helper (the `arxiv` package is dropped); Unpaywall: `GET https://api.unpaywall.org/v2/{doi}?email=...` then the existing `select_unpaywall_oa_pdfs`. |
| Target home | `app/modules/literature/legacy_download.py` gains `openalex_search`, `openalex_work`, `arxiv_search`, `unpaywall_oa_pdfs` (hardened); `download_agent.py` calls only those. `download_pdfs.fetch_unpaywall_oa_pdfs` stays as the parity oracle for its test, like `download_pdf`. |
| Wire behaviour | Candidate records (`id`, `doi`, `title`, `abstract`, `publication_year`, `pdf_urls`, `oa_url`, `repository`, `_score`) must be byte-identical for the same API JSON; `_search_one`'s retry-without-OA-filter order and the warning texts are kept. |
| Tests | Parity tests feeding the same recorded OpenAlex/arXiv/Unpaywall JSON to the old (pyalex/arxiv/requests) and the new path (`httpx.MockTransport`); rewrite the `FakeWorks` fakes in `tests/test_download_agent.py`, `tests/coverage_t27/test_download_agent_extra.py` and the contract harness to fake the JSON fetcher instead; extend `test_download_agent_egress.py` to assert the hop set is empty. |
| Risks | pyalex adds `mailto` and user-agent conventions and handles cursor paging; relevance scores come from the API as `relevance_score` and must map to `_score` identically. The `arxiv` package applies a polite delay the new helper must keep (`OA_HOST_DELAYS`). The contract harness fakes `DownloadAgent` wholesale (`api_mod.DownloadAgent`), so the legacy UI tests are unaffected, but `tests/test_download_agent.py` is a rewrite. |
| Size | 1-2 days (helpers 0.5 d, parity fixtures and test rewrite 1 d). |

**Done (2026-11-06).** `legacy_download.py` gained `hardened_get_bytes` (the transport of `hardened_get_json`, which now
wraps it unchanged), `openalex_search`, `openalex_work`, `arxiv_search` + `parse_arxiv_atom`, `unpaywall_oa_pdfs`.
`DownloadAgent` calls only those, with the getter passed explicitly from its module globals (so
`monkeypatch.setattr(da, "hardened_get_json", ...)` is the fake point, as `hardened_download_pdf` already is).
`_search_one(search_text, pool)` lost its `works` factory argument (private; callers were the agent and its tests).
`download_pdfs.fetch_unpaywall_oa_pdfs` stays as the parity oracle.

- Hosts: `api.openalex.org`, `export.arxiv.org`, `api.unpaywall.org` (the hosts the old clients used; the egress policy has no
  host allowlist, it checks scheme, port 80/443, credentials and public addresses on every hop). No host added.
- Parity (`tests/literature/test_download_agent_metadata_parity.py`, golden fixtures in
  `tests/literature/fixtures/download_agent_metadata/`): the real pyalex, `arxiv` package and `fetch_unpaywall_oa_pdfs` are driven
  offline with a recorded answer and compared with the new helpers on the same answer: OpenAlex records equal (including
  `relevance_score`) and the request query (`search`, `filter`, `per-page`) and `From`/`Authorization` headers equal; arXiv request
  URL byte-identical to `arxiv.Client._format_url`, entries equal field by field (short id with version, pdf link, doi, whitespace-folded
  title, summary, year; entries without `<published>` skipped by both); Unpaywall URL byte-identical to the prepared `requests`
  request (DOIs with parentheses, `<>`, `%`, spaces) and result tuples equal, failure paths (404, 500, non-object, non-JSON) all `([], [])`.
- Behaviour kept: arXiv first then OpenAlex, OA-filter retry without the filter and its warning texts, `per_page = min(pool, 50)`
  (1..200 enforced as pyalex did), arXiv `page_size == max_results` and 2 retries with no pause (`delay_seconds=0`), pool and
  dedupe logic, record shapes.
- Differences, all deliberate: (1) OpenAlex requests also carry the documented `mailto=` query parameter next to the `From` header
  pyalex sent. (2) An arXiv API error entry (id not under `arxiv.org/abs/`) is skipped; the package turned it into a bogus short id.
  (3) arXiv Atom is parsed with the stdlib parser after refusing any non-UTF-8 body and any `<!DOCTYPE`/`<!ENTITY` (arXiv answers never
  have one), so entity expansion and external references are impossible; a refused or malformed document yields no entries, as the
  package's `recover=True` parser did. No new dependency (`defusedxml` is not a declared dependency). (4) A policy refusal is not
  retried; a failure retried as the package retried.
- OpenAlex API key: the agent never used one (pyalex `api_key` unset, no env read). The helpers accept `api_key` and send
  `Authorization: Bearer` only as a header, never in the URL, never logged (tested), but the agent does not pass one: wiring
  `OPENALEX_API_KEY` is a new secrets path and is owner-gated.
- Quota/`Retry-After`: neither pyalex (`max_retries=0`) nor the agent handled them, and `hardened_get_json` does not expose response
  headers, so a 429 is a failed lookup (warning, fallback, empty), as before. Honouring `Retry-After` needs a getter variant returning headers: owner-gated.
- Dependencies: `pyalex` and `arxiv` are NOT unused: `f2w_agent/api.py` (publication search), `commands/harvest_rsoxs.py`,
  `library/harvest.py` and `literature/download_pdfs.py` still import them. `pixi.toml`/`pyproject.toml` unchanged, lock untouched.
- Residual risks: the other pyalex/arxiv users above remain outside the hardened policy (follow-up ports, same helpers apply);
  `mailto` is sent to OpenAlex in the URL as well as the header; the arXiv terms ask for one request per 3 s and the agent keeps
  the previous no-pause behaviour (up to 5 queries x 3 tries per search).

### P2. `orchestrator_agent.py` + `debate_agent.py` -> `agent_runtime/` and `curation/`

| | |
|---|---|
| What exists | `WorkflowOrchestratorAgent` (Academy `Agent`; `decide` picks one of 10+ `ACTIONS` with an LLM classifier plus deterministic prechecks and `validate` against the workflow state) and `EvidenceDebateAgent` (Academy `Agent`; `decide` ranks OpenAlex candidates from summarized KG evidence with a deterministic fallback). `api.py` constructs both (lines 2806-2810), imports the private prechecks `_direct_download_request`, `_paper_reference_followup`, `_extracted_terms_followup` for tracing and routing (1915, 3917-3919, 4446, 4486, 5354-5376), and names both classes in the agent-status payload (4402-4406). `coordinator.py` takes an `EvidenceDebateAgent`. |
| Design decision needed first | (a) The Academy `Agent` base and `action` decorator: the framework runtime is LangGraph (`agent_runtime/supervisor.py`); porting as-is would make `agent_runtime` depend on Academy, porting onto LangGraph changes the controller the contract exercises. Proposal: move the *pure* parts (the prechecks and `_decision`/`validate` state machine to `agent_runtime/routing.py`; candidate scoring, `_selected_indices`, `_summary_from_decision` to `curation/evidence.py`) and leave two thin Academy wrappers in `f2w_agent/` that call them. (b) Where the LLM calls go: through `agent_runtime.admission` (`chat_model.ChatModel` structural type) rather than `term_extractor.clients.make_chat_client` directly, which also brings them under the cost ledger; needs the owner's view on whether class (r) turns may count against curation budgets. |
| Target home | `app/modules/agent_runtime/routing.py` (next-action selection and validation; no Academy import), `app/modules/curation/evidence.py` (debate scoring and summary), wrappers stay at the old paths. |
| Wire behaviour | `decide` payloads (`action`, `reason`, `confidence`, `summary`, `selected`, `objections`), the agent-status names and the precheck trace keys are unchanged; the legacy UI contract and `tests/test_f2w_api.py` pin them. |
| Tests | Unit tests for the pure modules with recorded classifier outputs; `tests/test_orchestrator_agent*.py` and the debate tests continue to pass unchanged through the wrappers; a static test that `agent_runtime/` and `curation/` import no `academy`. |
| Risks | The classifier prompt and fallback thresholds are behaviour; move them verbatim. `_is_ops_layout_question` is imported from `retrieval_agent` (keep as an injected predicate to avoid `agent_runtime` -> `f2w_agent` imports). |
| Size | 2-3 days after the design decision. |

**P2 status (owner decision 2026-11-09, fourteenth set: "LangGraph, via agent_runtime.admission").**

- *Orchestrator: done.* The routing logic lives in `app/modules/agent_runtime/routing.py` (prechecks, `_decision`,
  `validate_decision`, fallback state machine, classifier prompt verbatim, and a compiled LangGraph
  `plan -> classify -> finalize` graph; no Academy import). The classifier call goes through
  `agent_runtime/llm_gate.py::AdmittedChat` (budget reservation, admission slot `agent_llm`, bounded
  `cborg_http.call_with_deadline`); the ledger adapter is `curation/agent_budget.py::LedgerAgentBudget`, attached to
  the legacy service by `create_api_app` so these calls show in the `budget` block of `/v1/ops/status` and in
  the cost-control spend. `f2w_agent/orchestrator_agent.py` is now a deprecated thin facade (real module, not a
  `sys.modules` alias: the legacy hooks it injects cannot be imported from `agent_runtime`, whose import
  boundary test forbids `app.modules` imports) and no longer subclasses the Academy `Agent`.
- *Debate: done.* Scoring, index parsing, summary, heuristic fallback, prompt and guard rails live in
  `app/modules/curation/evidence.py` with a compiled LangGraph `ask -> resolve | fallback` graph (no Academy import);
  the call goes through the same `AdmittedChat` and `LedgerAgentBudget`. `f2w_agent/debate_agent.py` is a deprecated
  thin facade (same reason as the orchestrator) with `f2w_agent/admitted_chat.py` holding the shared legacy wiring.
  Parity: `tests/curation/test_evidence_golden.py` replays 22 recorded cases.
- Parity: `tests/runtime/test_routing_golden.py` replays 48 recorded fake-model cases (decision, exact prompt, call
  kwargs, precheck notes) captured from the legacy module before the port.

### P3. `bl1101_ingest.py` -> `graph/imports/bl1101/`

| | |
|---|---|
| What exists | 2,727 lines, 76 top-level functions/classes: GitHub repo sync (`sync_repo`, subprocess `git`), ALS page and blueprint HTML parsing (lxml), Python/TypeScript symbol extraction (`ast`), beam-path ordering, term assembly (`make_term`, `merge_terms`, `attach_repo_terms`), motor-stage linking from `storage/schema/bl1101_motor_stage_map.yaml`, versioned snapshot writing (`storage/kg/matkg_bl1101_vN.json`) and a CLI `main`. Consumers: `scripts/ingest_bl1101.py` (facade), `tiled_sim.py` (14 names: ids, paths, `make_term`, `_rel`, snapshot helpers), `hybrid_rag/sources.py` (`REPO_SPECS`). |
| Target home | `app/modules/graph/imports/bl1101/` as a package: `specs.py` (`RepoSpec`, `REPO_SPECS`, ids and namespaces: the only part `hybrid_rag` and `tiled_sim` need), `fetch.py` (hardened fetch via `legacy_download.hardened_get_json`/a hardened text fetch; `git` sync behind `deployment`-style subprocess guards), `parse_als.py`, `parse_blueprint.py`, `symbols.py`, `terms.py`, `snapshot.py` (write through `graph.snapshot.create_snapshot` so bl1101 snapshots get manifests like the literature KG), `cli.py`. Old path becomes the alias shim; `scripts/ingest_bl1101.py` keeps working. |
| Wire behaviour | None on class (r) routes; the produced `matkg_bl1101_vN.json` must be byte-identical for the same inputs (sorted by `sort_graph`). The `--from-graph` JSON-first path is the regression oracle: replay v7 -> v8 from the checked-in snapshot and compare. |
| Tests | Golden replay from `storage/kg/matkg_bl1101_v7.json` with the motor map; parser unit tests on checked-in HTML fixtures (no network); a static test that nothing under `graph/imports/bl1101` imports `urllib.request`; `tiled_sim` and `hybrid_rag` import tests. |
| Risks | `fetch_url` uses `urllib.request.urlopen` with any scheme and no size cap (move onto the hardened fetcher is a behaviour change for redirects); `git` subprocess use must stay out of the request path (the snapshot module forbids subprocess). The ALS page and blueprint HTML are live documents: fixtures must be frozen from the current cache (`.cache/bl1101`). |
| Size | 3-5 days (multi-day, as the audit said); slice a (`specs.py`) is done, 3-4.5 days remain. |

## Order proposed

P1 (download_agent hops) is independent and smallest. P3's `specs.py` slice is done (see 4); the
`hybrid_rag` -> flat-module import remains until a later slice. P2 needs the owner's design answer (Academy wrappers vs. LangGraph; LLM calls
through admission) before any code.
