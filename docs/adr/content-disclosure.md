# ADR: Agent-private source access and user-facing disclosure

- **Status:** Proposed (T21A, gate H1). Not approved. No code, route or data changed.
- **Decisions this ADR must satisfy (fixed by the owner):** D19 (source papers are agent-private; relevant bounded exact quotations are allowed with citation; no full papers, raw sections, original crops or reconstructable source-text export; applies to API, SSE, tool traces, graph properties/exports, reports and FORGE forwarding). D30/D34 (profile-specific access; see `docs/migration/authentication/access_contract.md`). D31 (frozen KG JSON is committed to Git).
- **Evidence:** `docs/migration/disclosure/route_field_map.json` (every route and source-carrying field, with file:line against the frozen baseline `222b1e7`). Policy text: `docs/migration/companion/CONTENT_ACCESS_POLICY.md`, plan sections 4.8 and 4.14.
- **Tags:** **[Observed]** read in baseline source or measured. **[Inferred]** reasoned, not run. **[Proposed]** design for H1 approval.

## 1. What the baseline does today

**[Observed]** Summary of `route_field_map.json`:

| Route class (highest class among source-derived fields) | Routes |
| --- | --- |
| public-safe | 18 (health, settings, sessions, telemetry, annotations, model-proxy stubs, Paper Finder) |
| quote-bounded | 11 (graph read/search/node/neighborhood/edit/upload, publication search, both model-proxy chat routes in each copy) |
| internal-only | 4 (`/chat`, `/chat/action` and their `/stream` variants, because `rounds` forwards raw internal state) |
| source-serving | 1 (`GET /traces/{turn_id}`) |

Across 60 field records (32 shared DTO fields, 28 route-specific): 29 public-safe, 16 quote-bounded, 13 internal-only, 2 source-serving.

Findings that drive this ADR:

1. **No route serves PDF bytes, a parsed section or a figure crop.** The UI links out to doi.org, arXiv or Semantic Scholar only (`ui/src/app/components/publicationLinks.ts:60-125`). Paper Finder never fetches or serves a PDF (`scripts/paper_finder.py:636`).
2. **`GET /traces/{turn_id}` serves source text.** It returns the unclipped trace (`app/modules/f2w_agent/api.py:7001-7007`). The trace holds `retrieval.merged_context`, which is the exact prompt context with hybrid paper chunks (`retrieval_agent.py:1279-1291,1440`). It also holds every rendered prompt (`trace.py:460-494`). A paper-evidence prompt carries up to 8 pages x 7000 chars in full mode, or every selected page x 7000 chars in targeted mode (`paper_evidence_agent.py:16-17,358-377`), and its repair prompt repeats them (`:402-410`). There is no auth. Turn ids may be caller-chosen (`trace.py:346-350`). `docker-compose.yml:43,51` turns tracing on and publishes port 8090 on all interfaces.
3. **Tracked graph JSON is a cumulative source export.** `storage/kg/*.json` holds 126,977 `context_snippets` (28.3 M chars). In a sample of 300, all 300 occur verbatim on the cited PDF page. The union of snippets reproduces a median 31% (max 48%) of each paper's normalized text, for 90 papers. `storage/terminology/*.json` holds 141,458 more. These files are in a public repository. No F2W route returns `context_snippets` (`api.py:598` excludes them), but under D19/D31 this is a graph export.
4. **Full PDFs are in public Git history** (H0 E1). This is outside the HTTP API, and the owner decision is pending.
5. **`ChatResponse.rounds` forwards internal state** (`api.py:4810-5702`). This includes full OpenAlex candidate dicts with untruncated abstracts, extraction output paths and `str(exc)` errors. SSE `complete`/`error` events and `HTTPException.detail` do the same.
6. **`GraphNode.properties` forwards every stored property** when properties are a dict, as on Tiled and Splash nodes (`api.py:657-676`). The Splash importer stores all non-core thing fields as properties, including `context_snippets` (`splash_links/scripts/import_kg.py:42-58`). Today's live Tiled graph holds identity entities only. A literature import that copies properties wholesale would serve snippets.
7. **The model-compatible proxy** (`kg_rag_api.py:245-280`) puts a 1000-char window of full PDF text, positioned by the caller's query terms (`:458-471,1242-1247`), into prompts. It returns unconstrained model output and binds `0.0.0.0:11435` without auth (`:321-330`).
8. **Nothing verifies an exact quote.** Answers are checked for citation format and page eligibility only (`paper_evidence_agent.py:83-120`). Graph descriptions and code snippets carry a source and page but are never compared with the source.

## 2. Decision: two capabilities, never inferred from a request

**[Proposed]**

### 2.1 Capabilities

| Capability | Grants | Holders |
| --- | --- | --- |
| `source.read` | Read a source PDF, parsed text, sections, figure/page crops, source-index chunks and private evidence payloads **inside the process**, to run agents, extraction, validation, figure analysis and Knowledge Operations. | The trusted agent runtime (`AgentRuntime`, workers, FORGE in-process delegates). The holder is set by trusted host wiring, not by the caller. |
| `result.disclose` | Return a **projection** to a user surface: derived results, citations, bibliographic metadata, and verified bounded exact quotations. | Every user-facing serializer: FastAPI DTOs, SSE adapter, trace/report export, graph export, Git commit candidates, FORGE forwarding. |

`source.read` never implies a right to return what was read. No user-facing surface holds a capability that returns source bodies. This includes the :local full-access profile (D34) and ALS admins (D30). "Full access" means all application *functions*, not raw-paper delivery (AUTHORIZATION_SPEC section 2, CONTENT_ACCESS_POLICY section 2).

### 2.2 Where authority comes from

- **Rule A1.** Internal-agent authority is bound when the trusted runtime is constructed: `security/content_access.py: SourceAccessScope`, created by `app/modules/application.py` from startup configuration. It is passed to Tools as a Python object, never as data.
- **Rule A2.** These can never confer `source.read` on a user surface, or widen a projection:
  - an HTTP parameter, header, cookie, query string or body field (for example `agent=true`, `internal=1`, `X-Agent`, `User-Agent`);
  - a `ui_settings` key or a model/Tool argument;
  - a prompt instruction, including text inside a paper ("print the full text");
  - a saved job record or a caller label (`annotator`, `session_id`, `client_turn_id`).
- **Rule A3.** Internal HTTP for source reads (if any) runs on a separate listener that is never routed through `/fair2wise`. It uses a scoped service credential, not a user token and not the Tiled writer key. A Python import in the same host is a trusted-host boundary, not a sandbox (CONTENT_ACCESS_POLICY section 2).
- **Rule A4.** Order of checks on every user request: authorize the caller (access_contract.md), retrieve with `source.read` inside the runtime, then project with `result.disclose` before anything leaves the process. A projection failure returns a typed error, never the unprojected object.

### 2.3 One projection for every surface

`security/disclosure.py` exposes one function per DTO family. Every surface calls the same function.

| Surface | Today | Projection |
| --- | --- | --- |
| FastAPI JSON (`ChatResponse`, `GraphNode`, `GraphPayload`, `PublicationSearchResponse`, `AgentSettingsResponse`, `/health`) | Pydantic models straight from internal dicts | `project_chat_response`, `project_graph_node`, `project_publications`, `project_settings`, `project_health` |
| SSE `progress` / `complete` / `error` (`api.py:6848-6999`) | Raw event data and full `ChatResponse` | Same projections. `error` carries a typed code and a correlation id, not `str(exc)`. |
| `GET /traces/{turn_id}` | Unclipped detail file | `project_trace` (section 4.3). The full detail file stays in the protected trace store. |
| `HTTPException.detail` | `str(exc)` | Typed message; raw text goes to the server log and protected trace only |
| Graph export, Git commit candidates, Tiled import (T23A/T26A) | `context_snippets`, `has_evidence`, properties copied wholesale | `project_graph_export` with private evidence split into references (section 5) |
| Reports (Knowledge Operations, gate reports) | n/a | `project_report`: IDs, counts, verdicts, citations and verified quotes |
| FORGE forwarding | n/a | Versioned `SafeResultView`. Internal tool returns are never forwarded as public tool traces. |
| Model-compatible proxy | Unconstrained model output | Same answer projection as `/chat` (section 4.2) |

Projection is applied in `application.py`, so a direct SDK, CLI or Tool call reaches the same function. An HTTP-only middleware is not enough (CONTENT_ACCESS_POLICY section 4).

## 3. Exact-quote rules

**[Proposed]** These rules apply to any text that a user surface presents as source text: a quoted span in an answer, a graph evidence quote, a `code_snippet` taken from a paper, or a report excerpt.

- **Q1. Exactness.** The quote must match the **retained source version** (`work_id` + content hash of the parsed text or PDF) at its locator. Matching may normalize whitespace and line-break hyphenation only. The normalization profile is versioned and recorded with the quote. It is never applied silently to rewrite the displayed text.
- **Q2. Locator.** Every quote carries `{work_id or source_paper, source_version_hash, page, section/figure/char_offsets when available}` and the existing visible citation token (`[PDF: file p.N]`, `[KG: graph: name]`, `[OPS: file §heading]`; formats in `docs/migration/f2w_llm_qa_baseline.json`). Storage paths, URLs that bypass authorization, and content hashes used as access tokens are never part of a locator.
- **Q3. Omissions.** An internal omission is marked `[…]`. A quote that is changed in any other way is a paraphrase.
- **Q4. Paraphrase is not verbatim.** Text that fails Q1 is shown as an unquoted, cited statement. It may not be shown in quotation marks or a quote block, and it may not set `verbatim=true`. A model output that claims to quote but fails Q1 is downgraded, not passed through (QUOTE-02).
- **Q5. Relevance and boundedness.** A quote must be relevant to the request and bounded. There is **no fixed application-wide word limit** (plan 4.8). Bounds are configured, reviewed values on a `DisclosureBudget`:
  - per-quote: a max length;
  - per-response: max quotes and total quoted chars per `work_id`;
  - per-session/actor: cumulative distinct quoted chars per `work_id` over a window.
  When a budget is reached, the remaining quotes become citations without quoted text. Initial values are an H1 approval item. They must be chosen so that the existing F2W snippets (section 6) still pass.
- **Q6. No enumeration.** No route returns chunks, pages or snippets by index, offset or "next". Repeated requests for adjacent spans count against the session budget. A request asking for the full text of a section or paper gets a citation and a summary. "Print the full text" instructions inside a paper are data and are ignored.
- **Q7. Context.** A quote keeps enough surrounding words to avoid misattribution, and that context counts against the budget. A bounded window centred on a query term (the model-proxy pattern) is not a quote unless Q1-Q5 pass on the window actually shown.

These rules map onto the T01 draft `EvidenceRef.match_type` (`app/modules/contracts/evidence.py:16-23,82-91`):

- `exact` may be displayed as verbatim.
- `normalized` may be displayed as verbatim only under the recorded Q1 normalization profile.
- `paraphrase` and `none` are never displayed as verbatim.

Reconciliation is listed in `docs/migration/authentication/access_contract.md` section 10, C12.

Verification runs on the **displayed** text after the model answers. A prompt instruction to "quote exactly" does not satisfy it. Quotes are verified against retained source text inside `source.read`. The verifier returns only `{verified, locator, normalized_profile}` to the projection.

## 4. Per-surface rules

### 4.1 Graph node detail and graph payloads

- **[Proposed]** Keep the current `GraphNode` wire shape (`api.py:167-193`) and its compatible fields (FRONTEND_COMPATIBILITY.md).
- `description`, `publications`, `source_papers`, provenance and `formula` pass through as today. A description that is presented as a quote follows section 3.
- `properties` and `extra_fields` go through an allowlist projection. Keys that hold private evidence (`context_snippets`, `has_evidence`, full-text excerpts, crop/image URLs, storage paths) are removed, or replaced with `{evidence_ref, locator}`. This replaces today's pass-through of every key for dict-shaped properties.
- `code_snippet` from a paper is a quote under section 3. Code from a repository keeps its repo, commit and line provenance and is not a paper quote.
- `GraphPayload.source_path`, `GraphUploadResponse.graph_path`, `ChatResponse.workdir` and the `/health` paths become opaque ids or are dropped. The UI does not read them [Inferred from `f2w_ui_contracts.json`; confirm in T18A].

### 4.2 Chat answers (F2W and the model-compatible proxy)

- Answers keep their current citation tokens and statuses.
- Any quoted span passes Q1-Q5 before return. Unverifiable "quotes" are rewritten to cited statements (Q4), not dropped. The answer's facts stay.
- `rounds` becomes a projected summary: round number, status, counts, selected node ids/labels, candidate title/DOI/year/abstract (the abstract cut as in `_candidate_public`, `api.py:6524`) and typed error codes. The raw verdicts stay in the protected trace.
- The model-compatible proxy uses the same answer projection. Its PDF-window context is internal input under `source.read`.

### 4.3 Traces

- The full detail file stays in the protected trace store (`F2W_TRACE_DIR`). It inherits private-mount permissions (CONTENT_ACCESS_POLICY section 3).
- `GET /traces/{turn_id}` returns `project_trace`: ids, timings, labels, model names, prompt **hashes** and lengths (not prompt text), selected node ids, counts, the projected answer, citations, status and typed errors.
- Evaluators who need raw prompts use a separate, explicitly approved review channel. That channel is a separate access decision (CONTENT_ACCESS_POLICY section 5) and is not part of this ADR.
- On ALS, a trace is readable only by its recorded actor or a verified admin (access_contract.md). On :local, any local user may read the projected trace.

### 4.4 Errors

User-facing errors are typed codes with a correlation id. `str(exc)` goes to server logs and the protected trace. This covers `HTTPException.detail` (`api.py:6654-6815`), SSE `stream_error` (`:6882-6890`) and `Retrieval failed safely: {exc}`-style answers.

## 5. Graph exports, Git and Tiled publication

- **[Proposed]** `project_graph_export` splits each node into a public part and private evidence.
  - Public part: identifiers, derived facts, citations, bounded verified quotes.
  - Private evidence: `context_snippets`, edge `has_evidence`, source-text excerpts. Each is moved to a referenced controlled store as `{evidence_ref, work_id, source_version_hash, page, offsets}`.
  - No scientific fact is dropped (plan 4.14).
- A commit-ready snapshot must pass a **cumulative reconstruction check**. For each `work_id`, the union of all quoted characters in the snapshot (nodes + edges + properties) must stay under the reviewed coverage budget. The baseline fails this: median 31%, max 48% per paper.
- The Tiled importer (T23A/T08B) imports the projected export, never raw MatKG things. This keeps Tiled properties, which are create-only in rc2, free of private evidence from the first write.
- **Owner items, not decided here:**
  - **O-D1.** Whether the 47 tracked `storage/kg` and 58 `storage/terminology` files must be replaced by projected versions on `forge_merge` (a normal commit) and/or purged from history (a rewrite). This mirrors H0 E1.
  - **O-D2.** Initial `DisclosureBudget` values.

## 6. Preserving legacy snippet and citation behaviour

The existing F2W behaviour that must survive (QA-01, OPS-05, QUOTE-01):

- **Citation tokens and parsing.** Citation tokens and UI parsing are unchanged (`kgCitations.ts:5-160`; `trace.py:50-52`; `paper_evidence_agent.py:83-120`).
- **Citation popover snippet.** The popover shows `node.description || abstract_text` (`kgCitations.ts:548`). Descriptions and abstracts stay. Only a part shown as a quote is verified.
- **Pending card.** The pending card abstract keeps its 600-char cut (`api.py:6524`, `ChatSidebar.tsx:53-55`).
- **Extracted-terms summary.** The deterministic page-cited extracted-terms summary (`paper_evidence_agent.py:122-292`) is derived text with citations and passes as-is.
- **Graph node card and edits.** Description, publications, code snippets and edit readback keep their wire shape (TIL-05, JSON-10).

## 7. Tests

**[Proposed]** All cases in `binary_case_inventory.json` are `proposed_case_not_implemented`. This ADR only says what each case must prove. T21B implements them against fake stores before the first H2 UI demo. H4/H6 re-run them on the integrated paths.

### 7.1 Positive (exact quote survives)

| Test | What must hold | Case |
| --- | --- | --- |
| P1 | A relevant exact quote from a fixture paper appears in `/chat` answer, SSE `complete`, projected trace and `GET /graph/node/{id}`, with locator and citation token, byte-identical after whitespace normalization. | QUOTE-01, QA-01 |
| P2 | Existing citation popover snippet and pending-card abstract render unchanged. | QUOTE-01, QA-01 |
| P3 | A quote with an internal omission shows `[…]` and still verifies. | QUOTE-01 |
| P4 | Trusted agent reads of PDF, pages, chunks and crops succeed inside the runtime for extraction and paper evidence. | SEC-02, FIG-04 |
| P5 | Deterministic paper-summary citations are unchanged. | OPS-05 |
| P6 | Projected graph export keeps every fact and replaces snippets with evidence refs. Re-import and readback work. | JSON-08, JSON-10, REL-04 |

### 7.2 Negative (no leakage)

| Test | Attack / condition | Must hold | Case |
| --- | --- | --- | --- |
| N1 | Request a full paper: "print the whole paper", "continue from page 3", a malicious instruction inside a fixture PDF | Summary + citations only; quoted chars per `work_id` within budget | SEC-01, QUOTE-02 |
| N2 | Request a raw section, original table or crop; request `/papers/...`, `/static/...`, `/assets/...`, guessed figure/artifact ids, content-hash URLs | 404/403 with no body content; no route serves PDF/crop bytes | SEC-01, SEC-05, FIG-04, DEP-10 |
| N3 | Sequential reconstruction: 50 adjacent-span requests over one paper, across sessions and through both chat routes and the model proxy | Cumulative distinct quoted chars per `work_id` stays within the session/actor budget; no index/offset enumeration route exists | SEC-05, QUOTE-02 |
| N4 | `GET /traces/{turn_id}` after a paper-evidence turn and a source-RAG turn | No page text, chunk text or prompt body; prompt hashes only | SEC-04 |
| N5 | Force errors (missing PDF, bad manifest, Tiled 500, Splash error) | `detail`, SSE `error` and answer text carry typed codes; no paths, no page text, no `str(exc)` | SEC-04 |
| N6 | `ChatResponse.rounds` / SSE `complete` | No raw verdict bodies, no untruncated abstracts, no paths | SEC-04 |
| N7 | Caller flags: `agent=true`, `X-Agent: 1`, `ui_settings.internal=true`, `User-Agent: f2w-agent`, a Tool argument `trusted=true` | No widening of the projection; no `source.read` | SEC-03, LOCAL-07 |
| N8 | Fabricated quote; right text with wrong page; right page with an old source version | Downgraded to an unquoted cited statement or rejected; never shown as verbatim | QUOTE-02, EXT-04 |
| N9 | Graph node with `properties.context_snippets` (Tiled/Splash fixture) | Snippets are not returned by node detail, neighborhood, search or SSE `graph_update` | SEC-05, TIL-05 |
| N10 | Graph export / Git commit candidate | No `context_snippets` text, no `has_evidence` text, no PDFs, no secrets; reconstruction check passes | REL-04, DEP-09, SEC-05 |
| N11 | FORGE delegate call | Receives `SafeResultView` only; internal tool returns are absent | AUTH-10, SEC-04 |
| N12 | :local full access and ALS admin | Same projection as an ordinary user; no raw paper/crop delivery | LOCAL-09, AUTH-10 |

Semantic tests (N1, N3, N8) need a fake deterministic model that echoes context on request, plus a real-model run at H4/H6. Hiding routes does not stop a model from reproducing context (CONTENT_ACCESS_POLICY section 4).

## 8. Consequences

- `/traces/{turn_id}` loses raw prompts. Evaluation tooling that reads raw traces (`scripts/check_trace.py` and `scripts/run_eval_batch.py` on HEAD) moves to the protected store. This needs a T18A/T03 compatibility note.
- `ChatResponse.rounds` changes content but keeps its type (`List[Dict]`). The UI's use of it must be confirmed in T18A.
- Graph publication needs an evidence-reference store before H4. Tracked KGs fail the reconstruction check until O-D1 is decided.
- The module paths (`security/content_access.py`, `security/disclosure.py`) are the ones proposed in CONTENT_ACCESS_POLICY section 4 and plan section 13. They do not exist yet.
