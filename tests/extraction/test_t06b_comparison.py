"""T06B: legacy, term_extractor agent (F9) and FORGE strategies on identical fixtures; persisted evidence keeps
baseline side outputs; no simulation code crosses into the owned packages."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.contracts.testing import FakeSyncChatClient
from app.modules.evidence import InMemoryEvidenceSink
from app.modules.extraction import DEFAULT_STRATEGY, ExtractionSource, compare_results, strategy_class
from app.modules.extraction.record_validation import run_validation_stage
from app.modules.extraction.stage_artifact import stage_artifact, write_stage_artifact
from app.modules.extraction.strategies import run_strategies
from app.modules.extraction.strategies.forge_paper import ForgePaperStrategy
from app.modules.extraction.strategies.legacy_f2w import LegacyF2WStrategy
from app.modules.extraction.strategies.paper_structure import PaperStructureStrategy
from app.modules.extraction.strategies.term_extractor_agent import TermExtractorAgentStrategy
from app.modules.extraction.structured_output import TextCompletionAdapter

from tests.evidence.evidence_fixtures import PUB_PATH, WORK_ID, make_document
from tests.extraction.extraction_fakes import TERMS_REPLY, make_extractor, page_texts_with_code
from tests.extraction.test_forge_strategies import ENTITY_REPLY, STRUCTURE_REPLY

REPO = Path(__file__).resolve().parents[2]
FILENAME = "synthetic-t06.pdf"


class _FakeGraph:
    """Stand-in for the LangGraph agent: answers every page with the same JSON terms (no model)."""

    def __init__(self) -> None:
        self.prompts = []

    def invoke(self, state):
        self.prompts.append(state["messages"][0].content)
        return {"messages": [SimpleNamespace(content=TERMS_REPLY)]}


def make_orchestrator(tmp_path: Path, monkeypatch):
    """A real ``term_extractor.Orchestrator`` with its heavy constructor skipped and a fake agent graph."""
    from app.modules.term_extractor.orchestrator import Orchestrator
    from app.modules.term_extractor.schema import SchemaHelper
    from app.modules.term_extractor.store import TermStore

    monkeypatch.setattr("app.modules.term_extractor.orchestrator.extract_and_attach_properties",
                        lambda text, store, services: False)
    orch = Orchestrator.__new__(Orchestrator)
    orch.model, orch.backend, orch.temperature = "fake-model", "ollama", 0.0
    orch.schema_helper = SchemaHelper(str(REPO / "storage/schema/matkg_schema.yaml"))
    tmp_path.mkdir(parents=True, exist_ok=True)
    orch.store = TermStore(str(tmp_path / "agent_terms.json"))
    orch.services = SimpleNamespace()
    orch.graph = _FakeGraph()
    return orch


def _source() -> ExtractionSource:
    return ExtractionSource(work_id=WORK_ID, document=make_document(), page_texts=tuple(page_texts_with_code()),
                            filename=FILENAME, title="Synthetic")


def _strategies(tmp_path, monkeypatch):
    return [
        LegacyF2WStrategy(make_extractor(tmp_path)),
        TermExtractorAgentStrategy(make_orchestrator(tmp_path / "agent", monkeypatch)),
        ForgePaperStrategy(TextCompletionAdapter(FakeSyncChatClient(default=ENTITY_REPLY))),
        PaperStructureStrategy(TextCompletionAdapter(FakeSyncChatClient(default=STRUCTURE_REPLY))),
    ]


def test_default_is_still_legacy_and_the_agent_path_is_opt_in():
    assert DEFAULT_STRATEGY == "legacy_f2w" and strategy_class() is LegacyF2WStrategy
    with pytest.raises(PermissionError):
        strategy_class("f2w_term_extractor_agent")
    assert strategy_class("f2w_term_extractor_agent", allow_optional=True) is TermExtractorAgentStrategy


def test_four_strategies_on_identical_fixture_compare_and_validate(tmp_path, monkeypatch):
    source = _source()
    results = run_strategies(_strategies(tmp_path, monkeypatch), source)
    comparison = compare_results(results)
    assert [s["strategy"] for s in comparison["summaries"]] == [
        "legacy_f2w", "f2w_term_extractor_agent", "forge_paper", "forge_paper_structure"]
    assert comparison["work_ids"] == [WORK_ID] and comparison["source_versions"] == [source.document.source_ref]
    legacy, agent = comparison["surfaces"]["legacy_f2w"], comparison["surfaces"]["f2w_term_extractor_agent"]
    assert {"pm6", "rsoxs", "chloroform"} <= set(legacy) & set(agent)
    json.dumps(comparison)
    reports = {}
    for result in results:
        sink = InMemoryEvidenceSink()
        reports[result.strategy] = (run_validation_stage(result, source.document, sink, pub_path=PUB_PATH), sink)
    structure, _ = reports["forge_paper_structure"]
    assert structure.accepted == 2 and not structure.withheld and structure.tags == {"reported": 2}
    assert structure.peak_slots["peak_positions"] == ["q = 0.3 Å⁻¹"]
    assert structure.peak_slots["peak_assignments"] == ["(100)"]
    for name in ("legacy_f2w", "f2w_term_extractor_agent", "forge_paper"):
        report, sink = reports[name]  # term strategies carry no observations; nothing is invented
        assert report.accepted == report.written == 0 and not sink.quantities


def test_agent_strategy_output_equals_direct_orchestrator_calls(tmp_path, monkeypatch):
    direct = make_orchestrator(tmp_path / "direct", monkeypatch)
    for number, text in enumerate(page_texts_with_code()):
        direct.process_page(text, FILENAME, number)
    wrapped = make_orchestrator(tmp_path / "wrapped", monkeypatch)
    result = TermExtractorAgentStrategy(wrapped).extract(_source())
    assert direct.graph.prompts == wrapped.graph.prompts
    assert json.loads(Path(direct.store.output_file).read_text())["terms"] == \
        json.loads(Path(wrapped.store.output_file).read_text())["terms"]
    assert result.side_outputs["terms"] == [r.to_dict() for r in direct.store.all_records()]
    assert {m.extracted_by for m in result.mentions} == {"f2w_term_extractor_agent"}
    assert [c.chunk_id for c in result.coverage] == ["page:1", "page:2"] and result.complete is False


def test_persisted_artifact_retains_baseline_side_outputs_and_tagged_evidence(tmp_path, monkeypatch):
    source = _source()
    legacy, _agent, _entities, structure = run_strategies(_strategies(tmp_path, monkeypatch), source)
    for result in (legacy, structure):
        sink = InMemoryEvidenceSink()
        report = run_validation_stage(result, source.document, sink, pub_path=PUB_PATH)
        path = write_stage_artifact(tmp_path / "out" / f"{result.strategy}.json", result, report, sink)
        artifact = json.loads(path.read_text())
        assert artifact["status"] == "candidate" and artifact["validation"]["published"] is False
        assert artifact["extraction"]["side_outputs"] == json.loads(json.dumps(dict(result.side_outputs)))
        assert artifact["validation"]["gates"] == {"apply_validation_mappings": True, "new_schema_slots": True,
                                                   "canonical_merge": False, "publish": False}
        assert artifact == json.loads(json.dumps(stage_artifact(result, report, sink)))
    legacy_side = json.loads((tmp_path / "out" / "legacy_f2w.json").read_text())["extraction"]["side_outputs"]
    assert legacy_side["code_snippets"] and {t["term"] for t in legacy_side["terms"]} >= {"PM6", "chloroform"}
    structure_art = json.loads((tmp_path / "out" / "forge_paper_structure.json").read_text())
    for row in structure_art["quantities"]:
        assert row["evidence_source"] == "reported" and row["evidence_ids"]
        assert {e["ref"]["evidence_id"] for e in structure_art["evidence"]} >= set(row["evidence_ids"])


def test_stage_artifact_refuses_a_mismatched_report(tmp_path, monkeypatch):
    source = _source()
    legacy, _agent, _entities, structure = run_strategies(_strategies(tmp_path, monkeypatch), source)
    sink = InMemoryEvidenceSink()
    report = run_validation_stage(structure, source.document, sink, pub_path=PUB_PATH)
    with pytest.raises(ValueError):
        stage_artifact(legacy, report, sink)


_OWNED = ("app/modules/extraction", "app/modules/evidence", "app/modules/science")
_SIMULATION_NEEDLES = ("simulat", "bl733", "gate_and_map", "cyrsoxs", "dual_fit", "paper_simulation")


def _docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                yield first.value


def test_no_simulation_code_in_owned_packages():
    for root in _OWNED:
        for path in (REPO / root).rglob("*.py"):
            tree = ast.parse(path.read_text())
            docstrings = {id(n) for n in _docstring_nodes(tree)}
            for node in ast.walk(tree):
                words = []
                if isinstance(node, ast.Name):
                    words = [node.id]
                elif isinstance(node, ast.Attribute):
                    words = [node.attr]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    words = [node.name]
                elif isinstance(node, ast.alias):
                    words = [node.name]
                elif isinstance(node, ast.ImportFrom):
                    words = [node.module or ""]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    words = [node.value]
                for word in words:
                    low = word.lower()
                    assert not any(n in low for n in _SIMULATION_NEEDLES), (path, word[:80])
