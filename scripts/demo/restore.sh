#!/usr/bin/env bash
# Restore the demo volumes from a backup directory made by backup.sh.
#
# Usage: scripts/demo/restore.sh <backup-dir> [--replace] [--no-start]
#   <backup-dir>   directory holding state.tar, tiled.tar and SHA256SUMS
#   --replace      the target volumes may hold data: it is deleted (after you type "restore") before
#                  the tar files are unpacked. Without it a non-empty volume stops the restore.
#   --no-start     do not start the container afterwards (default: started if it exists)
# The checksums are verified first. The container is stopped while the volumes are written.
# Environment: F2W_CONTAINER_NAME, F2W_STATE_VOLUME, F2W_TILED_VOLUME, F2W_BACKUP_IMAGE as in backup.sh.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; }
DIR=""; REPLACE=0; START=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --replace) REPLACE=1; shift ;;
    --no-start) START=0; shift ;;
    -*) usage >&2; demo_die "unknown option: $1" ;;
    *) [ -z "$DIR" ] || demo_die "only one backup directory is accepted"; DIR="$1"; shift ;;
  esac
done
[ -n "$DIR" ] || { usage >&2; exit 2; }
[ -d "$DIR" ] || demo_die "backup directory not found: $DIR"
DIR="$(cd "$DIR" && pwd)"
for f in state.tar tiled.tar SHA256SUMS; do [ -f "$DIR/$f" ] || demo_die "$DIR/$f is missing"; done
demo_need docker
demo_docker_ok || demo_die "docker is not running"

demo_info "verifying checksums"
while read -r sum file; do
  [ -n "$sum" ] || continue
  case "$file" in state.tar|tiled.tar) ;; *) demo_die "unexpected entry in SHA256SUMS: $file" ;; esac
  [ "$(demo_sha256 "$DIR/$file")" = "$sum" ] || demo_die "checksum mismatch for $DIR/$file; the backup is damaged"
done < "$DIR/SHA256SUMS"

nonempty=""
for v in "$STATE_VOL" "$TILED_VOL"; do
  if demo_volume_exists "$v" && demo_vol_nonempty "$v" "$DIR"; then nonempty="$nonempty $v"; fi
done
if [ -n "$nonempty" ] && [ "$REPLACE" -ne 1 ]; then
  demo_die "volume(s) not empty:${nonempty}. Refusing to overwrite. Use --replace to delete their contents first (a backup of them is your own responsibility: run backup.sh)."
fi
if [ -n "$nonempty" ]; then
  printf 'About to DELETE the contents of:%s and restore from %s\n' "$nonempty" "$DIR" >&2
  demo_confirm_typed restore
fi

had_container=0
if demo_container_exists; then had_container=1; demo_info "stopping ${NAME}"; docker stop -t 120 "$NAME" >/dev/null 2>&1 || true; fi
for v in "$STATE_VOL" "$TILED_VOL"; do demo_volume_exists "$v" || docker volume create "$v" >/dev/null; done
if [ -n "$nonempty" ]; then
  for v in $nonempty; do
    demo_info "emptying $v"
    demo_vol_run "$v" rw "$DIR" find /v -mindepth 1 -delete
  done
fi
demo_info "unpacking state.tar -> ${STATE_VOL}"
demo_tar_run "$STATE_VOL" rw "$DIR" -C /v -xpf /b/state.tar
demo_info "unpacking tiled.tar -> ${TILED_VOL}"
demo_tar_run "$TILED_VOL" rw "$DIR" -C /v -xpf /b/tiled.tar
demo_info "restore complete"
if [ "$had_container" -eq 1 ] && [ "$START" -eq 1 ]; then
  docker start "$NAME" >/dev/null; demo_wait_healthy 300
  printf 'Running again at %s/\n' "$BASE"
elif [ "$had_container" -eq 0 ]; then
  printf 'No container named %s exists; start one with scripts/run_local_container.sh <image>\n' "$NAME"
fi
