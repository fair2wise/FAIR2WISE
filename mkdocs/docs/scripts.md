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

## Other entry points

| File | Use |
|---|---|
| `scripts/run.py` | Local modular term extraction |
| `app/run_pipeline_cborg.py` | Incremental 25/50/75/100-paper evaluation |

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
| `install_pixi.sh` | Install Pixi if absent and initialize Splash |
| `test_compose.sh` | Isolated Splash-stack smoke test (`compose.splash.yaml`) |
| `wipe_splash_db.sh` | Guarded deletion of the local Splash SQLite database |

## Data acquisition and graph scripts

| Script | Behavior |
|---|---|
| `download_pdfs.py` | Search arXiv or OpenAlex and validate downloaded PDFs |
| `harvest_rsoxs.py` | Harvest **open-access** RSoXS PDFs into `papers/rsoxs/` (does not write the KG; never bulk-fetches publisher PDFs) |
| `paper_finder.py` | Paper Finder side tool: human review queue for skipped/failed paywalled rows (127.0.0.1 only) |
| `ingest_bl1101.py` | Replay BL 11.0.1.2 ops KG into `storage/kg/matkg_bl1101_vN.json` |
| `ingest_tiled_sim.py` | Seeded ESAF/Proposal/Scan/Sample overlay → next `matkg_bl1101_vN.json` (seed `20260916`; no live Tiled) |
| `promote_rsoxs_snapshot.py` | Copy live extract terms, json2kg the **copy** into `matkg_rsoxs_vN.json` |
| `index_source_rag.py` | Build literature PDF + bl1101 ops-doc indexes (`storage/source_index/`) |
| `build_kg.sh` | Extract terms and convert to graph using temp files/backups |
| `reimport_merged_kg.sh` | Merge two graphs, start Splash if needed, and reimport |
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
- `--allow-splash-wipe` is an explicit destructive capability; do not add it
  to new automation without a guarded workflow.
