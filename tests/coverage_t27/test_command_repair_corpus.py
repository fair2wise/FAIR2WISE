"""``f2w-repair-corpus`` (app.modules.commands.repair_corpus): the four repair steps and the CLI around them."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.modules.commands import repair_corpus as rc

runner = CliRunner()


def term(label: str, *, category: str | None = "Material", papers=("p1",), snippets=("s",), pages=(1,), **extra: Any):
    out: dict[str, Any] = {"term": label, "source_papers": list(papers), "context_snippets": list(snippets),
                           "pages": list(pages)}
    if category is not None:
        out["category"] = category
    return {**out, **extra}


# -- helpers -------------------------------------------------------------------------------------------------


def test_normalise_and_levenshtein():
    assert rc._normalise("  Poly   Styrene\t") == "poly styrene"
    assert rc._levenshtein("kitten", "kitten") == 0
    assert rc._levenshtein("kitten", "sitting") == 3 and rc._levenshtein("sitting", "kitten") == 3
    assert rc._levenshtein("", "abc") == 3


def test_term_weight_counts_papers_snippets_pages_and_tolerates_missing_keys():
    assert rc._term_weight({"source_papers": [1, 2], "context_snippets": [1], "pages": [1, 2, 3]}) == 11
    assert rc._term_weight({"source_papers": None}) == 0


@pytest.mark.parametrize(("label", "expected"), [
    ("CCD camera", ("Detector", False)), ("Synchrotron source", ("Instrument", False)),
    ("Resonant scattering", ("ExperimentalTechnique", False)), ("thin film sample", ("Material", False)),
    ("P3HT", ("Material", False)), ("zebra", ("Thing", True)),
    # regression: the formula pattern ran case-insensitively, so any label starting with a letter (and "Pressure",
    # "Polarization") became a Material and the needs_review flag could never be set
    ("Zebra", ("Thing", True)), ("Pressure", ("Thing", True)), ("Polarization state", ("Thing", True)),
    ("TiO2", ("Material", False)), ("NaCl", ("Material", False)), ("Na3Bi", ("Material", False)),
    ("C60", ("Material", False)), ("PTB7", ("Material", False)), ("Poly(styrene)", ("Material", False)),
    ("poly-methyl", ("Material", False)), ("a nano coating", ("Material", False)),
])
def test_reclassify(label, expected):
    assert rc._reclassify(label) == expected


# -- steps ---------------------------------------------------------------------------------------------------


def test_hard_stubs_are_quarantined_and_partial_evidence_is_kept():
    stub = {"term": "empty"}
    only_pages = term("only pages", papers=(), snippets=())
    kept, quarantined, count = rc.step_quarantine_hard_stubs([stub, only_pages, term("full")])
    assert quarantined == [stub] and count == 1 and [t["term"] for t in kept] == ["only pages", "full"]


def test_untyped_terms_are_reclassified_without_mutating_the_input():
    original = [term("CCD detector", category="Thing", needs_review=True), term("zebra", category=None),
                term("keeps", category="Material"), term("synchrotron x", category="owl:Thing")]
    snapshot = json.dumps(original)
    out, count = rc.step_reclassify_untyped(original)
    assert count == 3 and json.dumps(original) == snapshot
    by = {t["term"]: t for t in out}
    assert by["CCD detector"]["category"] == "Detector" and "needs_review" not in by["CCD detector"]
    assert by["zebra"]["category"] == "Thing" and by["zebra"]["needs_review"] is True
    assert by["keeps"] is original[2] and by["synchrotron x"]["category"] == "Instrument"


def test_known_bad_relations_are_corrected_and_marked():
    terms = [
        term("P3HT film", relations=[{"relation": "processed_by", "related_term": "P-RSoXS"},
                                     {"relation": "processed_by", "related_term": "annealing"}, "odd-string"]),
        term("CyRSoXS", relations=[{"relation": "measures", "related_term": "p-rsoxs"}]),
        term("no relations"), term("empty", relations=[]),
        term("P3HT again", relations=[{"relation": "characterized_by", "related_term": "P-RSoXS"}]),
    ]
    out, count = rc.step_fix_known_bad_relations(terms)
    assert count == 2
    assert out[0]["relations"][0] == {"relation": "characterized_by", "related_term": "P-RSoXS",
                                      "_repaired_from": "processed_by"}
    assert out[0]["relations"][1]["relation"] == "processed_by" and out[0]["relations"][2] == "odd-string"
    assert out[1]["relations"][0]["relation"] == "simulates"
    assert terms[0]["relations"][0]["relation"] == "processed_by"  # input untouched
    assert out[4] is terms[4] and out[2] is terms[2]


def test_near_duplicates_merge_into_the_term_with_more_evidence():
    strong = term("polystyrene", papers=("p1", "p2"), snippets=("a", {"k": 1}), pages=(1, 2), aliases=["PS"])
    weak = term("polystyrenes", papers=("p3",), snippets=("a", "b", {"k": 1}), pages=(2, 9))
    far = term("polyvinyl")
    out, aliases, merged = rc.step_merge_near_duplicates([weak, far, strong])
    assert (aliases, merged) == (1, 1) and [t["term"] for t in out] == ["polyvinyl", "polystyrene"]
    canon = out[1]
    assert canon["source_papers"] == ["p1", "p2", "p3"] and canon["pages"] == [1, 2, 9]
    assert canon["context_snippets"] == ["a", {"k": 1}, "b"] and canon["aliases"] == ["PS", "polystyrenes"]


def test_the_lighter_first_term_loses_and_ties_keep_the_alphabetically_first():
    heavy_second = term("catalyst b", papers=("p1", "p2"))
    out, _, merged = rc.step_merge_near_duplicates([term("catalyst a"), heavy_second])
    assert merged == 1 and out[0]["term"] == "catalyst b" and out[0]["aliases"] == ["catalyst a"]
    tie, _, _ = rc.step_merge_near_duplicates([term("tied one"), term("tied on")])
    assert [t["term"] for t in tie] == ["tied on"]


def test_dissimilar_short_or_differently_prefixed_terms_are_not_merged():
    terms = [term("abc"), term("abd"), term("catalyst"), term("datalyst"), term("same"), term("same"),
             term("a very long label that differs"), term("a very long label that differs by far more than three")]
    out, aliases, merged = rc.step_merge_near_duplicates(terms)
    assert out is terms and (aliases, merged) == (0, 0)


def test_a_term_already_dropped_is_not_merged_twice():
    out, _, merged = rc.step_merge_near_duplicates([term("polymer a", papers=("1", "2")), term("polymer b"), term("polymer c")])
    assert merged == 1 and len(out) == 2


# -- CLI -----------------------------------------------------------------------------------------------------


def _write_terms(path: Path, **extra: Any) -> dict[str, Any]:
    data = {"metadata": {"corpus": "rsoxs"}, "terms": [
        {"term": "stub"}, term("zebra", category="Thing"), term("P3HT", relations=[
            {"relation": "processed_by", "related_term": "P-RSoXS"}]),
        term("polystyrene", papers=("p1", "p2")), term("polystyrenes")], **extra}
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _invoke(*args: str):
    return runner.invoke(rc._main_app, ["repair", *args])


def test_help_documents_the_options():
    result = _invoke("--help")
    assert result.exit_code == 0 and "--dry-run" in result.output and "--terms" in result.output


def test_repair_writes_a_repaired_copy_and_never_touches_the_original(tmp_path):
    src, dst = tmp_path / "in.json", tmp_path / "sub" / "out.json"
    _write_terms(src, extra_key={"keep": "me"})
    before = src.read_bytes()
    result = _invoke("--terms", str(src), "--output", str(dst))
    assert result.exit_code == 0, result.output
    assert src.read_bytes() == before
    out = json.loads(dst.read_text(encoding="utf-8"))
    assert out["corpus_revision"] == "repaired-draft" and out["metadata"] == {"corpus": "rsoxs"}
    assert out["repair_stats"] == {"stubs_quarantined": 1, "untyped_reclassified": 1, "relations_fixed": 1,
                                   "aliases_added": 1, "near_dupes_merged": 1}
    assert out["_quarantined_stubs"] == [{"term": "stub"}] and out["extra_key"] == {"keep": "me"}
    labels = [t["term"] for t in out["terms"]]
    assert labels == ["zebra", "P3HT", "polystyrene"] and out["terms"][0]["needs_review"] is True
    assert "Stats" in result.output and "Done. Output:" in result.output


def test_dry_run_prints_stats_and_writes_nothing(tmp_path):
    src, dst = tmp_path / "in.json", tmp_path / "out.json"
    _write_terms(src)
    result = _invoke("--terms", str(src), "--output", str(dst), "--dry-run")
    assert result.exit_code == 0 and "[dry-run] No file written." in result.output and not dst.exists()
    assert "stubs_quarantined" in result.output


def test_dry_run_may_name_the_input_as_output_but_a_real_run_may_not(tmp_path):
    src = tmp_path / "in.json"
    _write_terms(src)
    before = src.read_bytes()
    assert _invoke("--terms", str(src), "--output", str(src), "--dry-run").exit_code == 0
    refused = _invoke("--terms", str(src), "--output", str(tmp_path / "." / "in.json"))
    assert refused.exit_code == 1 and "must differ" in refused.output and src.read_bytes() == before


def test_a_missing_input_exits_1(tmp_path):
    result = _invoke("--terms", str(tmp_path / "missing.json"), "--output", str(tmp_path / "o.json"))
    assert result.exit_code == 1 and "input file not found" in result.output


def test_empty_document_and_malformed_json(tmp_path):
    src = tmp_path / "in.json"
    src.write_text("{}", encoding="utf-8")
    dst = tmp_path / "o.json"
    assert _invoke("--terms", str(src), "--output", str(dst)).exit_code == 0
    assert json.loads(dst.read_text())["terms"] == [] and json.loads(dst.read_text())["metadata"] == {}
    src.write_text("{not json", encoding="utf-8")
    assert _invoke("--terms", str(src), "--output", str(tmp_path / "o2.json")).exit_code != 0
    assert not (tmp_path / "o2.json").exists()


def test_bad_option_exits_2():
    assert _invoke("--bogus").exit_code == 2


def test_main_runs_the_typer_app(monkeypatch):
    called = []
    monkeypatch.setattr(rc, "_main_app", lambda: called.append(True))
    rc.main()
    assert called == [True]
