# Docker profile contract: three image targets (T24A)

**Status:** Proposed for H1. This document describes a design. No Dockerfile, nginx config or workflow in the repository was changed, and nothing was built.

Line references point to baseline `222b1e7`. Where `forge_merge` differs, the `forge_merge` line is cited. Labels used: **[Observed]** for facts read from the source; **[Proposed]** for design choices that need review.

## 1. Current build graph

**[Observed]**

| Stage / service | Source | What it is | Problems for the three targets |
| --- | --- | --- | --- |
| `frontend-build` (node:20-bookworm-slim) | `Dockerfile:3-12` | `npm ci`, then `vite build` with `VITE_F2W_AGENT_API_URL` (default `http://127.0.0.1:8090`) | The browser default is localhost:8090 (`:10`). There is no `base`. |
| `frontend` (nginx:1.27-alpine) | `Dockerfile:15-25`; `docker/nginx.conf:1-29` | Static UI, plus `/api/` proxied to `agent:8090` | Rooted at `/`. The upstream is another container, `agent:8090` (`nginx.conf:15`). The SPA fallback catches every path (`:26-28`), so missing assets get `index.html`. |
| `pixi` | `Dockerfile:28` | Copies the pixi binary, used only for Splash | Splash is retired (TILED_DEPLOYMENT_SPEC.md section 6). Remove it. |
| `agent` (python:3.12-slim) | `Dockerfile:31-76` | pip install of `requirements.txt` (runtime lock + `semantic.in`: torch/faiss/sentence-transformers), Splash pixi env, then `COPY . .` (`:64`); CMD `scripts/start_agent_backend.sh` | Broad copy. No UI. No supervisor. Binds 0.0.0.0:8090. No Tiled installed, so the Tiled block in the start script does not run. |
| `compose.yaml` agent + frontend | `compose.yaml:27-98` | Product compose file. The browser calls `http://127.0.0.1:8090` directly (`:82`), so nginx `/api` is not used. JSON KG files are bind-mounted from `./storage` (`:59-66`). | Two containers. Papers are not mounted. |
| `compose.splash.yaml` | `:85-93` | Frontend built with `VITE_F2W_AGENT_API_URL=/api` (same origin) | The only existing same-origin precedent. Splash itself is retired. |
| `docker-compose.yml` | `:22-129` | Dev: Vite hot-reload, an agent, a Tiled sidecar that pip-installs at runtime, and `tiled_seed` | Developer convenience only. It cannot satisfy `:local` (DEPLOYMENT_STRATEGY.md section 2). |
| `compose.hostnet.yaml` | `:1-14` | Linux host-network overlay for CBorg IPv6 | Keep it as an operator overlay. Not part of the image contract. |
| `publish-image.yml` | `.github/workflows/publish-image.yml:38-46` | Builds and pushes `target: agent` only, with no `platforms:` | Must become a three-target matrix (CI_CD_CONTRACT.md). Platforms: O08. |

## 2. Target mapping

**[Proposed]**

```text
ui-build (node, pinned digest)         app-runtime (python 3.12, pinned digest)
  vite build --base=/fair2wise/          F2W lock install (no Splash/pixi)
  VITE_F2W_AGENT_API_URL=/fair2wise/api  allowlisted COPY: app/, scripts/<needed>, storage/{kg,terminology,schema,...}
        \                                 nginx gateway + supervisor + non-root user
         \___________________________________/
                 |                         \
          local (final)                 external-runtime (intermediate)
          + /opt/tiled/venv (tiled==<H1 artifact>[server])      |            \
          + tiled managed.yml (no secrets)                   als-dev        als-prod
          + supervisor service "tiled"                       PROFILE=als-dev PROFILE=als-prod
          PROFILE=local                                      URL=staging     URL=production
```

| Current | Becomes | Notes |
| --- | --- | --- |
| `frontend-build` | `ui-build` | Shared by all targets. One build, because all three use the same base path and API URL. |
| `frontend` (nginx) | The gateway inside `app-runtime` | No separate container. The upstream changes from `agent:8090` to `127.0.0.1:8090`. |
| `agent` | `app-runtime` | The UI is copied in from `ui-build`. `COPY . .` (`Dockerfile:64`) is replaced by an allowlist. The pixi/Splash lines (`:28,45,60-62`) are removed. |
| (none) | `local` | Adds the isolated Tiled server venv and the managed config. It must not be inherited by the ALS targets. |
| (none) | `external-runtime`, `als-dev`, `als-prod` | Differ only in profile environment defaults and labels (`org.opencontainers.image.*`, `f2w.profile`, `f2w.base_path`, `f2w.tiled_mode`, `f2w.tiled_version`). |

**Image content rules (H0):**

- **Allowed:** code, approved schemas, and `storage/kg`, `storage/terminology`, `storage/schema` (E3 owner exception).
- **Excluded:**
  - `papers/`, `storage/source_index` (E4), and `**/tiled_catalog*.db*` (`.dockerignore:24,29-33` on `forge_merge`)
  - `.env*`, `runs`, `logs`
  - `storage/ontologies/chebi.obo`, unless attribution is shipped (H0 audit E4 note)
- **Optional catalog seed in `local` only:** a read-only seed as described in the ADR, section 4.2.
- **Use allowlisted `COPY` lines.** `.dockerignore` is a backstop, not the control.

## 3. Gateway routing

**[Proposed]** nginx inside the container, listening on `:8080`.

The edge forwards `/fair2wise` unchanged. The in-container gateway is the only place that strips a prefix.

| Public request | Gateway rule | Upstream sees |
| --- | --- | --- |
| `/fair2wise` | `location = /fair2wise { return 308 /fair2wise/$is_args$args; }` | (redirect) |
| `/fair2wise/healthz` | Static `200 ok`. No secrets and no upstream call. | (none) |
| `/fair2wise/assets/*` | `try_files $uri =404;` (never index.html) | (static) |
| `/fair2wise/` and deep links | `root /usr/share/fair2wise/www;` with the UI at `www/fair2wise/`; `try_files $uri $uri/ /fair2wise/index.html;` | (static) |
| `/fair2wise/api/<path>` | `location /fair2wise/api/ { proxy_pass http://127.0.0.1:8090/; }` The trailing slash strips `/fair2wise/api` exactly once. | `/<path>`, e.g. `/chat/stream`, `/settings`, `/graph/...` (routes at `app/modules/f2w_agent/api.py:6624-7018`) |
| `/fair2wise/api` (no slash) | `return 308 /fair2wise/api/` | (redirect) |
| `/fair2wise/model-api/<path>` | `location /fair2wise/model-api/ { proxy_pass http://127.0.0.1:11435/; }` Only if the profile enables it. | `/v1/chat/completions`, `/v1/models`, `/api/chat`, `/api/tags`, `/api/version`, `/api/ps` (`app/modules/kg_rag_api.py:245-313`) |
| Anything else | `404` | (none) |
| Local Tiled `:8765` | **No route** | (none) |

**Settings for the streaming API locations:**

- Keep from `docker/nginx.conf:16-23`: `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_cache off`, and 900 s read/send timeouts.
- Add `X-Forwarded-Prefix /fair2wise/api`.
- Set `client_max_body_size` to the approved PDF intake limit. That limit is still unset; Ansible supplies it.

**API side:**

- The FastAPI process runs with `root_path="/fair2wise/api"`, so OpenAPI and docs URLs resolve correctly.
- It trusts proxy headers only from `127.0.0.1`: uvicorn `proxy_headers=True`, `forwarded_allow_ips="127.0.0.1"`.
- Today `uvicorn.run(...)` sets neither of these (`app/modules/f2w_agent/api.py:7038`). That is a T24C seam.

**CORS:** the same-origin default needs none. The current hard-coded dev origins (`api.py:6603-6608`, `scripts/start_agent_backend.sh:276-281`) stay for the developer launcher. Images take an explicit allowlist from Ansible, empty by default.

## 4. Frontend build contract

**[Proposed]**

- `ui/vite.config.ts:20-38`: add `base` from a validated build input (`F2W_UI_BASE`, default `/`), so the developer `npm run dev` stays unchanged. Images build with `/fair2wise/`. The build fails unless the value starts and ends with `/`.
- `VITE_F2W_AGENT_API_URL=/fair2wise/api` at build time. `liveAgent.ts:221` already strips the trailing slash and concatenates paths. `uiTelemetry.ts:99-102` resolves `${AGENT_API_BASE}/traces/...` against `window.location.origin`, which is correct for an absolute-path base.
- Remove the old compiled default `http://127.0.0.1:8090` (`Dockerfile:10`) from image builds. The dev default in `liveAgent.ts:221` can stay.
- Router: `BrowserRouter` has no `basename` (`ui/src/app/App.tsx:217`). Set `basename={import.meta.env.BASE_URL}`. Routes are rooted at `'/'` (`App.tsx:185-187`).
- Absolute asset paths:
  - `App.tsx:209` uses `src="/wise_owl.svg"`. Vite does not rewrite JSX string literals, so this breaks under `/fair2wise/`. Change it to `${import.meta.env.BASE_URL}wise_owl.svg`.
  - `ui/index.html:9` uses `href="/wise_owl.svg"`. Vite's HTML transform normally applies `base` to public-dir URLs in `index.html`. Verify this in T24C.
- Docs link: `AppDocsButton.tsx:13` defaults to `/docs/`. Only the Vite dev plugin serves MkDocs (`ui/vite-mkdocs-plugin.ts:76-120`). The current nginx image has no `/docs` route, so the link falls into the SPA fallback. H1 must choose one: serve docs at `/fair2wise/docs/`, or set `VITE_F2W_DOCS_URL` to the published docs site.
- Build-time and runtime agreement: write the base and API URL into `www/fair2wise/f2w-build.json`. The entrypoint compares it with `F2W_BASE_PATH` and refuses to start on a mismatch (DEPLOYMENT_STRATEGY.md section 3).

## 5. Process model and supervisor

**[Proposed]** One container per deployment, one ingress port.

| Service | Bind | `:local` | ALS | Readiness | On unrecoverable failure |
| --- | --- | --- | --- | --- | --- |
| gateway (nginx) | `0.0.0.0:8080` in the container | yes | yes | `/fair2wise/healthz` | Container exits non-zero |
| api (uvicorn, F2W FastAPI) | `127.0.0.1:8090` | yes | yes | `GET /health` | Container exits non-zero |
| worker (durable jobs: extraction/ingest/import) | none | yes | yes | Heartbeat file or job store | Container exits non-zero after drain timeout |
| model-api (`kg_rag_api`) | `127.0.0.1:11435` | if enabled | if enabled | `GET /v1/models` | Feature not ready |
| tiled (managed) | `127.0.0.1:8765` | **yes** | **absent** | C1-C3 (ADR section 3.4) | Graph publication not ready; restart with backoff |

**Observed constraints:**

- `kg_rag_api` hard-codes `host="0.0.0.0", port=11435` (`app/modules/kg_rag_api.py:330`). It needs a configurable loopback bind.
- Extraction (`scripts/run.py`) is deliberately not managed today (`compose.yaml:8-16`; `scripts/start_agent_backend.sh:6-14`). The worker service is new work (T24B), coordinated with the durable-job tasks.

**Supervisor options for the H1 choice:**

| Option | Pros | Cons |
| --- | --- | --- |
| **s6-overlay v3** | Dependency ordering and readiness (`notification-fd`). Per-service `finish` scripts. `S6_BEHAVIOUR_IF_STAGE2_FAILS=2` makes the container exit when a required service fails. Proper PID 1 and reaping. | Another tool to learn. Pin by release tarball and checksum. |
| supervisord | Familiar, Python-based | No dependency readiness. Keeps running after a child dies unless an eventlistener kills it. Needs a Python env separate from the app lock. |
| tini + bash | Small | Not a restart or readiness supervisor (DEPLOYMENT_STRATEGY.md section 2 rules it out). |

**Recommendation (unresolved, H1):** s6-overlay, with `tiled` as a longrun service that exists only in `local`. Rules for every option:

- Stop gracefully: SIGTERM, drain, then a bounded `S6_KILL_GRACETIME`.
- Never use the `cmd1 & cmd2 & tail -f` pattern.
- Run all services as a non-root UID/GID (proposed 10001). Mount UID/GID are checked at startup and never `chown -R`'d.

**Container `HEALTHCHECK`:** gateway liveness only (`/fair2wise/healthz`). Capability readiness (Tiled, model, mounts) is reported by `/fair2wise/api/health` and does not fail liveness. A JSON-only workflow must still work while central Tiled is down.

## 6. Entrypoint validation

**[Proposed]** Runs before the supervisor starts any services.

1. Read `F2W_DEPLOYMENT_PROFILE`; it must equal the image's baked `f2w.profile` label. An ALS image refuses `local`, and `local` refuses `als-*`.
2. Check that `F2W_BASE_PATH` equals `/fair2wise` and matches `f2w-build.json`.
3. Tiled:
   - `local`: `F2W_TILED_MODE=managed`, and the `/var/lib/fair2wise/tiled` mount exists and is writable by the UID.
   - ALS: `F2W_TILED_MODE=external`, `F2W_TILED_URL` equals the fixed URL, `F2W_TILED_WRITER_SECRET_FILE` is a readable regular file with mode `0600`/`0400` owned by the running uid, and no Tiled server config or venv is present.
4. Check each required mount exists with the expected mode (`ansible_input_contract.md` section 3). If a mount is missing, stop with an actionable error. Never fall back to container-local storage.
5. Never `source .env`. The baseline launcher sources `.env` (`scripts/start_agent_backend.sh:74-79`). Image entrypoints do not.
