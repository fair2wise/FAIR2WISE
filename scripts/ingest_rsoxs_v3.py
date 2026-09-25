"""
json2kg-slots: Build matkg_rsoxs_v3.json from extracted_terms_rsoxs_v1_repaired.json

Converts repaired extracted terms into a proper KG JSON file with:
- Deterministic URIs for all nodes
- Typed numeric slots promoted to proper schema fields
- Stubs dropped (quarantined in the repaired file)
- PROV-O provenance edges
- corpus_revision: draft
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
REPAIRED_FILE = ROOT / "storage/terminology/extracted_terms_rsoxs_v1_repaired.json"
V2_KG_FILE = ROOT / "storage/kg/matkg_rsoxs_v2.json"
OUTPUT_FILE = ROOT / "storage/kg/matkg_rsoxs_v3.json"

# ---------------------------------------------------------------------------
# PROV-O constants (mirroring app/modules/prov.py)
# ---------------------------------------------------------------------------

PROV_NS = "http://www.w3.org/ns/prov#"
WAS_GENERATED_BY = f"{PROV_NS}wasGeneratedBy"
WAS_DERIVED_FROM = f"{PROV_NS}wasDerivedFrom"
USED = f"{PROV_NS}used"
STARTED_AT_TIME = f"{PROV_NS}startedAtTime"
ENDED_AT_TIME = f"{PROV_NS}endedAtTime"
PROV_ACTIVITY = "prov:Activity"
PROV_ENTITY = "prov:Entity"

ACTIVITY_ID = "rsoxs:Activity-json2kg-v3"
SOURCE_ENTITY_ID = "rsoxs:Entity-RepairCorpus-v1"
GRAPH_ID = "rsoxs_v3"

# ---------------------------------------------------------------------------
# Schema slots (from rsoxs_schema.yaml)
# ---------------------------------------------------------------------------

RSOXS_SLOTS = [
    "photon_energy_eV",
    "absorption_edge",
    "scattering_technique",
    "polarization",
    "geometry",
    "q_range",
    "contrast_mechanism",
]

# Regex for photon energy extraction
ENERGY_RE = re.compile(r"(\d+\.?\d*)\s*(?:eV)\b", re.IGNORECASE)
ENERGY_KEV_RE = re.compile(r"(\d+\.?\d*)\s*keV\b", re.IGNORECASE)

# Absorption edges
ABSORPTION_EDGE_RE = re.compile(
    r"\b(C|N|O|S|Si|F|Cl|Br|Fe|Ni|Cu|Mn|Co|Cr|V|Ti|La|Nd|Gd|Sm|Er)\s+K-?edge\b|"
    r"\b(C|N|O|S|Si|F|Cl|Br|Fe|Ni|Cu|Mn|Co|Cr|V|Ti|La|Nd|Gd|Sm|Er)\s+L-?edge\b",
    re.IGNORECASE,
)

# Polarization patterns
POLARIZATION_RE = re.compile(
    r"\b(linear(?:ly)?\s+polarized?|circular(?:ly)?\s+polarized?|"
    r"unpolarized?|horizontal|vertical|P-RSoXS|h-polarization|v-polarization)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    # Lowercase
    text = text.lower()
    # Replace non-alphanumeric with dash
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Strip leading/trailing dashes
    text = text.strip("-")
    # Collapse multiple dashes
    text = re.sub(r"-{2,}", "-", text)
    return text or "unknown"


def make_id(entity_type: str, label: str, v2_id_map: dict[str, str]) -> str:
    """Generate a deterministic node ID, preserving v2 IDs when available."""
    label_lower = label.lower()
    if label_lower in v2_id_map:
        return v2_id_map[label_lower]
    slug = slugify(label)
    return f"rsoxs:{entity_type}-{slug}"


def extract_photon_energy(text: str) -> float | None:
    """Extract photon energy in eV from text."""
    # Try eV first
    m = ENERGY_RE.search(text)
    if m:
        val = float(m.group(1))
        # Sanity check: RSoXS typically 100–2000 eV
        if 50 <= val <= 5000:
            return val
    # Try keV
    m = ENERGY_KEV_RE.search(text)
    if m:
        val = float(m.group(1)) * 1000
        if 50 <= val <= 5000:
            return val
    return None


def extract_absorption_edge(text: str) -> str | None:
    """Extract absorption edge from text."""
    m = ABSORPTION_EDGE_RE.search(text)
    if m:
        return m.group(0)
    return None


def extract_polarization(text: str) -> str | None:
    """Extract polarization from text."""
    m = POLARIZATION_RE.search(text)
    if m:
        return m.group(0)
    return None


def extract_slots(term: dict) -> dict:
    """Extract typed schema slots from a term dict."""
    slots: dict = {s: None for s in RSOXS_SLOTS}

    # Build searchable text
    defn = term.get("definition", "") or ""
    term_name = term.get("term", "") or ""
    snippets = term.get("context_snippets", []) or []
    snippet_text = " ".join(
        s.get("text", "") if isinstance(s, dict) else str(s) for s in snippets
    )
    relations = term.get("relations", []) or []
    rel_text = " ".join(
        r.get("related_term", "") for r in relations if r.get("related_term")
    )
    full_text = f"{term_name} {defn} {rel_text} {snippet_text}"

    # Photon energy
    energy = extract_photon_energy(full_text)
    if energy is not None:
        slots["photon_energy_eV"] = energy

    # Absorption edge
    edge = extract_absorption_edge(full_text)
    if edge:
        slots["absorption_edge"] = edge

    # Polarization
    pol = extract_polarization(full_text)
    if pol:
        slots["polarization"] = pol

    # Scattering technique from term/definition
    tech_matches = re.findall(
        r"\b(RSoXS|R-SoXS|P-RSoXS|CyRSoXS|SAXS|GISAXS|GIWAXS|SANS|WAXS)\b",
        full_text,
        re.IGNORECASE,
    )
    if tech_matches:
        slots["scattering_technique"] = tech_matches[0].upper()

    # Geometry (transmission/reflection)
    geo_m = re.search(
        r"\b(transmission|reflection|grazing.incidence|GISAXS|GIWAXS)\b",
        full_text,
        re.IGNORECASE,
    )
    if geo_m:
        slots["geometry"] = geo_m.group(0)

    return slots


def get_best_description(term: dict, max_chars: int = 500) -> str:
    """Get best available description, capped at max_chars."""
    defn = (term.get("definition", "") or "").strip()
    if defn:
        return defn[:max_chars]
    # Fall back to first snippet
    snippets = term.get("context_snippets", []) or []
    if snippets:
        s = snippets[0]
        text = s.get("text", "") if isinstance(s, dict) else str(s)
        return text.strip()[:max_chars]
    return ""


def get_papers(term: dict) -> list[str]:
    """Extract paper identifiers (DOIs) from source_papers."""
    papers = term.get("source_papers", []) or []
    dois = []
    for p in papers:
        if isinstance(p, str):
            # Convert filename-style to DOI: "10.1021_acs.jpcb.4c05774.pdf" -> "10.1021/acs.jpcb.4c05774"
            doi = p.replace("_", "/", 1)  # only first underscore
            doi = re.sub(r"\.pdf$", "", doi, flags=re.IGNORECASE)
            dois.append(doi)
    return dois


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_v3_kg() -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()

    print(f"Loading repaired terms from {REPAIRED_FILE}...")
    with open(REPAIRED_FILE) as f:
        repaired = json.load(f)

    terms = repaired.get("terms", [])
    quarantined = repaired.get("_quarantined_stubs", [])
    print(f"  Terms: {len(terms)}, Quarantined stubs: {len(quarantined)}")

    # Build quarantined term names set for extra safety
    quarantined_names = {
        (s.get("term", "") if isinstance(s, dict) else str(s)).lower()
        for s in quarantined
    }

    print(f"Loading v2 KG from {V2_KG_FILE} for ID preservation...")
    with open(V2_KG_FILE) as f:
        v2_kg = json.load(f)

    # Build v2 ID map: lowercase_name -> id
    v2_things = v2_kg.get("things", [])
    v2_id_map: dict[str, str] = {}
    for thing in v2_things:
        name = (thing.get("name", "") or "").lower()
        tid = thing.get("id", "")
        if name and tid:
            v2_id_map[name] = tid
    print(f"  v2 ID map: {len(v2_id_map)} entries")

    # ---------------------------------------------------------------------------
    # Build nodes
    # ---------------------------------------------------------------------------

    nodes: list[dict] = []
    edges: list[dict] = []

    # Track node IDs for edge building
    node_id_by_label: dict[str, str] = {}
    # Track all label -> id for relation resolution
    label_to_id: dict[str, str] = {}

    skipped_thing = 0
    skipped_quarantined = 0

    print("Building nodes...")

    for term in terms:
        label = (term.get("term", "") or "").strip()
        if not label:
            continue

        category = term.get("category", "Unknown") or "Unknown"

        # Skip Thing + needs_review
        if category == "Thing" and term.get("needs_review"):
            skipped_thing += 1
            continue

        # Skip quarantined stubs
        if label.lower() in quarantined_names:
            skipped_quarantined += 1
            continue

        # Generate or preserve ID
        node_id = make_id(category, label, v2_id_map)

        # Build slots
        slots = {s: None for s in RSOXS_SLOTS}
        if category in ("RSoXSMeasurement", "ExperimentalTechnique", "Measurement"):
            slots = extract_slots(term)

        # Papers/DOIs
        papers = get_papers(term)

        # Pages
        pages = term.get("pages", []) or []
        if not isinstance(pages, list):
            pages = []

        # Aliases
        aliases = term.get("aliases", []) or []
        if not isinstance(aliases, list):
            aliases = []

        # Description (capped at 500 chars)
        description = get_best_description(term, max_chars=500)

        node = {
            "id": node_id,
            "label": label,
            "entityType": category,
            "description": description,
            "aliases": aliases,
            "papers": papers,
            "pages": pages,
            "slots": slots,
            "graph_id": GRAPH_ID,
        }

        nodes.append(node)
        node_id_by_label[label.lower()] = node_id
        label_to_id[label.lower()] = node_id

    print(f"  Built {len(nodes)} nodes (skipped: {skipped_thing} Thing+needs_review, {skipped_quarantined} quarantined)")

    # ---------------------------------------------------------------------------
    # Build edges from relations
    # ---------------------------------------------------------------------------

    print("Building relation edges...")
    rel_edges_count = 0

    for term in terms:
        label = (term.get("term", "") or "").strip()
        if not label:
            continue
        category = term.get("category", "Unknown") or "Unknown"
        if category == "Thing" and term.get("needs_review"):
            continue
        if label.lower() in quarantined_names:
            continue

        subject_id = label_to_id.get(label.lower())
        if not subject_id:
            continue

        for rel in (term.get("relations", []) or []):
            if not isinstance(rel, dict):
                continue
            related_term = (rel.get("related_term", "") or "").strip()
            relation_type = (rel.get("relation", "") or "").strip()
            if not related_term or not relation_type:
                continue

            object_id = label_to_id.get(related_term.lower())
            if not object_id:
                # Create a placeholder if target not in our node set
                # Use v2 ID if available, else rsoxs:Unknown-slug
                object_id = v2_id_map.get(related_term.lower())
                if not object_id:
                    slug = slugify(related_term)
                    object_id = f"rsoxs:Unknown-{slug}"

            edge = {
                "subject": subject_id,
                "predicate": f"rel:{relation_type}",
                "object": object_id,
                "has_evidence": None,
            }
            edges.append(edge)
            rel_edges_count += 1

    print(f"  Built {rel_edges_count} relation edges")

    # ---------------------------------------------------------------------------
    # PROV-O nodes and edges
    # ---------------------------------------------------------------------------

    print("Adding PROV-O provenance...")

    # Activity node for this json2kg pass
    activity_node = {
        "id": ACTIVITY_ID,
        "entityType": PROV_ACTIVITY,
        "type": PROV_ACTIVITY,
        "label": "json2kg-v3",
        "description": "json2kg pass converting extracted_terms_rsoxs_v1_repaired.json to matkg_rsoxs_v3.json",
        "graph_id": GRAPH_ID,
        STARTED_AT_TIME: now_iso,
    }

    # Source corpus entity
    source_entity_node = {
        "id": SOURCE_ENTITY_ID,
        "entityType": PROV_ENTITY,
        "type": PROV_ENTITY,
        "label": "RepairCorpus-v1",
        "description": "Repaired extracted terms corpus (extracted_terms_rsoxs_v1_repaired.json, corpus_revision: repaired-draft)",
        "path": "storage/terminology/extracted_terms_rsoxs_v1_repaired.json",
        "corpus_revision": "repaired-draft",
        "graph_id": GRAPH_ID,
    }

    nodes.append(activity_node)
    nodes.append(source_entity_node)

    # Provenance edges for each KG node
    prov_edges: list[dict] = []

    for node in nodes:
        nid = node["id"]
        if nid in (ACTIVITY_ID, SOURCE_ENTITY_ID):
            continue

        # node wasGeneratedBy activity
        prov_edges.append({
            "subject": nid,
            "predicate": WAS_GENERATED_BY,
            "object": ACTIVITY_ID,
            "type": "prov:wasGeneratedBy",
        })
        # node wasDerivedFrom source corpus
        prov_edges.append({
            "subject": nid,
            "predicate": WAS_DERIVED_FROM,
            "object": SOURCE_ENTITY_ID,
            "type": "prov:wasDerivedFrom",
        })

    edges.extend(prov_edges)
    print(f"  Added {len(prov_edges)} PROV-O edges")

    # ---------------------------------------------------------------------------
    # Top-level metadata
    # ---------------------------------------------------------------------------

    prov_metadata = {
        "entity_id": f"{GRAPH_ID}:KGSnapshot-v3",
        "type": "prov:Entity",
        "wasDerivedFrom": ["rsoxs:Entity-RepairCorpus-v1"],
        "wasGeneratedBy": ACTIVITY_ID,
        "atTime": now_iso,
    }

    kg = {
        "graph_id": GRAPH_ID,
        "corpus_revision": "draft",
        "source_terms": "extracted_terms_rsoxs_v1_repaired.json",
        "generated_at": now_iso,
        "prov_metadata": prov_metadata,
        "nodes": nodes,
        "edges": edges,
    }

    return kg


def main() -> None:
    kg = build_v3_kg()
    nodes = kg["nodes"]
    edges = kg["edges"]

    print(f"\nWriting {len(nodes)} nodes and {len(edges)} edges to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(kg, f, indent=2, ensure_ascii=False)

    # Compute file size
    size_mb = OUTPUT_FILE.stat().st_size / 1_048_576
    print(f"  File size: {size_mb:.1f} MB")

    # ---------------------------------------------------------------------------
    # Quick verification
    # ---------------------------------------------------------------------------

    print("\n=== Verification ===")
    by_type: dict[str, int] = defaultdict(int)
    for n in nodes:
        t = n.get("entityType", "Unknown")
        by_type[t] += 1

    print(f"Total nodes: {len(nodes)}")
    print(f"Total edges: {len(edges)}")
    print("By type (top 15):")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1])[:15]:
        print(f"  {t}: {c}")

    prov_edges = [e for e in edges if "prov" in e.get("predicate", "") or "prov:" in e.get("type", "")]
    print(f"PROV-O edges: {len(prov_edges)}")

    with_slots = [
        n for n in nodes
        if any(v is not None for v in n.get("slots", {}).values())
    ]
    print(f"Nodes with numeric slots: {len(with_slots)}")

    print("\nDone!")


if __name__ == "__main__":
    main()
