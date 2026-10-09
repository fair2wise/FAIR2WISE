# Pre-migration change 2430320

Commit `243032056a72e03b2dbd4aa24fe75c6a45bc80cc` sits on `forge_merge` directly
above the frozen baseline `222b1e7`. It holds local work made before the
migration started. It is not migration work and carries no plan task ID.

**Owner decision (2026-10-09):** `222b1e7` remains the frozen H0 baseline. The
companion anchors (B01-B36, route spans, inventory hashes) are valid for
`222b1e7` and are not refreshed. Parity and characterization comparisons use
`222b1e7` behavior as the reference.

## Effect on the baseline inventory

Running `companion/scripts/verify_handoff.py` against `git archive 2430320`
reports 717 static mismatches (647 AST spans, 28 file hashes, 38 route
span/decorator records, 4 supporting/v4 hashes). The mismatches come from
changed hashes and shifted lines in these baseline seam files:

- `app/modules/f2w_agent/api.py`, `f2w_agent/trace.py`
- `app/modules/kg_rag_api.py`, `scripts/app/modules/kg_rag_api.py`
- `app/modules/term_extractor/clients.py`, `orchestrator.py`, `tools.py`
- `app/modules/cborg_http.py`, `app/modules/extract_terms.py`
- `scripts/harvest_rsoxs.py`, `scripts/run.py`, `scripts/start_agent_backend.sh`,
  `scripts/test_chat_apis.py`, `storage/schema/README.md`, and five test files

Contract surface checked by hand against `222b1e7`:

- All 19 UI-agent route decorators in `f2w_agent/api.py` are unchanged.
- No function or class was removed from the eight seam modules above.
- Added symbols: `cborg_http.stream_chunk_text`, `collect_stream_text`,
  `collect_stream_text_async`, `_stream_piece`; new module
  `term_extractor/merge_index.py` (`TermMergeIndex`, `build_merge_prompt`,
  `clean_term`).

## Behavior change: term merge (B14), counted as early T07 work

At `222b1e7`, `fuzzy_merge_term` prompts the LLM with every name in
`store.all_display_names()`. At `2430320`:

1. A SentenceTransformers + FAISS `IndexFlatIP` index returns the top-k nearest
   registered names (`F2W_MERGE_TOP_K`, default 12; model `F2W_MERGE_EMBED_MODEL`,
   default `paraphrase-MiniLM-L6-v2`). Only those candidates go into the prompt.
2. The LLM answer is accepted only if it is one of those candidates.
3. If `ToolState.merge_index` is `None`, the tool logs a warning and returns
   `no_match` without calling the LLM. At `222b1e7` it ran the full-list prompt.
4. Newly registered terms are added to the index.

The plan (section 3.1, T07) treats bounded candidate shortlisting as an
explicit, evaluated upgrade, not an existing baseline capability. Disposition:

- Track it as an early T07 upgrade.
- At H2, characterize the `222b1e7` full-list behavior as the baseline.
- At H3, measure the shortlist against that baseline on approved fixtures:
  merge outcomes, named negative controls, recall when the true match falls
  outside top-k, and the no-index path in item 3.
- Item 3 is a behavior difference to resolve explicitly at H3. Silently
  skipping merges when no index is configured may count as a regression.

## Other additions (no baseline contract change)

- Eval harness and cases: `evals/`, `scripts/run_eval_batch.py`, `tests/evals/`,
  `tests/test_eval_harness.py`
- Tomography harvesting: `scripts/harvest_tomo.py`, `tests/test_harvest_tomo.py`
- Trace tooling: `scripts/check_trace.py`, `scripts/annotation_report.py`
- Tests: `tests/test_term_merge_index.py`

These tests add 41 passes and 4 xfails at HEAD. They introduce no new
failures (see `baseline_tests.md`).
