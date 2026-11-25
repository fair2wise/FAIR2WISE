# Extraction tab

The **Extract** tab (left sidebar, `:local` image only) builds a knowledge graph (KG) from library papers without using the chat or the command line. It runs as a background job in the worker, so it survives closing the tab or reloading the page.

## Idle view: start or continue

- **Knowledge graph.** *Start new KG*, or *Continue existing KG* (an earlier bundle, or a plain KG file in the release folder; continuing a file rebuilds its terms from the KG first). A continuation creates a new child bundle and leaves the parent unchanged.
- **KG name**, **Topic** (a label), **Schema** (locked to the parent's schema when continuing) and **Model**. The model list is the provider's live list; it is used for this run only and never changes Settings.
- **Papers.** Pick from the library. The folder filter selects a category (`rsoxs`, `tomo`, ...) and *Select all shown* picks that whole folder for the topic; the text filter narrows by file name, title or DOI.
- **Cost.** *Estimate cost* gives the page count and an estimated cost; the *I confirm this cost estimate* box unlocks *Start extraction*. Changing papers, model or schema invalidates the estimate.

Without a configured model backend the tab says so and Start stays disabled: set `CBORG_API_KEY` (or `F2W_EXTRACT_BACKEND=ollama`) in the worker environment.

## Run view

Progress bar (papers done of total), a live log (bounded to the latest lines; followed by short polling of `GET /v1/jobs/{id}/logs?after=<seq>`, paused while the tab or page is hidden), and **Cancel**. Cancelling stops between PDFs and keeps what finished. A run is resumable: a PDF is re-processed only if its bytes, the model or the schema changed. When the job succeeds, **Export** writes the KG to the configured release folder; **New extraction** returns to the idle view.

## Publish to Tiled

On the `:local` image a **Publish to Tiled** button sits next to **Export** (in the finished run and beside the "Existing KG" picker). It runs the three manual steps in one go: it exports `matkg_<name>.json`, adopts it as a frozen release `<corpus>/<vN>` (the corpus is the KG name, the version is the next free one, an earlier release is never overwritten) and stages a publication plan for this container's own Tiled. A dialog first states exactly that, and that **the plan still needs your approval**: nothing is published until you approve it on **Pending Approvals** ([approvals](ui-approvals.md)); the button never approves anything. Progress is shown per step; when the plan is staged the dialog shows its hash and links to Pending Approvals. Refusals are shown by reason: the disclosure check (rule and count, for example `R7 x2`), an image without a commit label (set `F2W_CODE_COMMIT`, see [the local image](local-image.md)), an empty KG, or a plan already pending for this corpus. The button is hidden when the server does not offer it (an ALS image, no managed Tiled).

## Using an extracted KG, and what Export is for

An extraction creates a KG under its name (for example `tomo_test`). You do **not** need to export it to use it: it is already a graph of this installation, listed by that name in the **KG Viewer** source list, in **Settings** under the JSON graphs, and in **Operations** (graph statistics and the other reads). In **chat** it is searched once you select it in Settings (chat only searches the graphs you have selected), and its references and node cards carry its name, not "kg". Editing the schema or re-running never changes it behind your back.

**Export** is a different step. It does **not** download anything to your computer: after it succeeds the page says which file it wrote (`matkg_<name>.json`, with its size), that the file is stored on the server in the release folder for Publish to Tiled and Continue existing KG, and offers a **Download** link that streams that file to your browser (`GET /v1/library/kgs/<name>/download`, 404 until the KG has been exported). It writes a plain file, `matkg_<name>.json`, into the release folder (`F2W_KG_RELEASE_DIR`, by default `kg_releases` inside the state folder). You need that file when you want the KG as a file you can copy out, keep or hand to someone, when you want to continue from it as a plain KG file in another KG, or when you adopt it as a frozen release by hand. **Publish to Tiled** exports for you as its first step. A file in the release folder is *not* added to chat, the viewer or Operations by itself; only the KG itself is.

## Known issues

- **Supporting passages were dropped for nearly every term between 2026-09-15 and 2026-11-24.** The LangGraph tool-based
  extractor introduced in commit `0b0fb782` ("Initial RSoXS commit", 2026-09-15) kept the page of a `register_term`
  call in a thread-local that the tool node's worker threads never saw, so the tool had no page and silently discarded
  the passage the model had given (the legacy extractor before it recorded a passage for every term: the
  `storage/terminology/extracted_terms_aug21_*.json` files have 100% coverage, `extracted_terms_rsoxs_v1.json` 3.7%).
  The terms-to-KG build then dropped the passages of any term that had first been created as a relation-target stub
  (on the shipped RSoXS terms: 849 distinct passages in, 230 out). Both are fixed as of 2026-11-24: the page travels in
  a context variable, a passage is kept even when the page is unknown, and the KG build of an extracted bundle unions passages,
  pages, properties and publications into an existing node (the legacy json2kg CLI and the snapshot path keep their
  frozen T23A behaviour; `docs/migration/json_kg_contract.md` 2.6a). Still open, design pending owner review: relationship evidence
  (`has_evidence` was never populated by the extractor), passage quality rules (a fallback used to take the first 500
  characters of a page, which can be a title/affiliation block), a recovery job for existing KGs without passages
  (`tomo_test`, `scattering_test`), and per-run coverage reporting.
- **`storage/kg/matkg_rsoxs_v1.json` was built from an older, smaller terms snapshot** than the shipped
  `storage/terminology/extracted_terms_rsoxs_v1.json`: 18,490 of the terms file's 24,328 distinct ids are not in the KG
  and the current builder has no filter that would remove them (it is a snapshot-age difference, not a rule). The file
  is left as it is pending an owner decision on the shipped corpus (owner queue B19).

## What gets written

Each KG is a bundle under the server's work directory (`kgs/<name>/`) with the terms, the KG, a manifest, `processed_pdfs.json` and `extraction_manifest.json` in the shape the command-line coordinator writes, so the CLI and the tab agree.

See also [Library tab and Harvester](ui-library.md) for finding and adding papers.
