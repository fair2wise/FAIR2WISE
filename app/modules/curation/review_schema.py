"""Strict review-result contract (U-11): answer schema, parsing and deterministic validation.

Captures the *structure* of the donor ``codex_sol_resolution.py`` review workflow: the
``answer_schema`` JSON Schema (single and grouped), strict parsing, and the deterministic
publication gates a proposal must pass before a reviewer even sees it as acceptable. The donor's
credential-bearing Codex CLI transport, its hardcoded model name and its account-usage handling
are **not** ported: ``CodexSolResolver`` is a donor transport that is replaced by the existing
F2W chat clients through :mod:`.model_port`. Migration note: provider-specific diagnostics that
mentioned ``codex_sol`` are now recorded under the generic ``provider``/``requested_model`` of an
attempt.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/codex_sol_resolution.py (``answer_schema``) and
cborg_resolution.py (``validate_resolution``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

POLICY = "review-schema-v1"
CONFIDENCE_FLOOR = 0.8
DISPOSITIONS = ("resolved", "needs_review", "abstain")


def answer_schema(grouped: bool) -> dict[str, Any]:
    """JSON Schema each verdict must satisfy (``grouped``: one verdict per ``case_id`` in ``resolutions``)."""
    properties: dict[str, Any] = {
        "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
        "canonical_label": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "evidence_quote": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    }
    if grouped:
        properties["case_id"] = {"type": "string"}
    answer: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if not grouped:
        return answer
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["resolutions"],
        "properties": {"resolutions": {"type": "array", "items": answer}},
    }


class ReviewAnswer(BaseModel):
    """One strictly typed verdict. Unknown keys are refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: Literal["resolved", "needs_review", "abstain"]
    canonical_label: str | None = None
    category: str | None = None
    aliases: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    case_id: str | None = None


class ReviewParseError(ValueError):
    """The model reply is not a valid review result."""


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_review_response(text: str, *, grouped: bool = False) -> list[ReviewAnswer]:
    """Strictly parse a reply into verdicts; anything else raises :class:`ReviewParseError`."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ReviewParseError("reply is not JSON") from exc
    try:
        if grouped:
            if (
                not isinstance(data, dict)
                or set(data) != {"resolutions"}
                or not isinstance(data["resolutions"], list)
            ):
                raise ReviewParseError("grouped reply must be an object with only 'resolutions'")
            return [ReviewAnswer.model_validate(item) for item in data["resolutions"]]
        return [ReviewAnswer.model_validate(data)]
    except ValidationError as exc:
        raise ReviewParseError("reply does not match the review schema") from exc


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def source_quote(evidence: str, surface: str) -> str:
    """Return the complete literal mention of ``surface`` in ``evidence``, else ``""``.

    Whitespace and case may differ.
    """
    if not surface.strip():
        return ""
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in surface.split()) + r"(?!\w)"
    match = re.search(pattern, evidence, re.I)
    return match.group(0) if match else ""


def validate_resolution(case: Mapping[str, Any], answer: ReviewAnswer) -> dict[str, Any]:
    """Deterministic gates for a proposal. ``accepted`` means *eligible for reviewer decision*, not applied."""
    errors: list[str] = []
    evidence = str(case.get("evidence", ""))
    expected = str((case.get("payload") or {}).get("category") or "")
    if answer.disposition == "resolved":
        if not (answer.canonical_label or "").strip():
            errors.append("missing_canonical_label")
        if not answer.evidence_quote.strip():
            errors.append("missing_evidence_quote")
        elif _norm(answer.evidence_quote) not in _norm(evidence):
            errors.append("evidence_quote_not_found")
        if any(not source_quote(evidence, alias) for alias in answer.aliases):
            errors.append("unsupported_alias")
        if answer.confidence < CONFIDENCE_FLOOR:
            errors.append("confidence_below_acceptance_floor")
    if expected and (answer.category or "") != expected:
        errors.append("category_conflict")
    if case.get("case_type") == "unresolved_term" and expected == "material":
        errors.append("material_identity_requires_deterministic_resolver")
    return {"accepted": answer.disposition == "resolved" and not errors, "errors": errors, "policy": POLICY}


def grouped_answers_by_case(
    answers: list[ReviewAnswer], case_ids: list[str]
) -> tuple[dict[str, ReviewAnswer], list[str]]:
    """Map grouped verdicts to cases; returns ``(by_case, problems)`` (missing, duplicate or foreign ids)."""
    by_case: dict[str, ReviewAnswer] = {}
    problems: list[str] = []
    for answer in answers:
        if answer.case_id not in case_ids:
            problems.append(f"unknown case_id {answer.case_id!r}")
        elif answer.case_id in by_case:
            problems.append(f"duplicate case_id {answer.case_id}")
        else:
            by_case[answer.case_id] = answer
    problems += [f"missing verdict for {c}" for c in case_ids if c not in by_case]
    return by_case, problems


__all__ = (
    "CONFIDENCE_FLOOR",
    "DISPOSITIONS",
    "POLICY",
    "ReviewAnswer",
    "ReviewParseError",
    "answer_schema",
    "grouped_answers_by_case",
    "parse_review_response",
    "source_quote",
    "validate_resolution",
)
