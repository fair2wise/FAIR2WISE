# Design: supporting passages for every term and relationship

Status (2026-11-24): built on branch `passages`: the locator and quality rules, grounding at extraction time (b), per-run
coverage counters and the manifest `passages` block (d), and the recovery job with its route, button, run-view coverage and
the per-paper note (f), and relationship evidence, located only (the relationship evidence section; owner decision
2026-11-24: the extraction prompt is NOT changed). Not built: the deterministic 3-paper extraction suite test with
thresholds (e; the real-path and counters tests cover grounding and coverage) and the optional second model pass for
relations without evidence. The two fixes
earlier on `forge_merge` (page context survives the tool threads, a missing page never discards a passage; the
extracted-bundle KG build keeps the passages of a node that already existed) stay. Measurements come from the real RSoXS papers in `papers/` (334 PDFs,
6,229 pages) and the shipped terms file; items not verified are marked.

## Where we are

Status 2026-11-26: steps 1 to 7 of the plan are on `forge_merge` (locator and rules, grounding, located-only relation evidence,
coverage counters and the Extract page block, the recovery job with its button, the deterministic real-extraction suite test
with the 90/50/80 thresholds in `tests/extraction/test_passage_suite_thresholds.py`, these docs). Since then: aliases, the
table_row kind, short factual clauses, OCR-tolerant quotes, pages without a text layer, and the searched-and-empty record of
the recovery job (`recovery_state.json` per bundle, keyed by the paper's sha256 and the rules' revision). The measurements
below are the ones the plan started from.

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

## Relationship evidence (located only)

**Decision (owner, 2026-11-24): evidence is located, never asked of the model; the extraction prompt is not changed.** A
prompt variant that asked the model for one evidence sentence per relation was measured and rejected.

*The measurement* (one run, arm A the unchanged prompt run once, arm B the prompt with the extra bullet; free model
`lbl/cborg-chat`, two papers of 10 and 17 pages, 55 model calls against a cap of 60): relations passed to `register_term`
fell by 31% (612 to 421; 485 to 343 distinct), model requests rose by 27% (189 to 241); 89% of arm B's relations carried an
evidence sentence that could be located on the page, but only 41% of the evidence named both endpoints, 32% repeated the
term's own context sentence, and the ceiling (the two endpoints co-occurring in one sentence of any page) was about 58%.
It costs relations and still mostly returns sentences that do not state the relation, so the prompt stays as it is.

*What is built.* For each relation (subject, predicate, object) of a term the shared locator (`passages.locate`: squash
rules, plural tail, short terms, `(ABBR)` where the paper defines it) searches the source paper's pages for every sentence
that names **both** endpoints, or two adjacent sentences of one page that name one each (never when a sentence already
names both), and the same quality rules as for term passages apply (references, headers by shape, captions, fragments,
length; a sentence over 400 characters gives a clause window that must hold both mentions). A sentence that names only one
endpoint is not evidence, and the term's own context sentence counts only when it names both. Ranking: one-sentence
evidence before two-sentence windows, then 120-300 characters, definition words, mention nearest the sentence start, earlier
page. Every such sentence is kept, unioned and de-duplicated by (paper, page, text), with **no cap** in storage (caps apply
only when evidence is displayed). No model call.

- **Where.** At extraction time, after a term's relations are validated (`ToolState.attach_relation_evidence`, and the JSON
  fallback of the Orchestrator), using the paper's pages the tool already holds; and in the recovery job `library_passages`
  for existing KGs (the hook `recover_relation_evidence` is now real), per paper, pages read once, a relation that already has
  located evidence from the paper is left alone (idempotent), written atomically and resumable like the term part.
- **Storage.** `RelationRecord.evidence` entries are strings (older files) or mappings `{text, source_paper, page, kind:
  "located", verified: true}`. A string reads as `{text, kind: "unknown"}`: nothing is known about its paper, page or how it
  was found. A relation without evidence keeps its exact old shape (no `evidence` key). The store unions the evidence of a
  repeated relation instead of dropping it.
- **Edges.** The KG edge gains `evidence` (the mappings) beside the unchanged `has_evidence`, which is the text of the best
  (first) entry when there are mappings and the `"; "` join of the strings otherwise. A repeated edge keeps the union in the
  bundle (opt-in merge) build. `kg_to_terms` reads `evidence` first and falls back to splitting `has_evidence`. The frozen
  default build is byte-identical for inputs without evidence (`docs/migration/json_kg_contract.md` 2.6b).
- **Display.** The single-node record carries `extra_fields.relationship_evidence` (per relationship `evidence_count`,
  and the bounded sentences only with `F2W_DISCLOSE_SNIPPETS=1`, same caps and scrub as node passages). There is no edge
  detail screen, so the node card shows the evidence under each relationship of its relationship list.
- **Coverage.** Per paper and in total: `relations`, `relations_with_evidence`, `evidence_sentences`, `relations_without`
  (counted once per relation and paper) in the run summary, the terms file metadata, the manifest `passages` block and the
  recovery result; one info line per paper and a WARNING below 50% of the paper's relations.
- Coverage for relations is well below that for terms (both ends must be found in one place, and a relation inferred across
  pages has no sentence). It is reported, not hidden.

*Real-data coverage* (no model call; `relcov.py` in the session scratchpad; the logic is the one the job runs):

| Set | Relations | With a sentence naming both ends | Sentences stored | Time |
|---|---|---|---|---|
| The two measured papers (10 and 17 pages), relations of arm A, distinct per paper | 469 | 208 (44.3%) | 531 | 0.2 s per paper |
| 10 papers of `papers/`, relations of the shipped `extracted_terms_rsoxs_v1.json` terms that cite them | 69,453 | 8,255 (11.9%) | 19,882 | 106 s in all, 10.6 s per paper (31 to 110 pages, 3,500 to 11,000 relations each) |

The second set is a lower bound: the shipped terms carry the relations of every paper that mentioned the term, so most of a
term's relations do not belong to the paper being searched (the object name is absent in 44,049 of the 61,198 without), and
the first set is the honest one. Why the 261 relations of the first set have no sentence: object name not found 60 and
subject name not found 42 (alias or paraphrase), neither found 18; both named on one page but never in one or two adjacent
sentences 67, on different pages 12; two adjacent sentences over 400 characters 17; only in a figure caption 13, a sentence
rejected for a missing final punctuation 15 or a header shape 8, the reference list 3, one table fragment, 3 sentences over
400 characters with the endpoints too far apart. Examples (relation, why): `J61:m-ITIC bulk heterojunction` -> `J61`
(subject written another way in the paper); `power conversion efficiency` -> `approached 10% in highly efficient OPV devices`
(the object is a clause, never a name in the text); `selective excitation wavelength` -> `S1 excited state` (neither is
named as such); `FRET` -> `organic solar cells` (only in the reference list); `electron-only device` -> `DIO` and
`electron-only device` -> `disorder energy` (only in figure captions); `WAXS` -> `coherence length` and `aggregation` ->
`PTB7:PC71BM blend` (same page, different sentences); `temperature-dependent mobility` -> `transistor configuration` (two
adjacent sentences, 400 characters exceeded); `PTB7:PC71BM blend` -> `PTB7` and `DIO` -> `organic photovoltaics` (only in
a sentence cut by a page break or a line without final punctuation). The `tomo_test` and `scattering_test` bundles exist on
the pilot only and are to be measured there through the recovery job.

*Optional second pass (not built).* If located coverage turns out too low, a separate model pass can ask for evidence only for
the relations that have none, so it cannot reduce what the main extraction produces (the measured failure of the prompt
change). It would reuse the same grounding: a returned sentence is kept only when the locator finds it in the paper and it
names both endpoints.

## d. Coverage per run

Counters added to the run summary (ints ride through the existing `counters` channel; per-paper figures through the
per-paper unit result): `terms_total`, `terms_with_passage`, `relations_total`, `relations_with_passage`,
`passages_quoted`, `passages_located`, `passages_rejected_not_found`, and per paper the same four counts. Shown on the
Extract page's run view (next to KG growth) and as one plain log line per paper plus a summary line. **Warning
threshold (proposed):** a warning in the log and on the page when terms with a passage are below 90% of terms, or
relations with evidence below 50% of relations (built: a log warning per paper and a warning line on the run view), or any paper below 80% of its terms; the figures above justify 90% for
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
of the Extract page. Relations are processed too (see the relationship evidence section): per paper the result adds
`relations`, `relations_with_evidence_before`, `evidence_added`, `relations_newly_covered` and `relations_still_without`.

**Cost, measured on this machine** (6 real papers of `papers/`, copied; 2,296 terms of the shipped RSoXS terms file that cite
them, 2,497 term-paper pairs, 6 to 36 pages each). Terms only (before relation evidence): 8.5 s for the whole job (1.4 s per
paper). With relation evidence, on the same data (the shipped terms carry tens of thousands of corpus-merged relations,
so this is a worst case): 40.3 s, 6.7 s per paper, PDF read, locate and rewrite of `terms.json` included; the second run,
which adds nothing, 29.4 s (the relations without a sentence are searched again; the terms are not). Located term passages:
1,912 of the 2,497 term-paper pairs (76.6%; 1,910 with the earlier institution-word rule: 22 candidate sentences of these
six papers, every one a complete sentence with a verb, such as "A new in-vacuum spectrometer has been installed on beamline
X1A2 at the NSLS, Brookhaven National Laboratory.", were rejected as affiliations; only 2 term-paper pairs depended on
them); the rest are terms whose name does not appear in the paper in a sentence that passes the quality rules (the earlier
estimate of about 90% counted any mention, including headers, references and fragments).

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
ends never share a sentence or two adjacent sentences (relations). A mention in a sentence over 400 characters is stored as a
clause window, not the full sentence.

Started from the Extract page: a **Find supporting text** button on the existing-KG picker, with progress per paper, cancel
and the coverage block at the end.

## Aliases (other names a paper gives a term)

No common-name map exists in the app (owner decision 2026-11-25: the `pleco -> Loricariidae` table was removed). A term's
other names come from the text, by shape only: `term (X)` and `X (term)` (an abbreviation, a common name, a synonym in
brackets), `term, also known as X`, `X, also called term`, `term (hereafter X)`; a bracketed year, a citation, a figure or
table reference, a URL and a one- or two-letter token are never aliases (`passages.aliases_in_text`). The extractor's
`register_term` also takes an optional `aliases` list from the model. Aliases are unioned per term (case-insensitive, never
the term itself) at extraction and by **Find supporting text** over the whole paper (`aliases_added` per paper), the KG node
carries `aliases` only when it has any (the frozen default output is unchanged), `kg_to_terms` keeps them, and retrieval
resolves an alias like a name (exact canonical lookup and the lexical text). When a question still finds nothing, the chat
asks the model once for up to five other names the subject may appear under (shown the KG's own most frequent words, not a
word list) and asks the KG again before any paper is offered (`retrieval_retry` event, `alternatives_tried` on the verdict).

## Per-paper note for older KGs

A KG extracted before supporting text was recorded would show nothing on its cards. For a registered bundle the node record
sets `passages_recorded: false` on each publication (an untyped key; the typed wire model is unchanged) that has no
passages on the node and no entry in the manifest `passages` block, and the card says "No supporting text was recorded for
this paper. Use Find supporting text on the Extract page to add it." It never says to extract again. The key is stripped
before bookmarks and node edits. Continuing a KG (`BundleStore.fork`) copies the parent's manifest `passages` entries into the child (an independent copy, which the child's own extractions sum onto, so a paper the parent covered has no note on the child); the parent's `recovery` block is not copied, because it describes a run of the parent. A plain KG file has no manifest, so continuing from one starts without a block.

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
evidence, located only, through tool, store, build, `kg_to_terms` and the recovery job (built); (4) coverage counters, log lines, warning threshold and
the Extract page block (built); (5) the recovery job and its button (built); (6) the deterministic real-extraction suite test with the
thresholds (built: 90% of terms with a passage, 50% of relations with a sentence naming both ends, 80% of stored passages quoted
and verified, every stored passage found in its page); (7) docs (this page). All seven steps are done.

## Table-heavy and scanned papers (measured 2026-11-25, proposals not built)

The plecos run (9 harvested papers, 2,935 terms) had 68% of terms with supporting text overall, six papers at 86 to 93%, the
1904 Regan monograph at 54% (1,215 sentences rejected) and the 2024 Loricariidae cytogenetics review at 38% (429 rejected);
relations with a sentence naming both ends were 24% overall, 5% on the monograph and 11% on the review. The two papers were
fetched again (open access, DOIs 10.1111/j.1096-3642.1904.tb00040.x and 10.1590/1982-0224-2024-0050) and profiled with the
passage rules, no model. The pilot's own term lists were not available, so the counts below are of the **text**, not of the
terms; the rejected-sentence sample needs the next run (the extractor now logs it, see the end of this section).

| | 1904 monograph | 2024 review |
|---|---|---|
| Pages, words | 173 pages, 63,487 words | 36 pages, 20,145 words |
| Text layer | present (OCR); 33 plate pages under 50 words; 74% of 4-letter words are dictionary words, with OCR slips such as `afinis`, `Chretostomvs`, `vertebrse` | born digital, clean |
| Where the facts are | running prose of species descriptions in telegraphic style | Table 1 on pages 6 to 21: 16 of the 23 body pages, 53% of all words, about 390 rows (species, locality with coordinates, 2n, karyotype formula, citation); references start on page 24 |
| Sentence spans | 8,520; **6,219 (73%) rejected as fragments**, 6,207 of them for having fewer than 5 words of 3+ letters; 2,215 accepted (26%) | 1,002; 237 accepted (24%); 325 fragments, 281 header or boilerplate (the page header glued into the table), 85 starting mid-sentence |
| Samples of what is rejected | `Total length 50 mm.` `32-34 scutes in a longitudinal series.` `H. acipenserinus Kner.` `Plecostomus afinis Steind.` `255 -- auomalus ....` | `"Pão" Xingu River, Belo Monte hydroeletric plant 3°06'12.8"S 51°43'53.9"W 52♀♂ 94 40m/sm + 12st/a Almeida et al. (2023)` and `8/36 Neotropical Ichthyology, 22(4):e240050, 2024 ... TABLE 1 | (Continued) Subfami...` |

What this says:

- **The review is a table paper.** Its facts are table rows, which the PDF reader flattens into one line per row with the page
  header glued in. A model asked for a sentence writes one from the row (`X has 2n = 54, 40 m/sm + 12 st/a`), which is not in
  the text, so it is rejected; the located fallback looks for a sentence that mentions the term and finds only rows the rules
  call fragments or headers. Both routes fail for the same reason, which is the honest reason: the evidence is a row.
- **The monograph is not a poor text layer so much as telegraphic prose.** The OCR is imperfect but readable. The rule that
  rejects a sentence with fewer than five words of three letters throws away real measurements (`Total length 50 mm.`) and
  keeps only the long prose. Rejected model sentences there are probably near matches that fail on OCR slips; the next run
  confirms or refutes it.
- Relation coverage follows: a relation needs one sentence (or two adjacent ones) naming both ends, and a row or a telegraphic
  clause rarely names two terms in a sentence the rules accept.

What was built (owner approval 2026-11-25, in this order):

1. **Short factual clauses** (`passages.factual_clause`): a sentence of fewer than five words stands as a passage when it has
   at least two words, a number followed by a unit-shaped token (`50 mm`, `2n`, `150 °C`) or a numeric range before a noun
   (`32-34 scutes`), at most one comma, no dotted leader or dash run, and is not a citation or header. There is no list of
   units or nouns, only the shape. `Total length 50 mm.` and `32-34 scutes in a longitudinal series.` are accepted;
   `Plecostomus affinis Steind.` and `255 -- anomalus ....` are not. Such a passage is also exempt from the 120-character
   minimum.
2. **OCR-tolerant quote matching** (`passages.find_sentence_tolerant`): when the exact (squash) match of a model sentence
   fails, the sentence's words of three letters or more must appear in order on one page, at least 90% of them, each within
   three words of the previous; the stored passage is then the **page's own text** from the first matched word to the last,
   kind `quoted`, verified. A sentence with fewer than six such words is never matched this way.
3. **`table_row` evidence** (`passages.table_like_page`, `table_row_passage`): on a page where at least 30% of the lines with
   three tokens or more are mostly numeric cells, a mention whose sentence the rules reject as a fragment, header or
   unterminated line yields the physical line of the mention with the running page header cut off, prefixed by the nearest
   preceding header line (three or more tokens, at most 10% numeric, after the nearest `Table n` line): `header | row`, kind
   `table_row`, verified. A sentence still ranks above a row. The coverage counts a row as located; the card labels it
   **table row**. Relation evidence over rows is not built (the relation rules are unchanged).
4. **Pages with no text layer** are reported per paper by Find supporting text (`pages`, `pages_without_text` in the recovery
   result and the per-paper table) and in the extraction log; a page with fewer than ten words counts. No OCR pass is run
   (it would need the Docling OCR models in the image, which was not checked; decision deferred to the owner).

Instrumentation added with this section (diagnostic, changes nothing stored): each rejected model sentence is classified as
`near_match` (the words are on a page; paraphrase, OCR slip or reordering), `table_row`, `partial_overlap` (words from several
lines) or `not_in_paper` (the model's own words), counted per paper, and 20 samples (at most 6 per class, 300 characters) are
written to the run log at the end of an extraction, with the counts by class. Run the plecos extraction again, or only the
two papers, to read it.

## Tiled property size: measured (2026-11-26, local throwaway Tiled 0.2.18)

One node written with a 200 KB `context_snippets` property: 19 ms. `read_state` (the complete scan the publication plans
from) over 101 nodes (one of 200 KB, 100 of 3 KB): 29 ms; after 100 more nodes of 20 KB each: 62 ms; the double read 128 ms.
No limit was reached and the scan time stays well under a second at these sizes, so evidence is kept whole in storage, as
decided; the per-page payload of the scan grows with the evidence bytes and is the thing to watch on a KG with tens of
thousands of nodes (the owner's `read_state` timing on the pilot before and after step 5 is still the number that counts).

## Not verified

Clause-window trimming (proposed only); the caption and Methods rules; short-acronym false positives across the corpus;
R3-R5 hit rate on real passages; Tiled property size limits for larger evidence; that the pilot's pypdfium2 version
extracts the same text as this machine.
