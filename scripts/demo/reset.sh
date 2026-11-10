#!/usr/bin/env bash
# Return the demo to a clean, known state: back up both volumes, delete them, start an empty container.
#
# Usage: scripts/demo/reset.sh [--env-file <dotenv>] [--image <tag>] [--out <backup-dir>] [--no-start]
#   --env-file FILE   dotenv with CBORG_API_KEY for scripts/run_local_container.sh (default: <repo>/.env)
#   --image TAG       image to start (default: the image of the current container, else f2w:local)
#   --out DIR         where the backup goes (default: ~/f2w-demo-backups/<timestamp>-before-reset)
#   --no-start        stop after the volumes are recreated (empty); do not start a container
# Order: (1) stop the container, (2) back up the state and Tiled volumes to tar files and verify them,
# (3) ask you to type "reset", (4) only then remove the container and both volumes, (5) recreate
# them empty and start the container, (6) wait until healthy. There is no flag that skips the typed
# confirmation. A failed backup stops everything before anything is removed.
# After a reset run scripts/demo/seed.sh to put the demo corpora back.
# Environment: F2W_CONTAINER_NAME, F2W_HOST_PORT, F2W_STATE_VOLUME, F2W_TILED_VOLUME, F2W_DEMO_BACKUP_ROOT.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
ENVFILE=""; IMAGE=""; OUT=""; START=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --env-file) [ "$#" -ge 2 ] || demo_die "--env-file needs a file"; ENVFILE="$2"; shift 2 ;;
    --image) [ "$#" -ge 2 ] || demo_die "--image needs a tag"; IMAGE="$2"; shift 2 ;;
    --out) [ "$#" -ge 2 ] || demo_die "--out needs a directory"; OUT="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    *) usage >&2; demo_die "unknown argument: $1" ;;
  esac
done
demo_need docker curl
demo_docker_ok || demo_die "docker is not running"
if [ -z "$IMAGE" ]; then
  IMAGE="$(docker inspect -f '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"
  [ -n "$IMAGE" ] || IMAGE="$DEFAULT_IMAGE"
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || demo_die "image $IMAGE is not present; load it with install.sh first (or pass --image)"
[ -n "$OUT" ] || OUT="${F2W_DEMO_BACKUP_ROOT:-$HOME/f2w-demo-backups}/$(demo_timestamp)-before-reset"

printf 'RESET of the demo.\n  container: %s\n  volumes:   %s, %s\n  image:     %s\n  backup to: %s\n' "$NAME" "$STATE_VOL" "$TILED_VOL" "$IMAGE" "$OUT" >&2
for v in "$STATE_VOL" "$TILED_VOL"; do demo_volume_exists "$v" || demo_warn "volume $v does not exist yet (nothing to back up for it)"; done

if demo_volume_exists "$STATE_VOL" && demo_volume_exists "$TILED_VOL"; then
  "$DEMO_DIR/backup.sh" --out "$OUT" --no-restart
  for f in state.tar tiled.tar SHA256SUMS; do [ -s "$OUT/$f" ] || demo_die "backup incomplete ($f missing); nothing was removed"; done
else
  demo_warn "skipping the backup because a volume is missing"
fi

printf '\nThe backup above is verified. Resetting DELETES container %s and volumes %s and %s.\nRestore later with: scripts/demo/restore.sh %s\n' "$NAME" "$STATE_VOL" "$TILED_VOL" "$OUT" >&2
demo_confirm_typed reset

demo_container_exists && { demo_info "removing container ${NAME}"; docker rm -f "$NAME" >/dev/null; }
for v in "$STATE_VOL" "$TILED_VOL"; do
  if demo_volume_exists "$v"; then docker volume rm "$v" >/dev/null; demo_info "volume $v removed"; fi
  docker volume create "$v" >/dev/null; demo_info "volume $v recreated (empty)"
done
[ "$START" -eq 1 ] || { demo_info "--no-start: done; the volumes are empty"; exit 0; }

demo_info "starting a fresh ${NAME}"
if [ -n "$ENVFILE" ]; then "$SCRIPTS_DIR/run_local_container.sh" "$IMAGE" "$ENVFILE"; else "$SCRIPTS_DIR/run_local_container.sh" "$IMAGE"; fi
demo_wait_healthy 300
printf '\nClean state reached. Next: scripts/demo/seed.sh   then   scripts/demo/smoke.sh\n'
