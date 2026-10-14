"""T07 measurement harness: runs offline, reports false merges and false splits separately."""
import json

from app.modules.canonicalization.measurement import render_markdown, run_measurement, write_report


def test_report_structure_and_invariants(tmp_path):
    report = run_measurement()
    for mode, block in report["results"].items():
        for name, s in block.items():
            if name == "no_index":
                continue
            assert {"false_merges", "false_splits_no_match", "false_splits_held_visible"} <= set(s)
        # the new path never merges a named negative control, whatever the stand-in judge does
        assert block["candidate_search"]["negative_controls_merged"] == 0
        assert block["candidate_search"]["false_merges"] == 0
        # prompts are bounded: far below the full-list prompt
        assert block["candidate_search"]["prompt_tokens_max"] < block["baseline_full_list"]["prompt_tokens_max"] / 4
    perm = report["results"]["permissive"]
    assert perm["baseline_full_list"]["negative_controls_merged"] > 0  # why the gate exists
    # no-index path: the FAISS path silently resolves nothing; the new path degrades to lexical
    no_index = report["results"]["conservative"]["no_index"]
    assert no_index["faiss_shortlist_k12"]["judge_calls"] == 0
    assert no_index["faiss_shortlist_k12"]["correct_merges"] == 0
    assert no_index["candidate_search_lexical_only"]["correct_merges"] > 0
    # recall outside top-k is measured
    assert report["results"]["conservative"]["faiss_shortlist_k5"]["true_outside_shortlist"]
    files = write_report(tmp_path, report)
    assert json.loads(files[0].read_text())["harness_version"] == report["harness_version"]
    assert "decision reserved for H3" in render_markdown(report)
