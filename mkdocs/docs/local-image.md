# The `:local` image, end to end

This page is the operator walk-through for the `:local` image on one machine:
start it, add papers, extract, export a KG, publish it into the container's own
Tiled, approve the plan, read the result and roll back. Build, check and
generic `docker run` commands, mounts and the supervisor are in
`docker/profiles/README.md`; this page does not repeat them.

The whole flow is the one the owner rehearsed on 2026-11-06 (see
[What the rehearsal found](#what-the-rehearsal-found)). Steps 1 to 4 run in the
browser; the publication steps use `docker exec` and `curl` because there is no
one-click Library-to-publication route (owner decision).

## 1. Start the container

```bash
scripts/run_local_container.sh [image] [dotenv-file]
```

`image` defaults to `f2w:local` (build the `local` target and tag it that way, or
pass your own tag); `dotenv-file` defaults to the repository `.env`. Only the
`CBORG_API_KEY` line is read from that file. It is passed to `docker run` by
name (never in argv or printed), but it is stored in the container
configuration, so `docker inspect` shows it to anyone who can use your Docker.

| What | Value |
|---|---|
| Address | `http://127.0.0.1:8080/fair2wise/` (health: `/fair2wise/api/health`); published on `127.0.0.1` only |
| Volumes | `f2w-local-state` (`/var/lib/fair2wise/state`: jobs, sessions, approvals, publication state, exported KGs) and `f2w-local-tiled` (`/var/lib/fair2wise/tiled`: catalog and writer key) |
| Papers | `papers/` of the repository, mounted read-only at `/data/papers` and offered as the one folder root (`F2W_FOLDER_ROOTS`) |
| Library | the same `papers/` folder, mounted read-write at `/var/lib/fair2wise/library`: uploads and the Harvester write there and update each category's `manifest.json` |
| Restart | an existing container of the same name is removed and recreated; the volumes survive |

Environment, all optional:

| Variable | Default | Effect |
|---|---|---|
| `F2W_HOST_PORT` | `8080` | host port |
| `F2W_CONTAINER_NAME` | `f2w-local` | container name |
| `F2W_PAPERS_DIR` | repo `papers/` | the read-only folder root |
| `F2W_LIBRARY_HOST_DIR` | same as the papers folder | the read-write Library folder |
| `F2W_KG_RELEASE_DIR` | `/var/lib/fair2wise/state/kg_releases` | directory inside the container where **Export** writes `matkg_<name>.json` |
| `OPENALEX_API_KEY` | unset | passed through only when exported in your shell (never read from the dotenv file); without it the Harvester searches arXiv only |

The Library's export needs its release directory to exist and the app never
creates it; the script creates it with `docker exec ... mkdir -p` after start.
Without `F2W_KG_RELEASE_DIR` the app would use its own `storage/kg`, which is
read-only in the image.

**Model cost.** The Extract tab lists the provider's live model list and shows a
cost estimate you must confirm before it starts. The owner's rehearsal and
evaluation runs used LBL-hosted `lbl/...` models on the free tier
(`lbl/cborg-chat`, `lbl/cborg-deepthought`, `lbl/gpt-oss-120b`;
`docs/migration/reviews/H3/b4_c4_free_model_run.md`); pick one of those if you
do not want a per-token model.

## 2. Harvest, extract, export

1. **Library > Harvester**: topic, category, sources, max downloads
   ([Library tab and Harvester](ui-library.md)). Paywalled papers wait in a
   queue for you to drop the PDF.
2. **Extract**: choose papers, model and schema, *Estimate cost*, confirm,
   *Start extraction* ([Extraction tab](ui-extraction.md)).
3. When the job succeeds, **Export** writes `matkg_<name>.json` into
   `F2W_KG_RELEASE_DIR`. The route needs `{"confirm": true}`, never overwrites
   a file that is not the bundle's own earlier export, and never touches the
   bundle (`app/modules/library/bundles.py`, `export_bundle`).

## 3. Adopt the flat file as a release

A publication takes a frozen release `<corpus>/<vN>` under `<workdir>/storage_kg`
(`/var/lib/fair2wise/state/session/storage_kg` on the image). `f2w-kg-adopt`
turns a flat `matkg_<name>.json` into that layout. It checks the file (schema
validation must pass, the disclosure check must be commit-ready), writes
`<corpus>/<vN>/{graph.json,manifest.json,validation.json}` and never overwrites
an existing frozen directory. It does no Git write, no network and no Tiled
call. The corpus and version come from the file name: `matkg_e2e_demo.json`
becomes `e2e_demo/v1`; `matkg_bl1101_v4.json` becomes `bl1101/v4`.

After exporting, run inside the container (`f2w-kg-adopt` is installed as a
console script in the image, see [console scripts](#console-scripts-in-the-image)).
This is the sequence used in the 2026-11-06 rehearsal: copy the exported file
from the export folder into the release root, then adopt it.

```bash
docker exec f2w-local cp /var/lib/fair2wise/state/kg_releases/matkg_<name>.json \
  /var/lib/fair2wise/state/session/storage_kg/
docker exec f2w-local f2w-kg-adopt adopt \
  --storage-kg /var/lib/fair2wise/state/session/storage_kg \
  --code-commit <40-hex commit of the code you run> --only matkg_<name>.json --dry-run
# the JSON report lists "would_adopt" or the exact refusing rule; repeat without --dry-run
```

`--code-commit` is required in the image: the commit is normally read from
`.git`, and the image has none. Use the commit the image was built from.

An alternative layout (not exercised in the rehearsal) is to start the container
with `F2W_KG_RELEASE_DIR=/var/lib/fair2wise/state/session/storage_kg` so that
export and adoption share one folder and the copy step disappears.

`f2w-kg-adopt descriptor --commit <sha> --corpus <c> --snapshot v1 --repository
github.com/<org>/<repo>` prints a `state=candidate` data-release descriptor read
from Git objects at an existing commit. It needs a Git checkout (not the image)
and refuses an `--out` inside the repository.

## 4. Publish, approve, read back

The publication API is the same route set as in `docs/migration/api/README.md`
(sections 3.1 and 4). On `:local` exactly one target is allowed: the image's
own Tiled, `http://127.0.0.1:8765` (the `target` field below). Central and ALS
targets, and every ALS profile, stay refused. Writes need nothing beyond the
local-operator session; there is no sign-in.

```bash
API=http://127.0.0.1:8080/fair2wise/api
H=(-H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8080')

# 1. plan (nothing is written yet)
curl -fsS "${H[@]}" -X POST $API/v1/publications \
  -d '{"snapshot_ref":"e2e_demo/v1","target":"http://127.0.0.1:8765"}'     # -> job_id
# 2. read the plan you are about to approve; note plan_sha256
curl -fsS $API/v1/publications/<job_id>/plan
# 3. approve exactly that plan
curl -fsS "${H[@]}" -X POST $API/v1/publications/<job_id>/approval \
  -d '{"plan_sha256":"<64 hex from step 2>"}'
# 4. wait for ui_state "published", then read the pointer
curl -fsS $API/v1/jobs/<job_id>/view
curl -fsS "$API/v1/publications/pointer?target=http://127.0.0.1:8765"
```

- **`Origin` header.** The origin gate (`app/api/origin_gate.py`) refuses
  cross-site browser requests. A request with no `Origin` (plain curl) passes;
  one that carries `Origin` must name the request's own host or an allowed CORS
  origin, otherwise it is `403 cross_site_request`. The rehearsal commands sent
  `Origin: http://127.0.0.1:8080`, which is what a same-origin browser sends.
- **Plan hash.** The approval names the `plan_sha256` you read. If the target,
  schema, vocabulary or the corpus revision moved since, it is
  `409 stale_approval` and nothing is approved; fetch the plan again.
- **`:local` approvals never expire.** The approval is created by the server for
  the local operator; you never send an approval record.
- The job moves `awaiting_review` (`staged`) to `approved` to `published`.
  Poll `GET /v1/jobs/{id}/view` for `ui_state`.
- Read the result in the UI: **Knowledge graph > Source > Tiled**
  ([Graph source](ui-knowledge-graph.md#graph-source)).

## 5. Roll back

```bash
curl -fsS "${H[@]}" -X POST $API/v1/publications/rollback-replacement \
  -d '{"target":"http://127.0.0.1:8765","reason":"why"}'     # -> a new job; approve its plan as above
```

- One pointer exists per target, with history. A rollback is a toggle between
  the last two pointers (a second rollback is a roll-forward) and is itself a
  planned, approved job. Candidates, receipts and snapshots are never deleted.
- **A rollback moves the pointer; it never removes a corpus.** Removing a corpus
  is a separate plan (`corpus-retire`).
- The pointer-only `POST /v1/publications/rollback` answers
  `409 conflict` with reason `rollback_replacement_required` on this image;
  use `rollback-replacement`.
- Re-sending a rollback with the same reason returns the same job, so after a
  failed attempt submit it again with a different reason.

## What the rehearsal found

Source: the "E2E rehearsal record" in
`docs/migration/reviews/owner_decisions_2026-11-03.md` and
`docs/migration/reviews/H5/local_publication.md`.

- `e2e_demo/v1` was published (pointer v1), then `bl1101/v4` (pointer v2), each
  approved by the owner with curl.
- Rolling back to `e2e_demo/v1` the first time planned **zero operations**
  (publishing `bl1101` retired nothing of `e2e_demo`) and the job quarantined at
  `verify`: the importer returned the older receipt while the step looked up the
  new plan hash. Tiled was not changed. Fixed in `d25a32f`: a zero-operation
  rollback writes a thin `noop` receipt under its own plan hash that rests on the
  covering receipt. A second attempt with another reason succeeded: pointer v3
  was `e2e_demo/v1` and Tiled held the same entities and links before and after
  (691 entities, 1063 links). The rollback touched only `e2e_demo`; `bl1101`'s
  records and marker were untouched.
- `f2w-kg-adopt` did not exist in the container: the project is never
  pip-installed there. Fixed in `ee6271b` and `83bca15` (next section).
- Open owner question: whether a no-op rollback should still require approval.

## Console scripts in the image

The image installs every `[project.scripts]` entry of `pyproject.toml`
(`f2w-run`, `f2w-tiled-server`, `f2w-harvest`, `f2w-paper-finder`,
`f2w-corpus-qa`, `f2w-download-pdfs`, `f2w-repair-corpus`, `f2w-kg-snapshot`,
`f2w-kg-release`, `f2w-kg-adopt`) as a launcher in `/usr/local/bin`. The
generator is `docker/entrypoint/install_console_scripts.py`, run by the
Dockerfile; each launcher runs the image's Python in isolated mode and adds
`/app` to the path itself, because `-I` ignores `PYTHONPATH`. Check with
`docker exec f2w-local f2w-kg-adopt --help`. Tests: `tests/images/test_console_scripts.py`.

## Save on `:local`

Editing a node and pressing Save works on `:local` only after a first
publication:

- The image starts the API with `--kg-mode json` (`docker/profiles/local.json`),
  which is read-only: Save is off, and the Edit control is hidden. To edit,
  choose the editable source in **Settings > Knowledge graph source**
  (the legacy `splash_links` option; on the image it is backed by Tiled, not
  Splash).
- The Save target is resolved **per request** from the active publication
  pointer: the corpus the last publication activated. Nothing is bound at
  startup (`app/api/local_save.py`).
- Before the first publication, or when a check fails, `PATCH /graph/node/{id}`
  answers `501 unsupported_capability` ("JSON available, Tiled unavailable: ...")
  and `GET /v1/capabilities` reports `can_save: false`, so the UI hides Save.
  Nothing is written and Splash is never called.
- A node of another corpus is a 404; the UI's read-only graph sources never
  reach Save.
- Edits are journaled per corpus. A later publication of an edited corpus is
  refused with `CONFLICT` until the edit is reconciled or a reviewed resolution
  is attached (the import-time conflict block).

## Accepted merge proposals (route V81)

`POST /v1/curation/merges/apply` plans the accepted canonicalization proposals
of one corpus. Body: `{"corpus", "route": "apply_migration" | "apply_cleanup" |
"apply_campaign", "dry_run": true, "plan_sha256"}`. `dry_run` defaults to true
and writes nothing. A real apply must name the `plan_sha256` a dry run showed,
and the route's gate is checked first: **every gate is closed** (opening one is
an owner decision recorded at H3, made in `app/modules/canonicalization/gates.py`,
never by environment or request), so a real apply answers
`501 unsupported_capability` with reason `gate_closed` and records nothing.
On the `:local` image no governed vocabulary is wired, so even the dry run
answers `501 unsupported_capability` ("no governed vocabulary is configured").
Details: `docs/migration/reviews/H4/apply_adapters.md`.

## Where the KGs come from (E3) and Splash status

- **KGs ship in images as flat copies only.** The Dockerfile copies
  `storage/kg/` and `storage/terminology/`: flat `matkg_*.json` files plus
  nested manifests and validation reports ship; the nested
  `<corpus>/v<N>/graph.json` duplicates do not (`.dockerignore`, D3.24).
  Papers, `storage/source_index/` and secrets stay out. A baked file never
  selects data or authorizes a publication. The E3 errata were applied on
  2026-11-06.
- **Splash retirement is prepared, not done.** The inventory, compatibility
  window and deletion list are a proposal in
  `docs/migration/reviews/H8/splash_retirement.md`; the cutover and every
  deletion stay gated at H8 and `splash_links/` is still in the repository. The
  legacy editable mode and `compose.splash.yaml` still work.
