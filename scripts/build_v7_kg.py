#!/usr/bin/env python3
"""Build matkg_bl1101_v7.json from v6 + ALS public page + local cached docs.

New content in v7:
  - DocumentChunk nodes from: ALS public page, blueprint HTML, bl1101knowledge.md
  - Instrument node: beamline:Instrument-AXIS-SXR-40
  - Technique nodes: RSOXS-transmission, RSOXS-reflection, P-RSoXS
  - hasContact edges: BL-11-0-1-2 → Person (Cheng Wang, Thomas Ferron)
  - hasCapability edges: BL-11-0-1-2 → Technique nodes
  - hasInstrument edges: BL-11-0-1-2 → Instrument nodes
  - corpus_revision fields on Beamline and Person nodes
  - Updated metadata.corpus_revision with bl1101_blueprint, bl1101knowledge_md, als_public_page keys
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_URL = "https://als.lbl.gov/beamlines/11-0-1-2/"
BEAMLINE_ID = "beamline:BL-11-0-1-2"
OPS_DOC_ID = "beamline:ops-doc-ALS-11-0-1-2-public-page"

BLUEPRINT_PATH = REPO_ROOT / "papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html"
KNOWLEDGE_MD_PATH = REPO_ROOT / ".cache/bl1101/repos/bcs2sim-ophyd/info/bl1101knowledge.md"


def fetch_als_page() -> tuple[str, bool]:
    """Fetch the ALS beamline page; return (page_text, fetch_ok)."""
    try:
        req = urllib.request.Request(
            PROVENANCE_URL,
            headers={"User-Agent": "FAIR2WISE-bl1101-ingest/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&amp;", "&", re.sub(r"&#\d+;", " ", text))
        text = re.sub(r"\s+", " ", text).strip()
        m = re.search(
            r"(Resonant Soft X-Ray Scattering.*?)(?=The ALS mission|Copyright)", text, re.DOTALL
        )
        return (m.group(1).strip()[:5000] if m else text[:5000]), True
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: ALS page fetch failed: {exc}")
        return "", False


def strip_html(html: str) -> str:
    t = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&amp;", "&", re.sub(r"&#038;", "&", re.sub(r"&#\d+;", " ", t)))
    return re.sub(r"\s+", " ", t).strip()


def chunk_text(text: str, max_len: int = 2000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def make_doc_chunk(
    chunk_id: str,
    name: str,
    text: str,
    provenance: str,
    section: str = "",
    index: int = 0,
) -> dict:
    description = (text[:600] + "…") if len(text) > 600 else text
    return {
        "id": chunk_id,
        "name": name,
        "category": "DocumentChunk",
        "raw_category": None,
        "description": description,
        "pages": [],
        "source_papers": [provenance],
        "context_snippets": [],
        "formula": "",
        "formula_validation": {},
        "properties": [],
        "publication_year": None,
        "paper_title": name,
        "source_metadata": {},
        "publications": [{"source_paper": provenance}],
        "authors": [],
        "institutions": [],
        "doi": None,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages_range": None,
        "abstract_text": None,
        "keywords": [],
        "type": "beamline:DocumentChunk",
        # DocumentChunk-specific fields
        "entityType": "DocumentChunk",
        "provenance": provenance,
        "chunk_text": text,
        "section": section,
        "chunk_index": index,
        "source_kind": "paper_chunk",
        "corpus": "bl1101",
    }


def build_v7() -> None:
    retrieval_date = date.today().isoformat()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── 1. Fetch ALS page ────────────────────────────────────────────────────
    als_text, als_fetch_ok = fetch_als_page()
    if not als_fetch_ok:
        als_text = (
            "ALS Beamline 11.0.1.2 — Resonant Soft X-Ray Scattering (RSoXS). "
            "Source EPU5. Energy 165–1,500 eV. "
            "Techniques: XAS, SAXS, WAXS, GISAXS, Magnetic scattering, Resonant scattering. "
            "Polarization: user selectable, linear (H–V) and left/right circular. "
            "Flux 10^13 ph/s/0.1%BW at 800 eV; resolving power 4000 at 800 eV. "
            "Contacts: Cheng Wang cwang2@lbl.gov 510-486-4082; "
            "Thomas Ferron TJFerron@lbl.gov 510-486-4957. "
            "Detector: AXIS-SXR-40 (Tucsen Dhyana XFXV4040BSI)."
        )

    corpus_revision = f"public-page-{retrieval_date}"
    als_fetch_status = "live-fetch" if als_fetch_ok else "prior-knowledge-fallback"

    # ── 2. Read local cached docs ────────────────────────────────────────────
    bp_text = ""
    if BLUEPRINT_PATH.exists():
        bp_text = strip_html(BLUEPRINT_PATH.read_text(encoding="utf-8"))
        print(f"  Read blueprint: {len(bp_text)} chars")
    else:
        print(f"  WARNING: blueprint not found at {BLUEPRINT_PATH}")

    km_text = ""
    if KNOWLEDGE_MD_PATH.exists():
        km_text = KNOWLEDGE_MD_PATH.read_text(encoding="utf-8")
        print(f"  Read bl1101knowledge.md: {len(km_text)} chars")
    else:
        print(f"  WARNING: bl1101knowledge.md not found at {KNOWLEDGE_MD_PATH}")

    # ── 3. Load v6 ───────────────────────────────────────────────────────────
    v6_path = REPO_ROOT / "storage/kg/matkg_bl1101_v6.json"
    with open(v6_path, encoding="utf-8") as f:
        kg = json.load(f)

    things: list[dict] = kg["things"]
    associations: list[dict] = kg.get("associations", [])
    node_by_id = {n["id"]: n for n in things}

    # Helper: add node if not present
    def add_node(node: dict) -> bool:
        nid = node["id"]
        if nid not in node_by_id:
            things.append(node)
            node_by_id[nid] = node
            return True
        return False

    # Helper: add association if not present
    seen_assocs = {
        (a.get("subject"), a.get("predicate"), a.get("object"))
        for a in associations
    }

    def add_assoc(s: str, p: str, o: str, ev: str) -> bool:
        key = (s, p, o)
        if key in seen_assocs:
            return False
        associations.append({"subject": s, "predicate": p, "object": o, "has_evidence": ev})
        seen_assocs.add(key)
        return True

    # ── 4a. Update Beamline node ─────────────────────────────────────────────
    if BEAMLINE_ID in node_by_id:
        bl = node_by_id[BEAMLINE_ID]
        bl["corpus_revision"] = corpus_revision
        bl["provenance_url"] = PROVENANCE_URL
        bl["energy_range_eV"] = {"min_eV": 165, "max_eV": 1500, "label": "165–1,500 eV"}
        bl["polarization"] = (
            "user selectable; linear H–V continuously variable; "
            "left and right elliptical/circular"
        )
        bl["flux"] = "10^13 photons/s/0.1%BW at 800 eV"
        bl["resolving_power"] = "4000 at 800 eV"
        bl["spot_size_um"] = 100
        bl["resolution_nm"] = "down to 2 nm"
        bl["access_modes"] = ["Onsite", "Remote - ALS-operated"]
        bl["als_u_status"] = (
            "Temporarily closes during ALS-U dark time; resumes after commissioning"
        )
        if PROVENANCE_URL not in bl.get("source_papers", []):
            bl.setdefault("source_papers", []).append(PROVENANCE_URL)
        print("  Updated: Beamline node BL-11-0-1-2")

    # ── 4b. Update Person nodes ──────────────────────────────────────────────
    person_updates = {
        "beamline:Person-Cheng-Wang": {
            "phone": "510-486-4082",
            "corpus_revision": corpus_revision,
            "provenance_url": PROVENANCE_URL,
        },
        "beamline:Person-Thomas-Ferron": {
            "phone": "510-486-4957",
            "corpus_revision": corpus_revision,
            "provenance_url": PROVENANCE_URL,
        },
    }
    for pid, updates in person_updates.items():
        if pid in node_by_id:
            node_by_id[pid].update(updates)
            print(f"  Updated: {pid}")

    # ── 4c. Add hasContact edges ─────────────────────────────────────────────
    n_assoc_contact = sum([
        add_assoc(BEAMLINE_ID, "rel:hasContact", "beamline:Person-Cheng-Wang", PROVENANCE_URL),
        add_assoc(BEAMLINE_ID, "rel:hasContact", "beamline:Person-Thomas-Ferron", PROVENANCE_URL),
    ])
    print(f"  Added {n_assoc_contact} hasContact edges")

    # ── 4d. Add Instrument node ──────────────────────────────────────────────
    INSTRUMENT_AXIS_ID = "beamline:Instrument-AXIS-SXR-40"
    n_instrument = add_node({
        "id": INSTRUMENT_AXIS_ID,
        "name": "AXIS-SXR-40",
        "category": "Detector",
        "raw_category": None,
        "description": (
            "AXIS-SXR-40 area detector at BL 11.0.1.2: Tucsen Dhyana XFXV4040BSI sCMOS "
            "sensor on a theta arc inside the vacuum chamber. Mounted on CCD Theta arc "
            "with CCD X (in/out) and CCD Y (distance) motors. A photodiode is mounted "
            "on the camera body for diagnostic monitoring. Operated via the ADAxisSXR40 "
            "EPICS area-detector driver (sim_det in Bluesky)."
        ),
        "pages": [],
        "source_papers": [BLUEPRINT_PATH.name if BLUEPRINT_PATH.exists() else "blueprint_bl11012_version09092026.html"],
        "context_snippets": [],
        "formula": "",
        "formula_validation": {},
        "properties": [],
        "publication_year": None,
        "paper_title": None,
        "source_metadata": {},
        "publications": [],
        "authors": [],
        "institutions": [],
        "doi": None,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages_range": None,
        "abstract_text": None,
        "keywords": ["detector", "sCMOS", "AXIS-SXR-40", "Tucsen", "area detector"],
        "type": "beamline:Detector",
        "corpus_revision": corpus_revision,
        "provenance_url": PROVENANCE_URL,
    })
    print(f"  {'Added' if n_instrument else 'Already had'}: {INSTRUMENT_AXIS_ID}")

    # ── 4e. Add Technique nodes ──────────────────────────────────────────────
    techniques_to_add = [
        (
            "beamline:Technique-RSOXS-transmission",
            "RSoXS Transmission",
            "RSoXS (Resonant Soft X-Ray Scattering) in transmission geometry. "
            "Measures scattering through thin polymer or soft-matter films in the "
            "165–1,500 eV soft X-ray range. Primary technique at ALS BL 11.0.1.2. "
            "Used for morphology characterization of organic photovoltaics, block "
            "copolymers, and biological materials with element-specific contrast.",
        ),
        (
            "beamline:Technique-RSOXS-reflection",
            "RSoXS Reflection / Grazing Incidence",
            "RSoXS in grazing-incidence or reflectometry geometry. Measures surface "
            "and near-surface structure of thin films using the reflectometer endstation "
            "at ALS BL 11.0.1.2. Complements transmission RSoXS with depth-sensitivity "
            "and access to buried interfaces.",
        ),
        (
            "beamline:Technique-P-RSoXS",
            "P-RSoXS",
            "Polarized Resonant Soft X-Ray Scattering (P-RSoXS). Exploits the "
            "user-selectable polarization of the EPU5 undulator (linear H–V and "
            "left/right circular/elliptical) to probe molecular orientation and "
            "anisotropy in soft-matter films. Routinely used for organic "
            "semiconductor thin films at ALS BL 11.0.1.2.",
        ),
    ]
    n_tech = 0
    for tid, tname, tdesc in techniques_to_add:
        added = add_node({
            "id": tid,
            "name": tname,
            "category": "ExperimentalTechnique",
            "raw_category": None,
            "description": tdesc,
            "pages": [],
            "source_papers": [PROVENANCE_URL],
            "context_snippets": [],
            "formula": "",
            "formula_validation": {},
            "properties": [],
            "publication_year": None,
            "paper_title": None,
            "source_metadata": {},
            "publications": [],
            "authors": [],
            "institutions": [],
            "doi": None,
            "journal": None,
            "volume": None,
            "issue": None,
            "pages_range": None,
            "abstract_text": None,
            "keywords": ["RSoXS", "soft X-ray", "scattering", "ALS"],
            "type": "beamline:ExperimentalTechnique",
            "corpus_revision": corpus_revision,
            "provenance_url": PROVENANCE_URL,
        })
        if added:
            n_tech += 1
    print(f"  Added {n_tech} Technique nodes")

    # ── 4f. Add hasCapability and hasInstrument edges ─────────────────────────
    n_cap = sum([
        add_assoc(BEAMLINE_ID, "rel:hasCapability", "beamline:Technique-RSOXS-transmission", PROVENANCE_URL),
        add_assoc(BEAMLINE_ID, "rel:hasCapability", "beamline:Technique-RSOXS-reflection", PROVENANCE_URL),
        add_assoc(BEAMLINE_ID, "rel:hasCapability", "beamline:Technique-P-RSoXS", PROVENANCE_URL),
    ])
    n_inst = sum([
        add_assoc(BEAMLINE_ID, "rel:hasInstrument", INSTRUMENT_AXIS_ID, PROVENANCE_URL),
        add_assoc(BEAMLINE_ID, "rel:hasInstrument", "beamline:Detector-AXIS-SXR-40",
                  BLUEPRINT_PATH.name if BLUEPRINT_PATH.exists() else "blueprint_bl11012"),
    ])
    print(f"  Added {n_cap} hasCapability edges, {n_inst} hasInstrument edges")

    # ── 4g. Add DocumentChunk nodes ──────────────────────────────────────────
    doc_chunks: list[dict] = []

    # --- ALS public page chunk ---
    bp_prov = BLUEPRINT_PATH.name if BLUEPRINT_PATH.exists() else "blueprint_bl11012_version09092026.html"
    doc_chunks.append(make_doc_chunk(
        chunk_id="beamline:chunk-als-public-page-overview",
        name="ALS 11.0.1.2 Public Page — Beamline Overview",
        text=als_text,
        provenance=PROVENANCE_URL,
        section="overview",
        index=0,
    ))

    # --- Blueprint chunks ---
    if bp_text:
        # Chunk 1: header / overview (~first 2000 chars)
        doc_chunks.append(make_doc_chunk(
            chunk_id="beamline:chunk-blueprint-header",
            name="Blueprint BL 11.0.1.2 — Header and Beam Path",
            text=bp_text[:2000],
            provenance=bp_prov,
            section="header",
            index=0,
        ))
        # Chunk 2: stages / devices (~2000-4500)
        doc_chunks.append(make_doc_chunk(
            chunk_id="beamline:chunk-blueprint-stages",
            name="Blueprint BL 11.0.1.2 — Optical Stages and Devices",
            text=bp_text[2000:4500],
            provenance=bp_prov,
            section="stages",
            index=1,
        ))
        # Chunk 3: operating rules / safety (~4500-7000)
        doc_chunks.append(make_doc_chunk(
            chunk_id="beamline:chunk-blueprint-rules",
            name="Blueprint BL 11.0.1.2 — Operating Rules and Safety",
            text=bp_text[4500:7000],
            provenance=bp_prov,
            section="rules",
            index=2,
        ))

    # --- bl1101knowledge.md chunks ---
    if km_text:
        # Split into sections by ## headers
        sections = re.split(r"\n(?=## )", km_text)
        for i, section in enumerate(sections[:5]):  # max 5 sections
            s_title_m = re.match(r"##\s+(.+)", section.strip())
            s_title = s_title_m.group(1).strip() if s_title_m else f"Section {i}"
            chunk_id = f"beamline:chunk-bl1101knowledge-{i}"
            doc_chunks.append(make_doc_chunk(
                chunk_id=chunk_id,
                name=f"bl1101knowledge.md — {s_title}",
                text=section[:3000],
                provenance="bl1101knowledge.md",
                section=s_title,
                index=i,
            ))

    n_chunks = 0
    for chunk in doc_chunks:
        if add_node(chunk):
            n_chunks += 1
    print(f"  Added {n_chunks} DocumentChunk nodes (total now: {sum(1 for t in things if t.get('category') == 'DocumentChunk')})")

    # ── 4h. Add ops-doc RAG chunk node (OpsDoc category) ────────────────────
    if OPS_DOC_ID not in node_by_id:
        notes = "" if als_fetch_ok else " [prior knowledge – live fetch failed; pending re-fetch]"
        add_node({
            "id": OPS_DOC_ID,
            "name": "ALS 11.0.1.2 Public Beamline Page",
            "category": "OpsDoc",
            "raw_category": None,
            "description": (
                f"RAG chunk ingested from the ALS public beamline page ({PROVENANCE_URL}). "
                f"Retrieved {retrieval_date}. "
                "Contains: energy range 165–1,500 eV, EPU5 source, techniques "
                "(RSoXS/SAXS/WAXS/GISAXS/XAS/Magnetic scattering/Resonant scattering), "
                "polarization (user selectable H–V and elliptical/circular), "
                "contacts (Cheng Wang, Thomas Ferron), and ALS-U closure notice."
                + notes
            ),
            "pages": [],
            "source_papers": [PROVENANCE_URL],
            "context_snippets": [],
            "formula": "",
            "formula_validation": {},
            "properties": [],
            "publication_year": None,
            "paper_title": "ALS Beamline 11.0.1.2",
            "source_metadata": {},
            "publications": [{"source_paper": PROVENANCE_URL}],
            "authors": ["Cheng Wang", "Thomas Ferron"],
            "institutions": ["Lawrence Berkeley National Laboratory"],
            "doi": None,
            "journal": None,
            "volume": None,
            "issue": None,
            "pages_range": None,
            "abstract_text": None,
            "keywords": ["RSoXS", "NEXAFS", "SAXS", "WAXS", "ALS", "beamline", "soft X-ray"],
            "type": "beamline:OpsDoc",
            "source_kind": "paper_chunk",
            "corpus": "bl1101",
            "provenance_url": PROVENANCE_URL,
            "retrieval_date": retrieval_date,
            "fetch_status": als_fetch_status,
            "corpus_revision": corpus_revision,
            "page_text": als_text,
            "energy_range_eV": {"min_eV": 165, "max_eV": 1500},
            "source_epu": "EPU5",
            "techniques": [
                "RSoXS", "SAXS", "WAXS", "GISAXS",
                "XAS", "XFS", "Coherent scattering",
                "Magnetic scattering", "Resonant scattering",
            ],
            "polarization": (
                "user selectable; linear H–V continuously variable; "
                "left and right elliptical/circular"
            ),
            "flux": "10^13 photons/s/0.1%BW at 800 eV",
            "resolving_power": "4000 at 800 eV",
            "spot_size_um": 100,
            "resolution_nm": "down to 2 nm",
            "samples": [
                "Polymer thin films",
                "Solid thin films",
                "Vacuum compatible liquid cell",
            ],
            "contacts": [
                {"name": "Cheng Wang", "email": "cwang2@lbl.gov", "phone": "510-486-4082"},
                {"name": "Thomas Ferron", "email": "TJFerron@lbl.gov", "phone": "510-486-4957"},
            ],
            "beamline_phone": "510-495-2010",
            "als_u_status": "Temporarily closes during ALS-U dark time",
            "ingested_at": today_utc,
        })
        print(f"  Added: ops-doc node {OPS_DOC_ID}")

    # ── 4i. Add associations for new nodes ───────────────────────────────────
    ev = PROVENANCE_URL
    extra_assocs = sum([
        add_assoc(OPS_DOC_ID, "rel:part_of", BEAMLINE_ID, ev),
        add_assoc(OPS_DOC_ID, "rel:related_to", "beamline:Person-Cheng-Wang", ev),
        add_assoc(OPS_DOC_ID, "rel:related_to", "beamline:Person-Thomas-Ferron", ev),
        add_assoc(BEAMLINE_ID, "rel:related_to", OPS_DOC_ID, ev),
        add_assoc(INSTRUMENT_AXIS_ID, "rel:part_of", BEAMLINE_ID, bp_prov),
        add_assoc(BEAMLINE_ID, "rel:hasCapability", "beamline:Technique-RSOXS-transmission", ev),
        add_assoc(BEAMLINE_ID, "rel:hasCapability", "beamline:Technique-P-RSoXS", ev),
    ])
    # Chunk → beamline
    for chunk in doc_chunks:
        extra_assocs += add_assoc(chunk["id"], "rel:part_of", BEAMLINE_ID, chunk["provenance"])
    print(f"  Added {extra_assocs} additional associations")

    # ── 5. Sort ──────────────────────────────────────────────────────────────
    things.sort(key=lambda n: n.get("id") or "")
    associations.sort(
        key=lambda e: (e.get("subject", ""), e.get("predicate", ""), e.get("object", ""))
    )

    # ── 6. Update metadata ───────────────────────────────────────────────────
    meta = kg.setdefault("metadata", {})
    snap = meta.setdefault("graph_snapshot", {})
    snap["version"] = 7
    snap["name"] = "matkg_bl1101_v7"
    snap["written_at"] = today_utc
    snap["nodes"] = len(things)
    snap["edges"] = len(associations)

    # corpus_revision with the three required keys
    cr = meta.setdefault("corpus_revision", {})
    cr["als_public_page"] = {
        "url": PROVENANCE_URL,
        "retrieval_date": retrieval_date,
        "fetch_status": als_fetch_status,
        "corpus_revision_tag": corpus_revision,
    }
    cr["bl1101_blueprint"] = {
        "path": str(BLUEPRINT_PATH.relative_to(REPO_ROOT)) if BLUEPRINT_PATH.exists() else bp_prov,
        "retrieval_date": retrieval_date,
        "status": "ok" if BLUEPRINT_PATH.exists() else "missing",
    }
    cr["bl1101knowledge_md"] = {
        "path": str(KNOWLEDGE_MD_PATH.relative_to(REPO_ROOT)) if KNOWLEDGE_MD_PATH.exists() else "not-found",
        "retrieval_date": retrieval_date,
        "status": "ok" if KNOWLEDGE_MD_PATH.exists() else "missing",
    }
    meta["ingest_version"] = "1.3.0"

    kg["things"] = things
    kg["associations"] = associations

    # ── 7. Write v7 ──────────────────────────────────────────────────────────
    v7_path = REPO_ROOT / "storage/kg/matkg_bl1101_v7.json"
    with open(v7_path, "w", encoding="utf-8") as f:
        json.dump(kg, f, indent=2, ensure_ascii=False)

    n_chunks_total = sum(1 for t in things if t.get("category") == "DocumentChunk")
    print(f"\nWrote {v7_path}")
    print(f"  Nodes: {len(things)} (was 1129)")
    print(f"  Associations: {len(associations)}")
    print(f"  DocumentChunk nodes: {n_chunks_total}")
    print(f"  ALS fetch status: {als_fetch_status}")
    print(f"  Corpus revision tag: {corpus_revision}")


if __name__ == "__main__":
    build_v7()
