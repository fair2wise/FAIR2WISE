# H3 B4 pilot labelling, C4 real-model rerun and binary runner: free LBL-hosted models

Date: 2026-11-06. Authority: owner decision 2026-11-06 (thirteenth set, `owner_decisions_2026-11-03.md`): "for the paid cborg run, let's just use the best cborg model available that is free". Nothing here approves a gate, edits `decision.json` or `approvals.json`, or promotes a label to gold.

## Models (the only ids sent to CBORG)

| Role | Model id |
|---|---|
| Model under test (did the extraction) | `lbl/cborg-chat` |
| Judge | `lbl/cborg-deepthought` |
| Labeler (B4) | `lbl/gpt-oss-120b` |

Role separation was checked by `evals/binary/labeling/roles.py::check_roles` (labeler, judge, model under test pairwise different) in the labelling CLI and in the runner; neither refused. The CBORG listing (1746 ids on 2026-11-06) contains all three ids as written. No other model id was used.

## Live calls

- Probe: 3 calls, one per id, to confirm the ids are accepted. 1 model-listing request (`--list-models`) and 1 inside the labelling CLI.
- B4 labelling (`lbl/gpt-oss-120b`, 5 pages per value call, text-only figures, no retry): 25 calls (A 4, B 5, C published 6, C preprint 10), about 1034 s. Token totals are not recorded by the tool (prompts are never stored); the C4-style len/4 estimate is not available for these calls.
- C4 (`lbl/cborg-deepthought`, temperature 0): 98 calls (+9 identical-prompt cache hits), 0 failed, about 104,820 prompt tokens and 1,251 reply tokens (len/4 estimate), 258 s model time. Cap in the script: 160 calls.
- Binary runner: no model call (the runner still uses its fake/none mode; `live_model_used` is false). The judge and model-under-test ids are recorded in the report only.
- Total chat calls: 126.

## B4 result (provisional)

| Paper | Version | Labels | Calls | Calls not parsed (missing) | Quote not located |
|---|---|---:|---:|---|---:|
| A | repository copy | 137 | 4 | none | 16 |
| B | published | 112 | 5 | call 02 (values, pages 6-10) | 30 |
| C | published | 61 | 6 | call 02 (values, pages 6-10), call 05 (figures), call 06 (context) | 12 |
| C | preprint | 71 | 10 | call 05 (values, pages 21-25) | 3 |

5 of 25 calls returned an answer that was not a JSON object. The tool records them as missing and does not retry, so papers B and C are `complete: false` and cannot be promoted to complete gold until relabelled or filled in by the reviewer. Every label is `provisional_model_label`.

Private labels (contain verbatim paper quotes and parsed page text; not committed): `/Users/david/.f2w/pilot_labels/` (`A/`, `B/`, `C/`, `parsed/`). Review sheets (CSV and MD, with quotes, so also private): `/Users/david/.f2w/pilot_labels/<A|B|C>/review_sheet.csv`.

## C4 result

Report: `evidence/measurement_report_c4_real.{json,md}`. Real encoder `paraphrase-MiniLM-L6-v2` (CPU, local cache) and real judge `lbl/cborg-deepthought`, same 24-mention synthetic fixture and parameters as `measurement_report.json`. Synthetic columns are the deterministic stand-in judges with the n-gram encoder.

| strategy | synthetic permissive | synthetic conservative | real: correct / false merges / false splits |
|---|---|---|---|
| baseline_full_list | 12 / 7 / 0 | 9 / 0 / 4 | 10 / 0 / 3 |
| faiss_shortlist_k5 | 7 / 7 / 5 | 4 / 0 / 9 | 6 / 0 / 7 |
| faiss_shortlist_k12 | 7 / 7 / 5 | 4 / 0 / 9 | 6 / 0 / 7 |
| candidate_search | 12 / 0 / 1 held | 10 / 0 / 3 | 12 / 0 / 1 held |
| candidate_search, no index (lexical) | 12 / 0 / 1 held | 10 / 0 / 3 | 12 / 0 / 1 held |

Real recall in shortlist: baseline 1.0, FAISS k5 and k12 0.692 (true match outside the shortlist: GIWAXS, small angle x-ray scattering, RSoXS, XRD), candidate_search 1.0. Mean prompt tokens: baseline 1990.7, FAISS k12 1108.9, candidate_search 198.7. FAISS with no index merges nothing (13 false splits).

## Binary runner

Report: `evidence/binary_report_b4_provisional.{json,md,xml}`, `--labels-dir /Users/david/.f2w/pilot_labels --judge-model lbl/cborg-deepthought --model-under-test lbl/cborg-chat`, no `--subject-snapshot` (the tracked snapshots under `storage/kg/` come from other models and bind nothing relevant here).

- `gate.status` NOT_PASS (unchanged, binary), `gate.provisional` true, provisional case ids EXT-04 and FIG-03.
- Execution states: completed 79, blocked 22, timeout 6, skipped 1. Verdicts: PASS 79 (of which EXT-04 and FIG-03 are reported as PROVISIONAL, never PASS), none 29.
- All 13 label cases have label basis `provisional` (A, B, C all provisional). Of them, 2 completed (reported PROVISIONAL), 9 blocked (oracles still await owner-approved rubrics or gold), 2 timed out (OPS-04, QA-02).
- The 6 timeouts happened while the machine load average was above 300 from other test runs; they are timeouts, not failures. Rerun on a quiet machine before relying on them.
- `source` fields (commit, app tree, dirty) are null: the run used a `git archive` export of forge_merge HEAD `bc659ef` (no `.git`).

## Caveats

- Labels are model output (`lbl/gpt-oss-120b`), unreviewed; results resting on them are provisional.
- The CBORG listing includes the three ids; the chat API accepted all three.
- C4: one run, temperature 0, no retry; the 222b1e7 prompt text is not in this repo, so the baseline uses the current `build_merge_prompt`; the harness's permissive and conservative personas have no real-model counterpart; the fixture is synthetic, not a corpus estimate.
- Free tier only; nothing billed per token was used.

## What the owner must do now

1. Review `/Users/david/.f2w/pilot_labels/<A|B|C>/review_sheet.csv` (accept, correct or reject each label; fix or reject the 61 labels whose quote was not located), then run `python -m evals.binary.label promote --paper <A|B|C> --reviewer "<Full Name>"`. B and C also need their 5 unparsed calls relabelled (`--force` after reviewing) or covered by hand.
2. Decide C1 on the real numbers in `measurement_report_c4_real.md`.
3. After promotion, rerun the binary runner on a quiet machine, then the human gate steps (`evals.binary.approve`) stay with the owner.
