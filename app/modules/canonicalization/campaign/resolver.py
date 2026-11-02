"""Menu-constrained term resolver (C-10): votes, never authority.

The judge may only answer ``map_to`` a slug that is in the menu it was shown, ``novel``,
``reject_kind`` or ``abstain``. A free-form new id is refused. A ``map_to`` is *accepted* only when
(1) the slug is in the menu, (2) the evidence quote is found in the evidence shown, (3) confidence
clears the floor and (4) the mandatory safety check passes for every variant. Anything else keeps
its verdict but is ``accepted=False`` and the reason is recorded. Unresolved state is preserved.

Inference is an injected ``judge(prompt) -> str`` (see :class:`~..adjudication.ChatJudge`, an adapter
over the existing F2W chat clients). There is no provider, model or reasoning-level default; call and
prompt-token budgets are explicit and exhausting them yields ``abstain``, not a guess. ``cost_usd`` is
``None`` (unknown), never zero.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_resolver.py (BSD-3-Clause).
Rewritten: transport, reasoning-level plumbing and provider fields removed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import hashlib

from ..adjudication import clamp_confidence, parse_json_object
from ..candidate_search import estimate_tokens
from ..migration import unsafe_reason
from ..vocabulary import Vocabulary
from .models import FRAMINGS, VERDICTS, TermEvidence, TermVerdict

REJECT_KINDS = (
    "software",
    "procedure",
    "junk:fragment",
    "junk:sentence",
    "junk:property_or_result",
    "junk:generic",
)


@dataclass(frozen=True)
class ResolverConfig:
    """Acceptance floor and call/prompt budgets for the resolver; ``model`` is only recorded on verdicts."""

    min_confidence: float = 0.8
    max_calls: int = 1000
    max_prompt_tokens: int = 1500
    model: str = ""


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


class StructuredTermResolver:
    """Resolve one :class:`TermEvidence` per call, within explicit budgets."""

    def __init__(
        self, judge: Callable[[str], str], vocab: Vocabulary, config: ResolverConfig | None = None
    ) -> None:
        if judge is None:
            raise ValueError("a judge is required; there is no default provider")
        self.judge, self.vocab, self.config = judge, vocab, config or ResolverConfig()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def build_prompt(self, ev: TermEvidence, framing: str) -> str:
        """Render the judge prompt for ``ev`` under ``framing``.

        Snippets are dropped from the end until the prompt fits
        ``config.max_prompt_tokens`` (or none remain).
        """
        menu = "\n".join(f"- {m.slug}: {m.display_name}" for m in ev.menu) or "(empty)"
        snippets = list(ev.snippets)
        while True:
            context = "\n".join(f'- "{s.text}"' for s in snippets) or "(none)"
            body = (
                f'Term: "{ev.surface}" (category: {ev.category}; {ev.distinct_publications} publications)\n'
                if framing == "menu_first"
                else ""
            )
            ask = (
                'Answer ONE JSON object: {"verdict": "map_to|novel|reject_kind|abstain", "target_slug": '
                '"<slug from the menu or null>", "reject_kind": "<kind or null>", "evidence_quote": "<exact '
                'words copied from the evidence>", "confidence": 0.0-1.0, "reason": "<one sentence>"}\n'
                "Rules: target_slug must be one of the menu slugs; never invent an id. Specific vs generic, "
                "different compounds, phases or techniques are different things. Unsure means abstain."
            )
            if framing == "menu_first":
                prompt = f"{body}Menu:\n{menu}\n\nEvidence:\n{context}\n\n{ask}"
            else:
                prompt = f'Evidence for "{ev.surface}":\n{context}\n\nMenu:\n{menu}\n\n{ask}'
            if estimate_tokens(prompt) <= self.config.max_prompt_tokens or not snippets:
                return prompt
            snippets.pop()

    def resolve(self, ev: TermEvidence, *, round_key: str, framing: str = "menu_first") -> TermVerdict:
        """Ask the judge about one term and return a verdict, accepted or not.

        Judge failures, unparseable replies, unknown verdicts and an exhausted call
        budget yield an ``abstain`` verdict with the cause in ``errors``.

        Raises:
            ValueError: If ``framing`` is not in ``FRAMINGS``.
        """
        if framing not in FRAMINGS:
            raise ValueError(f"unknown framing {framing!r}")
        prompt = self.build_prompt(ev, framing)
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        tokens = estimate_tokens(prompt)

        def verdict(
            kind: str,
            slug: str | None = None,
            reject: str | None = None,
            quote: str = "",
            conf: float = 0.0,
            reason: str = "",
            accepted: bool = False,
            errors: Iterable[str] = (),
        ) -> TermVerdict:
            return TermVerdict(
                ev.term_key,
                round_key,
                framing,
                kind,
                slug,
                reject,
                quote,
                conf,
                reason,
                accepted,
                tuple(errors),
                sha,
                self.config.model,
                None,
                tokens,
                0,
            )

        if self.calls >= self.config.max_calls:
            return verdict("abstain", reason="call budget exhausted", errors=("budget_exhausted",))
        self.calls += 1
        self.input_tokens += tokens
        try:
            raw = self.judge(prompt)
        except Exception as exc:  # noqa: BLE001
            return verdict("abstain", reason=f"{type(exc).__name__}: {exc}", errors=("judge_failed",))
        payload = parse_json_object(raw)
        if payload is None:
            return verdict("abstain", reason="reply was not JSON", errors=("unparseable",))
        kind = str(payload.get("verdict") or "abstain")
        if kind not in VERDICTS:
            return verdict("abstain", reason=f"unknown verdict {kind!r}", errors=("bad_verdict",))
        conf = clamp_confidence(payload.get("confidence"))
        quote = str(payload.get("evidence_quote") or "")
        reason = str(payload.get("reason") or "")[:400]
        errors = []
        if kind == "abstain":
            return verdict("abstain", conf=conf, reason=reason)
        if conf < self.config.min_confidence:
            errors.append("below_confidence_floor")
        if kind in ("map_to", "reject_kind") and (not quote or _norm(quote) not in _norm(ev.evidence_text())):
            errors.append("quote_not_in_evidence")
        slug = payload.get("target_slug")
        reject = payload.get("reject_kind")
        if kind == "map_to":
            if slug not in {m.slug for m in ev.menu}:
                errors.append("slug_not_in_menu")
            else:
                entry = self.vocab.get(str(slug))
                why = unsafe_reason(entry, ev.variants or (ev.surface,)) if entry else "unknown slug"
                if why:
                    errors.append("safety_check")
                    reason = f"{reason} [safety: {why}]"
        elif kind == "reject_kind" and reject not in REJECT_KINDS:
            errors.append("unknown_reject_kind")
        return verdict(
            kind,
            str(slug) if kind == "map_to" and slug else None,
            str(reject) if kind == "reject_kind" and reject else None,
            quote,
            conf,
            reason,
            accepted=not errors,
            errors=errors,
        )
