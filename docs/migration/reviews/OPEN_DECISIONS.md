# Open owner decisions

State of `forge_merge` at aafffb0 (2026-10-16). Every item below is waiting on
the project owner. Each has the context, the options, and a recommendation.
Answers get recorded in the matching gate's `decision.json`.

Groups:
- **A:** blocks work now.
- **B:** needed before the H4 pilot, H5 publication or H6 release.
- **C:** technical defaults I will apply unless you object.

---

## A. Blocking work now

### A1. H3 B4: pilot fixture set and labeler
**Context.** The binary suite needs real reference data: gold extraction
records, gold questions with expected answers, and figures with known values.
The plan proposes about 12 representative Work families (a paper plus its
versions). A person must label them, and that person cannot be the agent that
implemented the code. Without this, T11B (running the approved cases) and T12
(the integrated pilot) cannot start.

**Options.**
- (a) Name the ~12 Work families and a labeler. They can mix RSoXS papers,
  BL11.0.1 papers and general materials papers that are already in the corpus.
- (b) I propose 12 families from the corpus (by topic and figure types). You
  approve the list and name only the labeler.

**Recommendation.** (b), to save you the selection work. The labeler still has
to be a person you name.

### A2. H3 B7: judge model for disputed semantic verdicts
**Context.** Some binary cases are semantic, for example whether the wording
of an answer is entailed by the quote. A judge model gives the first verdict.
Each disputed verdict goes to a human. The judge runs through the existing
F2W CBORG clients and only in separate validation runs, never inside the app
(your answer B).

**Options.**
- A strong CBORG model, such as the strongest Claude or GPT model CBORG
  offers.
- The model the app uses today, which is cheaper but judges its own family.

**Recommendation.** A strong model from a different family than the one being
evaluated, plus human review of every dispute.

### A3. H3: confirm "agree with most recommendations"
**Context.** You answered B, S4 and F5 directly. I read "agree with most" as
adopting the packet's recommendation for every other item:
- B1-B3, B5, B6, B8
- S1-S3, S5
- F1-F4, F6-F9
- C1-C5
- G1-G3
- K1-K7

Two of these matter more than the rest:
- C4: the real-model rerun of the merge measurement uses
  `paraphrase-MiniLM-L6-v2` and the B7 judge. This is a paid CBORG run.
- F2: cited and assumed values stay as candidates and are never published.

**Options.** Confirm, or name any items where you disagree.

### A4. Tiled graph editing (STOP-1 to STOP-6)
**Context.** In the frozen UI, editing a node or relationship and pressing
Save still goes through Splash. On a Tiled server without Splash, Save fails
safely with the old 502 error and writes nothing. This is T18C finding F1, and
it blocks H6. Tiled 0.2.18 has no in-place property update and no
transactions. So one Save turns into many separate writes, because every
link of the edited node must be recreated. These six questions come from
`docs/migration/ui_contracts/legacy_splash_to_tiled_adr.md` section 4.

| # | Question | Options | Recommendation |
|---|---|---|---|
| STOP-1 | **Link re-pointing.** An edited node gets a new revision, and all its incoming and outgoing links must be recreated. A crash halfway leaves the links half-moved. | (a) a multi-step move with a journal and a recovery job; (b) record edits in F2W's edit journal and project them into Tiled at the next JSON publication; (c) ask the Tiled team for property updates in 0.2.19 | **(b)**, and ask the Tiled team about (c) in parallel |
| STOP-2 | **Deleting links F2W does not own.** Removing a relationship or snippet whose link carries another corpus's or FORGE's access tags. Today's code deletes every matching link. | restrict deletion to F2W-tagged links and return the existing 400 for others; or keep deleting everything | **restrict to F2W links** (narrows today's behaviour) |
| STOP-3 | **Implicit snippet unlink.** A snippet left out of the saved list gets unlinked. A stale browser tab could remove snippets added in another session. | keep it, with a stale-revision check that refuses Saves made from an out-of-date copy; or require an explicit unlink (changes UI behaviour) | **keep it, with the stale check** |
| STOP-4 | **Wipe after extraction.** After each approved extraction the old flow wipes the whole graph and re-imports it, which is forbidden on a shared Tiled server. | replace it with a scoped publication of the new JSON version (Tiled may lag the chat answer for a short time) | **accept the scoped publication and the lag** |
| STOP-5 | **Partial failures.** A Save that reaches only part of Tiled. | report success only when the journal and the Tiled effect both completed; otherwise return the existing 502 and repair it later | **as stated** |
| STOP-6 | **Edit buttons for ALS non-admins.** The frozen UI still shows them. | accept a 403 with a message, with no UI change; or add a capability read to the UI later | **accept the 403** |

Once these are answered, the Tiled edit path becomes a new implementation
task. It touches `api.py` and the graph modules.

### A5. CI frozen-UI hash
**Context.** CI pins the frozen UI tree `75a19b98…`. T24C changed two lines in
`ui/src/app/App.tsx`: the router base and the logo path now come from the
build base, so the UI works under `/fair2wise/`. Without a prefix the UI
behaves exactly as before. The tree is now `2cc7ccd3…`, so the CI frozen-UI
check would fail. Nothing has been pushed, so it has not failed yet.

**Options.**
- (a) Approve the change and re-pin to `2cc7ccd3`. This is the same as
  applying a `ui-change-approved` label.
- (b) Revert the two lines and serve the prefix another way, for example
  `<base href>` injection at the gateway, which is more fragile.

**Recommendation.** (a).

---

## B. Needed before H4, H5 or H6

### Paper scope (T17, `reviews/H6/paper_scope_closure.md`)

| # | Decision | Context | Recommendation |
|---|---|---|---|
| B1 | **Port Docling** (L-12 parser, L-13 parser pool/worker) | PyMuPDF, which is AGPL, is still the only parser. At H0 you said Alex wants Docling and asked to keep PyMuPDF for now with deprecation markers. The T05 merge deferred Docling without your approval, and no task owns it. | Make it a task: Docling behind the existing pluggable parser interface, compared with PyMuPDF on the pilot set before switching |
| B2 | **Owner of licensing metadata** (L-18) | Each paper's licence or OA status is needed before anything is published at H5. Deferred in T05; no task owns it. | Add it to the Docling/parsing task, or as a small task before H5 |
| B3 | **Six proposed exceptions** | R-08: don't port the Langfuse tracer (F2W traces plus the audit log cover it). X-05: don't port `seed_synthetic_kg.py` (a FORGE viewer fixture). X-07: don't port `bench_kg.py` (dev-only). RT-20 `add_annotation`: stays in FORGE. A-01 `openalex_supplement.json`: retire it once citation links are regenerated (L-17). RT-47 `table_csv`: internal to the agent only. | Approve all six |
| B4 | **Folder browse/index routes** (RT-62 to RT-68) | FORGE has routes that list and index server folders. On ALS, any route that browses folders needs a fixed allowlist of roots, or it can expose the filesystem. | Allow them on `:local` only, with roots fixed in configuration; disabled on ALS |
| B5 | **Unowned capabilities** | Corpus index (J-07); enrichment, topic-sweep and PDF-parse job kinds (J-11, RT-27 to RT-32); a real OCR engine (V-05); paper subgraph and graph growth/recent views (K-10, RT-59, RT-07, RT-08); O-10 and O-12 to O-14; reference parsing, section projection, paper context and citation links (L-14 to L-17); HTTP routes for cost control, bulk review, vocabulary quality and figure-scale records. | Graph views and O-items go to T19C (after H3). Job kinds go to a T10 follow-up. L-14 to L-17 go with B1. OCR: decide whether it is needed for this release |

### Security and disclosure

| # | Decision | Context | Recommendation |
|---|---|---|---|
| B6 | **Model API chat routes** (T18C F3) | The two chat routes of the six-endpoint model API (Ollama/OpenAI compatible) skip the D19 quote limit. A model that echoes its prompt returns more than 40% of a page verbatim. `run_fastapi` also binds 0.0.0.0 with no auth. | Apply the disclosure projection to both routes and bind to loopback by default |
| B7 | **Model API under the prefix** | `/fair2wise/model-api` returns 404, so the model API is not exposed on ALS. | Keep it unexposed on ALS; available on `:local` |
| B8 | **Origin check on the 19 legacy routes** | The new `/v1` routes and sign-in refuse cross-site browser requests. The 19 frozen UI routes do not. | Add the check. Same-origin UI calls are unaffected |
| B9 | **Size limit on legacy `/graph/upload`** | Unlimited today, as in the baseline. nginx caps uploads at 128 MB. | Add a cap that matches nginx (128 MB) |
| B10 | **Reviewer excerpt length** | A curation case shows reviewers a 600-character quote from the source (D19). | Accept 600 characters |
| B11 | **CSRF token for sign-out** | Sign-in and sign-out refuse cross-site requests, and the cookie is `SameSite=Strict`. A session-bound token is deferred because the frozen UI has no sign-in page to send one. | Accept the current protection until a sign-in page exists |
| B12 | **In-memory ALS sessions** | A restart signs everyone out. | Accept until T24E (persistent store with the deployment) |
| B13 | **Tiled 0.2.18 revocation window** | After a revocation or role downgrade, a Tiled token held outside F2W keeps working until it expires (up to 15 min). F2W sign-out is immediate, and F2W re-checks roles at each protected step (max age 60 s). | Accept for the interim pin; revisit with 0.2.19 |
| B14 | **Approval expiry (TTL)** | The baseline never expires pending approvals, and the UI relies on them surviving reloads. Your H2 Q8 decision keeps them open across new messages. The access contract proposes a short TTL for ALS approvals. *Correction (T28D, AUD-10):* the code applies a 1 h TTL on every profile (`DEFAULT_APPROVAL_TTL = timedelta(hours=1)`, `app/api/operations.py:58`), not a 24 h placeholder; see D3.8. | `:local`: no expiry, as today. ALS: 24 h |

### Tiled (`docs/adr/evidence/tiled/capabilities_0.2.18.md`)

| # | Decision | Context | Recommendation |
|---|---|---|---|
| B15 | **Anonymous listing on managed `:local` Tiled** | A single-user Tiled lists graph records to anonymous callers. It binds only to loopback inside the container. | Loopback binding is enough on `:local`; add an access policy only if `:local` is ever exposed |
| B16 | **`executed_as` on `:local`** | Single-user Tiled has no principal, so the audit can only record `single-user-api-key` as the writer. `approved_by` is still the local operator. | Accept for `:local` audit |
| B17 | **SQLAlchemy in the Tiled lock** | The 0.2.18 lock resolved SQLAlchemy 2.1.4, outside the rc2 bound (`<2.1`). All tests pass on 2.1.4. | Pin 2.0.x to match upstream's bound, unless 0.2.19 lifts it |

### Deployment (T24B and T24C)

| # | Decision | Context | Recommendation |
|---|---|---|---|
| B18 | **Run `nginx -t` in a network-less container** | The gateway config has been checked only by a Python model of nginx. `docker run --rm --network none nginx:1.27-alpine-slim nginx -t` uses an image already on this machine; nothing is built or pushed. | Approve |
| B19 | **Image builds** | No image has been built. Commands and post-build checks are in `docker/profiles/README.md`. A build alone confirms that nginx 1.22 accepts the config, that non-root nginx can bind port 80, and that the Tiled constraints resolve on Linux. | You run them, or authorize a local build (no push) |
| B20 | **Compose URL** | The `compose.yaml` stack now serves at `http://127.0.0.1:5175/fair2wise/`, and `/` redirects there. Dev paths (`vite dev`, `:8090`) are unchanged. | Accept |
| B21 | **Supervisor** | A small standard-library Python supervisor under tini, instead of s6-overlay. H1 left this open. | Accept (no extra dependency, tested) |
| B22 | **Managed Tiled failure on `:local`** | If the bundled Tiled fails, it restarts up to 3 times. After that it is marked failed and the container keeps serving JSON workflows instead of exiting. | Accept |
| B23 | **Unset writer secret on ALS** | An ALS image starts with no writer secret; graph writes then fail at the first write. A secret that is set but unreadable is refused at startup. | Accept (reader-only ALS deployments are valid) |
| B24 | **Hash-locked Tiled dependencies** | Only the tiled wheel itself is hash-checked. Its dependencies are pinned but not hashed, and the pins come from macOS. A full `--require-hashes` lock needs network access (`uv pip compile`). | Authorize one lock-generation run with network, or do it yourself |
| B25 | **`pip check` conflict** | The app environment fails `pip check` (huggingface-hub 1.33 needs click>=8.4.2; the lock has 8.3.3) because `requirements/semantic.in` is unpinned. | Pin `huggingface-hub` below 1.33 or raise click in the lock: a reviewed lock change |

### Other

| # | Decision | Context | Recommendation |
|---|---|---|---|
| B26 | **Snapshot rollback after an edit was carried forward** (T18C F4) | Re-importing a snapshot older than the one that absorbed a UI edit is not refused. The edit stays in the newer JSON but disappears from Tiled. | Require a reviewed conflict resolution for that rollback |
| B27 | **Re-plan after a stale approval** (T10) | When the plan changed before approval, resuming takes two steps: re-plan, then approve the new plan. | Keep it manual, so the approver sees the new plan |
| B28 | **Second rollback** (T10) | A second publication rollback undoes the first, because it steps back one pointer-history entry. | Accept, and document it |
| B29 | **Legacy DownloadAgent fetch path** | T05 added a hardened downloader: per-hop redirect checks, private-address refusal, size, signature and timeout limits. The legacy `DownloadAgent` used by the UI still fetches with its own code; T05 only records its results in the new store. | Switch DownloadAgent to the hardened downloader behind a parity test |
| B30 | **FORGE consumer (T15)** | FORGE importing F2W agents and services needs changes in the FORGE repo, coordinated with Alex. Nothing on the FORGE side has been touched. | Tell me when Alex is ready; until then I can build only the F2W-side package surface |

---

## C. Technical defaults (I will apply these unless you object)

No objection was raised, so these defaults are applied (branch `forge_merge-cdefaults`).

| # | Item | Default | Status |
|---|---|---|---|
| C1 | Importer maps a writer 401/403 from Tiled to `writer_unauthorized` (T25C/T14 Q1) | yes | applied, `a515b18` |
| C2 | `F2WClient` methods that still raise `NotImplementedError` forward to the shared `F2WOperations` service (T14 Q2) | yes | applied, `083be8a` |
| C3 | Scoped job-list query in the job store instead of filtering in the service (T14 Q8) | yes | applied, `be1c724` |
| C4 | T10 files `jobs/sqlite.py` and `curation/repair.py` stay where they are (the plan had `infra/` and `extraction/`) | keep | applied (no change) |
| C5 | The T18C browser suite lives in `tests/e2e/live_backend/full/`, not `ui/e2e/...`, so the frozen UI tree hash is unchanged | keep | applied (no change) |
| C6 | `run_api --jobs-api` gets `root_path`, and the gateway port becomes configurable (8080 per contract), so nginx no longer needs `setcap` | yes | applied, `dba5f3e` |
| C7 | `F2W_CORS_ORIGINS` is required on ALS images (no fallback to dev origins) | yes | applied, `139132f` |
| C8 | The ALS edge proxy must overwrite `X-Forwarded-Proto` (an Ansible/T24E requirement) | record for T24E | applied (recorded), `6619cbf` |

---

## D. Added since 2026-10-16

### D1. Since H3 (extraction, schema, labels, operations)

| # | Decision | Context | Recommendation |
|---|---|---|---|
| D1.1 | Approve schema set 0.3.0 (`reviews/H3/schema_diff_T22C.md`) | Until approved, builds and publishing refuse 0.3.0; the pilot cannot extract samples or runs | Approve |
| D1.2 | Class name `MeasurementRun` (and `RunCondition`) | `Measurement` already exists (123 nodes in the rsoxs graphs) and keeps its meaning | Accept |
| D1.3 | Reconcile bl1101's `Sample`/`Detector`/`Endstation` with the new classes later (bl1101 v3) | bl1101 stays on its own schema for now | Later, as a reviewed change |
| D1.4 | Slot for normalized values (e.g. in nm) in 0.3.0 | Today they stay in candidate records | Keep in candidates for this release |
| D1.5 | Derived-value tolerance | 1% today; paper A prints 34.3 nm against a computed 34.9 nm (1.7%) | 2% |
| D1.6 | A value both derived and in an attributing sentence | Tagged cited today | Keep cited |
| D1.7 | Multimodal labelling of figures | Labeller reads text only; picture-only readings are marked `needs_visual_check` | Yes, as an opt-in flag |
| D1.8 | LOCAL-02 counts as gold with one reviewed paper | It needs a real run, not labels | Accept |
| D1.9 | Content-hash step in Work counting (K1) | A renamed copy with no DOI, arXiv id or title counts separately | Add it, as a versioned rule change |
| D1.10 | Near-duplicate title check | About 4.5 min at 10k keys | Run it as a job above a size limit |
| D1.11 | Tiled scan that reaches the end of its listing | Reported complete, marked as a live read | Keep, with the warning |

### D2. O06 image tag rules (T24D, `reviews/H6/o06_tag_rules_proposal.md`)

Publishing fails closed until these are decided and `O06_APPROVED=true` is set.

| # | Decision | Proposal |
|---|---|---|
| D2.1 | Events per channel: do `:local`/`:als-dev` follow `forge_merge` pushes? | No: manual dispatch only |
| D2.2 | Reviewers for `ghcr-publish` and `ghcr-als-prod`; block self-review? | Owner plus one; block self-review on prod |
| D2.3 | Condition for promotion to `:als-prod` | Same digest already on `:als-dev` |
| D2.4 | Registry name, owner and package visibility | `ghcr.io/fair2wise/fair2wise`, private |
| D2.5 | Tag forms (full or short commit) | Full commit |
| D2.6 | Stop publishing the legacy `agent` image | Yes |
| D2.7 | Retention | Images 30 days, manifests 90 |
| D2.8 | Platforms | amd64 only until O08 |
| D2.9 | Pin base images by digest | Yes |
| D2.10 | Require a binary report reference in each release | Record it; never gating (H3 B) |

### D3. Audit 2026-10-29 (plan_addenda.md A5)

| # | Decision | Recommendation |
|---|---|---|
| D3.1 | PC-1: paper snippets in images (`storage/terminology/*.json`, gitignored, 321 MB, ~141k snippets) | Exclude from images; keep gitignored |
| D3.2 | PC-2: cumulative D19 budget per paper per session | Add a cap; number to be chosen |
| D3.3 | PC-3: make T28A (authority wiring) a hard precondition of H4 | Yes (T28A merged `12156d1`; the precondition is now met) |
| D3.4 | PC-5: owner records the H3 approval in the binary manifest (T28B) | Yes |
| D3.5 | PC-6: approval TTL (code: 1 h everywhere; B14 said 24 h placeholder) | None on `:local`, 24 h on ALS |
| D3.6 | Approve tasks T28A-T28D as proposed in A5 | Approve (T28A and T28B already merged as fixes; T28C/T28D pending) |
| D3.7 | T26A release-contract questions Q2-Q6 (never recorded; see `docs/migration/companion` T26A notes) | Carried into this register by T28D as D3.9 |

### D3 additions by T28D (2026-10-29, AUD-10 and A5 section 3 notes)

Recorded for the owner; nothing below is answered here.

| # | Decision | Context |
|---|---|---|
| D3.8 | **Approval TTL: code and register disagree** | `app/api/operations.py:58` sets `DEFAULT_APPROVAL_TTL = timedelta(hours=1)` and applies it on every profile, including `:local`. B14 described the code as a "24 h placeholder" (corrected above) and recommends no expiry on `:local` and 24 h on ALS; D3.5 repeats that recommendation. Until the owner answers, a `:local` publication approval becomes `STALE_APPROVAL` after one hour, which is not the baseline behaviour B14 promises to keep. Pending owner decision (OPEN_DECISIONS D3.5); the code is not changed by T28D. |
| D3.9 | **T26A release-contract questions Q2-Q6** | Copied verbatim from `docs/migration/data_releases/release_contract.md` section 10 (Q1 was resolved by O-D1). Each is pending owner decision (OPEN_DECISIONS D3.9). |
| D3.10 | **B25 lock change still pending** | `pip check` fails in the app environment (huggingface-hub vs click, `requirements/semantic.in` unpinned). B25 asks for a reviewed lock change (pin `huggingface-hub` below 1.33 or raise click in the lock). Not applied; T28C owns the requirements files. Pending owner decision (OPEN_DECISIONS B25). |
| D3.11 | **May the integrator run `docker build` / `docker run` locally for the T24E container tests?** | B19 authorized the owner to build or to approve a local build (no push). `:local` was built and run once on 2026-10-29 (A5 section 1). T24E's container-level tests (supervisor, preflight, gateway on a real image) need repeated local builds and runs on the integrator's machine. No image is pushed and no registry login happens. Pending owner decision (OPEN_DECISIONS D3.11). |
| D3.12 | **D1.5 derived-value tolerance is still 1% in code** | `app/modules/evidence/units.py:381` `RECIPROCAL_DERIVATION_REL_TOL = 0.01`; D1.5 recommends 2% (paper A, 1.7% mismatch) and `extraction_field_mapping.md` says "1 % tolerance". The constant and the doc change together once D1.5 is decided (AUD-17; owner T06B). Pending owner decision (OPEN_DECISIONS D1.5). |

**D3.9 verbatim text** (`release_contract.md` section 10, items 2-6):

> 2. Are the existing flat files adopted, archived, or left as unpromotable legacy?
> 3. Approval of the `.gitignore` negation and `-text` attributes for KG paths.
> 4. Whether T23B stops writing `metadata.graph_snapshot` in promoted payloads.
> 5. Errata to plan 4.14, GIT_KG_PROMOTION_SPEC 4 and JSON_KG_PUBLICATION_SPEC for E3.
> 6. Where descriptors live: the release system, or a later commit under `docs/migration/data_releases/releases/`.

---

## Already settled (for reference)

H0, H1, H2 and H3 are approved (H3 on 2026-10-16). H3 answers include B
(non-blocking validation), S4 (LBNL BSD-style licence everywhere), F2 (all
values captured and tagged), F5/F6 (Sample, MeasurementRun and Instrument
classes, additive; task T22C in `plan_addenda.md` A4), B4 (pilot papers A-C,
provisional CBORG-model labels) and B7/O-U2 (judge model picked in the
settings panel). The workflow_mode decision (admin-only), the role-check
max age (O-A2: 60 s, capped at 900 s) and per-user default graphs were settled
at H1/H2.
