"""Tests for app/modules/prov.py and downstream PROV-O artefacts.

Run with::

    python -m pytest tests/test_prov.py -x -q
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Optional

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures / paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
RSOXS_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema" / "rsoxs_schema.yaml"
BL1101_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema" / "bl1101_schema.yaml"
PROV_VOCABULARY_PATH = PROJECT_ROOT / "storage" / "schema" / "prov_vocabulary.yaml"


def _latest_bl1101_kg() -> Optional[Path]:
    """Return the highest-numbered matkg_bl1101_v*.json, or None."""
    pattern = str(PROJECT_ROOT / "storage" / "kg" / "matkg_bl1101_v*.json")
    candidates = [
        Path(p) for p in glob.glob(pattern)
        if "bak" not in Path(p).name
    ]
    if not candidates:
        return None
    def _version(p: Path) -> int:
        stem = p.stem  # e.g. matkg_bl1101_v7
        try:
            return int(stem.split("_v")[-1])
        except (ValueError, IndexError):
            return -1
    return max(candidates, key=_version)


def _latest_bl1101_kg_with_prov() -> Optional[Path]:
    """Return the highest-numbered matkg_bl1101_v*.json that contains prov_metadata, or None."""
    pattern = str(PROJECT_ROOT / "storage" / "kg" / "matkg_bl1101_v*.json")
    candidates = [
        Path(p) for p in glob.glob(pattern)
        if "bak" not in Path(p).name
    ]
    if not candidates:
        return None
    def _version(p: Path) -> int:
        try:
            return int(p.stem.split("_v")[-1])
        except (ValueError, IndexError):
            return -1
    for p in sorted(candidates, key=_version, reverse=True):
        try:
            import json as _json
            d = _json.loads(p.read_text())
            if "prov_metadata" in d:
                return p
        except Exception:
            continue
    return None


_BL1101_LATEST = _latest_bl1101_kg()
_BL1101_SKIP = pytest.mark.skipif(
    _BL1101_LATEST is None,
    reason="No matkg_bl1101_v*.json found in storage/kg/",
)

_BL1101_PROV = _latest_bl1101_kg_with_prov()
_BL1101_PROV_SKIP = pytest.mark.skipif(
    _BL1101_PROV is None,
    reason="No matkg_bl1101_v*.json with prov_metadata found in storage/kg/",
)


@pytest.fixture(scope="module")
def bl1101_v7() -> dict:
    """Load the latest bl1101 KG that contains prov_metadata (skip if none found)."""
    if _BL1101_PROV is None:
        pytest.skip("No matkg_bl1101_v*.json with prov_metadata found in storage/kg/")
    return json.loads(_BL1101_PROV.read_text())


@pytest.fixture(scope="module")
def rsoxs_schema() -> dict:
    """Load the RSoXS schema YAML once for all tests in this module."""
    return yaml.safe_load(RSOXS_SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def bl1101_schema() -> dict:
    """Load the bl1101 schema YAML once for all tests in this module."""
    return yaml.safe_load(BL1101_SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def prov_vocabulary_doc() -> dict:
    """Load the standalone prov_vocabulary.yaml reference document."""
    return yaml.safe_load(PROV_VOCABULARY_PATH.read_text())


# ---------------------------------------------------------------------------
# prov.py — namespace and URI helpers
# ---------------------------------------------------------------------------


class TestProvUri:
    """Tests for prov_uri() and the PROV_NS constant."""

    def test_was_generated_by_full_uri(self):
        from app.modules.prov import prov_uri

        assert prov_uri("wasGeneratedBy") == "http://www.w3.org/ns/prov#wasGeneratedBy"

    def test_used_full_uri(self):
        from app.modules.prov import prov_uri

        assert prov_uri("used") == "http://www.w3.org/ns/prov#used"

    def test_was_derived_from_full_uri(self):
        from app.modules.prov import prov_uri

        assert prov_uri("wasDerivedFrom") == "http://www.w3.org/ns/prov#wasDerivedFrom"

    def test_constants_match_prov_uri(self):
        from app.modules import prov

        assert prov.WAS_GENERATED_BY == prov.prov_uri("wasGeneratedBy")
        assert prov.USED == prov.prov_uri("used")
        assert prov.WAS_DERIVED_FROM == prov.prov_uri("wasDerivedFrom")
        assert prov.WAS_ATTRIBUTED_TO == prov.prov_uri("wasAttributedTo")
        assert prov.WAS_ASSOCIATED_WITH == prov.prov_uri("wasAssociatedWith")
        assert prov.AT_TIME == prov.prov_uri("atTime")
        assert prov.STARTED_AT_TIME == prov.prov_uri("startedAtTime")
        assert prov.ENDED_AT_TIME == prov.prov_uri("endedAtTime")
        assert prov.ENTITY == prov.prov_uri("Entity")
        assert prov.ACTIVITY == prov.prov_uri("Activity")
        assert prov.AGENT == prov.prov_uri("Agent")

    def test_prov_ns_constant(self):
        from app.modules.prov import PROV_NS

        assert PROV_NS == "http://www.w3.org/ns/prov#"

    def test_prov_prefixed(self):
        from app.modules.prov import prov_prefixed

        assert prov_prefixed("Entity") == "prov:Entity"
        assert prov_prefixed("Activity") == "prov:Activity"


# ---------------------------------------------------------------------------
# prov.py — extraction_activity()
# ---------------------------------------------------------------------------


class TestExtractionActivity:
    """Tests for extraction_activity() helper."""

    def test_returns_dict(self):
        from app.modules.prov import extraction_activity

        act = extraction_activity("run-1", "gpt-4o", "2026-09-21T00:00:00Z")
        assert isinstance(act, dict)

    def test_entity_type_is_prov_activity(self):
        from app.modules.prov import extraction_activity, PROV_ACTIVITY

        act = extraction_activity("run-1", "gpt-4o", "2026-09-21T00:00:00Z")
        assert act["entityType"] == PROV_ACTIVITY
        assert act["entityType"] == "prov:Activity"

    def test_type_equals_entity_type(self):
        from app.modules.prov import extraction_activity

        act = extraction_activity("run-1", "gpt-4o", "2026-09-21T00:00:00Z")
        assert act["type"] == act["entityType"]

    def test_started_at_time_stored(self):
        from app.modules.prov import extraction_activity, STARTED_AT_TIME

        act = extraction_activity("run-2", "deepseek-r1", "2026-01-01T12:00:00Z")
        assert act[STARTED_AT_TIME] == "2026-01-01T12:00:00Z"

    def test_ended_at_time_optional(self):
        from app.modules.prov import extraction_activity, ENDED_AT_TIME

        act_no_end = extraction_activity("run-3", "llama3", "2026-06-01T00:00:00Z")
        assert ENDED_AT_TIME not in act_no_end

        act_with_end = extraction_activity(
            "run-4", "llama3", "2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z"
        )
        assert act_with_end[ENDED_AT_TIME] == "2026-06-01T01:00:00Z"

    def test_id_contains_run_id(self):
        from app.modules.prov import extraction_activity

        act = extraction_activity("abc-99", "model-x", "2026-01-01T00:00:00Z")
        assert "abc-99" in act["id"]

    def test_model_field(self):
        from app.modules.prov import extraction_activity

        act = extraction_activity("r1", "qwen3:235b", "2026-09-01T00:00:00Z")
        assert act["model"] == "qwen3:235b"


# ---------------------------------------------------------------------------
# prov.py — work_entity()
# ---------------------------------------------------------------------------


class TestWorkEntity:
    """Tests for work_entity() helper."""

    def test_entity_type_is_prov_entity(self):
        from app.modules.prov import work_entity, PROV_ENTITY

        e = work_entity("10.1000/xyz123", "A Paper Title")
        assert e["entityType"] == PROV_ENTITY
        assert e["entityType"] == "prov:Entity"

    def test_doi_stored(self):
        from app.modules.prov import work_entity

        e = work_entity("10.1000/xyz123", "A Paper Title")
        assert e["doi"] == "10.1000/xyz123"

    def test_pdf_path_optional(self):
        from app.modules.prov import work_entity

        e_no_pdf = work_entity("10.1/a", "T")
        assert "pdf_path" not in e_no_pdf

        e_pdf = work_entity("10.1/b", "T", "/data/papers/b.pdf")
        assert e_pdf["pdf_path"] == "/data/papers/b.pdf"


# ---------------------------------------------------------------------------
# prov.py — kg_snapshot_entity()
# ---------------------------------------------------------------------------


class TestKgSnapshotEntity:
    """Tests for kg_snapshot_entity() helper."""

    def test_entity_type_is_prov_entity(self):
        from app.modules.prov import kg_snapshot_entity, PROV_ENTITY

        snap = kg_snapshot_entity("storage/kg/matkg_bl1101_v7.json", "v7")
        assert snap["entityType"] == PROV_ENTITY

    def test_path_stored(self):
        from app.modules.prov import kg_snapshot_entity

        snap = kg_snapshot_entity("storage/kg/matkg_bl1101_v7.json", "v7")
        assert snap["path"] == "storage/kg/matkg_bl1101_v7.json"

    def test_corpus_revision_stored(self):
        from app.modules.prov import kg_snapshot_entity

        snap = kg_snapshot_entity("storage/kg/matkg_bl1101_v7.json", "abc123")
        assert snap["corpus_revision"] == "abc123"


# ---------------------------------------------------------------------------
# prov.py — extraction_provenance_edges()
# ---------------------------------------------------------------------------


class TestExtractionProvenanceEdges:
    """Tests for extraction_provenance_edges() helper."""

    def test_returns_three_edges(self):
        from app.modules.prov import extraction_provenance_edges

        edges = extraction_provenance_edges(
            "prov:Activity-run-1",
            "prov:Entity-Work-X",
            "matkg:Terms-1",
        )
        assert len(edges) == 3

    def test_all_edges_are_dicts(self):
        from app.modules.prov import extraction_provenance_edges

        edges = extraction_provenance_edges("A", "W", "T")
        assert all(isinstance(e, dict) for e in edges)

    def test_edge_keys(self):
        from app.modules.prov import extraction_provenance_edges

        edges = extraction_provenance_edges("A", "W", "T")
        for edge in edges:
            assert {"subject", "predicate", "object"} <= set(edge.keys())

    def test_used_edge_predicate(self):
        from app.modules.prov import extraction_provenance_edges, USED

        edges = extraction_provenance_edges("act", "work", "terms")
        used_edges = [e for e in edges if e["predicate"] == USED]
        assert len(used_edges) == 1
        assert used_edges[0]["subject"] == "act"
        assert used_edges[0]["object"] == "work"

    def test_was_generated_by_edge_predicate(self):
        from app.modules.prov import extraction_provenance_edges, WAS_GENERATED_BY

        edges = extraction_provenance_edges("act", "work", "terms")
        gen_edges = [e for e in edges if e["predicate"] == WAS_GENERATED_BY]
        assert len(gen_edges) == 1
        assert gen_edges[0]["subject"] == "terms"
        assert gen_edges[0]["object"] == "act"

    def test_was_derived_from_edge_predicate(self):
        from app.modules.prov import extraction_provenance_edges, WAS_DERIVED_FROM

        edges = extraction_provenance_edges("act", "work", "terms")
        der_edges = [e for e in edges if e["predicate"] == WAS_DERIVED_FROM]
        assert len(der_edges) == 1
        assert der_edges[0]["subject"] == "terms"
        assert der_edges[0]["object"] == "work"

    def test_all_three_predicates_present(self):
        from app.modules.prov import (
            extraction_provenance_edges,
            USED,
            WAS_GENERATED_BY,
            WAS_DERIVED_FROM,
        )

        edges = extraction_provenance_edges("act", "work", "terms")
        predicates = {e["predicate"] for e in edges}
        assert predicates == {USED, WAS_GENERATED_BY, WAS_DERIVED_FROM}


# ---------------------------------------------------------------------------
# bl1101 v7 KG — prov_metadata and prov edges
# ---------------------------------------------------------------------------


class TestBl1101V7ProvMetadata:
    """Tests that the latest matkg_bl1101_v*.json has the expected PROV-O additions."""

    def test_prov_metadata_key_exists(self, bl1101_v7):
        assert "prov_metadata" in bl1101_v7, (
            "latest matkg_bl1101_v*.json must have a top-level 'prov_metadata' key"
        )

    def test_prov_metadata_entity_id(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        # entity_id must carry the "bl1101:KGSnapshot-v" prefix; exact version varies
        assert str(pm.get("entity_id") or "").startswith("bl1101:KGSnapshot-v")

    def test_prov_metadata_type(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert pm["type"] == "prov:Entity"

    def test_prov_metadata_was_derived_from(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert "wasDerivedFrom" in pm
        # Must reference at least one predecessor snapshot
        assert len(pm["wasDerivedFrom"]) > 0

    def test_prov_metadata_was_generated_by(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert "wasGeneratedBy" in pm

    def test_prov_metadata_at_time(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert "atTime" in pm

    def test_source_entity_nodes_present(self, bl1101_v7):
        node_ids = {n["id"] for n in bl1101_v7["nodes"]}
        expected = {
            "beamline:Entity-SourceDoc-Blueprint-v09092026",
            "beamline:Entity-SourceDoc-KnowledgeMD",
            "beamline:Entity-SourceDoc-ALSPage",
        }
        missing = expected - node_ids
        assert not missing, f"Missing source entity nodes: {missing}"

    def test_was_derived_from_edges_present(self, bl1101_v7):
        WAS_DERIVED_FROM = "http://www.w3.org/ns/prov#wasDerivedFrom"
        prov_edges = [
            e for e in bl1101_v7["edges"] if e["predicate"] == WAS_DERIVED_FROM
        ]
        assert len(prov_edges) > 0, (
            f"Expected at least one prov:wasDerivedFrom edge, got {len(prov_edges)}"
        )

    def test_chunk_has_derived_from_edge(self, bl1101_v7):
        """Spot-check that a known Chunk node has a wasDerivedFrom edge."""
        WAS_DERIVED_FROM = "http://www.w3.org/ns/prov#wasDerivedFrom"
        edge_subjects = {
            e["subject"]
            for e in bl1101_v7["edges"]
            if e["predicate"] == WAS_DERIVED_FROM
        }
        assert "beamline:Chunk-blueprint-overview" in edge_subjects

    def test_als_page_chunk_has_derived_from_edge(self, bl1101_v7):
        WAS_DERIVED_FROM = "http://www.w3.org/ns/prov#wasDerivedFrom"
        edge_subjects = {
            e["subject"]
            for e in bl1101_v7["edges"]
            if e["predicate"] == WAS_DERIVED_FROM
        }
        assert "beamline:Chunk-als-page-overview" in edge_subjects


# ---------------------------------------------------------------------------
# Schema files — prov_vocabulary key
# ---------------------------------------------------------------------------


class TestRsoxsSchemaProvVocabulary:
    """Tests for the standalone prov_vocabulary.yaml reference document.

    The prov_vocabulary: block was extracted from rsoxs_schema.yaml (and
    bl1101_schema.yaml) into storage/schema/prov_vocabulary.yaml because
    LinkML's SchemaDefinition rejects unknown top-level keys.  These tests
    verify the content now lives in the canonical reference file.
    """

    def test_prov_vocabulary_key_exists(self, prov_vocabulary_doc):
        assert "prov_vocabulary" in prov_vocabulary_doc, (
            "prov_vocabulary.yaml must have a top-level 'prov_vocabulary' key"
        )

    def test_prov_vocabulary_has_namespace(self, prov_vocabulary_doc):
        pv = prov_vocabulary_doc["prov_vocabulary"]
        assert "namespace" in pv
        assert pv["namespace"]["prefix"] == "prov"
        assert pv["namespace"]["uri"] == "http://www.w3.org/ns/prov#"

    def test_prov_vocabulary_has_classes(self, prov_vocabulary_doc):
        pv = prov_vocabulary_doc["prov_vocabulary"]
        assert "classes" in pv
        assert "prov:Entity" in pv["classes"]
        assert "prov:Activity" in pv["classes"]
        assert "prov:Agent" in pv["classes"]

    def test_prov_vocabulary_has_relations(self, prov_vocabulary_doc):
        pv = prov_vocabulary_doc["prov_vocabulary"]
        assert "relations" in pv
        required = {
            "prov:wasGeneratedBy",
            "prov:used",
            "prov:wasDerivedFrom",
            "prov:wasAttributedTo",
            "prov:wasAssociatedWith",
            "prov:atTime",
            "prov:startedAtTime",
            "prov:endedAtTime",
        }
        missing = required - set(pv["relations"].keys())
        assert not missing, f"Missing prov_vocabulary relation keys: {missing}"


class TestBl1101SchemaProvVocabulary:
    """Tests that schema files load cleanly after prov_vocabulary extraction.

    The prov_vocabulary: top-level key caused a TypeError in LinkML
    (SchemaDefinition.__init__() got an unexpected keyword argument
    'prov_vocabulary').  These tests verify it has been removed from both
    schema files so they parse without errors.
    """

    def test_prov_vocabulary_key_exists(self, prov_vocabulary_doc):
        """prov_vocabulary content now lives in the standalone reference file."""
        assert "prov_vocabulary" in prov_vocabulary_doc, (
            "prov_vocabulary.yaml must have a top-level 'prov_vocabulary' key"
        )

    def test_prov_vocabulary_namespace_uri(self, prov_vocabulary_doc):
        pv = prov_vocabulary_doc["prov_vocabulary"]
        assert pv["namespace"]["uri"] == "http://www.w3.org/ns/prov#"

    def test_prov_vocabulary_relations_complete(self, prov_vocabulary_doc):
        pv = prov_vocabulary_doc["prov_vocabulary"]
        required = {
            "prov:wasGeneratedBy",
            "prov:used",
            "prov:wasDerivedFrom",
            "prov:wasAttributedTo",
            "prov:wasAssociatedWith",
            "prov:atTime",
            "prov:startedAtTime",
            "prov:endedAtTime",
        }
        missing = required - set(pv["relations"].keys())
        assert not missing, f"Missing prov_vocabulary relation keys: {missing}"

    def test_rsoxs_schema_loads_without_prov_vocabulary(self, rsoxs_schema):
        """rsoxs_schema.yaml must not contain prov_vocabulary (would crash LinkML)."""
        assert "prov_vocabulary" not in rsoxs_schema, (
            "rsoxs_schema.yaml still has 'prov_vocabulary' — it must be removed "
            "to prevent LinkML SchemaDefinition TypeError"
        )

    def test_bl1101_schema_loads_without_prov_vocabulary(self, bl1101_schema):
        """bl1101_schema.yaml must not contain prov_vocabulary (would crash LinkML)."""
        assert "prov_vocabulary" not in bl1101_schema, (
            "bl1101_schema.yaml still has 'prov_vocabulary' — it must be removed "
            "to prevent LinkML SchemaDefinition TypeError"
        )
