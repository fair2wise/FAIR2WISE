# FAIR2WISE images: profiles, supervisor and build/run commands (T24B)

Status: candidate for H4. Built and run only by the owner; nothing here has been pushed.

## Stage layout (`Dockerfile`)

| Stage | From | Content |
| --- | --- | --- |
| `frontend-build`, `docs-build`, `frontend` | node / python / nginx | T24C: Vite bundle for `/fair2wise`, MkDocs site, standalone gateway image (compose) |
| `tiled-server-build` | `${PYTHON_IMAGE}` | `/opt/tiled/venv`: `tiled[server]` from the `tiled-0.2.18-py3-none-any.whl` checked against `PINNED_TILED_WHEEL_SHA256`, installed `--require-hashes` from `tiled-server/constraints.lock.txt`, `pip check`, version assert |
| `runtime-base` | `${PYTHON_IMAGE}` | app lock (`requirements.txt`), allowlisted code/schemas/tracked KG JSON, UI + docs, gateway template, `docker/entrypoint/`, tini, nginx, uid 10001; no profile |
| `local` | `runtime-base` | + `/opt/tiled/venv`, `profiles/local.json`, `/var/lib/fair2wise/tiled` mount point |
| `als-dev` | `runtime-base` | + `profiles/als-dev.json` (`https://tiled-staging.als.lbl.gov`) |
| `als-prod` | `runtime-base` | + `profiles/als-prod.json` (`https://tiled.als.lbl.gov`) |
| `agent` | `runtime-base` | legacy compose API-only target (`scripts/start_agent_backend.sh`, root, no supervisor) plus Splash/pixi (`splash_links`, `pixi install --locked`) for `compose.splash.yaml`; Splash stays here until its approved retirement gate (T16B/H7 or H8) and is never in the three images |

Not in any layer: `papers/`, PDFs, parsed bodies, crops, `storage/source_index`, Tiled catalogs,
job/session databases, `.env` files, `runs/`, `logs/`, `.cache/`, tests, evals, docs sources.
Tracked `storage/kg` and `storage/terminology` JSON are included (owner O-D1, H0 E3).

## What each image runs

`ENTRYPOINT tini -- python /opt/fair2wise/entrypoint/f2w_supervisor.py --profile-file /etc/fair2wise/profile.json`

| Order | Service | `:local` | ALS | Bind | Health | Required |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | preflight (`preflight.py`, `nginx -t`) | yes | yes | - | exit 0 | yes (78) |
| 1 | `tiled` (`tiled_service.py` -> T20B lifecycle) | yes | **absent** | 127.0.0.1:8765 | `/healthz` | no: 3 restarts with backoff, never for exit 78 |
| 2 | `api` (`api_service.py`: T14 jobs API, `root_path=/fair2wise/api`; mounts the model-compatible API at `/model-api`, published as `/fair2wise/model-api`, owner B7) | yes | yes | 127.0.0.1:8090 | `/health` | yes |
| 3 | `worker` (`python -m app.modules.workers`) | yes | yes | none | alive 3 s | yes |
| 4 | `gateway` (nginx with `docker/nginx.conf`) | yes | yes | 0.0.0.0:8080 (`F2W_GATEWAY_PORT`) | `/fair2wise/healthz` | yes |

The gateway is the one container ingress. It listens on `F2W_GATEWAY_PORT` (profile default
`8080`, the T24A contract; overridable at run time, refused if it equals the API or Tiled port).
The port is unprivileged, so the uid-10001 nginx needs no `setcap`/`cap_net_bind_service`.

Model-compatible API (owner decision B7, O-A9, 2026-10-30): `/fair2wise/model-api/<path>` is
forwarded by the gateway as `/model-api/<path>` to the same `api` process, which mounts
`app/modules/kg_rag_api.py` there (`app/api/main.py`, `create_api_app(model_api=True)`). It is
not a separate service: ALS sessions are held in the API process (B12), so only that process
can honour the sign-in cookie. On `:local` every caller is the local operator; on ALS the six
routes need the same signed-in browser session as `/fair2wise/api` (401 `sign_in_required`
otherwise). Non-browser clients (Open WebUI, SDKs) have no token sign-in yet: D-T8, open.

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
| `F2W_CORS_ORIGINS` | optional (unset: the API's development localhost origins) | **required** (`cors_origins_required`): comma-separated `https://host[:port]` origins of the deployed UI; unset or malformed exits 78, never a fallback to the development origins |

## Edge proxy requirement (T24E)

The ALS edge proxy in front of the gateway (Ansible/Traefik, T24E) **must overwrite
`X-Forwarded-Proto`** with the scheme the browser used (`https` on ALS), replacing any value the
client sent. The gateway passes on only the literal values `http`/`https` it receives
(`docker/nginx.conf`, `$f2w_forwarded_proto`) and uvicorn believes it only from the loopback
gateway, so a client-supplied header that the edge lets through would reach the API's URL and
same-origin decisions. Recorded in `docs/migration/plan_addenda.md` ("Deployment notes for T24E",
OPEN_DECISIONS C8). The Ansible handoff under `deploy/` (role `fair2wise`, template
`edge-nginx.conf.j2`) implements it: `proxy_set_header X-Forwarded-Proto $scheme;`, `/fair2wise`
forwarded unchanged to `127.0.0.1:8080`, digest-only image, state + read-only writer-secret mounts,
`--stop-timeout 120`; `tests/deployment/` checks it statically (`deploy/README.md`).

## Mounts

| Container path | Images | Mode | Notes |
| --- | --- | --- | --- |
| `/var/lib/fair2wise/state` | all | rw, uid 10001 | sessions, `session/operations/{jobs,curation,audit}.sqlite3`, traces. Required |
| `/var/lib/fair2wise/tiled` | `:local` | rw, uid 10001 | catalog, writable storage, `secrets/writer.key`. Required |
| `/run/secrets/tiled_writer` | ALS | ro | set `F2W_TILED_WRITER_SECRET_FILE`; regular file, mode `0600`/`0400`, owned by uid 10001 (preflight refuses group/world-readable or foreign-owned files) |
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
docker run --rm ghcr.io/fair2wise/fair2wise:als-dev; echo $?                       # 78: state not mounted, no F2W_CORS_ORIGINS
```

Run `:local` (loopback only):

```sh
docker volume create f2w-state && docker volume create f2w-tiled
docker run -d --name f2w-local -p 127.0.0.1:8080:8080 --stop-timeout 120 \
  -v f2w-state:/var/lib/fair2wise/state -v f2w-tiled:/var/lib/fair2wise/tiled \
  -e CBORG_API_KEY ghcr.io/fair2wise/fair2wise:local
curl -fsS http://127.0.0.1:8080/fair2wise/healthz
curl -fsS http://127.0.0.1:8080/fair2wise/api/health
docker exec f2w-local cat /run/fair2wise/supervisor.json
docker stop f2w-local; docker logs f2w-local | grep f2w-supervisor             # reverse-order stop, exit 0
docker start f2w-local                                                           # catalog and writer key persist
```

IPv6 preference for CBORG (local dev only, opt-in). Docker on Colima has no IPv6 by default,
so a container reaches CBORG over the host's IPv4 address, which CBORG's allowlist refuses.
After enabling IPv6 for the VM and the Docker daemon (Colima: `network.address: true`, daemon
`"ipv6": true` with a `fd00::/80`-style `fixed-cidr-v6`), glibc still sorts the IPv4 destination
first because the container's only IPv6 address is a ULA (RFC 6724 label mismatch). `docker/gai.conf`
is glibc's default table with `fc00::/7` given label 1 so IPv6 wins; mount it read-only (the runtime
user is uid 10001 and cannot write `/etc`, so the image does not bake or copy it, and nothing
changes without the mount):

```sh
mkdir -p ~/.config/fair2wise && cp docker/gai.conf ~/.config/fair2wise/gai.conf
docker run -d --name f2w-local -p 127.0.0.1:8080:8080 --stop-timeout 120 \
  -v f2w-state:/var/lib/fair2wise/state -v f2w-tiled:/var/lib/fair2wise/tiled \
  -v ~/.config/fair2wise/gai.conf:/etc/gai.conf:ro \
  -e CBORG_API_KEY ghcr.io/fair2wise/fair2wise:local
docker exec f2w-local getent ahosts api.cborg.lbl.gov | head -1                  # an IPv6 address first
```

Run an ALS image (disposable host; central Tiled is contacted only by app features):

```sh
docker run -d --name f2w-als-dev -p 127.0.0.1:8081:8080 --stop-timeout 120 \
  -v f2w-als-state:/var/lib/fair2wise/state \
  -v /path/to/writer.secret:/run/secrets/tiled_writer:ro -e F2W_TILED_WRITER_SECRET_FILE=/run/secrets/tiled_writer \
  -e F2W_CORS_ORIGINS=https://<ui-host> \
  ghcr.io/fair2wise/fair2wise:als-dev
```

Hash-locked installs (T28C / AUD-08). Both pip installs that decide image content run with
`--require-hashes`: `runtime-base` from `requirements/runtime.lock.txt` (the pins of
`requirements/runtime.txt` plus every wheel/sdist sha256) and `tiled-server-build` from
`docker/profiles/tiled-server/constraints.lock.txt` (the pins of `constraints.txt`, plus
`tiled[server]==0.2.18` whose hashes include `PINNED_TILED_WHEEL_SHA256`). The plain version
lists stay for developers and CI, because `pip install -r requirements.txt -r requirements/dev.txt`
chains the unpinned `requirements/semantic.in`, and pip puts a whole install into hash-checking
mode once any line carries a hash. `tests/packaging/test_requirements_locks.py` keeps each pair
in step. Still unhashed: CPU torch (PyTorch index, not PyPI) and `semantic.in`. Regenerate
(PyPI network only; keep each file's header; bump versions in the plain list first):

```sh
uv pip compile requirements/runtime.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --strip-extras -c requirements/runtime.txt -o requirements/runtime.lock.txt
echo 'tiled[server]==0.2.18' > /tmp/tiled.in
uv pip compile /tmp/tiled.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --no-strip-extras -c docker/profiles/tiled-server/constraints.txt \
  -o docker/profiles/tiled-server/constraints.lock.txt
```

## Tests

`tests/images/` (no Docker): Dockerfile stage graph and content allowlist, simulated build
context with planted private files, profile/app-validator agreement, the supervisor with fake
children, preflight, the API wrapper, and (with `F2W_TILED_PYTHON`) a real supervised Tiled 0.2.18.


## Semantic retrieval in the images

Every image carries CPU-only torch, FAISS and sentence-transformers, plus the two
embedding models the code defaults to (`all-MiniLM-L6-v2` for the QA retrieval path,
`paraphrase-MiniLM-L6-v2` for the term-merge shortlist), baked at pinned revisions under
`HF_HOME=/opt/fair2wise/hf`. `HF_HUB_OFFLINE=1` stays set, so nothing is downloaded at
runtime. The profiles default to `KG_RAG_RETRIEVAL_BACKEND=lexical`; pass
`-e KG_RAG_RETRIEVAL_BACKEND=semantic` to use the embedding backend. A different
`KG_RAG_EMBED_MODEL` or `F2W_MERGE_EMBED_MODEL` is not in the image and will fail offline.
