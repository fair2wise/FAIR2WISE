#!/usr/bin/env bash
# Start the :local image on 127.0.0.1:8080 with CBORG_API_KEY taken from a dotenv file.
#
# Only CBORG_API_KEY is read from the file (never the rest of it, never printed, never in
# argv: `docker run -e NAME` takes the value from this process's environment). The value is
# stored in the container configuration, so `docker inspect` shows it to the Docker user.
#
# Usage: scripts/run_local_container.sh [image] [dotenv-file]
#   env: OPENALEX_API_KEY (passed through when exported; not read from the dotenv file),
#        F2W_KG_RELEASE_DIR (where KG exports are written; default on the state volume),
#        F2W_CONTAINER_NAME (f2w-local), F2W_HOST_PORT (8080),
#        F2W_PAPERS_DIR (the repo's papers/ folder): mounted read-only at /data/papers and
#        offered as the one folder root (F2W_FOLDER_ROOTS) of the folder routes.
#        F2W_LIBRARY_HOST_DIR (default: the same papers/ folder): mounted read-write at
#        /var/lib/fair2wise/library, the Library tab's folder (uploads and the Harvester write
#        there, filing papers by category and year and updating each category's manifest.json).
set -euo pipefail

image="${1:-f2w:local}"
envfile="${2:-$(cd "$(dirname "$0")/.." && pwd)/.env}"
name="${F2W_CONTAINER_NAME:-f2w-local}"
port="${F2W_HOST_PORT:-8080}"
papers="${F2W_PAPERS_DIR:-$(cd "$(dirname "$0")/.." && pwd)/papers}"
library="${F2W_LIBRARY_HOST_DIR:-$papers}"
[ -d "$library" ] || { echo "library folder not found: $library" >&2; exit 1; }
[ -d "$papers" ] || { echo "papers folder not found: $papers" >&2; exit 1; }

[ -f "$envfile" ] || { echo "dotenv file not found: $envfile" >&2; exit 1; }
line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?CBORG_API_KEY=' "$envfile" | tail -n 1 || true)"
value="${line#*=}"
value="${value%%[[:space:]]#*}"                       # trailing inline comment
value="${value%"${value##*[![:space:]]}"}"            # trailing whitespace
case "$value" in \"*\") value="${value#\"}"; value="${value%\"}";; \'*\') value="${value#\'}"; value="${value%\'}";; esac
[ -n "$value" ] || { echo "CBORG_API_KEY is empty or missing in $envfile" >&2; exit 1; }
export CBORG_API_KEY="$value"
unset line value

# OpenAlex key: only if already exported by the caller (the dotenv file is never read for it).
openalex_args=()
[ -z "${OPENALEX_API_KEY:-}" ] || openalex_args=(-e OPENALEX_API_KEY)

docker volume create f2w-local-state >/dev/null
docker volume create f2w-local-tiled >/dev/null
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" --stop-timeout 120 \
  -p "127.0.0.1:${port}:8080" \
  -e CBORG_API_KEY \
  -e F2W_FOLDER_ROOTS=/data/papers \
  -e "F2W_KG_RELEASE_DIR=${F2W_KG_RELEASE_DIR:-/var/lib/fair2wise/state/kg_releases}" \
  ${openalex_args[@]+"${openalex_args[@]}"} \
  -v "$papers":/data/papers:ro \
  -v "$library":/var/lib/fair2wise/library \
  -v f2w-local-state:/var/lib/fair2wise/state \
  -v f2w-local-tiled:/var/lib/fair2wise/tiled \
  "$image"

# The Library's KG export needs its release directory to exist (the app never creates it).
release_dir="${F2W_KG_RELEASE_DIR:-/var/lib/fair2wise/state/kg_releases}"
docker exec "$name" mkdir -p "$release_dir" >/dev/null 2>&1 || true
