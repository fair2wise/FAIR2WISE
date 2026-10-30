# Source seams: Docker, workflow, gateway, browser and Tiled (T24A / T20A)

**[Observed]** These seams come from static reading of the clean baseline export `222b1e7`. Where `forge_merge` differs, both versions are shown. I did not build images or run CI. This list complements `docs/migration/companion/DEPLOYMENT_SOURCE_AUDIT.md`; it does not replace it.

Each row gives the location, what the source does now, and the change task that owns the fix. The "Required change" column is a proposal.

## 1. Docker build

| # | File:line | Current behavior | Required change (task) |
| --- | --- | --- | --- |
| D1 | `Dockerfile:3,15,28,31` | Base images use tags only: `node:20-bookworm-slim`, `nginx:1.27-alpine`, `ghcr.io/prefix-dev/pixi:0.70.2`, `python:3.12-slim`. No digests. | Pin by reviewed digest (T24B/T24D). |
| D2 | `Dockerfile:10-12` | `ARG VITE_F2W_AGENT_API_URL=http://127.0.0.1:8090` is compiled into the bundle. | Build with `/fair2wise/api`; add a validated `base` (T24C). |
| D3 | `Dockerfile:15-25` | Frontend is a separate nginx image. | Fold into an in-container gateway shared by all three targets (T24B). |
| D4 | `Dockerfile:28,40,45,60-62` | Splash pixi binary, `SPLASH_LINKS_REPO`, and `pixi install` for `splash_links`. | Remove; Splash is retired (T24B, coordinated with the Splash disposition). |
| D5 | `Dockerfile:41-42` | `F2W_AGENT_HOST=0.0.0.0`, port 8090. | API on `127.0.0.1:8090` behind the gateway (T24B). |
| D6 | `Dockerfile:53-58` | `pip install -r requirements.txt` = runtime lock plus unlocked `requirements/semantic.in` (`torch>=2.12.0`, `faiss-cpu`, `sentence-transformers`). Platform-specific wheels, no lock (`semantic.in:1-3`). | A reproducible lock per supported platform (T24D, O08). |
| D7 | `Dockerfile:64` | `COPY . .` copies the whole context. | Allowlisted COPY (T24B). H0 E3 allows `storage/{kg,terminology,schema}`. |
| D8 | `Dockerfile:69-72` | Healthcheck is `curl /health`; CMD is `scripts/start_agent_backend.sh`. | Supervisor entrypoint, gateway liveness healthcheck (T24B). |
| D9 | `.dockerignore:1-25` (baseline); `:26-33` added on `forge_merge` (commits `bfbec85`, `23d3d05`) | Excludes `.env*`, `papers`, `runs`, `logs`, `splash_links/links.sqlite`. `forge_merge` also excludes `**/tiled_catalog*.db*` and `storage/source_index`. | Keep as a backstop. Add `storage/ontologies/` unless ChEBI attribution ships. Add layer scanning in CI (T24D). |
| D10 | `docker/nginx.conf:2,14-15` | Listens on `:80`; `/api/` goes to `http://agent:8090/` (another container). | `:8080`; `/fair2wise/api/` goes to `127.0.0.1:8090/`, stripped once (T24C). |
| D11 | `docker/nginx.conf:16-23` | SSE-safe proxy settings: buffering off, cache off, 900 s timeouts. | Keep (T24C). |
| D12 | `docker/nginx.conf:26-28` | `try_files $uri $uri/ /index.html` for every path. Missing assets and unknown API paths return HTML. | Scope the fallback to `/fair2wise/`; return 404 for assets and API (T24C). |

## 2. Compose and launchers

| # | File:line | Current behavior | Required change (task) |
| --- | --- | --- | --- |
| C1 | `compose.yaml:33-35` | `env_file: .env` (optional) supplies runtime secrets. | Images read `/run/secrets` files; compose can stay as a dev launcher (T24B). |
| C2 | `compose.yaml:57-58,83-84` | Loopback host ports `127.0.0.1:8090` and `127.0.0.1:${F2W_UI_PORT:-5175}`. | One loopback mapping to `:8080` for `:local` (T24B). |
| C3 | `compose.yaml:59-66` | KG/schema files bind-mounted read-only from `./storage`. Papers not mounted. | Use the `/var/lib/fair2wise/*` mount contract (T24B). |
| C4 | `compose.yaml:82` | Browser calls `http://127.0.0.1:8090` directly, bypassing nginx `/api/`. | Same-origin `/fair2wise/api` (T24C). |
| C5 | `compose.splash.yaml:91` | `VITE_F2W_AGENT_API_URL: /api`, the only same-origin precedent. | Model for T24C; the Splash file itself is retired. |
| C6 | `docker-compose.yml:79-98` | Tiled sidecar runs `pip install 'tiled[server]>=0.2.18'` at startup and serves on 0.0.0.0:8765. No `TILED_CATALOG_URI` in its env. The healthcheck uses `curl` in a python-slim image. | Not a deployment path. Superseded by `:local` managed Tiled (T20B). See ADR section 2. |
| C7 | `docker-compose.yml:100-119` | `tiled_seed` runs `ingest_tiled_sim.py --to-tiled` using the full host `.env`. | No automatic seed at start (T20B). |
| C8 | `scripts/start_agent_backend.sh:74-79` | Sources `$ROOT/.env`. | Developer launcher only. Image entrypoints never source `.env` (T24B). |
| C9 | `scripts/start_agent_backend.sh:212-240` | Starts Tiled with `nohup` if `import tiled` works, seeds on every start, exports `F2W_LIVE_TILED=1`. | Replace with explicit lifecycle in images. The developer path can call `scripts/tiled_server.py` (T20B). |
| C10 | `scripts/start_agent_backend.sh:216-217` | Catalog defaults to `storage/tiled_catalog.db` in the source tree. Hard-coded fallback key `devonlykeychangeinproduction…`. | Data mount plus a generated per-install key (T20B). |
| C11 | `scripts/start_agent_backend.sh:193` (baseline `lsof -ti`; `forge_merge` uses `lsof -nP -iTCP -sTCP:LISTEN`) | Port check. `lsof` is not installed in `python:3.12-slim`, so the check is a silent no-op in the image (inferred, not run). | The supervisor owns bind failures (T24B). |
| C12 | `scripts/start_agent_backend.sh:261` | Always passes `--allow-splash-wipe`. | Remove with Splash (T24B). |
| C13 | `scripts/start_agent_backend.sh:276-281`; `app/modules/f2w_agent/api.py:6603-6608,6616-6622` | Hard-coded dev CORS origins `:5173`/`:5175` with `allow_credentials=True`. | Images: same-origin, explicit allowlist (T24C). |
| C14 | `app/modules/f2w_agent/api.py:7038` | `uvicorn.run(app, host, port)`; no `root_path`, `proxy_headers` or `forwarded_allow_ips`. | Add them for `/fair2wise/api` behind the gateway (T24C). |
| C15 | `app/modules/kg_rag_api.py:245-313,330` | Six model-compatible routes; `uvicorn.run(app, host="0.0.0.0", port=11435)` hard-coded. A duplicate exists at `scripts/app/modules/kg_rag_api.py:305`. | Configurable loopback bind; route via `/fair2wise/model-api/` (T24C). |

## 3. GitHub workflows

| # | File:line | Current behavior | Required change (task) |
| --- | --- | --- | --- |
| W1 | `.github/workflows/publish-image.yml:3-6` | Triggers on push to `main` and tags `v*`. | O06 decided 2026-10-30 (D2.1-D2.10, `reviews/H6/o06_tag_rules_proposal.md`): push to `main`/`v*` publishes the three tested variants and moves all three channels; the `agent` image is no longer published (T24D). |
| W2 | `.github/workflows/publish-image.yml:13-17` | `permissions: contents: read, packages: write` for the whole job. Fine for a trusted job. | Split build, test and publish jobs; PRs never get write access (T24D). |
| W3 | `.github/workflows/publish-image.yml:10,36` | Image name is `ghcr.io/${{ github.repository }}`. | The normalized name `ghcr.io/fair2wise/fair2wise` is still a proposal; confirm the registry (T24D). |
| W4 | `.github/workflows/publish-image.yml:38-46` | `build-push-action` with `target: agent`, `push: true`. No `platforms:`, no `setup-qemu`/`setup-buildx` step. | Matrix over `local`/`als-dev`/`als-prod`, with digest capture and a platforms decision (O08) (T24D). |
| W5 | `.github/workflows/publish-image.yml:32-36` | `docker/metadata-action` defaults: branch/tag/semver tags. `:local`/`:als-*` are not produced. | Explicit tags per variant, plus an immutable commit+variant reference (T24D). |
| W6 | `.github/workflows/build-app.yml:3-7` | Tests run only for `main` push and PR. | Add `forge_merge` and PRs targeting it (T24D). |
| W7 | `.github/workflows/build-app.yml:41-46` | `mkdocs gh-deploy` and README tree update, guarded to `main` push. | Keep the guards; never broaden them (T24D). |
| W8 | `.github/workflows/build-app.yml:14,16` | `actions/checkout@v4` and `setup-python@v5` are pinned by tag, not SHA. In `publish-image.yml`, the docker actions are SHA-pinned but `checkout@v4` (`:21`) is not. | Pin everything by reviewed SHA (T24D). |

## 4. Browser and base path

| # | File:line | Current behavior | Required change (task) |
| --- | --- | --- | --- |
| B1 | `ui/vite.config.ts:20-38` | No `base`. | Validated `base` (`/fair2wise/` for images, `/` for dev) (T24C). |
| B2 | `ui/src/app/components/data/liveAgent.ts:221` | `AGENT_API_BASE = VITE_F2W_AGENT_API_URL \|\| 'http://127.0.0.1:8090'`, trailing slash stripped. | Images set `/fair2wise/api`; the dev default stays (T24C). |
| B3 | `ui/src/app/components/uiTelemetry.ts:53,80,99-102` | Uses `AGENT_API_BASE` for `/telemetry/ui`, `/annotations`, `/traces/*`. The trace URL is resolved against `window.location.origin`. | Works with an absolute-path base. Verify in a browser (T24C). |
| B4 | `ui/src/app/App.tsx:217` | `BrowserRouter` has no `basename`. | `basename` from `import.meta.env.BASE_URL` (T24C). |
| B5 | `ui/src/app/App.tsx:185-187` | Routes rooted at `'/'`. | Unchanged under the basename (T24C). |
| B6 | `ui/src/app/App.tsx:209` | `src="/wise_owl.svg"`, a root-absolute literal that Vite does not rewrite. | Prefix with the base (T24C). |
| B7 | `ui/index.html:9` | `href="/wise_owl.svg"`. Vite's HTML transform should apply `base`. | Verify in the build output (T24C). |
| B8 | `ui/src/app/components/AppDocsButton.tsx:13` | `VITE_F2W_DOCS_URL \|\| '/docs/'`. Only the Vite dev plugin serves `/docs` (`ui/vite-mkdocs-plugin.ts:76-120`); the nginx image has no docs route. | Decide on docs hosting at H1 (T24C). |
| B9 | `ui/vite-mkdocs-plugin.ts:48` | Generated MkDocs `site_url: http://127.0.0.1:5175/docs/`. | Dev only (T24C). |

## 5. Tiled client and target selection

| # | File:line | Current behavior | Required change (task) |
| --- | --- | --- | --- |
| T1 | `app/modules/tiled_graph.py:39-42` | Default `http://127.0.0.1:8765`, `/api/graphql`, probe queries. | Keep the path. The URL comes from the profile (T20B). |
| T2 | `app/modules/tiled_graph.py:153-175` | `coerce_local_tiled_uri` replaces any `*.als.lbl.gov` host with localhost, logging only a warning. | Profile allowlist. Never swap silently (T20B/T25B). |
| T3 | `app/modules/tiled_graph.py:183-195` | One `TILED_API_KEY` from env, scheme `Apikey`. | Separate writer credential (file) from the caller token (T25B). |
| T4 | `app/modules/tiled_graph.py:198-222` | Enabled by `F2W_LIVE_TILED`/`TILED_GRAPH_ENABLED`/`TILED_URI` from `os.environ`. | Immutable startup config object (T20B). |
| T5 | `app/modules/tiled_graph.py:225-280` | Probe statuses: disabled, invalid_uri, unreachable, unsigned, empty, ok. | Extend with the C1-C6 capability results (T20B). |
| T6 | `app/modules/tiled_graph.py:785-795` | `nodeId` fallback query. Needed for rc2 (`isNodeBound`). | Keep, and record which shape was used (T08B). |
| T7 | `app/modules/tiled_sim.py:290-303` | Write path refuses ALS and non-loopback hosts. | Generalize into a test/local profile guard (T20B). |
| T8 | `app/modules/tiled_sim.py:506-517,530-560` | Idempotent create by `uri`/`name`; no upsert. Requires `TILED_API_KEY`. | Pattern for the T08B importer (T08B). |
| T9 | `app/modules/f2w_agent/api.py:2774-2779` | `PUT /settings` accepts `tiled_uri` from the browser; fallback `http://127.0.0.1:8000`. | Read-only target in deployed profiles; fix the port (T25B/T20B). |
| T10 | `storage/tiled_config.yml:1-22` | Dev server config (`trees`/catalog, single-user key, anonymous read, browser CORS). | Replace with the managed config in the image (ADR section 4.2) (T20B). |
| T11 | `requirements/runtime.txt:306` | `pydantic-settings==2.14.2`, which conflicts with Tiled's `<2.12.0`. Tiled itself is not in any requirement file. | Isolated Tiled server venv; HTTP-only app client (T04/T20B). |
