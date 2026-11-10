#!/usr/bin/env bash
# Install the :local demo image from a docker-save bundle and start it.
#
# Usage: scripts/demo/install.sh <image-tar> [--checksum <sha256-file>] [--fresh] [--env-file <dotenv>]
#                                [--image <tag>] [--no-start]
#   <image-tar>            the bundle (docker save, plain or already decompressed .tar)
#   --checksum FILE        file holding the expected SHA-256 (either "<hex>  <name>" or just "<hex>");
#                          the install stops on a mismatch. Without it the computed value is printed
#                          and you must compare it yourself.
#   --fresh                the two demo volumes may exist already (f2w-local-state, f2w-local-tiled).
#                          Without this flag an existing volume stops the install: nothing is
#                          overwritten. With it, both are BACKED UP first (scripts/demo/backup.sh), then
#                          removed after you type "delete".
#   --env-file FILE        dotenv with CBORG_API_KEY (default: <repo>/.env); only that one line is read,
#                          by scripts/run_local_container.sh
#   --image TAG            image reference to run when the bundle holds several (default: the one loaded)
#   --no-start             load and prepare volumes, do not start the container
# Environment: F2W_CONTAINER_NAME, F2W_HOST_PORT, F2W_PAPERS_DIR as in scripts/run_local_container.sh.
# Never prints the API key. Starts the container with scripts/run_local_container.sh and waits until healthy.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; }
TAR=""; SUMFILE=""; FRESH=0; ENVFILE=""; IMAGE=""; START=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --checksum) [ "$#" -ge 2 ] || demo_die "--checksum needs a file"; SUMFILE="$2"; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    --env-file) [ "$#" -ge 2 ] || demo_die "--env-file needs a file"; ENVFILE="$2"; shift 2 ;;
    --image) [ "$#" -ge 2 ] || demo_die "--image needs a tag"; IMAGE="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    -*) usage >&2; demo_die "unknown option: $1" ;;
    *) [ -z "$TAR" ] || demo_die "only one image tar is accepted"; TAR="$1"; shift ;;
  esac
done
[ -n "$TAR" ] || { usage >&2; exit 2; }
[ -f "$TAR" ] || demo_die "image tar not found: $TAR"
demo_need docker curl
demo_docker_ok || demo_die "docker is not running (start Docker Desktop, or: colima start --cpu 4 --memory 8 --disk 100)"

# 1. checksum
actual="$(demo_sha256 "$TAR")"
if [ -n "$SUMFILE" ]; then
  [ -f "$SUMFILE" ] || demo_die "checksum file not found: $SUMFILE"
  expected="$(awk 'NF{print tolower($1); exit}' "$SUMFILE")"
  case "$expected" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
    *) demo_die "checksum file does not start with a hex digest: $SUMFILE" ;;
  esac
  [ "${#expected}" -eq 64 ] || demo_die "checksum file does not hold a 64-hex SHA-256: $SUMFILE"
  if [ "$actual" != "$expected" ]; then
    demo_die "checksum MISMATCH for $TAR (expected ${expected}, got ${actual}); the copy is damaged or not the intended bundle. Nothing was loaded."
  fi
  demo_info "checksum ok (${actual})"
else
  demo_warn "no --checksum given: sha256 of the bundle is ${actual}; compare it with the one you were sent"
fi

# 2. volumes: decide before loading several GB
existing=""
demo_volume_exists "$STATE_VOL" && existing="$existing $STATE_VOL"
demo_volume_exists "$TILED_VOL" && existing="$existing $TILED_VOL"
if [ -n "$existing" ] && [ "$FRESH" -ne 1 ]; then
  demo_die "demo volume(s) already exist:${existing}. Refusing to overwrite them. To keep the data run scripts/demo/backup.sh first; to start over run this again with --fresh (it backs them up, then asks for confirmation)."
fi
if [ -n "$existing" ] && [ "$FRESH" -eq 1 ]; then
  demo_info "--fresh: backing up the existing volumes first"
  bdir="${F2W_DEMO_BACKUP_ROOT:-$HOME/f2w-demo-backups}/$(demo_timestamp)-before-install"
  "$DEMO_DIR/backup.sh" --out "$bdir" --no-restart
  printf 'Backup written to %s\nAbout to DELETE container %s and volumes:%s\n' "$bdir" "$NAME" "$existing" >&2
  demo_confirm_typed delete
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  for v in $existing; do docker volume rm "$v" >/dev/null; done
fi

# 3. load
demo_info "docker load (this takes a few minutes)"
loaded="$(docker load -i "$TAR")"
printf '%s\n' "$loaded"
ref="$(printf '%s\n' "$loaded" | sed -n 's/^Loaded image: //p' | tail -n 1)"
[ -n "$IMAGE" ] || IMAGE="${ref:-$DEFAULT_IMAGE}"
docker image inspect "$IMAGE" >/dev/null 2>&1 || demo_die "image $IMAGE is not present after docker load; pass --image <tag>"
img_arch="$(docker image inspect -f '{{.Architecture}}' "$IMAGE" 2>/dev/null || echo ?)"
demo_info "image ${IMAGE}: architecture ${img_arch}, revision $(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE" 2>/dev/null || echo unknown)"
case "$img_arch" in arm64|aarch64) ;; *) demo_warn "image architecture is ${img_arch}; on an Apple-silicon Mac it will run emulated (slow) or not at all" ;; esac

# 4. volumes, only if absent
for v in "$STATE_VOL" "$TILED_VOL"; do
  if demo_volume_exists "$v"; then demo_info "volume $v exists (kept)"; else docker volume create "$v" >/dev/null; demo_info "volume $v created"; fi
done
[ "$START" -eq 1 ] || { demo_info "--no-start: stopping here"; exit 0; }

# 5. start through the documented script
if [ -z "${F2W_PAPERS_DIR:-}" ]; then
  default_papers="$(cd "$SCRIPTS_DIR/.." && pwd)/papers"
  [ -d "$default_papers" ] || { mkdir -p "$default_papers"; demo_info "created empty papers folder $default_papers"; }
fi
demo_info "starting ${NAME}"
if [ -n "$ENVFILE" ]; then "$SCRIPTS_DIR/run_local_container.sh" "$IMAGE" "$ENVFILE"; else "$SCRIPTS_DIR/run_local_container.sh" "$IMAGE"; fi
demo_wait_healthy 300
printf '\nThe demo is up.\n  Open: %s/\n  Next: scripts/demo/seed.sh   then   scripts/demo/smoke.sh\n' "$BASE"
