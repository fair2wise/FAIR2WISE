# F2W forge_merge independent audit (c9cf9b0, 2026-10-29)

Scope: read-only review of the worktree at `c9cf9b0`, plus `flake8 --select=F,E9`, `pip check`
in the app venv, and AST/grep analysis. No model, network, Tiled, Docker or `.env` was used.
bandit is not installed in the venv (skipped, not installed). Severity scale: blocker / high /
medium / low / note. "Owner" names the plan task or gate that should carry the item.

---

## Findings

### AUD-01 (high, authorization) Job-stage re-authorization is a silent no-op in production wiring
- **Claim.** Every pipeline stage that declares a capability re-check runs without one, because no
  production caller of `JobService.register` passes `authority`, and `StageContext.require` returns
  `None` when none is configured.
- **Evidence.** `app/modules/jobs/pipeline.py:214-219` (`require` -> `return None` if
  `self._authority is None`); `pipeline.py:396-397` (`required_capability` enforced only through that
  `require`); registrations without `authority`: `app/modules/jobs/publication.py:112`,
  `app/modules/operations/reports.py:389`; `sc.require("graph.publish")` at `publication.py:152,281`
  and `sc.require("ingest.acquire")` at `jobs/stages.py:33` are therefore inert. `WorkerAuthority`
  (`app/modules/security/authorization.py:826`) is referenced only by tests; tests that prove the
  re-check re-register the kind themselves (`tests/jobs/test_publication.py:178`,
  `tests/jobs/test_pipeline.py:493`).
- **Impact.** On ALS, a job submitted by an admin keeps performing privileged effects after sign-out,
  expiry or role downgrade, except where the scope-change hook invalidates a publication approval
  (`publication.py:113`). The operation job kind (`reports.py`) has no approval to invalidate. The
  submit-time check is intact, so no unauthenticated submission is possible; this is a
  stale-authority gap, not an entry gap (AUTH-07 in the access contract is not met).
- **Fix.** `build_operations` wires `authority=WorkerAuthority(deps.ctx_for(job), settings,
  identity_port, credential_provider=session_store lookup, clock).check`; `StageContext.require`
  raises `ContractError(INTERNAL)` on ALS when a stage declares a capability and no authority exists.
  Add a test that builds operations through `build_operations` (not a hand-registered kind) and
  shows the import stage stops after sign-out.
- **Owner.** T10 follow-up / T14 (`build_operations`); gate H4 (binary case AUTH-07 should fail today
  if run through the real wiring).

### AUD-02 (medium, secrets) Import-time `load_dotenv()` remains in three files; one is copied into every image
- **Evidence.** `app/run_pipeline_cborg.py:30` (`load_dotenv()` at module top level, under `app/`);
  `scripts/run.py:19`; `scripts/app/modules/kg_rag_api.py:59` (stale duplicate of the module). All
  three call `dotenv.load_dotenv()` directly and ignore `F2W_DOTENV` (`app/modules/dotenv_entry.py`
  is bypassed). `Dockerfile` `COPY scripts/ ./scripts/` puts `scripts/app/modules/kg_rag_api.py`
  into `runtime-base`, although `ROUTE_POLICIES` R26-R31 describe it as "developer script; not
  packaged" (`authorization.py:634-640`). `app/modules/tiled_sim.py:278-287` loads the repo `.env`
  from a function (`:1049`, CLI path only; acceptable).
- **Impact.** In a checkout, importing `app.run_pipeline_cborg` (or any test that does) loads the
  real `.env` into the process (the test-isolation leak recorded in the project memory). Images
  carry no `.env` (`.dockerignore`), so the runtime risk is the stale, unguarded model-API copy
  being present in all three variants.
- **Fix.** Move the two `app/`/`scripts/` calls under `if __name__ == "__main__":` via
  `load_entrypoint_dotenv`; delete `scripts/app/` or exclude it in `.dockerignore`; add an AST test
  that `app/` and `scripts/` contain no module-level `load_dotenv(` outside `dotenv_entry.py`.
- **Owner.** T17B (legacy consolidation) for the code; T24B for the image allowlist.

### AUD-03 (medium, security) Model-compatible API has neither access guard nor projector; binds 0.0.0.0
- **Evidence.** `app/modules/kg_rag_api.py:205-330`: `create_fastapi_app` registers six routes with
  no `Depends(request_context)`, no `DisclosureProjector`; `run_fastapi` -> `uvicorn.run(app,
  host="0.0.0.0", port=11435)` (`:339`). `project_model_chat` exists (`disclosure.py:1445`) but is
  never called from this app. `ROUTE_POLICIES` R20-R25 declare `chat.ask` for this surface
  (`authorization.py:628-633`) but nothing in the surface reads them. xfail strict in
  `tests/contracts/legacy_ui/closure/test_closure_api.py:70` documents the D19 gap (T18C F3).
- **Impact.** On `:local` launched via `run_fastapi`, any host on the network gets unauthenticated
  chat with unbounded source echo. ALS images are unaffected only because nginx returns 404 for
  `/fair2wise/model-api` (`docker/nginx.conf:105-108`). Already an open owner item (B6/B7).
- **Fix.** Independent of B6: bind `127.0.0.1` by default with an explicit flag for network;
  wrap both chat handlers in `project_model_chat`; attach `request_context` so R20-R25 are real.
- **Owner.** B6 decision; implementation T14 follow-up before H6.

### AUD-04 (medium, security) Origin gate and body cap cover `/v1/` and `/auth/` only; the 19 legacy routes have neither
- **Evidence.** `app/api/main.py:39` `GUARDED_PREFIXES = ("/v1/", "/auth/")`; `main.py:113-114`.
  Legacy `POST /graph/upload` writes `json.dumps(graph)` of any size to `<workdir>/uploads/<name>`
  (`app/modules/f2w_agent/api.py:3526-3539`; filename is sanitized with `Path(...).name`, good).
  CORS: `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]` with explicit
  origins (`api.py:6822-6825`); dev origins are the fallback whenever `cors_origins` is empty
  (`:6822`), and the C7 requirement is enforced only by `docker/entrypoint/preflight.py:47-56`,
  not by `create_app`.
- **Impact.** Cross-site state-changing requests to legacy routes rely on `SameSite=Strict` alone on
  ALS and on nothing on `:local`; unbounded uploads on a directly exposed API (compose publishes
  8090) are a disk-exhaustion vector. nginx's `client_max_body_size 128m` (`nginx.conf:95`) helps
  only behind the gateway. Known items B8/B9; the CORS fallback is new.
- **Fix.** Extend `GUARDED_PREFIXES` to the legacy mutating routes (PATCH/POST/DELETE), cap
  `/graph/upload` at 128 MB, refuse the dev-origin fallback when `settings.profile` is ALS.
- **Owner.** B8/B9 decisions; T14 follow-up; gate H6.

### AUD-05 (medium, disclosure) The 40% region rule is per response; the ledger has no cross-response limit by default
- **Evidence.** `app/modules/security/disclosure.py:134-142` `DEFAULT_BUDGET` sets every
  per-work/session/actor limit to `None` and `max_region_fraction=0.4`; `_consume` applies the
  fraction to `state.per_region` (per response, `:730-740`); `DisclosureLedger.try_consume` skips
  scopes whose limit is `None` (`:540-549`). Echo guard reports runs >= 200 chars only
  (`echo_min_chars`, `:115`).
- **Impact.** A client that asks for different spans of the same page in successive turns can
  reconstruct the page (40% + 40% + 20%); unquoted verbatim runs under 200 chars are never
  counted. This follows owner decision O-D2 ("no count limits"), so it is a point of concern for
  D19's "no reconstructable source-text export", not a defect against the approved budget.
- **Fix.** Make the region fraction also a ledger scope (for example 60% per session per region
  within `window_seconds`), which needs no count limit; surface this trade-off to the owner.
- **Owner.** T21B follow-up; owner note at H6 (D19 review).

### AUD-06 (low, disclosure) `/v1` responses get path-only projection
- **Evidence.** `app/api/routes_common.py:70-75` -> `projector.project_paths_only` (no private-key
  scrub, no quote budget). `routes_review.py:20,52` returns a 600-char raw evidence excerpt (B10).
  `JobEvent` carries typed fields only (`contracts/jobs.py:68-93`), so job streams are bounded.
- **Impact.** Bounded: the curation excerpt (admins only, B10) is the only source text on `/v1`.
  Future `/v1` payloads that embed text would bypass the scrub silently.
- **Fix.** Route `/v1` through `_scrub` as well, and route the curation excerpt through
  `project_text` once B10 is decided.
- **Owner.** T14; B10.

### AUD-07 (medium, plan drift) Binary case manifest still records H3 as not approved
- **Evidence.** `evals/binary/cases/index.json` `"human_gates": {"H3": "not_approved"}`; all 108
  cases carry `owner_approval.status = "proposed"`, `approved_by = null` (counted over
  `evals/binary/cases/*.json`); `evals/binary/runner.py:326,381` reports `owner_approval:proposed`
  as a reason row. `docs/migration/reviews/H3/decision.json` has `status: approved`,
  `decided_on: 2026-10-16`. Readiness: 56 executable, 44 blocked, 8 `needs_h3_rubric`;
  `rubrics.json` is `draft_not_approved`. Blockers by task: T24E (7), T24C (6), T08B (12 incl.
  shared), T10 (8), T23B (5), H3 B4 pilot labels (9: QA-01/03/04, EXT-01..03, FIG-01/02, LOCAL-02),
  T26B (4), T13 (3), T15 (1), T22B (2), O03 (3: LLM-05..07), T24B (5), T24D (4), T25C (3).
- **Impact.** Every binary report produced for H4/H6 will carry "proposed" cases and the
  8 rubric cases cannot run; the gate evidence will never read clean unless the approval is
  recorded in the manifest.
- **Fix.** A single owner-directed commit writing `owner_approval` (ref `reviews/H3/decision.json`)
  into the case files and `human_gates.H3`, plus approved rubrics; regenerate `index.json` hashes.
- **Owner.** T11A/T11B; gate H4 blocker if left.

### AUD-08 (medium, operational) Unhashed, partly unpinned Python dependencies; CUDA torch likely dominates the 13.2 GB image
- **Evidence.** `requirements/runtime.txt`: 479 lines, 0 `sha256` entries. `requirements/semantic.in`
  is unpinned (`torch>=2.12.0`, `sentence-transformers>=5.5.1`, `faiss-cpu>=1.14.2`) and is part of
  the default install (`requirements.txt:3`). No CPU index (`--extra-index-url
  .../whl/cpu`) anywhere in `requirements/*` or the `Dockerfile`, while the image sets
  `KG_RAG_FORCE_CPU=1` (`Dockerfile`). `pip check` in the venv: `huggingface-hub 1.33.0 has
  requirement click>=8.4.2, but you have click 8.3.3` (B25). `docker/profiles/tiled-server/
  constraints.txt`: 0 hashes (B24). The macOS venv's CPU-only torch is 528 MB; the Linux default
  wheel pulls CUDA plus `nvidia-*` packages (several GB), which is the most plausible bulk of
  13.2 GB (inferred, not measured: no build was run).
- **Impact.** Non-reproducible images (a `>=` range resolves differently per build), supply-chain
  exposure, oversized images, and a `pip check` failure in the shipped environment.
- **Fix.** Pin `semantic.in` into a Linux lock with the CPU index and `--require-hashes` for both
  the app and the Tiled venv (B24 run); drop the resolver conflict by pinning `huggingface-hub<1.33`
  or raising click (B25); record the per-layer size in `scripts/ci/image_checks.py`.
- **Owner.** T24B/T24D; B24/B25; gate H6.

### AUD-09 (medium, test health) No CI job runs the disposable-Tiled suites; 135 default skips hide them
- **Evidence.** `.github/workflows/forge-merge-ci.yml:86` runs plain `pytest` (no
  `F2W_TILED_PYTHON`); `build-app.yml:70,111` likewise. 11 test modules are `pytestmark =
  skipif(tiled_python() is None)` plus conftests in `tests/security_live`, `tests/api_v1_live`,
  `tests/graph_import`, `tests/fixtures/tiled_server.py:71`. Skip inventory in `tests/`: 26
  `skipif`, 10 `pytest.skip(`, 4 `importorskip`, 4 `xfail` (3 in `tests/evals` for known regex /
  substring gaps, 1 strict for F3). Non-Tiled skips: PyMuPDF absent (3), port 8081 busy (2),
  git-checkout-only (2), bl1101 fixtures (2), opt-in live probe (1), gateway image root (1).
- **Impact.** The importer, ALS identity/role enforcement, `/v1` live permissions and the T18C
  closure checks (about 260 tests) are verified only on a developer machine; CI green does not
  cover them. T17's "demonstrated with a disposable Tiled" claims rest on local runs.
- **Fix.** Add a CI job that creates the `venv-tiled` from `constraints.txt` and runs the gated
  suites; keep the default-suite skip but report the count in the known-failures comparison.
- **Owner.** T24D / integration lead; gate H4.

### AUD-10 (low, correctness) Approval TTL: code is 1 h on every profile; the decision register says "24 h placeholder" and recommends none on `:local`
- **Evidence.** `app/api/operations.py:58` `DEFAULT_APPROVAL_TTL = timedelta(hours=1)`; `:366`
  applied regardless of profile. `OPEN_DECISIONS.md` B14: "the code has a 24 h placeholder;
  `:local`: no expiry, as today. ALS: 24 h".
- **Impact.** A `:local` operator's publication approval silently goes `STALE_APPROVAL` after an
  hour, contrary to the baseline behaviour the register promises to keep; the register is wrong
  about the code.
- **Fix.** Make the TTL profile-dependent once B14 is answered; correct B14's context text now.
- **Owner.** B14; T14.

### AUD-11 (medium, docs) README and MkDocs still describe the Splash architecture and old ports; MkDocs ships inside every image
- **Evidence.** `README.md:10` ("Import the MatKG JSON into `splash_links`" as workflow step 4),
  `:31` (browser talks to 8090), `:44-45`, `:147-161` (Splash env). `mkdocs/docs/architecture.md:
  9-31,44,68-82` (Splash as the graph service), `deployment.md:127-221` (Splash volumes, backups),
  `knowledge-graph.md:74-81`, `retrieval.md:11`, `splash-links.md` (whole page). `Dockerfile`
  `docs-build` stage copies `mkdocs/` into `/fair2wise/docs` of all three images. `plan.md` D22,
  D24, D33 still say Tiled 0.2.19 without a pointer to the O-T1 0.2.18 exception (only
  `docs/adr/tiled-deployment.md` carries the banner). Release docs correctly say 8080
  (`docker/profiles/README.md:31-35`, `mkdocs/docs/release.md:122`); no stale port-80 text found.
- **Impact.** Operators of the new images read docs for a deprecated backend (D03/D16) and for the
  developer compose topology; ALS deployers see Splash backup procedures that do not apply.
- **Fix.** Mark the Splash pages legacy (move under `legacy.md`), add a "current architecture
  (Tiled, /fair2wise, supervisor)" page, annotate D22/D24/D33 with O-T1.
- **Owner.** T16A (runbooks) / T24D docs; before H6.

### AUD-12 (low, docs) ADR decisions overtaken by later owner decisions without ADR updates
- **Evidence.** `docs/adr/schema-ownership.md` 2.1: "Do not add measurement-instance classes"
  (overridden by H3 F5/F6, A4, T22C `MeasurementRun`; only Q4 licence at `:92` was annotated).
  `job-and-approval-lifecycle.md:40` still calls pending-action expiry "proposed" (T02B merged,
  B14 open). `graph-mutation-semantics.md` item 3: "exact projection decided with T18A/T23A" (still
  open: STOP-1..6, A4). Every ADR remains `Status: Proposed (gate H1)` although H1 is approved.
- **Fix.** One docs pass: status lines to "Accepted at H1 (date)" and a "Superseded by" note per
  changed decision.
- **Owner.** T01 owner (integration lead); T27 sniff test at the latest.

### AUD-13 (low, test health) Two of the three known failures are trivial test-isolation defects carried since H0
- **Evidence.** `docs/migration/reviews/H0/known_failures.json`:
  `TestMakeChatClient::test_cborg_backend` / `test_cborg_openai_alias` fail only for a missing
  `CBORG_API_KEY` (`tests/test_strip_ansi_and_helpers.py:288-293`, no `monkeypatch.setenv`); the
  third (`tests/test_kg_update.py:14`) checks pixi before `--allow-splash-wipe`, on the deprecated
  Splash path (`app/modules/f2w_agent/kg_update.py`).
- **Impact.** A green `compare_known_failures.py` masks nothing today, but three weeks of carrying
  a one-line fix lowers the signal of the list; the Splash test should be retired with Splash.
- **Fix.** `monkeypatch.setenv("CBORG_API_KEY", "test")` in the two tests; reorder the
  precondition check or mark the Splash test for H7/H8 retirement.
- **Owner.** T27; H8 for the Splash test.

### AUD-14 (low, plan drift) Task graph says every task is `not_started`; ownership boundaries were crossed in several merges
- **Evidence.** `docs/migration/companion/task_graph.json`: 50 tasks, all `not_started`, while the
  log shows about 35 task merges (T02-T26B). Files outside the task's `exclusive_path_ownership`
  (diff of each merge against its first parent, docs/tests excluded): T11B (`d992ce4`) touched
  `ui/` (6 files), `app/api/` (4), `.github/workflows/`; T25C (`93fb0e8`) touched `app/api/`,
  `app/modules/security/`, `f2w_agent/api.py`; T22C (`2a80557`) `app/modules/graph/`; T08A/T08B/
  T22B touched `app/modules/graph/` beyond their sub-area; most tasks edited `pyproject.toml`.
  `0fdbf9a` (T11B) re-pinned `FROZEN_UI_TREE` itself; the pin now matches `HEAD:ui`
  (`a9c222b1…`, verified).
- **Impact.** Process, not product: all were merged by the integration lead (ADR
  shared-file-ownership permits that), and the UI change implements owner O-U2. But no
  machine-readable status exists, and self-re-pinning the frozen-UI guard weakens the guard.
- **Fix.** Keep the companion byte-for-byte (A1) but add `docs/migration/companion/status.json`
  with merged commit per task; require the owner's `ui-change-approved` note in the commit that
  re-pins `FROZEN_UI_TREE`.
- **Owner.** integration lead; H6 packet.

### AUD-15 (low, operational) Writer-secret file: readability checked, mode and owner are not
- **Evidence.** `docker/entrypoint/preflight.py:141-144` (`is_file and os.access(R_OK)`);
  `tiled_config.py:386` refuses secret-valued env vars (good); generated keys are written 0600
  (`tiled_config.py:470`); the key reaches the Tiled child as a file path in argv
  (`tiled_lifecycle.py:390`), not a value (good).
- **Impact.** A world-readable secret file passes preflight on a shared host.
- **Fix.** Refuse `st_mode & 0o077` or a different owner uid; log names only (already the case).
- **Owner.** T24E (Ansible renders `/run/secrets` 0400, per `open_choices.md` K1); B23.

### AUD-16 (low, disclosure) Raw traces for every `:local` user, including when `:local` is network-bound
- **Evidence.** `disclosure.py:1273-1287` returns the unprojected record for `trace.read_any`, held
  by every `:local` context; `authorization.py:62-66,250` allow `F2W_LOCAL_BIND=network` with a
  logged warning only. Traces hold full prompts with page text (content-disclosure ADR 1.2).
- **Impact.** Owner-decided (2026-10-12) for the single-user case; with `network` binding the trace
  route is a source-serving endpoint for everyone on the LAN.
- **Fix.** Project traces when `local_bind == "network"`, or require an explicit second flag.
- **Owner.** T21B / T25B; note for the owner.

### AUD-17 (note, correctness) Derived-value tolerance is 1% while the register recommends 2%
- `app/modules/evidence/units.py:381` `RECIPROCAL_DERIVATION_REL_TOL = 0.01`; `OPEN_DECISIONS.md`
  D1.5 recommends 2% (paper A, 1.7% mismatch). Not a defect until D1.5 is decided; flagging so the
  constant and `extraction_field_mapping.md:141` ("1 % tolerance") change together. Owner: T06B.

### AUD-18 (note, correctness) Stale TODO and minor pyflakes items
- `app/modules/agent_runtime/supervisor.py:92` `TODO(T03): placeholder only` survives T03/T03B;
  confirm the placeholder is not on the `F2W_RUNTIME=langgraph` path or delete the comment.
- flake8 F-class in new modules: `extraction/schema_selection.py:18` (2 unused imports),
  `literature/download_pdfs.py:895` (unused `success`), `operations/corpus.py:65` (unused import).
- Owner: T27.

### AUD-19 (note, operational) Policy declaration check runs only in tests
- `undeclared_routes` (`app/api/auth.py:174`) is asserted in `tests/api_v1/test_v1_local.py:44`
  and `tests/security_live/*`, not at startup. Fail-closed holds anyway: an undeclared route is
  `DENIED` on ALS (`auth.py:236-238`) and open on `:local` by design. Suggest a startup assertion
  on ALS profiles so a future router cannot be mounted undeclared. Owner: T14/T25B.

### AUD-20 (note, operational) Known, accepted-pending items re-confirmed in code
- In-memory sessions and role cache are per process (`app/api/sessions.py:36-37`, B12).
- Safe fetch allows ports 80/443 only (`literature/safe_fetch.py:90`); legitimate publisher
  mirrors on other ports are refused (conservative; document it).
- `:local` CORS fallback to localhost dev origins with credentials (`f2w_agent/api.py:6779-6826`)
  is harmless behind the gateway; see AUD-04 for the ALS case.

---

## Counts by severity
- blocker: 0
- high: 1 (AUD-01)
- medium: 8 (AUD-02, 03, 04, 05, 07, 08, 09, 11)
- low: 7 (AUD-06, 10, 12, 13, 14, 15, 16)
- note: 4 (AUD-17, 18, 19, 20)

## Top 10 by risk
1. AUD-01 stage re-authorization never wired (`register(..., authority=None)`); AUTH-07 unmet.
2. AUD-08 unhashed/unpinned dependencies, CUDA torch in CPU-only images, `pip check` failing.
3. AUD-03 model API: no guard, no projector, 0.0.0.0 (B6 open).
4. AUD-04 legacy routes without origin gate or body cap; ALS CORS fallback outside the supervisor.
5. AUD-09 no CI coverage of the Tiled-gated suites that carry the integration claims.
6. AUD-07 binary manifest still "H3 not approved"; gate evidence cannot read clean.
7. AUD-05 per-response-only 40% rule; page reconstruction across turns (owner trade-off).
8. AUD-02 three import-time `load_dotenv()` calls; stale model-API copy shipped in images.
9. AUD-11 Splash-era README/MkDocs built into every image; plan D22/D24/D33 not annotated.
10. AUD-10 + AUD-12 register/ADR text that no longer matches code (TTL, schema classes, expiry).

## Checked and found clean
- **Profile selection / fail-closed.** `DeploymentSettings.__post_init__` refuses unknown profiles,
  mode mismatches, `local-operator` as writer, ALS target overrides, `*.als.lbl.gov` on `:local`
  (`authorization.py:118-170`). ALS with no policy -> `DENIED` (`auth.py:236-238`); no sign-in
  prompt on `:local`. `undeclared_routes(app) == []` for local and ALS apps in tests, so
  `app/api/policies.py` plus `ROUTE_POLICIES` cover every mounted route.
- **Trust headers.** `TRUST_CLAIM_HEADERS` logged and ignored (`auth.py:208-212`); only the
  `f2w_session` cookie is read, server-side store only (`sessions.py`); tokens never in
  responses or logs; cookie HttpOnly/Secure/SameSite=Strict (`sessions.py:310`).
- **Role check cache.** Bounded age `(0, 900]` s, default 60 s, fresh check forced for admin
  capabilities and in handlers before effects (`authorization.py:283-312,581`,
  `legacy_ui.py:373,434`); downgrade detection notifies scope listeners (`auth.py:126-138`).
- **Approvals.** Bound to `plan_sha256`, plan id, target, scope and expiry (`contracts/changes.py:
  186-195`); `create_approval` requires human actor, fresh whoami with the approver's own
  credential, admin, same subject; `verify_approval` checks profile, target, issuer, writer
  (`authorization.py:915-1001`); `approve_publication` refuses a changed plan (`operations.py:355`).
  All datetimes tz-aware (`datetime.now(timezone.utc)`, `fromtimestamp(..., tz=utc)`); no naive
  `utcnow()` in new modules.
- **`disposable_target`.** `init=False` field settable only by `for_disposable_test_target`
  (loopback `http://127.0.0.1:<port>` only, ALS profiles only); `from_mapping`, env and
  `dataclasses.replace` cannot set it (`authorization.py:128-131,220-249`).
- **SDK vs HTTP parity.** `/v1` handlers are thin over `F2WOperations`, which re-`require`s
  capabilities itself (`operations.py:277-402`); C2 forwarding applied; no `NotImplementedError`
  reachable from a route (`app/public.py:305` is SDK-only).
- **Snapshot/manifest hashes.** Graph bytes, manifest, terms input, json2kg source, schema
  fingerprint, validation and disclosure reports, parent verification, sorted sources checksum
  and extraction fingerprint (`snapshot.py:365-399`, `snapshot_manifest.py:278-288`);
  canonical `sort_keys` JSON for plan hashes (`imports/tiled_json.py:105-110`); no float equality
  in hashed material.
- **Importer.** Preflight re-verifies bytes against the manifest, refuses unsupported schema
  fingerprint, failed validation or disclosure (`tiled_json.py:153-200`); complete-scan
  reconciliation, fenced lease, same-revision receipt -> `noop`, partial scan never plans
  (`tiled_json.py:22-26,448-490,598-606`); writer 401/403 -> `writer_unauthorized` (C1).
  F4/B26 (rollback below a reconciled edit) is known and not re-rated.
- **Release checker.** `private:` refs refuse `..` and escaping the evidence root
  (`releases.py:926-936`); git paths repository-relative without `..` (`:420`); read-only git
  with argument lists and scrubbed env (`:372`).
- **SSRF / path traversal.** `safe_fetch.NetworkPolicy` rejects non-public addresses per hop and
  per redirect, re-checks the connected peer, limits size and time (`safe_fetch.py:98-252`);
  `LocalIntakePolicy` resolves symlinks before containment and refuses links by default
  (`:265-325`); folder routes RT-62..68 are not exposed over HTTP (B4); opaque `f2w://` ids
  decode only under two roots with `..` refused (`legacy_ui.py:150-170`); `/graph/node`
  `json_graph_path` limited to `storage/kg` and uploads (`f2w_agent/api.py:3542-3556`).
- **Secrets in config.** `F2W_TILED_WRITER_SECRET` and friends refused as env values
  (`tiled_config.py:386`); Tiled child gets a scrubbed env and the key by file path
  (`tiled_lifecycle.py:219,387-395`); supervisor loads an explicit dotenv once and passes
  `F2W_DOTENV=off` to children (`f2w_supervisor.py:228-271`); CI workflows pin actions by SHA,
  `permissions: {}` at top level, publication gated on `O06_APPROVED` (`publish-image.yml`);
  `executed_as` is always the configured writer and never the approver (`authorization.py:956,997`).
- **Disclosure on UI routes.** Chat, SSE, graph reads, node properties, publications and errors
  go through `project_chat_response` / `project_sse_event` / `_scrub` with a private-key
  denylist that covers `context_snippets`, prompts, paths and tokens (`disclosure.py:149-156,
  1194-1257`; `legacy_ui.py:512-601`); operations outputs have their own `FORBIDDEN_KEYS` guard
  and T19D tests (`operations/reports.py:61,145-160`). Paths that do not go through the
  projector: the model API (AUD-03), raw traces for `trace.read_any` (AUD-16, owner-decided),
  `/v1` path-only projection (AUD-06).
- **Hardcoded models/hosts.** Judge model is chosen from the live list via `JudgeModelService`
  (O-U2), not hardcoded; legacy agents default to `lbl/cborg-chat` through env (D05 baseline);
  `academy_extractor.py:120` CLI default `qwen3.5:9b` is a legacy launcher; ALS Tiled hostnames
  appear only as the fixed profile targets and refusal checks.
- **Supervisor.** Required services are never restarted in-container (profile parse refuses it);
  non-required Tiled uses exponential backoff up to `restart.max`, then `failed` in the status file
  (`f2w_supervisor.py:170-182,423-438`); API binds loopback only (`api_service.py:49-50`);
  `X-Forwarded-Proto` is forwarded literally with the edge-overwrite requirement recorded (C8).
- **Open questions recorded.** T14 Q1-Q8 -> C1, C2, B8, B9, T17, B10, B11/B12, C3; T18C F1-F7,
  D-1, D-2 -> A4, B6, B7, B26, C5, A5 (F5-F7 informational); T25C -> B12, B13; T19C -> D1.9-D1.11;
  T24B -> B18-B25; T24D -> D2; T26A's six release-contract questions (`release_contract.md:606`)
  are not in `OPEN_DECISIONS.md` (Q1 resolved by O-D1; Q2-Q6 on flat-file adoption, `.gitignore`
  negation, `metadata.graph_snapshot`, errata, descriptor location remain unrecorded). T19D and
  T26B reports list no owner questions of their own.
