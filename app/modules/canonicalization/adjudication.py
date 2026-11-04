"""Constrained adjudication (C-06): the model proposes, deterministic checks dispose.

Two shapes share the same rules:

* :func:`adjudicate_group` decides one offline cluster in one call;
* :func:`adjudicate_mention` decides one online mention against a bounded, typed shortlist
  and may only answer with a shortlist ``entity_id`` or abstain.

Rules: no judge means ``keep`` (not merge, not reject); a judge fault or unparseable reply is
not a verdict; a canonical or id outside the offered set is refused, never minted; every
accepted alias or mapping must pass :func:`~.merge_safety.check_merge`, and anything the
check refuses is held for review with its reason. The model is never asked whether
polymorphs or specific/generic pairs are the same thing: the grouping layer's refusal
(``auto_applicable=False``) wins over any answer.

Inference goes through :class:`ChatJudge`, a thin adapter over the *existing* F2W chat client
classes (``term_extractor.clients.ChatClient``: ``chat(prompt, temperature=, timeout=)``). It
has no provider or model default: the caller passes an already-built client.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/group_adjudication.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
from typing import Any, TYPE_CHECKING

from .candidate_search import Shortlist, estimate_tokens
from .grouping import TermGroup
from .merge_safety import check_merge

if TYPE_CHECKING:
    from ..extraction.structured_output import SyncChatClient
    from ..json_types import JsonValue

logger = logging.getLogger(__name__)

AUTO_APPLY_CONFIDENCE = 0.8
#: Hard cap on a group prompt's context snippets so a prompt cannot grow with the corpus.
MAX_SNIPPETS_PER_MEMBER = 2
MAX_SNIPPET_CHARS = 240
MAX_GROUP_MEMBERS = 40

Judge = Callable[[str], str]


class ChatJudge:
    """Adapter from an existing F2W chat client to a ``judge(prompt) -> str`` callable."""

    def __init__(self, client: SyncChatClient, *, temperature: float = 0.0, timeout: int = 240) -> None:
        if client is None:
            raise ValueError("a chat client is required; there is no default provider")
        self.client = client
        self.temperature = temperature
        self.timeout = timeout

    def __call__(self, prompt: str) -> str:
        """Send `prompt` to the chat client and return its reply."""
        return self.client.chat(prompt, temperature=self.temperature, timeout=self.timeout)


_GROUP_PROMPT = """You are consolidating a controlled vocabulary for a scattering \
knowledge graph.

These surface forms were clustered as possibly naming the SAME {category}:

{members}

Context in which they appear:
{context}

Answer with ONE JSON object and nothing else:
{{"same_thing": true|false,
  "canonical": "<the form that should be kept, exactly as written above>",
  "aliases": ["<other forms that mean the same>"],
  "separate": ["<forms that are a DIFFERENT thing and must not be merged>"],
  "confidence": 0.0-1.0,
  "reason": "<one sentence>"}}

Rules:
- If any form names a different material phase, polymorph, isotope, or a \
different technique, put it in "separate". Two things that are merely RELATED \
are not the same thing.
- A specific instrument or sample is never the same thing as its generic class.
- "canonical" must be one of the forms listed, copied exactly.
- If you are unsure, say same_thing=false. A wrong merge cannot be undone."""


@dataclass
class GroupVerdict:
    """What to propose for one cluster."""

    category: str
    canonical: str
    aliases: list[str] = field(default_factory=list)
    separate: list[str] = field(default_factory=list)
    #: Aliases the model accepted but the deterministic check refused: (alias, reason).
    held_for_review: list[tuple[str, str]] = field(default_factory=list)
    same_thing: bool = False
    confidence: float = 0.0
    reason: str = ""
    method: str = "llm"
    group_auto_applicable: bool = True

    @property
    def auto_applicable(self) -> bool:
        """Eligible to enter a plan without individual review. Never means "applied"."""
        return (
            self.group_auto_applicable
            and self.same_thing
            and self.method == "llm"
            and self.confidence >= AUTO_APPLY_CONFIDENCE
            and bool(self.aliases)
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the judgement as a plain dict (confidence rounded to 3 places)."""
        return {
            "category": self.category,
            "canonical": self.canonical,
            "aliases": list(self.aliases),
            "separate": list(self.separate),
            "held_for_review": [list(x) for x in self.held_for_review],
            "same_thing": self.same_thing,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "method": self.method,
            "auto_applicable": self.auto_applicable,
        }


def build_group_prompt(group: TermGroup, contexts: dict[str, list[str]]) -> str:
    """One bounded prompt for a whole cluster (members and snippets are capped)."""
    members = group.members[:MAX_GROUP_MEMBERS]
    lines = [
        f"- {m}: {s[:MAX_SNIPPET_CHARS]}"
        for m in members
        for s in (contexts.get(m) or [])[:MAX_SNIPPETS_PER_MEMBER]
    ]
    return _GROUP_PROMPT.format(
        category=group.category,
        members="\n".join(f"- {m}" for m in members),
        context="\n".join(lines) or "(none available)",
    )


def _keep(group: TermGroup, method: str, reason: str) -> GroupVerdict:
    return GroupVerdict(
        group.category,
        group.representative,
        method=method,
        reason=reason,
        group_auto_applicable=group.auto_applicable,
    )


def parse_json_object(raw: JsonValue) -> dict[str, Any] | None:
    """The first JSON object in ``raw`` (tolerates prose or fences), else ``None``."""
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    end_incl = end + 1
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start:end_incl])
    except (ValueError, TypeError):  # the reply is not JSON
        return None
    return value if isinstance(value, dict) else None


def _strings(value: object) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def clamp_confidence(value: JsonValue) -> float:
    """Confidence in [0, 1]; unreadable is 0.0 (it must not clear the apply floor)."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def adjudicate_group(
    group: TermGroup, contexts: dict[str, list[str]], *, judge: Judge | None = None
) -> GroupVerdict:
    """Decide one cluster in one call. Never raises: a judge fault is not a verdict."""
    if judge is None:
        return _keep(group, "no_model", "no model available to adjudicate")
    try:
        raw = judge(build_group_prompt(group, contexts))
    except Exception as exc:  # noqa: BLE001 - judge is an arbitrary callable; any failure keeps the group unmerged
        logger.warning("group judge failed on %r: %s", group.representative, exc)
        return _keep(group, "llm_failed", f"{type(exc).__name__}: {exc}")
    payload = parse_json_object(raw)
    if payload is None:
        return _keep(group, "unparseable", "the model's reply was not JSON")
    members = set(group.members[:MAX_GROUP_MEMBERS])
    canonical = str(payload.get("canonical") or "").strip()
    if canonical not in members:
        return _keep(group, "canonical_not_in_group", f"model chose {canonical!r}, which is not a clustered form")
    separate = [s for s in _strings(payload.get("separate")) if s in members]
    accepted: list[str] = []
    held: list[tuple[str, str]] = []
    for alias in _strings(payload.get("aliases")):
        if alias not in members or alias == canonical or alias in separate:
            continue
        verdict = check_merge(canonical, alias, canonical_class=group.category, alias_class=group.category)
        if verdict.allowed:
            accepted.append(alias)
        else:
            held.append((alias, verdict.reason))
    return GroupVerdict(
        group.category,
        canonical,
        accepted,
        separate,
        held,
        bool(payload.get("same_thing")),
        clamp_confidence(payload.get("confidence")),
        str(payload.get("reason") or "")[:400],
        "llm",
        group.auto_applicable,
    )


_MENTION_PROMPT = """A new term was extracted: "{term}" (category: {category})

Pick the existing entity it is a different spelling or expansion of, from this list only:
{menu}

Answer with ONE JSON object and nothing else:
{{"entity_id": "<an id from the list, or null>", "confidence": 0.0-1.0, "reason": "<one sentence>"}}

Rules:
- Merge only if the new term is the same thing written differently (case, punctuation, plural, typo, acronym).
- A specific instrument, sample, phase or surface is never the same as its generic class.
- Different compounds, techniques or acronyms (SEM/TEM, MoTe2/WTe2) are different things.
- If none is the same thing, answer null. A wrong merge cannot be undone."""


@dataclass(frozen=True)
class MentionVerdict:
    """Outcome of constrained adjudication for one mention."""

    status: str  # "match", "abstain", "refused", "no_judge", "judge_failed", "unparseable", "not_offered"
    entity_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    prompt_tokens: int = 0


def build_mention_prompt(mention: str, category: str, shortlist: Shortlist) -> str:
    """Prompt over the bounded shortlist only; its size is capped by the shortlist's budget."""
    return _MENTION_PROMPT.format(term=mention, category=category or "unspecified", menu=shortlist.render())


def adjudicate_mention(
    mention: str,
    category: str,
    shortlist: Shortlist,
    *,
    judge: Judge | None = None,
    min_confidence: float = AUTO_APPLY_CONFIDENCE,
) -> MentionVerdict:
    """Ask the judge to choose among shortlist ids; the id must be offered and pass the safety check."""
    if not shortlist.candidates:
        return MentionVerdict("abstain", reason="empty shortlist")
    if judge is None:
        return MentionVerdict("no_judge", reason="no model available to adjudicate")
    prompt = build_mention_prompt(mention, category, shortlist)
    tokens = estimate_tokens(prompt)
    try:
        raw = judge(prompt)
    except Exception as exc:  # noqa: BLE001 - judge is an arbitrary callable; any failure is a judge_failed verdict
        return MentionVerdict("judge_failed", reason=f"{type(exc).__name__}: {exc}", prompt_tokens=tokens)
    payload = parse_json_object(raw)
    if payload is None:
        return MentionVerdict("unparseable", reason="reply was not JSON", prompt_tokens=tokens)
    chosen = payload.get("entity_id")
    confidence = clamp_confidence(payload.get("confidence"))
    reason = str(payload.get("reason") or "")[:400]
    if chosen in (None, "", "null"):
        return MentionVerdict(
            "abstain", confidence=confidence, reason=reason or "model abstained", prompt_tokens=tokens
        )
    by_id = {c.entity_id: c for c in shortlist.candidates}
    if str(chosen) not in by_id:
        return MentionVerdict("not_offered", reason=f"{chosen!r} is not on the shortlist", prompt_tokens=tokens)
    cand = by_id[str(chosen)]
    verdict = check_merge(cand.label, mention, canonical_class=cand.category or None, alias_class=category or None)
    if not verdict.allowed:
        return MentionVerdict("refused", cand.entity_id, confidence, f"safety check: {verdict.reason}", tokens)
    if confidence < min_confidence:
        return MentionVerdict(
            "abstain", cand.entity_id, confidence, f"confidence {confidence:.2f} below floor", tokens
        )
    return MentionVerdict("match", cand.entity_id, confidence, reason, tokens)
