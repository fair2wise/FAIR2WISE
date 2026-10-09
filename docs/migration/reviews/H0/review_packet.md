# Human review packet: H0 (source and baseline)

Gate: H0
Approver: project owner
Status: **awaiting owner decision**

H0 asks: *are the exact confirmed rsoxs source, LLM/QA baseline, tests and
scope verified?* H0 blocks T01 and all branch-specific implementation.

## 1. Source and branch locks

| Item | Value | Evidence |
|---|---|---|
| F2W repository / ref | `fair2wise/FAIR2WISE` `refs/heads/rsoxs` | `source_lock_checkout.json` |
| Remote rsoxs tip (fetched 2026-10-09) | `222b1e7b130567dbf4a3242697ac2b465794a38d` | equals the archive-recorded commit |
| Archive vs Git source | equivalent: `verify_handoff.py` 0 errors on `git archive 222b1e7` (144 files, 36 anchors, 25 F2W routes, deployment excerpts) | static only |
| Frozen baseline | `222b1e7` (owner decision 2026-10-09) | `source_lock_checkout.json` |
| `forge_merge` | created from `222b1e7`, single worktree, local only, not pushed | |
| Pre-migration change | `2430320`, recorded and not part of the baseline | `pre_migration_changes.md` |
| FORGE donor | local tree matches port manifest (153 files, 817 symbols, 128 test candidates); commit `f1cd2de` **unverified** (no `.git`) | `source_lock_checkout.json` |
| Companion bundle v8 | committed verbatim; 64 of 66 checksums OK; `MIGRATION_PLAN.md`/`.docx` not supplied | `docs/migration/README.md` |
| Frozen UI | `ui/` tree `75a19b9823bc924a6391311191d3e41a5b4917ff`; `vite build` manifest sha256 `1e7ee4ef887f7ac88b9f51617f2c70f744ac2b11c29fe6d0af7735621e3c3178`; unchanged at HEAD | |

`forge_merge` commits, oldest first:

| Commit | Content |
|---|---|
| `2430320` | pre-migration work in progress (not migration work) |
| `8cefac3` | plan v8.0 and short-form specs |
| `2825a39` | companion bundle v8, verbatim |
| `379010e` | T00 baseline lock and test results |
| `2569a4f` | T00 audit, inventories, LLM/QA baseline, this packet |
| `f0c6ce3` | E1: untrack `papers/` |
| `28820b0` | E2: mark PyMuPDF sites for Docling replacement |
| `bfbec85` | E3: exclude Tiled catalog DB from the image |

No commit touches `main` or `rsoxs`. Nothing was pushed. No source PDF, key,
catalog or runtime database was added by any migration commit (the PDFs and
catalogs below already existed at `222b1e7`). `.run/agent_backend.pid` and a
stray empty `package-lock.json` were deliberately left uncommitted.

## 2. Tasks completed

T00 only. Deliverables:

| T00 deliverable | File |
|---|---|
| Pinned F2W source, FORGE source lock, archive-vs-Git check | `source_lock_checkout.json` |
| BRANCH_WORKFLOW evidence, `forge_merge` prepared | `source_lock_checkout.json`, section 1 above |
| Baseline tests executed under isolation | `baseline_tests.md`, `environment/python_freeze.txt` |
| B01-B36 validated by executed tests; parity inventory | `../../f2w_baseline_inventory.json` |
| License/dependency audit | `license_dependency_audit.md`, `environment/python_licenses.json` |
| LLM/QA baseline | `../../f2w_llm_qa_baseline.json` |

Fixed decisions checked at this gate: D01 (exact repo/branch, no substitution),
D02 (`app/modules` untouched), D10 (forge_merge from the reviewed baseline; no
main/rsoxs commits, reset or force-push). D03-D09 and D11-D34 constrain later
tasks; nothing at H0 conflicts with them, except the escalations in section 5.

## 3. Test results

Isolation: `git archive` export (no `.env`), `env -i`, throwaway `HOME`, fresh
venv. Full commands are in `baseline_tests.md`.

| Suite | `222b1e7` |
|---|---|
| pytest | 692 passed, 20 skipped, **3 failed, 15 errors** |
| Vitest | 88 passed (10 files) |
| `vite build` | succeeds |
| Playwright | 23 passed (**backend mocked**) |

Pre-existing deviations, recorded and not fixed:

- 15 errors: `test_bl1101_ingest.py` needs untracked `storage/kg/matkg_bl1101_v7.json`.
- 2 failures: `TestMakeChatClient` needs an ambient `CBORG_API_KEY`.
- 1 failure: `test_kg_update.py` checks for `pixi` before validating arguments.

B01-B36: 32 anchors pass every mapped test, 2 pass with skips (B25 live Tiled
probe, B31 missing bl1101 data), and **2 have no tests** (B34 `repair_corpus.py`,
B35 `corpus_qa.py`). No pre-existing failure falls on a B anchor.

Mocks vs real: every test above used fake or mocked inference. No real FastAPI
browser test, real Tiled server, live model or paid endpoint was used.

## 4. LLM/QA baseline (plan section 5.1)

`f2w_llm_qa_baseline.json` holds 138 line-anchored items in three sections:
inference paths and admission (30), QA policy and prompts (62), and chat
state, approval and streaming (46). It also hashes 23 prompt strings
(`JUDGE_SYSTEM`: 5410 chars, `9ae2a566…bd6`, recomputed independently).
24 items have no test coverage. It is a static characterization mapped to
executed fake-model tests, not a recording of model outputs. Call-path
fixtures are T18A/T03 work.

Baseline behavior the migration must preserve or explicitly decide on. These
are recorded, not fixed:

1. **Model precedence.** UI `PUT /settings` > CLI `--model` > env
   (`KG_RAG_CBORG_MODEL` > `KG_RAG_MODEL` > `F2W_MODEL`) > `config.yml` >
   hardcoded `lbl/cborg-chat` / `deepseek-r1:70b`. A single chat request cannot
   override the model. `kg_rag_api.py` reads its env once at import.
2. **The CBORG admission cap is not actually shared (D18).** There are separate
   thread and async semaphores of `CBORG_MAX_CONCURRENCY` (default 5) each, per
   process, with no 429 backoff. `extract_terms.py` and the `cborg-openai`
   orchestrator backend bypass the cap.
3. **Four inference paths behave differently.** Temperature, timeout, base-URL
   variables and error mapping differ between the sync, async and tool-bound
   paths. Nothing streams model output. A straight swap would change behavior.
4. **Leeway answers look like normal answers.** The retrieval agent's leeway
   answer returns `sufficient=True` without a `leeway` flag, so `api.py:1978`
   counts it as genuine. The API's JSON-graph leeway is given node IDs but no
   context text. The separated fallback is `insufficient_evidence` with a
   disclaimer.
5. **Citation syntax is inconsistent and only partly enforced.**
   `[KG: gid: name]` is allowed but contexts emit `[KG:gid: name]`. PDF
   citations are checked only on the active-paper path. KG/OPS/MP citations
   are not validated server-side. The trace parser lacks an MP pattern.
6. **There are no prompt-injection guards** on retrieved KG/PDF/OPS/MP/Tiled
   text or history. `bool("false")` is truthy in the paper agent.
7. **Graph publication has no approval gate.** An approved extraction
   automatically rebuilds the KG and, in splash mode, wipes and re-imports it.
   `start_agent_backend.sh` always passes `--allow-splash-wipe`.
8. **Pending actions never expire.** They persist in `workflow_state.json` and
   capture every following message until declined.
9. **One global `asyncio.Lock`** serializes all sessions' turns. The KG rebuild
   runs synchronously in the async handler.
10. **Sessions isolate memory and workflow state only.** Corpus, terms and KG
    are shared across sessions.
11. **SSE.** `progress`/`complete`/`error`, LF framing, `turn_started` first.
    Exceptions become `error` with `stream_error` on HTTP 200. Disconnect
    cancels with no terminal event. No backend test covers the SSE routes.

## 5. Escalations

Details are in `license_dependency_audit.md`.

Decided by the owner on 2026-10-09 and applied on `forge_merge`:

- **E1: 90 publisher PDFs tracked in public repos.** Decision: gitignore them
  here; leave `main` alone. `f0c6ce3` untracks `papers/` (the `.gitignore` rule
  already existed). The files stay on disk. History and `main` still contain
  them.
- **E2: PyMuPDF is AGPL-3.0.** Decision: keep it for now; it will be deprecated
  in this repo in favor of Docling. `28820b0` marks all 13 import sites and the
  requirement with `DEPRECATED(pymupdf)`. The Docling switch must resolve the
  Docling/Tiled dependency conflict at T04 and keep the geometry that evidence
  locators use (T05/T09).
- **E3: Corpus data baked into the agent image.** Decision: `storage/` may be
  in the image; the Tiled catalog DB may not, except in a local image.
  `bfbec85` excludes `**/tiled_catalog*.db` via `.dockerignore`. The future
  `:local` target (T24B) may bundle a local catalog. This is an owner exception
  to the plan's "exclude storage/kg from build layers" (section 4.14).

After E1-E3 the isolated pytest run on a fresh HEAD export is unchanged (733
passed, same 3 failures and 15 errors).

Still open:

- **E4: Local paper-text index copied into locally built images.**
  `storage/source_index/literature/chunks.jsonl` (gitignored) contains paper
  text chunks with page locators. Under E3, an image built from a working
  checkout bakes it in (CI builds from clean checkouts would not). This
  conflicts with D19/D28. Proposed: add `storage/source_index/` to
  `.dockerignore` and mount it at runtime.

Other findings for H1 scoping (no decision needed now):

- `scripts/app/modules/kg_rag_api.py` duplicates the six model-compatible routes.
- `scripts/paper_finder.py` serves 3 more routes.
- 7 modules call `load_dotenv()` at import, which violates the minimal-import
  rule (plan section 4.5).
- 13 files mutate `sys.path`.
- 27 non-test files are not referenced by any test, including `debate_agent.py`
  (B06) and `repair_corpus.py` (B34).
- `2430320`'s no-index `fuzzy_merge_term` path returns `no_match` (to be
  measured at H3).

## 6. Dispositions

`f2w_baseline_inventory.json` proposes a default disposition for each of the
94 non-test source files:

| Disposition | Files |
|---|---|
| `retain_baseline_seam` | 33 |
| `retain_unmapped_needs_H1_crosswalk` | 31 |
| `retain_cli_facade_T19A_review` | 17 |
| `legacy_retained_deletion_needs_owner_approval` | 8 |
| `retain_entrypoint` | 5 |

No disposition removes anything. Feature-level paper-scope dispositions for
the FORGE donor are T17 work.

## 7. Resources, cost and risk

- Cost: no model, provider or paid call. Downloads were pip/npm packages and a
  Chromium headless shell into the scratchpad.
- Risks: E1-E3 above. The FORGE commit identity is unverified. The plan text
  differs from the listed `MIGRATION_PLAN.md` hash.
- Rollback: all H0 work is documentation on `forge_merge`. Rollback is to drop
  the branch or revert the commits. `main`/`rsoxs` are untouched.

## 8. Requested authorization

Approve H0 with this exact scope:

1. Accept `222b1e7` as the verified baseline and the 18 pre-existing test
   deviations in section 3 as known, unfixed baseline failures.
2. Accept the LLM/QA baseline (`f2w_llm_qa_baseline.json`) and parity inventory
   as the H0 reference, including the default dispositions in section 6.
3. E1-E3 are decided and applied (section 5). Decide E4, or record it as an
   open item to resolve by T24A at H1.
4. Unblock T01 and the H1 contract tasks (T18A, T19A, T20A, T21A, T22A, T23A,
   T24A, T25A, T26A), which remain documentation and contract work until H1.

Not requested: any push, PR, FORGE remote write, image build, Tiled access, live
model evaluation or change to `main`/`rsoxs`.

## Owner decision

Decision:
Limits:
Date:

Record the decision in `docs/migration/reviews/H0/decision.json`. Changes
outside the approved scope require a new review.
