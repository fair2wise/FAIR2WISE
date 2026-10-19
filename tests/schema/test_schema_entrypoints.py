"""T22A findings F2-F4 as tests: which entrypoint defaults to which schema and which normalizer."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.schema.registry import (
    ENTRYPOINT_DEFAULTS,
    NORMALIZER_LEGACY,
    NORMALIZER_TERM_EXTRACTOR,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("ep", ENTRYPOINT_DEFAULTS, ids=lambda e: e.name)
def test_recorded_default_is_what_the_source_says(registry, ep):
    text = (REPO / ep.source).read_text(encoding="utf-8")
    assert ep.needle in text, f"{ep.source} no longer contains the recorded default for {ep.name}"
    assert registry.get(ep.schema_id).is_linkml
    assert ep.schema_id.replace("_schema", "") in ep.needle


def test_profile_dependent_selection_is_explicit():
    """F3: compose and the rsoxs stack select rsoxs_schema; every other launch path uses matkg_schema."""
    by_schema = {}
    for ep in ENTRYPOINT_DEFAULTS:
        by_schema.setdefault(ep.schema_id, set()).add(ep.name)
    assert by_schema["rsoxs_schema"] == {"compose-stack", "rsoxs-stack-script"}
    assert "extract-terms-cli" in by_schema["matkg_schema"] and "scripts-run" in by_schema["matkg_schema"]
    assert set(by_schema) == {"rsoxs_schema", "matkg_schema"}


def test_legacy_normalizer_entrypoints_are_recorded():
    """F4: extract_terms.py CLI and run_pipeline_cborg use the divergent copy; everything else the module one."""
    legacy = {e.name for e in ENTRYPOINT_DEFAULTS if e.normalizer == NORMALIZER_LEGACY}
    assert legacy == {"extract-terms-cli", "run-pipeline-cborg"}
    assert all(e.normalizer in (NORMALIZER_LEGACY, NORMALIZER_TERM_EXTRACTOR) for e in ENTRYPOINT_DEFAULTS)
    # the legacy path is real: extract_terms defines its own SchemaHelper class
    assert "class SchemaHelper" in (REPO / "app/modules/extract_terms.py").read_text(encoding="utf-8")


def test_graph_filename_pattern_selection_matches_multi_kg(registry):
    """F2: schema_path_for_graph picks overlays by filename; xray graphs bind xray_schema (H3 S5); others none."""
    assert registry.for_graph("storage/kg/matkg_rsoxs_v4.json") is registry.get("rsoxs_schema")
    assert registry.for_graph("matkg_bl1101_v7.json") is registry.get("bl1101_schema")
    assert registry.for_graph("matkg_with_code.json") is None
    assert registry.for_graph("matkg_xray_papers_cborg_chat.json") is registry.get("xray_schema")


def test_default_schema_is_matkg_not_an_interpretation_fallback():
    from app.modules.schema.registry import DEFAULT_SCHEMA_ID

    assert DEFAULT_SCHEMA_ID == "matkg_schema"
