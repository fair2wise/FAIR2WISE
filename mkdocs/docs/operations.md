# Local and Docker operation

## Local launcher

```bash
./scripts/start_all.sh
```

Startup order matters: Splash Links must be ready before the agent API loads
the graph, and the API must be ready before the browser sends its settings
bootstrap.

Managed PID files are written under `.run/`. On startup, the launcher stops
stale managed processes, refuses occupied ports, and waits for HTTP readiness.
Its signal/exit trap stops all child processes and removes the PID files.

## Service diagnostics

```bash
curl -fsS http://127.0.0.1:8081/splash_links/health
curl -fsS http://127.0.0.1:8090/health | python3 -m json.tool
curl -fsS http://127.0.0.1:5173/ >/dev/null
```

Common failures:

| Symptom | Check |
|---|---|
| Pixi missing | Run `./scripts/install_pixi.sh` |
| UI says local Vite is missing | Run `cd ui && npm ci` |
| Backend warns Splash is unavailable | Start Splash and verify port 8081 |
| Port already in use | Stop the existing service or change its `F2W_*_PORT` |
| CBORG authentication error | Check `CBORG_API_KEY` without printing it |
| Semantic model crash/download issue | Use lexical retrieval or verify the Python/ML environment |
| Splash falls back to JSON | Inspect Splash logs, URI, database, and GraphQL health |

## Docker image

The root `Dockerfile` is a multi-stage full-stack image:

- Node.js 20 provides Node and npm;
- a pinned Pixi image provides the Pixi executable;
- Python 3.12 slim is the runtime;
- root Python, UI npm, and Splash Pixi dependencies use separate cacheable
  layers;
- the default command is `./scripts/start_all.sh`; and
- the health check verifies all three services.

Build:

```bash
docker build -t fair2wise .
```

Run with the included launcher:

```bash
./scripts/run_docker.sh
```

The script:

- verifies Docker and the daemon;
- requires an environment file;
- verifies that the image exists;
- creates persistent Splash and run directories;
- starts a new container or restarts an existing stopped container; and
- prints all service URLs.

Override launcher values with:

| Variable | Default |
|---|---|
| `F2W_DOCKER_IMAGE` | `fair2wise` |
| `F2W_DOCKER_CONTAINER` | `fair2wise` |
| `F2W_DOCKER_ENV_FILE` | repository `.env` |
| `F2W_DOCKER_UI_PORT` | `5173` |
| `F2W_DOCKER_AGENT_PORT` | `8090` |
| `F2W_DOCKER_SPLASH_PORT` | `8081` |
| `F2W_DOCKER_DATA_DIR` | `.docker-data/splash-links` |
| `F2W_DOCKER_RUNS_DIR` | `runs` |

The container mounts:

- `storage/` for graph and terminology artifacts;
- `runs/` for session state; and
- `.docker-data/splash-links/` for the SQLite database.

The launcher overrides `SPLASH_LINKS_DB` to
`/app/data/splash-links/links.sqlite` so the database survives container
replacement.

View logs and stop:

```bash
docker logs -f fair2wise
docker stop fair2wise
docker rm fair2wise
```

## Splash database lifecycle

Back up the database only while Splash is stopped:

```bash
cp splash_links/links.sqlite splash_links/links.sqlite.backup
```

To intentionally erase the local database:

```bash
./scripts/wipe_splash_db.sh
```

The wipe tool is deliberately interactive and local-SQLite-only. PostgreSQL
administration is outside its scope.

## Rebuild/import workflow

For a new PDF set:

```bash
./scripts/build_kg.sh PDF_DIR TERMS_JSON KG_JSON
```

To inspect an import:

```bash
cd splash_links
pixi run python scripts/import_kg.py --dry-run ../storage/kg/example.json
```

To merge the current code-enriched graph with the larger corpus and reimport:

```bash
./scripts/reimport_merged_kg.sh
```

Read the script's environment defaults before using it against valuable data.

## Logs and mutable outputs

| Path | Contents |
|---|---|
| `.run/` | Managed service PID files |
| `runs/` | Session PDFs, manifests, terms, graph, memory, workflow |
| `logs/` | Extraction logs when configured |
| `storage/knowledge_gaps/` | Missing-node JSONL |
| `.docker-data/` | Container-persistent local data |
| `.cache/pystow/` | Repository-local PyStow cache |

These are runtime artifacts and should not be treated as source modules.
