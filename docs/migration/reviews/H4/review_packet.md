# Human review packet: H4 (integrated candidate corpus)

Gate: H4
Approver: project owner
Status: **open, nothing approved.** Prepared 2026-11-09 on the owner's instruction ("Yes, prepare it", fifteenth set,
`docs/migration/reviews/owner_decisions_2026-11-03.md`). This packet records no decision. Every decision below is the
owner's: C1, opening the four apply gates, and promoting the B4 labels. `decision.json` for H4 does not exist yet.

## 1. What H4 approves, and what is evidenced

Plan wording (`docs/migration/plan.md`, gate matrix, "H4 - Integrated candidate corpus"):

- **Review evidence:** T24B/C candidate images and prefixed UI smoke; required binary suite completed PASS for text,
  figures, QA and T19D operations; versioned JSON output; app-provisioned 0.2.19 import; T25C local no-login flows and
  real ALS identity/role checks; T26B committed-artifact validation; exact-quote/privacy checks; no silent missing
  capability.
- **Blocks:** publishing the candidate corpus; enabling broad paid or background processing.
- **Safe while waiting:** bug fixes, read-only demos, packaging and adapter tests.
- T12 (the staging-only pilot, `plan.md`) "STOP H4": "do not weaken binary expected outcomes or required-case
  membership to make the pilot pass."

Evidence status (`docs/migration/task_status.json`; "merged" means on `forge_merge`, not that H4 is met):

| H4 evidence item | Task | Status | Where |
|---|---|---|---|
| Candidate images, prefixed UI | T24B, T24C | merged | task_status.json |
| Real ALS roles, no-login local flows | T25C | merged | task_status.json |
| Committed artifact validation | T26B | merged | task_status.json |
| JSON import into Tiled 0.2.19 | T08B | merged | task_status.json |
| Operations correctness and scale | T19C, T19D | merged | task_status.json |
| Extraction strategies and evidence | T06B | merged | task_status.json |
| Binary runner | T11B | merged | task_status.json |
| Canonicalization apply adapters | T07B | **blocked_on_owner** | `reviews/H4/apply_adapters.md` |
| Integrated pilot | T12 | **blocked_on_owner** | section 4 below |
| Required binary suite completed PASS | T11B/T12 | **not met** | section 2.3 |

I did not re-run any of these merged-task checks for this packet; "merged" is the recorded status only.

## 2. Decisions the owner must make

### 2.1 C1: which merge path becomes the default in extraction

H3 already adopted the recommendation "new candidate search, after a real-model rerun (C4)"
(`reviews/H3/decision.json`, C1-C5 "as recommended"). The rerun now exists, so the open question is whether to confirm.

Options: (a) baseline full-list prompt; (b) FAISS shortlist (the pre-migration path); (c) candidate search (new).

**Real-model numbers** (committed report `reviews/H3/evidence/measurement_report_c4_real.{md,json}`; judge
`lbl/cborg-deepthought`, real encoder `paraphrase-MiniLM-L6-v2`; fixture: 133 entities, 24 mentions, 13 true merges,
9 negative controls, 2 novel). Columns are correct merges / false merges / false splits (no match), with "held" meaning
a split that stays visible for review.

| Strategy | Synthetic, permissive judge | Synthetic, conservative judge | Real judge | Real recall in shortlist | Real mean prompt tokens |
|---|---|---|---|---|---|
| baseline full list | 12 / 7 / 0 | 9 / 0 / 4 | 10 / 0 / 3 | 1.0 | 1990.7 |
| FAISS shortlist k5 | 7 / 7 / 5 | 4 / 0 / 9 | 6 / 0 / 7 | 0.692 | 1058.8 |
| FAISS shortlist k12 | 7 / 7 / 5 | 4 / 0 / 9 | 6 / 0 / 7 | 0.692 | 1108.9 |
| candidate search | 12 / 0 / 0, 1 held | 10 / 0 / 3 | 12 / 0 / 0, 1 held | 1.0 | 198.7 |
| candidate search, no index (lexical only) | 12 / 0 / 0, 1 held | 10 / 0 / 3 | 12 / 0 / 0, 1 held | 1.0 | 177.3 |
| FAISS, no index | 0 / 0 / 13 | 0 / 0 / 13 | 0 / 0 / 13 | 0.0 | 0 |

Synthetic columns come from `reviews/H3/evidence/measurement_report.md` (deterministic stand-in judges, n-gram
encoder). The synthetic recall values there are 0.615 for FAISS; the real value is 0.692. In the real run the true
matches outside the FAISS shortlist were GIWAXS, small angle x-ray scattering, RSoXS and XRD. Real judge calls:
24 for baseline and FAISS, 19 for candidate search. Negative controls merged: 0 in every real row.

What the numbers support: candidate search found the most true merges (12 of 13, the 13th held for review), made no
false merge, and used prompts about a tenth of the baseline's. FAISS lost 7 of 13 true matches because they never
reached the judge. What they do not show: the real judge made no false merge even with the full list, so this run does
not demonstrate the safety check preventing one; only the synthetic permissive judge shows that (baseline 7 false
merges, candidate search 0).

**Honest limits** (all stated in the report's caveats and `reviews/H3/b4_c4_free_model_run.md`):

- One run, temperature 0, no retry; replies are not guaranteed identical on a second run.
- Synthetic 24-mention fixture; not a corpus estimate.
- Free judge (`lbl/cborg-deepthought`), chosen by the owner on 2026-11-06; the permissive/conservative personas have no
  real counterpart.
- The baseline uses the current `build_merge_prompt`; the `222b1e7` prompt text is not in this repository, so
  "baseline" is not the pre-migration prompt byte for byte. (I did not look for it outside the repository.)
- Zero legacy replies were unparseable (0 of 72).

**Recommendation:** choose (c) candidate search, with the "index unavailable" report required by C2. Keep the choice
reviewable: re-measure on pilot data (C5) before any broad use. Choosing C1 opens nothing by itself.

### 2.2 Opening the four apply gates

Gates: `apply_migration`, `apply_cleanup`, `apply_merge` (`app/modules/canonicalization/gates.py`) and extraction
`canonical_merge` (`app/modules/extraction/__init__.py`, `H3_GATES`).

- **Closed (today, all four):** `require_gate` raises `ApplyRefused("gate_closed")`; the V81 route answers
  `501 unsupported_capability` with `reason = gate_closed` before anything is recorded. This holds on `:local` too.
  The same check also refuses if extraction `canonical_merge` is closed, even when the route's own gate is open.
- **Open:** the gate check passes, but nothing is applied unless an `ApprovalRecord` authorizes the exact plan hash,
  target and scope, the source and vocabulary revisions are unchanged, and the vocabulary is approved. Opening a gate
  is not an approval of any merge.
- **How:** a gate is a constant, not configuration. The owner opens one by editing `gates.py` / `H3_GATES` after
  review; environment variables and arguments cannot (module docstring).

What must be true first:

1. C1 decided (2.1).
2. A vocabulary is wired and approved. That is H5 work. Today no vocabulary is wired on the image, so the V81 route
   answers `unsupported_capability` even for a dry run (`tests/api_v1/test_v1_merge_apply.py::test_not_wired_is_the_typed_501`;
   `apply_adapters.md`). The dry run you would want to review therefore cannot yet run on `:local`.
3. The items `apply_adapters.md` says the rerun must still show on the real corpus: false merges 0 on the named
   negative controls, the false-split rate, prompt and candidate sizes within budget, and dry-run reports that look
   right for the accepted proposals you review. The fixture run covers none of the real-corpus parts.
4. Who emits `canonical_merge` proposals into the review queue (nobody does yet; no emitter is switched on).

**Recommendation:** do not open any gate at H4. Approve C1 only, and open the gates in one later step, after the
vocabulary is wired (H5) and a real-corpus dry run has been reviewed. If you want one gate earlier, `apply_merge` plus
`canonical_merge` is the narrowest; `apply_migration` and `apply_cleanup` can wait. Publication to any Tiled target
still needs H5 regardless.

### 2.3 B4 labels: promotion to gold

State today (checked against `/Users/david/.f2w/pilot_labels/`, counts only; no paper text is copied here): three
pilot papers A, B and C (four label files, C has a published and a preprint version), all
`provisional_model_label`, labeler `lbl/gpt-oss-120b`. No review decision has been entered in any sheet
(the `decision` column is empty in all 381 rows), and no gold file exists.

| Paper | Version | Labels | Quotes not located | Unparsed calls |
|---|---|---:|---:|---|
| A | repository copy | 137 | 16 | none |
| B | published | 112 | 30 | call 02 (values, pages 6-10) |
| C | published | 61 | 12 | calls 02 (values), 05 (figures), 06 (context) |
| C | preprint | 71 | 3 | call 05 (values, pages 21-25) |

Sheet row counts are 137 (A), 112 (B) and 132 (C, both versions). "Quotes not located" (61 in all) are sheet rows whose
quote status is not_found, wrong_page or missing_quote; this matches `b4_c4_free_model_run.md`. Five of 25 calls
returned something that was not a JSON object; the tool records them as missing and does not retry, so B and C are
`complete: false` and cannot become complete gold until relabelled or filled by hand.

What to review (process): open `review_sheet.csv` (or `.md`) for each paper under `/Users/david/.f2w/pilot_labels/<A|B|C>/`
and, for each row, enter accept, correct (with `corrected_json`) or reject in the `decision` column; reject or fix every
row whose quote was not located; relabel the five missing calls with `--force` after reviewing, or cover them by hand.
The sheets hold verbatim paper quotes; keep them private. Then run, per paper:

```
python -m evals.binary.label promote --paper <A|B|C> --reviewer "<Full Name>"
```

Promotion refuses rows it cannot verify and prints them; the command exits non-zero until the paper is complete.

**Recommendation:** promote A first (it has no missing calls), then B and C after relabelling. A reviewer who is a
person is what turns labels from provisional to gold (H3 B4: "Human review of provisional labels before they count as
gold"). Until then, EXT-04 and FIG-03 report PROVISIONAL, never PASS.

### 2.4 Binary suite today (context for 2.3 and section 3)

`reviews/H3/evidence/binary_report_b4_provisional.{md,json}` (run on a `git archive` export of `bc659ef`):

- Gate NOT_PASS; 108 required cases, 68 blocking; provisional cases EXT-04 and FIG-03.
- Execution states: 79 completed (all PASS), 22 blocked, 6 timeout, 1 skipped. 29 cases have no verdict.
- The 6 timeouts (5 OPS, 1 QA; OPS-04 and QA-02 are label cases) ran while load average exceeded 300; they are
  timeouts, not failures. Rerun on a quiet machine.
- Of the 13 label cases: 2 completed (PROVISIONAL), 9 blocked (oracles still await owner-approved rubrics or gold),
  2 timed out.
- No model call in the runner (`live_model_used` false); judge and model-under-test ids are recorded only.

H4 asks for the required suite "completed PASS". It is not, and 22 blocked cases are not a scientific failure but do
keep the gate closed. I did not verify the blocked-case list beyond these counts.

## 3. Remaining H4 evidence checklist

| Item | State |
|---|---|
| T24B/C, T25C, T26B, T08B, T19C/D, T06B, T11B | done (merged); not re-run here |
| C4 real-model rerun | done (free judge, one run); C1 decision waits on the owner |
| T07B apply adapters (design, tests, V81 route) | done; gates closed; blocked on the owner (C1, gates) and H5 (vocabulary) |
| V81 capability | decided 2026-11-09: `graph.publish` (a real apply also needs `curation.approve` via `create_approval`) |
| B4 label review and promotion | **blocked on the owner** (2.3) |
| Rubrics for extraction, chat and figures (9 blocked label cases) | **blocked on the owner**; drafts only |
| Binary rerun after promotion | waits on promotion; needs a quiet machine (section 5) |
| T12 pilot: JSON built with Tiled unavailable, dry-run/import rehearsal of the same file | waits on promotion and C1 |
| T12 pilot cases: one ambiguous merge, one failed chunk, one duplicate version, one unavailable provider, figure/table evidence | waits on the same |
| Staging reader/admin sign-in check | **blocked on infrastructure** (D3.20: runs after the first CI image build, by the owner with their ALS account; exact commands prepared by the integrator) |
| GitHub publish setup | **blocked on infrastructure**: create the two GitHub environments (`ghcr-publish`, `ghcr-als-prod`), set repository variable `O06_APPROVED=true` (environments first), registry `ghcr.io/fair2wise/fair2wise` public (D2.4); see `reviews/H6/o06_tag_rules_proposal.md`, `reviews/OPEN_DECISIONS.md` D2.2/D2.4 |
| Exact-quote and privacy checks | QUOTE and SEC cases completed in the provisional report (2 and 5); real-paper quote checks wait on the pilot |
| Capability parity report and before/after bundle (T12) | not started |

Plan wording for T12 says "proposed 12 representative Work families"; the owner narrowed B4 to three papers
(`decision.json`, 2026-10-16g). Whether three satisfies T12 is not stated anywhere I found; it is a question for the
owner (I recommend yes, with the limit written into the H4 decision). I could not verify whether the GitHub
environments, variable or package visibility have already been set; the repository state is outside the files I read.

## 4. Owner-gated items touching H4

From T07B (`reviews/H4/apply_adapters.md`):
1. V81 capability: **decided** `graph.publish` (2026-11-09). Alternatives not chosen: `jobs.admin`, `curation.approve`.
2. Opening `apply_migration`, `apply_cleanup`, `apply_merge` and extraction `canonical_merge` (2.2).
3. Which vocabulary is wired and approved on the image (H5); until then V81 is a typed 501.
4. The node fields a merge writes (`canonical_*`) and whether H5 publication projects them.
5. Who emits `canonical_merge` proposals into the queue.

From the paper-handler notes (`reviews/H8/handler_port_notes.md`, gaps list): production port adapters and live-service
wiring (Tiled catalog, vocabulary matcher, parser) are not shipped, so those job kinds answer `unsupported_capability`;
proposal volume needs an auto-approval-versus-review decision. These touch "broad background processing" which H4 blocks.
Gap 1 there (applying accepted proposals) was written before T07B; T07B covers canonical merges only, as far as I read.

From T19E (`reviews/H6/t19e_closure.md` section 3; these are H6 items, listed because H4 requires T19D operations):
catalog route, OP01 session-scope route, operations job panel capability flag, OP04/OP05 (need E2 and an owner decision).

From T27 (`reviews/H7/readthrough_F25_F38.md`; H7 items): 13 suspected code bugs and 4 contract docstrings await the
owner. B11 and B13 describe value/unit parsing in `app/modules/agents/properties.py`, which `term_extractor` imports;
whether that touches the H4 "number+unit in quote" check I did not verify.

## 5. Re-run commands (after B4 promotion, on a quiet machine)

Check the load first (`uptime`; the earlier timeouts happened above 300). Run from the repository root, not in a
worktree under another test run, and use the owner's own CBORG credential outside any agent (agents never read the
`.env`). Replace `<out>` with a new directory.

```
python -m evals.binary.runner --out-dir <out> --basename binary_report_h4 \
  --labels-dir /Users/david/.f2w/pilot_labels \
  --judge-model lbl/cborg-deepthought --model-under-test lbl/cborg-chat
```

Add `--subject-snapshot storage/kg/<corpus>/<id>` once the pilot's frozen JSON exists, so the report binds to it. One
run, no retry. Then review the report; recording the gate stays with the owner (`python -m evals.binary.approve`).
Before relying on a manifest, `python -m evals.binary.import_companion --check` must be clean (run for this packet: "up to date").
The C4 measurement itself (about 98 free-tier CBORG calls) is not re-run by a command I could find: the script that
produced `measurement_report_c4_real` (harness `t07-measure-1+c4-real-encoder-judge`) is not in the repository
(no file under it mentions that harness name). Re-running C4 needs that script recovered or re-created first.

## 6. Rollback and what this packet changed

Docs only: this file on branch `forge_merge-H4-packet`. Nothing under `app/` or `tests/`, no frozen doc, no
`decision.json`, no `approvals.json`. Discard by deleting the branch.

## Owner decision (to be filled in by the owner)

Decision:
Limits:
Date:
