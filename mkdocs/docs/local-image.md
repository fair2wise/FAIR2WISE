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
Without `F2W_KG_RELEASE_DIR` the app uses `<F2W_WORKDIR>/kg_releases` (on the
writable state volume) and, with no workdir either, its own `storage/kg`, which
is read-only in the image. The app creates the directory at start and checks that
it can write there; if not, it refuses to start with a message naming the
directory and `F2W_KG_RELEASE_DIR`. An export that still fails answers a typed
`unavailable` error with the same message instead of a 500.

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
`.git`, and the image has none. Use the commit the image was built from: it is the
label `org.opencontainers.image.revision` (`docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' f2w-local`;
CI sets it, a local build sets it with `--build-arg GIT_COMMIT=$(git rev-parse HEAD)`, and an image built without
it says `unknown`).

**One button instead (owner decision 2026-11-10):** the Extract tab's **Publish to Tiled** button does the export of section 2, this adoption and the staging of section 4 in one job
(`POST /v1/library/kgs/<name>/publish`), choosing the next free `<corpus>/v<N>` itself, and leaves the plan on **Pending Approvals** for you to approve; it approves nothing. It needs the
image's commit: set `F2W_CODE_COMMIT=<40-hex>` in the container (the image build's `GIT_COMMIT` argument should set it; without it the button answers `no_commit_label` and nothing is
invented). Design: `docs/migration/reviews/H6/publish_button.md`.

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
- With several corpora live a rollback must name the corpus (`"corpus": "<name>"`
  in the request); without it the server answers `409 conflict` with reason
  `corpus_required` and lists the live corpora. With one live corpus nothing changes.
- **A rollback moves the pointer; it never removes a corpus.** Removing a corpus
  is a separate plan (`corpus-retire`); when another corpus stays live, that
  removal moves the pointer (and so the Save target) to the most recently
  activated remaining corpus instead of clearing it.
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
`f2w-kg-release`, `f2w-kg-adopt`, `f2w-library-backfill`, `f2w-doctor`) as a launcher in `/usr/local/bin`. The
generator is `docker/entrypoint/install_console_scripts.py`, run by the
Dockerfile; each launcher runs the image's Python in isolated mode and adds
`/app` to the path itself, because `-I` ignores `PYTHONPATH`. Check with
`docker exec f2w-local f2w-kg-adopt --help`. Tests: `tests/images/test_console_scripts.py`.

## Save on `:local`

Editing a node and pressing Save works on `:local` only after a first
publication:

- The image starts the API in the **editable** KG mode (`--kg-mode splash` in
  `docker/profiles/local.json`; `splash` is only the legacy wire token of that
  mode, on the image it is backed by Tiled and the graph view reads JSON, and
  no Splash service is ever contacted). Nothing needs switching: Save is
  available as soon as a publication exists. Choosing the `json` source in
  **Settings > Knowledge graph source** is the read-only view (Save answers the
  legacy 400).
- Before a first publication the graph view shows the image's JSON graph, the
  Edit/Save controls are hidden and a PATCH is the typed 501 (below). After a
  publication the graph view shows that corpus's effective record (active
  snapshot plus in-flight edits).
- The Save target is resolved **per request** and edits **the corpus shown in
  the graph view**: with no graph source selected, the active publication
  pointer's corpus (the one the last publication activated, which the editable
  default shows); with a Tiled graph source selected (**Source** control of the
  KG viewer), that graph's corpus, provided it is live on this image. Nothing is
  bound at startup (`app/api/local_save.py`).
- Before the first publication, or when a check fails, `PATCH /graph/node/{id}`
  answers `501 unsupported_capability` ("JSON available, Tiled unavailable: ...")
  and `GET /v1/capabilities` reports `can_save: false`, so the UI hides Save.
  Nothing is written and Splash is never called.
- A node of another corpus is a 404. A Tiled graph source whose corpus is live
  is editable (the source listing says `read_only: false`); JSON sources and
  Tiled graphs that are not live stay read-only (`400 read_only_source`).
- Edits are journaled per corpus. A later publication of an edited corpus is
  refused with `CONFLICT` until the edit is reconciled or a reviewed resolution
  is attached (the import-time conflict block).

### JSON or Tiled Graph: what the choice means

**Settings > Knowledge graph source** offers **JSON** (the browser default) and
**Tiled Graph** (stored as `splash_links`, the editable mode). The browser
applies its own choice to the server on load and when the window regains focus
(see below), so the choice decides all of this:

| | JSON | Tiled Graph |
|---|---|---|
| Chat searches | the JSON graphs ticked in Settings (`storage/kg/*.json`), reloaded at the start of every turn | the live corpora published to Tiled (the editable session graph) |
| Graph viewer default | the first selected JSON graph | the active publication's corpus |
| Editing | read-only (Edit hidden, Save answers `400`) | Edit and Save once a first publication exists |
| Auto-publication after an extraction | unchanged: it follows the server's start-up `--kg-mode splash` (the image default), not the browser choice | the same |

Newly extracted knowledge and the JSON source (verified in
`tests/api_wiring/test_json_chat_graph_scope.py`, which runs the real chat
routes with the real graph-loading code and a fake extractor). An extraction
always rebuilds the session graph, and under JSON the chat does **not** search
it afterwards:

- During the extraction turn the answer is re-checked against the rebuilt
  session graph alone, so that one reply can use the new terms.
- The next chat turn reloads the selected JSON graphs again and the new terms
  are gone from the chat's search until they are part of one of the selected
  graphs (for example after **Export** and adopting the file, below).
- With Tiled Graph selected, the publication that follows the extraction puts
  them in the live corpus the chat searches.

How the browser applies its choice:

- The server keeps the graph source in process memory and returns to its
  start-up mode (`splash`, the editable mode) on every restart. The browser
  sends only `{"graph_source": ...}` (a partial `PUT /settings`, nothing else
  changes) when the server differs from its own choice: on load and when the tab
  becomes visible or focused again, at most once every 15 seconds. A restart is
  therefore repaired on the next focus or load. If `storage/kg` holds no JSON
  file the server refuses (`400`); the browser ignores that and keeps its choice.
- The chat request does not carry the source; it is the server's current one.
- Two tabs of one browser share one server setting and one stored choice per
  tab only while they agree. If they differ, the last one to apply wins; a tab
  that lost restores its own choice the next time it is focused. Another
  browser on the same `:local` image behaves the same way.
- On an ALS deployment (`GET /v1/capabilities` reports a profile other than
  `local`, or cannot be read) the browser applies its source only after the
  user picked it explicitly in Settings; the unchosen default never overrides a
  saved personal setting.

### Reconciling edits into a new version

A reconciled edit becomes part of the corpus's JSON, not just of Tiled. Two explicit steps, run in the container
(no network, no model, nothing written to Tiled or Git); the journal is the one Save writes,
`<ops-root>/publication-state/<target-slug>/<corpus-slug>/edits.jsonl`:

1. `f2w-kg-snapshot --corpus <corpus> --reconcile-edits --parent <live vN> --storage-kg <workdir>/storage_kg
   --state-dir <ops-root>/publication-state --target <tiled-url>` (or `--journal <file>`). It writes
   `<workdir>/storage_kg/<corpus>/v<N+1>/` (graph, manifest with relation `edit_reconciliation`, validation
   report) and the flat copy, prints the paths and sha256, and only then, after re-reading and hash-checking the
   files, marks the edits `reconciled`.
2. Commit the new version with the release tooling (`f2w-kg-release prepare`, `verify-committed`, then the printed
   git commands), then `f2w-kg-snapshot --corpus <corpus> --confirm-commit <40-hex> --snapshot-id v<N+1> --repo
   <repo> --storage-kg <workdir>/storage_kg --journal <file>` to record the commit on those edits.

Publish `<corpus>/v<N+1>` as usual; the edits are inside its lineage, so the conflict block passes. A mount made by
`f2w-kg-release export` carries `<vN>.release_commit.json`, so the receipt records the data commit.

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

## Demo scripts

For a demo on another Mac, `scripts/demo/` holds read-only `check_machine.sh`, `install.sh` (checksum, `docker load`,
volumes, start), `seed.sh` (adopts the demo corpora, stages the publication plans and prints the owner's approval
commands; it approves only with the owner's explicit `--i-approve`), `smoke.sh`, `reset.sh`, `backup.sh`, `restore.sh`
and `keepawake.sh`. The order of operations is in `docs/migration/demo/RUNBOOK_DRAFT.md`.

## CBORG from the container: IPv6

CBORG only accepts requests from addresses the owner has authorized (LBLnet, VPN, or an address authorized at
`https://api.cborg.lbl.gov/key/manage`). A developer's authorized address is usually the machine's **global IPv6**
address. The Colima VM and the Docker daemon are set up for IPv6 (`network.address: true`, daemon `"ipv6": true`
with `fixed-cidr-v6: fd00:f2::/64`), so a container has a private `fd00::` address and NAT66 out through the host.
glibc still sorts the IPv4 destination first for a private IPv6 source, and the container then reaches CBORG over
IPv4, which is not authorized: every call answers HTTP 403 "IP address ... not recognized" (model lists come back
empty, QA answers fail).

`scripts/run_local_container.sh` therefore mounts `docker/gai.conf` read-only at `/etc/gai.conf` (it gives private
IPv6 sources the same label as global destinations, so IPv6 wins). Nothing else changes; if the container has no
IPv6 it falls back to IPv4. `F2W_GAI_CONF=none` skips the mount. Check from inside a running container:
`docker exec f2w-local python -c "import socket;print(socket.getaddrinfo('api.cborg.lbl.gov',443)[0][0])"`
should print `AddressFamily.AF_INET6`. On LBLnet or the VPN IPv4 is also authorized and the mount is harmless.

To test authorization instead of guessing, run `docker exec f2w-local f2w-doctor --only cborg`: it makes one keyed model-list
call and prints `cborg: ok` or `cborg: ip_not_authorized` (a call without a key cannot tell, so no key prints `cborg: no_key`).
See [Standalone install](standalone-install.md#5-f2w-doctor-implemented).
