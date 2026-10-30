# H1 owner decisions log

Decisions the project owner has made during H1 contract work. These are not
the H1 gate approval; that is recorded separately in `decision.json` when the
owner reviews the H1 packet.

## O-D1: verbatim snippets in stored KG JSON (2026-10-12)

**Finding (T21A).** Tracked `storage/kg` JSON holds about 127k verbatim
context snippets. A 300-sample check matched every sampled snippet to its
cited PDF page, and combined they reproduce a median 31% (max 48%) of each
paper's text across 90 papers. `storage/terminology` holds about 141k more.
Under D19 this would make the files a partial source-text export, in the
public repository and in any image that includes `storage/`.

**Decision.** Keep the KG JSON files, snippets included, in the repository and
in the Docker image.

**Scope of the exception.**

- Applies to stored KG artifacts: committing them to Git (D31) and baking them
  into images (H0 E3).
- Does not relax D19 for serving. What routes, SSE, traces, graph detail and
  exports return to users still goes through the T21A disclosure projection.
- Does not cover raw PDFs, full parsed paper bodies, figure crops, the
  source-RAG index (`storage/source_index`, H0 E4), credentials, local paths
  or operational databases.
- `storage/terminology` is already in the image under H0 E3 and stays in Git;
  this decision does not change that.

**Updated in.** `docs/adr/content-disclosure.md` (O-D1); T26A release contract
(relayed to the T23A/T26A worker).

## O-G1: what is tracked in Git (2026-10-12)

(Recorded as "O-D2" in commit 9155d76; renamed because `content-disclosure.md` already uses O-D2 for disclosure budgets.)

**Decision (owner, verbatim intent).** Beamline ops documents are fine to keep
in this repo. Research papers stay gitignored. All KGs are saved in this repo
and not ignored.

**Applied in `0d3d294`.**

- `.gitignore`: the `storage/kg/*.json` and `*.json.bak` rules are removed.
  `papers/` became `papers/**` with a re-include for
  `papers/rsoxs/beamline_blueprint/blueprint_*.html`. No PDF is trackable.
- Committed the 12 previously untracked KGs (`matkg_bl1101_v1`-`v9`,
  `matkg_rsoxs_v1`-`v3`) after a credential and local-path scan, and the BL
  11.0.1.2 blueprint HTML.
- Effect on the H0 baseline deviations: the 15 `test_bl1101_ingest` errors
  were caused by the missing `matkg_bl1101_v7.json`; with it committed, the
  isolated suite at HEAD is 770 passed, 3 failed (the ambient
  `CBORG_API_KEY` and `pixi` cases), 20 skipped, 4 xfailed.

**Still unclassified (left ignored pending the owner):**

| Item | Size | What it is |
| --- | --- | --- |
| `papers/rsoxs/manifest.json`, `papers/tomo/manifest.json` | 2.4 MB, 172 KB | Harvest manifests: paper metadata the source-RAG and harvest code read; no paper text expected |
| `papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf` | 490 KB | Publication-list export stored next to the blueprint |
| `.cache/bl1101/pages`, `readmes` | 120 KB, 2.7 MB | Cached ALS beamline page and READMEs, indexed as ops documents |
| `.cache/bl1101/repos` | 31 MB, part of 1264 files | Cloned third-party Git repositories indexed as ops documents (their own licenses apply) |

## O-T1: Tiled version (2026-10-12)

**Decision.** Use Tiled 0.2.18 for now.

**Why it is needed.** D22 pins exactly 0.2.19, but no final 0.2.19 release
exists (PyPI, GitHub releases and conda-forge stop at 0.2.18; only
0.2.19rc1/rc2 are published).

**Pin.** `tiled==0.2.18` (published 2026-09-02):

| File | SHA-256 |
| --- | --- |
| `tiled-0.2.18-py3-none-any.whl` | `416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06` |
| `tiled-0.2.18.tar.gz` | `2f3943260c46824b162796fcb8a43c3d02f41205e2ccefc64e365b18b6ec1e27` |

**Scope and risks to carry forward.**

- An interim owner exception to D22 for the managed `:local` server and the
  disposable test fixture. Revisit when a final 0.2.19 (or the central
  servers' exact build) is available; changing the pin invalidates H1.
- The T20A API description came from the 0.2.19rc2 wheel. Rc2 changed the
  graph schema from 0.2.18 (`nodeId` -> `isNodeBound`, access blobs -> access
  tags, four catalog migrations). T20B must re-check the capability contract
  against 0.2.18 before building on it.
- The central staging/production servers are reported to run 0.2.19 (D33). A
  local 0.2.18 graph may not match them. External-mode tests must check the
  central server's version and capabilities and fail clearly on mismatch.
- Tiled's `pydantic-settings` constraint still conflicts with the F2W lock;
  the managed server runs in its own environment (`optional-dependencies.md`).

## O-A5, O-A6, O-A7: T25B follow-ups (2026-10-12)

Owner answers to the T25B open questions:

- **O-A5 (JSON graph reading).** "All users on local, and all authenticated
  users on staging/prod should have json graph reading ability." Applied:
  `GET /graph/node/{id}` with `json_graph_path` now needs only
  `chat.read_support` (`authorization.graph_node_read_capabilities`, route
  R09). Per-turn graph choice in chat requests was already open. Changing the
  server-wide default selection (`PUT /settings json_graph_paths`) stays
  admin-only pending the owner (see open items).
- **O-A6 (`extraction_mode`).** Admin-gated. Already implemented: it is not a
  personal settings field, so changing it needs `settings.global`.
- **O-A7 (MkDocs).** "MKDocs should be rendered and hosted on ALS deployment
  target versions (and local)." Requirement for T24B/T24C: build the MkDocs
  site into every image (`:local`, `:als-dev`, `:als-prod`) and serve it
  under the `/fair2wise` prefix (the T24A source audit found the nginx image
  has no `/docs` route today). This is separate from FastAPI's generated
  `/docs`, `/redoc` and `/openapi.json` pages (open item below).

Owner answers to the remaining T25B questions (2026-10-12):

- **Service principals.** Refuse Tiled machine accounts as callers on ALS for
  now; FORGE acts on behalf of a signed-in user. Revisit at T15. Already
  implemented (`Unauthenticated("service_principal")`).
- **O-A8 (FastAPI API docs).** `/docs`, `/docs/oauth2-redirect`, `/redoc` and
  `/openapi.json` are public on ALS as well as local. Applied as
  `FRAMEWORK_ROUTE_POLICIES` (F01-F04) in `authorization.py`. These pages
  publish the full API schema to anyone who can reach the server.
- **Default graph selection.** Add per-user graph selection, so no user changes
  the selection for others. New work, scheduled with the settings/session
  migration (T14/T18C). Until it exists, `PUT /settings json_graph_paths`
  (the server-wide default) stays admin-only on ALS; local users can always
  change it.
- **O-A1 (annotations).** Any signed-in ALS user may annotate their own turns;
  admins may annotate any turn. Recorded on route R19; enforced with the
  own-or-any helper when the legacy routes are wired (T03/T18B).

Still open: `workflow_mode` (recommended: leave admin-only; it has no effect in
the UI API), O-A2 (role-check max age; default 60 s, capped at 900 s).

## O-D2 and T21B follow-ups (2026-10-12)

- **O-D2 (quote limits).** First answer: "dont worry about quote limits as long
  as they are relevant". Tests then showed that with no limits at all, a whole
  retrieved page passes through verbatim, which D19 forbids. Clarified answer:
  **no count limits, block page dumps.** Applied in
  `security/disclosure.py`: the default budget has no per-quote, per-answer,
  per-session or per-user limits; a quote is shown verbatim when it verifies
  against a source retrieved and cited for the turn; one response may not
  reproduce more than 40% of any single page/section verbatim
  (`max_region_fraction=0.4`, counting quotes and echoed runs). The earlier
  proposed numbers remain available as `STRICT_BUDGET`. Existing field cuts
  (abstracts, properties, descriptions, errors) are unchanged.
- **Tiled URL in settings.** Editable on `:local` only; never writable at
  runtime on ALS (`settings_update_capabilities(..., local=...)`).
- **Traces.** Admins get the raw trace from `GET /traces/{turn_id}`:
  `DisclosureProjector.project_trace_for(record, context)` returns the raw
  record when the server-built context holds `trace.read_any` (every `:local`
  user; verified ALS admins) and the projected summary otherwise.
- Remaining T21B conflict for wiring (no owner decision needed): the uploaded
  graph path becomes an opaque `f2w://` id, so `GET /graph/node` must accept
  that id before the projection is wired into routes.

## O-R1: supervisor planner failure (2026-10-13)

**Decision.** "Yes keep the fallback." In `F2W_RUNTIME=langgraph` with
`F2W_QA_ROUTE=supervisor`, if the planning model errors or does not call the
QA tool, the turn falls back to the direct `answer_from_knowledge` call; the
reason is logged and the failed planning call is recorded in the trace. To be
revisited before the supervisor route becomes the default runtime (H6/H7).

## O-P1: T04 follow-ups (2026-10-14)

Owner replied "let's continue" to the T04 recommendations; recorded as
accepted: (1) the installed CLI reads `.env` only from the current directory;
(2) the F2W-client-to-LangChain bridge for `create_literature_agent` belongs
to T15; (3) remaining import-time settings (kg_rag_api, CBORG cap, repo-root
paths) move to startup in one later cleanup task with parity tests;
(4) console scripts with T14; (5) the first GitHub CI run is the Linux check.
Also: at most 2 workers run in parallel.

## O-U1: frozen UI re-pin for the /fair2wise prefix (2026-10-16)

**Decision.** "A5: I approve." T24C changed two lines in `ui/src/app/App.tsx`
(router `basename` and logo path from `import.meta.env.BASE_URL`, identical
when the base is `/`). The CI frozen-UI tree is re-pinned from `75a19b98…` to
`2cc7ccd3778bac46794cbd1ef254211e617cc38d`. Historical documents keep the
baseline hash. Any further `ui/` change still needs owner approval.

## O-U2: judge-model picker in the settings panel (2026-10-16)

**Decision.** H3 B7 follow-up: "model judges should be able to picked from a
dropdown with what's currently available (not hardcoded)"; asked where, "in the
settings panel in the UI". Approved `ui/` change: the settings panel gets a
judge-model dropdown populated at run time from the models the configured
backend currently lists (no hardcoded model). The choice is a setting read by
the separate validation runner (binary suite, non-blocking for the app per H3
B); each report records the judge model used. Implemented with T11B; the CI
frozen-UI pin is updated in that change (as O-U1).

## Register walk-through, batch 1 (2026-10-30)

- **D3.1 / PC-1 (O-D3).** "Track in Git and ship": the `storage/terminology/*.json`
  harvest files are committed and stay in every image. Consequences: remove the
  `.gitignore` rule at line 185, commit the 9 files (321 MB), the release-manifest
  `shipped_untracked_files` check becomes a refusal, O-D1's "stays in Git" is now true.
- **D3.11 (O-X1).** "Yes, build and run locally": the integrator may run `docker build`
  and `docker run` on this machine for the T24E container checks. Local images and
  containers only, random loopback ports (never 8080 while the owner's container is
  up), no push, no registry login, everything stopped and removed afterwards.
- **D3.5 / D3.8 / B14 (O-T2).** Approval TTL: none on `:local`, 24 h on ALS. Chat
  approvals keep never expiring (H2 Q8).
- **D1.1 / D1.2 (O-S1).** Schema set 0.3.0 approved; name `MeasurementRun` (and
  `RunCondition`) accepted. 0.3.0 becomes selectable for extraction and builds; the
  default schema stays as is until the pilot.

## Register walk-through, batch 2 (2026-10-30)

- **B1 (O-P2).** "Yes, and switch default": port Docling behind the pluggable parser
  interface and make it the default parser once its parity tests pass on the pilot
  papers; PyMuPDF stays available (and default until then). New task T29 (parsing).
- **B2 (O-P2).** Licensing / open-access metadata is recorded at intake as part of the
  same T29 parsing task (OpenAlex/Crossref fields plus the PDF's own notice); required
  before any H5 publication.
- **Tiled graph editing plan Q1-Q16:** owner chose to answer one by one (recorded below
  as they are answered). **B3 exceptions:** owner chose to pick individually.

## Register walk-through, batch 3: B3 exceptions (2026-10-30, O-P3)

- **R-08 Langfuse tracer:** NOT an exception. Port it as an opt-in exporter behind a
  setting, off by default, with a disclosure review (traces hold source text). Work item
  for T29/T17 scope.
- **X-05 `seed_synthetic_kg.py` and X-07 `bench_kg.py`:** NOT exceptions. Port both into
  `scripts/` as developer tools (T29 scope).
- **RT-20 `add_annotation`:** exception approved; record-level annotations stay in FORGE.
- **A-01 `openalex_supplement.json`:** retire once L-17 citation links are regenerated
  (T29). **RT-47 `table_csv`:** stays an internal agent helper, not a user route.

## Register walk-through, batch 4: Tiled editing plan Q1-Q4 (2026-10-30, O-E1 part 1)

- **Q1:** "One graph id per version" (option a), i.e. `rsoxs_v4`, `rsoxs_v5` stay separate
  Tiled graphs. Pending confirmation of the consequence (batch 5): unchanged records get
  new Tiled ids each version, so cross-version identity and citation redirection go
  through the IdentityLedger rather than shared Tiled ids.
- **Q2:** import staged, switch the live marker, retire N (hidden, revivable).
- **Q3:** purge is a separate admin-approved step; never for a record with foreign links;
  keep at least one previous version by default.
- **Q4:** report foreign links before approval, keep the retired record alive; propose
  stable anchor entities to Alex and the Tiled team.

## Register walk-through, batch 5: Tiled editing plan Q1 (revised), Q5-Q7 (2026-10-30, O-E1 part 2)

- **Q1 (revised after the consequence check):** one stable Tiled graph per corpus (option b);
  the live version is named in the release marker. Unchanged records keep their Tiled ids
  across versions; only changed records get new ones. Supersedes the batch-4 Q1 answer.
- **Q5:** yes, write a release-marker record (corpus, version, snapshot hash, receipt).
- **Q6:** F2W reads stay pinned through the ledger during a switch; accept the short mixed
  window for other readers; ask the Tiled team about transactions.
- **Q7 (STOP-1):** "Multi-write per Save" (option a), not the recommended journal-and-defer.
  Each Save performs the recreate-links sequence on Tiled immediately, journaled and
  resumable, with a recovery job for crashes that leave links half-moved. Consequences:
  Q12 acknowledgment must wait for the Tiled effect, not only the journal; Q8/Q14 apply
  to every Save; implementation task T08D grows accordingly.

## Register walk-through, batch 6: Tiled editing plan Q8-Q11 (2026-10-30, O-E1 part 3)

- **Q8:** a Save during a live switch is refused with the existing 502.
- **Q9 (STOP-2):** remove only F2W-owned links; others return the existing 400.
- **Q10 (STOP-3):** keep the implicit snippet unlink, with a stale-revision check (409 on
  a Save from an out-of-date copy).
- **Q11 (STOP-4):** scoped publication after extraction runs automatically on `:local`
  when the plan is safe; on ALS it waits for admin review.

## Register walk-through, batch 7: Tiled editing plan Q12-Q15 (2026-10-30, O-E1 part 4)

- **Q12 (STOP-5):** a Save is acknowledged only after the journal write AND the Tiled
  effect complete; a partial failure returns the existing 502, the journal entry is
  marked failed, the recovery job reconciles it.
- **Q13 (STOP-6):** "Hide controls via a capability read". This is a frozen-UI change:
  recorded as **O-U3 (granted in principle)**: the UI reads the caller's capabilities and
  hides Save/edit controls for ALS non-admins; the server still returns 403 as the
  backstop. The CI `FROZEN_UI_TREE` pin is updated in the implementing change (T18D).
- **Q14:** "Ignore and continue": a foreign record that changes under a run does not
  stop the run. The pre/post fingerprint report still records the change in the receipt
  for the admin; the run does not fail closed on it.
- **Q15 (F4/B26):** rollback below a carried-forward edit requires a reviewed conflict
  resolution.

## Register walk-through, batch 8: Q16, B4, B5, B7 (2026-10-30)

- **Q16 (O-E1 part 5, closes Q1-Q16):** one verified admin approves als-prod replacement or
  purge, with `requested_by` and `approved_by` recorded; revisit before the first
  production purge. The Tiled graph editing plan is now fully decided (Q1 revised to one
  graph per corpus; Q7 multi-write per Save; Q13 capability read, O-U3; Q14 ignore and
  continue; all others as recommended). The seven proposed tasks (T20C, T08C, T08D,
  T23C, T18D, T18E, T10B) are approved to be added to the plan as addendum A6.
- **B4 (O-P4):** folder browse/index routes on `:local` only, with root directories fixed
  in configuration; disabled on ALS images.
- **B5 (O-P4):** L-14..L-17 join T29 (parsing); job kinds J-11 / RT-27..32 become T10B;
  OCR (V-05) and the missing `/v1` routes (cost control, bulk review, vocabulary quality,
  figure-scale records) get their own small tasks before H6.
- **B7 (O-A9):** "Expose on ALS for signed-in users": route `/fair2wise/model-api`
  through the gateway with sign-in and the D19 projection (applied by T28A). Reverses
  the T18C/T24C default (404 on ALS). Work item for the gateway and route policies.

## Register walk-through, batch 9: B10-B13 (2026-10-30, O-A10)

- **B10:** reviewer excerpt stays 600 characters.
- **B11:** accept origin check + `SameSite=Strict` as the CSRF protection until a sign-in
  page exists.
- **B12:** in-memory ALS sessions accepted until T24E delivers a persistent store
  (single-process ALS images until then).
- **B13:** the 0.2.18 revocation window (up to 15 min for tokens held outside F2W) is
  accepted for the interim pin; revisit at 0.2.19.

## Register walk-through, batch 10: B15-B17, B20 (2026-10-30, O-T3)

- **B15:** loopback binding is enough for the managed `:local` Tiled; no access policy
  unless `:local` is ever exposed.
- **B16:** `executed_as = single-user-api-key` accepted for `:local` audit.
- **B17:** pin SQLAlchemy 2.0.x in the Tiled lock to match upstream's bound; regenerate
  the lock and rerun the Tiled suites (work item).
- **B20:** the Compose developer stack at `http://127.0.0.1:5175/fair2wise/` is accepted.

## Register walk-through, batch 11: B21-B24, D3.13 (2026-10-30, O-X2)

- **B21:** the standard-library Python supervisor under tini is accepted (no s6-overlay).
- **B22:** a failed bundled Tiled on `:local` leaves the container serving JSON workflows.
- **B23:** an ALS image may start without a writer secret (writes fail with
  `writer_unauthorized`); an unreadable or badly permissioned secret is refused at startup.
- **B24 / D3.13:** the sibling hashed-lock layout is accepted (images `--require-hashes`;
  developers and CI keep the plain lists).

## Register walk-through, batch 12: B25-B29 (2026-10-30, O-X3)

- **B25 / D3.10:** raise `click` to 8.4.2 in the runtime lock (hashed lock regenerated;
  full suite rerun). Work item.
- **B26:** covered by Q15 (reviewed resolution required).
- **B27:** the runner re-plans automatically after a stale approval and then waits for
  approval of the new plan (one fewer manual step; the approver still sees the new plan).
  Work item in T10B.
- **B28:** rollback as a toggle between the last two pointers is accepted and documented.
- **B29:** switch the legacy `DownloadAgent` to the hardened T05 downloader behind a
  parity test on the existing download fixtures. Work item (T29 scope).

## Register walk-through, batch 13: B30, D1.3-D1.5 (2026-10-30, O-S2 / O-P5)

- **B30 (O-P5):** prepare the F2W-side package surface for T15 now (import surface, a
  consumer example, a contract test FORGE can run); the FORGE-side changes wait for the
  owner's signal to coordinate with Alex.
- **D1.3 (O-S2):** reconcile bl1101 now: define bl1101 v3 mapping its `Sample`, `Detector`
  and `Endstation` onto 0.3.0's `Sample` and `Instrument` before the pilot (T22C follow-up,
  reviewed diff).
- **D1.4 (O-S2):** add a normalized-value slot to 0.3.0 (converted value, unit, factor and
  conversion profile as provenance). Versioned additive change to 0.3.0 (becomes 0.3.1).
- **D1.5 / D3.12 (O-S2):** derived-value tolerance 2% (`RECIPROCAL_DERIVATION_REL_TOL`).

## Register walk-through, batch 14: D1.6-D1.9 (2026-10-30, O-S3)

- **D1.6:** cited wins over derived; the derivation stays recorded on the record.
- **D1.7:** add an opt-in `--figures multimodal` flag to the labelling tool (figure crops to
  a vision-capable CBORG model for FIG-01..03); off by default.
- **D1.8:** LOCAL-02 counts as covered once one reviewed pilot paper exists.
- **D1.9:** add a content-hash step to the K1 distinct-Work rule (byte-identical files count
  once) as a versioned rule change with parity tests.

## Register walk-through, batch 15: D1.10, D1.11, D3.2, D3.4 (2026-10-30, O-S4)

- **D1.10:** the near-duplicate title check runs inline for small corpora and as a
  `knowledge_operation` job above a size limit (proposed 2,000 keys).
- **D1.11:** a Tiled scan that reaches the end of its listing stays "complete" with the
  live-read warning.
- **D3.2 / PC-2:** keep the per-response 40% rule only; no cumulative per-session budget
  (O-D2 stands).
- **D3.4 / PC-5:** the owner runs `python -m evals.binary.approve --gate H3 ...` and commits
  `evals/binary/approvals.json` and `evals/binary/cases/` personally.

## Register walk-through, batch 16: D2.1-D2.4 (2026-10-30, O-O6 part 1; O06 tag rules)

- **D2.1:** owner's words: "it should be published when PRs are merged into main". Images
  are published on push to `main` (PR merge), not by manual dispatch only. Which channels
  that covers (`:local`/`:als-dev` only, or `:als-prod` too) is confirmed in batch 17.
- **D2.2:** "You alone, self-approval allowed": the owner is the sole reviewer of both
  publish environments and may approve their own runs.
- **D2.3:** "Any tested digest" may be promoted to `:als-prod`; running on `:als-dev` first
  is not required.
- **D2.4:** `ghcr.io/fair2wise/fair2wise`, **public**. Pending the batch-17 confirmation
  that, combined with D3.1 (terminology snippets shipped in the image), the ~141k paper
  snippets become publicly downloadable.

## Register walk-through, batch 17: D2.1 channels, D2.4 confirmation, D2.5, D2.6 (2026-10-30, O-O6 part 2)

- **D2.1 (confirmed):** "All three on merge to main": a PR merge into `main` publishes
  `:local`, `:als-dev` and `:als-prod` to the new tested digest. No manual promotion step
  for production. The publish workflow's manual-dispatch-only design and the dev-before-prod
  rule are replaced accordingly (T24D follow-up); the O06 `O06_APPROVED` switch is set when
  the workflow is updated.
- **D2.4 (confirmed):** public registry accepted knowing the images carry the tracked KG
  JSON and the terminology snippets (D3.1); the owner considers the snippets short quotes
  already accepted under O-D1.
- **D2.5:** full 40-character commit in tags (`sha-<40 hex>-<variant>`, plus
  `vX.Y.Z-<variant>` on release tags).
- **D2.6:** stop publishing the legacy `agent` image; `compose.splash.yaml` may still build
  it locally.
