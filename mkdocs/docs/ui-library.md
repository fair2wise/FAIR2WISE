# Library tab and Harvester

The **Library** tab (left sidebar) manages the reference PDFs of the papers folder: what is there, adding to it, checking titles and DOIs, indexing it for question answering, and finding new papers for a topic. It exists only on the `:local` image. On an ALS image the library routes answer 404 and the tab is hidden.

The tab has two panels, switched by the **Papers** / **Harvester** tab strip.

## Papers

- **Categories.** The top-level folders of the papers folder are categories (`rsoxs`, `tomo`, ...). Files directly in the root show as *Uncategorized*. The folder filter above the table (`All`, then one button per category with its count) narrows the table; the table has a *Folder* column whose tooltip shows the full path (for example `rsoxs/2013/x.pdf`).
- **Upload.** Drop PDFs on the drop zone (or press Enter on it to browse). The **Upload to** control chooses the destination: the library root, an existing category, or **New category…** (letters, digits, `_`, `-`; the category folder is created on first use).
- **Filed by year.** Inside a category a paper is stored as `<category>/<year>/<file>.pdf`. The year is read locally, with no network: a year in the file name (publisher downloads are named `Journal - 2016 - Author - Title.pdf`), then the received/published/copyright years printed on the first pages, then the PDF creation date. If none is found the paper goes to `<category>/unknown/`. Papers uploaded to the root are not filed by year.
- **Manifest.** Each category keeps a `manifest.json` (the same file the harvest scripts maintain). An upload into a category appends a row (`source: upload`, `pdf_path: papers/<category>/<year>/<file>.pdf`, DOI, title, year, `pdf_sha256`, `ingestion.status: pending`). An existing manifest is only appended to; one that cannot be parsed is left untouched and the upload reports why.
- **Authors and abstract.** A row also carries `authors` (display names, at most 100, no e-mail addresses) and `abstract` (at most 4000 characters) when a source gave them: the Harvester stores what OpenAlex or arXiv returned for the paper, and **Verify** stores them from the OpenAlex work it matched, filling only empty fields. A paper whose row has none shows "not stored" in the list; they are kept in the manifest only (never in logs, traces or job messages). Papers harvested earlier are filled by the `f2w-library-backfill` command (see below).
- **Duplicates.** A PDF whose sha256 is already in the library writes nothing; the toast names the existing path.
- **Verify** checks one paper's title and DOI against OpenAlex (cached by sha256). **Verify all** and **Index for QA** run as background jobs with a status line; indexing makes the papers searchable in Source RAG. Verification never moves a file.

## Harvester

The Harvester finds new papers for a topic and files them into a category.

1. **Form.** *Topic* (up to 200 characters; quote a phrase to keep it whole), *Category* (an existing folder or a new one; the root is not offered), *Schema (label only)*, *Sources* (arXiv, OpenAlex), and *Max downloads* (1 to 200, default 20). The schema is recorded with the run so Extraction can default to it; it does not change the search or the ranking.
2. **Search and ranking.** The job searches each chosen source and merges duplicates by DOI or arXiv id. Each paper is scored against the topic words: **A** when every word appears in the title or abstract and at least half in the title, **B** when at least 60 % appear, **C** otherwise. Papers already in the library (same DOI) are skipped.
3. **Downloads.** Tier A, then B, in score order: the open-access PDF is fetched (every redirect checked against the network policy, size and time limits, `%PDF-` check) and filed like an upload, with the registry's year, title and DOI. Tier C is never downloaded. The run stops downloading at *Max downloads*.
4. **Paywalled queue.** An A/B paper with no free PDF (or whose "open" link is not a PDF) is **paywalled**. The queue shows an **Open publisher** link (opens in your own browser; the server never requests the publisher), a drop slot for the PDF you save, and **Dismiss**. A PDF you drop is filed in the run's category by the paper's year and the candidate becomes *filed*.
5. **Run view.** Progress, a live log, **Cancel**, and a results table with the status of every candidate: *downloaded*, *in library*, *paywalled*, *filed*, *failed*, *skipped*, *dismissed*. Past runs are listed and can be reopened; a run still in progress is re-attached after a page reload. The page follows the run by short polling (the job state and `GET /v1/jobs/{id}/logs?after=<seq>`, about every 2 s, slower when idle or after errors), not by a long-lived stream, so a proxy that cuts idle connections cannot break it. Polling pauses while the tab or the browser page is hidden and resumes, from the last line seen, when it is shown again.

OpenAlex is searched with `OPENALEX_API_KEY` when the server environment has one. Without a key it is still searched, unauthenticated and at a reduced rate (about one request per second, one request per search, the contact address sent when one is configured); the Harvester panel shows "running without a key, reduced rate" next to OpenAlex and the job log starts the OpenAlex stage with a line saying so. If OpenAlex answers that the unauthenticated search is rate limited, OpenAlex is skipped for that run with a plain log line ("add OPENALEX_API_KEY") and arXiv results still count. Library metadata verify and backfill still require the key. After the searches the log carries one `search summary` line in plain words: what each source returned or why it could not be searched, how many results the merge dropped (no title, duplicates) and how many distinct papers remain. A search that found nothing says whether that is an empty answer or a source that never ran.

**A source that fails is an error, never "0 found".** Each source's answer is stored with the run (`source_status`: `ok`, `error` or `rate_limited`, how many results, a plain-words reason without URLs or keys) and shown under the run in the Runs list ("OpenAlex could not be searched: OpenAlex refused the request (HTTP 401): API key not found. Get a free key at …", the server's own `message`, one line, at most 300 characters). The log line is an error, and a run that found nothing while a source failed ends as a failed run with the reasons. OpenAlex documents its key only as the `api_key` query parameter; it answers a key it does not know, also one sent as a bearer header, with HTTP 401 "Invalid or missing API key" (a key that was just created may need the activation link in OpenAlex's e-mail first).

**Where a run's log is.** Choosing a run in the Runs list shows its log under the papers ("Run log", read by cursor from `GET /v1/jobs/<run id>/logs`; a run in progress shows its live log above the form). The same lines are in the job-log file `<workdir>/operations/job-logs/<run id>.log` (mode 0600, scrubbed). They are job-store records, not container output, so a search of the pod log for "openalex" or "harvest" finds nothing.

**Contact address.** `OPENALEX_EMAIL` is read (a placeholder such as `you@example.com` is dropped) and sent as the OpenAlex `mailto`, with or without a key; OpenAlex says it does not raise the keyless budget. `CORE_API_KEY` is used only by the legacy `harvest_rsoxs` command-line harvester (CORE.ac.uk open-access lookup by DOI); the Harvester page does not use CORE.

## Backfill authors and abstracts

Papers harvested before the manifest stored authors and abstracts show "not stored" for them. `f2w-library-backfill` fills them from OpenAlex (rows with a DOI) or arXiv (rows with an arXiv id), changing only empty fields of `<category>/manifest.json` (never a stored value) and writing each manifest atomically inside the library directory. It never runs at startup. Rows without a DOI or arXiv id are reported as `unfillable`; nothing is guessed from a title.

```
f2w-library-backfill                         # dry run (default): no network, nothing written; counts what --apply would ask
f2w-library-backfill --apply --limit 50      # fetch and write at most 50 rows; rerun to continue (filled rows are skipped)
f2w-library-backfill --apply --category rsoxs --delay 2
```

In the pilot pod: `kubectl -n <namespace> exec deploy/<f2w-deployment> -- f2w-library-backfill --apply --limit 200` (it reads `F2W_LIBRARY_DIR` and `OPENALEX_API_KEY` from the container environment; `--library-dir` overrides the directory). Run the dry run first.

- **Egress (with `--apply` only):** `api.openalex.org` (key sent as an `Authorization` header) and `export.arxiv.org` (one request per 3 seconds), through the same hardened fetch helpers and `F2W_SAFE_FETCH_TRUST_PROXY` setting as the Harvester. No other host.
- **Output:** a JSON report with counts (`updated`, `not_found`, `no_data`, `failed`, `unfillable`, `already_filled`, `deferred`, `would_fetch`), `failed_codes` (for example `openalex_http_500`, `openalex_unreachable`, `arxiv_unreachable`) and library-relative PDF paths. Authors, abstracts and titles are never printed or logged. Exit status 1 means it stopped early (`openalex_no_key`, `openalex_quota`) or the directory is unusable; completed rows are kept.
- **After it runs** the Library list shows the new values on the next refresh (the metadata reader notices the changed manifest file).

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

## What the statuses mean

| Status | Meaning |
|---|---|
| Paywalled | genuinely not open access: OpenAlex lists no open-access copy (`is_oa` false, no open-access location). A person completes it: open the publisher in their own browser, save the PDF, drop it on the row |
| Blocked by network policy | the paper is open access, but this deployment's network policy refused the host (the detail names it, for example `blocked by network policy (private_address) at example.org`, or `proxy_refused` when the egress proxy's allowlist said no). Fix the allowlist or supply the PDF by hand; it is never counted as paywalled |
| Failed | open access, but the fetch failed for another reason (timeout, network error, an HTTP 401/403/5xx from the host, a link that is not a PDF, no direct PDF link); the detail gives the reason and the host |
| Downloaded / Filed / In library / Skipped / Dismissed | as before |

The detail never holds a URL path, query or key, only the host and the reason.

## Not used: content.openalex.org

OpenAlex offers cached full-text PDFs for about 60 million open-access works at `https://content.openalex.org/works/<id>.pdf`. It is **metered**: a key is required (a keyless request answers HTTP 401) and each download costs about $0.01 (OpenAlex's example-costs page lists the cached PDF at $10 per 1,000 calls; a free key's $1/day budget covers about 100). The PDFs keep their original copyright. **This is deliberately not used** (owner decision 2026-11-24): the Harvester does not request `content_urls`, does not use it as a candidate URL and has no pay-per-fetch route. Open-access papers are fetched only from the free links OpenAlex lists (repository and publisher open-access locations). Do not propose it again without a new decision.

## Planned: bioRxiv

bioRxiv is not searched yet. The plan is to search it through OpenAlex with `filter=primary_location.source.id:<bioRxiv source id>`; the source id is to be looked up first with `/sources?search=biorxiv` (not yet done). Nothing is built for this now. bioRxiv's own API has no keyword search, so it cannot be used as a source directly.
