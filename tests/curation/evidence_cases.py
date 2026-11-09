"""Recorded fake-model cases for the evidence-debate goldens (T17B P2).

``golden/evidence_transcripts.json`` was recorded from the legacy ``EvidenceDebateAgent`` before the port and
pins, per case, the decision summary, the exact prompt, the call keyword arguments, and trace notes.
"""

from __future__ import annotations

import json
from typing import Any

OK = {"sufficient": True}
BAD = {"sufficient": False, "missing": ["x", "y"], "reason": "no snippets"}


def c(title: str, score: float, **kw: Any) -> dict[str, Any]:
    return {"title": title, "score": score, **kw}


def r(**kw: Any) -> str:
    return json.dumps(kw)


MANY = [c("A", 0.5, abstract="x" * 3000, doi="10.1/a")] + [c(f"T{i}", 0.4 + i / 100) for i in range(8)]

#: id -> (question, probe, candidates, round_no, scripted reply)
CASES: dict[str, tuple[str, dict[str, Any], list[dict[str, Any]] | None, int, Any]] = {
    "llm_download_valid": ("what is X?", BAD, [c("a", 0.3), c("b", 0.8)], 1,
                           r(selected_action="download_selected", candidate_indices=[1], reason="good",
                             hypothesis="H", objections="cost", refined_query="  ")),
    "llm_download_no_indices_defaults_first": ("q", BAD, [c("a", 0.9)], 1, r(selected_action="download_selected")),
    "llm_download_low_score_vetoed": ("q", BAD, [c("a", 0.01)], 1,
                                      r(selected_action="download_selected", candidate_indices=[0])),
    "llm_download_out_of_range": ("q", BAD, [c("a", 0.9)], 1,
                                  r(selected_action="download_selected", candidate_indices=[7, "0", 0, "x"])),
    "llm_refine": ("q", BAD, [c("a", 0.5)], 2, r(selected_action="refine_search", refined_query="narrower")),
    "llm_answer_when_sufficient": ("q", OK, [], 1, r(selected_action="answer_from_kg", reason="kg ok")),
    "llm_answer_when_insufficient_becomes_stop": ("q", BAD, [], 1, r(selected_action="answer_from_kg")),
    "llm_stop": ("q", BAD, [c("a", 0.9)], 3, r(selected_action="stop_insufficient", reason="weak",
                                               objections=["a", "", 3])),
    "llm_unknown_action_falls_back_download": ("q", BAD, [c("a", 0.9)], 1, r(selected_action="nonsense")),
    "llm_unknown_action_falls_back_answer": ("q", OK, [], 1, r(selected_action="nonsense")),
    "llm_not_json": ("q", BAD, [c("a", 0.9)], 1, "not json"),
    "llm_empty_reply": ("q", BAD, [], 1, ""),
    "llm_prose_wrapped_json": ("q", BAD, [c("a", 0.4)], 1,
                               'ok {"selected_action": "stop_insufficient", "reason": "r"} bye'),
    "llm_compact_prompt_many_candidates": ("what is X?", BAD, MANY, 2,
                                           r(selected_action="download_selected", candidate_indices=[1])),
    "llm_raises_falls_back_download": ("q", BAD, [c("a", 0.9)], 3, {"raise": "llm down"}),
    "llm_raises_falls_back_no_candidates": ("q", BAD, None, 1, {"raise": "llm down"}),
    "llm_raises_falls_back_sufficient": ("q", OK, [c("a", 0.9)], 1, {"raise": "llm down"}),
    "llm_raises_falls_back_weak": ("q", BAD, [c("w", 0.05), c("w2", 0.01)], 1, {"raise": "llm down"}),
    "llm_unicode_question": ("Wie funktioniert RSoXS? é中", BAD, [c("é", 0.5)], 1,
                             r(selected_action="download_selected", candidate_indices=[0])),
    "llm_candidates_with_id_only": ("q", BAD, [{"id": "W1", "_score": 0.7}], 1,
                                    r(selected_action="download_selected", candidate_indices=0)),
    "llm_hypothesis_default_sufficient": ("q", OK, [c("a", 0.5)], 1, r(selected_action="answer_from_kg")),
    "llm_probe_truncated_long": ("q", {"sufficient": False, "blob": "z" * 6000}, [c("a", 0.5)], 1,
                                 r(selected_action="refine_search")),
}
