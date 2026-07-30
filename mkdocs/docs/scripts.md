# Scripts and entry points

## Root entry points

| File | Use |
|---|---|
| `f2w_agent.py` | Canonical orchestrated CLI/API entry point |
| `run.py` | Local modular term extraction |
| `run_academy_extractor.py` | Submit monitored extraction to Globus Compute |
| `user_agent_launcher.py` | Launch the local Academy dashboard/user agent |
| `app/run_pipeline_cborg.py` | Incremental 25/50/75/100-paper evaluation |
| `auth.py` | Authentication helper retained at repository root |

`f2w_agent.py` subcommands are `status`, `ask`, `chat`, and `api`. Global
options configure the model, graph, session, workflow, literature, and
extraction behavior.

## Local application scripts

| Script | Behavior |
|---|---|
| `start_all.sh` | Start Splash, agent API, and UI with readiness and cleanup |
| `start_agent_backend.sh` | Resolve `F2W_*` variables and run `f2w_agent.py api` |
| `start_agent_frontend.sh` | Validate npm/Vite and run the Vite dev server |
| `install_pixi.sh` | Install Pixi if absent and initialize Splash |
| `run_docker.sh` | Start/restart the full-stack Docker container |
| `wipe_splash_db.sh` | Guarded deletion of the local Splash SQLite database |

## Data acquisition and graph scripts

| Script | Behavior |
|---|---|
| `download_pdfs.py` | Search arXiv or OpenAlex and validate downloaded PDFs |
| `build_kg.sh` | Extract terms and convert to graph using temp files/backups |
| `reimport_merged_kg.sh` | Merge two graphs, start Splash if needed, and reimport |
| `get_pdf_years.py` | Infer PDF year from arXiv filename, metadata, then text |
| `analyze_kgs.py` | Compare graph growth and structural metrics across checkpoints |
| `test_chat_apis.py` | Manual CBORG/Ollama client smoke utility |

Example PDF download:

```bash
python3 scripts/download_pdfs.py \
  --keyword "grazing incidence x-ray scattering polymers" \
  --target xray_papers \
  --max-results 10
```

`download_pdfs.py` rejects HTML/empty responses even when a URL claims to be a
PDF.

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
