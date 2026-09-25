"""Tests for app/modules/prov.py and downstream PROV-O artefacts.

Run with::

    python -m pytest tests/test_prov.py -x -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures / paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
BL1101_V7_PATH = PROJECT_ROOT / "storage" / "kg" / "matkg_bl1101_v7.json"
RSOXS_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema" / "rsoxs_schema.yaml"
BL1101_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema" / "bl1101_schema.yaml"


@pytest.fixture(scope="module")
def bl1101_v7() -> dict:
    """Load the bl1101 v7 KG JSON once for all tests in this module."""
    return json.loads(BL1101_V7_PATH.read_text())


@pytest.fixture(scope="module")
def rsoxs_schema() -> dict:
    """Load the RSoXS schema YAML once for all tests in this module."""
    return yaml.safe_load(RSOXS_SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def bl1101_schema() -> dict:
    """Load the bl1101 schema YAML once for all tests in this module."""
    return yaml.safe_load(BL1101_SCHEMA_PATH.read_text())


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
    """Tests that matkg_bl1101_v7.json has the expected PROV-O additions."""

    def test_prov_metadata_key_exists(self, bl1101_v7):
        assert "prov_metadata" in bl1101_v7, (
            "matkg_bl1101_v7.json must have a top-level 'prov_metadata' key"
        )

    def test_prov_metadata_entity_id(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert pm["entity_id"] == "bl1101:KGSnapshot-v7"

    def test_prov_metadata_type(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert pm["type"] == "prov:Entity"

    def test_prov_metadata_was_derived_from(self, bl1101_v7):
        pm = bl1101_v7["prov_metadata"]
        assert "wasDerivedFrom" in pm
        assert "bl1101:KGSnapshot-v6" in pm["wasDerivedFrom"]

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
        assert len(prov_edges) >= 19, (
            f"Expected at least 19 prov:wasDerivedFrom edges, got {len(prov_edges)}"
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
    """Tests that rsoxs_schema.yaml has the prov_vocabulary section."""

    def test_prov_vocabulary_key_exists(self, rsoxs_schema):
        assert "prov_vocabulary" in rsoxs_schema, (
            "rsoxs_schema.yaml must have a top-level 'prov_vocabulary' key"
        )

    def test_prov_vocabulary_has_namespace(self, rsoxs_schema):
        pv = rsoxs_schema["prov_vocabulary"]
        assert "namespace" in pv
        assert pv["namespace"]["prefix"] == "prov"
        assert pv["namespace"]["uri"] == "http://www.w3.org/ns/prov#"

    def test_prov_vocabulary_has_classes(self, rsoxs_schema):
        pv = rsoxs_schema["prov_vocabulary"]
        assert "classes" in pv
        assert "prov:Entity" in pv["classes"]
        assert "prov:Activity" in pv["classes"]
        assert "prov:Agent" in pv["classes"]

    def test_prov_vocabulary_has_relations(self, rsoxs_schema):
        pv = rsoxs_schema["prov_vocabulary"]
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
    """Tests that bl1101_schema.yaml has the prov_vocabulary section."""

    def test_prov_vocabulary_key_exists(self, bl1101_schema):
        assert "prov_vocabulary" in bl1101_schema, (
            "bl1101_schema.yaml must have a top-level 'prov_vocabulary' key"
        )

    def test_prov_vocabulary_namespace_uri(self, bl1101_schema):
        pv = bl1101_schema["prov_vocabulary"]
        assert pv["namespace"]["uri"] == "http://www.w3.org/ns/prov#"

    def test_prov_vocabulary_relations_complete(self, bl1101_schema):
        pv = bl1101_schema["prov_vocabulary"]
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
