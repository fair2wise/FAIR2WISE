#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CURRENT_KG="${CURRENT_KG:-}"
LARGE_KG="${LARGE_KG:-storage/kg/matkg_qwen3_235b_580papers.json}"
OUTPUT_KG="${OUTPUT_KG:-storage/kg/matkg_with_code.json}"
SPLASH_REPO="${SPLASH_LINKS_REPO:-$ROOT_DIR/splash_links}"
if [[ "$SPLASH_REPO" != /* ]]; then
  SPLASH_REPO="$ROOT_DIR/$SPLASH_REPO"
fi
SPLASH_URI="${KG_RAG_SPLASH_URI:-splash://localhost:8081}"
START_SPLASH="${START_SPLASH:-1}"

if [[ -z "$CURRENT_KG" && -f .env ]]; then
  CURRENT_KG="$(
    python3 - <<'PY'
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "KG_RAG_GRAPH":
        print(value.strip().strip('"').strip("'"))
        break
PY
  )"
fi

CURRENT_KG="${CURRENT_KG:-storage/kg/matkg_xray_papers_cborg_chat.json}"

if [[ ! -f "$CURRENT_KG" ]]; then
  echo "error: current KG not found: $CURRENT_KG" >&2
  exit 1
fi
if [[ ! -f "$LARGE_KG" ]]; then
  echo "error: large KG not found: $LARGE_KG" >&2
  exit 1
fi
if [[ ! -d "$SPLASH_REPO" ]]; then
  echo "error: splash_links repo not found: $SPLASH_REPO" >&2
  exit 1
fi

echo "Merging KGs"
echo "  current: $CURRENT_KG"
echo "  large:   $LARGE_KG"
echo "  output:  $OUTPUT_KG"

CURRENT_KG="$CURRENT_KG" LARGE_KG="$LARGE_KG" OUTPUT_KG="$OUTPUT_KG" python3 - <<'PY'
import json
import os
from copy import deepcopy
from pathlib import Path

current = Path(os.environ["CURRENT_KG"])
large = Path(os.environ["LARGE_KG"])
out = Path(os.environ["OUTPUT_KG"])

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def stable_key(value):
    if isinstance(value, dict):
        for field in ("id", "doi", "source_paper", "paper_title", "name"):
            raw = value.get(field)
            if raw:
                return f"{field}:{str(raw).strip().lower()}"
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)

def merge_lists(left, right):
    merged = []
    seen = set()
    for item in list(left or []) + list(right or []):
        key = stable_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(deepcopy(item))
    return merged

def merge_dicts_prefer_left(left, right):
    merged = deepcopy(left)
    for key, value in right.items():
        if value in (None, "", []):
            continue
        if key not in merged or merged[key] in (None, "", []):
            merged[key] = deepcopy(value)
        elif isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = merge_lists(merged[key], value)
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_dicts_prefer_left(merged[key], value)
    return merged

def edge_key(edge):
    return (
        str(edge.get("subject") or edge.get("source") or ""),
        str(edge.get("predicate") or ""),
        str(edge.get("object") or edge.get("target") or ""),
    )

current_data = load(current)
large_data = load(large)

nodes_by_id = {}
node_order = []
for data in (current_data, large_data):
    for node in data.get("things") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        if node_id not in nodes_by_id:
            nodes_by_id[node_id] = deepcopy(node)
            node_order.append(node_id)
        else:
            nodes_by_id[node_id] = merge_dicts_prefer_left(nodes_by_id[node_id], node)

edges_by_key = {}
edge_order = []
for data in (current_data, large_data):
    for edge in data.get("associations") or []:
        if not isinstance(edge, dict):
            continue
        key = edge_key(edge)
        if not key[0] or not key[2]:
            continue
        if key not in edges_by_key:
            edges_by_key[key] = deepcopy(edge)
            edge_order.append(key)
        else:
            edges_by_key[key] = merge_dicts_prefer_left(edges_by_key[key], edge)

merged = {}
for data in (large_data, current_data):
    for key, value in data.items():
        if key not in {"things", "associations"} and key not in merged:
            merged[key] = deepcopy(value)
merged["things"] = [nodes_by_id[node_id] for node_id in node_order]
merged["associations"] = [edges_by_key[key] for key in edge_order]

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
print(
    "merged nodes={nodes} edges={edges} duplicate_nodes={dup_nodes} duplicate_edges={dup_edges}".format(
        nodes=len(merged["things"]),
        edges=len(merged["associations"]),
        dup_nodes=len(current_data.get("things") or []) + len(large_data.get("things") or []) - len(merged["things"]),
        dup_edges=len(current_data.get("associations") or []) + len(large_data.get("associations") or []) - len(merged["associations"]),
    )
)
PY

python3 -m json.tool "$OUTPUT_KG" >/dev/null

if ! curl -fsS "http://127.0.0.1:8081/docs" >/dev/null 2>&1; then
  if [[ "$START_SPLASH" != "1" ]]; then
    echo "error: splash-links is not running at http://127.0.0.1:8081" >&2
    exit 1
  fi
  echo "Starting splash-links server"
  mkdir -p "$ROOT_DIR/runs"
  (cd "$SPLASH_REPO" && pixi run serve > "$ROOT_DIR/runs/splash_links.log" 2>&1 &)
  for _ in {1..40}; do
    if curl -fsS "http://127.0.0.1:8081/docs" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if ! curl -fsS "http://127.0.0.1:8081/docs" >/dev/null 2>&1; then
  echo "error: splash-links did not become ready; see runs/splash_links.log" >&2
  exit 1
fi

echo "Reimporting into splash-links at $SPLASH_URI"
OUTPUT_KG="$OUTPUT_KG" SPLASH_REPO="$SPLASH_REPO" SPLASH_URI="$SPLASH_URI" python3 - <<'PY'
import os
from app.modules.f2w_agent.kg_update import splash_reimport

result = splash_reimport(
    os.environ["OUTPUT_KG"],
    splash_repo=os.environ["SPLASH_REPO"],
    splash_uri=os.environ["SPLASH_URI"],
    allow_wipe=True,
)
print(result)
if result.get("status") != "success":
    raise SystemExit(1)
PY
