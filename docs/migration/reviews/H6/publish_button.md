# "Publish to Tiled" button in the Library KG tab (H6 review packet)

Status: implemented on branch `forge_merge-demo-publish` (not merged). **Evidence for review, not owner approval.**
Owner-gated items are in section 8.

Owner decision (2026-11-10, twenty-first set, `docs/migration/reviews/owner_decisions_2026-11-03.md`, verbatim):
"Yes: a 'Publish to Tiled' action in the Library KG tab (Recommended)" (export, adopt and stage the publication plan
from one button on `:local`; the plan then appears on Pending Approvals for the owner to approve; no new approval
path). It supersedes the "Not now" of the tenth set and removes the demo finding that staging had no UI button
(`docs/migration/demo/DEMO_CONTENT.md`).

## 1. Design (this section was written before the code)

### 1.1 What the button does, and what it never does

One button on a finished KG bundle runs, in order, three steps that exist today as separate manual actions:

1. **export**: `POST /v1/library/kgs/{name}/export {"confirm": true}` semantics, same code path
   (`KgLibrary.export`, `bundles.export_bundle`): writes `matkg_<name>.json` into `F2W_KG_RELEASE_DIR`, never
   overwrites a file that is not the bundle's own earlier export.
2. **adopt**: what `f2w-kg-adopt adopt --storage-kg <workdir>/storage_kg --only <file> --code-commit <40-hex>` does,
   called in process (`app.modules.graph.adoption.adopt_legacy_flat_file`). Same checks, same refusals: R1 paths, R2
   format and schema validation, R3 secrets, R4 local paths, R5 bearer URLs, R7 raw bodies (R6 informational), never
   an allowlist (no reviewed exception can be passed from here).
3. **stage**: `F2WOperations.submit_publication(ctx, "<corpus>/<vN>", <managed local Tiled>)`, the very call behind
   `POST /v1/publications` (V16). That creates the existing `publish` job, which plans and then waits in
   `awaiting_review` for the existing approval route (V18) on the Pending Approvals page.

It **never approves anything**. There is no new approval path, no `create_approval` call, no `submit_approval` call,
no auto-approval (the `:local` auto-approval of a zero-operation rollback stays confined to rollback jobs). The owner
approves on the Pending Approvals page with the existing V18 route bound to the shown `plan_sha256`, exactly as today.
"Legacy adoption auto-approves on `:local` only" (owner decision on `adopt_legacy`) concerns the one-time
`adopt_legacy` option of a *publication* and is not used here.

### 1.2 Route, capability, profile

One new route, **V83** `POST /v1/library/kgs/{name}/publish`, `:local` only (`on_als=False`: an ALS profile does not
mount it and answers 404, as for every other `/v1/library` route), entry capability `graph.publish` (the capability of
V16 and the other publish rows; on `:local` the operator holds it). Because step 1 exports, the handler also requires
`library.extract` (the V74 capability); the policy row lists `graph.publish` only. Mounted next to the other KG
routes in `app/api/routes_library_kgs.py`.

Body: `{"confirm": bool = false, "again": bool = false}`.

* `confirm: false` is a **preview**: writes nothing, creates no job, answers 200 with what would happen
  (`corpus`, expected `snapshot_id`, `snapshot_ref`, `target`, `release_file`, `previous` job if this exact bundle
  state was published before, and `problems`, the list of reasons the publish would be refused now). The UI
  dialog is built from it. The version shown is the next free one at that moment; the job re-resolves it when it
  adopts (section 1.4) and reports the actual one.
* `confirm: true` runs the same checks; the first problem is raised as a typed error (section 1.6); otherwise a
  `library_publish` job is submitted and the response names it.
* `again: true` adds a nonce to the job inputs: a deliberate re-publish of an unchanged bundle (job identity is
  idempotent on identical inputs, section 1.5).

`GET /v1/capabilities` gets the additive field **`can_publish`**: `profile is local`, the caller holds `graph.publish`
and `library.extract`, a publication is wired on this deployment and its gate allows the deployment's own Tiled target
(the same objects the route uses, so the two cannot drift). `can_approve` is unchanged. The UI shows the button only on
an explicit `true`; a 401/403/404/405 on the bundle list or the route hides it like the other new tabs.

### 1.3 Job, not a synchronous call

Export is quick but adoption validates a whole graph against LinkML and the publication plan needs the worker (the
importer reads the Tiled scope), so the route returns a job: kind **`library_publish`**, capability `graph.publish`,
registered with the other library kinds on `:local` only, in the API process (submission validation) and in the worker
(`workers.factories.library_service`, which now builds the publication first because the job needs it). It runs on the
existing worker and is followed with the existing job polling; stages (each is an ordinary durable job stage, reused
on resume, with its own `required_capability`):

| stage | does | result data |
|---|---|---|
| `export` | `KgLibrary.export(name)` (needs `library.extract`) | `file_name` |
| `adopt` | copies the export to `<workdir>/storage_kg/matkg_<corpus>_v<N>.json`, adopts it (section 1.4) | `corpus`, `snapshot_id`, `snapshot_ref`, `graph_sha256`, `reused` |
| `stage` | `submit_publication` to the managed local Tiled (needs `graph.publish`) | `publication_job_id` |
| `plan` | waits (bounded, default 120 s, cancellable) until the publication job has planned and is `awaiting_review` | `plan_sha256`, `plan_id`, counts, `publication_state` |

The `library_publish` job itself ends `succeeded`, not in `awaiting_review`: the review state belongs to the
`publish` job (the one Pending Approvals lists and V18 approves), and a second job parked in `awaiting_review` would
never be resumed by anyone. The "awaiting_review visible in the job" requirement is met by the job's view
(`GET /v1/jobs/{id}/view`, V07, additive `library_publish` block): it names the publication job and its live
`ui_state` (`staged` = awaiting your approval), `plan_sha256`, counts, corpus, snapshot and the page to open
("Pending Approvals"). If the worker is busy and the plan is not ready within the wait, the job still succeeds with
`plan_sha256: null`, and the view fills the plan in as soon as the publication job has planned.

### 1.4 Corpus naming and the version number

* **Corpus**: a bundle `x` is corpus `x` (lower-cased; characters outside `a-z0-9_-` cannot occur in a bundle name,
  which allows `A-Za-z0-9_-`, so the only change is case). A name whose corpus is not valid is refused
  (`invalid_input`). This is the T-pointerC / T-save-corpus rule; the CLI maps `matkg_x.json` the same way.
* **Version**: the **next free `v<N>`** of that corpus: one more than the highest `v<N>` directory under
  `<workdir>/storage_kg/<corpus>/` (and than any `matkg_<corpus>_v<N>.json` flat copy there), `v1` when none. Gaps are
  never reused and an existing adopted release is **never overwritten** (`adopt_legacy_flat_file` already refuses an
  existing directory; two racing publishes retry with the next number, at most 5 times).
* **Flat copy**: `adopt_legacy_flat_file` derives the identity from the flat file name and records the flat file in
  `compat_paths` (checked byte for byte whenever the snapshot is loaded). Each release therefore needs its own flat
  copy `matkg_<corpus>_v<N>.json` in `storage_kg` (a shared `matkg_x.json` would stop matching `v1` after a re-export).
  The release-directory export is only the source. A copy of an adoption that was refused is removed.
* **Resume safety**: the manifest records `created.run_id = <job id>`; if the adopt stage runs again for the same job
  after the release was written (a crash before the stage was recorded), it finds that release and reuses it instead of
  adopting a second version.
* A release adopted earlier by the CLI (`matkg_x.json` as `x/v1`) counts: the next publish of `x` is `v2`.

### 1.5 Idempotency

* Job identity is the existing rule (kind plus inputs). Inputs: `bundle`, `confirm`, `code_commit`, `target`, the sha256 of
  the bundle's `kg.json`, and the nonce when `again`. A double click, or a retry of a lost response, submits the same
  inputs and returns the same job (`reused: true` when it already ended).
* An unchanged bundle published and finished is not silently re-adopted: the same job comes back. A **changed** bundle
  (new `kg.json` hash) is a new job and adopts the next version. A deliberate re-publish of an unchanged bundle is `again`.
* `stage` is idempotent because `Publication.submit` is (job identity includes the pointer version).
* A plan still **pending for the same corpus** (a `publish` job in `awaiting_review`, `queued` or `running`) blocks a
  new publish with `plan_pending`; so does a different `library_publish` run in progress for that corpus
  (`publish_in_progress`). Approve or cancel the pending plan first.

### 1.6 Commit and error cases

The image has no `.git`, and the manifest field `created.code_commit` must be an existing full commit id. The commit comes
from the image: the demo tooling's `GIT_COMMIT` build ARG sets the label `org.opencontainers.image.revision`; a label is not
visible inside the container, so the process reads **`F2W_CODE_COMMIT`** (then `GIT_COMMIT`), which the image sets from that
ARG (one `ENV F2W_CODE_COMMIT=${GIT_COMMIT}` line beside the ARG; owner-gated, section 8) or an operator passes with
`docker run -e`. A value that is not 40 or 64 lower-case hex (including the ARG default `unknown`) is "no label". Nothing is
invented and no `.git` is read: the route and the job answer `no_commit_label`. The commit is resolved when the job is
submitted and stored in its inputs.

Typed errors (HTTP `code` plus `reason` in the body; the same strings prefix the job's failed-stage message, so the job view
can show them; none of them uses `denied`, which would invalidate dependent approvals):

| reason | code | when |
|---|---|---|
| `publication_unavailable` | `unsupported_capability` | no publication wired, or the gate does not allow the deployment's Tiled (`:local` without its managed Tiled) |
| `no_commit_label` | `unsupported_capability` | the image carries no valid commit label (section 1.6); says how to set it |
| `nothing_to_publish` | `invalid_input` | the bundle's KG has no nodes and no edges (also the adopter's `placeholder` rule) |
| `disclosure_refused` | `invalid_input` | adoption refused by R3, R4, R5, R6-as-error or R7 (any of them), naming each rule and its count, e.g. `R7 x2`; nothing adopted or staged; the export file stays where it was written |
| `adoption_refused` | `invalid_input` | another adoption refusal (R1, R2 format, R2 schema, R2 validation), naming the rule |
| `plan_pending` | `conflict` | a plan for this corpus already waits for approval (or is running) |
| `publish_in_progress` | `conflict` | another publish of this corpus is running |
| `publication_failed` | `conflict` | the publication job failed or was quarantined while planning |

An unknown bundle is `404 not_found`; a bad name `422`/`invalid_input`; ALS `404`; missing `confirm` on the real call is
the preview, never a write.

### 1.7 What the UI shows

In the Library KG surface (the Extraction tab: the continue picker and the finished run view carry Export today) a
**Publish to Tiled** button next to Export on a chosen or finished bundle, shown only when `can_publish === true`.

* Click: fetches the preview and opens a confirmation dialog stating what will happen: "Export `matkg_x.json`", "Adopt as
  `x/v2` (a frozen release in this container; version chosen automatically, never overwriting)", "Stage a publication plan to
  this container's Tiled `http://127.0.0.1:8765`. **The plan still needs your approval** on Pending Approvals; nothing is
  published until you approve." Preview problems are shown in the dialog and disable Confirm. When this exact KG state was
  published before, a checkbox "Publish a new version anyway" appears.
* Confirm: `POST ... {confirm: true}`; progress is the existing job polling (stage list and state from the job view).
* Success: "Plan staged: `x/v2`, hash `abcdef012345...`, N creates, M links. Waiting for your approval." with a **Open
  Pending Approvals** link. Nothing on this surface approves.
* Typed errors are rendered by reason: refused disclosure rule(s), image has no commit label (and how to set it), nothing to
  publish, a plan already pending for this corpus (with the link), and the generic message otherwise.

## 2. Files

Backend: `app/modules/library/publish.py` (publisher, stages, view), `app/modules/library/jobs.py` (kind and registration of
a custom pipeline), `app/api/routes_library_kgs.py` (route), `app/api/policies.py` (V83), `app/api/routes_capabilities.py`
(`can_publish`), `app/api/operations.py` (additive `library_publish` block of `job_view`), `app/api/main.py` and
`app/modules/workers/factories.py` (wiring). UI: see section 5. Docs: this file, `docs/migration/api/README.md`,
`mkdocs/docs/local-image.md`.

## 3. Behaviour (as tested)

Backend, `tests/api_v1/test_v1_library_publish.py` (real `create_api_app`, real replacement importer on the in-memory
Tiled double, real adopted releases, a real bundle fixture built from the `core_dict` snapshot graph):

* policy row V83 (`POST`, `graph.publish`, `on_als=False`), every route declared, ALS answers 404 for the route (preview
  and confirm) and `can_publish: false`; `can_publish` is true only where a publication reaches the deployment's Tiled;
* a preview writes nothing (no release file, no job) and names `demo/v1`; an unknown bundle is 404;
* the job ends `succeeded` after `export`, `adopt`, `stage`, `plan`; `matkg_Demo.json` is in the release directory, the
  frozen release is `demo/v1` (manifest `created.code_commit` = the label, `created.run_id` = the job id), with its flat
  copy `matkg_demo_v1.json`; the `publish` job sits in `awaiting_review`, `GET /v1/approvals/pending` lists it, its plan
  hash equals the one in the job view, **no approval exists** and nothing of the corpus is in Tiled;
* a double click returns the same job; with the plan pending a second publish is `409 plan_pending`;
* after the first plan is cancelled, an unchanged bundle returns the finished job, `again` previews and adopts `demo/v2`
  and `demo/v1` is byte-identical; a CLI-adopted `v1` and a `v3` make the next version `v4`;
* an R7 field (`full_text`) fails the `adopt` stage with `disclosure_refused` naming `R7`, leaves no release and no flat
  copy, creates no publication job, keeps the export;
* no commit label (also the value `unknown`) is `501 no_commit_label` before anything is written; an empty bundle is
  `nothing_to_publish`;
* the module contains no approval creation or submission call.

UI: `PublishToTiled.test.tsx` (hidden for an older server, `can_publish` false, 403 and 404, disabled without a bundle,
preview and dialog text, progress, success with the Pending Approvals link and no approval call, preview problems,
refused disclosure rule from a failed stage, typed `plan_pending`, new-version checkbox, hide on a 404 preview) and
`capabilitiesApi.test.ts`; Playwright `e2e/publish.spec.ts` in the mock-agent fixtures (confirm and link, refused rule,
no commit label, hidden without `can_publish`).

## 4. Try it on a running container

1. Make sure the image knows its commit: `-e F2W_CODE_COMMIT=$(git rev-parse HEAD)` (or the image sets it from `GIT_COMMIT`).
2. Extract or continue a KG in the Extract tab. Click **Publish to Tiled**, read the dialog, confirm.
3. Watch the four steps; the dialog ends with the plan hash and **Open Pending Approvals**.
4. On Pending Approvals approve the plan (existing approval route). Only then does anything reach Tiled.
5. The same without the UI: `curl -X POST .../v1/library/kgs/<name>/publish -d '{"confirm":false}'` previews;
   with `{"confirm":true}` it submits; `GET /v1/jobs/<job_id>/view` shows `library_publish`.

## 5. UI files

`ui/src/app/components/extraction/PublishToTiled.tsx` (button, dialog, progress, result), `publishApi.ts`,
`publishHooks.ts`, `capabilitiesApi.ts` (`can_publish`, `canPublish`), the Export row of `extraction/ExtractionIdleForm.tsx`
and the finished-run header (`ExtractionRunView.publishSlot`, filled by `ExtractionTab.tsx`). `App.tsx` is untouched.

## 6. What did not change

Frozen legacy routes and contract tests are untouched; no new approval path; the `:local` auto-approval stays confined to
zero-operation rollbacks; `adopt_legacy` is not used; `Dockerfile` and `scripts/demo/` are not edited; `adoption.py` and
the `f2w-kg-adopt` CLI are unchanged (the new code calls the same function).

## 7. Limits

* A refusal by the disclosure check surfaces from the job (the export is written first); a pre-check would need the export
  bytes.
* The job waits up to 120 s for the plan on its worker thread (the image runs two): with both busy the job still
  succeeds and the view shows the plan as soon as the publication job has planned.
* Two bundles whose names differ only in case share a corpus and become successive versions of it.

## 8. Owner-gated and follow-up items

* **Image commit variable.** `GIT_COMMIT` is a build ARG and a label; the process cannot read a label. One line beside the
  ARG in the `local` stage of the `Dockerfile` (`ENV F2W_CODE_COMMIT=${GIT_COMMIT}`) makes the button work without
  `docker run -e`. Not edited here because the demo-tooling branch edits those lines; add it when merging.
* Corpus naming for a name that ends in `_v<N>` (`x_v2` is corpus `x_v2`, version `v1`, not `x` `v2` as the CLI would map
  the file name) and "next free = highest + 1, gaps not reused": confirm.
* Wait budget of the `plan` stage and "the library job ends `succeeded`, the review state lives on the `publish` job".
