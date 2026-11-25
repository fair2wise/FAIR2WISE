# Scripts and entry points

## Launcher modules

| Module | Use |
|---|---|
| `app.modules.launchers.f2w_agent` | Canonical orchestrated CLI/API entry point |
| `app.modules.launchers.academy_extractor` | Submit monitored extraction to Globus Compute |
| `app.modules.launchers.user_agent` | Launch the local Academy dashboard/user agent |
| `app.modules.launchers.academy_auth` | Authenticate to Academy through Globus |

Run launcher modules from the repository root:

```bash
python3 -m app.modules.launchers.f2w_agent status
```

## Console scripts (T17B)

`pip install fair2wise` installs these commands. Each is implemented in the packaged module shown (under
`app/modules/commands/` unless noted); the file of the same name in `scripts/` remains as a launch facade, so
`python scripts/<name>.py ...` in a checkout is unchanged. No command takes a key, token or password as an
argument: credentials come from the environment (`CBORG_API_KEY`, `F2W_TILED_*` files) or a file the environment
names.

| Command | Module | Use |
|---|---|---|
| `f2w-run` | `commands.run` | Local modular term extraction |
| `f2w-tiled-server` | `commands.tiled_server` | Managed Tiled lifecycle (`start`, `status`, `stop`, `check`, `upgrade-catalog`) |
| `f2w-harvest` | `commands.harvest_rsoxs` | Harvest open-access RSoXS PDFs |
| `f2w-paper-finder` | `commands.paper_finder` | Human-paced review queue for paywalled papers |
| `f2w-corpus-qa` | `commands.corpus_qa` | Extracted-terms QA audit (byte-pinned to the T19A baseline) |
| `f2w-repair-corpus` | `commands.repair_corpus` | Write a repaired copy of an extracted-terms file |
| `f2w-download-pdfs` | `literature.download_pdfs` | Search arXiv/OpenAlex and download PDFs |
| `f2w-kg-snapshot` | `commands.build_kg_snapshot` | Freeze a versioned JSON KG snapshot; `--reconcile-edits` freezes the next version from the live snapshot plus the Save journal, `--confirm-commit <40-hex>` records the commit that holds it on the reconciled edits |
| `f2w-kg-release` | `commands.prepare_kg_release` | Prepare, verify and export a KG release (never commits or pushes); `export` also leaves `<corpus>/<vN>.release_commit.json` on the mount so a publication receipt records `data_commit` |
| `f2w-kg-adopt` | `commands.adopt_legacy_kg` | `adopt` (flat `matkg_*.json` to `<corpus>/v<N>/` releases; `--dry-run`, `--only`, `--code-commit`) and `descriptor` (candidate data-release descriptor from Git objects); no Git write, no network, no Tiled |
| `f2w-library-backfill` | `commands.backfill_library_metadata` | Fill missing authors/abstracts (and with `--repair` junk titles, venue and year) in the library manifests from OpenAlex (DOI) and arXiv (id); dry run by default, `--apply`, `--limit`, `--delay`; see [Library tab](ui-library.md#backfill-authors-and-abstracts) |
| `f2w-doctor` | `commands.doctor` | Check CBORG (one keyed call), OpenAlex, arXiv, Ollama and the library/state directories; prints one `name: state` line per check, never a key, host or path; `--json`, `--timeout`, `--only`, `--skip`; exit 0/1/2; see [Standalone install](standalone-install.md#5-f2w-doctor-implemented) |

In the images the project is not pip-installed, so the same twelve commands are installed as launchers in
`/usr/local/bin` at build time (`docker/entrypoint/install_console_scripts.py`, one launcher per
`[project.scripts]` entry, running the image's Python with `/app` on the path); `docker exec f2w-local f2w-kg-adopt --help`
works in a running `:local` container. See [The `:local` image](local-image.md#console-scripts-in-the-image).

`harvest_rsoxs`, `harvest_xray` and `harvest_tomo` download PDFs through the hardened fetcher
(`literature.legacy_download.hardened_download_pdf`, the one `DownloadAgent` uses) and read Crossref and OpenAlex
metadata through `hardened_get_json`. Compared with the former private `requests`/`urllib` code: every URL and every
redirect hop must be http(s) on port 80/443 with no credentials and a public address (loopback, private,
link-local and CGNAT hosts are refused); redirects are limited to five; a PDF is capped at 100 MiB and 120 s and must
start with `%PDF-`; metadata answers are capped at 8 MiB; refused or non-PDF answers are not retried.

Politeness (owner decision 2026-11-09): every arXiv request made by these paths and by `download_pdfs`/`DownloadAgent`
is spaced at least 3 seconds apart per process (one shared monotonic limiter; a larger `delay_seconds`, such as the harvest's
15 s, is kept, a smaller one is raised to 3 s). A 429 or 503 from OpenAlex, arXiv or Unpaywall that carries `Retry-After`
is waited out once (capped at 60 s) and the identical request is repeated once; a second refusal, or a 429 without
`Retry-After`, fails as before. When `OPENALEX_API_KEY` is exported in the environment of the process, OpenAlex requests
carry it as `Authorization: Bearer <key>` (header only: never in a URL, a log line, a `repr` or a report). It is read from the
process environment only; a `.env` file is not used for it, and `scripts/run_local_container.sh` passes it into the
container only when already exported. `library/harvest.py` is separate and unchanged.

## Other entry points

| File | Use |
|---|---|
| `scripts/run.py` | Local modular term extraction |
| `app/run_pipeline_cborg.py` | Incremental 25/50/75/100-paper evaluation |

Operations CLI (`python -m app.api.cli --root DIR jobs|cases|audit|ops ...`, local profile only):
`ops list` names the operations, graph ids and corpus names; `ops summarize` summarizes one extracted paper;
see [Operations tab, CLI and SDK](ui-operations.md).

Agent launcher subcommands are `status`, `ask`, `chat`, and `api`. Global
options configure the model, graph, session, workflow, literature, and
extraction behavior.

## Local application scripts

| Script | Behavior |
|---|---|
| `start_all.sh` | Start Splash, agent API, and UI with readiness and cleanup |
| `start_rsoxs_stack.sh` | Daemonize RSoXS JSON agent+UI (5175/8090); `stop` does not kill extract |
| `start_agent_backend.sh` | Resolve `F2W_*` variables and run the packaged agent API launcher |
| `start_agent_frontend.sh` | Validate npm/Vite and run the Vite dev server (default port 5175) |
| `run_local_container.sh` | Start the `:local` image on `127.0.0.1:8080` with `CBORG_API_KEY` from a dotenv file and the papers folder mounted as the Library ([The `:local` image](local-image.md)) |
| `install_pixi.sh` | Install Pixi if absent and initialize Splash |
| `test_compose.sh` | Isolated Splash-stack smoke test (`compose.splash.yaml`) |

## Data acquisition and graph scripts

| Script | Behavior |
|---|---|
| `download_pdfs.py` | Search arXiv or OpenAlex and validate downloaded PDFs |
| `harvest_rsoxs.py` | Harvest **open-access** RSoXS PDFs into `papers/rsoxs/` (does not write the KG; never bulk-fetches publisher PDFs) |
| `harvest_xray.py` | Harvest OA X-ray *fundamentals* PDFs into `papers/xray/` (not RSoXS, not clinical CT) |
| `harvest_tomo.py` | Harvest OA **synchrotron X-ray tomography** PDFs into `papers/tomo/` (TXM, nano-CT, holotomography; not clinical CT, not `papers/xray/`) |
| `paper_finder.py` | Paper Finder side tool: human review queue for skipped/failed paywalled rows (127.0.0.1 only) |
| `ingest_bl1101.py` | Replay BL 11.0.1.2 ops KG into `storage/kg/matkg_bl1101_vN.json` |
| `ingest_tiled_sim.py` | Seeded ESAF/Proposal/Scan/Sample overlay → next `matkg_bl1101_vN.json` (seed `20260916`; no live Tiled) |
| `promote_rsoxs_snapshot.py` | Copy live extract terms, json2kg the **copy** into `matkg_rsoxs_vN.json` |
| `index_source_rag.py` | Build literature PDF + bl1101 ops-doc indexes (`storage/source_index/`) |
| `build_kg.sh` | Extract terms and convert to graph using temp files/backups |
| `get_pdf_years.py` | Infer PDF year from arXiv filename, metadata, then text |
| `analyze_kgs.py` | Compare graph growth and structural metrics across checkpoints |
| `test_chat_apis.py` | Manual CBORG/Ollama client smoke utility |

Example PDF download:

```bash
python3 scripts/download_pdfs.py \
  --keyword "grazing incidence x-ray scattering polymers" \
  --target papers \
  --max-results 10
```

`download_pdfs.py` rejects HTML/empty responses even when a URL claims to be a
PDF.

### Paper Finder (paywalled RSoXS, human-paced)

Lab VPN on. Do not burst publisher downloads. The publisher-facing request
happens because a **human clicked a link in a real browser tab** — the script
never GETs the PDF.

```bash
python3 scripts/paper_finder.py serve
python3 scripts/paper_finder.py serve --dry-run
python3 scripts/paper_finder.py reset
```

Binds `127.0.0.1:5057` only (never 5174 / 5175 / 8090). Queue source is
`papers/rsoxs/manifest.json` rows with `ingestion.status` in `{skipped, failed}`
and no PDF on disk. Save publisher PDFs into **`papers/staging/`** (created on
`serve` if missing; override with `--staging-dir` or `--downloads-dir`). Assign
moves the file to `papers/rsoxs/{year}/{stem}.pdf`. `missing_rsoxs_papers.txt`
stays the helpdesk ticket list. Keep OA harvest with
`python3 scripts/harvest_rsoxs.py --harvest-pending`.

Replay the BL 11.0.1.2 ops graph (appends `storage/kg/matkg_bl1101_vN.json`):

```bash
python3 scripts/ingest_bl1101.py --from-scratch
```

JSON-first Tiled Graph **simulation** (copies `matkg_bl1101_v4.json`, writes `v5`; seed `20260916`):

```bash
python3 scripts/ingest_tiled_sim.py --from-graph storage/kg/matkg_bl1101_v4.json --snapshot 5
python3 scripts/ingest_tiled_sim.py --to-tiled
```

`--to-tiled` writes the fixture identity graph (5 ESAFs, proposals, samples,
BlueskyRuns) into local Tiled GraphQL (`TILED_URI`, default
`http://127.0.0.1:8001`). Needs `TILED_API_KEY`. Re-runs skip existing uri/name
matches and leave unrelated catalog runs in place.

Promote a **copy** of the live RSoXS extract into the next literature snapshot (does not write `extracted_terms_rsoxs_v1.json`):

```bash
python3 scripts/promote_rsoxs_snapshot.py
```

Build hybrid source-RAG indexes (gold/sample literature PDFs + ops docs). Full
PDF reindex is opt-in:

```bash
python3 scripts/index_source_rag.py
python3 scripts/index_source_rag.py --literature all
```

## Documentation/repository utility

`scripts/update_readme_tree.py` generates or replaces the README tree between
special markers. It can use the local `tree` command or generate linked GitHub
entries.

## NERSC scripts

| Script | Behavior |
|---|---|
| `deploy_nersc.sh` | Rsync code/PDFs, create env, restart endpoint, submit job |
| `nersc_remote_setup.sh` | Create/update remote venv and working directories |
| `run_nersc_3agent.sh` | Invoke `status`, `ask`, or `chat` remotely through SSH |
| `f2w_nersc_api.sh` | Start the agent API on NERSC |
| `f2w_nersc_smoke.sh` | Run one remote agent question |
| `write_nersc_env.sh` | Write selected local secrets to protected remote env file |
| `slurm_scripts/run_ollama.sh` | Launch Ollama under Slurm |

See [NERSC and remote extraction](nersc.md) for sequencing and security.

## Splash workspace scripts

| Script/task | Behavior |
|---|---|
| `scripts/import_kg.py` | Import MatKG JSON into the running service |
| `pixi run serve` | Production-style Uvicorn service |
| `pixi run serve-dev` | Reloading development service |
| `pixi run test` | Splash tests with coverage gate |
| `pixi run lint` / `fmt` | Ruff checks/format |
| `pixi run db` | Local database shell |
| `pixi run entities` / `links` / `embeddings` | Inspect database records |
| `pixi run migrate` | Apply Alembic migrations |
| `pixi run frontend-dev` / `frontend-build` | Standalone Splash frontend |

## Safety expectations

- Run scripts from the repository root unless their examples explicitly
  `cd` elsewhere.
- Inspect defaults before running data mutation or remote deployment.
- Keep `.env` local; deployment scripts exclude it.
- Prefer dry-run modes when available.
- Stop Splash before copying or wiping its SQLite file.
- `--allow-splash-wipe` and the wipe/reimport scripts were removed (T18E); a corpus
  is replaced through a reviewed publication plan only.
