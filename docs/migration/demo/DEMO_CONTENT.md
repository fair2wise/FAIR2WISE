# Demo content, December 10-11 2026 (W4)

Companion to `DEMO_PLAN_2026-12.md`. It fixes WHAT the demo shows: the paper for the live extraction, the harvest
topic, the corpora to seed, a 15-minute script and the content risks. Nothing here changes code. Every number was
read from a file or computed read-only from the JSON KGs in this checkout (`forge_merge` at `a4054b0`); estimates are
marked **(estimate)**. No model was called and no network was used, so anything that needs arXiv or CBORG is an
estimate from older logs. Machine-readable copy: `demo_content.json` (same folder).

## 0. Decisions in one table

| Item | Recommendation |
|---|---|
| Live-extraction paper | `papers/rsoxs/2020/10.1107_s1600577520011066.pdf`: "Absolute intensity calibration for carbon-edge soft X-ray scattering", J. Synchrotron Rad. 2020, DOI 10.1107/S1600577520011066, 8 pages, 715 KB |
| Model | `cborg-chat` (free, LBL-hosted; the project default `lbl/cborg-chat`) |
| Extraction cap | The Extract tab has no page or chunk cap. The cap is the selection: exactly 1 paper of at most 10 pages. Plan 5 to 10 minutes **(estimate, see 1.3)**; start it by minute 2 |
| Harvest topic | `resonant soft x-ray scattering` (unquoted), arXiv only, Max downloads 8 |
| Seed corpora (published, known state) | `e2e_demo/v1` and `bl1101/v4`, as rehearsed |
| Corpus adopted and published live | `opv_demo/v1`, adopted from the shipped file `matkg_deepseek-r1_70b_100_20250929_004942.json` |
| QA questions target | `e2e_demo` (the live-extraction lineage) and `bl1101/v4` (beamline); `opv_demo` for materials questions |
| Edit target | `opv_demo` (a Material-class label and category fix); never re-publish it afterwards |

## 1. The live-extraction paper

### 1.1 How the five candidates were found and compared

All 539 PDFs under `papers/` were read locally with PyMuPDF (page count, size, characters per page, images, tables,
RSoXS terms). Keeping files of 5 to 10 pages with a real text layer and RSoXS content left several dozen; the five below are
the best by topic fit. None is a pilot paper (A = `10.1021/acs.macromol.5b00950`, B = `10.1038/s41598-018-31024-1`,
C = CyRSoXS preprint and published pair), so B4 labelling is unaffected. Text-layer check: every candidate has 3,700 to
5,300 characters per page and no garbled glyphs (0 replacement characters).

| # | Paper (DOI or arXiv id) | Pages | Size | Words | Figures / tables | Fit | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Absolute intensity calibration for carbon-edge soft X-ray scattering (10.1107/S1600577520011066, JSR 2020) | 8 | 715 KB | 6,748 | 10 figure captions, 0 tables | 38 "RSoXS" and 4 "11.0.1.2" mentions: a beamline 11.0.1.2 method paper | **Chosen**: smallest file, on topic, plain single-argument text, no tables to parse, references on page 8 |
| 2 | Aluminum oxide free-standing thin films to enable nitrogen edge soft x-ray scattering (10.1557/mrc.2018.195, MRS Commun. 2018) | 5 | 1.0 MB | 3,162 | 5 captions, 0 tables | 24 RSoXS mentions, no beamline number | Fastest (5 pages), but a materials-prep note: fewer facts to show. Best backup |
| 3 | Resonant soft x-ray scattering unravels the hierarchical morphology of nanostructured bulk heterojunction photovoltaic thin films (10.1103/PhysRevMaterials.2.066003) | 5 | 1.8 MB | 3,338 | 7 captions, 0 tables | 8 RSoXS mentions, title says it | Good story (OPV morphology, matches the OPV questions), no reference list in the 5 pages |
| 4 | Soft X-Ray Detection Down to the Nitrogen K-Edge Using a Pixelated iLGAD Sensor (arXiv 2610.42064v1) | 9 | 3.2 MB | 7,276 | 14 captions, 7 table regions | Detector paper; "soft x-ray" 36 times, no RSoXS | It is one of the 5 papers the Nov 4 harvest downloaded, so it can also arrive through the live harvest; 7 table regions make parsing slower and noisier |
| 5 | Resonant Soft X-ray Scattering of Polymers with a 2D Detector: Initial Results and System Developments at the Advanced Light Source (10.1088/1757-899X/14/1/012016, 2010) | 8 | 3.3 MB | 3,542 | 5 captions, 0 tables | 6 "11.0.1.2" mentions: history of the beamline | Lower text density (2,900 characters per page), 2010 vocabulary; a good second paper, not the first |

Docling: the application's PDF parser is Docling. **I could not run Docling here** (its pinned weights are not
present offline, and the pinned folder of another session was empty), so "parses cleanly" is shown by the PyMuPDF
checks above and by the H7 parity note (`reviews/H7/docling_parity.md`: 8 papers of 2 to 40 pages parsed, a 31-page
paper in about 13 s). The presenter should parse candidate 1 once on the Mac Studio during rehearsal 1.

Not found by DOI string in `matkg_rsoxs_v1` to `v4`, `matkg_bl1101_v4` or `matkg_with_code`: candidates 1 to 5 are new
to those graphs, so the extraction adds something. The PDF is not in the image (papers are never baked in): copy it to
the Mac and upload it by drag and drop in Library > Papers (category `demo_live`). Do not copy it into the repository.

### 1.2 Why page count is the cap

The Extract tab's estimate is pages x 1,200 input tokens plus pages x 400 output tokens (`library/estimate.py`, a
documented heuristic, not a measurement): 8 pages is about 12,800 tokens. For a free model the cost field is empty and
the "I confirm this cost estimate" box still has to be ticked. There is no setting for pages or chunks, so the demo
rule is: one paper, at most 10 pages. **Cancel only stops between PDFs** (`ui-extraction.md`), so a one-paper run cannot
be cut short: it keeps its worker slot until it ends. The worker runs 2 jobs at once (`F2W_WORKER_CONCURRENCY=2`).

### 1.3 How long does extraction take? What the files say

The `e2e_demo` extraction left no timing in this checkout: its job events live in the demo container's state, and
`papers/e2e_demo/manifest.json` still shows `ingestion.status: pending`, `extracted_at: null` for all five papers.
**Time of one paper in the e2e_demo run: unknown.** The closest measurements (all from `logs/`, all with the free
reasoning model `lbl/cborg-deepthought` and the same term-extraction orchestrator the Extract tab starts, none with
`cborg-chat`):

| Evidence | Value |
|---|---|
| One 6-page paper, 1 worker (`extraction_smoke_20260929_095324.log`) | first page at 09:53:58, last event 10:07:42: 13 min 44 s, about 2.3 min per page |
| 4-worker batch (`rsoxs_v1_extract_cont.log`, 44 papers) | median 424 s between paper starts, so about 28 min per paper per worker (estimate: interval x 4 workers; pages not logged) |
| Later batches (`extraction_remaining8_*`) | median 139 s between starts, but 970 "Agent failed" lines and multi-hour gaps (rate limits, a suspended machine): not usable as a speed |
| Labelling calls with `lbl/gpt-oss-120b`, 5 pages each (`reviews/H3/b4_c4_free_model_run.md`) | 25 calls, about 1,034 s, about 41 s per call (a different workload) |

**Estimate for 8 pages with `cborg-chat`: 5 to 10 minutes** (it does not reason before answering, so it should be a
few times faster than the 2.3 min per page above; low confidence, never measured). Rule: start by minute 2, show the
answer at minute 13 to 14, and treat 12 minutes of running as the point where the fallback is used. The first
rehearsal must time the real run and replace this estimate.

## 2. The harvest topic

### 2.1 What the code does with a topic (`library/harvest.py`)

The Harvester takes the topic, drops stop words and sends two arXiv queries: the whole phrase, then every term ANDed
(a 7-term topic therefore matches almost nothing). Each query asks for 50 records (`per_query` default 50; the form
does not expose it, only Max downloads 1 to 200, default 20) newest first. Papers are scored A (all terms in title or
abstract and half in the title), B (60 percent), C (never downloaded). Quoting a phrase keeps it whole **but then the
title test needs the exact phrase with spaces**: checked offline, the quoted form `"resonant soft x-ray scattering"` scores
the hyphenated title "Laser-driven resonant soft-X-ray scattering ..." as C (skipped), the unquoted form scores it A.
So **do not quote** the topic.

### 2.2 Topic and alternates

| Role | Topic (type it exactly) | Queries sent | Evidence it returns hits |
|---|---|---|---|
| Primary | `resonant soft x-ray scattering` | `all:"resonant soft x-ray scattering"` and `all:"resonant" AND all:"soft" AND all:"x-ray" AND all:"scattering"` | `papers/e2e_demo/manifest.json`: a harvest on 2026-11-04 filed 5 arXiv papers, the first being the laser-driven RSoXS paper (the topic string itself was not recorded; this is the short topic of the owner's note) |
| Alternate 1 | `RSoXS vapor-deposited` | `all:"rsoxs vapor-deposited"` and `all:"rsoxs" AND all:"vapor-deposited"` | `logs/` Sept 2026: the second form returned 3 results, three on-topic arXiv preprints are in the local corpus (2402.07280, 2606.09527, 2607.20066) |
| Alternate 2 | `soft x-ray scattering` | phrase, then 3 ANDed terms | Not verified (no network); broader, so more hits and more noise (detector and astronomy papers) |
| Alternate 3 | `RSoXS` | `all:"rsoxs"` | Not verified; single narrow term, a paper reaches tier B when only its abstract names RSoXS |

Last Sept 2026 attempts at the plain phrase all ended in HTTP 429 (before the 3 s spacing existed), so no result
count for it is recorded. **Do a dry harvest of the primary topic on demo day minus one and keep the three alternates
ready.**

### 2.3 Size of a 60-second live harvest (estimate)

Searches: 2 requests, spaced 3 s each, plus server latency: about 8 to 12 s. A 429 or 503 answer with `Retry-After`
is waited out once, up to 60 s (`RETRY_AFTER_CAP_SECONDS`), so one throttle can use the whole minute. Downloads: 1 s
pause plus transfer. Measured on the Nov 4 harvest (`downloaded_at` stamps): 5 PDFs in 13.4 s, four gaps of 2.0, 1.5, 1.6 and
8.2 s, about 3.4 s each.

**Recommendation: sources arXiv only (OpenAlex needs `OPENALEX_API_KEY`), Max downloads 8, `per_query` left at 50.**
Expected 10 s search plus 8 x 3.4 s = about 40 s, and 60 s with a slow hit. `per_query` changes little (one request
per query up to 50), so it is not worth an API call. Max downloads 8 also keeps the library small enough to browse.

## 3. Pre-extracted fallback set

### 3.1 What ships in the image

`/app/storage/kg/` holds the 58 flat `matkg_*.json` files tracked in git (plus a `.bak` copy and nested manifests for 6
adopted releases; nested `graph.json` copies do not ship). I ran `adopt_legacy_flat_file(..., dry_run=True)` on all 58
with the current rules and `check_disclosure` on the chosen ones. Result: 43 would adopt, 6 were adopted earlier (their
nested folder exists), 5 are empty placeholders, and four are refused: `matkg_bl1101_v7`, `_v8`, `_v9` (R7: `DocumentChunk`
review flags) and `matkg_rsoxs_v3` (R2: nodes/edges format). `e2e_demo` has no tracked flat file: it was exported from
the owner's container (`matkg_e2e_demo.json`) and must be carried to the demo machine by the seed tooling.

"Rendered" below means nodes that are not `Unknown` placeholders (the viewer hides those) and the edges between them.
"Max 3-hop" is the largest neighbourhood any node has, to compare with the 500-node cap.

| # | Release (flat file) | Nodes / edges (file) | Rendered nodes / edges | Types | Max 3-hop | Disclosure R1-R7 | For |
|---|---|---|---|---|---|---|---|
| 1 | **bl1101/v4** `matkg_bl1101_v4.json` | 421 / 937 | 421 / 937 | 24 | 410 | pass, would adopt | dense beamline graph; rehearsed; Tiled readback 421 nodes |
| 2 | **opv_demo/v1** (copy of `matkg_deepseek-r1_70b_100_20250929_004942.json`) | 725 / 533 | 384 / 62 | 21 | 16 | pass, would adopt (also with the new name) | materials (OPV) KG from 73 source papers; the live adopt-and-publish corpus |
| 3 | bl1101/v6 `matkg_bl1101_v6.json` | 1,129 / 4,602 | 1,129 / 4,602 | 28 | 1,119 | pass, would adopt | richer beamline graph (222 runs, 222 RSoXS measurements, 241 samples); 3-hop views hit the 500 cap on purpose |
| 4 | deepseek-r1_32b_25 `matkg_deepseek-r1_32b_25_20250919_065133.json` | 1,315 / 1,123 | 748 / 192 | 20 | 28 | pass, would adopt | most connected small materials KG (24 papers) |
| 5 | xray_papers_cborg_chat `matkg_xray_papers_cborg_chat.json` | 206 / 107 | 198 / 97 | 12 | 54 | pass, would adopt | 7 source papers, 15 code snippets, file named for `cborg-chat`: the closest thing to what the live run makes |

Sizes checked: all five are under 1,400 nodes. #1, #2 and #5 render 500 nodes or fewer; #3 and #4 (1,129 and 748
rendered nodes) show the "SVG layout may lag" banner. Force layout settles in about 1 s at 500 nodes and 40 s at 10,000
(`reviews/H7/neighborhood_cap_evaluation.md`). Too large for the viewer: `matkg_rsoxs_v1` (7,022
nodes), `_v2` (12,014), `_v4` (22,012), `matkg_with_code` (15,986), the `qwen3_235b_*papers` files.
Also checked and passing but bigger: `matkg_lbl_cborg-chat_latest_25_20251007_224848.json` (2,403 / 2,640; 1,280 / 150
rendered; 25 papers).

An important finding for the explore story: in the paper-derived KGs most edges point at `Unknown` placeholders, so
only a few percent of nodes have a visible neighbour (opv_demo: 62 visible edges among 384 nodes, 308 isolated). The
dense graph for neighbourhoods is the beamline one (bl1101).

### 3.2 Recommended corpora

**Seed (published before the audience arrives): `e2e_demo/v1` and `bl1101/v4`**, the rehearsed pair.
- `e2e_demo`: Tiled holds 268 nodes and 126 links (269 entities at first publication; max degree 13). Node types and
  names cannot be read here (the file is not in the checkout). It is the corpus of the live-extraction narrative: the
  harvest filed five arXiv papers, the first being "Laser-driven resonant soft-X-ray scattering for probing picosecond
  dynamics of nanometre-scale order" (arXiv 2501.11506v2, 29 pages).
- `bl1101/v4`: 421 nodes, 937 links, 24 types (124 ProcessVariable, 90 Motor, 38 Software, 32 CodeSnippet, 22
  OphydDevice, 22 BeamlineStage, 20 AnalogInput, 13 DigitalIO, 10 Condition, 9 BlueskyPlan ...), no Material nodes.

**Adopted and published live: `opv_demo/v1`** (rename on copy; see the script). A fresh corpus is needed because a
corpus that is already live shows nothing new when "published" again. Types (rendered): ChemicalEntity 125, Material 52,
MaterialProperty 44, ConjugatedPolymer 42, ExperimentalTechnique 32, Device 24, ProcessingMethod 14, ElectronicProperty
12, PhotovoltaicCell 11, Thing 11; 341 more nodes are `Unknown` placeholders. The renamed copy was dry-run adopted in a
temporary folder (corpus `opv_demo`, snapshot `v1`, schema `matkg_schema`). Publishing it to Tiled was not
rehearsed here: time it on the Mac **(not measured)**.
If `matkg_e2e_demo.json` cannot be carried over, use #5 above as the second seed.

**Which corpus the QA questions target.** `e2e_demo` for the laser-driven RSoXS paper and `bl1101/v4` for beamline
questions (the QA worker's `qa_questions.json`, in progress on its own branch and not merged yet, uses exactly these, with `rsoxs_v1` as a larger JSON graph). Materials
questions from `storage/competency_questions/thomas_f.txt` (28 OPV questions) fit `opv_demo`: a crude check, counting
questions with at least 2 content words found in node names, gives 21 of 28 for opv_demo, 24 for `rsoxs_v1`, 14 for
`xray_papers_cborg_chat` and 6 for `bl1101_v4` (a word-overlap proxy, not an answer-quality measure).

### 3.3 Five named nodes for "explore and edit"

All ids, labels and types below were read from the files; degrees are visible links.

**opv_demo** (edit here, in Tiled via Source > `opv_demo / v1`):

| Node id | Label | Type | Visible links | What to do |
|---|---|---|---|---|
| `matkg:P3HT` | P3HT | ConjugatedPolymer | 5 | Start here: search "P3HT", focus it, neighbours include the blend, an OFET and a hybrid composite |
| `matkg:P3HTPC61BMblend` | P3HT:PC61BM blend | ChemicalEntity | 3 | **Edit: change the type to Material** (a blend is a material); the node colour changes |
| `matkg:66-Phenyl-C71-butyricacidmethylesterPCBM` | [6,6]-Phenyl-C71-butyric acid methyl ester (PCBM) | ChemicalEntity | 2 | **Edit: correct the label to "(PC71BM)"**: C71 is PC71BM, and a separate `matkg:PC71BM` node exists |
| `matkg:Y6` | Y6 | ChemicalEntity | 2 | Neighbours Y6-1O and Y6-2O (`derivative_of`); asked about in the OPV questions |
| `matkg:WAXS` | WAXS | ExperimentalTechnique | 3 | Ties materials to scattering; a technique node |

**bl1101/v4** (read, do not edit): `beamline:BL-11-0-1-2` "ALS Beamline 11.0.1.2" (Beamline, 298 links: the hub; its 1-hop
view is 199 nodes), `beamline:stage-Sample-stage` (30), `beamline:stage-Detector` (19), `beamline:Software-Bluesky` (23),
`beamline:Motor-beamline-energy` "Beamline Energy" (7).

**e2e_demo**: five nodes cannot be named here (graph not in the checkout). When the seed tooling exports it, pick the
nodes of the laser-driven RSoXS paper with `scripts/demo/qa_expected_nodes.py` or by search, and add them to
`demo_content.json`.

## 4. Fifteen-minute script

Pre-flight (before anyone arrives, not counted): seed state has `e2e_demo/v1` and `bl1101/v4` published; the
live-adopt file is in the container; CBORG answers (LBL network or VPN) and the worker has the key; the PDF of
candidate 1 sits on the Desktop; Mac awake (sleep prevention on); both slots of the worker idle.

| Time | Storyline | Click | Say | Expected | If it fails |
|---|---|---|---|---|---|
| 0:00-1:00 | Intro | none | "This runs entirely on this laptop: papers in, knowledge graph out, published to a local Tiled, then questions answered from it." | Title screen | n/a |
| 1:00-2:00 | 1a Live extraction, start | Library > Papers: drag the PDF into category `demo_live`. Extract: Start new KG, name `live1`, pick the paper, model `cborg-chat`, Estimate cost (about 8 pages, 12,800 tokens), tick confirm, Start | "One real paper, a free LBL-hosted model. It takes minutes, so it runs in the background while I show the rest." | Progress bar at 0 of 1, live log scrolling | Upload refused or no model: say so, skip to 3:00 and use `e2e_demo` as the "already extracted" example |
| 2:00-4:00 | 1b Harvest | Library > Harvester: topic `resonant soft x-ray scattering`, category `demo_live`, arXiv only, Max downloads 8, Start | "It searches arXiv, ranks every hit A, B, C and only downloads open papers it believes are relevant." | Log shows two searches, a tier count, about 8 PDFs filed in about 40 s; a paywalled or off-topic row | 429/slow after 60 s: Cancel, point at the Nov 4 run's 5 papers (`e2e_demo` category) and move on; try alternate 1 only if the audience asks |
| 4:00-7:30 | 2 Adopt, stage, approve | Terminal: copy and adopt `opv_demo` (see below). Terminal: POST /v1/publications. Pending Approvals > Refresh > open the card > Approve > confirm. Wait for published | "Adopting freezes a release. Staging makes a plan: so many creates and links. Nothing is written until a person approves, and the approval is bound to the plan's hash." | Card shows corpus `opv_demo`, target `http://127.0.0.1:8765`, plan hash; after approval the job reaches published within about a minute **(estimate)** | Adopt refused: the file is already adopted, use `opv_demo2`-style name; approve 409 stale: Refresh, approve the new plan; stuck: show `bl1101/v4` in the Source list as the published example and continue |
| 7:30-11:00 | 3 Explore and edit | Knowledge graph > KG Viewer. Source: show JSON list (read-only badge) vs Tiled (`opv_demo / v1`, editable badge). Search P3HT, focus, click neighbours. Open `P3HT:PC61BM blend` > Edit > type Material > Save. Open the PC71BM node > Edit label > Save. Switch to `tiled:bl1101` and open Sample stage | "JSON is read-only. A Tiled graph that is live here is editable, and Save goes back into Tiled, to the graph you are looking at." | After Save the node colour or label changes and the graph refreshes | Edit button missing: wrong source or corpus not live: re-select `tiled:opv_demo`; Save error: show the typed message, switch to reading `bl1101` neighbourhoods |
| 11:00-13:30 | 4 QA | Chat, ask 3 questions from `qa_questions.json` (one on `e2e_demo`, one beamline on `bl1101`, one OPV); open View Knowledge Graph below an answer | "The answer cites graph nodes, and the nodes light up." | Answers with highlighted nodes in a few seconds each | CBORG unreachable (only LBLnet or VPN): switch to the recorded answers (W2 fallback), do not retry live |
| 13:30-14:30 | 1c Extraction result | Extract tab: the run | "Here is the paper we started at the beginning." | Succeeded: Export, show it as a `matkg_live1.json` file. Not finished: "still running" | Not finished by 12 minutes of run time: show the cancelled or running state honestly and open `e2e_demo` (extracted earlier from the same harvest) in the viewer |
| 14:30-15:00 | Wrap | none | "Same containers deploy at ALS with a central Tiled." | n/a | n/a |

Commands for 4:00-7:30 (see `mkdocs/docs/local-image.md`, sections 3 and 4; `<sha>` is the commit the image was built
from, `docker` is only for the presenter's own terminal):

```bash
docker exec f2w-local cp /app/storage/kg/matkg_deepseek-r1_70b_100_20250929_004942.json \
  /var/lib/fair2wise/state/session/storage_kg/matkg_opv_demo.json
docker exec f2w-local f2w-kg-adopt adopt --storage-kg /var/lib/fair2wise/state/session/storage_kg \
  --code-commit <sha> --only matkg_opv_demo.json
curl -fsS -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8080' \
  -X POST http://127.0.0.1:8080/fair2wise/api/v1/publications \
  -d '{"snapshot_ref":"opv_demo/v1","target":"http://127.0.0.1:8765"}'
```

Staging has no button in the UI (owner decision: no library-to-publish button yet), so the presenter runs the `curl`
and then approves in the browser. Slot use: extraction takes one of the 2 worker slots from minute 2; the harvest the
other until minute 4; the publication needs a free slot, so never start the harvest again after minute 4.

## 5. Content risks

| Risk | What happens | What to do |
|---|---|---|
| Harvest returns irrelevant or paywalled papers | The Nov 4 run filed 5 papers, 2 of them astronomy (cyclotron lines in X-ray sources) because the ANDed terms match "resonance scattering ... soft X-ray"; and one was a detector paper. Paywalled hits become "paywalled" rows, not downloads | Say it: ranking is a filter, not a judge. Use Dismiss. Choose the extraction paper from the uploaded PDF, never from the harvest |
| arXiv throttling | 429/503 with `Retry-After` waits up to 60 s once; Sept 2026 logs show many 429s before spacing was added | Dry run the day before; Max downloads 8; have the Nov 4 `e2e_demo` set as the spoken fallback |
| Live extraction slow or failing | The reasoning model took 2.3 min per page; `cborg-chat` is untimed; Cancel cannot stop mid-paper; CBORG answers only from LBLnet or VPN; rate limits cost retries (970 "Agent failed" lines in a Sept batch) | Start by minute 2; rehearse once and replace the estimate; fallback is the already extracted `e2e_demo` |
| Large graph slowness | **Update 2026-11-10:** a source above 1,500 nodes (`rsoxs_v4` 22,012; the 580-paper OPV KG 15,815) now opens on a 300-node overview of the most connected nodes (Unknown placeholders hidden), not on "All": search covers every node, focus shows a neighbourhood capped at 500, Show more steps 300, 1,000, 2,000, 5,000 and Force is disabled above 2,000 (see `neighborhood_cap_evaluation.md` section 10). Below that limit the viewer defaults to "All" nodes as before. Over 500 nodes the viewer shows "SVG layout may lag at this scale"; force layout needs about 1 s at 500 nodes, 40 s at 10,000 | The big graphs are now safe to open (overview, search, focus); do not step past 2,000 nodes on stage (a few seconds at 2,000, 20 s or more at 5,000) and do not choose Force on a graph above 1,000 nodes |
| Neighbourhood truncation notice | A focused Tiled neighbourhood is cut at 500 nodes; the viewer shows "Neighbourhood limited to N nodes; N+ nodes match" (header `X-F2W-Graph-Truncated`). It means the view is incomplete, not an error | Not triggered by `e2e_demo` (max 28), `opv_demo` (max 3-hop 16) or `bl1101/v4` (max 410). `bl1101/v6` reaches 1,119, so publishing v5/v6 would show it. If it appears: say "this is a safety cap", pick a smaller hop or another node |
| Editable-pill rule | JSON sources are always read-only (Edit hidden, `400 read_only_source`). A Tiled source is editable only when its corpus is live on the Save target, and Save edits the corpus shown | Check the badge before editing; edit only on `tiled:opv_demo` |
| Unreconciled edit blocks the next publication | Each Save is journaled for its corpus. Publishing that corpus again is refused with `CONFLICT` until the edits are reconciled into a new version (`f2w-kg-snapshot --reconcile-edits`, then a commit) | **Never edit `e2e_demo` or `bl1101` before the demo** and never edit a corpus you still plan to publish again. Do all rehearsal edits on a copy of the volume and run the reset script (W1) before the real run. Do not use Rollback or Operations; do not re-publish `opv_demo` after Save |
| Pointer and Save target | The last publication moves the pointer; Save follows the corpus shown | Publish `opv_demo` before editing, and select its source explicitly |
| Duplicate adoption | Adopting a name that already has `<corpus>/v1` is refused ("frozen directories are never rewritten") | Start from a clean volume or rename the copy; the reset script must remove `opv_demo` |
| Docling or weights missing on the demo machine | Extraction would fail at the start | The rehearsal must parse candidate 1 once |

## 6. What I could not determine

- Time of one paper in the `e2e_demo` extraction (no job events in the repository) and any `cborg-chat` extraction time.
- Docling's own parse result for the five papers (weights not available offline).
- arXiv hit counts for any topic (no network), and the exact topic string of the Nov 4 harvest.
- Node types, names and counts of `e2e_demo` (not tracked); only its Tiled readback (268 / 126) is documented.
- Publication time of `opv_demo` into Tiled.
- QA answer quality on any corpus (no model calls; the QA worker owns that review).
