"""``f2w-corpus-qa`` (app.modules.commands.corpus_qa): the eight audit sections and the CLI (tests only; the source is pinned).

The terms file is a small synthetic corpus that trips every audit branch; nothing is read from ``storage/``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.modules.commands import corpus_qa as qa

runner = CliRunner()

LONG_A = "abcdefghijklmnopqrstuvwxyz abcdef"
LONG_B = "abcdefghijklmnopqrstuvwxyz abcdeg"


def term(label: str, **extra: Any) -> dict[str, Any]:
    return {"term": label, **extra}


def corpus() -> list[dict[str, Any]]:
    aliases = [f"alias{i}" for i in range(11)]
    return [
        term("RSoXS", category="ExperimentalTechnique", source_papers=["10.1002_a.pdf", "10.1002_a.pdf"],
             context_snippets=["s"], pages=[1], relations=[{"relation": "measures", "related_term": "film"}],
             source_metadata={"2101.00001_arxiv.pdf": {"paper_title": "Resonant soft x-ray scattering of polymers",
                                                        "publication_year": 2021},
                              "10.1002_a.pdf": {"paper_title": "Resonant soft x-ray scattering of polymers",
                                                "publication_year": "2022"},
                              "10.1002_b.pdf": {"paper_title": LONG_A, "publication_year": 2022},
                              "2102.00002_arxiv.pdf": {"paper_title": LONG_B},
                              "10.1002_empty.pdf": {"paper_title": ""}, "arxiv_empty.pdf": {"paper_title": ""},
                              "odd.pdf": "not a dict"}),
        term("P-RSoXS scan", category="RSoXSMeasurement", source_papers=["10.1002_a.pdf"], context_snippets=["s"],
             pages=[2]),
        term("GIWAXS only", category="ExperimentalTechnique", source_papers=[], relations=[
            {"relation": "used_in", "related_term": "GIWAXS"}, {"relation": "owned_by", "related_term": "x"}, "bad"]),
        term("P3HT", category="Material", source_papers=["10.1002_c.pdf"], context_snippets=["s"], pages=[1],
             relations=[{"relation": "processed_by", "related_term": "P-RSoXS"},
                        {"relation": "similar_to", "related_term": " p3ht "}, "not-a-dict"]),
        term("CyRSoXS", category="Software", source_papers=["10.1002_c.pdf"], context_snippets=["s"], pages=[1],
             relations=[{"relation": "measures", "related_term": "P-RSoXS"}]),
        term("water", category="Material", formula="H2O", source_papers=["10.1002_d.pdf"], context_snippets=["s"],
             pages=[1], relations=[
                 {"relation": "absorption_edge", "related_term": "Carbon K edge"},
                 {"relation": "photon_energy_eV", "related_term": 50}, {"relation": "energy", "related_term": "3000 eV"},
                 {"relation": "energy_eV", "related_term": "n/a"}, {"relation": "photon_energy", "related_term": 284.0},
                 {"relation": "photon_energy_range", "related_term": "  "}, {"relation": "energy", "related_term": None},
                 "junk"]),
        term("methane", category="Material", formula="CH4", source_papers=["10.1002_d.pdf"], relations=[
            {"relation": "absorption_edge", "related_term": "C K-edge"}]),
        term("no formula", relations=[{"relation": "absorption_edge", "related_term": "carbon k"}]),
        term("hollow", category=None),
        term("thingy", category="owl:Thing", source_papers=["10.1002_e.pdf"]),
        term("alias hog", category="Material", aliases=aliases, source_papers=["10.1002_f.pdf"]),
        term("single alt", category="Material", alternate_labels="only one"),
        term("polystyrene", category="Material"), term("polystyrenes", category="Material"),
        term("Polystyrene", category="Material"),
    ]


# -- helpers -------------------------------------------------------------------------------------------------


def test_normalise_levenshtein_overlap_and_filename_helpers():
    assert qa._normalise("  A   B\n") == "a b"
    assert qa._levenshtein("abc", "abc") == 0 and qa._levenshtein("ab", "abcd") == 2 and qa._levenshtein("abcd", "ab") == 2
    assert qa._title_overlap("a b c", "a b d") == pytest.approx(0.5)
    assert qa._title_overlap("", "x") == 0.0 and qa._title_overlap("x", "  ") == 0.0
    assert qa._doi_from_filename("10.1_x.pdf") == "10.1_x" and qa._doi_from_filename("noext") == "noext"
    assert qa._is_arxiv("2101.00001_ArXiv") and qa._is_arxiv("10.48550_foo") and not qa._is_arxiv("10.1002_a")


@pytest.mark.parametrize(("value", "expected"), [(3, 3.0), (2.5, 2.5), ("284.0 eV", 284.0), (" -1e2 x", -100.0), ("+7", 7.0),
                                                 ("eV 284", None), ("", None), (None, None), ([1], None)])
def test_try_float(value, expected):
    assert qa._try_float(value) == expected


def test_print_section_frames_the_title_and_indents_lines(capsys):
    qa._print_section("TITLE", ["one", "two"], indent=4)
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "=" * 72 and out[2] == "  TITLE" and out[-2:] == ["    one", "    two"]


# -- sections ------------------------------------------------------------------------------------------------


def test_duplicate_works_counts_internal_dups_and_arxiv_journal_pairs():
    lines, summary = qa.audit_duplicate_works(corpus())
    assert summary["internal_dup_count"] == 1
    assert summary["arxiv_papers"] == 3 and summary["journal_papers"] == 3
    assert summary["arxiv_journal_near_dupes"] == 2  # identical titles, and the two near-identical long ones
    text = "\n".join(lines)
    assert "dup  term='RSoXS'  doi=10.1002_a" in text and "overlap=1.00" in text and "arXiv title" in text
    assert "Top-10 most-cited DOI keys" in text


def test_stubs_distinguish_hard_partial_and_untyped():
    lines, summary = qa.audit_stubs([term("hard"), term("typed", category="Material", source_papers=["a"],
                                                        context_snippets=["s"], pages=[1]),
                                     term("untyped", category="Thing", source_papers=["a"], context_snippets=["s"],
                                          pages=[1]), term("partial", category="Material", source_papers=["a"])])
    assert summary == {"stub_signals_total": 3, "hard_stubs": 1, "untyped_stubs": 2}  # "hard" has category None
    assert any("'hard'" in line and "no_pages" in line for line in lines)


def test_technique_measurement_finds_reclassification_candidates():
    lines, summary = qa.audit_technique_measurement(corpus())
    assert summary == {"correct_measurement_typed": 1, "technique_reclassification_candidates": 1}
    assert any("term='RSoXS'" in line and "papers=2" in line and "has_measures_rel=True" in line for line in lines)
    only_relation = qa.audit_technique_measurement([term("rsoxs map", category="ExperimentalTechnique",
                                                         relations=[{"relation": "measures", "related_term": "x"}, "bad"]),
                                                    term("rsoxs idle", category="ExperimentalTechnique"),
                                                    term("unrelated", category="ExperimentalTechnique", source_papers=["a"])])
    assert only_relation[1]["technique_reclassification_candidates"] == 1


def test_numeric_values_flag_range_strings_and_carbon_edge_mismatches():
    lines, summary = qa.audit_numeric_values(corpus())
    assert summary == {"out_of_range_energy_values": 2, "non_numeric_energy_strings": 1, "edge_formula_mismatches": 1}
    text = "\n".join(lines)
    assert "value=50.0 eV" in text and "value=3000.0 eV" in text and "raw='n/a'" in text
    assert "term='water'" in text and "Carbon K-edge but no C in formula" in text
    assert "methane" not in text.split("edge/formula mismatches:")[1]


def test_zero_measurement_papers_are_those_without_a_measurement_typed_citer():
    lines, summary = qa.audit_zero_measurement_papers(corpus())
    assert summary["total_papers"] == 5 and summary["papers_with_measurement"] == 1
    assert summary["zero_measurement_papers"] == 4 and any(line.startswith("  10.1002_c") for line in lines)


def test_aliases_flag_over_merging_and_under_merging():
    lines, summary = qa.audit_aliases(corpus())
    assert summary["total_aliases"] == 12 and summary["terms_with_aliases"] == 2
    assert summary["over_merged_candidates"] == 1 and summary["under_merged_candidates"] >= 1
    text = "\n".join(lines)
    assert "  11 aliases  'alias hog'" in text and "'Polystyrene'  ↔  'polystyrenes'" in text


def test_relations_find_self_references_known_bad_patterns_and_cross_edges():
    lines, summary = qa.audit_relations(corpus())
    assert summary["known_bad_hits"] == 2 and summary["self_referential_edges"] == 1
    assert summary["tech_cross_edges"] == 1 and summary["total_edges"] == 15
    text = "\n".join(lines)
    assert "'P3HT'  --[processed_by]-->  'P-RSoXS'" in text and "'GIWAXS only' --[used_in]--> 'GIWAXS'" in text
    assert "'P3HT'  --[similar_to]-->  (self)" in text


def test_summary_stats_totals_distribution_and_years():
    lines, summary = qa.audit_summary_stats(corpus(), {"model": "m"})
    assert summary["total_entities"] == 15 and summary["total_papers"] == 5 and summary["total_edges"] == 15
    assert summary["top_categories"]["Material"] == 8 and summary["top_categories"]["UNKNOWN"] == 2
    assert summary["papers_per_year"] == {2021: 1, 2022: 2}
    assert any('"model": "m"' in line for line in lines)


# -- CLI -----------------------------------------------------------------------------------------------------


def _invoke(*args: str):
    return runner.invoke(qa._main_app, ["audit", *args])


def test_audit_prints_every_section_and_writes_the_json_summary(tmp_path):
    src, out = tmp_path / "terms.json", tmp_path / "deep" / "report.json"
    src.write_text(json.dumps({"metadata": {"corpus": "rsoxs"}, "terms": corpus()}), encoding="utf-8")
    before = src.read_bytes()
    result = _invoke("--terms", str(src), "--output", str(out))
    assert result.exit_code == 0, result.output
    for marker in ("§1  DUPLICATE WORKS", "§2  STUB", "§3  TECHNIQUE", "§4  IMPOSSIBLE", "§5  ZERO-MEASUREMENT",
                   "§6  ALIAS COUNTS", "§7  RELATION VALIDATION", "§8  SUMMARY STATS", "KEY NUMBERS RECAP",
                   "Loaded 15 term entries.", "JSON summary written to"):
        assert marker in result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report) == {"duplicate_works", "stubs", "technique_measurement", "numeric_values", "zero_measurement",
                           "aliases", "relations", "summary"}
    assert report["relations"]["known_bad_hits"] == 2 and report["stubs"]["hard_stubs"] == 7
    assert src.read_bytes() == before  # non-destructive


def test_audit_without_output_writes_no_file(tmp_path, monkeypatch):
    src = tmp_path / "terms.json"
    src.write_text(json.dumps({"terms": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(qa._main_app, ["audit", "-o", str(tmp_path / "short.json"), "--terms", str(src)])
    assert result.exit_code == 0 and (tmp_path / "short.json").is_file() and "Loaded 0 term entries." in result.output
    assert _invoke("--terms", str(src)).exit_code == 0 and sorted(p.name for p in tmp_path.iterdir()) == [
        "short.json", "terms.json"]


def test_a_missing_terms_file_exits_1(tmp_path):
    result = _invoke("--terms", str(tmp_path / "nope.json"))
    assert result.exit_code == 1 and "terms file not found" in result.output


def test_malformed_json_fails_without_a_report(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text("{oops", encoding="utf-8")
    result = _invoke("--terms", str(src), "--output", str(tmp_path / "r.json"))
    assert result.exit_code != 0 and not (tmp_path / "r.json").exists()


def test_help_and_bad_options():
    assert "--terms" in _invoke("--help").output and _invoke("--bogus").exit_code == 2
    assert Path("storage/terminology/extracted_terms_rsoxs_v1.json") == qa.DEFAULT_TERMS
