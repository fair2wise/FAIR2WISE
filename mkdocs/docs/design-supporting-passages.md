# Design: supporting passages for every term and relationship

Status (2026-11-24): built on branch `passages`: the locator and quality rules, grounding at extraction time (b), per-run
coverage counters and the manifest `passages` block (d), and the recovery job with its route, button, run-view coverage and
the per-paper note (f). Not built: relationship evidence (the `has_evidence` section) and the deterministic 3-paper
extraction suite test with thresholds (e; the real-path and counters tests cover grounding and coverage). The two fixes
earlier on `forge_merge` (page context survives the tool threads, a missing page never discards a passage; the
extracted-bundle KG build keeps the passages of a node that already existed) stay. Measurements come from the real RSoXS papers in `papers/` (334 PDFs,
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
   (by shape, not by a bare institution word: e-mail or URL or DOI, author markers, long comma lists, a verbless
   fragment of institution names or a postal address, copyright/received/accepted/license, journal name lines; a complete
   sentence with a verb that names a national laboratory or facility is kept); citation entries (year + volume + page range, author-initial lists); equations and fragments (fewer than 5
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
| Nature Communications | rejected: 13 header/boilerplate and 7 reference hits |
| synchrotron | accepted, p7: the Methods sentence "GIWAXS measurements were performed at the 11-BM ... beamline of the National Synchrotron Light Source II" (the earlier `National` word rule rejected it; owner decision 2026-11-24); 2 further hits in the reference list are rejected |
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

**Built.** A worker job `library_passages` (capability `library.extract`, `:local` only; module
`app/modules/library/passages_job.py`, route `POST /v1/library/kgs/{name}/passages`, policy V99). For one bundle, under the
bundle lock (the extraction lock: a running extraction or a second recovery of the bundle refuses the start with a typed
error), one unit of work per source paper cited by the bundle's terms, so progress, cancel and resume work as for an
extraction:

1. The paper is found in the library by file name. A name two files share is settled by the manifest's sha256 of the
   extracted bytes; if that does not settle it the paper is reported `ambiguous_name` and skipped, never guessed. A name
   not in the library is `missing`, a PDF with fewer than 20 words of text `no_text_layer`, one that cannot be opened
   `unreadable`; each is reported per paper.
2. Every page is read once (`passages.page_texts`, the extractor's reader) and the locator and quality rules run over the pages
   for all of the paper's terms (`passages.best_passages`; never all pages of all papers per term). A term that has no passage
   from this paper gets up to 2 passages, appended as `kind: located`, `verified: true`. Existing passages are never changed
   or reordered; the union key is (paper, page, text). A term that has passages from another paper but none from this one is
   searched too, because the per-paper note and the manifest coverage are per paper.
3. `terms.json` is rewritten after each paper and `kg.json` is rebuilt once at the end (also after a cancel), both through a
   temporary file next to the target and an atomic rename, so a failure leaves the old files; a rebuild that fails leaves
   the old KG and the next run only has to rebuild. Terms written before a kill are kept; a resumed job skips the papers
   that were finished.
4. The manifest gains a `passages[<file name>]` entry per processed paper (absolute counts when the KG had no entry, otherwise
   `located` and `with_passage` added and `uncovered` lowered) and a `recovery` block (last run, counts). That entry is what
   removes the per-paper "no supporting text was recorded" note on the node card; a paper skipped for a reason has no entry
   and keeps the note.
5. Idempotent: a second run adds nothing and says so (no file is rewritten when nothing was added).

The result is the unit results of the job, shown by `GET /v1/jobs/{id}/progress` as `summary.recovery` (`total` and per-paper
`{terms, with_passage_before, located_added, newly_covered, still_uncovered}` or `{reason, text}`), and as the coverage block
of the Extract page. Relationship evidence does not exist yet, so only terms are processed:
`passages_job.recover_relation_evidence` is the named hook for it and does nothing.

**Cost, measured on this machine** (6 real papers of `papers/`, copied; 2,296 terms of the shipped RSoXS terms file that cite
them, 2,497 term-paper pairs, 6 to 36 pages each): 8.5 s for the whole job (1.4 s per paper, PDF read, locate and rewrite of
`terms.json` included); the second run, which adds nothing, 1.9 s. Located passages were attached for 1,912 of the 2,497
term-paper pairs (76.6%; 1,910 with the earlier institution-word rule: 22 candidate sentences of these six papers, every one
a complete sentence with a verb, such as "A new in-vacuum spectrometer has been installed on beamline X1A2 at the NSLS,
Brookhaven National Laboratory.", were rejected as affiliations; only 2 term-paper pairs depended on them); the rest are terms whose name does not appear in the paper in a sentence that passes the quality
rules (the earlier estimate of about 90% counted any mention, including headers, references and fragments).

**Why the other 23% are not located** (the same six papers, 2,497 term-paper pairs, measured by running the locator and the
quality rules on every mention; each pair is counted once under the cause closest to acceptance: length, then fragment,
caption, header, references; script: `causes.py` in the session scratchpad, no model call):

| Cause | Old rule (`National`/institution words) | Shape rule (now) |
|---|---|---|
| Located | 1,910 (76.5%) | 1,912 (76.6%) |
| (a) name not found on any page | 122 | 122 |
| (b) found only in the reference list | 232 | 232 |
| (c) found only in header, affiliation or boilerplate lines | 43 | 39 |
| (d) found only in figure captions | 36 | 36 |
| (e) found only in sentences rejected as fragments, equations, table text or mid-sentence cuts (no final punctuation 48, equation or fragment 47, starts mid-sentence 30, citation entry 1) | 125 | 126 |
| (f) found only in sentences that stay under 120 characters even extended | 29 | 30 |
| (g) no text layer or unreadable | 0 | 0 |
| (h) anything else | 0 | 0 |

The `National` rule is almost no part of it: only 2 pairs became located because of the change (22 candidate sentences of
these papers, all with a verb, were rejected as affiliations before; most belong to terms that were located elsewhere). The
largest groups are the reference list (9.3%: terms whose only appearance is in a citation title), names not found (4.9%) and
fragments (5.0%). The 122 pairs of (a) split, by a rough check of the words of the name against the paper, into 18 where the
model wrote `long form (ABBR)` and the paper has only the abbreviation (`gate voltage (VGS)`, `short-circuit current density
(JSC)`, `internal charge transfer (ICT)`: the locator counts an abbreviation only where the paper defines it, so these would
need paper-level knowledge), 91 where every word of the name is in the paper but not adjacent or in another form (`thermal
annealing`, `e-beam evaporation`, `Ultraviolet-visible (UV-Vis) spectroscopy`, `fluorine (F)`: word order, inflection,
paraphrase), 6 where only some words are present (`threshold voltage`, `silicon nitride membranes`, `SmCTB phase`,
`glassy posaconazole small molecule films`) and 7 where no word of the name is (`Curing`, `passivation`, `Posaconazole`:
the model's own wording or a name the paper writes another way). Stems of words were compared crudely, so the split is
indicative; the total of (a) is exact.

**What it cannot recover:** terms the model rewrote (`electron-doped high-temperature superconductor`, LaTeX names, `1/C2-V
Mott-Schottky plots`), scanned PDFs without a text layer (reported as such), papers no longer in the library or whose name is
ambiguous (reported), terms whose only mentions are in headers, references or figure captions, and relations whose two
ends never share a sentence (relations are not processed yet). A mention in a sentence over 400 characters is stored as a
clause window, not the full sentence.

Started from the Extract page: a **Find supporting text** button on the existing-KG picker, with progress per paper, cancel
and the coverage block at the end.

## Per-paper note for older KGs

A KG extracted before supporting text was recorded would show nothing on its cards. For a registered bundle the node record
sets `passages_recorded: false` on each publication (an untyped key; the typed wire model is unchanged) that has no
passages on the node and no entry in the manifest `passages` block, and the card says "No supporting text was recorded for
this paper. Use Find supporting text on the Extract page to add it." It never says to extract again. The key is stripped
before bookmarks and node edits.

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
the Extract page block; (5) the recovery job and its button (built); (6) the deterministic real-extraction suite test with the
thresholds; (7) docs (statuses, coverage, recovery, known issues). Stop for review after this page.

## Not verified

Clause-window trimming (proposed only); the caption and Methods rules; short-acronym false positives across the corpus;
R3-R5 hit rate on real passages; Tiled property size limits for larger evidence; that the pilot's pypdfium2 version
extracts the same text as this machine.
