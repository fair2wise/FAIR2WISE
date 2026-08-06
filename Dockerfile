# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS frontend-build

WORKDIR /build/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY ui/ ./
ARG VITE_F2W_AGENT_API_URL=/api
ENV VITE_F2W_AGENT_API_URL=${VITE_F2W_AGENT_API_URL}
RUN npm run build


FROM nginx:1.27-alpine AS frontend

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /build/ui/dist /usr/share/nginx/html

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=12 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1

LABEL Name="fair2wise-frontend" \
      Version="2.0" \
      Description="FAIR2WISE web frontend and private agent API gateway"


FROM ghcr.io/prefix-dev/pixi:0.70.2 AS pixi


FROM python:3.12-slim AS agent

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
