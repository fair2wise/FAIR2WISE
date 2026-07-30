# Keep Node/npm and Pixi versions explicit so the image satisfies the same
# prerequisites documented for local development.
FROM node:20-bookworm-slim AS node
FROM ghcr.io/prefix-dev/pixi:0.70.2 AS pixi

# Python 3.12 is required by FAIR2WISE and avoids Python 3.14 native ML stack
# instability.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYSTOW_HOME=/app/.cache/pystow \
    SPLASH_LINKS_REPO=splash_links \
    SPLASH_LINKS_DB=links.sqlite \
    F2W_AGENT_HOST=0.0.0.0 \
    F2W_UI_HOST=0.0.0.0

# Node's official image installs npm beside Node under /usr/local.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=pixi /usr/local/bin/pixi /usr/local/bin/pixi

RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    apt-get update && \
    apt-get install --no-install-recommends -y ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install each dependency set in its own cacheable layer.
COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY ui/package.json ui/package-lock.json ./ui/
RUN npm ci --prefix ui

# splash_links is an editable Pixi dependency, so its source is required while
# creating the environment.
COPY splash_links ./splash_links
RUN pixi install --manifest-path splash_links/pixi.toml --locked

COPY . .

RUN mkdir -p .cache/pystow storage/knowledge_gaps storage/ontologies .run && \
    chmod +x scripts/start_all.sh scripts/start_agent_backend.sh \
      scripts/start_agent_frontend.sh

EXPOSE 5173 8081 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5173/ >/dev/null && \
        curl -fsS http://127.0.0.1:8090/health >/dev/null && \
        curl -fsS http://127.0.0.1:8081/splash_links/health >/dev/null || exit 1

CMD ["./scripts/start_all.sh"]

LABEL Name="FAIRtoWISE-FORUM-AI" \
      Version="2.0" \
      Description="FAIR2WISE UI, agent backend, and Splash Links knowledge graph"
