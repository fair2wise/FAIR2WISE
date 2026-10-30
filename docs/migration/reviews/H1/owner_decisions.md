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
