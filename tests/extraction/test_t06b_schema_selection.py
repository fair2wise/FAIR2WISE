"""T06B: schema set 0.3.0 is used only on explicit selection; the default stage maps nothing (H3 F5/F6)."""

from __future__ import annotations

import copy
import sys
import subprocess
from pathlib import Path

import pytest

from app.modules.evidence import InMemoryEvidenceSink
from app.modules.extraction import ExtractionResult
from app.modules.extraction.record_validation import run_validation_stage
from app.modules.extraction.schema_selection import map_to_schema_set
from app.modules.schema.registry import RegistryError, SchemaRegistry
from app.modules.schema.validation import validate_graph

from tests.extraction.t06b_fixtures import PUB_PATH, WORK_ID, control_quantities, make_document

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


def _result():
    doc = make_document()
    return doc, ExtractionResult(strategy="controls", strategy_version="1", work_id=WORK_ID,
                                 source_version=doc.source_ref, parser_revision=doc.parser_key, complete=True,
                                 observations=tuple(control_quantities()))


def test_default_stage_stays_on_the_base_schema():
    doc, result = _result()
    report = run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH)
    assert report.schema_mapping is None and report.mapping_applied is False and report.published is False


def test_selecting_030_without_acknowledging_review_is_refused():
    doc, result = _result()
    with pytest.raises(RegistryError, match="pending owner review"):
        run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.3.0")
    with pytest.raises(ValueError, match="not selectable"):
        run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.4.0",
                             allow_pending_review=True)


def test_explicit_030_selection_maps_every_accepted_value_with_its_tag(registry):
    doc, result = _result()
    report = run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.3.0",
                                  allow_pending_review=True)
    mapping = report.schema_mapping
    assert report.mapping_applied is True and report.published is False
    assert (mapping["schema"], mapping["review_status"], mapping["status"]) == (
        "rsoxs_schema@0.3.0", "pending_owner_review", "candidate")
    values = [n for n in mapping["payload"]["things"] if n["category"] == "QuantityValue"]
    assert len(values) == report.accepted
    tags = {}
    for node in values:
        tags[node["evidence_source_tag"]] = tags.get(node["evidence_source_tag"], 0) + 1
    assert tags == report.tags
    by_label = {n["quantity_label"]: n for n in values}
    assert by_label["pixel_size"]["evidence_source_tag"] == "assumed"
    assert by_label["peak_d_spacing[(100)]"]["evidence_source_tag"] == "derived"
    check = validate_graph(copy.deepcopy(mapping["payload"]), registry.get("rsoxs_schema@0.3.0"), registry=registry)
    assert check.status == "pass" and check.categories["contract_error"]["count"] == 0


def test_map_to_schema_set_refuses_a_base_schema_entry_name(registry):
    doc, result = _result()
    from app.modules.extraction.schema_selection import validation_from_quantities
    from app.modules.extraction.record_validation import validate_reported_quantities

    accepted, _ = validate_reported_quantities(result.observations, doc, work_id=WORK_ID, pub_path=PUB_PATH)
    with pytest.raises(RegistryError):
        map_to_schema_set(validation_from_quantities(accepted), doc, work_id=WORK_ID, source_paper="x.pdf",
                          schema_set="0.3.0", schema_id="bl1101_schema", allow_pending_review=True,
                          registry=registry)


def test_importing_extraction_loads_no_schema_or_provider_modules():
    code = ("import sys; import app.modules.extraction, app.modules.extraction.record_validation; "
            "bad = [m for m in ('app.modules.schema.experiment_mapping', 'linkml_runtime', 'fitz', 'openai', "
            "'langchain_core', 'app.modules.term_extractor.orchestrator') if m in sys.modules]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
