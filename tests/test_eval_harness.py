"""Case files, trace completeness, and the annotation report. No network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.modules.f2w_agent.trace import finish_turn, open_turn, record_llm_call, record_retrieval
from scripts.annotation_report import render
from scripts.check_trace import main as check_main
from scripts.check_trace import trace_problems
from scripts.run_eval_batch import (
    effective_settings,
    execute_cases,
    format_effective_settings,
    load_cases,
    score_case,
)

ROOT = Path(__file__).resolve().parents[1]
KEYS = [
    "id",
    "task",
    "task_type",
    "complication",
    "target_graph",
    "query_detail",
    "check_type",
    "questions",
    "source",
    "target",
    "assertion",
    "xfail",
    "notes",
    "history_from",
]
SHA = "a" * 64


def _enable(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("F2W_TRACE_ENABLED", "1")
    monkeypatch.setenv("F2W_TRACE_DIR", str(root))


def _complete_turn(turn_id: str) -> None:
    open_turn(
        client_turn_id=turn_id,
        session_id="eval-session",
        request={"message": "Which stage is in_chamber_jj_vert_aperture part of?"},
        env={"graphs": [{"path": "storage/kg/matkg_bl1101_v2.json", "sha256": SHA}]},
    )
    record_llm_call(
        label="orchestrator-classify",
        model="test",
        messages=[{"role": "user", "content": "classify this"}],
        response='{"classification": "ops_layout"}',
        latency_ms=1,
    )
    record_llm_call(
        label="KG-RAG-judge",
        model="test",
        messages=[
            {"role": "system", "content": "judge the context"},
            {"role": "user", "content": "question and context"},
        ],
        response='{"sufficient": true}',
        latency_ms=2,
    )
    record_retrieval({
        "route_flags": {
            "_is_ops_layout_question": True,
            "_is_numeric_claim": False,
            "_is_conceptual_question": True,
            "is_experiment_identity_question": False,
        },
        "layout_ops_only_filter": True,
        "merged_context": "Scatter slits (JJ) includes in_chamber_jj_vert_aperture.",
        "raw_judge_output": '{"sufficient": true}',
        "leeway_used": False,
    })
    finish_turn()


def test_tuples_jsonl_shape_and_history() -> None:
    path = ROOT / "evals/cases/chat/tuples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["id"] for row in rows] == (
        [f"C{index:02d}" for index in range(1, 24)]
        + ["C24a", "C24b"]
        + [f"C{index:02d}" for index in range(25, 37)]
    )
    assert all(list(row) == KEYS for row in rows)
    assert [row["id"] for row in rows if row["xfail"]] == ["C09", "C11", "C17"]
    assert {row["id"]: row["history_from"] for row in rows if row["history_from"]} == {
        "C04": "C20",
        "C32": "C31",
        "C36": "C30",
    }
    by_id = {row["id"]: row for row in rows}
    assert "not in storage/kg" in by_id["C24a"]["notes"]
    assert len(by_id["C24b"]["questions"]) == 5
    assert "10.1073/pnas.1821372116" in by_id["C24b"]["questions"][0]
    counts = {}
    for row in rows:
        counts[row["check_type"]] = counts.get(row["check_type"], 0) + 1
    assert counts == {"C": 28, "U": 5, "J": 2, "X": 2}


def test_batch1_scaffold_counts() -> None:
    document = yaml.safe_load((ROOT / "evals/cases/batch1.yaml").read_text(encoding="utf-8"))
    cases = document["cases"]
    by_category = {}
    for case in cases:
        by_category.setdefault(case["category"], []).append(case)
        assert "prefilled" in case
    competency = [
        line.strip()
        for line in (ROOT / "storage/competency_questions/thomas_f.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [case["question"] for case in by_category["science"]] == competency
    assert len(by_category["ops"]) == 15
    assert len(by_category["tiled"]) == 10
    assert len(by_category["regex_probe"]) == 2
    tiled_questions = "\n".join(case["question"] for case in by_category["tiled"])
    assert "2026-00041" in tiled_questions
    assert "2026-99999" in tiled_questions
    assert [case["question"] for case in by_category["regex_probe"]] == [
        "How do I run CyRSoXS?",
        "What sample classes suit RSoXS?",
    ]
    assert all(case["prefilled"] is True and case["expected_graph_ids"] == ["rsoxs_v1"] for case in by_category["science"])
    assert all(case["must_include"] == [] and case["allowed_entities"] == [] for case in by_category["science"])
    assert {case["id"] for case in by_category["science"] if case["assert_graph"] is False} == {
        "S03", "S05", "S06", "S11", "S12", "S16", "S19",
    }
    ops = by_category["ops"]
    assert [case["must_include"] for case in ops[:6]] == [
        ["Scatter slits (JJ)"],
        ["Upstream slits (JJ)"],
        ["Exit slits"],
        ["Sample stage"],
        ["Mono 101"],
        ["Detector"],
    ]
    assert ops[6]["must_include"] == ["sample_z", "Sample stage"]
    assert all(case["expected_graph_ids"] == ["bl1101"] for case in ops[:12])
    assert ops[9]["must_include"] == []
    assert "must_not_mention" not in ops[9]
    assert ops[9]["must_not_match"] == ops[11]["must_not_match"] == document["x-stage-attribution"]
    assert ops[12]["tuple_ids"] == ["C12", "C13"]
    assert ops[12]["must_not_mention"] == ["CyRSoXS", "NRSS", "YBCO", "cuprate"]
    assert ops[14]["tuple_ids"] == ["C02"]
    tiled = {case["id"]: case for case in by_category["tiled"]}
    assert tiled["T01"]["must_include"][0] == "Cheng Wang"
    assert tiled["T01"]["must_include"][-1].startswith("scan_count:")
    assert "P202600041-01-S01 (P3HT:PCBM blend)" not in tiled["T01"]["must_include"]
    assert "P202600041-01-S01 (P3HT:PCBM blend)" in tiled["T01"]["allowed_entities"]
    assert "P202600041-01-S01 (P3HT:PCBM blend)" in tiled["T02"]["must_include"]
    assert tiled["T08"]["question"] == "Show me the most recent scan"
    assert tiled["T10"]["must_include"] == []
    assert tiled["T10"]["allowed_entities"] == []
    assert tiled["T10"]["must_not_match"] == tiled["T01"]["when_tiled_off"]["must_not_match"]
    assert "P3HT" not in " ".join(tiled["T10"]["must_not_match"])
    assert tiled["T01"]["tuple_ids"] == ["C29"]
    assert tiled["T10"]["tuple_ids"] == ["C33"]
    regex = {case["id"]: case for case in by_category["regex_probe"]}
    assert regex["R01"]["must_include"] == ["CyRSoXS"]
    assert regex["R01"]["expect_live_tiled_status"] == "skipped"
    assert regex["R02"]["expect_live_tiled_status"] == "skipped"
    assert regex["R01"]["tuple_ids"] == ["C11"]
    assert all(case["prefilled"] is False for case in by_category["regex_probe"])
    assert document["x-beamline-stages"]
    assert cases[0]["tuple_ids"] == [] or "tuple_ids" in cases[0]


def test_score_case_include_allow_and_tiled_off() -> None:
    case = {
        "must_include": ["Cheng Wang", "P202600041-01", "scan_count: 98"],
        "allowed_entities": ["P202600041-01-S01 (P3HT:PCBM blend)", "9cd237f8-8fc5-4d6f-bc31-8d2c803ccbc4"],
        "expected_graph_ids": ["bl1101"],
        "assert_graph": True,
        "when_tiled_off": {
            "must_include": [],
            "must_not_match": [r"P2026\d{5}(-\d{2})?(-S\d{2})?", r"(?i)Cheng Wang|Thomas Ferron"],
        },
    }
    answer = "Cheng Wang ran P202600041-01. There are 98 scans. Sample P202600041-01-S02 is extra."
    problems = score_case(case, answer, graph_ids=["bl1101"], live_tiled_on=True)
    assert any("P202600041-01-S02" in problem for problem in problems)
    ok = "Cheng Wang ran P202600041-01 across 98 scans on P202600041-01-S01."
    assert score_case(case, ok, graph_ids=["bl1101"], live_tiled_on=True) == []
    assert score_case(case, ok, graph_ids=[], live_tiled_on=True) == ["graph ids missing: bl1101"]
    quiet = score_case(
        {**case, "assert_graph": False},
        "No catalog rows.",
        graph_ids=[],
        live_tiled_on=False,
    )
    assert quiet == []
    leaked = score_case(case, "Scientist Cheng Wang.", graph_ids=["bl1101"], live_tiled_on=False)
    assert any("must_not_match" in problem for problem in leaked)


def test_score_case_live_tiled_status_and_stage_regex() -> None:
    skipped = score_case(
        {"expect_live_tiled_status": "skipped", "must_include": ["CyRSoXS"]},
        "Use CyRSoXS for the reduction.",
        live_tiled_on=True,
        live_tiled_status="skipped",
    )
    assert skipped == []
    off = score_case(
        {"expect_live_tiled_status": "skipped", "must_include": ["CyRSoXS"]},
        "Use CyRSoXS for the reduction.",
        live_tiled_on=False,
        live_tiled_status="disabled",
    )
    assert off == []
    missed = score_case(
        {"expect_live_tiled_status": "skipped"},
        "hello",
        live_tiled_on=True,
        live_tiled_status="used",
    )
    assert missed
    stage = score_case(
        {"must_not_match": [r"(?i)(part of|on|belongs to|mounted on)\s+(the\s+)?(Detector|Sample stage)"]},
        "piezo_horiz is part of the Detector",
    )
    assert stage
    clear = score_case(
        {"must_not_match": [r"(?i)(part of|on|belongs to|mounted on)\s+(the\s+)?(Detector|Sample stage)"]},
        "piezo_horiz has no documented stage",
    )
    assert clear == []


def test_cli_overrides_replace_file_settings() -> None:
    merged = effective_settings(
        {"source_rag": False, "use_live_tiled": False, "graph_source": "json", "ui_settings": {"sourceRag": False}},
        live_tiled=True,
        source_rag=True,
    )
    assert merged["use_live_tiled"] is True
    assert merged["source_rag"] is True
    assert merged["ui_settings"]["useLiveTiled"] is True
    assert merged["ui_settings"]["sourceRag"] is True
    assert merged["graph_source"] == "json"
    text = format_effective_settings(merged)
    assert "source_rag: True" in text
    assert "use_live_tiled: True" in text


def test_tuple_loader_skips_regex_and_expands_competency_file() -> None:
    _settings, cases = load_cases(ROOT / "evals/cases/chat/tuples.jsonl")
    skipped = [case for case in cases if not case["run"]]
    assert {case["id"] for case in skipped} >= {"C07", "C08", "C09", "C10", "C11", "C06", "C28"}
    expanded = [case for case in cases if str(case["id"]).startswith("C05.")]
    assert len(expanded) == 28
    assert expanded[0]["run"] is True
    by_id = {case["id"]: case for case in cases}
    assert by_id["C04"]["history_from"] == "C20"
    assert by_id["C32"]["history_from"] == "C31"
    assert by_id["C36"]["history_from"] == "C30"


def test_history_from_posts_prior_in_the_same_session() -> None:
    cases = [
        {"id": "C20", "question": "prior q", "category": "literature_kg", "run": True, "history_from": None},
        {"id": "C04", "question": "be less strict", "category": "classify", "run": True, "history_from": "C20"},
    ]
    seen = []

    def post(_base, body, _timeout):
        seen.append(body)
        answer = "prior answer" if body["message"] == "prior q" else "follow"
        return {"turn_id": body["client_turn_id"], "status": "ok", "answer": answer}

    rows, failures = execute_cases(
        cases,
        {},
        batch_id="pilot",
        live_tiled=None,
        base_url="http://example",
        timeout=1,
        post=post,
    )
    assert failures == 0
    assert [body["message"] for body in seen] == ["prior q", "prior q", "be less strict"]
    history, follow = seen[1], seen[2]
    assert history["session_id"] == follow["session_id"]
    assert "messages" not in history
    assert follow["messages"] == [
        {"role": "user", "content": "prior q"},
        {"role": "assistant", "content": "prior answer"},
    ]
    assert rows[1]["id"] == "C04←C20"
    assert rows[1]["note"] == "history for C04"


def test_check_trace_accepts_a_complete_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    _complete_turn("turn-complete-1")
    assert check_main(["check_trace.py", "turn-complete-1"]) == 0


def test_check_trace_rejects_a_missing_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    open_turn(client_turn_id="turn-thin-1", request={"message": "thanks!"})
    finish_turn()
    assert check_main(["check_trace.py", "turn-thin-1"]) == 1
    detail_path = next(tmp_path.glob("*/turns/turn-thin-1.json"))
    problems = trace_problems(json.loads(detail_path.read_text(encoding="utf-8")))
    assert any("KG-RAG-judge" in problem for problem in problems)
    assert any("orchestrator-classify" in problem for problem in problems)


def test_check_trace_rejects_a_clipped_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, tmp_path)
    _complete_turn("turn-clipped-1")
    detail_path = next(tmp_path.glob("*/turns/turn-clipped-1.json"))
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    detail["retrieval"]["merged_context"] = ("x" * 1199) + "…"
    detail_path.write_text(json.dumps(detail), encoding="utf-8")
    problems = trace_problems(detail)
    assert any("clipped at 1200" in problem for problem in problems)


def test_annotation_report_groups_pass_rate_and_fail_tags(tmp_path: Path) -> None:
    day = tmp_path / "2026-09-29"
    day.mkdir()
    turns = [
        {"turn_id": "t1", "batch_id": "pilot", "category": "science", "question": "What is RSoXS?", "ts": "1"},
        {"turn_id": "t2", "batch_id": "pilot", "category": "ops", "question": "Beam path?", "ts": "2"},
        {"turn_id": "t3", "batch_id": "pilot", "category": "science", "question": "Unlabeled", "ts": "3"},
        {"turn_id": "other", "batch_id": "other", "category": "science", "question": "ignore", "ts": "4"},
    ]
    (day / "turns.jsonl").write_text("".join(json.dumps(row) + "\n" for row in turns), encoding="utf-8")
    annotations = [
        {"turn_id": "t1", "verdict": "fail", "failure_tags": ["wrong-citation"], "ts": "1", "note": ""},
        {"turn_id": "t1", "verdict": "pass", "failure_tags": [], "ts": "2", "note": ""},
        {"turn_id": "t2", "verdict": "fail", "failure_tags": ["invented-motor"], "ts": "3", "note": ""},
        {"turn_id": "other", "verdict": "fail", "failure_tags": ["wrong-citation"], "ts": "4", "note": ""},
    ]
    (tmp_path / "annotations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in annotations),
        encoding="utf-8",
    )
    from scripts.annotation_report import _turns

    report = render("pilot", _turns(tmp_path, "pilot"), annotations)
    assert "Overall pass rate (labeled): 1/2 (50%)" in report
    assert "Auto-scores were not joined." in report
    assert "wrong-citation" in report
    assert "`t2`" in report
    assert "Beam path?" in report
    assert "ignore" not in report
    assert "Unlabeled" not in report or "unlabeled: 1" in report


def test_disagreement_table_lists_auto_versus_label(tmp_path: Path) -> None:
    from scripts.annotation_report import disagreement_rows

    turns = {
        "t-pass": {"turn_id": "t-pass", "question": "Which stage is exit_slit_top part of?"},
        "t-agree": {"turn_id": "t-agree", "question": "Which stage is exit_slit_top part of?"},
    }
    labeled = {
        "t-pass": {"verdict": "pass", "note": "stage is right even without the graph id", "failure_tags": []},
        "t-agree": {"verdict": "fail", "note": "missed the stage", "failure_tags": ["wrong-stage"]},
    }
    case = {
        "id": "O03",
        "question": "Which stage is exit_slit_top part of?",
        "must_include": ["Exit slits"],
        "allowed_entities": [],
        "expected_graph_ids": ["bl1101"],
        "assert_graph": True,
    }
    details = {
        "t-pass": {
            "request": {"use_live_tiled": False, "ui_settings": {"case_id": "O03"}},
            "retrieval": {"selected_hits": [], "live_tiled": {"status": "disabled"}},
            "response": {"answer": "Exit slits."},
        },
        "t-agree": {
            "request": {"use_live_tiled": False, "ui_settings": {"case_id": "O03"}},
            "retrieval": {"selected_hits": [{"graph_id": "bl1101"}], "live_tiled": {"status": "disabled"}},
            "response": {"answer": "I don't know."},
        },
    }
    rows = disagreement_rows(turns, labeled, details, {"O03": case})
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "t-pass"
    assert rows[0]["auto"] == "fail"
    assert rows[0]["label"] == "pass"
    assert "graph ids missing: bl1101" in rows[0]["rule"]
    assert "stage is right" in rows[0]["note"]
    report = render("pilot", turns, [
        {"turn_id": "t-pass", "verdict": "pass", "note": "stage is right even without the graph id", "failure_tags": [], "ts": "1"},
    ], disagreements=rows)
    assert "## Auto-score disagreements" in report
    assert "graph ids missing: bl1101" in report
