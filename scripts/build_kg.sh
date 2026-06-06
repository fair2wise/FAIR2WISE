#!/usr/bin/env bash
# build_kg.sh — Extract terms from a PDF folder then build the KG.
#
# Safe: writes to temp files during extraction. Only overwrites final
# output on success. Old files backed up as .bak, not deleted.
# If interrupted, previous output is untouched.
#
# Usage:
#   ./scripts/build_kg.sh [PDF_DIR] [TERMS_OUTPUT] [KG_OUTPUT]
#
# Defaults:
#   PDF_DIR      xray_papers/
#   TERMS_OUTPUT storage/terminology/extracted_terms_xray_papers_cborg_chat.json
#   KG_OUTPUT    storage/kg/matkg_xray_papers_cborg_chat.json
#
# Examples:
#   ./scripts/build_kg.sh
#   ./scripts/build_kg.sh polymer_papers/ \
#       storage/terminology/extracted_terms_polymer.json \
#       storage/kg/matkg_polymer.json

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- args with defaults ---
PDF_DIR="${1:-xray_papers/}"
TERMS_OUT="${2:-storage/terminology/extracted_terms_xray_papers_cborg_chat.json}"
KG_OUT="${3:-storage/kg/matkg_xray_papers_cborg_chat.json}"

# temp files — extraction writes here, only moved on success
TERMS_TMP="${TERMS_OUT}.tmp"
KG_TMP="${KG_OUT}.tmp"

cleanup() {
    rm -f "$TERMS_TMP" "$KG_TMP"
}
trap cleanup EXIT

echo "========================================"
echo "  KG Build Pipeline"
echo "========================================"
echo "  PDF dir   : $PDF_DIR"
echo "  Terms out : $TERMS_OUT"
echo "  KG out    : $KG_OUT"
echo "========================================"

# --- sanity checks ---
if [ ! -d "$PDF_DIR" ]; then
    echo "ERROR: PDF directory '$PDF_DIR' not found." >&2
    exit 1
fi

if [ -z "$(ls "$PDF_DIR"*.pdf 2>/dev/null)" ]; then
    echo "ERROR: No PDFs found in '$PDF_DIR'." >&2
    exit 1
fi

# --- Step 1: extract terms → temp file ---
echo ""
echo "[1/2] Running term extraction..."
rm -f "$TERMS_TMP"
python3 app/modules/extract_terms.py \
    --pdf-dir "$PDF_DIR" \
    --output "$TERMS_TMP" \
    --backend cborg \
    --model lbl/cborg-chat

if [ ! -f "$TERMS_TMP" ]; then
    echo "ERROR: Extraction produced no output." >&2
    exit 1
fi

# --- Step 2: build KG → temp file ---
echo ""
echo "[2/2] Building knowledge graph..."
rm -f "$KG_TMP"
python3 app/modules/json2kg.py \
    "$TERMS_TMP" \
    "$KG_TMP" \
    --verbose

if [ ! -f "$KG_TMP" ]; then
    echo "ERROR: KG build produced no output." >&2
    exit 1
fi

# --- Both steps succeeded — swap files atomically ---
echo ""
echo "Both steps succeeded. Promoting outputs..."

# Backup old files if they exist
if [ -f "$TERMS_OUT" ]; then
    mv "$TERMS_OUT" "${TERMS_OUT}.bak"
    echo "  Backed up previous terms → ${TERMS_OUT}.bak"
fi
if [ -f "$KG_OUT" ]; then
    mv "$KG_OUT" "${KG_OUT}.bak"
    echo "  Backed up previous KG    → ${KG_OUT}.bak"
fi

mv "$TERMS_TMP" "$TERMS_OUT"
mv "$KG_TMP" "$KG_OUT"

echo ""
echo "========================================"
echo "  Done!"
echo "  Terms → $TERMS_OUT"
echo "  KG    → $KG_OUT"
echo "========================================"
