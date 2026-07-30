#!/usr/bin/env bash
# Build the image first with: docker build -t fair2wise .

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="${F2W_DOCKER_IMAGE:-fair2wise}"
CONTAINER_NAME="${F2W_DOCKER_CONTAINER:-fair2wise}"
ENV_FILE="${F2W_DOCKER_ENV_FILE:-$ROOT_DIR/.env}"
UI_PORT="${F2W_DOCKER_UI_PORT:-5173}"
AGENT_PORT="${F2W_DOCKER_AGENT_PORT:-8090}"
SPLASH_PORT="${F2W_DOCKER_SPLASH_PORT:-8081}"
DATA_DIR="${F2W_DOCKER_DATA_DIR:-$ROOT_DIR/.docker-data/splash-links}"
RUNS_DIR="${F2W_DOCKER_RUNS_DIR:-$ROOT_DIR/runs}"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: Docker is not installed or is not on PATH" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "error: cannot reach the Docker daemon; start Docker Desktop and retry" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: environment file not found: $ENV_FILE" >&2
  echo "create it from .env.example or set F2W_DOCKER_ENV_FILE" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "error: Docker image '$IMAGE_NAME' does not exist" >&2
  echo "build it first: docker build -t \"$IMAGE_NAME\" \"$ROOT_DIR\"" >&2
  exit 1
fi

mkdir -p "$DATA_DIR" "$RUNS_DIR"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]]; then
    echo "FAIR2WISE is already running in container '$CONTAINER_NAME'."
  else
    docker start "$CONTAINER_NAME" >/dev/null
    echo "Restarted existing FAIR2WISE container '$CONTAINER_NAME'."
  fi
else
  docker run -d \
    --name "$CONTAINER_NAME" \
    --env-file "$ENV_FILE" \
    -e SPLASH_LINKS_DB=/app/data/splash-links/links.sqlite \
    -p "$UI_PORT:5173" \
    -p "$AGENT_PORT:8090" \
    -p "$SPLASH_PORT:8081" \
    -v "$ROOT_DIR/storage:/app/storage" \
    -v "$RUNS_DIR:/app/runs" \
    -v "$DATA_DIR:/app/data/splash-links" \
    "$IMAGE_NAME" >/dev/null
  echo "Started FAIR2WISE container '$CONTAINER_NAME'."
fi

echo "UI:           http://localhost:$UI_PORT"
echo "Agent API:    http://localhost:$AGENT_PORT"
echo "Splash Links: http://localhost:$SPLASH_PORT"
echo "Logs:         docker logs -f $CONTAINER_NAME"
