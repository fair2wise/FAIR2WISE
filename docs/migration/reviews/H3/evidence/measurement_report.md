# T07 measurement report (decision reserved for H3)

Harness `t07-measure-1`. Fixture: {'entities': 133, 'mentions': 24, 'true_merges': 13, 'negative_controls': 9, 'novel': 2}.

## Caveats

- Judges are deterministic stand-ins, not language models.
- The encoder hashes character n-grams; it is not the sentence-transformer model.
- Baseline prompt is rebuilt with the current build_merge_prompt over all names; the 222b1e7 prompt text itself is not in this repo.
- The fixture is synthetic; numbers are not corpus estimates. The decision belongs to H3.

## Judge: permissive

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 12 | 7 | 0 | 0 | 6 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k5 | 7 | 7 | 5 | 0 | 6 | 0.615 | 24 | 1063.6 | 1097 |
| faiss_shortlist_k12 | 7 | 7 | 5 | 0 | 6 | 0.615 | 24 | 1115.6 | 1154 |
| candidate_search | 12 | 0 | 0 | 1 | 0 | 1.0 | 18 | 191.6 | 260 |

### Missing index (permissive)

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 12 | 7 | 0 | 0 | 6 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k12 | 0 | 0 | 13 | 0 | 0 | 0.0 | 0 | 0 | 0 |
| candidate_search_lexical_only | 12 | 0 | 0 | 1 | 0 | 1.0 | 16 | 177.3 | 201 |
- `baseline_full_list` false merges: ['wide-angle scattering at grazing incidence', 'WTe2', 'Al2O3[0001]', 'USAXS', 'GISAXS', 'PC71BM', 'gamma-Fe2O3']; true match outside the shortlist: none
- `faiss_shortlist_k5` false merges: ['wide-angle scattering at grazing incidence', 'WTe2', 'Al2O3[0001]', 'USAXS', 'GISAXS', 'PC71BM', 'gamma-Fe2O3']; true match outside the shortlist: ['GI-WAXS', 'small angle x-ray scattering', 'TEM', 'XRD', 'P3HT']
- `faiss_shortlist_k12` false merges: ['wide-angle scattering at grazing incidence', 'WTe2', 'Al2O3[0001]', 'USAXS', 'GISAXS', 'PC71BM', 'gamma-Fe2O3']; true match outside the shortlist: ['GI-WAXS', 'small angle x-ray scattering', 'TEM', 'XRD', 'P3HT']
- `candidate_search` false merges: none; true match outside the shortlist: none

## Judge: conservative

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 9 | 0 | 4 | 0 | 0 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k5 | 4 | 0 | 9 | 0 | 0 | 0.615 | 24 | 1063.6 | 1097 |
| faiss_shortlist_k12 | 4 | 0 | 9 | 0 | 0 | 0.615 | 24 | 1115.6 | 1154 |
| candidate_search | 10 | 0 | 3 | 0 | 0 | 1.0 | 18 | 191.6 | 260 |

### Missing index (conservative)

| strategy | correct_merges | false_merges | false_splits_no_match | false_splits_held_visible | negative_controls_merged | recall_in_shortlist | judge_calls | prompt_tokens_mean | prompt_tokens_max |
|---|---|---|---|---|---|---|---|---|---|
| baseline_full_list | 9 | 0 | 4 | 0 | 0 | 1.0 | 24 | 1990.7 | 2013 |
| faiss_shortlist_k12 | 0 | 0 | 13 | 0 | 0 | 0.0 | 0 | 0 | 0 |
| candidate_search_lexical_only | 10 | 0 | 3 | 0 | 0 | 1.0 | 16 | 177.3 | 201 |
- `baseline_full_list` false merges: none; true match outside the shortlist: none
- `faiss_shortlist_k5` false merges: none; true match outside the shortlist: ['GI-WAXS', 'small angle x-ray scattering', 'TEM', 'XRD', 'P3HT']
- `faiss_shortlist_k12` false merges: none; true match outside the shortlist: ['GI-WAXS', 'small angle x-ray scattering', 'TEM', 'XRD', 'P3HT']
- `candidate_search` false merges: none; true match outside the shortlist: none
