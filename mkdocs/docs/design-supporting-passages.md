# Design: supporting passages for every term and relationship

Status: proposal for owner review (2026-11-24). Nothing below is built except the two fixes already on `forge_merge`
(page context survives the tool threads, a missing page never discards a passage; the extracted-bundle KG build keeps
the passages of a node that already existed). Measurements come from the real RSoXS papers in `papers/` (334 PDFs,
6,229 pages) and the shipped terms file; items not verified are marked.

## Where we are

| Fact | Measurement |
|---|---|
| Shipped terms with a passage (`extracted_terms_rsoxs_v1.json`) | 917 of 24,681 (3.7%); the legacy extractor's `aug21_*` files: 100% |
| Model gave a `context` sentence (tool path, real `lbl/cborg-chat`, 44 calls) | 37 (84%) |
| Existing passages found verbatim on the claimed page (normalised whitespace/case) | 1,221 of 1,636 (74.6%); with the tolerant locator 1,600 (97.8%); not grounded 36 (2.2%) |
| Passages that are the `text[:500]` fallback (exactly 500 chars) / header-like / do not contain the term | 213 (13%) / 139 (8.5%) / 236 (14%) |
| Passage length | median 203 chars; only about 54% inside 120-400 |
| Relations with evidence (`has_evidence`) | 0 of 82,195: the prompt never asks for it and the tool has no field |
| Term names found in their own source papers (2,000-term sample) | 90.5% exact, 91.9% with abbreviation/hyphen variants |
| Terms with a known page | 917 of 24,681; on the pilot the same: recovery cannot rely on pages |
| Page text after extraction | not kept anywhere; re-read from the PDF: 29 ms per page, deterministic, no scanned paper in the corpus |

## a. Requirement

Every extracted term and every relationship carries at least one supporting passage from the paper it was extracted
from, stored with the paper's file name and sha256 (from the bundle manifest) and, where known, the page. A passage is
one of two kinds: **quoted** (the model's sentence, verified in the source) or **located** (found by searching the
source for the term's mention). Both kinds are stored the same way; the kind is recorded.

## b. Grounding

A passage is kept only if the tolerant locator (next section) finds it in the extracted text of the named paper:
exact after whitespace normalisation, else the squash match (case, hyphenation, line breaks, dashes, ligatures,
Greek letters, sub/superscripts). A model sentence that is not found is rejected, counted (`passages_rejected`,
per paper) and never stored. The page is the page where it was found, not the page the model claimed, when they differ.
If the model gave no sentence, or its sentence was rejected, the term's mention is located in the page it came from
(then the paper's other pages) and the surrounding sentence is stored as `located`. If no mention is found, nothing
is stored and the term is counted as uncovered. The `text[:500]` fallback and the first-sentence-by-punctuation rule
of `_context_for_term` are removed.

## Quality rules (what a passage is)

Candidate sentences are computed once per page (the verdicts are term-independent and cached):

1. Sentence boundaries: `[.!?]` followed by space and a capital, bracket or digit; `et al.`, `Fig.`, `Eq.`, `e.g.`,
   `i.e.` and single initials do not end a sentence; a line break inside a sentence is a space; `x-\ny` is dehyphenated
   for display, with offsets kept into the page text.
2. Rejected: anything in or after the `References` heading; header, title, affiliation and boilerplate lines
   (institution words, e-mail or URL or DOI, long comma lists, copyright/received/accepted/license, journal name
   lines); citation entries (year + volume + page range, author-initial lists); equations and fragments (fewer than 5
   alphabetic words, under 55% letters, more than 6 math symbols); sentences without a capital, bracket or digit at the
   start or without final punctuation; figure captions (a paragraph starting `Fig.`/`Figure N` or with panel letters);
   page-number-only lines.
3. Accepted: a sentence that passes and contains the located mention. Shorter than 120 chars: extended with the next
   sentence when that one passes and the total stays at or under 400. Longer than 400: a clause window of at most 400
   chars around the mention (proposed, not prototyped) rather than rejection.
4. Ranking when several qualify: 120-300 chars first, then sentences with definition words (`is`, `are`, `defined`,
   `refers`, `called`, `known as`, `used`, `measured`), then the mention nearest the sentence start, then the earlier
   page. The best passage is stored **first**, because the per-paper disclosure caps cut in stored order.

Examples from a real 10-page paper (`10.1038_s41467-020-14549-w.pdf`), text as the extractor sees it:

| Term | Result |
|---|---|
| organic photovoltaics | accepted, p2 (181 chars): "Appropriately selected D:A combinations have enabled high performance in organic electronic devices such as organic photovoltaics (OPVs) ..." |
| GIWAXS | accepted, p8 (214 chars) |
| RSoXS | accepted, p8: matched the paper's "R-SoXS"; two sentences joined because the first is 91 chars (280 total) |
| polymer-fullerene | accepted, p6 (262 chars): matched "polymer:fullerene" |
| EL measurement / EL measurements | same passage, p7 (plural tolerated) |
| grazing-incidence wide-angle X-ray scattering (GIWAXS) | rejected: the long form occurs only in two sentences over 400 chars (this is why the clause window is proposed) |
| PCBM, nanostructure, polymer blend, thin film | rejected: found only in the reference list |
| Nature Communications, synchrotron | rejected: 20 header/boilerplate and 10 reference hits, 1 boilerplate and 2 reference hits |
| P3HT:PCBM, CH4, denoising diffusion probabilistic model (DDPM) | not found in this paper (correct: nothing invented) |

Known false accepts of the prototype: figure-caption sentences and Methods boilerplate ("The solid and dashed black
lines are fits ...", an instrument description). The caption rule above and an optional Methods-section exclusion are
proposed for the build.

## The tolerant locator

One pure function, used at extraction time (to verify or replace the model's sentence) and by the recovery job, so
both behave the same: `locate(page_text, term, *, abbr_defined=None) -> [Span(start, end, sentence_start,
sentence_end, form)]`.

1. Squash the page and the term: Greek letters to names (`α` to `alpha`), NFKC (ligatures, sub/superscripts, fullwidth),
   lower case, keep only letters and digits, with a map from squashed index to original offset. Whitespace, line
   breaks, hyphens, dashes, underscores, soft hyphens and brackets are thereby ignored.
2. Substring-find the squashed term; accept only when the original character before the span and after it (after an
   optional plural tail `s`, `es`, `'s`) is not alphanumeric.
3. A term of fewer than 3 alphanumeric characters (`D2`) needs a case-sensitive whole-token match.
4. `long form (ABBR)` is split: the long form is matched; the abbreviation alone counts only where the paper itself
   defines it (`(ABBR)` next to a long-form hit on that page, or `abbr_defined` from paper-level knowledge).
5. Sentence spans as in the quality rules.

Measured on the prototype: squash plus sentences for 10 pages 0.10 s once; locate 2,000 terms over 10 pages 2.0 s
(0.1 ms per term-page); with the per-page sentence verdicts cached, ranking adds little. Not verified: false-positive
rate of short acronyms across the whole corpus (33-term probe only).

## c. Merging

When a term is found again (same paper or another), passages are unioned per paper and de-duplicated by (paper, page,
text) at every step: the term store (done), the KG build of a bundle (done, `merge_existing`), continuation and
re-extraction. Nothing is overwritten; the caps apply when a node is shown, not when it is stored. The same for
relationships: evidence sentences are unioned by (paper, page, text) on the edge; the edge de-duplication by
(subject, predicate, object) keeps the union of evidence, not the first edge's.

## Relationship evidence (`has_evidence`)

Never implemented: `register_term` has no per-relation evidence, the prompt does not ask for it, `validate_and_fix_term`
rebuilds relations without it, and `TermStore.upsert` drops a new relation's evidence. Design:

- Prompt and tool: each relation becomes `{relation, related_term, evidence}` where `evidence` is the sentence that
  states the relation; the tool grounds it with the locator, requiring the subject mention and the object mention in
  the sentence (or in two adjacent sentences of the same page). Not found: rejected and counted; a `located` fallback
  looks for a sentence in the page containing both mentions.
- Storage: `RelationRecord.evidence` becomes a list of `{text, source_paper, page, kind}`; `validate_and_fix_term` and
  `upsert` keep and union it.
- Edges: a new additive field `evidence: [{text, source_paper, page, kind}]`. `has_evidence` stays as today's
  `"; "`-joined string of the texts for every reader that exists (`retrieval_agent`, `kg_rag_api`, `kg_update`,
  `releases.py`, disclosure), and `kg_to_terms` reads the structured list first (its `"; "` split breaks a sentence that
  contains `"; "`). `releases.py:970` expects a list of mappings; the structured field matches that shape.
- Coverage for relations will stay well below that for terms (both ends must be found, and a relation inferred across
  pages has no sentence). This is reported, not hidden.

## d. Coverage per run

Counters added to the run summary (ints ride through the existing `counters` channel; per-paper figures through the
per-paper unit result): `terms_total`, `terms_with_passage`, `relations_total`, `relations_with_passage`,
`passages_quoted`, `passages_located`, `passages_rejected_not_found`, and per paper the same four counts. Shown on the
Extract page's run view (next to KG growth) and as one plain log line per paper plus a summary line. **Warning
threshold (proposed):** a warning in the log and on the page when terms with a passage are below 90% of terms, or
relations with evidence below 50% of relations, or any paper below 80% of its terms; the figures above justify 90% for
terms (90.5% of names are findable, 84% of model calls carry a sentence, and the located fallback covers most of the
rest), the relation figure is a first guess to revisit after the first real runs. The same counts are written into the
bundle manifest (`passages` block) so a KG's coverage is visible later.

## e. Tests

- Real extraction in the suite: the real `Orchestrator` and LangGraph tool loop with a scripted model (the seam used by
  `tests/extraction/test_passage_capture_real_path.py`) over a generated 3-paper PDF set with known sentences; the test
  fails when term coverage is below 90% or relation coverage below 50%, or when any stored passage is not found in its
  page.
- Unit tests: locator (every variant above, plus the short-acronym and `(ABBR)` rules), quality rules on the real-paper
  examples, grounding (a rejected model sentence is counted and not stored; a located fallback is marked `located`),
  merging at store and build (counts before and after, per paper), relation evidence round trip through
  `kg_to_terms`.

## f. Recovery for existing KGs (no model calls)

A worker job `library_passages` (capability `library.extract`, `:local`): for one bundle, under the bundle lock, for
each term and relationship without evidence, read every page of each of the term's source papers from the library (by
file name, tie broken by the manifest sha256; an ambiguous name is reported, never guessed), run the locator and the
quality rules, attach the best passages as `located`, then rewrite `terms.json` and rebuild `kg.json` atomically.
Started from the Extract page: a **Find supporting text** button on the existing-KG picker, with progress per paper and
the coverage block at the end; resumable and cancellable like an extraction.

What it recovers: on the shipped data about 90% of terms (name findable in own papers, 91.9% with variants). What it
cannot: terms the model rewrote (`electron-doped high-temperature superconductor`, LaTeX names, `1/C2-V Mott-Schottky
plots`), scanned PDFs without a text layer (reported as such), terms whose mention is only in headers or references,
and relations whose two ends never share a sentence. Cost on this machine for a 2,000-term bundle of 40 papers x 15
pages: about 17 s text extraction, 8 s normalising, about 10 s locating and ranking: under a minute. Pages are empty on
`tomo_test` and `scattering_test`, so every page of each source paper is searched (one pass with a pre-built
multi-pattern index per page set; never all pages of all papers per term).

## g. Privacy and limits

Unchanged: passages are shown only with `F2W_DISCLOSE_SNIPPETS=1`; the projection caps stay (20 per paper, 1,200 chars
each, 100 per node, 6,000 chars per paper); the same scrub applies to located passages. Stored-but-not-disclosed
passages are counted, not shown: `snippet_count` per paper on the node record and the coverage counts on the run are
visible without the opt-in; the text is not. Storage keeps no cap (the stored data is evidence); because the caps cut
in stored order, the best passage is stored first. Publication rules R3-R5 scan every string including passages
(secret shapes, absolute paths, signed URLs): a verbatim sentence can trip them, which is a refusal to publish, never
a loss of the KG; R6 (snippet volume) is informational.

## h. Data changes and deployment

Additive only: `ContextSnippet.page` may be `null` (done); a snippet `kind` (`quoted`|`located`) and a `verified`
flag; `RelationRecord.evidence` entries become mappings; edges gain `evidence` (list) beside the unchanged
`has_evidence` string; the bundle manifest gains `passages` (coverage) and the run summary gains the counters.
Existing bundles and shipped KGs load unchanged (absent fields read as empty). No env var, mount, profile or image
change; the recovery job runs on the existing worker and reads only the library mount. Tiled stores whole node and edge
dicts as properties, so passages and evidence reach Tiled as they do today; adoption and release validation pass
through (inference from reading, not a test).

## Effort and commit order

About 10 working days. (1) locator and quality rules as one module with the real-paper tests; (2) extraction-time
grounding: verify the model's sentence, located fallback, rejected counts, remove the old fallback; (3) relationship
evidence through prompt, tool, store, build and `kg_to_terms`; (4) coverage counters, log lines, warning threshold and
the Extract page block; (5) the recovery job and its button; (6) the deterministic real-extraction suite test with the
thresholds; (7) docs (statuses, coverage, recovery, known issues). Stop for review after this page.

## Not verified

Clause-window trimming (proposed only); the caption and Methods rules; short-acronym false positives across the corpus;
R3-R5 hit rate on real passages; Tiled property size limits for larger evidence; that the pilot's pypdfium2 version
extracts the same text as this machine.
