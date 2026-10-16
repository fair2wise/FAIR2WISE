"""Ask a labelling model for provisional pilot labels and check them against the parsed text.

One paper version produces one label document (``status: provisional_model_label``).
Calls per version: ``ceil(pages / pages_per_call)`` value calls, one figure call (when the
version has the figure task) and one context call (absent quantities, gold questions,
Work/version identity). There is no retry: a failed, timed-out or unparsable call is
recorded under ``missing`` with its pages and reason, and nothing is filled in for it.
Malformed items in an otherwise valid answer are counted (``items_invalid``) and left out;
well-formed items whose quote cannot be located are kept and flagged.

Every model call goes through the client, which holds the shared CBORG admission slot
(:class:`app.modules.term_extractor.clients.CBorgChatClient` -> ``cborg_limiter.sync_slot``).
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from evals.binary.labeling import guide
from evals.binary.labeling.parse import ParsedPaper
from evals.binary.labeling.pilot import (
    TASK_CONTEXT,
    TASK_FIGURES,
    TASK_VALUES,
    VALUE_TAGS,
    PilotPaper,
    PilotVersion,
)
from evals.binary.labeling.quotes import check_quote, value_flags
from evals.binary.model import sha256_text
from evals.binary.sanitize import sanitize

LABEL_FORMAT = "f2w-pilot-labels/1"
LABEL_STATUS = "provisional_model_label"
TOOL_REVISION = "t11b-label-1"
DEFAULT_PAGES_PER_CALL = 5

#: Quote statuses that count as located.
LOCATED = ("exact", "normalized", "dehyphenated")
_STATUS_RANK = {"exact": 0, "normalized": 1, "dehyphenated": 2, "not_applicable": 3, "located_without_page": 4,
                "wrong_page": 5, "missing_quote": 6, "not_found": 7}


class LabelClient(Protocol):
    """A labelling model: ``model`` id and one blocking ``chat`` call."""

    model: str

    def chat(self, prompt: str) -> str: ...


class CborgLabelClient:
    """The real labeller: the F2W CBORG client at temperature 0 (holds the shared admission slot)."""

    def __init__(self, model: str, *, timeout: int = 600) -> None:
        from app.modules.term_extractor.clients import make_chat_client

        self.model = model
        self.timeout = timeout
        self._client = make_chat_client("cborg", model)

    def chat(self, prompt: str) -> str:
        return self._client.chat(prompt, temperature=0.0, timeout=self.timeout, trace_label="pilot_label")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_response(raw: Any) -> Optional[Dict[str, Any]]:
    """The JSON object in a model answer (code fences tolerated), or None."""
    if not isinstance(raw, str):
        return None
    text = _FENCE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def page_chunks(num_pages: int, size: int) -> List[Tuple[int, int]]:
    """``[(first, last)]`` page ranges of at most ``size`` pages."""
    size = max(1, int(size))
    return [(start, min(start + size - 1, num_pages)) for start in range(1, num_pages + 1, size)]


def calls_for(paper: PilotPaper, num_pages: Dict[str, int], pages_per_call: int = DEFAULT_PAGES_PER_CALL) -> int:
    """Planned model calls for ``paper`` given the page count of each version."""
    total = 0
    for version in paper.versions:
        n = num_pages.get(version.key, 0)
        if TASK_VALUES in version.tasks:
            total += math.ceil(n / max(1, pages_per_call)) if n else 0
        total += int(TASK_FIGURES in version.tasks) + int(TASK_CONTEXT in version.tasks)
    return total


def summarize_quotes(checks: Sequence[Dict[str, Any]]) -> str:
    if not checks:
        return "not_applicable"
    return max((c["status"] for c in checks), key=lambda s: _STATUS_RANK.get(s, 99))


class _Collector:
    """Builds the items list with deterministic ids."""

    def __init__(self, pilot_id: str, version: str, paper: ParsedPaper) -> None:
        self.prefix = f"{pilot_id}-{version}"
        self.paper = paper
        self.items: List[Dict[str, Any]] = []
        self.counts: Dict[str, int] = {}
        self.warnings: List[str] = []

    def add(self, kind: str, data: Dict[str, Any], quotes: Sequence[Tuple[Any, Any]], call_id: str,
            extra_flags: Sequence[str] = ()) -> Dict[str, Any]:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        checks = []
        flags: List[str] = list(extra_flags)
        for page, quote in quotes:
            check = check_quote(self.paper, quote, page)
            checks.append({"page": page, "quote": quote, **check})
            flags.extend(check["flags"])
        status = summarize_quotes(checks)
        if status not in LOCATED and status != "not_applicable":
            flags.append("quote_not_located")
        item = {
            "label_id": f"{self.prefix}-{kind}-{self.counts[kind]:03d}",
            "kind": kind,
            "data": data,
            "quotes": checks,
            "quote_status": status,
            "flags": sorted(set(flags)),
            "call_id": call_id,
        }
        self.items.append(item)
        return item


def _locator(item: Dict[str, Any]) -> Dict[str, Any]:
    loc = item.get("locator")
    return loc if isinstance(loc, dict) else {}


def _take_values(data: Dict[str, Any], col: _Collector, call_id: str) -> Tuple[int, int]:
    good = bad = 0
    for item in data.get("values") if isinstance(data.get("values"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("quantity"), str):
            bad += 1
            continue
        loc = _locator(item)
        flags = [] if item.get("tag") in VALUE_TAGS else ["invalid_tag"]
        flags += value_flags(item, loc.get("quote"))
        col.add("value", item, [(loc.get("page"), loc.get("quote"))], call_id, flags)
        good += 1
    for item in data.get("table_totals") if isinstance(data.get("table_totals"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("table"), str):
            bad += 1
            continue
        col.add("table_total", item, [(item.get("page"), item.get("quote"))], call_id)
        good += 1
    if not isinstance(data.get("values"), list):
        bad += 1
    return good, bad


def _take_figures(data: Dict[str, Any], col: _Collector, call_id: str) -> Tuple[int, int]:
    good = bad = 0
    for item in data.get("figure_readings") if isinstance(data.get("figure_readings"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("figure"), str):
            bad += 1
            continue
        flags = []
        if item.get("ambiguous") is True and item.get("reading") not in (None, ""):
            flags.append("ambiguous_but_read")
        if item.get("needs_visual_check") is True:
            flags.append("needs_visual_check")
        col.add("figure_reading", item, [(item.get("page"), item.get("quote"))], call_id, flags)
        good += 1
    for item in data.get("figure_totals") if isinstance(data.get("figure_totals"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("figure"), str):
            bad += 1
            continue
        col.add("figure_total", item, [(item.get("page"), item.get("quote"))], call_id)
        good += 1
    if not isinstance(data.get("figure_readings"), list):
        bad += 1
    return good, bad


def _evidence(item: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    rows = item.get("evidence")
    return [(r.get("page"), r.get("quote")) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _take_context(data: Dict[str, Any], col: _Collector, call_id: str, want_questions: bool) -> Tuple[int, int]:
    good = bad = 0
    for item in data.get("absent_quantities") if isinstance(data.get("absent_quantities"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("quantity"), str):
            bad += 1
            continue
        near = item.get("nearest_quote") if isinstance(item.get("nearest_quote"), dict) else {}
        quotes = [(near.get("page"), near.get("quote"))] if near.get("quote") else []
        col.add("absent", item, quotes, call_id)
        good += 1
    questions = data.get("gold_questions") if isinstance(data.get("gold_questions"), list) else []
    for item in questions:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            bad += 1
            continue
        flags = [] if _evidence(item) else ["no_evidence"]
        col.add("question", item, _evidence(item), call_id, flags)
        good += 1
    identity = data.get("work_identity")
    if isinstance(identity, dict):
        col.add("identity", identity, _evidence(identity), call_id)
        good += 1
    else:
        bad += 1
    if want_questions and not 5 <= len(questions) <= 8:
        col.warnings.append(f"{call_id}: {len(questions)} gold questions returned; the guide asks for 5 to 8")
    return good, bad


def label_version(paper: PilotPaper, version: PilotVersion, parsed: ParsedPaper, client: LabelClient, *,
                  pages_per_call: int = DEFAULT_PAGES_PER_CALL, source_file: str = "",
                  now: Callable[[], str] = _now, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Run the version's tasks through ``client`` and return its provisional label document."""
    col = _Collector(paper.pilot_id, version.key, parsed)
    calls: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    plan: List[Tuple[str, str, str]] = []
    pages = list(parsed.pages)
    if TASK_VALUES in version.tasks:
        for first, last in page_chunks(len(pages), pages_per_call):
            chunk = [p for p in pages if first <= p["page"] <= last]
            prompt = guide.build_prompt(TASK_VALUES, paper, version, pages=chunk, page_range=f"{first}-{last}")
            plan.append((TASK_VALUES, f"{first}-{last}", prompt))
    if TASK_FIGURES in version.tasks:
        plan.append((TASK_FIGURES, "figures", guide.build_prompt(TASK_FIGURES, paper, version,
                                                                 figures=parsed.figures)))
    if TASK_CONTEXT in version.tasks:
        plan.append((TASK_CONTEXT, f"1-{len(pages)}", guide.build_prompt(TASK_CONTEXT, paper, version, pages=pages)))
    for index, (task, scope, prompt) in enumerate(plan, start=1):
        call_id = f"{paper.pilot_id}-{version.key}-call-{index:02d}"
        record: Dict[str, Any] = {"call_id": call_id, "task": task, "scope": scope,
                                  "prompt_sha256": sha256_text(prompt), "started_at": now()}
        started = clock()
        try:
            raw = client.chat(prompt)
        except Exception as exc:  # noqa: BLE001 - a provider fault is recorded as missing, never filled in
            record.update(status="failed", error=sanitize(f"{type(exc).__name__}: {exc}", 300))
            raw = None
        record["elapsed_s"] = round(clock() - started, 3)
        if raw is not None:
            record["response_sha256"] = sha256_text(raw if isinstance(raw, str) else repr(raw))
            data = parse_response(raw)
            if data is None:
                record.update(status="unparsable", error="answer is not a JSON object")
            else:
                if task == TASK_VALUES:
                    good, bad = _take_values(data, col, call_id)
                elif task == TASK_FIGURES:
                    good, bad = _take_figures(data, col, call_id)
                else:
                    good, bad = _take_context(data, col, call_id, version.gold_questions)
                record.update(status="ok" if bad == 0 else "partial", items_returned=good, items_invalid=bad)
        if record["status"] != "ok":
            missing.append({"task": task, "scope": scope, "call_id": call_id, "reason": record["status"]})
        calls.append(record)
    counts = {k: v for k, v in sorted(col.counts.items())}
    counts["quote_not_located"] = sum(1 for it in col.items if "quote_not_located" in it["flags"])
    return {
        "label_format": LABEL_FORMAT,
        "status": LABEL_STATUS,
        "pilot_id": paper.pilot_id,
        "doi": paper.doi,
        "version": version.key,
        "provenance": {
            "labeler_model": client.model,
            "backend": "cborg",
            "guide_version": guide.GUIDE_VERSION,
            "prompt_template_sha256": guide.template_sha256(),
            "parser_revision": parsed.parser_revision,
            "source_sha256": parsed.source_sha256,
            "source_file": source_file,
            "created_at": now(),
            "tool_revision": TOOL_REVISION,
            "pages_per_call": pages_per_call,
        },
        "complete": not missing,
        "calls": calls,
        "missing": missing,
        "warnings": col.warnings,
        "counts": counts,
        "items": col.items,
        "review": {"status": "unreviewed", "note": "Model labels are provisional until a person reviews them (H3 B4)."},
    }


__all__ = ("CborgLabelClient", "DEFAULT_PAGES_PER_CALL", "LABEL_FORMAT", "LABEL_STATUS", "LOCATED", "LabelClient",
           "TOOL_REVISION", "calls_for", "label_version", "page_chunks", "parse_response")
