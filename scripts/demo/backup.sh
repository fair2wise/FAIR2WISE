#!/usr/bin/env bash
# Back up the two demo volumes (state and Tiled) to tar files in a timestamped directory.
#
# Usage: scripts/demo/backup.sh [--out DIR] [--no-restart] [--hot]
#   --out DIR      backup directory (default: $F2W_DEMO_BACKUP_ROOT or ~/f2w-demo-backups, then a timestamp)
#   --no-restart   leave the container stopped afterwards (default: it is started again if it was running)
#   --hot          do not stop the container (the copy may be inconsistent: SQLite is being written)
# Writes DIR/state.tar, DIR/tiled.tar, DIR/SHA256SUMS and DIR/meta.txt. Volumes are mounted read-only.
# Environment: F2W_CONTAINER_NAME, F2W_STATE_VOLUME, F2W_TILED_VOLUME (defaults f2w-local-state,
# f2w-local-tiled), F2W_BACKUP_IMAGE (default alpine:3; if absent the app image is used instead).
# The same mechanism as restore.sh: docker run --rm -v <volume>:/v:ro -v <dir>:/b alpine tar ...
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }
OUT=""; RESTART=1; HOT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --out) [ "$#" -ge 2 ] || demo_die "--out needs a directory"; OUT="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    --hot) HOT=1; shift ;;
    *) usage >&2; demo_die "unknown argument: $1" ;;
  esac
done
demo_need docker
demo_docker_ok || demo_die "docker is not running"
[ -n "$OUT" ] || OUT="${F2W_DEMO_BACKUP_ROOT:-$HOME/f2w-demo-backups}/$(demo_timestamp)"
case "$OUT" in /*) ;; *) OUT="$(pwd)/$OUT" ;; esac
for v in "$STATE_VOL" "$TILED_VOL"; do demo_volume_exists "$v" || demo_die "volume not found: $v"; done
[ ! -e "$OUT/state.tar" ] || demo_die "$OUT already holds a backup; choose another --out"
mkdir -p "$OUT"

was_running=0
if demo_container_running; then
  was_running=1
  if [ "$HOT" -eq 0 ]; then demo_info "stopping ${NAME} for a consistent copy (up to 2 minutes)"; docker stop -t 120 "$NAME" >/dev/null
  else demo_warn "--hot: copying while ${NAME} runs; the backup may be inconsistent"; fi
fi

demo_info "backing up ${STATE_VOL} -> ${OUT}/state.tar"
demo_tar_run "$STATE_VOL" ro "$OUT" -C /v -cpf /b/state.tar .
demo_info "backing up ${TILED_VOL} -> ${OUT}/tiled.tar"
demo_tar_run "$TILED_VOL" ro "$OUT" -C /v -cpf /b/tiled.tar .
[ -s "$OUT/state.tar" ] && [ -s "$OUT/tiled.tar" ] || demo_die "a backup file is missing or empty in $OUT"
( cd "$OUT" && for f in state.tar tiled.tar; do printf '%s  %s\n' "$(demo_sha256 "$f")" "$f"; done > SHA256SUMS )
{
  printf 'created: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  printf 'state_volume: %s\ntiled_volume: %s\ncontainer: %s\n' "$STATE_VOL" "$TILED_VOL" "$NAME"
  printf 'image: %s\n' "$(docker inspect -f '{{.Config.Image}}' "$NAME" 2>/dev/null || echo unknown)"
  printf 'hot: %s\n' "$HOT"
} > "$OUT/meta.txt"

if [ "$was_running" -eq 1 ] && [ "$HOT" -eq 0 ] && [ "$RESTART" -eq 1 ]; then
  demo_info "starting ${NAME} again"; docker start "$NAME" >/dev/null; demo_wait_healthy 300
fi
demo_info "backup complete: $OUT"
ls -l "$OUT" | sed 's/^/    /'
