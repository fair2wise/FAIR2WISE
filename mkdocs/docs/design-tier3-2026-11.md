# Design only, for the owner's review (backlog Tier 3, 2026-11)

Nothing on this page is built. Each section ends with the questions the owner has to answer before anything is.

## H. Numeric and aggregate questions over the KG

**The question.** "What is the largest measured length recorded in the KG?" and "all species with 2n = 52" are answered
today by the chat model reading node names and definitions in the retrieved context. A value in a name (`530 mm`) is text to
it; an aggregate over the graph is beyond what the context window holds. On the plecos KG the owner got the right answer
once and a refusal twice, which is the expected behaviour of that path.

**What exists.** A node already has `properties`: `PropertyRecord(property, value, unit, context, uncertainty, verified)`,
written by the property pass of the extractor (`extract_and_attach_properties`, regular expressions for value and unit around
a material name) and now also by a value-type slot of a user schema (`country: Brazil`, `length_mm: 530`). The KG node
carries the list; the lexical index of retrieval does not read it; the card shows it.

**Proposal.**

1. *Storage.* Keep `properties` as the one place for measurements and attributes, and type it: `value` stays what was
   written, plus `value_num` (a float parsed from the text when it is a number, a range's midpoint when it is a range with
   `value_min`/`value_max`), `unit` normalised to one spelling per quantity (a small table per unit *kind*: length, mass,
   temperature, count, fraction; the table is units, not a domain word list), and `quantity` (the slot or property name the
   schema gave). A user schema declares a quantity by giving the slot a numeric range (`integer`, `float`) and, optionally,
   a `unit` annotation; the extractor then stores `value_num` and refuses a value it cannot parse as a number (kept as text
   with `value_num: null`, counted).
2. *Index.* At KG build, a `measurements` table next to the graph (SQLite under the bundle, `measurements.sqlite`:
   node id, quantity, value_num, unit, source paper, page, passage text) so an aggregate is one SQL query, never a scan of
   JSON. For a Tiled-published KG the same rows live in the node properties (already the case) and the table is rebuilt
   from them on read.
3. *Query.* A deterministic question class before the chat model: a question that names a quantity the KG has and one of
   `largest|smallest|highest|lowest|max|min|all ... with|equal to|above|below|between` is answered by the table (the row, its
   node, its passage, the paper and page), with the model asked only to word the answer from the rows handed to it. The
   quantity names come from the selected KG's own schema and property names, not from a list in the app. A question that
   names no known quantity goes the usual way.
4. *Card and chat.* The answer cites the node and the passage; the card's property list gains the unit-normalised value and
   a "from table row" or "from sentence" mark (the passage kind).

**Cost.** About four days: typing and parsing (one day), the table and its rebuild (one day), the question class and the
answer path with tests on the plecos and the RSoXS data (two days).

**Questions.** (1) Is a per-quantity unit table acceptable (it is a list of units, not of domain terms)? (2) Should the
question class run before the model on every turn, or only when the retrieval judge says "numeric question" (it already
classes questions as numeric or conceptual)? (3) For a shipped KG whose properties came from the regex pass, is a
re-index without re-extraction wanted, or only new extractions?

## I. A second model pass for relations without located evidence

**Where it stands.** Located-only relation evidence covers 44% of relations on the two measured papers and 24% on the
plecos KG (5% on the monograph, 11% on the review). The one-run measurement of asking the model for evidence in the main
prompt (55 calls, `lbl/cborg-chat`) cut relations by 31%, raised requests by 27%, gave 89% located sentences of which 41%
named both ends and 32% were copies of the term's context sentence; the owner decided against changing the main prompt.

**Proposal.** A separate pass, after extraction or as a job beside Find supporting text, only for relations that have no
located evidence after the located search:

- Per paper, the relations without evidence are grouped by page (the subject term's page; a relation whose ends are on
  different pages is skipped and counted). One call per page with: the page text (data), the list of relations
  `subject -predicate-> object` (at most 20 per call), and the instruction to copy, for each, one sentence of this page that
  names both ends, or `null`. No new relation may be proposed; a sentence not found verbatim or by the tolerant match on
  that page is rejected and counted; a sentence that does not name both ends is rejected. Accepted sentences are stored as
  `kind: quoted`, `verified: true` on the relation, unioned as today.
- Budget: one call per (paper, page) with open relations, so at most pages per paper; the owner states a cap per job and the
  job stops at it. On the plecos KG (9 papers, about 150 body pages) that is about 150 calls for the whole KG, about 15 per
  paper; the free model only.
- Measurement first: on the two measured papers the ceiling is about 58% of relations (both ends in one sentence); the pass
  can only reach that. Expected cost to measure: 27 pages, 27 calls, one run, cap 30.

**Questions.** (1) Is a per-page cap of one call and a per-job cap the right shape of the budget? (2) Should the pass be a
button beside Find supporting text (explicit, with the cap shown) or a step of Find supporting text (which is model-free
today and should perhaps stay so)? (3) Is 58% worth 150 calls on a KG of this size?

## J. bioRxiv and other sources that need no new egress host

bioRxiv has no keyword search of its own, and the plan recorded in the Library page is to search it through OpenAlex with
`filter=primary_location.source.id:<id>`. Looked up for this page (one unauthenticated OpenAlex call, no model):
`GET /sources?search=biorxiv` names **S4306402567** ("bioRxiv (Cold Spring Harbor Laboratory)") as the source; medRxiv is
S3005729997 and Research Square S4306525896 the same way. All three are reached through `api.openalex.org`, already an
allowed host; their PDFs are on `biorxiv.org`, `medrxiv.org` and `assets.researchsquare.com`, which the download policy
must allow (`biorxiv.org` and `medrxiv.org` are already in the preferred-host ranking of the download agent; the egress
allowlist of a deployment is the owner's). Proposal: a `sources` entry `biorxiv` in the Harvester form that runs the OpenAlex
search with that filter and the same ranking, filing under `source: biorxiv`; a preprint found this way and later published
is de-duplicated by DOI as today (the preprint DOI differs from the journal DOI, so it is not: a `related_works` check on
the OpenAlex record would be the fix, one more field in `_OPENALEX_FIELDS`). Effort about a day with tests.

**Questions.** (1) Are preprints wanted in the library by default or only when the form asks for them? (2) Should a
preprint that OpenAlex links to a published version be skipped when the published version is already filed?

## K. Extraction speed: where the 35 minutes go

The plecos extraction (9 papers, 2,579 entities, 6,894 relations, `lbl/cborg-chat`) took 34 min 47 s, about 232 s per paper.

**Measured locally (no model).** The real extractor over six generated pages with a scripted model, under cProfile:
0.39 s in all, of which aliases 0.05 s, the terms.json save 0.03 s, publication metadata 0.03 s, grounding 0.006 s, relation
evidence 0.004 s, the property pass 0.003 s, PDF read 0.001 s. Per page that is under 70 ms. Everything that is not the model
is noise.

**Measured on the model (the relation-evidence run, same extractor, `lbl/cborg-chat`, one worker).** 27 pages of two real
papers took 287 s: 10.6 s per page, 189 raw chat requests, so **7 requests per page** at about 1.5 s each. The page prompt
is the schema text (about 12,000 characters for the shipped schema, 6,000 to 12,000 for a user schema) plus the page; the
agent loop sends the whole conversation again on every turn, so a page with k tool-call turns sends the schema k times.

**Where the time goes, then.** Model latency times the number of turns, serialised by the admission cap:
`CBORG_MAX_CONCURRENCY` (default 5) bounds the calls in flight across the process; papers run one after another and the
pages of one paper in parallel up to `max_workers` (8), so at most 5 pages are in flight. On the plecos papers (long, text
heavy) 232 s per paper against 10.6 s per page says about 22 page-equivalents per paper at the measured rate, which is the
page count of those papers.

**What to change, in order of return.**

1. **Fewer turns per page.** The loop's second message ("done") after the tool calls, and the re-sent schema on every turn,
   are most of the 7 requests. Asking for all `register_term` calls in one turn (already what the prompt says) and
   finishing the page without a closing turn when the last message held tool calls saves one to two turns per page: 15 to
   30%. A one-line change in the agent graph, measurable on the suite with no model.
2. **Shorter prompts per turn.** Send the schema once (the first turn) and a reference to it on the repair turns is not
   possible with a stateless API; what is possible is the bounded schema for shipped schemas too (12,000 characters is the
   budget already used for user schemas on CBORG): fewer input tokens per turn, the same answers in the earlier
   measurement. Needs the owner's yes, since the shipped prompt is pinned.
3. **More pages in flight.** `CBORG_MAX_CONCURRENCY` 5 to 8 and papers overlapping (two papers at a time when each is
   short) raise throughput as long as the provider's rate limit allows; a measurement on the pilot of three settings (5, 8,
   12) with the same two papers would show the knee. No code change for the first; the paper overlap is a small change in
   the extraction stage.
4. **Skip pages that cannot hold terms.** Reference lists and plate pages are extracted like any page today; the locator
   already knows where the references start (`references_start`) and the blank pages (under 10 words). Skipping them is
   free and removes 10 to 20% of the pages of a typical paper.
5. **Do not re-extract.** The library already skips a paper extracted into the bundle with the same bytes and model; the
   same key could skip an unchanged page on a re-run of a failed paper.

A measured plan, if the owner wants numbers before a change: run items 1 and 4 on the two measured papers (27 pages, cap 60
calls in all, both arms) and report seconds and requests per page against the 10.6 s / 7 baseline.

## L. Rebuilding the shipped RSoXS corpus (`matkg_rsoxs_v1.json`) with the fixed builder

Report only; the shipped file was not changed. The rebuild ran on 2026-11-25 with `convert_terms_to_graph(...,
merge_existing=True)` from the current `forge_merge` tree over the tracked terms file
`storage/terminology/extracted_terms_rsoxs_v1.json` (73 MB, 24,681 terms, 946 papers, extracted 2026-09-08) into the
scratchpad; 11.3 s, no model call.

| | shipped `matkg_rsoxs_v1.json` | rebuilt |
|---|---|---|
| nodes / edges | 7,022 / 13,848 | 29,561 / 81,958 |
| nodes with passages (passages) | 60 (71) | 914 (1,636) |
| nodes with properties | 21 | 608 |
| nodes with publications | 3,123 | 24,293 |
| stub nodes (no source paper) | 3,899 | 5,268 |
| categories | 26 | 26 |
| relations with evidence / aliases | 0 / 0 | 0 / 0 (the terms file predates both) |
| file size | 9.4 MB | 75.7 MB |

Every node id of the shipped file is in the rebuilt one (0 only in shipped); 22,539 node ids and 68,141 edges exist only
in the rebuilt graph; 31 shipped edges are not reproduced (predicate or object changed by the current builder's relation
normalisation). The reason for the 4x is not the builder: the shipped graph was committed (0d3d294b) from an earlier,
smaller terms file, and the terms file now tracked (7e3876ac) is the full 946-paper harvest. The rebuilt graph also shows
what the full harvest carries: 12 ids of the shape `matkg:-08V` in the shipped file and many more in the rebuilt one
(values registered as terms), 5,268 stubs, and 608 nodes with regex properties.

**Recommendation.** Do not swap the shipped file. A rebuild from the tracked terms file is a different release (eight
times the size, 75 MB in the image, the chat retrieval and the Tiled publish both sized on the 7,022-node graph), and the
release contract (`docs/migration/data_releases/release_contract.md`) wants a manifest entry, the R1-R7 checks and an
owner decision for a new KG release. If the owner wants the larger graph, the path is: a trim pass first (the "KG trim"
item of the standalone plan: drop value-shaped ids and single-paper stubs, which removes most of the 22,539 new ids), then
`matkg_rsoxs_v2`-style promotion through the release contract, not a replacement of v1. The rebuilt file and the
comparison script stay in the scratchpad of this session and are not committed.

**Questions.** (1) Is a trimmed rebuild wanted as a new release, or does v1 stay the shipped default? (2) If a new release:
which trim rules (value-shaped ids, stubs with no paper, minimum paper count)?

## M. Standalone install, the rest of section 7

Items 1 and 2 of the plan are done (`.env.example`, `f2w-doctor`). What changed since the plan: the library now has user
schemas, bundles with a previous generation and a recovery state, the proxy prefix, the publication records and the
relation repair; a first start needs the workdir layout for all of them, and a seeded library is the natural demo.

Proposal for the owner to pick from:

- **3. `f2w-init`** (agent side, no gated path): creates `<F2W_WORKDIR>/{kgs,schemas,library,tmp,kg_releases}` with the
  modes the stores expect, writes the marker file with the image commit, and seeds: the shipped RSoXS KG adopted as a
  bundle (`continue` from the KG file), one example user schema (the catfish schema of the tests, saved as `user/example@v1`),
  and an empty library category. Idempotent; refuses to touch a non-empty workdir without `--force`. One command, tests,
  about a day. The entrypoint part (calling it on first start) is Deploy.
- **4. Compose file** (Deploy, owner-gated): one service for the API, one for the UI gateway, the mounted library and workdir
  as named volumes, `F2W_LIBRARY_PROXY_PREFIX` and the egress proxy as environment, no Tiled by default (the publish route
  refuses with the typed error, as now); a `tiled` profile adds the managed Tiled.
- **Publish route in standalone**: unchanged code; the standalone compose simply has no Tiled target, so Publish to Tiled
  shows the refusal; the install page says so and shows the `tiled` profile.
- **5. KG trim** and **6. IPv6 egress**: unchanged from the plan; owner calls.

**Questions.** (1) Should `f2w-init` seed the RSoXS KG, the example schema, both or neither? (2) Is the first-start seeding
to run from the entrypoint (Deploy) or stay a documented command?
