"""T06: the default strategy wraps the existing F2W extractor with no behaviour change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.extraction import DEFAULT_STRATEGY, ExtractionSource, strategy_class
from app.modules.extraction.strategies.legacy_f2w import LegacyF2WStrategy

from tests.evidence.evidence_fixtures import WORK_ID, make_document
from tests.extraction.extraction_fakes import (
    PlainPages, RoutedFakeChatClient, make_extractor, page_texts_with_code,
)
from tests.literature.lit_fakes import have_pdf_stack, make_paper_pdf

FILENAME = "synthetic-t06.pdf"


def _baseline(tmp_path: Path):
    """Today's behaviour: call the extractor's own process_page over each page."""
    extractor = make_extractor(tmp_path / "baseline")
    (tmp_path / "baseline").mkdir(parents=True, exist_ok=True)
    pages = page_texts_with_code()
    doc = PlainPages(pages)
    for number in range(len(pages)):
        extractor.process_page(doc, FILENAME, number, None, None)
    return extractor


def _wrapped(tmp_path: Path):
    extractor = make_extractor(tmp_path / "wrapped")
    (tmp_path / "wrapped").mkdir(parents=True, exist_ok=True)
    source = ExtractionSource(work_id=WORK_ID, document=make_document(), page_texts=tuple(page_texts_with_code()),
                              filename=FILENAME)
    result = LegacyF2WStrategy(extractor).extract(source)
    return extractor, result


def test_default_strategy_is_the_existing_f2w_extractor():
    assert DEFAULT_STRATEGY == "legacy_f2w"
    assert strategy_class() is LegacyF2WStrategy


def test_wrapped_output_equals_direct_output_on_the_synthetic_fixture(tmp_path):
    baseline = _baseline(tmp_path)
    wrapped, result = _wrapped(tmp_path)
    assert wrapped.terms_dict == baseline.terms_dict
    assert wrapped.code_snippets == baseline.code_snippets
    assert wrapped.metadata == baseline.metadata
    a = json.loads(Path(baseline.output_file).read_text())
    b = json.loads(Path(wrapped.output_file).read_text())
    assert a == b
    assert [c["prompt"] for c in wrapped.chat_client.calls] == [c["prompt"] for c in baseline.chat_client.calls]
    # The side outputs are the extractor's own entries, verbatim.
    assert result.side_outputs["terms"] == list(baseline.terms_dict.values())
    assert result.side_outputs["code_snippets"] == baseline.code_snippets


def test_f2w_specific_fields_survive_normalization(tmp_path):
    _, result = _wrapped(tmp_path)
    chloroform = [m for m in result.mentions if m.surface == "chloroform"]
    assert chloroform and chloroform[0].attributes["chebi"]["chebi_id"] == "CHEBI:35255"
    assert chloroform[0].attributes["smiles"] == "ClC(Cl)Cl" and chloroform[0].attributes["formula"] == "CHCl3"
    rsoxs = [m for m in result.mentions if m.surface == "RSoXS"]
    assert rsoxs and rsoxs[0].category == "RSoXSMeasurement"  # schema class kept, not remapped
    pm6 = [m for m in result.mentions if m.surface == "PM6"][0]
    assert pm6.attributes["relations"][0]["relation"] == "part_of"
    snippet = result.side_outputs["code_snippets"][0]
    assert snippet["function_name"] == "fit_peak" and snippet["source_paper"] == FILENAME and snippet["page"] == 2
    assert all(m.quote for m in result.mentions)  # context snippets carried as quotes for locating


def test_result_does_not_alias_extractor_state(tmp_path):
    extractor, result = _wrapped(tmp_path)
    before = json.dumps(extractor.terms_dict, sort_keys=True)
    result.side_outputs["terms"][0]["term"] = "mutated"
    assert json.dumps(extractor.terms_dict, sort_keys=True) == before


def test_completeness_is_reported_as_unknown_not_success(tmp_path):
    _, result = _wrapped(tmp_path)
    assert result.complete is False
    assert all(c.ok is None and c.extracted_by == "legacy_page" for c in result.coverage)
    assert result.inference["model"] == "fake-model"


@pytest.mark.skipif(not have_pdf_stack(), reason="PDF reader stack not installed")
def test_pdf_mode_calls_process_pdf_unchanged(tmp_path):
    pdf = make_paper_pdf(tmp_path / FILENAME, title="Synthetic T06",
                         sections=[("Results", " ".join(page_texts_with_code()))])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    direct = make_extractor(tmp_path / "a", client=RoutedFakeChatClient())
    direct.process_pdf(str(pdf))
    wrapped = make_extractor(tmp_path / "b", client=RoutedFakeChatClient())
    result = LegacyF2WStrategy(wrapped).extract(ExtractionSource(work_id=WORK_ID, pdf_path=str(pdf)))
    assert wrapped.terms_dict == direct.terms_dict and wrapped.code_snippets == direct.code_snippets
    assert result.coverage[0].extracted_by == "legacy_pdf"


def test_legacy_strategy_requires_an_input():
    with pytest.raises(ValueError):
        LegacyF2WStrategy(object()).extract(ExtractionSource(work_id=WORK_ID))
