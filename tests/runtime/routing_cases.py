"""Recorded fake-model cases for the workflow-orchestrator routing goldens (T17B P2).

Each case is a turn, a workflow state, an optional route hint, the agents available, and the fake
classifier's scripted reply (a string is returned verbatim, ``{"raise": "..."}`` makes the call fail,
``None`` means the model must not be called). The goldens in ``golden/routing_transcripts.json`` were
recorded from the legacy ``WorkflowOrchestratorAgent`` before the port and pin the decision, the exact
prompt, the call keyword arguments and the precheck notes for every case.
"""

from __future__ import annotations

from typing import Any

IDLE: dict[str, Any] = {"phase": "idle", "orchestration_steps": 0, "pending": None, "approved_action": None}
PAPER: dict[str, Any] = {
    "paper_id": "p1", "status": "extracted", "filename": "scattering.pdf",
    "title": "Beamline calibration for x-ray scattering", "topic": "x-ray scattering beamline calibration",
}
PENDING_DL = {"kind": "download", "approval_token": "t1"}
PENDING_EX = {"kind": "extraction", "approval_token": "t2"}


def _s(**kw: Any) -> dict[str, Any]:
    return {**IDLE, **kw}


def _cls(label: str, reason: str = "because") -> str:
    return '{"classification": "%s", "reason": "%s"}' % (label, reason)


#: id -> (user_turn, state, route_hint, available_agents, scripted reply)
CASES: dict[str, tuple[str, dict[str, Any], str | None, list[str] | None, Any]] = {
    "fresh_science_llm_scientific": ("What is RSoXS?", _s(), None, None, _cls("scientific_or_uncertain")),
    "fresh_greeting_llm_mundane": ("hi there friend", _s(), None, None, _cls("mundane_conversation", "greeting")),
    "fresh_offtopic": ("tell me about dogs", _s(), None, None, _cls("irrelevant_non_scientific")),
    "fresh_ops_layout_overrides_llm": (
        "how is all of the hardware connected at 11.0.1.2, in order", _s(), None, None,
        _cls("scientific_or_uncertain", "LLM said science")),
    "fresh_ops_layout_llm_fails": (
        "what comes after the monochromator in the beam path", _s(), None, None, {"raise": "llm down"}),
    "fresh_unknown_label": ("Ambiguous technical request", _s(), None, None, _cls("general_knowledge")),
    "fresh_non_json_reply": ("Explain CyRSoXS", _s(), None, None, "I think this is science"),
    "fresh_json_wrapped_in_prose": (
        "Explain CyRSoXS", _s(), None, None, 'Sure: {"classification": "scientific_or_uncertain"} done'),
    "fresh_missing_reason": ("Explain CyRSoXS", _s(), None, None, '{"classification": "scientific_or_uncertain"}'),
    "fresh_llm_raises": ("Explain CyRSoXS", _s(), None, None, {"raise": "timeout"}),
    "fresh_mundane_llm_scientific": ("testing", _s(), None, None, _cls("scientific_or_uncertain")),
    "fresh_hello_regex_fallback_llm_fails": ("hello!", _s(), None, None, {"raise": "x"}),
    "fresh_download_request": (
        "Download a paper about GISAXS from arXiv", _s(), None, None, _cls("scientific_or_uncertain")),
    "fresh_followup_be_more_general": (
        "be more general", _s(current_topic="RSoXS"), None, None, _cls("scientific_or_uncertain")),
    "fresh_with_extracted_paper_state": (
        "Something unrelated entirely", _s(active_paper=PAPER, current_topic="foo"), None, None,
        _cls("scientific_or_uncertain")),
    "hint_direct_response": ("hi", _s(), "direct_response", None, None),
    "hint_retrieve_kg_idle": ("What is X?", _s(), "retrieve_kg", None, None),
    "hint_retrieve_kg_after_extraction": (
        "What did X-ray scattering calibration find?",
        _s(phase="paper_extracted", orchestration_steps=1, active_paper=PAPER,
           current_topic="x-ray scattering calibration"), "retrieve_kg", None, None),
    "hint_query_extracted_paper": ("q", _s(active_paper=PAPER), "query_extracted_paper", None, None),
    "hint_report_extraction": ("q", _s(active_paper=PAPER), "report_extraction", None, None),
    "hint_query_paper_without_paper": ("q", _s(), "query_extracted_paper", None, None),
    "paper_followup_generic": (
        "What did it say?", _s(active_paper=PAPER, current_topic="x-ray scattering beamline calibration"),
        None, None, None),
    "paper_followup_topic_mismatch": (
        "What did it say?", _s(active_paper=PAPER, current_topic="lithium battery dendrite growth"),
        None, None, _cls("scientific_or_uncertain")),
    "paper_followup_title_tokens": (
        "What did Beamline calibration for x-ray scattering find?", _s(active_paper=PAPER), None, None, None),
    "terms_followup": (
        "Summarize the terms you extracted from the paper: X-ray microtomography in biology?",
        _s(phase="paper_insufficient", active_paper={**PAPER, "paper_id": "micro", "title": "X-ray microtomography in biology",
                                                    "filename": "micro.pdf", "topic": "microtomography"}),
        None, None, None),
    "terms_followup_exact_phrase": ("list extracted terms", _s(active_paper=PAPER), None, None, None),
    "pending_download_unapproved": (
        "answer it", _s(phase="awaiting_download_approval", orchestration_steps=1, pending=PENDING_DL,
                        candidates=[{"title": "one"}], unavailable_candidate_indices=[]),
        "retrieve_kg", None, None),
    "pending_download_approved": (
        "yes", _s(phase="awaiting_download_approval", orchestration_steps=1, pending=PENDING_DL,
                  approved_action={"kind": "download", "approval_token": "t1", "candidate_index": 0},
                  candidates=[{"title": "one"}], unavailable_candidate_indices=[]), None, None, None),
    "pending_download_wrong_token": (
        "yes", _s(phase="awaiting_download_approval", pending=PENDING_DL,
                  approved_action={"kind": "download", "approval_token": "other", "candidate_index": 0},
                  candidates=[{"title": "one"}]), None, None, None),
    "pending_download_candidate_unavailable": (
        "yes", _s(phase="awaiting_download_approval", orchestration_steps=1, pending=PENDING_DL,
                  approved_action={"kind": "download", "approval_token": "t1", "candidate_index": 0},
                  candidates=[{"title": "one"}], unavailable_candidate_indices=[0]), None, None, None),
    "pending_download_index_out_of_range": (
        "yes", _s(phase="awaiting_download_approval", pending=PENDING_DL,
                  approved_action={"kind": "download", "approval_token": "t1", "candidate_index": 5},
                  candidates=[{"title": "one"}]), None, None, None),
    "pending_extraction_unapproved": (
        "ok", _s(phase="awaiting_extraction_approval", pending=PENDING_EX,
                 active_paper={"paper_id": "p9", "status": "downloaded"}), None, None, None),
    "pending_extraction_approved": (
        "ok", _s(phase="awaiting_extraction_approval", pending=PENDING_EX,
                 approved_action={"kind": "extraction", "approval_token": "t2"},
                 active_paper={"paper_id": "p9", "status": "downloaded"}), None, None, None),
    "pending_extraction_approved_wrong_paper_state": (
        "ok", _s(phase="awaiting_extraction_approval", pending=PENDING_EX,
                 approved_action={"kind": "extraction", "approval_token": "t2"},
                 active_paper={"paper_id": "p9", "status": "extracted"}), None, None, None),
    "pending_unknown_kind": (
        "ok", _s(phase="idle", pending={"kind": "weird", "approval_token": "z"}), None, None, _cls("scientific_or_uncertain")),
    "max_steps_reached": ("anything", _s(orchestration_steps=12), None, None, _cls("scientific_or_uncertain")),
    "phase_retrieval_insufficient": ("q", _s(phase="retrieval_insufficient"), None, None, None),
    "phase_post_extraction_insufficient": ("q", _s(phase="post_extraction_insufficient"), None, None, None),
    "phase_candidates_found": ("q", _s(phase="candidates_found"), None, None, None),
    "phase_candidate_selected": ("q", _s(phase="candidate_selected"), None, None, None),
    "phase_paper_downloaded": ("q", _s(phase="paper_downloaded", active_paper={"paper_id": "p3", "status": "downloaded"}),
                               None, None, None),
    "phase_paper_extracted": ("q", _s(phase="paper_extracted", active_paper=PAPER), None, None, None),
    "phase_terminal_error": ("q", _s(phase="retrieval_error"), None, None, None),
    "phase_answered_followup_needs_llm_free": ("thanks", _s(phase="answered"), None, None, None),
    "agent_unavailable_retrieval": ("What is RSoXS?", _s(), "retrieve_kg", ["DownloadAgent"], None),
    "agent_available_filter_ok": ("What is RSoXS?", _s(), "retrieve_kg", ["RetrievalAgent"], None),
    "fresh_llm_retrieval_agent_unavailable": (
        "What is RSoXS?", _s(), None, ["DownloadAgent"], _cls("scientific_or_uncertain")),
    "fresh_direct_response_agent_unavailable": (
        "hi there", _s(), None, ["RetrievalAgent"], _cls("mundane_conversation")),
}
