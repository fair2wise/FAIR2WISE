#!/usr/bin/env bash
# Put the demo into its known state in a RUNNING container: adopt the demo corpora, stage their
# publication plans, and print the exact commands the OWNER runs to approve them.
#
# Usage: scripts/demo/seed.sh [--code-commit <40-hex>] [--file <host-path>]... [--adopt-only]
#                             [--i-approve]
#   --code-commit SHA   the commit of the code in the image. Default: the image's
#                       org.opencontainers.image.revision label (set by CI and by `docker build
#                       --build-arg GIT_COMMIT=<sha>`). An image without one needs this argument.
#   --file PATH         a flat matkg_<name>.json on this Mac to copy into the container first (repeat for
#                       several). Needed for e2e_demo: that corpus was harvested and extracted live and is
#                       NOT baked into the image; export it (Library > Export) or take the rehearsal file.
#   --adopt-only        adopt the flat files and stop (no publication plan is staged)
#   --i-approve         the OWNER'S OWN ACT. Without it this script never calls the approval route.
#                       With it the script prints each plan summary, waits 5 seconds (Ctrl-C aborts) and
#                       then performs the same approval POST the Pending Approvals page makes.
# Idempotent: an adopted release is skipped, a corpus already live on the target is skipped, a plan
# already waiting for approval is reused (its hash is re-read). Re-run it after any 409 stale_approval.
# Environment: F2W_CONTAINER_NAME, F2W_HOST_PORT. Prints no secrets and reads no dotenv file.
#
# The demo corpora, one "corpus:flat-file:snapshot_ref" per entry (edit this list to change the demo):
#   e2e_demo : the arXiv-harvested corpus of the 2026-11-06 rehearsal (268 things); file supplied with --file
#   bl1101   : matkg_bl1101_v4.json, the file the rehearsal published as bl1101/v4 (421 things, 937 links,
#              the smallest recent bl1101 version, baked into the image; v8 is 654 things, v9 435).
DEMO_CORPORA="e2e_demo:matkg_e2e_demo.json:e2e_demo/v1 bl1101:matkg_bl1101_v4.json:bl1101/v4"
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; }
COMMIT=""; ADOPT_ONLY=0; I_APPROVE=0; HOSTFILES=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --code-commit) [ "$#" -ge 2 ] || demo_die "--code-commit needs a value"; COMMIT="$2"; shift 2 ;;
    --file) [ "$#" -ge 2 ] || demo_die "--file needs a path"; HOSTFILES+=("$2"); shift 2 ;;
    --adopt-only) ADOPT_ONLY=1; shift ;;
    --i-approve) I_APPROVE=1; shift ;;
    *) usage >&2; demo_die "unknown argument: $1" ;;
  esac
done
demo_need docker curl
demo_docker_ok || demo_die "docker is not running"
demo_container_running || demo_die "container ${NAME} is not running (scripts/demo/install.sh, or docker start ${NAME})"

# ---- commit
if [ -z "$COMMIT" ]; then
  COMMIT="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$NAME" 2>/dev/null || true)"
  [ -z "$COMMIT" ] || demo_info "code commit from the image label: $COMMIT"
fi
case "$COMMIT" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) demo_die "no usable code commit (image label is '${COMMIT:-empty}'). Pass --code-commit <40-hex commit the image was built from>." ;;
esac
[ "${#COMMIT}" -eq 40 ] || demo_die "the code commit must be 40 hex characters, got ${#COMMIT}"
case "$COMMIT" in *[!0-9a-f]*) demo_die "the code commit must be lowercase hex" ;; esac

# ---- helpers
H_CT='Content-Type: application/json'; H_OR="Origin: http://127.0.0.1:${PORT}"
A_CODE=""; A_BODY=""
api_call() {  # api_call METHOD PATH [BODY] -> A_CODE A_BODY (never fails the script)
  local method="$1" path="$2" body="${3:-}" out rc=0
  if [ "$method" = GET ]; then
    out="$(curl -sS --max-time 60 -w '\n%{http_code}' -X GET "${API}${path}" 2>&1)" || rc=$?
  else
    out="$(curl -sS --max-time 60 -w '\n%{http_code}' -H "$H_CT" -H "$H_OR" -X "$method" "${API}${path}" -d "$body" 2>&1)" || rc=$?
  fi
  A_CODE="${out##*$'\n'}"; A_BODY="${out%$'\n'*}"
  case "$A_CODE" in [0-9][0-9][0-9]) ;; *) A_CODE="000"; A_BODY="${out:-curl failed (exit $rc)}" ;; esac
  return 0
}
ok2xx() { case "$A_CODE" in 2??) return 0 ;; *) return 1 ;; esac; }
dx() { docker exec "$NAME" "$@"; }

api_call GET /health
ok2xx || demo_die "the API does not answer at ${API}/health (HTTP ${A_CODE}); is the container healthy? docker logs ${NAME}"

# ---- 1. adopt
demo_info "adopting flat KG files into ${STORAGE_KG}"
dx mkdir -p "$STORAGE_KG"
MISSING=""
for spec in $DEMO_CORPORA; do
  corpus="${spec%%:*}"; rest="${spec#*:}"; file="${rest%%:*}"; snap="${rest#*:}"
  if dx test -f "$STORAGE_KG/$snap/graph.json" 2>/dev/null; then
    demo_info "$corpus: ${snap} already adopted (skipped)"; continue
  fi
  # a host file given with --file wins over what the image holds
  for hf in ${HOSTFILES[@]+"${HOSTFILES[@]}"}; do
    if [ "$(basename "$hf")" = "$file" ]; then
      [ -f "$hf" ] || demo_die "--file not found: $hf"
      demo_info "$corpus: copying $hf into the container"
      docker cp "$hf" "$NAME:$STORAGE_KG/$file"
    fi
  done
  if ! dx test -f "$STORAGE_KG/$file" 2>/dev/null; then
    src=""
    for cand in "/app/storage/kg/$file" "/var/lib/fair2wise/state/kg_releases/$file"; do
      if dx test -f "$cand" 2>/dev/null; then src="$cand"; break; fi
    done
    if [ -z "$src" ]; then
      demo_warn "$corpus: $file is not in the image or in the export folder; pass --file <path to $file> (skipped)"
      MISSING="$MISSING $corpus"; continue
    fi
    demo_info "$corpus: using $src"
    dx cp "$src" "$STORAGE_KG/$file"
  fi
  report="$(dx f2w-kg-adopt adopt --storage-kg "$STORAGE_KG" --only "$file" --code-commit "$COMMIT" --dry-run 2>&1)" || {
    printf '%s\n' "$report" | head -n 40 >&2; demo_die "$corpus: the dry-run adoption failed (report above)"; }
  case "$report" in *would_adopt*) ;; *) printf '%s\n' "$report" | head -n 40 >&2; demo_die "$corpus: the dry run did not say would_adopt (the refusing rule is above)" ;; esac
  dx f2w-kg-adopt adopt --storage-kg "$STORAGE_KG" --code-commit "$COMMIT" --only "$file" >/dev/null
  dx test -f "$STORAGE_KG/$snap/graph.json" || demo_die "$corpus: adoption ran but $snap/graph.json is missing"
  demo_info "$corpus: adopted as ${snap}"
done

if [ "$ADOPT_ONLY" -eq 1 ]; then
  [ -z "$MISSING" ] || { demo_warn "not adopted:$MISSING"; exit 3; }
  demo_info "--adopt-only: done"; exit 0
fi

# ---- 2. stage plans
api_call GET "/v1/publications/pointer?target=${TILED_TARGET}"
ok2xx || demo_die "cannot read the publication pointer (HTTP ${A_CODE}): ${A_BODY}"
LIVE="$(printf '%s' "$A_BODY" | demo_json_py 'import json,sys
d=json.load(sys.stdin)
for k,v in sorted((d.get("corpora") or {}).items()):
    print(k, (v or {}).get("snapshot_ref",""))')"

STAGED=""   # "corpus|snapshot|job_id" lines
pending_job() {  # pending_job SNAPSHOT -> job id of a waiting plan for that snapshot on the local target, or empty
  api_call GET /v1/approvals/pending
  ok2xx || demo_die "cannot read the pending approvals (HTTP ${A_CODE}): ${A_BODY}"
  printf '%s' "$A_BODY" | demo_json_py "import json,sys
d=json.load(sys.stdin)
for i in d.get('items') or []:
    if i.get('kind')=='publication' and i.get('snapshot_ref')=='$1' and i.get('target')=='${TILED_TARGET}':
        print(i.get('job_id') or i.get('id')); break"
}
for spec in $DEMO_CORPORA; do
  corpus="${spec%%:*}"; snap="${spec##*:}"
  case " $MISSING " in *" $corpus "*) continue ;; esac
  if printf '%s\n' "$LIVE" | grep -qx "$corpus $snap"; then demo_info "$corpus: ${snap} is already live on the local Tiled (skipped)"; continue; fi
  job="$(pending_job "$snap")"
  if [ -n "$job" ]; then
    demo_info "$corpus: reusing the plan already waiting for approval ($job)"
  else
    api_call POST /v1/publications "{\"snapshot_ref\":\"${snap}\",\"target\":\"${TILED_TARGET}\"}"
    ok2xx || demo_die "$corpus: staging the plan failed (HTTP ${A_CODE}): $(printf '%s' "$A_BODY" | head -c 400)"
    job="$(printf '%s' "$A_BODY" | demo_json_py 'import json,sys; print(json.load(sys.stdin)["job_id"])')"
    demo_info "$corpus: staging ${snap} as job ${job} (nothing is written to Tiled yet)"
  fi
  waited=0
  while :; do
    api_call GET "/v1/jobs/${job}/view"
    state="$(printf '%s' "$A_BODY" | demo_json_py 'import json,sys; print(json.load(sys.stdin).get("ui_state",""))' 2>/dev/null || true)"
    [ "$state" != "staged" ] || break
    case "$state" in published|superseded) break ;; failed|quarantined|cancelled|denied) demo_die "$corpus: job $job is $state; see ${BASE}/ (Jobs) or docker logs ${NAME}" ;; esac
    [ "$waited" -lt 180 ] || demo_die "$corpus: job $job is still '${state:-unknown}' after 180s"
    sleep 3; waited=$((waited + 3))
  done
  [ "$state" = staged ] && STAGED="${STAGED}${corpus}|${snap}|${job}
"
done

# ---- 3. print (and, only with --i-approve, perform) the approvals
plan_of() {  # plan_of JOB -> "<sha256> <creates> <links>" read now
  api_call GET "/v1/publications/$1/plan"
  ok2xx || demo_die "cannot read the plan of $1 (HTTP ${A_CODE}): $(printf '%s' "$A_BODY" | head -c 300)"
  printf '%s' "$A_BODY" | demo_json_py 'import json,sys
d=json.load(sys.stdin)
print(d["plan_sha256"], d.get("creates","?"), d.get("links","?"), len(d.get("supersessions") or []))'
}
show_command() {  # show_command JOB HASH
  printf "curl -fsS -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:%s' \\\\\n  -X POST %s/v1/publications/%s/approval \\\\\n  -d '{\"plan_sha256\":\"%s\"}'\n" "$PORT" "$API" "$1" "$2"
}
if [ -z "$STAGED" ]; then
  demo_info "no plan is waiting for approval"
else
  printf '\n==== Plans waiting for the OWNER to approve ====\n'
  printf 'Approve in the UI (%s/ > Pending Approvals) or run the commands below, one at a time, in this order.\n' "$BASE"
  printf 'If an approval answers 409 stale_approval, run seed.sh again: it prints fresh hashes.\n\n'
  printf '%s' "$STAGED" | while IFS='|' read -r corpus snap job; do
    [ -n "$job" ] || continue
    set -- $(plan_of "$job")
    printf '# %s (%s): job %s, plan_sha256 %s, creates %s, links %s, supersessions %s\n' "$corpus" "$snap" "$job" "$1" "$2" "$3" "$4"
    show_command "$job" "$1"; printf '\n'
  done
fi

if [ "$I_APPROVE" -eq 1 ] && [ -n "$STAGED" ]; then
  printf '\n--i-approve was given: this is the owner approving the plans above.\n'
  printf '%s' "$STAGED" | while IFS='|' read -r corpus snap job; do
    [ -n "$job" ] || continue
    set -- $(plan_of "$job")
    hash="$1"
    printf '\nAbout to approve %s (%s): job %s, plan_sha256 %s, creates %s, links %s, supersessions %s\n' "$corpus" "$snap" "$job" "$hash" "$2" "$3" "$4"
    printf 'Approving in 5 seconds; press Ctrl-C to abort. '
    n=5; while [ "$n" -gt 0 ]; do printf '%s ' "$n"; sleep 1; n=$((n - 1)); done; printf '\n'
    api_call POST "/v1/publications/${job}/approval" "{\"plan_sha256\":\"${hash}\"}"
    if ! ok2xx; then
      demo_die "approval of $corpus refused (HTTP ${A_CODE}): $(printf '%s' "$A_BODY" | head -c 300). On 409 stale_approval run seed.sh --i-approve again."
    fi
    demo_info "$corpus: approved; waiting for it to be published"
    waited=0
    while :; do
      api_call GET "/v1/jobs/${job}/view"
      state="$(printf '%s' "$A_BODY" | demo_json_py 'import json,sys; print(json.load(sys.stdin).get("ui_state",""))' 2>/dev/null || true)"
      [ "$state" != published ] || break
      case "$state" in failed|quarantined|cancelled|denied) demo_die "$corpus: job $job is $state" ;; esac
      [ "$waited" -lt 300 ] || demo_die "$corpus: job $job is still '${state:-unknown}' after 300s"
      sleep 3; waited=$((waited + 3))
    done
    demo_info "$corpus: published"
  done
  api_call GET "/v1/publications/pointer?target=${TILED_TARGET}"
  printf '%s' "$A_BODY" | demo_json_py 'import json,sys
d=json.load(sys.stdin)
print("live corpora:", ", ".join("%s=%s" % (k,(v or {}).get("snapshot_ref","")) for k,v in sorted((d.get("corpora") or {}).items())) or "none")'
fi

[ -z "$MISSING" ] || { demo_warn "not seeded (file missing):$MISSING"; exit 3; }
[ "$I_APPROVE" -eq 1 ] || [ -z "$STAGED" ] || printf 'Nothing was approved by this script. After approving, run scripts/demo/smoke.sh.\n'
exit 0
