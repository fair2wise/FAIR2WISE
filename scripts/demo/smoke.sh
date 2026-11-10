#!/usr/bin/env bash
# GET-only checks of a running :local container, printed as a PASS/WARN/FAIL table.
#
# Usage: scripts/demo/smoke.sh [--expect "corpus corpus"]
#   --expect LIST   corpora that must be live on the local Tiled (default "e2e_demo bl1101", the seed
#                   list); pass --expect "" when the demo is not seeded yet (the row becomes a WARN).
# Exit code: 0 when no row FAILs (WARN rows need a look), 1 otherwise.
# Checks: container health; gateway and API health; capabilities (profile local, approvals reachable);
# graph sources with Tiled status ok; publication pointer corpora; pending approvals route;
# model lists (extraction picker and settings judge list) holding only free model ids; console
# scripts in the image (docker exec, read-only). It sends only GET requests (no body, no approval),
# prints no secrets and changes nothing.
# Environment: F2W_CONTAINER_NAME, F2W_HOST_PORT.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }
EXPECT="e2e_demo bl1101"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --expect) [ "$#" -ge 2 ] || demo_die "--expect needs a value (use \"\" for none)"; EXPECT="$2"; shift 2 ;;
    *) usage >&2; demo_die "unknown argument: $1" ;;
  esac
done
demo_need curl

FAILS=0; WARNS=0; ROWS=0
row() {  # row STATUS NAME DETAIL
  printf '%-5s %-26s %s\n' "$1" "$2" "$3"
  ROWS=$((ROWS + 1))
  [ "$1" != FAIL ] || FAILS=$((FAILS + 1))
  [ "$1" != WARN ] || WARNS=$((WARNS + 1))
}
A_CODE=""; A_BODY=""
get() {  # get URL -> A_CODE A_BODY
  local out rc=0
  out="$(curl -sS --max-time 30 -w '\n%{http_code}' -X GET "$1" 2>&1)" || rc=$?
  A_CODE="${out##*$'\n'}"; A_BODY="${out%$'\n'*}"
  case "$A_CODE" in [0-9][0-9][0-9]) ;; *) A_CODE="000"; A_BODY="${out:-curl failed (exit $rc)}" ;; esac
  return 0
}
have_json=1
jp() { printf '%s' "$A_BODY" | docker exec -i "$NAME" python -I -c "$1" 2>/dev/null; }

printf 'F2W demo smoke test: %s\n\n' "$BASE"
printf '%-5s %-26s %s\n' STATUS CHECK DETAIL
printf '%-5s %-26s %s\n' ------ -------------------------- ------

# 1 container
if command -v docker >/dev/null 2>&1 && docker inspect "$NAME" >/dev/null 2>&1; then
  st="$(docker inspect -f '{{.State.Status}} / health {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" 2>/dev/null || echo unknown)"
  case "$st" in "running / health healthy") row PASS "container" "$NAME $st" ;; running*) row WARN "container" "$NAME $st" ;; *) row FAIL "container" "$NAME $st" ;; esac
else
  row FAIL "container" "docker cannot see a container named $NAME (is Docker running?)"; have_json=0
fi

# 2 health
get "${BASE}/healthz"
if [ "$A_CODE" = 200 ]; then row PASS "gateway /healthz" "HTTP 200"; else row FAIL "gateway /healthz" "HTTP $A_CODE"; fi
get "${API}/health"
if [ "$A_CODE" = 200 ]; then row PASS "API /health" "HTTP 200"; else row FAIL "API /health" "HTTP $A_CODE $(printf '%s' "$A_BODY" | head -c 120)"; fi

# 3 capabilities
get "${API}/v1/capabilities"
if [ "$A_CODE" != 200 ]; then row FAIL "capabilities" "HTTP $A_CODE"
elif [ "$have_json" = 1 ]; then
  out="$(jp 'import json,sys
d=json.load(sys.stdin)
print(d.get("profile"), d.get("can_approve"), d.get("can_save"), d.get("can_edit"))' || echo "? ? ? ?")"
  set -- $out
  if [ "$1" = local ] && [ "$2" = True ]; then
    if [ "$3" = True ]; then row PASS "capabilities" "profile local, approvals reachable, Save available"
    else row WARN "capabilities" "profile local, approvals reachable; Save hidden until the first publication (can_save=$3)"; fi
  else row FAIL "capabilities" "profile=$1 can_approve=$2 (needs local / True)"; fi
else row WARN "capabilities" "HTTP 200; not parsed (no docker)"; fi

# 4 graph sources
get "${API}/v1/graph/sources"
if [ "$A_CODE" != 200 ]; then row FAIL "graph sources" "HTTP $A_CODE"
elif [ "$have_json" = 1 ]; then
  out="$(jp 'import json,sys
d=json.load(sys.stdin)
s=d.get("sources") or []
t=(d.get("tiled") or {}).get("status")
print(t, len(s), sum(1 for x in s if x.get("kind")=="tiled"), sum(1 for x in s if x.get("kind")=="json"))' || echo "? 0 0 0")"
  set -- $out
  if [ "$1" = ok ]; then row PASS "graph sources" "Tiled status ok; $2 sources ($3 tiled, $4 json)"
  else row FAIL "graph sources" "Tiled status '$1' (needs ok); $2 sources"; fi
else row WARN "graph sources" "HTTP 200; not parsed (no docker)"; fi

# 5 pointer corpora
get "${API}/v1/publications/pointer?target=${TILED_TARGET}"
if [ "$A_CODE" != 200 ]; then row FAIL "pointer corpora" "HTTP $A_CODE"
elif [ "$have_json" = 1 ]; then
  live="$(jp 'import json,sys
d=json.load(sys.stdin)
print(" ".join("%s=%s" % (k,(v or {}).get("snapshot_ref","")) for k,v in sorted((d.get("corpora") or {}).items())))' || echo "")"
  missing=""
  for c in $EXPECT; do case " $(printf '%s' "$live" | sed 's/=[^ ]*//g') " in *" $c "*) ;; *) missing="$missing $c" ;; esac; done
  if [ -n "$missing" ]; then row FAIL "pointer corpora" "live: ${live:-none}; MISSING:$missing (run seed.sh and approve)"
  elif [ -z "$live" ]; then row WARN "pointer corpora" "nothing live yet (not seeded)"
  else row PASS "pointer corpora" "live: $live"; fi
else row WARN "pointer corpora" "HTTP 200; not parsed (no docker)"; fi

# 6 pending approvals
get "${API}/v1/approvals/pending"
if [ "$A_CODE" != 200 ]; then row FAIL "pending approvals" "HTTP $A_CODE"
elif [ "$have_json" = 1 ]; then
  out="$(jp 'import json,sys
d=json.load(sys.stdin)
print(d.get("total"), d.get("counts"))' || echo "? ?")"
  row PASS "pending approvals" "route reachable; total ${out}"
else row PASS "pending approvals" "route reachable (HTTP 200)"; fi

# 7 model lists hold only free ids (policy.is_allowed in the image; the response's own free list as fallback)
PYMODELS='import json,sys
sys.path.insert(0,"/app")
d=json.load(sys.stdin)
key=sys.argv[1]
ids=[(m.get("id") or m.get("model") or m.get("name")) if isinstance(m,dict) else m for m in (d.get(key) or [])]
free=(d.get("model_policy") or {}).get("free_models") or []
try:
    from app.modules.model_policy import load_policy
    p=load_policy(); ok=lambda m: p.is_allowed(m)
except Exception:
    ok=lambda m: m in free
bad=[str(m) for m in ids if not ok(m)]
print(len(ids), ",".join(bad) or "-")'
model_row() {  # model_row LABEL URL KEY
  local label="$1" url="$2" key="$3" out n bad
  get "$url"
  if [ "$A_CODE" != 200 ]; then row FAIL "$label" "HTTP $A_CODE"; return 0; fi
  [ "$have_json" = 1 ] || { row WARN "$label" "HTTP 200; not parsed (no docker)"; return 0; }
  out="$(printf '%s' "$A_BODY" | docker exec -i "$NAME" python -I -c "$PYMODELS" "$key" 2>/dev/null || echo "? ?")"
  n="${out%% *}"; bad="${out#* }"
  if [ "$n" = "?" ]; then row WARN "$label" "could not parse the answer"
  elif [ "$bad" != "-" ]; then row FAIL "$label" "NON-FREE ids offered: $bad"
  elif [ "$n" = 0 ]; then row WARN "$label" "empty list (CBORG unreachable? off LBLnet/VPN)"
  else row PASS "$label" "$n ids, all free"; fi
}
model_row "models: extraction" "${API}/v1/library/models" models
model_row "models: settings" "${API}/v1/validation/judge-model" available_models

# 8 console scripts
if [ "$have_json" = 1 ]; then
  absent=""
  for s in f2w-run f2w-tiled-server f2w-harvest f2w-paper-finder f2w-corpus-qa f2w-download-pdfs f2w-repair-corpus f2w-kg-snapshot f2w-kg-release f2w-kg-adopt; do
    docker exec "$NAME" test -x "/usr/local/bin/$s" >/dev/null 2>&1 || absent="$absent $s"
  done
  if [ -n "$absent" ]; then row FAIL "console scripts" "missing:$absent"
  elif docker exec "$NAME" f2w-kg-adopt --help >/dev/null 2>&1; then row PASS "console scripts" "10 present; f2w-kg-adopt --help runs"
  else row FAIL "console scripts" "f2w-kg-adopt --help fails"; fi
else row FAIL "console scripts" "docker cannot reach $NAME"; fi

printf '\n%s checks: %s FAIL, %s WARN.\n' "$ROWS" "$FAILS" "$WARNS"
[ "$FAILS" -eq 0 ]
