# syntax=docker/dockerfile:1.7

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


# MkDocs site served by the gateway at /fair2wise/docs/ (owner decision O-A7).
FROM python:3.12-slim AS docs-build

WORKDIR /build
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install mkdocs==1.6.1 mkdocs-material==9.7.7
COPY mkdocs/ ./mkdocs/
COPY app/modules/deployment/gateway.py ./gateway.py
RUN python gateway.py mkdocs-config --source mkdocs/mkdocs.yml --out mkdocs/.mkdocs.image.yml && \
    mkdocs build --strict -f mkdocs/.mkdocs.image.yml -d /build/site


FROM nginx:1.27-alpine AS frontend

# docker/nginx.conf is the /fair2wise gateway template; the image entrypoint fills
# only F2W_* placeholders (the private API host:port) into conf.d/default.conf.
ENV F2W_API_UPSTREAM=127.0.0.1:8090 \
    NGINX_ENVSUBST_FILTER=^F2W_
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=frontend-build /build/ui/dist /usr/share/nginx/html/fair2wise
COPY --from=docs-build /build/site /usr/share/nginx/html/fair2wise/docs

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=12 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1

LABEL Name="fair2wise-frontend" \
      Version="2.0" \
      Description="FAIR2WISE web frontend and /fair2wise agent API gateway"


FROM ghcr.io/prefix-dev/pixi:0.70.2 AS pixi


FROM python:3.12-slim AS agent

# Runtime secrets are injected by Compose env_file from the host dotenv file.
# Do not pass LLM credentials as build args or bake dotenv files into layers.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYSTOW_HOME=/app/.cache/pystow \
    SPLASH_LINKS_REPO=/app/splash_links \
    F2W_AGENT_HOST=0.0.0.0 \
    F2W_AGENT_PORT=8090 \
    KG_RAG_FORCE_CPU=1

COPY --from=pixi /usr/local/bin/pixi /usr/local/bin/pixi

RUN apt-get update && \
    apt-get install --no-install-recommends -y ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
COPY requirements/runtime.txt ./requirements/runtime.txt
COPY requirements/semantic.in ./requirements/semantic.in
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY splash_links ./splash_links
RUN --mount=type=cache,target=/root/.cache/rattler \
    pixi install --manifest-path splash_links/pixi.toml --locked

COPY . .

RUN mkdir -p /app/.cache/pystow /app/runs && \
    chmod +x scripts/start_agent_backend.sh

HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=12 \
    CMD curl -fsS http://127.0.0.1:8090/health >/dev/null || exit 1

CMD ["./scripts/start_agent_backend.sh"]

LABEL Name="fair2wise-agent" \
      Version="2.0" \
      Description="FAIR2WISE orchestrated agent API"
