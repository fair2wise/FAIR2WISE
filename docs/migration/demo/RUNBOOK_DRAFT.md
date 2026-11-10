# Demo-day runbook (DRAFT), December 10-11 2026

Status: draft v0 written with the tooling in `scripts/demo/`; nothing here has been run on the demo Mac yet.
Gaps that need the owner or the rehearsals are marked **OPEN**. Scope and timeline are in `DEMO_PLAN_2026-12.md`.

Audience: whoever runs the demo, without needing to know the code. Every command is copy-paste. All commands run
in Terminal, from the folder that holds `scripts/` (the "demo folder"), on the demo Mac.

Two rules:

1. **Only the owner approves publications.** `seed.sh` stages plans and prints the approval commands; nobody and
   nothing else approves. Approve in the browser (Pending Approvals page) or with the printed `curl`.
2. **Never run `docker volume prune`, `docker system prune --volumes` or delete a volume by hand.** The demo data
   lives in two Docker volumes (`f2w-local-state`, `f2w-local-tiled`); `scripts/demo/backup.sh` protects them.

## What the scripts are

| Script | Use it to |
|---|---|
| `scripts/demo/check_machine.sh [image.tar]` | read-only report of the machine; paste the output back to the team |
| `scripts/demo/install.sh image.tar --checksum image.tar.sha256` | load the image, create the volumes, start the container |
| `scripts/demo/seed.sh` | adopt the demo corpora, stage the publication plans, print the approval commands |
| `scripts/demo/smoke.sh` | PASS/WARN/FAIL table of the running demo (GET requests only) |
| `scripts/demo/keepawake.sh start` / `stop` | stop the Mac from sleeping during the demo |
| `scripts/demo/backup.sh` / `restore.sh <dir>` | copy the two volumes to tar files and back |
| `scripts/demo/reset.sh` | back up, wipe and restart: the clean known state |

The demo URL is `http://127.0.0.1:8080/fair2wise/` (only reachable from this Mac). Health: `http://127.0.0.1:8080/fair2wise/api/health`.
Each script has `--help`.

## T-1 day (Dec 9): final rehearsal and freeze

Do these in order. Stop at the first FAIL and see "If something breaks".

1. **Machine check.** `scripts/demo/check_machine.sh /path/to/image.tar` and read the checklist at the end.
   Needed: PASS for arm64, at least 40 GiB free disk, at least 8 GiB RAM for Docker, ports 8080 and 8765 free,
   Docker running. WARN lines to understand: CBORG (off LBLnet/VPN), sleep (use keepawake), clock.
2. **Docker has room.** Docker Desktop: Settings > Resources: memory 8 GiB or more, disk 60 GiB or more. Colima:
   `colima start --cpu 4 --memory 8 --disk 100`. Also `docker pull alpine:3` while online (the backup scripts use it; they
   fall back to the demo image if it is missing).
3. **Network.** Join the LBL VPN (or be on LBLnet) and rerun the network part of the check: CBORG must not say
   OFF-NETWORK. Test the exact network you will use on demo day (venue Wi-Fi vs. VPN).
4. **Install from a clean state.** `scripts/demo/install.sh image.tar --checksum image.tar.sha256 --env-file /path/to/.env`
   (the file only needs the `CBORG_API_KEY` line; nothing else in it is read). If the volumes already exist the script
   refuses; add `--fresh` only if you want to start over (it backs up first and asks you to type `delete`).
   **OPEN:** the image's code commit label; an image built before 2026-11-10 has none (see seed, below).
5. **Seed.** `scripts/demo/seed.sh` (add `--code-commit <40-hex>` if the image has no commit label, and
   `--file /path/to/matkg_e2e_demo.json` for the arXiv corpus: it is not baked into the image; **OPEN:** the owner or the
   team puts that flat file into the build so it ships). It prints two `curl` approval commands.
6. **Owner approves** both plans: in the browser, left sidebar, **Pending Approvals**, Approve, confirm; or paste the printed
   `curl` commands one at a time, in order. A `409 stale_approval` means the plan moved: run `seed.sh` again and use the new hash.
7. **Smoke test.** `scripts/demo/smoke.sh` must end with `0 FAIL`. WARN for models means CBORG is unreachable.
8. **Backup the good state.** `scripts/demo/backup.sh` (it stops the container for a minute, copies, restarts). Copy the printed
   backup folder to a second disk or a laptop. This is the demo's undo button (`restore.sh <folder>`).
9. **Mac settings.** Turn off automatic macOS updates and App Store restarts until after the demo; do not-disturb on; the
   screen lock and screensaver to "never" (or a long time); power adapter plugged in. Print this runbook and the checklist.
10. **Dry-run the four storylines** below once with a stopwatch; note any fallback you needed.

## T-1 hour

1. Plug in power. `scripts/demo/keepawake.sh start --hours 8` (a suspended Mac once broke a job: the worker stopped, the job
   lease expired and the job was declared lost; a sleeping Mac also drops the VPN).
2. Join the VPN/LBLnet. Quit heavy apps. Close the Docker Desktop dashboard.
3. Is it running? `docker ps` shows `f2w-local` as `healthy`. If the container is missing: `docker start f2w-local`.
4. `scripts/demo/smoke.sh`. Expect `0 FAIL`.
5. Open these in browser tabs: the UI `http://127.0.0.1:8080/fair2wise/`; the project docs if wanted.
6. Check the pre-extracted fallback is in place (storyline 1): the `e2e_demo` corpus is live (smoke row "pointer corpora").
7. Keep a Terminal open in the demo folder with `docker logs -f f2w-local` in a second window, for you only.

## The demo itself (four storylines)

Everything is on `http://127.0.0.1:8080/fair2wise/`. Use a free model only (`lbl/cborg-chat` is the default); the UI lists
only free models and the cost estimate must be confirmed before anything runs.

### 1. Harvest, extract, build a KG (about 8 minutes)

- **Library > Harvester**: topic, category, sources (arXiv; OpenAlex only when `OPENALEX_API_KEY` was exported before install),
  a small maximum (3 to 5 downloads). Needs internet; arXiv is polite-rate-limited, so keep it small.
- **Extract**: pick **one** paper, model `lbl/cborg-chat`, schema as agreed (**OPEN:** C1/B4 decisions on the approved extraction
  path), **Estimate cost**, confirm, **Start extraction**. It takes a few minutes: talk over the progress.
- Fallback: the corpus `e2e_demo` was extracted before the demo (268 things); open it in the KG Viewer instead.

### 2. Publish to Tiled and approve in the UI (about 5 minutes)

- If the demo is seeded, the two corpora are already published. To show the flow live, use `reset.sh` before the demo (not
  during), run `seed.sh` (it stages the plans), then in the demo: **Pending Approvals**, read the plan card (corpus, target
  `http://127.0.0.1:8765`, plan hash, creates and links), **Approve**, confirm, watch the job go staged, approved, published.
- Read it back: **Knowledge graph > Source > Tiled**. The same result by command: `curl -fsS "http://127.0.0.1:8080/fair2wise/api/v1/publications/pointer?target=http://127.0.0.1:8765"`.
- Extras that work (not selected for the demo): rollback and the Operations tab.

### 3. Explore and edit the graph (about 8 minutes)

- **Knowledge graph > KG Viewer**, **Source** selector: switch JSON vs Tiled (`tiled:e2e_demo`, `tiled:bl1101`).
- Search a node, open a neighbourhood (a note appears if the 500-node cap cuts it: **OPEN** owner decision on the cap).
- Click a node, **Edit**, change the description, **Save**. Save goes to the corpus shown, and only exists after the first publication
  (the capabilities row in the smoke test shows `Save available`).

### 4. QA chat (about 10 minutes; "a huge part of it")

- The chat on the main page, free CBORG model. Ask the demo questions. **OPEN:** the final question list and the retrieval from
  the Tiled-published graph are workstreams W2 and W4; put the agreed questions here and rehearse their answers.
- The model needs CBORG: LBLnet or VPN. If CBORG fails, see the recovery table (what to say, the fallback).

After the demo: `scripts/demo/keepawake.sh stop`.

## If something breaks

| What you see | Likely cause | Do this |
|---|---|---|
| Browser: connection refused or page does not load | the container is stopped, or Colima/Docker is not running | `docker ps -a`. If Docker says it cannot connect: Docker Desktop: open the app and wait for "running"; Colima: `colima status`, then `colima start`. Then `docker start f2w-local` and wait about 2 minutes (the health check). `scripts/demo/smoke.sh` confirms. The data is in the volumes and survives |
| `colima start` or `docker` says "No space left on device" | the Colima VM disk is full (it has happened) | `docker system df` shows what uses it. Do NOT prune volumes. With the owner's ok: `docker builder prune` (build cache) or remove old images (`docker image rm <old>`). To grow the VM disk (cannot shrink): `colima stop`, `colima start --disk 120`. Then `docker start f2w-local` and `smoke.sh`. If the data looks damaged: `scripts/demo/restore.sh <backup folder> --replace` |
| Extraction, QA or the model list fails; smoke says models WARN or the check said OFF-NETWORK; "403 ... IP address not recognized" | CBORG only answers from LBLnet or the LBL VPN, and the Mac is outside it | Say: "the language-model service is internal to the lab network; I am off it right now". Join the VPN (then wait a minute and retry). If it cannot be fixed: storyline 1, show the pre-extracted corpus `e2e_demo` instead of the live extraction; storylines 2 and 3 do not need CBORG and still work. Storyline 4 (QA) needs the model: **OPEN:** the offline fallback is part of W2 (what QA does without CBORG); until it is decided, show the KG Viewer answer graph and the recorded answers from the rehearsal |
| A job runs far longer than rehearsed (extraction or publication "running" for more than 5 minutes) | slow or hung model call; a worker that missed its heartbeat (sleep) | Look: `docker logs --tail 50 f2w-local` and `curl -fsS http://127.0.0.1:8080/fair2wise/api/v1/jobs` (read only). If the Mac slept, run `scripts/demo/keepawake.sh status`, start it, and cancel and re-run the job from the UI. For a stuck publication: do not delete anything; show it as staged and move on, or restore the backup. Do not restart the container mid-job unless you are ready to re-run it |
| `seed.sh` or a script says port 8080 is in use | another program or an old container holds 8080 | `lsof -nP -iTCP:8080 -sTCP:LISTEN` names it. Quit it, or run on another port: `export F2W_HOST_PORT=8081` before `install.sh`/`seed.sh`/`smoke.sh`, and use `http://127.0.0.1:8081/fair2wise/` |
| An approval answers `409 stale_approval` | the plan changed after you read it | run `scripts/demo/seed.sh` again; it prints the new hash. Nothing was approved by the failed call |
| Save is missing in the KG Viewer | nothing is published yet (Save appears after the first publication) | approve the staged plan (storyline 2), or `seed.sh --i-approve` only if the owner is the one running it |
| Everything is wrong and there is no time | | `scripts/demo/restore.sh <the T-1 backup> --replace` (type `restore`): back to the verified state in about 2 minutes |
| Start over completely | | `scripts/demo/reset.sh` (backs up first, you type `reset`), then `seed.sh`, owner approves, `smoke.sh` |

## After the demo

`scripts/demo/keepawake.sh stop`; `scripts/demo/backup.sh` if the state is worth keeping; write down what failed and how long
each storyline took (the rehearsal log in `DEMO_PLAN_2026-12.md` week 3-4).

## Open items this draft depends on

- The flat `matkg_e2e_demo.json` must ship in the image (or be supplied with `seed.sh --file`); it exists only in the rehearsal volume.
- The image needs the code commit (label `org.opencontainers.image.revision`); build with `--build-arg GIT_COMMIT=$(git rev-parse HEAD)`
  or pass `--code-commit` to `seed.sh`.
- QA fallback without CBORG, the demo question list and QA over the Tiled-published graph (W2, W4).
- The neighbourhood cap, extraction quality decisions (H4 B4, C1) and pill/layout polish (owner decisions listed in the plan).
