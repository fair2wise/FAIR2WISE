"""H3 measurement harness: baseline full-list vs existing FAISS shortlist vs new candidate search.

This module measures; it does not decide. The owner decides at H3 (see
``docs/migration/reviews/H0/pre_migration_changes.md`` item 3 and H1 decision.json).

Strategies, run on the same synthetic fixture (:mod:`.measurement_fixture`) with the same
stand-in judge, so differences come from retrieval and gating, not from model quality:

* ``baseline_full_list``: the 222b1e7 behaviour: every registered display name goes in the
  prompt (``merge_index.build_merge_prompt``), the answer is accepted only if it is in the list.
* ``faiss_shortlist``: the pre-migration behaviour at 2430320: the real ``TermMergeIndex`` with
  an injected encoder returns top-k names; no index means ``no_match`` without calling the judge.
* ``candidate_search``: this package: approved alias / exact label / typed bounded shortlist /
  constrained adjudication / mandatory safety check.

Judges are deterministic stand-ins (``permissive`` accepts any close string, ``conservative``
accepts only spelling variants and initialisms). They are NOT language models: results say how
retrieval and gating behave, not how a real model performs. No network, weights or env are used.

Metrics keep separate: ``false_merges`` (merged when expected nothing, or to the wrong entity),
``false_splits`` (expected entity not obtained; split into ``no_match`` and ``held_for_review``),
``recall_in_shortlist`` for true merges (true entity actually shown to the judge, including when it
is outside top-k), prompt sizes, judge calls, and the no-index path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from statistics import mean
from typing import Any, cast

from ..term_extractor.merge_index import TermMergeIndex, build_merge_prompt, clean_term
from .adjudication import Judge
from .candidate_search import CandidateSearch, IndexedEntity, estimate_tokens
from .embeddings import DeterministicEncoder
from .measurement_fixture import MENTIONS, FixtureMention, approved_alias_vocabulary, entities
from .merge_safety import fold, is_acronym_of, looks_like_an_acronym
from .online import ACCEPTED, SourceMention, resolve_mention
from .vocabulary import VocabularyApproval

HARNESS_VERSION = "t07-measure-1"


def _pick(term: str, labels: Sequence[str], mode: str) -> str | None:
    """Deterministic stand-in decision: the label the 'model' would merge ``term`` into, or ``None``."""
    best, best_score = None, 0.0
    for label in labels:
        if fold(label) == fold(term):
            score = 1.0
        elif (looks_like_an_acronym(term) or looks_like_an_acronym(label)) and (
            is_acronym_of(term, label) or is_acronym_of(label, term)
        ):
            score = 0.95
        elif mode == "permissive":
            score = SequenceMatcher(None, fold(term), fold(label)).ratio()
        else:
            score = 0.0
        if score > best_score:
            best, best_score = label, score
    threshold = 0.6 if mode == "permissive" else 0.9
    return best if best_score >= threshold else None


def make_judges(mode: str) -> dict[str, Callable[[str], str]]:
    """Two prompt-format adapters over one decision rule: legacy (bare name) and JSON (entity_id)."""

    def legacy(prompt: str) -> str:
        term = cast(re.Match[str], re.search(r'new term:\s+"(.*?)"', prompt)).group(1)
        labels = re.findall(r"^- (.*)$", prompt.split("Below is")[1].split("You must decide")[0], re.M)
        return _pick(term, labels, mode) or "NO_MATCH"

    def modern(prompt: str) -> str:
        term = cast(re.Match[str], re.search(r'extracted: "(.*?)"', prompt)).group(1)
        rows = re.findall(r"^- (\S+): (.*)$", prompt.split("this list only:")[1].split("Answer with")[0], re.M)
        label = _pick(term, [lab for _, lab in rows], mode)
        ident = next((i for i, lab in rows if lab == label), None)
        return json.dumps({"entity_id": ident, "confidence": 0.95 if ident else 0.0, "reason": "stand-in"})

    return {"legacy": legacy, "modern": modern}


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    true_rows = [r for r in rows if r["kind"] == "true_merge"]
    bad = [r for r in rows if r["outcome"] == "false_merge"]
    splits = [r for r in rows if r["outcome"] in ("false_split_no_match", "false_split_held")]
    prompts = [r["prompt_tokens"] for r in rows if r["prompt_tokens"]]
    return {
        "mentions": len(rows),
        "correct_merges": sum(1 for r in rows if r["outcome"] == "correct_merge"),
        "false_merges": len(bad),
        "false_merge_items": [r["surface"] for r in bad],
        "false_splits": len(splits),
        "false_splits_no_match": sum(1 for r in rows if r["outcome"] == "false_split_no_match"),
        "false_splits_held_visible": sum(1 for r in rows if r["outcome"] == "false_split_held"),
        "false_split_items": [r["surface"] for r in splits],
        "negative_controls_merged": sum(1 for r in rows if r["kind"] == "negative_control" and r["merged"]),
        "recall_in_shortlist": (
            round(sum(1 for r in true_rows if r["true_shown"]) / len(true_rows), 3) if true_rows else None
        ),
        "true_outside_shortlist": [r["surface"] for r in true_rows if not r["true_shown"]],
        "judge_calls": sum(1 for r in rows if r["prompt_tokens"]),
        "prompt_tokens_mean": round(mean(prompts), 1) if prompts else 0,
        "prompt_tokens_max": max(prompts) if prompts else 0,
    }


def _classify(m: FixtureMention, merged_to: str | None, held: bool) -> str:
    if merged_to is not None:
        return "correct_merge" if m.expected == merged_to else "false_merge"
    if m.expected is None:
        return "correct_separate"
    return "false_split_held" if held else "false_split_no_match"


def run_baseline(mentions: Sequence[FixtureMention], ents: Sequence[IndexedEntity], judge: Judge) -> list[dict[str, Any]]:
    """Full-list prompt over every display name (the 222b1e7 behaviour)."""
    names = [clean_term(e.label) for e in ents]
    by_label = {clean_term(e.label): e.entity_id for e in ents}
    rows = []
    for m in mentions:
        prompt = build_merge_prompt(clean_term(m.surface), names)
        reply = (judge(prompt) or "").strip()
        merged = by_label.get(reply) if reply in by_label else None
        rows.append(
            {
                "surface": m.surface,
                "kind": m.kind,
                "merged": merged is not None,
                "merged_to": merged,
                "prompt_tokens": estimate_tokens(prompt),
                "true_shown": m.expected is not None,
                "outcome": _classify(m, merged, False),
            }
        )
    return rows


def run_shortlist(
    mentions: Sequence[FixtureMention],
    ents: Sequence[IndexedEntity],
    judge: Judge,
    *,
    top_k: int,
    with_index: bool = True,
) -> list[dict[str, Any]]:
    """The pre-migration FAISS shortlist (real ``TermMergeIndex``); no index -> ``no_match``, no judge call."""
    names = [clean_term(e.label) for e in ents]
    by_label = {clean_term(e.label): e.entity_id for e in ents}
    index = TermMergeIndex(top_k=top_k, encoder=DeterministicEncoder()) if with_index else None
    if index is not None:
        index.ensure(names)
    rows = []
    for m in mentions:
        cleaned = clean_term(m.surface)
        candidates = index.query(cleaned) if index is not None else []
        prompt = build_merge_prompt(cleaned, candidates) if candidates else ""
        reply = (judge(prompt) or "").strip() if prompt else "NO_MATCH"
        merged = by_label.get(reply) if reply in candidates else None
        shown = m.expected is not None and any(by_label.get(c) == m.expected for c in candidates)
        rows.append(
            {
                "surface": m.surface,
                "kind": m.kind,
                "merged": merged is not None,
                "merged_to": merged,
                "prompt_tokens": estimate_tokens(prompt) if prompt else 0,
                "true_shown": shown,
                "outcome": _classify(m, merged, False),
            }
        )
    return rows


def run_candidate_search(
    mentions: Sequence[FixtureMention],
    ents: Sequence[IndexedEntity],
    judge: Judge,
    *,
    max_candidates: int,
    token_budget: int,
    with_index: bool = True,
    use_vocabulary: bool = True,
) -> list[dict[str, Any]]:
    """This package's online path; the vocabulary is approved in memory for the measurement only."""
    search = CandidateSearch(
        ents,
        encoder=DeterministicEncoder() if with_index else None,
        model="deterministic",
        max_candidates=max_candidates,
        token_budget=token_budget,
    )
    vocab = None
    if use_vocabulary:
        candidate = approved_alias_vocabulary()
        vocab = candidate.approve(
            VocabularyApproval("measurement-only", candidate.revision, "harness", datetime.now(UTC))
        )
    rows = []
    for m in mentions:
        res = resolve_mention(SourceMention(m.surface, m.category), vocabulary=vocab, search=search, judge=judge)
        merged = res.entity_id if res.status == ACCEPTED else None
        shown = m.expected is not None and (
            res.method in ("approved_alias", "identifier")
            or (res.shortlist is not None and any(c.entity_id == m.expected for c in res.shortlist.candidates))
        )
        tokens = res.verdict.prompt_tokens if res.verdict else 0
        rows.append(
            {
                "surface": m.surface,
                "kind": m.kind,
                "merged": merged is not None,
                "merged_to": merged,
                "prompt_tokens": tokens,
                "true_shown": shown,
                "status": res.status,
                "reason": res.reason,
                "method": res.method,
                "outcome": _classify(m, merged, res.status == "review"),
            }
        )
    return rows


def run_measurement(
    *, top_k: int = 5, default_top_k: int = 12, max_candidates: int = 8, token_budget: int = 400
) -> dict[str, Any]:
    """Run every strategy x judge x index availability and return the machine-readable report."""
    ents = entities()
    report: dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "fixture": {
            "entities": len(ents),
            "mentions": len(MENTIONS),
            "true_merges": sum(m.kind == "true_merge" for m in MENTIONS),
            "negative_controls": sum(m.kind == "negative_control" for m in MENTIONS),
            "novel": sum(m.kind == "novel" for m in MENTIONS),
        },
        "parameters": {
            "faiss_top_k_small": top_k,
            "faiss_top_k_default": default_top_k,
            "max_candidates": max_candidates,
            "token_budget": token_budget,
            "encoder": DeterministicEncoder.name,
        },
        "caveats": [
            "Judges are deterministic stand-ins, not language models.",
            "The encoder hashes character n-grams; it is not the sentence-transformer model.",
            "Baseline prompt is rebuilt with the current build_merge_prompt over all names; the 222b1e7 "
            "prompt text itself is not in this repo.",
            "The fixture is synthetic; numbers are not corpus estimates. The decision belongs to H3.",
        ],
        "results": {},
    }
    for mode in ("permissive", "conservative"):
        judges = make_judges(mode)
        block: dict[str, Any] = {}
        block["baseline_full_list"] = _summarise(run_baseline(MENTIONS, ents, judges["legacy"]))
        for k in sorted({top_k, default_top_k}):
            block[f"faiss_shortlist_k{k}"] = _summarise(run_shortlist(MENTIONS, ents, judges["legacy"], top_k=k))
        block["candidate_search"] = _summarise(
            run_candidate_search(
                MENTIONS, ents, judges["modern"], max_candidates=max_candidates, token_budget=token_budget
            )
        )
        block["no_index"] = {
            "baseline_full_list": block["baseline_full_list"],
            f"faiss_shortlist_k{default_top_k}": _summarise(
                run_shortlist(MENTIONS, ents, judges["legacy"], top_k=default_top_k, with_index=False)
            ),
            "candidate_search_lexical_only": _summarise(
                run_candidate_search(
                    MENTIONS,
                    ents,
                    judges["modern"],
                    max_candidates=max_candidates,
                    token_budget=token_budget,
                    with_index=False,
                )
            ),
        }
        report["results"][mode] = block
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable report; states what was measured and leaves the decision to H3."""
    lines = [
        "# T07 measurement report (decision reserved for H3)",
        "",
        f"Harness `{report['harness_version']}`. Fixture: {report['fixture']}.",
        "",
        "## Caveats",
        "",
    ] + [f"- {c}" for c in report["caveats"]]
    cols = [
        "correct_merges",
        "false_merges",
        "false_splits_no_match",
        "false_splits_held_visible",
        "negative_controls_merged",
        "recall_in_shortlist",
        "judge_calls",
        "prompt_tokens_mean",
        "prompt_tokens_max",
    ]

    def table(block: dict[str, Any]) -> list[str]:
        out = ["| strategy | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for name, s in block.items():
            if name == "no_index":
                continue
            out.append(f"| {name} | " + " | ".join(str(s[c]) for c in cols) + " |")
        return out

    for mode, block in report["results"].items():
        lines += ["", f"## Judge: {mode}", ""] + table(block)
        lines += ["", f"### Missing index ({mode})", ""] + table(block["no_index"])
        for name, s in block.items():
            if name == "no_index":
                continue
            lines.append(
                f"- `{name}` false merges: {s['false_merge_items'] or 'none'}; "
                f"true match outside the shortlist: {s['true_outside_shortlist'] or 'none'}"
            )
    return "\n".join(lines) + "\n"


def write_report(directory: Path, report: dict[str, Any] | None = None) -> list[Path]:
    """Write ``measurement_report.json`` and ``.md`` into ``directory`` (created if needed)."""
    report = report or run_measurement()
    directory.mkdir(parents=True, exist_ok=True)
    js, md = directory / "measurement_report.json", directory / "measurement_report.md"
    js.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md.write_text(render_markdown(report), encoding="utf-8")
    return [js, md]


if __name__ == "__main__":  # pragma: no cover
    import sys

    from ..cli_output import output

    for path in write_report(Path(sys.argv[1] if len(sys.argv) > 1 else "t07_measurement")):
        output.info(f"{path}")
