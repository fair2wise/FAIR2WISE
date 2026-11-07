# syntax=docker/dockerfile:1.7

# Python base for the T24B runtime stages; pin a digest here when releasing (T24D).
ARG PYTHON_IMAGE=python:3.12-slim

FROM node:20-bookworm-slim AS frontend-build

WORKDIR /build/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY ui/ ./
# T24C/D26: every image serves under /fair2wise. ui/vite.config.ts derives base
# /fair2wise/, the same-origin API /fair2wise/api and docs /fair2wise/docs/ from
# this one setting and refuses any other browser API URL (no compiled host or
# localhost endpoint). Do not pass LLM credentials as build args.
ENV F2W_PUBLIC_PREFIX=/fair2wise
RUN npm run build && grep -q '"public_prefix": "/fair2wise"' dist/f2w-build.json


# Pixi version = the one that wrote pixi.lock (a newer pixi reads older locks, but the lock is
# only guaranteed byte-stable under the writing version). Used by the build stages only; the
# pixi binary never reaches an image the supervisor runs (the legacy `agent` target keeps its
# own pixi for splash_links below).
FROM ghcr.io/prefix-dev/pixi:0.66.0 AS pixi-app

# MkDocs site served by the gateway at /fair2wise/docs/ (owner decision O-A7). Built with the
# locked pixi `docs` environment (mkdocs 1.6.1, mkdocs-material 9.7.7), at the path it is used from.
FROM pixi-app AS docs-build

WORKDIR /build
COPY pixi.toml pixi.lock ./
RUN --mount=type=cache,target=/root/.cache/rattler \
    pixi install --locked -e docs
ENV PATH=/build/.pixi/envs/docs/bin:$PATH
COPY mkdocs/ ./mkdocs/
COPY app/modules/deployment/gateway.py ./gateway.py
RUN python gateway.py mkdocs-config --source mkdocs/mkdocs.yml --out mkdocs/.mkdocs.image.yml && \
    mkdocs build --strict -f mkdocs/.mkdocs.image.yml -d /build/site


FROM nginx:1.27-alpine AS frontend

# docker/nginx.conf is the /fair2wise gateway template; the image entrypoint fills
# only F2W_* placeholders (the private API host:port and the listen port) into
# conf.d/default.conf.
ENV F2W_API_UPSTREAM=127.0.0.1:8090 \
    F2W_GATEWAY_PORT=8080 \
    NGINX_ENVSUBST_FILTER=^F2W_
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=frontend-build /build/ui/dist /usr/share/nginx/html/fair2wise
COPY --from=docs-build /build/site /usr/share/nginx/html/fair2wise/docs

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=12 \
    CMD wget -qO- "http://127.0.0.1:${F2W_GATEWAY_PORT}/healthz" >/dev/null || exit 1

LABEL Name="fair2wise-frontend" \
      Version="2.0" \
      Description="FAIR2WISE web frontend and /fair2wise agent API gateway"


# ---------------------------------------------------------------------------------------
# T24B: one shared runtime base and three supervised finals (D23-D25, D29; owner O-T1/O-D1).
#
#   pixi-app            pixi 0.66.0, the version that wrote pixi.lock (build stages only)
#   app-env-build       pixi `default` environment from pixi.lock (`--locked`: every conda
#                       package and PyPI artifact is checked against the lock's sha256)
#   tiled-server-build  isolated Tiled 0.2.18 server environment (pixi `tiled-server`), :local only
#   runtime-base        app + UI bundle + MkDocs + gateway + supervisor, no profile baked
#   local               runtime-base + /opt/tiled/venv + docker/profiles/local.json
#   als-dev / als-prod  runtime-base + their profile; no Tiled server (central Tiled)
#   agent               legacy developer/compose API-only target (start_agent_backend.sh),
#                       the only target with Splash/pixi (until its retirement gate)
#
# Image content is an allowlist of COPY lines; .dockerignore is only a backstop. Never in a
# layer: papers/PDFs, parsed bodies, figure crops, storage/source_index, credentials or
# dotenv files, runs/logs, job/session databases, Tiled catalogs. Tracked KG JSON
# (storage/kg, storage/terminology) stays in the image (owner O-D1, H0 E3; E3 errata applied
# 2026-11-06): flat storage/kg/matkg_*.json copies and nested manifests/validation reports ship, nested
# <corpus>/v<N>/graph.json duplicates do not (.dockerignore, D3.24). A baked file never selects data. Durable state is
# mounted at runtime and checked by preflight (docker/profiles/README.md).
# ---------------------------------------------------------------------------------------

# The application environment. pixi.lock pins every conda package and PyPI artifact with its
# sha256 and `--locked` refuses to install anything the lock does not describe (audit
# AUD-08 / T28C: the previous pip --require-hashes rule, now for conda and PyPI alike). torch
# is the PyTorch CPU build (pixi.toml), so no CUDA wheel can enter; the build still fails if one
# slips in. The environment lives at the path it is used from: conda prefixes are not relocatable.
FROM pixi-app AS app-env-build
WORKDIR /opt/fair2wise/pixi
COPY pixi.toml pixi.lock ./
RUN --mount=type=cache,target=/root/.cache/rattler \
    pixi install --locked -e default && \
    /opt/fair2wise/pixi/.pixi/envs/default/bin/python -I -c "import sys, torch; sys.exit(0 if torch.version.cuda is None else 1)" && \
    find /opt/fair2wise/pixi/.pixi/envs/default -name __pycache__ -type d -prune -exec rm -rf {} + && \
    chmod -R a+rX /opt/fair2wise/pixi/.pixi

# Pin: owner decision O-T1 = PINNED_TILED_VERSION / PINNED_TILED_WHEEL_SHA256 in
# app/modules/deployment/tiled_config.py; tests/images check pixi.lock records that wheel hash.
# The server gets its own pixi environment (`tiled-server`, no app packages): tiled needs
# pydantic-settings<2.12, the app environment pins 2.14.x. Built at the final path
# /opt/tiled/ws/.pixi/envs/tiled-server; `local` copies it to the same path and links
# /opt/tiled/venv to it (F2W_TILED_PYTHON keeps its documented value).
FROM pixi-app AS tiled-server-build
WORKDIR /opt/tiled/ws
COPY pixi.toml pixi.lock ./
RUN --mount=type=cache,target=/root/.cache/rattler \
    pixi install --locked -e tiled-server && \
    /opt/tiled/ws/.pixi/envs/tiled-server/bin/python -I -c "import sys, tiled; sys.exit(tiled.__version__ != '0.2.18')" && \
    find /opt/tiled/ws/.pixi/envs/tiled-server -name __pycache__ -type d -prune -exec rm -rf {} + && \
    chmod -R a+rX /opt/tiled/ws/.pixi


FROM ${PYTHON_IMAGE} AS runtime-base

# Runtime secrets are runtime mounts/env (Ansible), never build args or layers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KG_RAG_FORCE_CPU=1 \
    PYSTOW_HOME=/var/lib/fair2wise/cache/pystow \
    F2W_PUBLIC_PREFIX=/fair2wise \
    F2W_API_UPSTREAM=127.0.0.1:8090 \
    F2W_GATEWAY_PORT=8080 \
    F2W_RESOURCE_ROOT=/app \
    PYTHONPATH=/app \
    F2W_DOTENV=off

# tini is PID 1 (signal delivery, orphan reaping); nginx is the in-container gateway, run
# unprivileged on F2W_GATEWAY_PORT (default 8080, the T24A one-ingress contract), so it needs
# no file capability (C6).
RUN apt-get update && \
    apt-get install --no-install-recommends -y ca-certificates curl tini nginx && \
    rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/* /etc/nginx/conf.d/* && \
    groupadd --system --gid 10001 fair2wise && \
    useradd --system --uid 10001 --gid 10001 --home-dir /home/fair2wise --create-home \
        --shell /usr/sbin/nologin fair2wise

WORKDIR /app

# The locked application environment from app-env-build, at the path it was built for. PATH
# puts its python first, so `python` in the entrypoint, HEALTHCHECK helpers and every
# `RUN python` below is that interpreter. Semantic retrieval (QA path and the term-merge
# shortlist) stays in every image; torch is the CPU build (locked in pixi.lock).
COPY --from=app-env-build /opt/fair2wise/pixi/.pixi/envs/default /opt/fair2wise/pixi/.pixi/envs/default
ENV PATH=/opt/fair2wise/pixi/.pixi/envs/default/bin:$PATH

# Embedding models the semantic backends load (code defaults: KG_RAG_EMBED_MODEL and
# F2W_MERGE_EMBED_MODEL), fetched at build time at pinned revisions (Apache-2.0) so the
# image serves them with HF_HUB_OFFLINE=1 and never downloads at runtime. The code loads them by
# bare model name, and a snapshot fetched by commit hash has no refs/main, which offline mode
# needs to resolve the name: the loop writes it (found when the pixi image was first run offline).
ENV HF_HOME=/opt/fair2wise/hf
RUN python -I -c "from huggingface_hub import snapshot_download as d; \
    d('sentence-transformers/all-MiniLM-L6-v2', revision='1110a243fdf4706b3f48f1d95db1a4f5529b4d41'); \
    d('sentence-transformers/paraphrase-MiniLM-L6-v2', revision='c9a2bfebc254878aee8c3aca9e6844d5bbb102d1')" && \
    for m in /opt/fair2wise/hf/hub/models--*/; do \
        mkdir -p "$m/refs" && ls "$m/snapshots" | head -n 1 | tr -d '\n' > "$m/refs/main"; \
    done && \
    chmod -R a+rX /opt/fair2wise/hf

# Docling's layout and table-structure model weights (MIT / CC-BY-4.0 model cards), fetched at build
# time at the commits pinned in scripts/fetch_docling_models.py (one list for the image and CI) into
# the folder layout Docling reads through F2W_DOCLING_ARTIFACTS_PATH, so the image parses PDFs with
# HF_HUB_OFFLINE=1 and never downloads at runtime. They are not part of the repository or pixi.lock.
# The same script also fetches the figure-OCR checkpoints (T30: Docling's RapidOCR, torch backend,
# English) into <artifacts>/RapidOcr, SHA256-verified against the locked rapidocr release's registry.
ENV F2W_DOCLING_ARTIFACTS_PATH=/opt/fair2wise/docling
COPY scripts/fetch_docling_models.py /tmp/fetch_docling_models.py
RUN python -I /tmp/fetch_docling_models.py --dest /opt/fair2wise/docling && \
    rm /tmp/fetch_docling_models.py && \
    chmod -R a+rX /opt/fair2wise/docling

# Allowlisted application content.
COPY pyproject.toml config.yml LICENSE.txt ./
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY storage/kg/ ./storage/kg/
COPY storage/terminology/ ./storage/terminology/
COPY storage/schema/ ./storage/schema/
COPY storage/competency_questions/ ./storage/competency_questions/
COPY storage/fixtures/ ./storage/fixtures/

# UI bundle and MkDocs site from the T24C stages, gateway template, supervisor.
COPY --from=frontend-build /build/ui/dist /usr/share/nginx/html/fair2wise
COPY --from=docs-build /build/site /usr/share/nginx/html/fair2wise/docs
COPY docker/nginx.conf /etc/fair2wise/nginx/gateway.conf.template
COPY docker/entrypoint/ /opt/fair2wise/entrypoint/

# The f2w-* console commands of pyproject.toml [project.scripts] (f2w-kg-adopt, f2w-kg-release, ...):
# the project is not pip-installed (the environment has no pip; the code runs from PYTHONPATH=/app),
# so launchers are generated from the same table pip would read (E2E finding 2026-11-06).
RUN python -I /opt/fair2wise/entrypoint/install_console_scripts.py \
        --pyproject /app/pyproject.toml --bin /usr/local/bin \
        --python /opt/fair2wise/pixi/.pixi/envs/default/bin/python --root /app

# Mount points owned by the runtime user (a fresh named volume inherits the owner);
# preflight refuses to start unless they are real mounts.
RUN python app/modules/deployment/gateway.py check-build \
        --manifest /usr/share/nginx/html/fair2wise/f2w-build.json --prefix /fair2wise && \
    mkdir -p /var/lib/fair2wise/state /var/lib/fair2wise/cache /run/fair2wise && \
    chown 10001:10001 /var/lib/fair2wise/state /var/lib/fair2wise/cache /run/fair2wise && \
    chmod 0700 /var/lib/fair2wise/state

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM

# Gateway liveness only; capability readiness (Tiled, model, mounts) is /fair2wise/api/health
# and the supervisor status file /run/fair2wise/supervisor.json.
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${F2W_GATEWAY_PORT}/fair2wise/healthz" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "python", "/opt/fair2wise/entrypoint/f2w_supervisor.py", "--profile-file", "/etc/fair2wise/profile.json"]


FROM runtime-base AS local

USER root
COPY --from=tiled-server-build /opt/tiled/ws/.pixi/envs/tiled-server /opt/tiled/ws/.pixi/envs/tiled-server
RUN ln -s /opt/tiled/ws/.pixi/envs/tiled-server /opt/tiled/venv
COPY docker/profiles/local.json /etc/fair2wise/profile.json
RUN mkdir -p /var/lib/fair2wise/tiled && \
    chown 10001:10001 /var/lib/fair2wise/tiled && \
    chmod 0700 /var/lib/fair2wise/tiled
USER 10001:10001

ENV F2W_DEPLOYMENT_PROFILE=local \
    F2W_AUTHORIZATION_MODE=local_trusted \
    F2W_TILED_MODE=managed \
    F2W_TILED_DATA_DIR=/var/lib/fair2wise/tiled \
    F2W_TILED_PYTHON=/opt/tiled/venv/bin/python \
    F2W_TILED_PORT=8765

LABEL org.opencontainers.image.title="fair2wise" \
      org.opencontainers.image.description="FAIR2WISE :local - UI, API, worker and managed Tiled 0.2.18" \
      f2w.profile="local" \
      f2w.base_path="/fair2wise" \
      f2w.tiled_mode="managed" \
      f2w.tiled_version="0.2.18"


FROM runtime-base AS als-dev

USER root
COPY docker/profiles/als-dev.json /etc/fair2wise/profile.json
USER 10001:10001

ENV F2W_DEPLOYMENT_PROFILE=als-dev \
    F2W_AUTHORIZATION_MODE=tiled_authenticated \
    F2W_TILED_MODE=external \
    F2W_TILED_URL=https://tiled-staging.als.lbl.gov

LABEL org.opencontainers.image.title="fair2wise" \
      org.opencontainers.image.description="FAIR2WISE :als-dev - UI, API and worker; central Tiled (staging)" \
      f2w.profile="als-dev" \
      f2w.base_path="/fair2wise" \
      f2w.tiled_mode="external" \
      f2w.tiled_version="0.2.18"


FROM runtime-base AS als-prod

USER root
COPY docker/profiles/als-prod.json /etc/fair2wise/profile.json
USER 10001:10001

ENV F2W_DEPLOYMENT_PROFILE=als-prod \
    F2W_AUTHORIZATION_MODE=tiled_authenticated \
    F2W_TILED_MODE=external \
    F2W_TILED_URL=https://tiled.als.lbl.gov

LABEL org.opencontainers.image.title="fair2wise" \
      org.opencontainers.image.description="FAIR2WISE :als-prod - UI, API and worker; central Tiled (production)" \
      f2w.profile="als-prod" \
      f2w.base_path="/fair2wise" \
      f2w.tiled_mode="external" \
      f2w.tiled_version="0.2.18"


# Splash/pixi for the legacy editable mode (compose.splash.yaml, F2W_KG_MODE=splash). Used
# only by the agent target below; never by runtime-base, local, als-dev or als-prod.
FROM ghcr.io/prefix-dev/pixi:0.70.2 AS pixi


# Legacy developer target for compose.yaml / compose.splash.yaml (agent API only, no
# supervisor, no gateway). It keeps the previous root user and /app/runs paths so existing
# compose volumes work. Splash stays in this legacy agent target until its approved
# retirement gate (T16B at H7, or H8); the Tiled-backed edit path is not built yet (T18C F1).
FROM runtime-base AS agent

USER root
ENV PYSTOW_HOME=/app/.cache/pystow \
    SPLASH_LINKS_REPO=/app/splash_links \
    F2W_AGENT_HOST=0.0.0.0 \
    F2W_AGENT_PORT=8090

COPY --from=pixi /usr/local/bin/pixi /usr/local/bin/pixi

COPY splash_links ./splash_links
RUN --mount=type=cache,target=/root/.cache/rattler \
    pixi install --manifest-path splash_links/pixi.toml --locked

RUN mkdir -p /app/.cache/pystow /app/runs && \
    chmod +x scripts/start_agent_backend.sh

HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=12 \
    CMD curl -fsS http://127.0.0.1:8090/health >/dev/null || exit 1

ENTRYPOINT []
CMD ["./scripts/start_agent_backend.sh"]

LABEL Name="fair2wise-agent" \
      Version="2.0" \
      Description="FAIR2WISE orchestrated agent API (developer compose target)"
