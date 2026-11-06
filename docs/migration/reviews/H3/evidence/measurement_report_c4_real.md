# C4 measurement report, real encoder and real CBORG judge (decision reserved for the owner)

Harness `t07-measure-1+c4-real-encoder-judge`. Fixture: {'entities': 133, 'mentions': 24, 'true_merges': 13, 'negative_controls': 9, 'novel': 2}.

## Caveats

- Judge is a real language model: CBORG `lbl/cborg-deepthought` (free, LBL-hosted tier; owner decision 2026-11-06), temperature 0, one run, no retry. Model replies are not deterministic across runs.
- The harness's permissive/conservative personas are deterministic decision rules with no real-model counterpart; this run has one judge mode, `cborg-deepthought`.
- Encoder is the real sentence-transformer `sentence-transformers/paraphrase-MiniLM-L6-v2` (the F2W_MERGE_EMBED_MODEL default) on CPU from the local cache; it replaces the character-n-gram stand-in of measurement_report.json.
- Baseline prompt is rebuilt with the current build_merge_prompt over all names; the 222b1e7 prompt text itself is not in this repo.
- Legacy-prompt replies are normalized (last line, quotes stripped, None/NO_MATCH -> no merge); a reply that is not a listed name counts as no merge and is counted under run.judge.legacy_replies_not_a_listed_name.
- The fixture is synthetic (24 mentions, 133 entities); numbers are not corpus estimates. The decision (C1) belongs to the owner.
- Model under test for the surrounding H3 run: lbl/cborg-chat; labeler: lbl/gpt-oss-120b; judge: lbl/cborg-deepthought (roles pairwise different).

## Judge: cborg-deepthought

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 10 | 0 | 3 | 0 | 0 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k5 | 6 | 0 | 7 | 0 | 0 | 0.692 | 24 | 1058.8 | 1097 |
| faiss_shortlist_k12 | 6 | 0 | 7 | 0 | 0 | 0.692 | 24 | 1108.9 | 1155 |
| candidate_search | 12 | 0 | 0 | 1 | 0 | 1.0 | 19 | 198.7 | 257 |

### Missing index (cborg-deepthought)

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 10 | 0 | 3 | 0 | 0 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k12 | 0 | 0 | 13 | 0 | 0 | 0.0 | 0 | 0 | 0 |
| candidate_search_lexical_only | 12 | 0 | 0 | 1 | 0 | 1.0 | 16 | 177.3 | 201 |
- `baseline_full_list` false merges: none; true match outside the shortlist: none
- `faiss_shortlist_k5` false merges: none; true match outside the shortlist: ['GIWAXS', 'small angle x-ray scattering', 'RSoXS', 'XRD']
- `faiss_shortlist_k12` false merges: none; true match outside the shortlist: ['GIWAXS', 'small angle x-ray scattering', 'RSoXS', 'XRD']
- `candidate_search` false merges: none; true match outside the shortlist: none

## Calls

- Judge `lbl/cborg-deepthought`: 98 calls (9 identical-prompt cache hits, 0 failed), about 104820 prompt tokens and 1251 reply tokens (len/4 estimate), 258.5 s of model time.
- Legacy-prompt replies that were not a listed name: 0 of 72.
- Encoder `sentence-transformers/paraphrase-MiniLM-L6-v2`: 470 texts encoded locally.
