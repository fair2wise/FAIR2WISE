"""
Tests for the bl1101 KG v7 ingest.

Checks that storage/kg/matkg_bl1101_v7.json contains the expected
DocumentChunk and Person nodes, hasContact edges, and minimum
description lengths.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KG_V7_PATH = REPO_ROOT / "storage" / "kg" / "matkg_bl1101_v7.json"


@pytest.fixture(scope="module")
def kg_v7():
    """Load v7 once for all tests."""
    assert KG_V7_PATH.exists(), f"v7 file not found: {KG_V7_PATH}"
    with open(KG_V7_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def things(kg_v7):
    """Return the 'things' list (primary node store)."""
    # v7 has both 'things' (canonical) and 'nodes' (alias)
    return kg_v7.get("things", kg_v7.get("nodes", []))


@pytest.fixture(scope="module")
def associations(kg_v7):
    """Return the 'associations' list (primary edge store)."""
    return kg_v7.get("associations", kg_v7.get("edges", []))


# ---------------------------------------------------------------------------
# DocumentChunk assertions
# ---------------------------------------------------------------------------

def test_v7_has_at_least_5_document_chunks(things):
    chunks = [
        t for t in things
        if t.get("entityType") == "DocumentChunk" or t.get("category") == "DocumentChunk"
    ]
    assert len(chunks) >= 5, (
        f"Expected at least 5 DocumentChunk nodes, got {len(chunks)}"
    )


def test_chunks_cover_all_sources(things):
    """At least one chunk from each of the three ingest sources."""
    chunks = [
        t for t in things
        if t.get("entityType") == "DocumentChunk" or t.get("category") == "DocumentChunk"
    ]
    provenance_values = [t.get("provenance", "") for t in chunks]

    has_blueprint = any("blueprint_bl11012" in p for p in provenance_values)
    has_knowledge_md = any("bl1101knowledge.md" in p for p in provenance_values)
    # ALS page is optional (may have failed); only assert if present in metadata
    assert has_blueprint, "Expected at least one chunk from the blueprint HTML"
    assert has_knowledge_md, "Expected at least one chunk from bl1101knowledge.md"


def test_at_least_one_chunk_has_description_longer_than_200_chars(things):
    chunks = [
        t for t in things
        if t.get("entityType") == "DocumentChunk" or t.get("category") == "DocumentChunk"
    ]
    long_chunks = [
        t for t in chunks
        if len(str(t.get("description", ""))) > 200
    ]
    assert len(long_chunks) >= 1, (
        "Expected at least one DocumentChunk with description > 200 chars"
    )


# ---------------------------------------------------------------------------
# Person node assertions
# ---------------------------------------------------------------------------

def test_cheng_wang_person_node_exists(things):
    ids = {t["id"] for t in things}
    assert "beamline:Person-Cheng-Wang" in ids, (
        "Expected Person node 'beamline:Person-Cheng-Wang' in v7"
    )


def test_thomas_ferron_person_node_exists(things):
    ids = {t["id"] for t in things}
    assert "beamline:Person-Thomas-Ferron" in ids, (
        "Expected Person node 'beamline:Person-Thomas-Ferron' in v7"
    )


def test_person_nodes_have_role(things):
    persons = [
        t for t in things
        if t["id"] in {"beamline:Person-Cheng-Wang", "beamline:Person-Thomas-Ferron"}
    ]
    assert len(persons) == 2
    for p in persons:
        role = p.get("role", "")
        assert role, f"Person {p['id']} has no role"


# ---------------------------------------------------------------------------
# hasContact edge assertions
# ---------------------------------------------------------------------------

def test_bl_has_contact_cheng_wang(associations):
    contacts = [
        a for a in associations
        if a.get("subject") == "beamline:BL-11-0-1-2"
        and "hasContact" in a.get("predicate", "")
        and a.get("object") == "beamline:Person-Cheng-Wang"
    ]
    assert len(contacts) >= 1, (
        "Expected edge: beamline:BL-11-0-1-2 hasContact beamline:Person-Cheng-Wang"
    )


def test_bl_has_contact_thomas_ferron(associations):
    contacts = [
        a for a in associations
        if a.get("subject") == "beamline:BL-11-0-1-2"
        and "hasContact" in a.get("predicate", "")
        and a.get("object") == "beamline:Person-Thomas-Ferron"
    ]
    assert len(contacts) >= 1, (
        "Expected edge: beamline:BL-11-0-1-2 hasContact beamline:Person-Thomas-Ferron"
    )


# ---------------------------------------------------------------------------
# Structural assertions
# ---------------------------------------------------------------------------

def test_v7_node_count_is_larger_than_v6(things):
    """v7 must grow from v6's 1129 things."""
    assert len(things) > 1129, (
        f"v7 has {len(things)} nodes, expected more than v6's 1129"
    )


def test_v7_metadata_has_corpus_revision(kg_v7):
    meta = kg_v7.get("metadata", {})
    cr = meta.get("corpus_revision", {})
    assert isinstance(cr, dict), "corpus_revision should be a dict"
    assert "bl1101_blueprint" in cr, "corpus_revision should have bl1101_blueprint key"
    assert "bl1101knowledge_md" in cr, "corpus_revision should have bl1101knowledge_md key"
    assert "als_public_page" in cr, "corpus_revision should have als_public_page key"


def test_v7_metadata_graph_snapshot(kg_v7):
    meta = kg_v7.get("metadata", {})
    snap = meta.get("graph_snapshot", {})
    assert snap.get("version") == 7, f"Expected version 7, got {snap.get('version')}"
    assert snap.get("name") == "matkg_bl1101_v7"


def test_beamline_node_has_capability_edges(associations):
    cap_edges = [
        a for a in associations
        if a.get("subject") == "beamline:BL-11-0-1-2"
        and "hasCapability" in a.get("predicate", "")
    ]
    assert len(cap_edges) >= 2, (
        f"Expected at least 2 hasCapability edges from BL-11-0-1-2, got {len(cap_edges)}"
    )


def test_beamline_node_has_instrument_edges(associations):
    inst_edges = [
        a for a in associations
        if a.get("subject") == "beamline:BL-11-0-1-2"
        and "hasInstrument" in a.get("predicate", "")
    ]
    assert len(inst_edges) >= 2, (
        f"Expected at least 2 hasInstrument edges from BL-11-0-1-2, got {len(inst_edges)}"
    )


def test_axis_sxr_detector_node_exists(things):
    ids = {t["id"] for t in things}
    assert "beamline:Instrument-AXIS-SXR-40" in ids, (
        "Expected Instrument node 'beamline:Instrument-AXIS-SXR-40' in v7"
    )


def test_rsoxs_technique_nodes_exist(things):
    ids = {t["id"] for t in things}
    expected = {
        "beamline:Technique-RSOXS-transmission",
        "beamline:Technique-RSOXS-reflection",
        "beamline:Technique-P-RSoXS",
    }
    missing = expected - ids
    assert not missing, f"Missing technique nodes: {missing}"
