# Extraction tab

The **Extract** tab (left sidebar, `:local` image only) builds a knowledge graph (KG) from library papers without using the chat or the command line. It runs as a background job in the worker, so it survives closing the tab or reloading the page.

## Idle view: start or continue

- **Knowledge graph.** *Start new KG*, or *Continue existing KG* (an earlier bundle, or a plain KG file in the release folder; continuing a file rebuilds its terms from the KG first). A continuation creates a new child bundle and leaves the parent unchanged.
- **KG name**, **Topic** (a label), **Schema** (locked to the parent's schema when continuing) and **Model**. The model list is the provider's live list; it is used for this run only and never changes Settings.
- **Papers.** Pick from the library. The folder filter selects a category (`rsoxs`, `tomo`, ...) and *Select all shown* picks that whole folder for the topic; the text filter narrows by file name, title or DOI.
- **Cost.** *Estimate cost* gives the page count and an estimated cost; the *I confirm this cost estimate* box unlocks *Start extraction*. Changing papers, model or schema invalidates the estimate.

Without a configured model backend the tab says so and Start stays disabled: set `CBORG_API_KEY` (or `F2W_EXTRACT_BACKEND=ollama`) in the worker environment.

## Run view

Progress bar (papers done of total), a live log (bounded to the latest lines), and **Cancel**. Cancelling stops between PDFs and keeps what finished. A run is resumable: a PDF is re-processed only if its bytes, the model or the schema changed. When the job succeeds, **Export** writes the KG to the configured release folder; **New extraction** returns to the idle view.

## Publish to Tiled

On the `:local` image a **Publish to Tiled** button sits next to **Export** (in the finished run and beside the "Existing KG" picker). It runs the three manual steps in one go: it exports `matkg_<name>.json`, adopts it as a frozen release `<corpus>/<vN>` (the corpus is the KG name, the version is the next free one, an earlier release is never overwritten) and stages a publication plan for this container's own Tiled. A dialog first states exactly that, and that **the plan still needs your approval**: nothing is published until you approve it on **Pending Approvals** ([approvals](ui-approvals.md)); the button never approves anything. Progress is shown per step; when the plan is staged the dialog shows its hash and links to Pending Approvals. Refusals are shown by reason: the disclosure check (rule and count, for example `R7 x2`), an image without a commit label (set `F2W_CODE_COMMIT`, see [the local image](local-image.md)), an empty KG, or a plan already pending for this corpus. The button is hidden when the server does not offer it (an ALS image, no managed Tiled).

## What gets written

Each KG is a bundle under the server's work directory (`kgs/<name>/`) with the terms, the KG, a manifest, `processed_pdfs.json` and `extraction_manifest.json` in the shape the command-line coordinator writes, so the CLI and the tab agree.

See also [Library tab and Harvester](ui-library.md) for finding and adding papers.
