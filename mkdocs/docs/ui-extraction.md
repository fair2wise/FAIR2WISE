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

## Schema panel: create, edit and try a schema

Next to the **Schema** field (and under the Harvester's schema field) the button **Create or edit a schema** opens the schema panel. It shows only on a local server and only when the schema routes answer: on ALS (404) or for a caller without the capability `library.author_schema` (403) the button is absent.

1. **Describe the domain, then Send.** The model is chosen in the panel's own picker, which lists the same free models as the Extract form (it starts on the form's choice). The model drafts a LinkML schema with the shipped schemas as a pattern; this can take minutes (a draft, then up to two repair rounds, each shown as an attempt). Failures are said in plain words: backend not configured, model not free, timeout, answer too large, Ollama unreachable, address not authorized, text too large. A draft is a draft: it may be wrong, and nothing is saved until you accept it.
2. **Edit and check.** A monospace editor with line numbers (no wrapping; Tab inserts two spaces, Escape then Tab leaves the field) and a counter against the 256 KiB limit. About 500 ms after you stop typing the text is checked; problems are listed by line (click one to select that line) and marked in the gutter with a `!`. Under the list the panel shows exactly what was checked (YAML parse without tags, anchors or duplicate keys; structural rules; the required core; loads in SchemaView and SchemaHelper) and what was not: **not full LinkML conformance**, the linkml linter and validator are not used. **Insert required core** adds the mandatory `Thing`, `CodeSnippet`, `name`, `description` and `related_to`. You can paste YAML, upload a `.yaml` or `.yml` file (at most 256 KiB, read as text only, checked after loading) or download the text as `<name>@vN.yaml` (made in the browser, no request).
3. **Accept and save.** The name follows `^[a-z][a-z0-9_-]{0,63}$` and must equal the text's own top-level `name`. Each save is a new immutable version. Invalid text is refused with its line-numbered report. A saved version appears in the **Schema** chooser under "Your schemas (user schema)" and is selected; a KG is then created on it exactly as on a shipped schema (the choice value is `user/<name>@vN`).
4. **Your schemas and versions.** Open a version in the editor, delete one (after a confirmation; a version a KG was built on cannot be deleted), or compare two versions (added and removed lines carry `+` and `-` and are tinted, never colour alone).
5. **Try it on one paper.** Choose a library paper and press Preview. The first request is only an estimate (pages, at most 3; tokens; cost; no model call). After you confirm, the first pages are read with one model call each and the panel shows the terms (category, definition, relations with whether each is verified), how many categories fell back to `Thing` and how many relations became `related_to` or stayed unverified, and the server's note. A preview is three pages and not the real extraction (no agent, no merging). Nothing is stored.

Text from the server and the schema is always rendered as plain text, never as HTML, and schema text is not kept in browser storage.

A user schema is marked "user schema" wherever the chooser shows it, and its KG cannot be published yet. **Long schemas are condensed for extraction:** a user schema whose prompt text exceeds 6,000 characters (Ollama) or 12,000 (CBORG) is sent as a condensed view (class names with a first sentence, relations with domain and range). After **Estimate cost** the schema field says so before you start; the run view repeats it with the sizes. Shipped schemas are never condensed.

## Publish to Tiled

On the `:local` image a **Publish to Tiled** button sits next to **Export** (in the finished run and beside the "Existing KG" picker). It runs the three manual steps in one go: it exports `matkg_<name>.json`, adopts it as a frozen release `<corpus>/<vN>` (the corpus is the KG name, the version is the next free one, an earlier release is never overwritten) and stages a publication plan for this container's own Tiled. A dialog first states exactly that, and that **the plan still needs your approval**: nothing is published until you approve it on **Pending Approvals** ([approvals](ui-approvals.md)); the button never approves anything. Progress is shown per step; when the plan is staged the dialog shows its hash and links to Pending Approvals. Refusals are shown by reason: the disclosure check (rule and count, for example `R7 x2`), an image without a commit label (set `F2W_CODE_COMMIT`, see [the local image](local-image.md)), an empty KG, or a plan already pending for this corpus. The button is hidden when the server does not offer it (an ALS image, no managed Tiled).

## Using an extracted KG, and what Export is for

An extraction creates a KG under its name (for example `tomo_test`). You do **not** need to export it to use it: it is already a graph of this installation, listed by that name in the **KG Viewer** source list, in **Settings** under the JSON graphs, and in **Operations** (graph statistics and the other reads). In **chat** it is searched once you select it in Settings (chat only searches the graphs you have selected), and its references and node cards carry its name, not "kg". Editing the schema or re-running never changes it behind your back.

**Export** is a different step. It does **not** download anything to your computer: after it succeeds the page says which file it wrote (`matkg_<name>.json`, with its size), that the file is stored on the server in the release folder for Publish to Tiled and Continue existing KG, and offers a **Download** link that streams that file to your browser (`GET /v1/library/kgs/<name>/download`, 404 until the KG has been exported). It writes a plain file, `matkg_<name>.json`, into the release folder (`F2W_KG_RELEASE_DIR`, by default `kg_releases` inside the state folder). You need that file when you want the KG as a file you can copy out, keep or hand to someone, when you want to continue from it as a plain KG file in another KG, or when you adopt it as a frozen release by hand. **Publish to Tiled** exports for you as its first step. A file in the release folder is *not* added to chat, the viewer or Operations by itself; only the KG itself is.

## Supporting passages, and Find supporting text

The sentences an extraction found a term in are shown on a node card when `F2W_DISCLOSE_SNIPPETS` is on. They are checked against the paper: a sentence the model quotes is kept only when it is found in the paper's text (and then stored with the page where it was found), otherwise the term's mention is searched for in the paper and the sentence around it is kept. The card marks each passage **quoted** (the model's sentence, found in the paper) or **located** (found by searching the paper). A term with no findable mention has no passage and is counted as such. The run view of an extraction shows the coverage: terms with supporting text, quoted, located, sentences rejected and terms without, per paper and in total, with a warning line for a paper under 90%. The counts are visible without `F2W_DISCLOSE_SNIPPETS`; the text is not.

**Find supporting text** sits on the **Existing KG** picker (Continue existing KG) beside Export and Publish to Tiled. It searches the KG's source papers, in the library, for the sentences that mention each term; no model is used and existing text is kept. It shows the papers searched of the total with the paper in progress, can be cancelled (the papers done keep what was found) and ends with the coverage block: how many terms got text and how many passages, how many terms still have none, a line per paper, and the papers that could not be searched with the reason in plain words (two library files with the same name and no way to tell which was used; not in the library; a scan with no text layer; unreadable). Running it again adds nothing. On a KG extracted before supporting text was recorded, each paper with no passages and no coverage says on the card "No supporting text was recorded for this paper. Use Find supporting text on the Extract page to add it."; the note goes away for a paper once it has been searched. **Relationships** get the same treatment: for each relation the search also looks for the sentences of the paper that name both of its ends (one sentence, or two adjacent sentences of one page), with no model. They are stored with the relation, unioned and de-duplicated, and a sentence that names only one end is never used. The node card shows them under each relationship (a collapsed "Show supporting text (n)" list, plain text, page and a `located` label, the first three with "Show more"); the counts are always visible, the sentences only with `F2W_DISCLOSE_SNIPPETS`. The coverage block adds, per paper and in total, the relations, how many have a sentence, the sentences stored and the relations with none (the log warns below 50%). Design and measurements: `design-supporting-passages.md`.

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
  characters of a page, which can be a title/affiliation block). Built on branch `passages` since: the quality rules and the
  locator, grounding at extraction time, per-run coverage, and the recovery job (Find supporting text) for existing KGs such
  as `tomo_test` and `scattering_test`. Still open: relationship evidence.
- **`storage/kg/matkg_rsoxs_v1.json` was built from an older, smaller terms snapshot** than the shipped
  `storage/terminology/extracted_terms_rsoxs_v1.json`: 18,490 of the terms file's 24,328 distinct ids are not in the KG
  and the current builder has no filter that would remove them (it is a snapshot-age difference, not a rule). The file
  is left as it is pending an owner decision on the shipped corpus (owner queue B19).

## What gets written

Each KG is a bundle under the server's work directory (`kgs/<name>/`) with the terms, the KG, a manifest, `processed_pdfs.json` and `extraction_manifest.json` in the shape the command-line coordinator writes, so the CLI and the tab agree.

See also [Library tab and Harvester](ui-library.md) for finding and adding papers.
