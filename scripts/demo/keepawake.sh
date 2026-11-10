#!/usr/bin/env bash
# Keep the Mac awake for the length of the demo (wraps `caffeinate -dimsu`).
#
# Usage: scripts/demo/keepawake.sh start [--hours N]    (default 12 hours)
#        scripts/demo/keepawake.sh stop
#        scripts/demo/keepawake.sh status
# Why: a suspended machine broke a job lease once (the worker's heartbeat stopped, the job was
# declared lost and the extraction had to be redone). A sleeping Mac also drops VPN, so CBORG calls
# fail. caffeinate flags: -d display, -i idle, -m disk, -s system (on AC power only), -u user active.
# macOS requires a timeout with -u, so a timeout is always passed (--hours); run `start` again to extend.
# The process id is kept in $F2W_DEMO_STATE_DIR (default ~/.f2w-demo)/keepawake.pid. Stop it when the
# demo is over: `stop` (or log out / reboot, which ends it as well).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }
STATE_DIR="${F2W_DEMO_STATE_DIR:-$HOME/.f2w-demo}"
PIDFILE="$STATE_DIR/keepawake.pid"
cmd="${1:-}"; [ "$#" -eq 0 ] || shift
HOURS=12
case "$cmd" in -h|--help|"") usage; [ -n "$cmd" ] && exit 0 || exit 2 ;; esac
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --hours) [ "$#" -ge 2 ] || demo_die "--hours needs a number"; HOURS="$2"; shift 2 ;;
    *) usage >&2; demo_die "unknown argument: $1" ;;
  esac
done
case "$HOURS" in ''|*[!0-9]*) demo_die "--hours must be a whole number" ;; esac
[ "$HOURS" -ge 1 ] || demo_die "--hours must be at least 1"

is_ours() {  # pid alive and really a caffeinate process
  local pid="$1" comm
  kill -0 "$pid" 2>/dev/null || return 1
  comm="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
  case "$comm" in *caffeinate*) return 0 ;; *) return 1 ;; esac
}

case "$cmd" in
  start)
    demo_need caffeinate
    if [ -f "$PIDFILE" ] && is_ours "$(cat "$PIDFILE")"; then
      demo_info "already running (pid $(cat "$PIDFILE")); run stop first to change the duration"; exit 0
    fi
    mkdir -p "$STATE_DIR"
    nohup caffeinate -dimsu -t $((HOURS * 3600)) >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    is_ours "$(cat "$PIDFILE")" || { rm -f "$PIDFILE"; demo_die "caffeinate did not start"; }
    demo_info "keeping the Mac awake for ${HOURS} hours (pid $(cat "$PIDFILE")). Stop with: scripts/demo/keepawake.sh stop"
    ;;
  stop)
    if [ -f "$PIDFILE" ] && is_ours "$(cat "$PIDFILE")"; then
      kill "$(cat "$PIDFILE")"; rm -f "$PIDFILE"; demo_info "stopped; the Mac may sleep again"
    else
      rm -f "$PIDFILE"; demo_info "not running"
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && is_ours "$(cat "$PIDFILE")"; then echo "keep-awake: ON (pid $(cat "$PIDFILE"))"; else echo "keep-awake: OFF"; exit 1; fi
    ;;
  *) usage >&2; demo_die "unknown command: $cmd" ;;
esac
