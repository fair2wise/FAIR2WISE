# Human review packet: H1 (architecture contracts)

Gate: H1
Approver: project owner
Status: **awaiting owner decision**

H1 asks the owner to approve the shared contracts, the D34 trusted-local and
D30 ALS sign-in/admin implementation, Git promotion, and the remaining
engineering choices. H1 blocks T02 runtime integration and any team changing
shared interfaces.

## 1. Source and branch

| Item | Value |
|---|---|
| Baseline | `222b1e7` (H0 approved, `../H0/decision.json`) |
| H0 approval commit | `22362e0` |
| H1 commits | `7f015c0` .. `b827a3d` plus this packet (section 2) |
| Pushed | no; `forge_merge` exists only locally |
| `main` / `rsoxs` | untouched |
| Frozen UI | `ui/` tree `75a19b98...`, unchanged |

## 2. Tasks and deliverables

| Task | Deliverable | Commit |
|---|---|---|
| T01 shared contracts | `app/modules/contracts/` (CONTRACT_VERSION 1.0.0-draft.2), fakes, `tests/contracts/test_shared_contracts.py` | `7f015c0`, `349aa9f` |
| T01 ADRs + API spec | `docs/adr/{package-and-imports, optional-dependencies, inference-bridge, graph-mutation-semantics, job-and-approval-lifecycle, shared-file-ownership}.md`, `docs/migration/public_api_spec.md`, packaging prototype in `docs/adr/evidence/packaging/` | `cbb99fe` |
| T01 CI | `.github/workflows/forge-merge-ci.yml`, `scripts/ci/compare_known_failures.py`, `../H0/known_failures.json`, `../H0/known_lint.txt` | `b827a3d` |
| T18A UI contracts | `tests/contracts/legacy_ui/` (executed fixtures), `docs/migration/ui_contracts/` (OpenAPI, persistence keys, splash-to-Tiled ADR, CI plan) | `a27942d` |
| T19A operations | `docs/migration/operations_inventory.{json,md}` | `a96f67e` |
| T20A Tiled | `docs/adr/tiled-deployment.md`, `docs/adr/evidence/tiled/` | `c46b2ec` |
| T21A disclosure | `docs/adr/content-disclosure.md`, `docs/migration/disclosure/` | `d3ae414` |
| T22A schemas | `docs/migration/schema_inventory.json`, `docs/adr/schema-ownership.md` | `a96f67e` |
| T23A JSON KG | `docs/migration/json_kg_contract.md`, `tests/contracts/json_kg/` | `bd508f2` |
| T24A deployment | `docs/migration/deployment/` (profile matrix, Docker/gateway, Ansible inputs, source seams, O06-O08) | `c46b2ec` |
| T25A access | `docs/migration/authentication/` (access contract, JSON Schemas, current state) | `d3ae414`, `349aa9f` |
| T26A Git release | `docs/migration/data_releases/` (release contract, schemas, examples, KG inventory) | `bd508f2` |
| Owner decisions during H1 | `owner_decisions.md`: O-D1 (KG snippets), O-G1 (KGs/ops docs tracked, papers ignored) | `d9b6853`, `0d3d294`, `9155d76` |

All ADRs are **Proposed**. No runtime behavior changed. Code changes are
limited to the new `app/modules/contracts/` package (nothing imports it yet),
tests, the CI workflow and its helper script.

## 3. Test results

Isolated runs: `git archive` export, `env -i`, no `.env`, H0 venv.

| Suite | Result |
|---|---|
| Full pytest at HEAD | 941 passed, 3 failed (known: 2 ambient `CBORG_API_KEY`, 1 `pixi`), 20 skipped, 7 xfailed |
| `compare_known_failures.py` on that report | 0 problems |
| `tests/contracts` at HEAD | 193 passed, 3 strict xfails (`ChatRequest.batch_id`, section 5.4) |
| `tests/contracts/legacy_ui` at baseline `222b1e7` | 138 passed |
| `tests/contracts/json_kg` at baseline | 36 passed (57 with existing json2kg tests) |
| Hard-fail flake8 subset at HEAD | matches `known_lint.txt` (2 pre-existing F821s) |
| `mkdocs build --strict` | passes |
| CI on GitHub | **not run** (branch not pushed) |

Change since H0: committing `matkg_bl1101_v7.json` (O-G1) removed the 15
`test_bl1101_ingest` errors, so the known-failure set is now 3.

Mocks vs real: every test uses fake inference and in-process apps. No real
browser-to-backend test, Tiled server, model or paid endpoint was used.

## 4. Key findings

1. **No final Tiled 0.2.19 exists.** PyPI, GitHub releases and conda-forge
   stop at 0.2.18; only 0.2.19rc1/rc2 are published (re-checked 2026-10-12).
   The T20A API description is from the rc2 wheel. Rc2 changed the graph
   schema from 0.2.18 (`nodeId` -> `isNodeBound`, access tags), and entity and
   link properties are set only at creation.
2. **Tiled vs the F2W lock.** Tiled requires `pydantic-settings<2.12`; F2W pins
   2.14.2. Proposed: managed Tiled in its own environment; F2W talks HTTP.
3. **Current code cannot reach central ALS Tiled.** `tiled_graph.py:153-175`
   rewrites `*.als.lbl.gov` URIs to localhost; `PUT /settings` accepts any
   non-ALS Tiled URL and the backend then sends its Tiled key there (inferred,
   untested).
4. **Packaging.** A prototype `f2w` wheel works without moving files and
   leaves a FORGE `app` package untouched, but today's code double-loads
   `cborg_http`, reads a parent project's `.env` at import, and takes 4.8 s to
   import the API (torch, FAISS, PyMuPDF).
5. **Access today.** No route checks the caller. `GET /traces/{turn_id}`
   returns unclipped traces including PDF page text; compose enables tracing
   on all interfaces.
6. **Schemas.** `rsoxs_schema_extract.yaml` is never loaded and does not
   resolve alone; most entrypoints default to `matkg_schema.yaml` while compose
   uses `rsoxs_schema.yaml`; no output records a schema fingerprint.
7. **json2kg** mutates the caller's input lists when merging duplicates
   (recorded and pinned by a test, not fixed).
8. **Operations.** 9 destructive scripts; counting pitfalls (per-year
   histogram overcount, 800-per-type Tiled identity cap, "papers" counted as
   PDF filename keys).

## 5. Decisions requested

### 5.1 Must decide for H1

| # | Decision | Options | Recommendation |
|---|---|---|---|
| H1-1 | **Tiled artifact (D22).** | (a) Tiled team supplies the exact build the central servers run (hash), and `:local`/tests pin it; (b) explicit exception: pin `0.2.19rc2` until a final release; (c) pin 0.2.18 locally (schema differs from central) | (a), with (b) as a recorded interim if (a) is slow |
| H1-2 | **Package names and import approach.** | Distribution `fair2wise`, namespace `f2w` over `app/`; relative-import rewrite, `sys.path` hacks removed, scripts as facades, no import-time `.env` (all in T04) | approve |
| H1-3 | **Shared CBORG admission.** | One cap shared by sync and async callers incl. `extract_terms.py` and `cborg-openai`, 429-aware, behind a flag. Effective concurrency drops from up to 10 to 5 per process | approve behind a flag |
| H1-4 | **Fail closed without `CBORG_API_KEY`** instead of the SDK falling back to `OPENAI_API_KEY` | approve / reject | approve |
| H1-5 | **Separate publication gate.** | Extraction still rebuilds the JSON KG; import to Tiled requires an `ApprovalRecord` for the exact plan | approve |
| H1-6 | **`ChatRequest.batch_id`** (pre-migration field). | adopt into the frozen contract (refresh 3 fixtures) / remove it | adopt (additive, optional) |
| H1-7 | **Capability vocabulary and per-route policy** (`authentication/access_contract.md`) | approve as frozen in `CAPABILITIES` | approve |
| H1-8 | **Contracts** (`app/modules/contracts/`, ADR set, public API spec) | approve as the H1 baseline | approve |

### 5.2 Legacy splash-to-Tiled STOP items (T18A ADR)

Each needs a decision before T08B/T18C implement editing:

1. Re-linking every edge when a node gets a new revision (create-only properties).
2. Deleting links Tiled holds under another owner's access tags.
3. Unlinking snippets a stale browser tab left out of its list.
4. The wipe-and-reimport after each extraction (would reset a shared graph).
5. Partly applied Saves must not report success.
6. Non-admin ALS users still see edit controls; denial needs a new 403.

### 5.3 May be deferred to a named later gate

| # | Item | Defer to |
|---|---|---|
| D-1 | O06 image tag promotion, O07 Ansible/storage/backup, O08 local inference and arm64 (recommendations in `deployment/open_choices.md`) | T24B (after H2) |
| D-2 | Q-T3 central access tags and writer principal scope | H4 |
| D-3 | O-A1 ALS annotations admin-only; O-A2 role-check cache age; O-A3 expose the model proxy on ALS; O-A4 local + Tiled sign-in variant; C11 ALS approval expiry | T25B (H2) |
| D-4 | O-D2 disclosure budget values | T21B (H2) |
| D-5 | Schema Q1-Q5 (`schema-ownership.md`) | H3 |
| D-6 | Operations OQ1-OQ7 (`operations_inventory.md`) | T19B/T19C |
| D-7 | JSON KG questions 1-8 (`json_kg_contract.md`, `release_contract.md`), incl. `.gitignore`/`-text` attributes and plan errata for O-D1/E3 | T23B/T26B |
| D-8 | Still-ignored items: harvest manifests, publication-list RTF, `.cache/bl1101` caches (`owner_decisions.md` O-G1) | any time |

## 6. Resources, cost, risk and rollback

- No model, provider or paid call. Network use: PyPI, GitHub and conda-forge
  metadata; the Tiled rc2 and 0.2.18 wheels were downloaded and read, not
  installed or run; pip/npm packages for local test environments.
- Risks: Tiled artifact identity (H1-1); the CI workflow has not run on
  GitHub yet; ADR numbers and counts are snapshot-scoped.
- Rollback: everything since `22362e0` is additive documentation, tests, the
  unused contracts package, CI and data files. Roll back with `git revert`
  of the relevant commits (no history reset, D10). The KG and blueprint commit (`0d3d294`) is an
  owner-requested data change; reverting it would bring back the 15 bl1101
  test errors.

## 7. Requested authorization

Approve H1 with this scope:

1. Decisions H1-1 to H1-8 as chosen in section 5.1, and the six STOP items in
   5.2 (or record them as blocking T08B/T18C).
2. The deferrals in 5.3, each to its named gate.
3. Unblock T02 (runtime with policy and recording hooks) and T25B (access
   context), which together lead to H2.

Not requested: any push, PR, remote write, image build or publication,
central Tiled access, live model evaluation, or a FORGE change.

## Owner decision

Decision:
Limits:
Date:

Record the decision in `docs/migration/reviews/H1/decision.json`.
