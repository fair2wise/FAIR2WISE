# Library tab and Harvester

The **Library** tab (left sidebar) manages the reference PDFs of the papers folder: what is there, adding to it, checking titles and DOIs, indexing it for question answering, and finding new papers for a topic. It exists only on the `:local` image. On an ALS image the library routes answer 404 and the tab is hidden.

The tab has two panels, switched by the **Papers** / **Harvester** tab strip.

## Papers

- **Categories.** The top-level folders of the papers folder are categories (`rsoxs`, `tomo`, ...). Files directly in the root show as *Uncategorized*. The folder filter above the table (`All`, then one button per category with its count) narrows the table; the table has a *Folder* column whose tooltip shows the full path (for example `rsoxs/2013/x.pdf`).
- **Upload.** Drop PDFs on the drop zone (or press Enter on it to browse). The **Upload to** control chooses the destination: the library root, an existing category, or **New category…** (letters, digits, `_`, `-`; the category folder is created on first use).
- **Filed by year.** Inside a category a paper is stored as `<category>/<year>/<file>.pdf`. The year is read locally, with no network: a year in the file name (publisher downloads are named `Journal - 2016 - Author - Title.pdf`), then the received/published/copyright years printed on the first pages, then the PDF creation date. If none is found the paper goes to `<category>/unknown/`. Papers uploaded to the root are not filed by year.
- **Manifest.** Each category keeps a `manifest.json` (the same file the harvest scripts maintain). An upload into a category appends a row (`source: upload`, `pdf_path: papers/<category>/<year>/<file>.pdf`, DOI, title, year, `pdf_sha256`, `ingestion.status: pending`). An existing manifest is only appended to; one that cannot be parsed is left untouched and the upload reports why.
- **Duplicates.** A PDF whose sha256 is already in the library writes nothing; the toast names the existing path.
- **Verify** checks one paper's title and DOI against OpenAlex (cached by sha256). **Verify all** and **Index for QA** run as background jobs with a status line; indexing makes the papers searchable in Source RAG. Verification never moves a file.

## Harvester

The Harvester finds new papers for a topic and files them into a category.

1. **Form.** *Topic* (up to 200 characters; quote a phrase to keep it whole), *Category* (an existing folder or a new one; the root is not offered), *Schema (label only)*, *Sources* (arXiv, OpenAlex), and *Max downloads* (1 to 200, default 20). The schema is recorded with the run so Extraction can default to it; it does not change the search or the ranking.
2. **Search and ranking.** The job searches each chosen source and merges duplicates by DOI or arXiv id. Each paper is scored against the topic words: **A** when every word appears in the title or abstract and at least half in the title, **B** when at least 60 % appear, **C** otherwise. Papers already in the library (same DOI) are skipped.
3. **Downloads.** Tier A, then B, in score order: the open-access PDF is fetched (every redirect checked against the network policy, size and time limits, `%PDF-` check) and filed like an upload, with the registry's year, title and DOI. Tier C is never downloaded. The run stops downloading at *Max downloads*.
4. **Paywalled queue.** An A/B paper with no free PDF (or whose "open" link is not a PDF) is **paywalled**. The queue shows an **Open publisher** link (opens in your own browser; the server never requests the publisher), a drop slot for the PDF you save, and **Dismiss**. A PDF you drop is filed in the run's category by the paper's year and the candidate becomes *filed*.
5. **Run view.** Progress, a live log, **Cancel**, and a results table with the status of every candidate: *downloaded*, *in library*, *paywalled*, *filed*, *failed*, *skipped*, *dismissed*. Past runs are listed and can be reopened; a run still in progress is re-attached after a page reload.

OpenAlex needs `OPENALEX_API_KEY` in the server environment; without it only arXiv is searched and the log says so.

## API

| Call | Purpose |
|---|---|
| `GET /v1/library`, `GET /v1/library/papers` | status and the papers (each with `folder`, `relpath`) |
| `POST /v1/library/papers?folder=<category>` | upload one PDF (multipart `file`) |
| `POST /v1/library/papers/{id}/verify` | verify one paper |
| `POST /v1/jobs` kind `library_verify`, `library_index`, `library_harvest` | background jobs (`inputs.params`, with a `nonce` to re-run) |
| `GET /v1/library/harvest/runs`, `.../runs/{id}/candidates` | Harvester results |
| `POST /v1/library/harvest/candidates/{id}/pdf`, `.../dismiss` | complete or dismiss a paywalled paper |

The full route table with capabilities is in `docs/migration/api/README.md`. All of these need the local-operator `library.*` capabilities.

To turn a harvested and extracted KG into a published graph, continue with [The `:local` image, end to end](local-image.md).
