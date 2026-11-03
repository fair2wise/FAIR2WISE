"""T19C OP02: curate_audit_corpus - corpus_qa parity, K1 distinct Works, K2 histogram, K3 exclusions, D19."""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from pathlib import Path

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import Effect, ErrorCode, ToolStatus
from app.modules.literature.store import FileDocumentStore
from app.modules.operations.corpus import (
    AUDIT_LITERATURE_CORPUS_SKILL,
    CORPUS_SECTIONS,
    CURATE_AUDIT_CORPUS,
    TOOL_AUDIT,
    CorpusAuditTool,
    CorpusSource,
    audit_corpus,
    mapping_corpus_resolver,
)
from app.modules.operations.reports import MAX_RESULT_CHARS, ScanControl
from app.modules.tools.bootstrap import build_registry
from tests.runtime.runtime_fakes import als_ctx, local_ctx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from app.modules.commands import corpus_qa  # noqa: E402

TERMINOLOGY = REPO / "storage" / "terminology"
CANARY_DEF = "SYNTHETIC-DEFINITION-CANARY-7731"
CANARY_SNIP = "SYNTHETIC-SNIPPET-CANARY-1187"

_LEGACY = {  # section name -> (corpus_qa function, takes metadata)
    "duplicate_works": corpus_qa.audit_duplicate_works,
    "stubs": corpus_qa.audit_stubs,
    "numeric_values": corpus_qa.audit_numeric_values,
    "aliases": corpus_qa.audit_aliases,
    "relations": corpus_qa.audit_relations,
}


def _term(name, papers, *, category="Material", meta=None, relations=None, **extra):
    record = {"term": name, "category": category, "definition": f"{CANARY_DEF} {name}", "pages": [1],
              "source_papers": papers,
              "context_snippets": [{"text": CANARY_SNIP, "source_paper": f"/abs/dir/{p}", "page": 1} for p in papers],
              "relations": relations or []}
    if meta is not None:
        record["source_metadata"] = meta
    record.update(extra)
    return record


TITLE = "Resonant soft X-ray scattering of synthetic polymer blends"
META_J = {"paper_title": TITLE, "authors": ["Doe, J."], "publication_year": 2021, "doi": "10.1021/x.1"}
META_A = {"paper_title": TITLE, "authors": ["J. Doe"], "publication_year": 2021}
META_B = {"paper_title": "Another synthetic study of thin films", "publication_year": 2019}


def synthetic_terms():
    """Repeated-paper fixture (KNOWLEDGE_OPERATIONS section 4): one Work cited by 6 terms, in 2 versions."""
    j, a, b = "10.1021_x.1.pdf", "2306.07295v1.pdf", "synthetic_b.pdf"
    terms = [_term(f"Polymer {i}", [j], meta={j: META_J}) for i in range(4)]
    terms += [_term("Preprint term", [a], meta={a: META_A}), _term("Both versions", [j, a, j], meta={j: META_J, a: META_A})]
    terms += [_term("Other paper", [b], meta={b: META_B}),
              _term("Stub", [], category="Thing", context_snippets=[], pages=[]),
              _term("P3HT film", [b], relations=[{"relation": "processed_by", "related_term": "P-RSoXS"},
                                                {"relation": "photon_energy_eV", "related_term": "50 eV"},
                                                {"relation": "photon_energy_eV", "related_term": "about the edge"},
                                                {"relation": "absorption_edge", "related_term": "carbon K-edge"}],
                    formula="SiO2"),
              _term("RSoXS", [b], category="ExperimentalTechnique",
                    relations=[{"relation": "used_in", "related_term": "GIWAXS"},
                               {"relation": "measures", "related_term": "rsoxs"}]),
              _term("Polymer blendx", [b], aliases=["a"] * 12), _term("Polymer blendy", [b])]
    return {"metadata": {"source": "/abs/secret/location.json"}, "terms": terms}


def _write(tmp_path, payload, name="terms.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _legacy_report(terms, metadata=None):
    out = {name: fn(terms)[1] for name, fn in _LEGACY.items()}
    out["summary"] = corpus_qa.audit_summary_stats(terms, metadata or {})[1]
    return out


def _assert_parity(report, terms, metadata=None):
    legacy = _legacy_report(terms, metadata)
    m = report.metrics
    assert m["duplicate_works"]["legacy"] == legacy["duplicate_works"]
    for name in ("stubs", "numeric_values", "aliases", "relations"):
        assert m[name] == legacy[name], name
    summary = legacy["summary"]
    assert {k: m["summary"]["legacy"][k] for k in ("total_entities", "total_papers", "total_edges",
                                                   "top_categories")} == \
        {k: summary[k] for k in ("total_entities", "total_papers", "total_edges", "top_categories")}
    assert m["summary"]["legacy"]["papers_per_year"] == {str(k): v for k, v in summary["papers_per_year"].items()}


# -- service ---------------------------------------------------------------------------------------

def test_synthetic_corpus_matches_corpus_qa_and_counts_distinct_works(tmp_path):
    payload = synthetic_terms()
    report = audit_corpus(CorpusSource("syn", str(_write(tmp_path, payload))))
    _assert_parity(report, payload["terms"], payload["metadata"])
    dup, summary = report.metrics["duplicate_works"], report.metrics["summary"]
    assert dup["legacy"]["unique_doi_keys"] == 3 and dup["paper_keys"] == 3
    assert dup["distinct_works"] == summary["distinct_works"] == 2 and dup["works_with_multiple_keys"] == 1
    assert dup["legacy"]["internal_dup_count"] == 1
    # K2: the legacy histogram counts 7 (term, metadata) increments for 2021; distinct Works count 1.
    assert summary["legacy"]["papers_per_year"] == {"2019": 1, "2021": 7}
    assert summary["papers_per_year_v2"] == {"2019": 1, "2021": 1} and summary["works_without_year"] == 0
    assert report.coverage.completeness == "complete" and report.coverage.records_expected == len(payload["terms"])
    assert report.source_revisions["syn"].startswith("sha256:")


def test_d20_sections_are_excluded_and_no_reclassification_is_proposed(tmp_path):
    report = audit_corpus(CorpusSource("syn", str(_write(tmp_path, synthetic_terms()))))
    assert set(report.metrics) == set(CORPUS_SECTIONS)
    assert {"technique_measurement", "zero_measurement"} <= set(report.excluded)
    assert all("K3" in report.excluded[k] for k in ("technique_measurement", "zero_measurement"))
    body = json.dumps(report.to_data()).lower()
    assert "reclassification_candidates" not in body and "zero_measurement_papers" not in body


def test_outputs_carry_no_definitions_snippets_metadata_or_paths(tmp_path):
    report = audit_corpus(CorpusSource("syn", str(_write(tmp_path, synthetic_terms()))))
    body = json.dumps(report.to_data())
    for forbidden in (CANARY_DEF, CANARY_SNIP, "/abs/", "secret", str(tmp_path)):
        assert forbidden not in body


def test_audit_is_read_only(tmp_path):
    path = _write(tmp_path, synthetic_terms())
    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in tmp_path.iterdir()))
    audit_corpus(CorpusSource("syn", str(path)))
    assert (path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in tmp_path.iterdir())) == before


def test_capped_scan_is_partial_and_names_the_bound(tmp_path):
    payload = synthetic_terms()
    report = audit_corpus(CorpusSource("syn", str(_write(tmp_path, payload))), control=ScanControl(5))
    assert report.coverage.completeness == "partial" and report.coverage.records_scanned == 5
    assert any("5 of 12 term entries" in r for r in report.coverage.reasons)
    assert report.metrics["summary"]["legacy"]["total_entities"] == 5


def test_empty_and_termless_files_are_complete_zero_populations(tmp_path):
    for payload in ({"terms": []}, {"metadata": {}}):
        report = audit_corpus(CorpusSource("e", str(_write(tmp_path, payload))))
        assert report.coverage.completeness == "complete" and report.metrics["summary"]["distinct_works"] == 0


@pytest.mark.parametrize("name", ["extracted_terms_xray_papers_cborg_chat.json",
                                  "extracted_terms_deepseek-r1_70b_25_20250925_103657.json",
                                  "extracted_terms_aug21_147papers.json"])
def test_committed_corpora_match_the_cli_baseline(name):
    path = TERMINOLOGY / name
    data = json.loads(path.read_text(encoding="utf-8"))
    report = audit_corpus(CorpusSource(name, str(path)))
    _assert_parity(report, data.get("terms") or [], data.get("metadata"))


def test_xray_corpus_histogram_overcount_is_disclosed_and_corrected():
    """T19A D-4: the legacy histogram totals 155 for 4 papers with a year; papers_per_year/2 counts Works."""
    path = TERMINOLOGY / "extracted_terms_xray_papers_cborg_chat.json"
    summary = audit_corpus(CorpusSource("xray", str(path))).metrics["summary"]
    assert sum(summary["legacy"]["papers_per_year"].values()) == 155
    assert summary["papers_per_year_v2"] == {"2020": 1, "2021": 1, "2022": 1, "2025": 1}
    assert summary["distinct_works"] == 7 and summary["works_without_year"] == 3


# -- Tool ------------------------------------------------------------------------------------------

def _executor(tool):
    registry = build_registry(None, include=(), extra=[tool.registration()], name="t19c", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


def test_spec_is_read_only_free_and_takes_no_path():
    spec = CURATE_AUDIT_CORPUS
    assert spec.effects == frozenset({Effect.READ_CORPUS}) and spec.cost_class.value == "free" and spec.idempotent
    assert spec.required_capabilities == frozenset({"chat.ask"})
    assert set(spec.input_schema["properties"]) == {"corpus", "sections"}
    assert "path" not in json.dumps(spec.input_schema) and spec.input_schema["additionalProperties"] is False
    assert AUDIT_LITERATURE_CORPUS_SKILL.tools == (TOOL_AUDIT,)


def test_tool_runs_without_network_or_model(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    path = _write(tmp_path, synthetic_terms())
    store = FileDocumentStore(tmp_path / "artifacts")
    tool = CorpusAuditTool(mapping_corpus_resolver({"syn": str(path)}), artifact_store=store)
    result = _executor(tool).execute(als_ctx(), TOOL_AUDIT, {"corpus": "syn"})
    assert result.status is ToolStatus.OK and result.data["metrics"]["summary"]["distinct_works"] == 2
    assert result.data["model_calls"] == 0 and len(json.dumps(result.data, sort_keys=True)) <= MAX_RESULT_CHARS
    assert len(result.artifact_refs) == 1 and str(tmp_path) not in json.dumps(result.data)
    full = json.loads(store.get(local_ctx(), result.artifact_refs[0]))
    assert full["metrics"] == result.data["metrics"]


def test_tool_partial_unknown_denied_and_invalid(tmp_path):
    path = _write(tmp_path, synthetic_terms())
    resolver = mapping_corpus_resolver({"syn": str(path)})
    partial = _executor(CorpusAuditTool(resolver, max_records=3)).execute(local_ctx(), TOOL_AUDIT, {"corpus": "syn"})
    assert partial.status is ToolStatus.PARTIAL and partial.data["coverage"]["completeness"] == "partial"
    executor = _executor(CorpusAuditTool(resolver))
    missing = executor.execute(local_ctx(), TOOL_AUDIT, {"corpus": "nope"})
    assert missing.status is ToolStatus.ERROR and missing.error_code is ErrorCode.NOT_FOUND
    denied = executor.execute(als_ctx(corpus_scope=frozenset({"other"})), TOOL_AUDIT, {"corpus": "syn"})
    assert denied.status is ToolStatus.DENIED
    no_chat = executor.execute(als_ctx(capabilities=frozenset({"chat.read_support"})), TOOL_AUDIT, {"corpus": "syn"})
    assert no_chat.status is ToolStatus.DENIED
    for args in ({"corpus": "/etc/passwd"}, {"corpus": "syn", "terms_path": "x"}, {"corpus": "syn",
                                                                                    "sections": ["zero_measurement"]}):
        assert executor.execute(local_ctx(), TOOL_AUDIT, args).status is ToolStatus.INVALID


def test_missing_or_corrupt_file_is_not_an_empty_corpus(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    executor = _executor(CorpusAuditTool(mapping_corpus_resolver({"gone": str(tmp_path / "gone.json"),
                                                                  "bad": str(bad)})))
    gone = executor.execute(local_ctx(), TOOL_AUDIT, {"corpus": "gone"})
    assert gone.error_code is ErrorCode.NOT_FOUND and str(tmp_path) not in " ".join(gone.warnings)
    corrupt = executor.execute(local_ctx(), TOOL_AUDIT, {"corpus": "bad"})
    assert corrupt.status is ToolStatus.UNAVAILABLE and str(tmp_path) not in " ".join(corrupt.warnings)


def test_cli_is_unchanged():
    """H3 K1/K2: the legacy CLI (with §3/§5 and its histogram) is byte-identical to the T19A baseline (OP02)."""
    inventory = json.loads((REPO / "docs" / "migration" / "operations_inventory.json").read_text(encoding="utf-8"))
    items = inventory["items"] if isinstance(inventory, dict) else inventory
    (op02,) = [item for item in items if item.get("id") == "OP02"]
    source = (REPO / "app" / "modules" / "commands" / "corpus_qa.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == op02["sha256"]
