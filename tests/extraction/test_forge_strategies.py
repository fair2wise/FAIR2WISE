"""T06: optional FORGE strategies (E-01, E-08) behind the same contract, via F2W inference only."""

from __future__ import annotations

import ast
import json
import re
import threading
from pathlib import Path

import pytest

from app.modules.contracts.testing import FakeSyncChatClient
from app.modules.extraction import OPTIONAL_STRATEGIES, ExtractionSource, strategy_class
from app.modules.extraction.strategies.forge_paper import (
    CandidateOnlyResolver, ForgePaperStrategy, extract_keywords, extract_paper, llm_refine,
)
from app.modules.extraction.strategies.paper_structure import (
    PaperStructureStrategy, attributed_phase_mentions, extract_structure_from_text, to_reported_quantities,
)
from app.modules.extraction.structured_output import TextCompletionAdapter, parse_json_object

from tests.evidence.evidence_fixtures import WORK_ID, make_document

REPO = Path(__file__).resolve().parents[2]
EXTRACTION_DIR = REPO / "app" / "modules" / "extraction"

ENTITY_REPLY = json.dumps({
    "techniques": ["resonant soft X-ray scattering (RSoXS)"], "instruments": [],
    "materials": ["PM6:Y6", "SMILES:CCO ethanol"],
    "analyses": [{"method": "peak fitting", "software": "SasView", "target_material": None}],
    "confidence": 0.9,
})


def adapter(replies=(), default=ENTITY_REPLY, **kw) -> TextCompletionAdapter:
    return TextCompletionAdapter(FakeSyncChatClient(replies, default=default), **kw)


# --- registry ----------------------------------------------------------------

def test_forge_strategies_are_optional_and_need_explicit_opt_in():
    assert set(OPTIONAL_STRATEGIES) == {"forge_paper", "forge_paper_structure", "f2w_term_extractor_agent"}
    for name in OPTIONAL_STRATEGIES:
        with pytest.raises(PermissionError):
            strategy_class(name)
        assert strategy_class(name, allow_optional=True).name == name
    with pytest.raises(KeyError):
        strategy_class("nope")


# --- inference adapter -----------------------------------------------------

def test_adapter_passes_settings_through_and_records_them():
    fake = FakeSyncChatClient(["hi"])
    a = TextCompletionAdapter(fake, temperature=0.0, timeout=60, trace_label="t06", limiter=threading.Semaphore(1))
    out = a.complete("prompt")
    assert out.text == "hi" and fake.calls[0]["timeout"] == 60 and fake.calls[0]["trace_label"] == "t06"
    assert out.settings["model"] == "fake-model" and "FakeSyncChatClient" in out.settings["client_class"]


def test_adapter_omits_trace_label_for_clients_without_it():
    class Legacy:
        model = "m"

        def chat(self, prompt, *, temperature=0.0, timeout=240):
            return "ok"

    assert TextCompletionAdapter(Legacy(), trace_label="x").complete("p").text == "ok"
    with pytest.raises(TypeError):
        TextCompletionAdapter(object())


def test_parse_json_object_tolerates_fences_chatter_and_control_chars():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure: {"a": "x\x00y"} done') == {"a": "x\x00y"}
    assert parse_json_object("no json") is None


# --- E-01 forge_paper --------------------------------------------------------

def test_extract_paper_keeps_coverage_and_never_mines_the_bibliography():
    a = adapter()
    paper = extract_paper({"Results": "We used RSoXS on PM6:Y6.", "References": "[1] Smith J."}, a)
    refs = paper.by_section["References"]
    assert refs.chunks[0]["extracted_by"] == "not_applicable" and not refs.terms
    results = paper.by_section["Results"]
    assert results.llm_ok and results.chunks[0]["needs_review"] is False
    assert all(not t.canonical for t in results.terms)  # no vocabulary wired: candidates only
    smiles = [t for t in results.terms if t.identity.get("method") == "explicit_smiles_unverified"]
    assert smiles and smiles[0].name == "CCO"
    assert paper.analyses == [{"method": "peak_fitting", "software": "SasView"}]
    assert len(a.client.calls) == 1  # one chunk, one call


def test_failed_refinement_is_visible_and_falls_back_to_injected_keywords():
    taxonomy = {"technique": [("rsoxs", re.compile(r"\bRSoXS\b"))]}
    paper = extract_paper({"Results": "We used RSoXS."}, adapter(default="not json"), taxonomy=taxonomy)
    chunk = paper.by_section["Results"].chunks[0]
    assert chunk["ok"] is False and chunk["extracted_by"] == "keyword" and chunk["error"]
    assert [t.slug for t in paper.by_section["Results"].terms] == ["rsoxs"]
    assert paper.llm_ok is False


def test_long_chunk_timeout_splits_once_with_overlap():
    class TimeoutThenOk:
        model = "m"

        def __init__(self):
            self.calls = 0

        def chat(self, prompt, *, temperature=0.0, timeout=240):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("slow")
            return ENTITY_REPLY

    client = TimeoutThenOk()
    answer = extract_paper({"Body": "word " * 600}, TextCompletionAdapter(client))
    assert client.calls == 3 and answer.by_section["Body"].chunks[0]["ok"] is True


def test_chunks_overlap_for_long_sections():
    paper = extract_paper({"Body": "x" * 13000}, adapter())
    ids = [c["id"] for c in paper.by_section["Body"].chunks]
    assert ids == ["Body:0:6000", "Body:5600:11600", "Body:11200:13000"]


def test_llm_refine_rejects_malformed_schema():
    bad = adapter(default=json.dumps({"techniques": "RSoXS"}))
    assert llm_refine("text", [], bad).ok is False
    assert extract_keywords("anything") == []
    assert CandidateOnlyResolver()("  ", "technique") is None


def test_forge_paper_strategy_returns_the_normalized_contract():
    result = ForgePaperStrategy(adapter()).extract(ExtractionSource(work_id=WORK_ID, document=make_document()))
    assert result.strategy == "forge_paper" and result.complete
    assert result.source_version == make_document().source_ref
    rsoxs = [m for m in result.mentions if m.surface.startswith("resonant")]
    assert rsoxs and rsoxs[0].page in (1, 3, 5) and rsoxs[0].category == "technique"
    assert "pending H3" in result.unmapped["note"]
    assert result.inference["prompt_version"]


# --- E-08 paper structure -------------------------------------------------

STRUCTURE_REPLY = json.dumps({
    "morphology": "lamellar", "material_system": "PM6:Y6", "orientation": "unknown",
    "quantities": [{"quantity": "film_thickness", "value": 105, "unit": "nm", "location": "Results",
                    "quote": "The film thickness was 105 ± 5 nm as measured by profilometry.", "caveat": "",
                    "uncertainty": 5, "uncertainty_kind": "standard_deviation", "observation_status": "reported"}],
    "peaks": [{"label": "(100)", "q_value": 0.30, "q_unit": "Å⁻¹", "direction": "out_of_plane",
               "location": "Results",
               "quote": "The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film."}],
    "confidence": 0.8, "evidence": "",
})


def test_structure_extraction_retries_with_validation_feedback():
    a = adapter([json.dumps({"morphology": "cubic"}), STRUCTURE_REPLY])
    out = extract_structure_from_text("Some results text.", adapter=a)
    assert out.morphology == "lamellar" and "failed validation" in a.client.calls[1]["prompt"]


def test_structure_unknown_is_none_not_negative():
    assert extract_structure_from_text("text", adapter=adapter(default="nope"), max_retries=1) is None
    assert extract_structure_from_text("   ", adapter=adapter()) is None


def test_structure_quantities_keep_raw_units_status_and_uncertainty():
    out = extract_structure_from_text("text", adapter=adapter(default=STRUCTURE_REPLY))
    rows = to_reported_quantities(out)
    thickness = next(r for r in rows if r.name == "film_thickness")
    assert thickness.uncertainty.value == 5 and thickness.extraction_confidence == 0.8
    peak = next(r for r in rows if r.name.startswith("peak_q"))
    assert peak.raw_unit == "Å⁻¹" and peak.name == "peak_q[(100)]"


def test_prompt_never_asks_for_or_injects_instrument_geometry():
    from app.modules.extraction.strategies import paper_structure

    assert "SIMULATE" not in paper_structure._PROMPT
    assert "Do NOT report beam energy" in paper_structure._PROMPT
    source = (EXTRACTION_DIR / "strategies" / "paper_structure.py").read_text()
    assert "bl733" not in source.split('"""', 2)[2]  # only mentioned in the module docstring


def test_attribution_detector_separates_literature_contrast():
    text = "Other groups reported a gyroid here. We observe cylinders."
    assert attributed_phase_mentions(text) == [("gyroid", "Other groups reported a gyroid here.")]
    assert attributed_phase_mentions("We observe a gyroid network.") == []


def test_both_forge_strategies_run_on_the_identical_fixture():
    source = ExtractionSource(work_id=WORK_ID, document=make_document(), title="Synthetic")
    structure = PaperStructureStrategy(adapter(default=STRUCTURE_REPLY)).extract(source)
    entities = ForgePaperStrategy(adapter()).extract(source)
    assert structure.source_version == entities.source_version == source.document.source_ref
    assert structure.observations and structure.side_outputs["structure"]["morphology"] == "lamellar"


# --- boundaries ---------------------------------------------------------------

FORBIDDEN_IMPORTS = ("openai", "langchain", "langchain_openai", "langchain_ollama", "ollama", "requests", "httpx",
                     "forge", "als_knowledge_agent", "vllm")


def test_extraction_package_constructs_no_provider_and_imports_no_donor_code():
    for path in EXTRACTION_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN_IMPORTS, (path, name)
        doc = ast.get_docstring(tree) or ""
        text = path.read_text().replace(doc, "")
        for needle in ("os.environ", "os.getenv", "ALS_EXTRACTION", "OLLAMA_MODEL", "VLLM_BASE_URL"):
            assert needle not in text, (path, needle)


def test_adapted_extraction_files_carry_bsd3_attribution():
    for rel in ("strategies/forge_paper.py", "strategies/paper_structure.py", "structured_output.py",
                "record_validation.py"):
        text = (EXTRACTION_DIR / rel).read_text()
        assert "Adapted from als-computing/forge_rsoxs_analysis" in text and "BSD-3-Clause" in text
