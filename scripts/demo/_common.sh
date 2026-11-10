# Shared helpers of the scripts/demo/ tools. Sourced, never run. bash 3.2 compatible (macOS).
# Nothing here prints an environment variable or reads a dotenv file.

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$DEMO_DIR/.." && pwd)"

NAME="${F2W_CONTAINER_NAME:-f2w-local}"
PORT="${F2W_HOST_PORT:-8080}"
STATE_VOL="${F2W_STATE_VOLUME:-f2w-local-state}"
TILED_VOL="${F2W_TILED_VOLUME:-f2w-local-tiled}"
BASE="http://127.0.0.1:${PORT}/fair2wise"
API="${BASE}/api"
TILED_TARGET="http://127.0.0.1:8765"
STORAGE_KG="/var/lib/fair2wise/state/session/storage_kg"
DEFAULT_IMAGE="f2w:local"

# Docker Desktop and Homebrew's docker are not always on a non-login shell's PATH.
if [ -z "${F2W_DEMO_NO_PATH_EXTEND:-}" ]; then   # tests set it so only their stub PATH is searched
  case ":$PATH:" in *:/opt/homebrew/bin:*) ;; *) [ ! -d /opt/homebrew/bin ] || PATH="$PATH:/opt/homebrew/bin" ;; esac
  case ":$PATH:" in *:/usr/local/bin:*) ;; *) PATH="$PATH:/usr/local/bin" ;; esac
  export PATH
fi

demo_info() { printf '==> %s\n' "$*"; }
demo_warn() { printf 'WARNING: %s\n' "$*" >&2; }
demo_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

demo_need() {  # demo_need cmd...  : every command must exist
  local c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || demo_die "required command not found: $c"; done
}

demo_docker_ok() { docker info >/dev/null 2>&1; }

demo_sha256() {  # demo_sha256 FILE -> hex digest
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else demo_die "neither shasum nor sha256sum found"; fi
}

demo_container_exists() { docker inspect "$NAME" >/dev/null 2>&1; }
demo_container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = "true" ]; }
demo_volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }

demo_confirm_typed() {  # demo_confirm_typed WORD : the operator must type WORD on stdin (no bypass flag)
  local word="$1" ans=""
  printf 'Type "%s" and press Enter to continue (anything else aborts): ' "$word" >&2
  IFS= read -r ans || ans=""
  [ "$ans" = "$word" ] || demo_die "confirmation not given; nothing was changed"
}

demo_wait_healthy() {  # demo_wait_healthy [seconds]: container health, then the API health route
  local limit="${1:-240}" waited=0 status=""
  demo_info "waiting for ${NAME} to become healthy (up to ${limit}s; the first start takes about two minutes)"
  while [ "$waited" -lt "$limit" ]; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      if curl -fsS --max-time 5 -o /dev/null "${API}/health" 2>/dev/null; then return 0; fi
    fi
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)" = "true" ] || demo_die "container ${NAME} is not running (docker logs ${NAME})"
    sleep 3; waited=$((waited + 3))
  done
  demo_die "container ${NAME} did not become healthy in ${limit}s (last status: ${status}); see: docker logs ${NAME}"
}

# JSON from stdin, parsed by the image's own python (the Mac need not have python3).
demo_json_py() { docker exec -i "$NAME" python -I -c "$1"; }

# Run a command against a named volume in a throwaway container. The volume is mounted at /v, the host
# directory at /b. Uses alpine (F2W_BACKUP_IMAGE, default alpine:3) when it is present locally;
# otherwise falls back to the app image (as root, with the command as the entrypoint) so an offline
# Mac still works.   demo_vol_run VOLUME ro|rw HOSTDIR command args...   (command: tar, ls, find)
demo_vol_run() {
  local vol="$1" mode="$2" hostdir="$3"; shift 3
  local img="${F2W_BACKUP_IMAGE:-alpine:3}" vmount="$vol:/v"
  [ "$mode" = "ro" ] && vmount="$vol:/v:ro"
  case "$hostdir" in /*) ;; *) demo_die "backup directory must be an absolute path: $hostdir" ;; esac
  if docker image inspect "$img" >/dev/null 2>&1; then
    docker run --rm -v "$vmount" -v "$hostdir:/b" "$img" "$@"
    return
  fi
  local app="${F2W_IMAGE:-$(docker inspect -f '{{.Config.Image}}' "$NAME" 2>/dev/null || true)}"
  [ -n "$app" ] || app="$DEFAULT_IMAGE"
  if docker image inspect "$app" >/dev/null 2>&1; then
    demo_warn "image ${img} is not present locally; using ${app} (as root, entrypoint $1) instead"
    local cmd="$1"; shift
    docker run --rm --user 0 --entrypoint "$cmd" -v "$vmount" -v "$hostdir:/b" "$app" "$@"
    return
  fi
  demo_die "neither ${img} nor ${app} is present locally; run 'docker pull alpine:3' while online (T-1 day checklist)"
}
demo_tar_run() { local v="$1" m="$2" d="$3"; shift 3; demo_vol_run "$v" "$m" "$d" tar "$@"; }

demo_vol_nonempty() {  # demo_vol_nonempty VOLUME HOSTDIR
  local out
  out="$(demo_vol_run "$1" ro "$2" ls -A /v)" || demo_die "cannot inspect volume $1 (is Docker healthy?); refusing to guess"
  [ -n "$out" ]
}

demo_timestamp() { date +%Y%m%d-%H%M%S; }
