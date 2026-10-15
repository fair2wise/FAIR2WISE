# T17 paper scope closure report (for H6)

- **Gate:** H6 (this is the scope-closure report T17 submits at H6; a final update follows at H7).
- **Auditor:** T17, independent migration auditor. **Base:** `forge_merge` at `9c282e5` (after T24C). Branch `forge_merge-T17-audit`, not merged.
- **Status:** **H6 cannot close yet.** Scope is now fully inventoried and each item has a disposition, but 17 inventory items (14 gaps, 3 proposed exceptions) and 19 routes (18 gaps, 1 proposed exception) are `gap_needs_owner` or `proposed_exception`. Each one needs an owner decision (assign a task, or approve an exception). Many more are `pending` on tasks that have not run (T06B, T07B, T11B, T12, T13, T15, T19C, T24D).
- **Records:** `docs/migration/paper_scope_inventory.json` (machine-readable, one entry per capability and per route), `docs/migration/paper_route_ledger.md` (routes and callers), and this report. Test: `tests/migration/test_paper_scope_inventory.py`.

## 1. Method

1. **Sources.** I enumerated all 153 entries of `companion/port_manifest.json`, all 107 files of `paper_source_coverage.json`, and all 84 routes of `paper_route_inventory.json`. I also used the F2W baseline crosswalk (B01-B36), `f2w_http_routes.json`, the T19A operations inventory and the T14 API README.
2. **Discovery outside the starting set.** I searched the read-only donor tree (grep and AST only, nothing executed) in `scripts/`, `src/tiled_agentic/`, `src/forge/api/`, `frontend/src/` and `als_knowledge_agent/` for paper utilities and for callers of paper routes. This found 13 more items (X-01..X-13) and one more asset (A-04).
3. **F2W mapping.** For each capability I located the F2W module from port-id docstrings, module docstrings and the merge commit messages. Each merge's stated deferrals were checked against the code.
4. **Test evidence.** Every cited test node id was collected and **passed** in a full isolated run of `9c282e5` (3007 passed, 132 skipped, 3 known failures, 0 problems). The server-backed graph and import cases skip by default. I ran them separately against a disposable Tiled 0.2.18 on an ephemeral loopback port (`F2W_TILED_PYTHON`): `tests/graph` + `tests/graph_import` gave 104 passed. The generator refuses to cite a test that is missing, skipped or failing. I made no code changes.

"Covered" means implemented and tested with fixtures and fakes. It does not mean demonstrated on real papers, real models or real Tiled publication. The column `demonstrated` in the JSON says which.

## 2. Counts

| | Total | covered | retained in place | partial | pending | gap (owner) | proposed exception | FORGE-owned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Capabilities | 201 | 94 | 17 | 26 | 41 | 14 | 3 | 6 |
| Donor routes | 84 | 17 | - | 12 | 27 | 18 | 1 | 9 |

How the 201 capabilities break down:
- 153 catalog ports: 139 port entries plus the 14 operators O-01..O-14.
- 4 assets: A-01..A-03 from the asset/support review, plus A-04 found by this audit.
- 13 other donor items found by this audit (X-01..X-13).
- 9 enumerated source files with no port id (S-01..S-09: package `__init__` files, FORGE-owned mixed modules, the replaced `tiled_client.py`).
- 22 existing F2W paper features (F-01..F-22). 17 are tested in place; 5 have no tests (F-18..F-22, pending T19C).

**T17's own port ids:**
- Covered: L-20, U-11, O-07.
- Partial: O-01, O-02, O-04, O-05, O-06.
- Pending: O-03 (T11B), O-08 and O-09 (T13), O-11 (T07B).
- Gap: O-10, O-12, O-13, O-14.

## 3. Demonstrated now versus later

- **Demonstrated now** (fixtures, fakes or a disposable Tiled):
  - Literature discovery, resolution, intake, store and safe fetch: L-01..L-11, L-19, L-20.
  - Staging, receipts and the journal: J-01..J-04, J-08, J-09.
  - Extraction strategies and evidence: E-01..E-06, E-08, E-09, all pre-H3 and off by default.
  - Canonicalization services: the proposal side of C-01..C-16.
  - Curation: U-01..U-11, including the U-11 strict review schema without the Codex transport.
  - Figure kernels and pipeline: V-01, V-03, V-04, V-06..V-14.
  - Graph read and import: K-01..K-09, K-12..K-14.
  - The `/v1` jobs, review, publication and monitor routes (O-07, I-03, I-05..I-07).
  - All 17 retained F2W features.
- **Needs the T12 integrated pilot / H4:**
  - Real-paper fixtures for figures (V-03) and tables.
  - VLM figure classification (V-02, A-04, X-09).
  - Real local and remote model resolution (U-04 and U-07; the judge model is owner item H3 B7).
  - Ingest, parse, local-review, figure job kinds and their routes: RT-16..18, 33, 35, 39..46, 50, 51, 58, 60, 61.
  - Folder ingest operator (O-06, J-05) and bibliography backfill (L-07).
- **Needs other tasks before H6:**
  - T06B: E-07, RT-57.
  - T07B: C-11..C-13, O-04, O-05, O-11, I-04, A-02, A-03, the vocabulary cleanup routes and all 13 term-campaign routes.
  - T11B: Q-01..Q-24, O-03, X-04. Per owner H3 "B", these are validation only and never block the application.
  - T13: J-06, J-10, J-12, K-15, U-09 execution, O-08, O-09, X-01, RT-24.
  - T15: FORGE adapters and callers X-11..X-13 and the frontend.
  - T18C: viewer caps and publication figures.
  - T19C: K-11, RT-03, and the untested F2W scripts F-18..F-22.
  - T24D: X-06, the public-source path guard.

## 4. Findings

1. **Unexplained deferrals by merged tasks.** Plan T17 says no paper capability may be silently deferred, yet these were:
   - T05's merge message deferred "Docling/parser pool, L-14..L-18, J-06/J-07, O-01..O-14".
   - T09 deferred "a real OCR engine, VLM classification".
   - T10 registered no topic-sweep or PDF-parse kinds (J-11).
   - T14 deferred the folder-browse routes pending a path-root policy (I-02).

   For several of these, no open task in `task_graph.json` implements them: T15 lists L-14..L-18 only for consumer parity, and T13 lists J-07/J-11 only for rehearsal. They are recorded as `gap_needs_owner` with a proposed owner (section 5).
2. **The parser is still PyMuPDF.** PyMuPDF (AGPL) is marked DEPRECATED pending Docling (H1 E2). The Docling parser and the parser pool/worker (L-12, L-13) have no owner. O-03 (parser benchmark) depends on them.
3. **Gaps in the paper graph.** There is no projection of citation links (L-17), the paper subgraph (K-10) or paper context (L-16). Paper context also feeds figure classification (V-02), so V-02 cannot reach donor parity without it.
4. **Licensing metadata (L-18) is missing.** It should exist before any publication at H5.
5. **The `/v1` HTTP surface has no route for** cost-control settings (U-08, RT-38), bulk review (RT-53), vocabulary quality (RT-04) or human figure-scale records (RT-54). The services exist for the first three.
6. **No experimental code has crossed the boundary.** The evidence:
   - `tests/vision/test_kernels.py::test_kernel_import_closure_has_no_experimental_modules`.
   - `tests/evidence/test_evidence.py::test_evidence_package_does_not_import_donor_or_simulation_code`.
   - `tests/extraction/test_forge_strategies.py::test_extraction_package_constructs_no_provider_and_imports_no_donor_code`.
   - `tests/runtime/test_runtime_registry.py::test_experimental_and_unknown_domains_rejected`.
   - The 9 FORGE routes and 6 FORGE-owned source items have no F2W counterpart.
7. **Every frontend and gateway caller path is one of the 84 inventoried routes.** No uninventoried Knowledge Agent paper route was found (ledger section 3).
8. **T14 Q5 is resolved.** The remaining I-01 route dispositions live in `paper_route_ledger.md` section 2 and in the `routes` array of the JSON.

## 5. Owner decisions needed

For each item, the owner either assigns the proposed task or approves the exception. Nothing below is treated as approved.

### 5.1 Proposed exceptions (auditor recommends not porting)

A-01 is `gap_needs_owner` and RT-47 is `partial` in the JSON, because each needs an owner decision. They appear here because what is proposed for them is a retirement or a restriction.

| ID | Item | Proposal | Why |
| --- | --- | --- | --- |
| R-08 | Langfuse tracer | Do not port; F2W traces (`/traces/{turn_id}`) and the K-13 audit recorder are the telemetry capability | Catalog marks it optional, export off unless configured; no open task owns it |
| X-05 | `scripts/seed_synthetic_kg.py` | Do not port | FORGE canvas-viewer fixture; F2W viewer caps use their own fixtures (T18C) |
| X-07 | `scripts/bench_kg.py` | Do not port | Donor says "DEV-ONLY ... NOT WORTH PORTING" (SQLite lock timing, meaningless behind Tiled) |
| RT-20 | `POST /api/graph/add_annotation` | Reclassify `remain_in_FORGE` (catalog says `split_paper_portion`) | It annotates dataset nodes; its only paper part, entity-mention extraction, already exists in F2W (F-05) |
| A-01 | `openalex_supplement.json` | Retire the static file; regenerate citation links via L-17 | Derived OpenAlex data, 314 KB; only `citation_links.py` reads it |
| RT-47 | `GET /api/graph/table_csv/{id}` | Confirm internal-agent-only (D19) | Table CSVs are parser assets of private sources |

### 5.2 Gaps that need a task (auditor's proposed owner)

| IDs | Capability | Proposed owner | Before |
| --- | --- | --- | --- |
| L-12 (Docling, pool), L-13 | Docling parser, isolated parser workers (H1 E2) | New T05 remainder task, before T12 | H4 |
| L-14, L-15, L-16, L-17 (+A-01) | Reference parsing, section projection, paper context, citation links | Same T05 remainder task | H4 (L-16 feeds V-02) |
| L-18 | Licensing / redistribution metadata | Same T05 remainder task | H5 |
| J-07 | Corpus index | T13, as implementation rather than rehearsal | H6 |
| J-11, RT-27..RT-32 | Enrichment, topic sweep, PDF-parse sweep job kinds | T12 (job kinds) | H6 |
| V-05 | A real OCR engine behind the existing fallback | T09 follow-up | H4 (catalog: required before H4 and H6) |
| K-10, RT-59 | Paper subgraph projection and route | T19C | H6 |
| RT-07, RT-08 | Graph growth / recent views | T19C | H6 |
| O-01, O-12, O-13, O-14 | Cache audit, corpus metadata, corpus and scattering profiling | T19C | H6 |
| O-02, O-10 | Bibliography backfill run, bulk refetch from a DOI list | T12 job kinds or CLI | H6 |
| I-02, RT-62..RT-68 | Folder roots/browse/index/search | Owner path-root policy first, then T12 | H6 |
| U-08 / RT-38, RT-53, RT-04 | Cost-control route, bulk review, vocabulary quality route | T14 follow-up (or T19E) | H6 |
| RT-54 | Human figure-scale record (paper part) | T09 follow-up, with curation | H6 |

### 5.3 Other open owner items this ledger depends on

- H3 B4 (pilot Work families and labeler) and H3 B7 (judge model) gate T11B and the real-model demonstrations at T12.
- The O03 policy (FORGE_JOB_SETTINGS_AUDIT.md) gates O-03.
- T14 Q3/Q4 (origin gate and body cap on legacy routes) do not affect paper scope.

## 6. What closes H6 for T17

1. Every row in section 5 is either approved as an exception (recorded in `reviews/H6/decision.json`) or assigned and then implemented.
2. Every `pending`/`partial` entry reaches `covered`, with its new tests cited in the JSON. The T17 test then rejects any covered row without tests.
3. The T12 pilot demonstrates the "needs T12" items on real papers.
4. This report and the JSON are re-run at the H6 head. The generator re-verifies every cited test against a full run.
