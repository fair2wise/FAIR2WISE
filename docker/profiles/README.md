# FAIR2WISE images: profiles, supervisor and build/run commands (T24B)

Status: candidate for H4. Built and run only by the owner; nothing here has been pushed.

## Stage layout (`Dockerfile`)

| Stage | From | Content |
| --- | --- | --- |
| `frontend-build`, `docs-build`, `frontend` | node / python / nginx | T24C: Vite bundle for `/fair2wise`, MkDocs site, standalone gateway image (compose) |
| `tiled-server-build` | `${PYTHON_IMAGE}` | `/opt/tiled/venv`: `tiled[server]` from the `tiled-0.2.18-py3-none-any.whl` checked against `PINNED_TILED_WHEEL_SHA256`, constrained by `tiled-server/constraints.txt`, `pip check`, version assert |
| `runtime-base` | `${PYTHON_IMAGE}` | app lock (`requirements.txt`), allowlisted code/schemas/tracked KG JSON, UI + docs, gateway template, `docker/entrypoint/`, tini, nginx, uid 10001; no profile |
| `local` | `runtime-base` | + `/opt/tiled/venv`, `profiles/local.json`, `/var/lib/fair2wise/tiled` mount point |
| `als-dev` | `runtime-base` | + `profiles/als-dev.json` (`https://tiled-staging.als.lbl.gov`) |
| `als-prod` | `runtime-base` | + `profiles/als-prod.json` (`https://tiled.als.lbl.gov`) |
| `agent` | `runtime-base` | legacy compose API-only target (`scripts/start_agent_backend.sh`, root, no supervisor) |

Not in any layer: `papers/`, PDFs, parsed bodies, crops, `storage/source_index`, Tiled catalogs,
job/session databases, `.env` files, `runs/`, `logs/`, `.cache/`, tests, evals, docs sources.
Tracked `storage/kg` and `storage/terminology` JSON are included (owner O-D1, H0 E3).

## What each image runs

`ENTRYPOINT tini -- python /opt/fair2wise/entrypoint/f2w_supervisor.py --profile-file /etc/fair2wise/profile.json`

| Order | Service | `:local` | ALS | Bind | Health | Required |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | preflight (`preflight.py`, `nginx -t`) | yes | yes | - | exit 0 | yes (78) |
| 1 | `tiled` (`tiled_service.py` -> T20B lifecycle) | yes | **absent** | 127.0.0.1:8765 | `/healthz` | no: 3 restarts with backoff, never for exit 78 |
| 2 | `api` (`api_service.py`: T14 jobs API, `root_path=/fair2wise/api`) | yes | yes | 127.0.0.1:8090 | `/health` | yes |
| 3 | `worker` (`python -m app.modules.workers`) | yes | yes | none | alive 3 s | yes |
| 4 | `gateway` (nginx with `docker/nginx.conf`) | yes | yes | 0.0.0.0:80 | `/fair2wise/healthz` | yes |

Stop: SIGTERM to the container -> gateway (SIGQUIT, 15 s), worker (SIGTERM, 45 s; running jobs
return to `queued`), api (20 s), tiled (25 s; data kept), then SIGKILL per process group. Use
`--stop-timeout 120` (compose `stop_grace_period: 120s`).

Supervisor exit codes: 0 clean stop, 1 stop needed SIGKILL / unclean worker stop, 70 required
child died, 71 required child missed its startup health check, 78 profile/env/preflight refused.
Status: `/run/fair2wise/supervisor.json`.

## Fixed profile

`locked_env` may be unset or equal; anything else exits 78 before a process starts.
`forbidden_env` must be unset. `stripped_env` (`TILED_URI`, `TILED_API_KEY`, `F2W_LIVE_TILED`, ...)
is removed. `F2W_DOTENV=<file>` is loaded once by the supervisor (never overriding the env, and
then subject to the same locks); children always get `F2W_DOTENV=off`.

| | `:local` | `:als-dev` / `:als-prod` |
| --- | --- | --- |
| `F2W_DEPLOYMENT_PROFILE` | `local` | `als-dev` / `als-prod` |
| `F2W_AUTHORIZATION_MODE` | `local_trusted` | `tiled_authenticated` |
| `F2W_TILED_MODE` | `managed` | `external` |
| `F2W_TILED_URL` | forbidden | fixed central URL |
| `F2W_TILED_DATA_DIR` / `_PYTHON` / `_PORT` | fixed (`/var/lib/fair2wise/tiled`, `/opt/tiled/venv/bin/python`, `8765`) | forbidden |
| `F2W_LOCAL_BIND` | allowed (`network` warns) | forbidden |

## Mounts

| Container path | Images | Mode | Notes |
| --- | --- | --- | --- |
| `/var/lib/fair2wise/state` | all | rw, uid 10001 | sessions, `session/operations/{jobs,curation,audit}.sqlite3`, traces. Required |
| `/var/lib/fair2wise/tiled` | `:local` | rw, uid 10001 | catalog, writable storage, `secrets/writer.key`. Required |
| `/run/secrets/tiled_writer` | ALS | ro | set `F2W_TILED_WRITER_SECRET_FILE` |
| `/var/lib/fair2wise/source_index` | optional | ro | set `HYBRID_RAG_LITERATURE_INDEX` / `HYBRID_RAG_OPS_INDEX` |

Preflight refuses a required path that is missing, not writable, or not on a mount (named
volumes inherit uid 10001 from the image; bind mounts must be owned by 10001).

## Owner commands (not run by T24B)

Build (BuildKit):

```sh
docker build --target local    -t ghcr.io/fair2wise/fair2wise:local .
docker build --target als-dev  -t ghcr.io/fair2wise/fair2wise:als-dev .
docker build --target als-prod -t ghcr.io/fair2wise/fair2wise:als-prod .
```

Static checks on the built images:

```sh
for t in local als-dev als-prod; do
  docker run --rm ghcr.io/fair2wise/fair2wise:$t --check                       # resolved plan, starts nothing
  docker run --rm --entrypoint sh ghcr.io/fair2wise/fair2wise:$t -c \
    'find / -xdev \( -iname "*.pdf" -o -path "*source_index*" -o -name "tiled_catalog*" -o -name ".env" \
       -o -name "*.sqlite*" -o -path "/app/papers*" \) 2>/dev/null'               # expect no output
  docker run --rm --entrypoint id ghcr.io/fair2wise/fair2wise:$t                 # uid=10001
done
docker run --rm --entrypoint /opt/tiled/venv/bin/python ghcr.io/fair2wise/fair2wise:local -I -c \
  'import tiled; print(tiled.__version__)'                                        # 0.2.18
docker run --rm --entrypoint sh ghcr.io/fair2wise/fair2wise:als-dev -c 'test ! -e /opt/tiled'
docker run --rm -e F2W_DEPLOYMENT_PROFILE=local ghcr.io/fair2wise/fair2wise:als-prod; echo $?   # 78
docker run --rm ghcr.io/fair2wise/fair2wise:als-dev; echo $?                       # 78: state not mounted
```

Run `:local` (loopback only):

```sh
docker volume create f2w-state && docker volume create f2w-tiled
docker run -d --name f2w-local -p 127.0.0.1:8080:80 --stop-timeout 120 \
  -v f2w-state:/var/lib/fair2wise/state -v f2w-tiled:/var/lib/fair2wise/tiled \
  -e CBORG_API_KEY ghcr.io/fair2wise/fair2wise:local
curl -fsS http://127.0.0.1:8080/fair2wise/healthz
curl -fsS http://127.0.0.1:8080/fair2wise/api/health
docker exec f2w-local cat /run/fair2wise/supervisor.json
docker stop f2w-local; docker logs f2w-local | grep f2w-supervisor             # reverse-order stop, exit 0
docker start f2w-local                                                           # catalog and writer key persist
```

Run an ALS image (disposable host; central Tiled is contacted only by app features):

```sh
docker run -d --name f2w-als-dev -p 127.0.0.1:8081:80 --stop-timeout 120 \
  -v f2w-als-state:/var/lib/fair2wise/state \
  -v /path/to/writer.secret:/run/secrets/tiled_writer:ro -e F2W_TILED_WRITER_SECRET_FILE=/run/secrets/tiled_writer \
  ghcr.io/fair2wise/fair2wise:als-dev
```

Fully hash-pinned Tiled server lock (needs network; replaces the constraints install):

```sh
echo 'tiled[server]==0.2.18' > /tmp/tiled.in
uv pip compile /tmp/tiled.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes -c docker/profiles/tiled-server/constraints.txt \
  -o docker/profiles/tiled-server/requirements.lock
# then in tiled-server-build: pip install --require-hashes --no-deps -r requirements.lock
# (keep the wheel sha256 check; confirm the tiled hash equals PINNED_TILED_WHEEL_SHA256)
```

## Tests

`tests/images/` (no Docker): Dockerfile stage graph and content allowlist, simulated build
context with planted private files, profile/app-validator agreement, the supervisor with fake
children, preflight, the API wrapper, and (with `F2W_TILED_PYTHON`) a real supervised Tiled 0.2.18.
