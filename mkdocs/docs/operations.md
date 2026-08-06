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

## Docker Compose operation

`compose.yaml` is the canonical deployment. It builds separate frontend,
agent, and Splash images. A one-shot initializer copies the tracked
`splash_links/links.sqlite` seed into the persistent volume before Splash and
the agent start. Export `CBORG_API_KEY`, then use:

```bash
docker compose up -d
docker compose ps
docker compose logs -f
```

Images are local Docker artifacts. A fresh clone rebuilds them from the root
`Dockerfile` and `splash_links/Containerfile`; it does not require an image
copied from the machine where FAIR2WISE was developed. Use
`docker compose up -d --build` after pulling build, source, or dependency
changes. Compose reads an untracked root `.env` automatically when credentials
are not exported by the shell.

The frontend is the only published service at `127.0.0.1:5173`. It proxies
`/api` to the private agent container; the agent communicates with Splash on
the private default network.

Health checks from the host and inside the private services:

```bash
curl -fsS http://127.0.0.1:5173/healthz
curl -fsS http://127.0.0.1:5173/api/health
docker compose exec agent curl -fsS http://127.0.0.1:8090/health
docker compose exec splash python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8081/splash_links/health').read().decode())"
```

Use `docker compose logs agent`, `docker compose logs splash`, or
`docker compose logs frontend` to narrow diagnostics. Stop containers while
retaining data with `docker compose down`.

Compose uses these named volumes:

| Volume | Contents |
|---|---|
| `splash-data` | Writable Splash SQLite graph, initialized from the tracked seed |
| `agent-runs` | Session PDFs, graphs, memory, and workflow state |
| `agent-cache` | Model and PyStow caches |

To erase all Compose-managed state and force a clean seed copy on the next
start:

```bash
docker compose down --volumes
```

This is destructive and cannot be recovered unless the Docker volumes were
backed up first.

## Splash database lifecycle

The following file-oriented commands apply to local development. Compose owns
its database in a named volume; manage that lifecycle with Compose commands
above.

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
