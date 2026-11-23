# FAIR2WISE images: profiles, supervisor and build/run commands (T24B)

Status: candidate for H4. Built and run only by the owner; nothing here has been pushed.

## Stage layout (`Dockerfile`)

| Stage | From | Content |
| --- | --- | --- |
| `pixi-app`, `frontend-build`, `docs-build`, `frontend` | pixi / node / nginx | T24C: `pixi-app` is pixi 0.84.2 (the version that wrote `pixi.lock`; build stages only); Vite bundle for `/fair2wise`, MkDocs site (pixi `docs` environment), standalone gateway image (compose) |
| `app-env-build` | `pixi-app` | pixi `default` environment at `/opt/fair2wise/pixi/.pixi/envs/default`, installed `--locked` from `pixi.lock`; fails if torch is a CUDA build |
| `tiled-server-build` | `pixi-app` | pixi `tiled-server` environment at `/opt/tiled/ws/.pixi/envs/tiled-server`: Tiled 0.2.18 (owner pin O-T1; wheel hash `PINNED_TILED_WHEEL_SHA256` recorded in `pixi.lock`), installed `--locked`, version assert |
| `runtime-base` | `${PYTHON_IMAGE}` | the `app-env-build` environment copied in (PATH puts it first), allowlisted code/schemas/tracked KG JSON, UI + docs, gateway template, `docker/entrypoint/`, tini, nginx, uid 10001; no profile |
| `local` | `runtime-base` | + the Tiled server environment, linked as `/opt/tiled/venv`, `profiles/local.json`, `/var/lib/fair2wise/tiled` mount point |
| `als-dev` | `runtime-base` | + `profiles/als-dev.json` (`https://tiled-staging.als.lbl.gov`) |
| `als-prod` | `runtime-base` | + `profiles/als-prod.json` (`https://tiled.als.lbl.gov`) |
| `cluster` | `runtime-base` | Kubernetes (restricted Pod Security): `:local`'s trust model and Tiled with `profiles/cluster.json`, optional nginx (`F2W_GATEWAY=off|on`), `/livez` and `/readyz`, declared writable mounts, `HOME` and caches on `/var/lib/fair2wise/cache`. Not one of the three published channels (`scripts/ci/image_checks.py` and `release_manifest.py` still enumerate only `local`, `als-dev`, `als-prod`). See `mkdocs/docs/cluster-image.md` |
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

The `api` service's Knowledge Operations (`/v1/operations/*`, T19E) read the profile's Tiled
graphs by default on every profile, `:local` included (owner decision D3.17, O-X4, 2026-10-30):
`create_api_app` builds `KnowledgeOperations.from_deployment`, a composite graph port over the
JSON graphs in `storage/kg` **and** the Tiled target of the profile. On `:local` the managed
`tiled` service is located through its manifest under `F2W_TILED_DATA_DIR` and read with the
writer key from `secrets/writer.key` (read per request, never held or logged); on ALS the
fixed `F2W_TILED_URL` is read with `F2W_TILED_WRITER_SECRET_FILE`. Reads only: no mutation is
reachable through the operations. Graph ids in Tiled are discovered from the `f2w_graph_id`
markers (cached 60 s; a JSON graph of the same id shadows the Tiled copy); a Tiled that is not
running or unreachable leaves the JSON graphs served and records one warning. Override with
`create_api_app(knowledge_operations=...)`.

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

The `cluster` target runs the same services; with `F2W_GATEWAY=off` (its default) the `gateway` row is absent and
the `api` service is the one listener on `F2W_GATEWAY_PORT` and serves the UI itself. `docker build --target cluster -t fair2wise:cluster .`
builds it; its tests are `tests/images/test_images_cluster.py`.

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
| `/var/lib/fair2wise/source_index` | optional | ro | set `HYBRID_RAG_LITERATURE_INDEX` / `HYBRID_RAG_OPS_INDEX`. `:local` (T36) sets `HYBRID_RAG_LITERATURE_INDEX=/var/lib/fair2wise/state/session/source_index/literature` on the state volume, written by the `library_index` job (corpus `library`); an operator override re-points it. Forbidden on ALS |

Preflight refuses a required path that is missing, not writable, or not on a mount (named
volumes inherit uid 10001 from the image; bind mounts must be owned by 10001).

## Owner commands (not run by T24B)

Build (BuildKit):

```sh
docker build --target local    -t ghcr.io/fair2wise/fair2wise:local .
docker build --target als-dev  -t ghcr.io/fair2wise/fair2wise:als-dev .
docker build --target als-prod -t ghcr.io/fair2wise/fair2wise:als-prod .
```

Published images come only from CI: a push to `main` (PR merge) or a `vX.Y.Z` tag runs
`.github/workflows/publish-image.yml`, which pushes the tested bytes as `sha-<full commit>-<variant>`
(and `vX.Y.Z-<variant>`) to the public `ghcr.io/fair2wise/fair2wise` and moves all three channel
tags; nobody pushes by hand (O06 tag rules, approved 2026-10-30,
`docs/migration/reviews/H6/o06_tag_rules_proposal.md`). The legacy `agent` target is never published.

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

Hash-locked installs (T28C / AUD-08). `pixi.toml` + `pixi.lock` at the repository root are
the single source of truth, and `pixi.lock` records the sha256 of every conda package and PyPI
artifact. Both environments that decide image content are installed with `pixi install --locked`,
which refuses anything the lock does not describe (this replaces `pip --require-hashes`):
`app-env-build` installs the `default` environment, which `runtime-base` copies in, and
`tiled-server-build` installs the isolated `tiled-server` environment (Tiled 0.2.18, whose wheel
hash `PINNED_TILED_WHEEL_SHA256` is checked against the lock). No pixi binary is in any final
image except the legacy `agent` target (`splash_links`, separate pixi 0.70.2). CI installs pixi
with `scripts/ci/install_pixi.sh` (pinned release, sha256-verified). The semantic stack
(faiss-cpu, sentence-transformers, CPU torch) is unpinned in `pixi.toml` by design and fixed by
the lock. `tests/packaging/test_pixi_lock.py` checks the lock. To change a dependency, edit
`pixi.toml`, run `pixi lock` with pixi 0.84.2, and commit `pixi.lock`; never hand-edit it.

Baked Hugging Face models: the image writes `refs/main` in the model cache so offline lookup by
bare model name resolves.

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

## PDF parsing in the images

PDFs are read with Docling (structure: headings, figures, tables with cells) and pypdfium2 (page text,
metadata, rendering); PyMuPDF (AGPL-3.0) is not in any image. Docling's layout and table-structure
model weights are not in the repository or in `pixi.lock`: the build fetches them with
`scripts/fetch_docling_models.py` at the commits pinned in that file into
`F2W_DOCLING_ARTIFACTS_PATH=/opt/fair2wise/docling` (about 0.5 GB), so with `HF_HUB_OFFLINE=1` a
running image never downloads them. CI fetches the same pinned weights for the suites that run real
conversions; those tests skip when the variable is unset. Bump a revision deliberately and rerun
`tests/literature/test_docling_parser.py` and `tests/evals_binary`. How the parser's output compares
with the old one: `docs/migration/reviews/H7/docling_parity.md`.

## Figure OCR in the images (T30)

The figure tools read text from the PDF text layer first; OCR runs only when a caller asks for it
(`use_ocr`) and the text layer is insufficient, and every OCR span is tagged `ocr` (the kernels never
believe a unit from it). The engine is Docling's built-in OCR (RapidOCR, `torch` backend, English; torch is
already in the environment, `onnxruntime` is not), in `app/modules/vision/docling_ocr.py`. Its checkpoints go
to `<F2W_DOCLING_ARTIFACTS_PATH>/RapidOcr` in the same build step as the parser weights
(`scripts/fetch_docling_models.py`, `--no-ocr` skips them), each file verified against the SHA256 in the
locked `rapidocr` release's registry; a running image never downloads them.

`F2W_FIGURE_OCR_ENGINE` selects the engine: `auto` (default) uses Docling's OCR when the three checkpoints
are on disk and otherwise behaves as before (`OCR engine` unavailable, retryable, figure reported as
`ocr_unavailable`); `docling` forces it (unavailable if the weights are absent); `rapidocr` forces the old
`rapidocr_onnxruntime` adapter; `none` disables OCR. Without Docling or the weights nothing is imported or
downloaded and results are unchanged. Tests: `tests/vision/test_docling_ocr.py` (fake converter always; the
real-weights test skips when `RapidOcr/` is absent).
