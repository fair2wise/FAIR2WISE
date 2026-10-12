# H1 owner decisions log

Decisions the project owner has made during H1 contract work. These are not
the H1 gate approval; that is recorded separately in `decision.json` when the
owner reviews the H1 packet.

## O-D1: verbatim snippets in stored KG JSON (2026-10-12)

**Finding (T21A).** Tracked `storage/kg` JSON holds about 127k verbatim
context snippets. A 300-sample check matched every sampled snippet to its
cited PDF page, and combined they reproduce a median 31% (max 48%) of each
paper's text across 90 papers. `storage/terminology` holds about 141k more.
Under D19 this would make the files a partial source-text export, in the
public repository and in any image that includes `storage/`.

**Decision.** Keep the KG JSON files, snippets included, in the repository and
in the Docker image.

**Scope of the exception.**

- Applies to stored KG artifacts: committing them to Git (D31) and baking them
  into images (H0 E3).
- Does not relax D19 for serving. What routes, SSE, traces, graph detail and
  exports return to users still goes through the T21A disclosure projection.
- Does not cover raw PDFs, full parsed paper bodies, figure crops, the
  source-RAG index (`storage/source_index`, H0 E4), credentials, local paths
  or operational databases.
- `storage/terminology` is already in the image under H0 E3 and stays in Git;
  this decision does not change that.

**Updated in.** `docs/adr/content-disclosure.md` (O-D1); T26A release contract
(relayed to the T23A/T26A worker).

## O-D2: what is tracked in Git (2026-10-12)

**Decision (owner, verbatim intent).** Beamline ops documents are fine to keep
in this repo. Research papers stay gitignored. All KGs are saved in this repo
and not ignored.

**Applied in `0d3d294`.**

- `.gitignore`: the `storage/kg/*.json` and `*.json.bak` rules are removed.
  `papers/` became `papers/**` with a re-include for
  `papers/rsoxs/beamline_blueprint/blueprint_*.html`. No PDF is trackable.
- Committed the 12 previously untracked KGs (`matkg_bl1101_v1`-`v9`,
  `matkg_rsoxs_v1`-`v3`) after a credential and local-path scan, and the BL
  11.0.1.2 blueprint HTML.
- Effect on the H0 baseline deviations: the 15 `test_bl1101_ingest` errors
  were caused by the missing `matkg_bl1101_v7.json`; with it committed, the
  isolated suite at HEAD is 770 passed, 3 failed (the ambient
  `CBORG_API_KEY` and `pixi` cases), 20 skipped, 4 xfailed.

**Still unclassified (left ignored pending the owner):**

| Item | Size | What it is |
| --- | --- | --- |
| `papers/rsoxs/manifest.json`, `papers/tomo/manifest.json` | 2.4 MB, 172 KB | Harvest manifests: paper metadata the source-RAG and harvest code read; no paper text expected |
| `papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf` | 490 KB | Publication-list export stored next to the blueprint |
| `.cache/bl1101/pages`, `readmes` | 120 KB, 2.7 MB | Cached ALS beamline page and READMEs, indexed as ops documents |
| `.cache/bl1101/repos` | 31 MB, part of 1264 files | Cloned third-party Git repositories indexed as ops documents (their own licenses apply) |
