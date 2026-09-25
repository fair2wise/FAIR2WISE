#!/usr/bin/env python3
"""
Ingest public ALS docs into bl1101 KG v7.

Sources:
  1. papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html
  2. .cache/bl1101/repos/bcs2sim-ophyd/info/bl1101knowledge.md
  3. https://als.lbl.gov/beamlines/11-0-1-2/  (optional, skips gracefully on failure)

Output: storage/kg/matkg_bl1101_v7.json
"""

import json
import os
import re
import sys
import textwrap
import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
KG_V6_PATH = REPO_ROOT / "storage" / "kg" / "matkg_bl1101_v6.json"
KG_V7_PATH = REPO_ROOT / "storage" / "kg" / "matkg_bl1101_v7.json"
BLUEPRINT_PATH = REPO_ROOT / "papers" / "rsoxs" / "beamline_blueprint" / "blueprint_bl11012_version09092026.html"
KNOWLEDGE_MD_PATH = REPO_ROOT / ".cache" / "bl1101" / "repos" / "bcs2sim-ophyd" / "info" / "bl1101knowledge.md"
ALS_PAGE_URL = "https://als.lbl.gov/beamlines/11-0-1-2/"
TODAY_ISO = datetime.date.today().isoformat()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_whitespace(text: str) -> str:
    """Collapse multiple spaces / newlines into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def word_wrap_chunk(text: str, min_chars: int = 1500, max_chars: int = 3500) -> str:
    """Return text trimmed to be within target range, breaking at word boundary."""
    text = clean_whitespace(text)
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars]
    last_space = trimmed.rfind(" ")
    return trimmed[:last_space] if last_space > min_chars else trimmed


def make_thing(
    node_id: str,
    name: str,
    category: str,
    description: str,
    source_kind: str,
    corpus: str = "bl1101",
    graph_id: str = "bl1101",
    provenance: str = "",
    extra: dict | None = None,
) -> dict:
    base = {
        "id": node_id,
        "name": name,
        "category": category,
        "entityType": category,  # for verify-script compatibility
        "raw_category": None,
        "description": description,
        "pages": [],
        "source_papers": [provenance] if provenance else [],
        "context_snippets": [],
        "formula": "",
        "formula_validation": {},
        "properties": [],
        "publication_year": None,
        "paper_title": None,
        "source_metadata": {
            "source_kind": source_kind,
            "corpus": corpus,
            "graph_id": graph_id,
            "provenance": provenance,
        },
        "publications": [],
        "authors": [],
        "institutions": [],
        "doi": None,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages_range": None,
        "abstract_text": None,
        "keywords": [],
        "type": f"beamline:{category}",
        "source_kind": source_kind,
        "corpus": corpus,
        "graph_id": graph_id,
        "provenance": provenance,
    }
    if extra:
        base.update(extra)
    return base


def make_assoc(subject: str, predicate: str, obj: str, evidence: str = "") -> dict:
    return {"subject": subject, "predicate": predicate, "object": obj, "has_evidence": evidence}


# ---------------------------------------------------------------------------
# Step 1a: Parse Blueprint HTML
# ---------------------------------------------------------------------------

def parse_blueprint_html() -> tuple[list[dict], list[dict]]:
    """Return (things, associations) from the blueprint HTML."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[WARN] BeautifulSoup not installed; skipping blueprint parse", file=sys.stderr)
        return [], []

    print(f"[INFO] Parsing blueprint: {BLUEPRINT_PATH}")
    with open(BLUEPRINT_PATH, encoding="utf-8", errors="replace") as fh:
        html = fh.read()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    full_text = soup.get_text(separator=" ", strip=True)
    full_text = clean_whitespace(full_text)
    total = len(full_text)
    print(f"[INFO]  Blueprint text: {total} chars")

    PROVENANCE = "blueprint_bl11012_version09092026.html"

    # -- Entity nodes ---------------------------------------------------------

    things = []
    associations = []

    # Technique nodes
    techniques = [
        ("beamline:Technique-RSOXS-transmission",
         "RSoXS Transmission",
         "Resonant soft X-ray scattering in transmission geometry through SiN membrane windows. "
         "Sample Theta at 90° (bar vertical, surface normal to beam). "
         "Photon energy range 200–1500 eV (usable), EPU polarization 100 or 190."),
        ("beamline:Technique-RSOXS-reflection",
         "RSoXS Reflection / XRR",
         "Resonant soft X-ray scattering and X-ray reflectivity in grazing-incidence geometry. "
         "Sample Theta at 0° (bar horizontal, surface parallel to beam). "
         "Used with Si coupons. Higher Order Suppressor attenuates beam for reflectivity."),
        ("beamline:Technique-P-RSoXS",
         "P-RSoXS",
         "Polarization-dependent RSoXS. The EPU (Elliptically Polarizing Undulator) provides "
         "linear in-plane (EPU polarization ~100) or circular (EPU polarization ~190) polarization "
         "by shifting magnet-row Z offset. Polarization switching done once or twice per beamtime."),
    ]
    for tid, tname, tdesc in techniques:
        things.append(make_thing(
            tid, tname, "ExperimentalTechnique", tdesc,
            source_kind="blueprint", provenance=PROVENANCE))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasCapability", tid, PROVENANCE))

    # Instrument nodes
    instruments = [
        ("beamline:Instrument-AXIS-SXR-40",
         "AXIS-SXR-40 Detector",
         "Tucsen Dhyana XFXV4040BSI sCMOS 2D area detector (4096×4096 pixels, 9 µm pixel size, "
         "16-bit). Bare silicon sensor — sensitive to visible light so the ring light must be off "
         "during any exposure. Mounted on a three-axis goniometer (CCD Theta −6°…122°, CCD X, CCD Y). "
         "Parked position: CCD Y = 80 (motor counts, fully out)."),
        ("beamline:Instrument-EPU",
         "Elliptically Polarizing Undulator (EPU)",
         "The insertion device providing photon beam to BL 11.0.1.2. EPU gap controls photon energy "
         "in coordination with the monochromator lookup table. EPU Z (magnet-row offset) selects "
         "polarization: ~100 for linear in-plane, ~190 for circular. Operated at ALS 1.9 GeV top-up "
         "~500 mA."),
        ("beamline:Instrument-Mono101",
         "Mono 101 (VLS Monochromator)",
         "Variable Line Spacing monochromator with a single mirror carrying three gratings: "
         "250, 500, and 1000 lines/mm. Selected by the Mono 101 Vessel motor. "
         "Energy type (lookup table) selects the grating condition, including the third harmonic "
         "table for high energies on the 500 l/mm grating. Changed once or twice per day."),
        ("beamline:Instrument-GoldMesh-I0",
         "Gold Mesh I0 Monitor (AI 3 Izero)",
         "Gold-coated copper mesh permanently in the beam before all scatter slits. "
         "Photo-ejected electrons generate a current proportional to beam intensity (reads negative, "
         "1 nA/V, ~−0.02 V typical). Attenuates beam by ~10%. Used to normalise every measurement."),
    ]
    for iid, iname, idesc in instruments:
        things.append(make_thing(
            iid, iname, "Instrument", idesc,
            source_kind="blueprint", provenance=PROVENANCE))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasInstrument", iid, PROVENANCE))

    # Capability nodes
    caps = [
        ("beamline:Capability-EnergyRange",
         "Energy Range 200–1500 eV",
         "ALS BL 11.0.1.2 operates in the soft X-ray range, 200–1500 eV usable "
         "(165–1500 eV design range). Typical operating energy is ~270 eV. "
         "Covers carbon K-edge (~285 eV), nitrogen K-edge (~400 eV), oxygen K-edge (~530 eV), "
         "sulphur L-edge (~165 eV), and higher edges up to 1500 eV."),
        ("beamline:Capability-PolarizationModes",
         "EPU Polarization Modes",
         "Elliptically Polarizing Undulator supports linear horizontal (EPU polarization ~100) and "
         "circular polarization (EPU polarization ~190) by shifting the magnet-row Z offset. "
         "Polarization switching requires beam realignment and is done once or twice per beamtime."),
    ]
    for cid, cname, cdesc in caps:
        things.append(make_thing(
            cid, cname, "Capability", cdesc,
            source_kind="blueprint", provenance=PROVENANCE))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasCapability", cid, PROVENANCE))

    # hasContact edges for existing Person nodes
    associations.append(make_assoc(
        "beamline:BL-11-0-1-2", "rel:hasContact", "beamline:Person-Cheng-Wang", PROVENANCE))
    associations.append(make_assoc(
        "beamline:BL-11-0-1-2", "rel:hasContact", "beamline:Person-Thomas-Ferron", PROVENANCE))

    # -- RAG chunk nodes from blueprint text ----------------------------------
    # Divide into 5 logical sections by character offsets
    # (full_text is ~55 K chars; pick 5 non-overlapping windows)
    chunk_specs = [
        ("beamline:Chunk-blueprint-overview",
         "Blueprint: Beamline Overview and Capabilities",
         0, 4500),
        ("beamline:Chunk-blueprint-optical-layout",
         "Blueprint: Optical Layout (EPU → Mono → M103 → exit slits)",
         4500, 10000),
        ("beamline:Chunk-blueprint-detector-endstation",
         "Blueprint: Detector and Endstation (AXIS-SXR-40, sample stage)",
         10000, 17000),
        ("beamline:Chunk-blueprint-operating-rules",
         "Blueprint: Operating Rules, Contacts, and Scheduling",
         17000, 23000),
        ("beamline:Chunk-blueprint-device-table",
         "Blueprint: Device Table (motors, AIs, DIOs, PVs)",
         23000, min(30000, total)),
    ]
    for cid, cname, start, end in chunk_specs:
        chunk_text = word_wrap_chunk(full_text[start:end], min_chars=800, max_chars=3200)
        things.append(make_thing(
            cid, cname, "DocumentChunk", chunk_text,
            source_kind="doc_chunk", corpus="bl1101", graph_id="bl1101",
            provenance=PROVENANCE))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasDocument", cid, PROVENANCE))

    print(f"[INFO]  Blueprint things: {len(things)}, associations: {len(associations)}")
    return things, associations


# ---------------------------------------------------------------------------
# Step 1b: Parse bl1101knowledge.md
# ---------------------------------------------------------------------------

def parse_knowledge_md() -> tuple[list[dict], list[dict]]:
    """Return (things, associations) from the knowledge markdown."""
    print(f"[INFO] Reading knowledge.md: {KNOWLEDGE_MD_PATH}")
    with open(KNOWLEDGE_MD_PATH, encoding="utf-8", errors="replace") as fh:
        md_text = fh.read()

    # Get file mtime as provenance timestamp
    mtime = os.path.getmtime(KNOWLEDGE_MD_PATH)
    mtime_str = datetime.datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%SZ")
    PROVENANCE = "bl1101knowledge.md"

    things = []
    associations = []

    # Split into sections by markdown headings
    sections = re.split(r"(?m)^## ", md_text)
    # sections[0] is preamble before first ##
    named_sections = []
    for sec in sections[1:]:
        lines = sec.split("\n", 1)
        heading = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        named_sections.append((heading, body))

    print(f"[INFO]  knowledge.md sections: {[h for h,_ in named_sections]}")

    # Map sections to chunk IDs
    chunk_map = [
        ("beamline:Chunk-kd-beamline-layout", "Knowledge: Beamline Layout and Optical Path"),
        ("beamline:Chunk-kd-sample-motors", "Knowledge: Sample Motors and Detector Geometry"),
        ("beamline:Chunk-kd-photon-energy", "Knowledge: Photon Energy, Mono, and Polarization Controls"),
        ("beamline:Chunk-kd-operating-rules", "Knowledge: Operating Rules, Scan Procedures, and Safety"),
    ]

    # Combine sections into chunks
    total_sections = len(named_sections)
    # Pair up sections if we have more than 4
    chunk_sections = []
    if total_sections <= 4:
        for i, (heading, body) in enumerate(named_sections):
            chunk_sections.append(f"## {heading}\n{body}")
    else:
        # Split into 4 roughly equal groups
        group_size = total_sections // 4
        for g in range(4):
            start = g * group_size
            end = (g + 1) * group_size if g < 3 else total_sections
            combined = "\n\n".join(
                f"## {named_sections[i][0]}\n{named_sections[i][1]}"
                for i in range(start, end)
            )
            chunk_sections.append(combined)

    for i, (cid, cname) in enumerate(chunk_map):
        text = chunk_sections[i] if i < len(chunk_sections) else ""
        chunk_text = word_wrap_chunk(clean_whitespace(text), min_chars=400, max_chars=3200)
        things.append(make_thing(
            cid, cname, "DocumentChunk", chunk_text,
            source_kind="doc_chunk", corpus="bl1101", graph_id="bl1101",
            provenance=PROVENANCE,
            extra={"retrieved_at": mtime_str}))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasDocument", cid, PROVENANCE))

    # Extract named plans / constraints as entity nodes
    plan_matches = re.findall(
        r"(?i)\b(qserver|queue.?server|RE\.run_engine|bluesky[\w ]*plan|"
        r"scan_[\w]+|count_[\w]+|rel_scan|list_scan)\b",
        md_text)
    plan_names = sorted(set(m.strip() for m in plan_matches if m.strip()))[:6]
    for pname in plan_names:
        pid = "beamline:Plan-" + re.sub(r"\W+", "-", pname).strip("-")
        things.append(make_thing(
            pid, pname, "BlueskyPlan",
            f"Bluesky/QServer plan or method referenced in bl1101knowledge.md: {pname}",
            source_kind="blueprint", provenance=PROVENANCE))

    print(f"[INFO]  knowledge.md things: {len(things)}, associations: {len(associations)}")
    return things, associations


# ---------------------------------------------------------------------------
# Step 1c: Fetch public ALS page
# ---------------------------------------------------------------------------

def fetch_als_page() -> tuple[list[dict], list[dict], str]:
    """Return (things, associations, status_str). Graceful on failure."""
    try:
        import requests
    except ImportError:
        print("[WARN] requests not installed; skipping ALS page fetch", file=sys.stderr)
        return [], [], "unavailable:no-requests"

    print(f"[INFO] Fetching ALS public page: {ALS_PAGE_URL}")
    try:
        resp = requests.get(
            ALS_PAGE_URL,
            timeout=10,
            headers={"User-Agent": "FAIRtoWISE-research-bot/1.0"},
        )
        resp.raise_for_status()
    except Exception as err:
        print(f"[WARN] Could not fetch ALS public page: {err}", file=sys.stderr)
        return [], [], "unavailable"

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        page_text = clean_whitespace(soup.get_text(separator=" ", strip=True))
    except Exception as err:
        print(f"[WARN] Could not parse ALS page HTML: {err}", file=sys.stderr)
        page_text = clean_whitespace(resp.text)

    print(f"[INFO]  ALS page text: {len(page_text)} chars")

    PROVENANCE = ALS_PAGE_URL
    things = []
    associations = []

    # Create 2 RAG chunks from the page
    chunk_specs = [
        ("beamline:Chunk-als-page-overview",
         "ALS Website: BL 11.0.1.2 Overview",
         0, 3500),
        ("beamline:Chunk-als-page-capabilities",
         "ALS Website: BL 11.0.1.2 Capabilities and Contacts",
         3500, min(7000, len(page_text))),
    ]
    for cid, cname, start, end in chunk_specs:
        chunk_text = word_wrap_chunk(page_text[start:end], min_chars=200, max_chars=3200)
        if len(chunk_text) < 50:
            continue
        things.append(make_thing(
            cid, cname, "DocumentChunk", chunk_text,
            source_kind="web_chunk", corpus="bl1101", graph_id="bl1101",
            provenance=PROVENANCE,
            extra={"retrieved_at": TODAY_ISO}))
        associations.append(make_assoc(
            "beamline:BL-11-0-1-2", "rel:hasDocument", cid, PROVENANCE))

    status = TODAY_ISO
    print(f"[INFO]  ALS page things: {len(things)}, associations: {len(associations)}")
    return things, associations, status


# ---------------------------------------------------------------------------
# Step 2: Merge into v7
# ---------------------------------------------------------------------------

def merge_and_write_v7(
    bp_things, bp_assocs,
    kd_things, kd_assocs,
    als_things, als_assocs,
    als_page_status: str,
):
    print(f"[INFO] Loading v6: {KG_V6_PATH}")
    with open(KG_V6_PATH, encoding="utf-8") as fh:
        kg = json.load(fh)

    existing_things: list[dict] = kg.get("things", [])
    existing_assocs: list[dict] = kg.get("associations", [])

    # Build ID sets to prevent duplicates
    existing_ids = {t["id"] for t in existing_things}
    existing_assoc_keys = {
        (a["subject"], a["predicate"], a["object"]) for a in existing_assocs
    }

    new_things = []
    for t in bp_things + kd_things + als_things:
        if t["id"] not in existing_ids:
            new_things.append(t)
            existing_ids.add(t["id"])
        else:
            print(f"[INFO]  Skipping duplicate thing: {t['id']}")

    new_assocs = []
    for a in bp_assocs + kd_assocs + als_assocs:
        key = (a["subject"], a["predicate"], a["object"])
        if key not in existing_assoc_keys:
            new_assocs.append(a)
            existing_assoc_keys.add(key)
        else:
            print(f"[INFO]  Skipping duplicate assoc: {key}")

    # Backfill entityType on existing things that only have category
    for t in existing_things:
        if "entityType" not in t and "category" in t:
            t["entityType"] = t["category"]

    # Merge
    merged_things = existing_things + new_things
    merged_assocs = existing_assocs + new_assocs

    # Get knowledge.md mtime for corpus_revision
    kd_mtime = os.path.getmtime(KNOWLEDGE_MD_PATH)
    kd_mtime_str = datetime.datetime.utcfromtimestamp(kd_mtime).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Update metadata
    meta = kg.get("metadata", {})
    old_cr = meta.get("corpus_revision", {})
    if isinstance(old_cr, dict):
        corpus_revision = dict(old_cr)
    else:
        corpus_revision = {}
    corpus_revision.update({
        "bl1101_blueprint": "version09092026",
        "bl1101knowledge_md": kd_mtime_str,
        "als_public_page": als_page_status,
    })

    meta["corpus_revision"] = corpus_revision
    meta["graph_snapshot"] = {
        "name": "matkg_bl1101_v7",
        "version": 7,
        "written_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": len(merged_things),
        "edges": len(merged_assocs),
        "path": "storage/kg/matkg_bl1101_v7.json",
        "promoted_from": "matkg_bl1101_v6",
        "promotion_type": "doc-ingest",
        "new_things": len(new_things),
        "new_assocs": len(new_assocs),
    }

    v7 = {
        "things": merged_things,
        "associations": merged_assocs,
        "metadata": meta,
        # Also expose as 'nodes'/'edges' for verify-script compatibility
        "nodes": merged_things,
        "edges": merged_assocs,
    }

    print(f"[INFO] Writing v7: {KG_V7_PATH}")
    with open(KG_V7_PATH, "w", encoding="utf-8") as fh:
        json.dump(v7, fh, indent=2, ensure_ascii=False)

    return len(merged_things), len(merged_assocs), len(new_things), len(new_assocs)


# ---------------------------------------------------------------------------
# Step 3: Verify
# ---------------------------------------------------------------------------

def verify():
    with open(KG_V7_PATH, encoding="utf-8") as fh:
        kg = json.load(fh)

    # Use 'things' (authoritative) but also check 'nodes' alias
    things = kg.get("things", [])
    nodes = kg.get("nodes", things)  # fallback to things

    chunks = [n for n in nodes if n.get("entityType") == "DocumentChunk"
              or n.get("category") == "DocumentChunk"]
    persons = [n for n in nodes if n.get("entityType") == "Person"
               or n.get("category") == "Person"]

    print(f"\n{'='*60}")
    print(f"  v7 Verification")
    print(f"{'='*60}")
    print(f"  Total nodes (things): {len(things)}")
    print(f"  DocumentChunk nodes:  {len(chunks)}")
    print(f"  Person nodes:         {len(persons)}")
    for c in chunks:
        desc = c.get("description", "")
        print(f"    chunk: {c.get('id')} ({len(str(desc))} chars)")
    print(f"\n  Edges (associations): {len(kg.get('associations', []))}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"[INFO] Starting bl1101 KG v7 ingest — {TODAY_ISO}")

    bp_things, bp_assocs = parse_blueprint_html()
    kd_things, kd_assocs = parse_knowledge_md()

    als_things, als_assocs, als_status = fetch_als_page()

    total_nodes, total_edges, new_things, new_assocs = merge_and_write_v7(
        bp_things, bp_assocs,
        kd_things, kd_assocs,
        als_things, als_assocs,
        als_status,
    )

    print(f"[INFO] Done.")
    print(f"  Total nodes: {total_nodes}")
    print(f"  Total edges: {total_edges}")
    print(f"  New things:  {new_things}")
    print(f"  New assocs:  {new_assocs}")
    print(f"  ALS page status: {als_status}")

    verify()


if __name__ == "__main__":
    main()
