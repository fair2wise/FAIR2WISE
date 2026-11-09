"""C4 rerun of the T07/H3 measurement harness with the real encoder and a real (free, LBL-hosted) CBORG judge.

Reuses app.modules.canonicalization.measurement (run_baseline / run_shortlist / run_candidate_search,
_summarise, render_markdown) unchanged; only the encoder and the judge are swapped:
  * encoder: sentence-transformers/paraphrase-MiniLM-L6-v2 (the F2W_MERGE_EMBED_MODEL default), CPU, offline cache;
  * judge: one CBORG model (argv[1], must be an allowed lbl/ id) at temperature 0, both prompt formats
    (legacy bare-name prompt of build_merge_prompt; JSON entity_id prompt of adjudicate_mention).
The harness's "permissive"/"conservative" personas are deterministic decision rules, not model personas, so
they have no real-model counterpart: one judge mode is run and named after the model.
Reports contain ids, counts and hashes; fixture surfaces (synthetic) as in the existing report. No key is logged.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.modules.canonicalization import measurement as M
from app.modules.canonicalization.candidate_search import estimate_tokens
from app.modules.canonicalization.measurement_fixture import MENTIONS, entities

ALLOWED_JUDGES = ("lbl/cborg-deepthought",)
ENCODER_NAME = "sentence-transformers/paraphrase-MiniLM-L6-v2"
MAX_CALLS = 160


class STEncoder:
    name = ENCODER_NAME

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer("paraphrase-MiniLM-L6-v2", device="cpu")
        self.texts_encoded = 0

    def __call__(self, texts):
        self.texts_encoded += len(texts)
        return np.asarray(self.model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False), dtype=np.float32)


def _clean_legacy(reply: str) -> str:
    text = reply.strip()
    # reasoning models may wrap the answer; take the last non-empty line, drop quotes/backticks/trailing period
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = lines[-1] if lines else ""
    text = text.strip("`\"' .")
    if text.lower() in ("none", "no_match", "no match", "null"):
        return "None"
    return text


class CborgJudge:
    """prompt -> reply through the F2W CBORG client; counts calls and estimated tokens; caches identical prompts."""

    def __init__(self, model: str, timeout: int = 240, dry_run: bool = False) -> None:
        assert model in ALLOWED_JUDGES, model
        self.model = model
        if dry_run:  # no network: the harness's permissive stand-in behind the same plumbing
            fakes = M.make_judges("permissive")

            class _Fake:
                def chat(self, prompt, **_):
                    return fakes["legacy"](prompt) if "Below is" in prompt else fakes["modern"](prompt)

            self.client: Any = _Fake()
        else:
            from app.modules.term_extractor.clients import make_chat_client

            self.client = make_chat_client("cborg", model)
        self.timeout = timeout
        self.calls = 0
        self.cache_hits = 0
        self.failed = 0
        self.prompt_tokens_est = 0
        self.reply_tokens_est = 0
        self.legacy_unlisted_replies = 0
        self.legacy_replies_total = 0
        self.elapsed = 0.0
        self._cache: dict[str, str] = {}

    def _call(self, prompt: str) -> str:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        if self.calls >= MAX_CALLS:
            raise RuntimeError(f"call cap {MAX_CALLS} reached")
        self.calls += 1
        self.prompt_tokens_est += estimate_tokens(prompt)
        t = time.monotonic()
        try:
            reply = self.client.chat(prompt, temperature=0.0, timeout=self.timeout, trace_label="c4_measure")
        except Exception:
            self.failed += 1
            raise
        finally:
            self.elapsed += time.monotonic() - t
        self.reply_tokens_est += estimate_tokens(reply)
        self._cache[key] = reply
        return reply

    def legacy(self, prompt: str) -> str:
        reply = _clean_legacy(self._call(prompt))
        self.legacy_replies_total += 1
        labels = set(re.findall(r"^- (.*)$", prompt.split("Below is")[1].split("You must decide")[0], re.M))
        if reply != "None" and reply not in labels:
            self.legacy_unlisted_replies += 1
        return reply

    def modern(self, prompt: str) -> str:
        return self._call(prompt)  # adjudicate_mention parses the JSON object itself

    def stats(self) -> dict[str, Any]:
        return {
            "judge_model": self.model, "backend": "cborg", "temperature": 0.0, "calls": self.calls,
            "cache_hits_identical_prompt": self.cache_hits, "failed_calls": self.failed,
            "prompt_tokens_estimated": self.prompt_tokens_est, "reply_tokens_estimated": self.reply_tokens_est,
            "legacy_replies_not_a_listed_name": self.legacy_unlisted_replies,
            "legacy_replies_total": self.legacy_replies_total, "elapsed_s": round(self.elapsed, 1),
        }


def main(argv: list[str]) -> int:
    judge_model, out_dir = argv[1], Path(argv[2])
    top_k, default_top_k, max_candidates, token_budget = 5, 12, 8, 400
    enc = STEncoder()
    # swap the harness encoder: the module instantiates DeterministicEncoder() inside run_shortlist/run_candidate_search
    M.DeterministicEncoder = lambda *a, **k: enc  # type: ignore[assignment]
    dry = len(argv) > 3 and argv[3] == "--dry-run"
    judge = CborgJudge(judge_model, dry_run=dry)
    if dry:
        mode_suffix = "-DRYRUN"
    ents = entities()
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = judge_model.split("/")[-1] + (mode_suffix if dry else "")
    block: dict[str, Any] = {}
    block["baseline_full_list"] = M._summarise(M.run_baseline(MENTIONS, ents, judge.legacy))
    for k in sorted({top_k, default_top_k}):
        block[f"faiss_shortlist_k{k}"] = M._summarise(M.run_shortlist(MENTIONS, ents, judge.legacy, top_k=k))
    block["candidate_search"] = M._summarise(M.run_candidate_search(
        MENTIONS, ents, judge.modern, max_candidates=max_candidates, token_budget=token_budget))
    block["no_index"] = {
        "baseline_full_list": block["baseline_full_list"],
        f"faiss_shortlist_k{default_top_k}": M._summarise(
            M.run_shortlist(MENTIONS, ents, judge.legacy, top_k=default_top_k, with_index=False)),
        "candidate_search_lexical_only": M._summarise(M.run_candidate_search(
            MENTIONS, ents, judge.modern, max_candidates=max_candidates, token_budget=token_budget, with_index=False)),
    }
    report: dict[str, Any] = {
        "harness_version": M.HARNESS_VERSION + "+c4-real-encoder-judge",
        "run": {"kind": "c4_real_model_rerun", "started_at": started,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "owner_decision": "2026-11-06 thirteenth set: best free CBORG model",
                "judge": judge.stats(), "encoder": {"name": ENCODER_NAME, "device": "cpu", "texts_encoded": enc.texts_encoded,
                                                     "source": "local Hugging Face cache, HF_HUB_OFFLINE=1"}},
        "fixture": {"entities": len(ents), "mentions": len(MENTIONS),
                    "true_merges": sum(m.kind == "true_merge" for m in MENTIONS),
                    "negative_controls": sum(m.kind == "negative_control" for m in MENTIONS),
                    "novel": sum(m.kind == "novel" for m in MENTIONS)},
        "parameters": {"faiss_top_k_small": top_k, "faiss_top_k_default": default_top_k, "max_candidates": max_candidates,
                       "token_budget": token_budget, "encoder": ENCODER_NAME, "judge_model": judge_model},
        "caveats": [
            f"Judge is a real language model: CBORG `{judge_model}` (free, LBL-hosted tier; owner decision 2026-11-06),"
            " temperature 0, one run, no retry. Model replies are not deterministic across runs.",
            "The harness's permissive/conservative personas are deterministic decision rules with no real-model"
            f" counterpart; this run has one judge mode, `{mode}`.",
            f"Encoder is the real sentence-transformer `{ENCODER_NAME}` (the F2W_MERGE_EMBED_MODEL default) on CPU"
            " from the local cache; it replaces the character-n-gram stand-in of measurement_report.json.",
            "Baseline prompt is rebuilt with the current build_merge_prompt over all names; the 222b1e7 prompt text"
            " itself is not in this repo.",
            "Legacy-prompt replies are normalized (last line, quotes stripped, None/NO_MATCH -> no merge); a reply that"
            " is not a listed name counts as no merge and is counted under run.judge.legacy_replies_not_a_listed_name.",
            "The fixture is synthetic (24 mentions, 133 entities); numbers are not corpus estimates. The decision (C1)"
            " belongs to the owner.",
            "Model under test for the surrounding H3 run: lbl/cborg-chat; labeler: lbl/gpt-oss-120b; judge:"
            " lbl/cborg-deepthought (roles pairwise different).",
        ],
        "results": {mode: block},
    }
    md = M.render_markdown(report).replace("# T07 measurement report (decision reserved for H3)",
                                           "# C4 measurement report, real encoder and real CBORG judge (decision reserved for the owner)")
    st = judge.stats()
    md += ("\n## Calls\n\n" + f"- Judge `{st['judge_model']}`: {st['calls']} calls ({st['cache_hits_identical_prompt']}"
           f" identical-prompt cache hits, {st['failed_calls']} failed), about {st['prompt_tokens_estimated']} prompt"
           f" tokens and {st['reply_tokens_estimated']} reply tokens (len/4 estimate), {st['elapsed_s']} s of model time.\n"
           f"- Legacy-prompt replies that were not a listed name: {st['legacy_replies_not_a_listed_name']} of"
           f" {st['legacy_replies_total']}.\n- Encoder `{ENCODER_NAME}`: {enc.texts_encoded} texts encoded locally.\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement_report_c4_real.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "measurement_report_c4_real.md").write_text(md, encoding="utf-8")
    print(json.dumps(st, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
