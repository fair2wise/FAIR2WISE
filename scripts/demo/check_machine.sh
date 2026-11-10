#!/usr/bin/env bash
# Read-only report about the machine that will run the :local demo. Run it on the demo Mac and paste
# the whole output back.
#
# Usage: scripts/demo/check_machine.sh [image-tar]
#   image-tar   optional path of the docker-save bundle; its SHA-256 is printed so it can be compared
#               with the checksum that came with it.
# Exit code: 0 when the checklist has no FAIL (WARN is allowed), 1 otherwise.
#
# STRICTLY READ-ONLY: it only runs commands that report state (sw_vers, uname, sysctl, df, pmset -g,
# docker info/version/ps/system df, colima status/list, lsof, date) and plain HTTPS GET requests with
# --max-time 8 (nothing is sent but the GET itself: no credentials, no body). It writes no file (no
# temporary files either), starts and stops nothing, pulls nothing, and prints no environment variable.
set -euo pipefail

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
[ "$#" -le 1 ] || { usage >&2; exit 2; }
IMAGE_TAR="${1:-}"

if [ -z "${F2W_DEMO_NO_PATH_EXTEND:-}" ]; then   # tests set it so only their stub PATH is searched
  case ":$PATH:" in *:/opt/homebrew/bin:*) ;; *) [ ! -d /opt/homebrew/bin ] || PATH="$PATH:/opt/homebrew/bin" ;; esac
  case ":$PATH:" in *:/usr/local/bin:*) ;; *) PATH="$PATH:/usr/local/bin" ;; esac
  export PATH
fi

have() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n== %s ==\n' "$*"; }
CHECKS=""
add_check() { CHECKS="${CHECKS}$1|$2|$3
"; }   # LEVEL|requirement|detail

NEED_DISK_GIB=40
NEED_DOCKER_RAM_MIB=7500   # a VM given 8 GiB reports about 7.7 GiB as MemTotal

printf 'F2W demo machine check (read-only)\n'
printf 'generated: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"

# ---------------------------------------------------------------- system
section "System"
OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
ARCH="$(uname -m 2>/dev/null || echo unknown)"
if have sw_vers; then
  printf 'macOS: %s (build %s)\n' "$(sw_vers -productVersion 2>/dev/null || echo ?)" "$(sw_vers -buildVersion 2>/dev/null || echo ?)"
else
  printf 'OS: %s (sw_vers not available: this is not macOS)\n' "$OS_NAME"
fi
printf 'architecture (uname -m): %s\n' "$ARCH"
TRANSLATED=""
if have sysctl; then TRANSLATED="$(sysctl -n sysctl.proc_translated 2>/dev/null || true)"; fi
[ "$TRANSLATED" != "1" ] || printf 'NOTE: this shell runs under Rosetta (x86_64 translation); the real CPU is Apple silicon\n'
CPU="unknown"; NCPU="?"; MEM_GIB="?"
if have sysctl; then
  CPU="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
  NCPU="$(sysctl -n hw.ncpu 2>/dev/null || echo '?')"
  MEMB="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
  [ "$MEMB" -gt 0 ] 2>/dev/null && MEM_GIB=$((MEMB / 1073741824))
elif [ -r /proc/cpuinfo ]; then
  NCPU="$(grep -c '^processor' /proc/cpuinfo || echo '?')"
fi
printf 'CPU: %s, %s logical cores\n' "$CPU" "$NCPU"
printf 'RAM: %s GiB\n' "$MEM_GIB"
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
  [ "$TRANSLATED" = "1" ] && add_check WARN "arm64" "shell is translated by Rosetta; open a native Terminal" \
    || add_check PASS "arm64" "native arm64"
else
  [ "$TRANSLATED" = "1" ] && add_check WARN "arm64" "uname says $ARCH because of Rosetta" || add_check FAIL "arm64" "architecture is $ARCH; the demo image is built for linux/arm64"
fi

# ---------------------------------------------------------------- docker engine
section "Docker engine"
HAVE_DOCKER=0; DOCKER_UP=0
DOCKER_MEM_MIB=""
printf 'docker CLI: %s\n' "$(command -v docker 2>/dev/null || echo 'not installed')"
printf 'colima:     %s\n' "$(command -v colima 2>/dev/null || echo 'not installed')"
if [ -d /Applications/Docker.app ]; then printf 'Docker Desktop app: installed (/Applications/Docker.app)\n'; else printf 'Docker Desktop app: not found in /Applications\n'; fi
DESKTOP_RUNNING="no"
if have pgrep && pgrep -f "Docker Desktop" >/dev/null 2>&1; then DESKTOP_RUNNING="yes"; fi
printf 'Docker Desktop running: %s\n' "$DESKTOP_RUNNING"
CTX=""
if have docker; then
  HAVE_DOCKER=1
  CTX="$(docker context show 2>/dev/null || true)"
  printf 'docker context: %s\n' "${CTX:-unknown}"
  if docker info >/dev/null 2>&1; then
    DOCKER_UP=1
    printf 'docker client/server version: %s / %s\n' \
      "$(docker version --format '{{.Client.Version}}' 2>/dev/null || echo ?)" \
      "$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo ?)"
    INFO="$(docker info --format '{{.NCPU}} {{.MemTotal}} {{.Architecture}} {{.OSType}}' 2>/dev/null || true)"
    set -- $INFO
    D_CPU="${1:-?}"; D_MEMB="${2:-0}"; D_ARCH="${3:-?}"; D_OS="${4:-?}"
    DOCKER_MEM_MIB=$((D_MEMB / 1048576))
    printf 'docker VM/engine limits: %s CPUs, %s MiB memory, arch %s, os %s\n' "$D_CPU" "$DOCKER_MEM_MIB" "$D_ARCH" "$D_OS"
    printf '\ndocker system df:\n'; docker system df 2>&1 | sed 's/^/  /' || true
    printf '\nrunning containers:\n'; docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}' 2>&1 || true
    printf '\nf2w images and volumes present:\n'
    docker image ls --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null | grep -i 'f2w\|fair2wise' || printf '  (no f2w image)\n'
    docker volume ls --format '  {{.Name}}' 2>/dev/null | grep 'f2w' || printf '  (no f2w volumes)\n'
    case "$D_ARCH" in aarch64|arm64) add_check PASS "docker engine arch" "$D_ARCH" ;; *) add_check FAIL "docker engine arch" "$D_ARCH (needs arm64/aarch64)" ;; esac
  else
    printf 'docker daemon: NOT reachable (docker info failed)\n'
  fi
else
  printf 'docker: not installed\n'
fi
if have colima; then
  printf '\ncolima status:\n'; colima status 2>&1 | sed 's/^/  /' || true
  printf '\ncolima list (cpus / memory / disk are the configured limits):\n'; colima list 2>&1 | sed 's/^/  /' || true
fi
if [ "$HAVE_DOCKER" = 0 ]; then
  add_check FAIL "docker installed" "no docker CLI found: install Docker Desktop or Colima"
elif [ "$DOCKER_UP" = 0 ]; then
  add_check FAIL "docker running" "docker info failed: start Docker Desktop, or 'colima start --cpu 4 --memory 8 --disk 100'"
else
  add_check PASS "docker running" "engine answers (context ${CTX:-?})"
fi
if [ -n "$DOCKER_MEM_MIB" ]; then
  if [ "$DOCKER_MEM_MIB" -ge "$NEED_DOCKER_RAM_MIB" ]; then add_check PASS ">=8 GiB RAM for Docker" "${DOCKER_MEM_MIB} MiB"
  else add_check FAIL ">=8 GiB RAM for Docker" "${DOCKER_MEM_MIB} MiB; raise the Docker Desktop memory limit or 'colima start --memory 8'"; fi
else
  add_check WARN ">=8 GiB RAM for Docker" "unknown (docker not running); rerun after Docker starts"
fi

# ---------------------------------------------------------------- disk
section "Disk"
DATA_PATH="$HOME"
case "$CTX" in
  *colima*) DATA_PATH="${COLIMA_HOME:-$HOME/.colima}" ;;
  *desktop*) DATA_PATH="$HOME/Library/Containers/com.docker.docker" ;;
  *) if [ -d "${COLIMA_HOME:-$HOME/.colima}" ]; then DATA_PATH="${COLIMA_HOME:-$HOME/.colima}"
     elif [ -d "$HOME/Library/Containers/com.docker.docker" ]; then DATA_PATH="$HOME/Library/Containers/com.docker.docker"; fi ;;
esac
[ -d "$DATA_PATH" ] || DATA_PATH="$HOME"
printf 'path used for the Docker data volume: %s\n' "$DATA_PATH"
DF_LINE="$(df -k "$DATA_PATH" 2>/dev/null | tail -n 1 || true)"
FREE_GIB=""
if [ -n "$DF_LINE" ]; then
  AVAIL_KB="$(printf '%s\n' "$DF_LINE" | awk '{print $4}')"
  MOUNT="$(printf '%s\n' "$DF_LINE" | awk '{print $NF}')"
  if [ "$AVAIL_KB" -ge 0 ] 2>/dev/null; then FREE_GIB=$((AVAIL_KB / 1048576)); fi
  printf 'volume: %s, free: %s GiB\n' "$MOUNT" "${FREE_GIB:-unknown}"
fi
printf 'note: free space INSIDE the Docker VM disk cannot be measured read-only; the VM disk size is in the colima list above (Docker Desktop: Settings > Resources). A full VM disk once stopped builds and writes ("No space left on device").\n'
if [ -z "$FREE_GIB" ]; then add_check WARN ">=${NEED_DISK_GIB} GiB free disk" "could not read df"
elif [ "$FREE_GIB" -ge "$NEED_DISK_GIB" ]; then add_check PASS ">=${NEED_DISK_GIB} GiB free disk" "${FREE_GIB} GiB free on ${MOUNT}"
else add_check FAIL ">=${NEED_DISK_GIB} GiB free disk" "${FREE_GIB} GiB free on ${MOUNT}"; fi

# ---------------------------------------------------------------- power
section "Sleep and power"
SLEEP_MIN="?"
if have pmset; then
  pmset -g 2>&1 | sed 's/^/  /' || true
  SLEEP_MIN="$(pmset -g 2>/dev/null | awk '$1=="sleep"{print $2; exit}' || true)"
  printf '\npower source:\n'; pmset -g batt 2>&1 | sed 's/^/  /' || true
else
  printf 'pmset: not available\n'
fi
if have caffeinate; then printf 'caffeinate: available (%s)\n' "$(command -v caffeinate)"; else printf 'caffeinate: NOT available\n'; fi
if [ "$SLEEP_MIN" = "0" ]; then add_check PASS "system sleep disabled" "sleep 0"
elif [ -z "$SLEEP_MIN" ] || [ "$SLEEP_MIN" = "?" ]; then add_check WARN "system sleep disabled" "unknown; use scripts/demo/keepawake.sh start during the demo"
else add_check WARN "system sleep disabled" "sleep ${SLEEP_MIN} min; run scripts/demo/keepawake.sh start (a suspended machine broke a job lease once)"; fi
have caffeinate && add_check PASS "caffeinate available" "$(command -v caffeinate)" || add_check WARN "caffeinate available" "missing; set Energy settings to never sleep"

# ---------------------------------------------------------------- time
section "Time zone and clock"
printf 'local time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z (%Z)')"
printf 'UTC time:   %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')"
if have readlink && [ -L /etc/localtime ]; then printf 'time zone:  %s\n' "$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"; fi
printf 'The clock is compared with the HTTPS Date header below (when a host answers).\n'

# ---------------------------------------------------------------- ports
section "Ports on this host"
port_state() {
  if have lsof; then
    if lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; then echo busy; else echo free; fi
  elif have netstat; then
    if netstat -an 2>/dev/null | grep -E "[.:]$1[[:space:]].*LISTEN" >/dev/null 2>&1; then echo busy; else echo free; fi
  else echo unknown; fi
}
F2W_RUNNING=0
if [ "$DOCKER_UP" = 1 ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'f2w-local'; then F2W_RUNNING=1; fi
for p in 8080 8765; do
  st="$(port_state "$p")"
  who=""
  if [ "$st" = busy ] && have lsof; then who=" ($(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $1}' | sort -u | tr '\n' ' ' | sed 's/ $//'))"; fi
  printf 'port %s: %s%s\n' "$p" "$st" "$who"
  case "$st" in
    free) add_check PASS "port $p free" "free" ;;
    busy) if [ "$F2W_RUNNING" = 1 ] && [ "$p" = 8080 ]; then add_check WARN "port $p free" "in use by the running f2w-local container (expected after install)"
          elif [ "$p" = 8080 ]; then add_check FAIL "port $p free" "in use${who}; stop it or set F2W_HOST_PORT"
          else add_check WARN "port $p free" "in use${who}; the container uses 8765 only inside itself, so this is harmless unless you run Tiled on the host"; fi ;;
    *) add_check WARN "port $p free" "cannot tell (no lsof or netstat)" ;;
  esac
done

# ---------------------------------------------------------------- network
section "Network (DNS, then one plain HTTPS GET with --max-time 8; status and elapsed seconds only)"
dns_ok() {
  if have dscacheutil; then dscacheutil -q host -a name "$1" 2>/dev/null | grep -q 'ip_address'
  elif have getent; then getent hosts "$1" >/dev/null 2>&1
  elif have nslookup; then nslookup "$1" >/dev/null 2>&1
  else return 2; fi
}
CLOCK_SKEW=""
probe() {  # probe HOST PATH -> P_CODE P_TIME P_BODY P_DATE
  local out="" rc=0 line last
  P_CODE="000"; P_TIME="0"; P_BODY=""; P_DATE=""
  out="$(curl -sS --max-time 8 -w '\n%{http_code} %{time_total}\n%header{date}' "https://$1$2" 2>/dev/null)" || rc=$?
  [ -n "$out" ] || return 0
  P_DATE="${out##*$'\n'}"; out="${out%$'\n'*}"
  line="${out##*$'\n'}"; P_BODY="${out%$'\n'*}"
  [ "$line" != "$out" ] || P_BODY=""
  P_CODE="${line%% *}"; P_TIME="${line#* }"
  case "$P_CODE" in [0-9][0-9][0-9]) ;; *) P_CODE="000" ;; esac
  case "$P_DATE" in *GMT*) ;; *) P_DATE="" ;; esac
  return 0
}
check_host() {  # check_host HOST PATH LABEL
  local host="$1" path="$2" dns="" verdict
  if dns_ok "$host"; then dns="DNS ok"; else rc=$?; [ "${rc:-1}" = 2 ] && dns="DNS not tested" || dns="DNS FAILS"; fi
  if ! have curl; then printf '%-28s %s; curl not installed\n' "$host" "$dns"; add_check WARN "$3 reachable" "curl missing"; return 0; fi
  probe "$host" "$path"
  if [ "$P_CODE" = "000" ]; then verdict="no HTTP answer (timeout or network)"; else verdict="HTTP $P_CODE in ${P_TIME}s"; fi
  if [ "$host" = "api.cborg.lbl.gov" ] && [ "$P_CODE" = "403" ] && printf '%s' "$P_BODY" | grep -qi 'IP address' && printf '%s' "$P_BODY" | grep -qi 'not recognized'; then
    verdict="HTTP 403 in ${P_TIME}s: OFF-NETWORK (CBORG only answers from LBLnet or VPN; your address is not recognized)"
    add_check WARN "$3 reachable" "off LBLnet/VPN: live extraction and QA need it; join the VPN or use the fallback (runbook)"
  elif [ "$P_CODE" = "000" ]; then add_check WARN "$3 reachable" "$dns; no HTTP answer in 8s"
  else add_check PASS "$3 reachable" "HTTP $P_CODE in ${P_TIME}s"; fi
  printf '%-28s %s; %s\n' "$host" "$dns" "$verdict"
  if [ -z "$CLOCK_SKEW" ] && [ -n "$P_DATE" ] && have date; then
    remote="$(date -j -u -f '%a, %d %b %Y %H:%M:%S GMT' "$P_DATE" +%s 2>/dev/null || date -u -d "$P_DATE" +%s 2>/dev/null || true)"
    if [ -n "$remote" ]; then CLOCK_SKEW=$(( $(date -u +%s) - remote )); fi
  fi
}
check_host api.cborg.lbl.gov / "CBORG (api.cborg.lbl.gov)"
check_host export.arxiv.org "/api/query?search_query=all:xray&max_results=1" "arXiv (export.arxiv.org)"
check_host api.openalex.org "/works?per-page=1" "OpenAlex (api.openalex.org)"
check_host tiled-staging.als.lbl.gov /api/v1/ "Tiled staging (stretch goal only)"
if [ -n "$CLOCK_SKEW" ]; then
  printf 'clock skew against a server Date header: %s s (positive = this Mac is ahead)\n' "$CLOCK_SKEW"
  abs="${CLOCK_SKEW#-}"
  if [ "$abs" -le 60 ]; then add_check PASS "clock correct" "within ${abs}s of a server"; else add_check WARN "clock correct" "off by ${CLOCK_SKEW}s; fix Date & Time settings"; fi
else
  add_check WARN "clock correct" "could not compare with a server"
fi

# ---------------------------------------------------------------- checksum
if [ -n "$IMAGE_TAR" ]; then
  section "Image bundle checksum"
  if [ -f "$IMAGE_TAR" ]; then
    printf 'file: %s\nsize: %s bytes\n' "$IMAGE_TAR" "$(wc -c < "$IMAGE_TAR" | tr -d ' ')"
    if have shasum; then SUM="$(shasum -a 256 "$IMAGE_TAR" | awk '{print $1}')"; elif have sha256sum; then SUM="$(sha256sum "$IMAGE_TAR" | awk '{print $1}')"; else SUM=""; fi
    if [ -n "$SUM" ]; then printf 'sha256: %s\n' "$SUM"; add_check PASS "image tar checksum" "computed (compare with the checksum file)"; else add_check WARN "image tar checksum" "no shasum"; fi
  else
    printf 'file not found: %s\n' "$IMAGE_TAR"
    add_check FAIL "image tar checksum" "file not found"
  fi
fi

# ---------------------------------------------------------------- checklist
section "Checklist against the demo requirements"
FAILS=0; WARNS=0
while IFS='|' read -r level req detail; do
  [ -n "$level" ] || continue
  printf '[%-4s] %-28s %s\n' "$level" "$req" "$detail"
  [ "$level" != FAIL ] || FAILS=$((FAILS + 1))
  [ "$level" != WARN ] || WARNS=$((WARNS + 1))
done <<EOT
$CHECKS
EOT
printf '\nResult: %s FAIL, %s WARN. ' "$FAILS" "$WARNS"
if [ "$FAILS" -eq 0 ]; then printf 'No blocker found.\n'; else printf 'Fix the FAIL lines before the demo.\n'; fi
[ "$FAILS" -eq 0 ]
