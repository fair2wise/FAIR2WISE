# Design: papers the server cannot fetch, finished in the user's own browser

Status: implemented (steps 1 to 6, owner decisions of 2026-11-24 below). User guide: [Library tab, Finishing papers by hand](ui-library.md#finishing-papers-by-hand-the-manual-pdf-flow). It extends the manual helper that already existed on the Harvester's paywalled queue.

## Goal and hard rules

For a paper the harvester could not file, the user opens it with their own access, saves the PDF and drops it on the row. The server then files it as if it had been uploaded. This covers three cases with one flow: genuinely paywalled papers, open-access papers the deployment's network policy blocks, and open-access papers whose fetch failed.

The server never fetches paywalled content itself. It never stores, forwards or sees the user's publisher credentials, cookies or session. It never goes round a publisher's access control. The only thing that reaches the server is a file the user chose to upload.

## What exists today

| Piece | Today | Source |
|---|---|---|
| Queue row | publisher link, drop slot, Dismiss, only for status `paywalled` | `ui/src/app/components/library/harvest/HarvestResults.tsx` |
| Filing route | `POST /v1/library/harvest/candidates/{id}/pdf` (policy V78, `library.upload`), refuses unless the candidate is `paywalled` (`CONFLICT`) | `app/api/routes_library_harvest.py`, `Harvester._paywalled` |
| Upload checks | size cap `max_upload_bytes` (413), `%PDF-` magic (415), safe file name (422), sha256 duplicate (becomes `in_library`), atomic write that never replaces a file | `LibraryService.upload`, `app/api/upload_limits.py` |
| What a probe reads | PDF Info title, page count, Info text and the first pages' text, with a timeout; status `ok`, `encrypted`, `unreadable`, `timeout` or `error` | `app/modules/library/probe.py` (`ProbeResult`) |
| Statuses | `paywalled` (not open access), `blocked` (network policy, host named), `failed` (open access, fetch failed) | `app/modules/library/harvest.py` |

## 1. Open at publisher

A button on each row opens `https://doi.org/<doi>` (else the landing page) in a new tab with `rel="noopener noreferrer"`. The browser, not the server, makes the request; the user's login, VPN or campus network applies there.

Optional institutional library proxy: one deployment setting, no default, proposed name `F2W_LIBRARY_PROXY_PREFIX`. Value is a plain URL prefix that the target URL is appended to (the common "login?url=" form), for example `https://ezproxy.example.edu/login?url=`. It is validated at startup: `https` only, no user-info, no whitespace, at most 300 characters; anything else makes the API refuse to start with the reason. It carries no secret. The server builds the link (`open_url` on each candidate, computed from the prefix plus the target) so the page stays simple, and nothing in the server ever requests it. A host-rewriting proxy style is not supported in v1.

## 2. Drop zone and matching

The drop slot is offered on every row that needs a PDF by hand (`paywalled`, `blocked`, and open-access `failed`). The upload goes to the existing route, which gains a verdict:

1. Validate exactly as today: size cap, PDF magic, file name; a byte-identical file already in the library is reported as such and nothing is written.
2. Probe the PDF (existing sandboxed probe, same timeout). Collect every DOI in the Info text and the first pages' text (a strict DOI pattern, lower-cased) and the PDF title.
3. Verdict: **strong** when the candidate's DOI is among those found, or when the candidate has no DOI and the title matches closely (normalised tokens, similarity at least 0.9). **Weak** when only the title is similar (0.6 to 0.9), or the PDF has no readable text or DOI (encrypted, scanned). **Mismatch** when the PDF names a different DOI and not the candidate's.
4. Strong files at once. Weak and mismatch are **not** filed: the route answers a typed `409 match_unconfirmed` with the verdict and what was found (title and DOI of the PDF, no text). The UI shows both side by side and asks; "File anyway" repeats the request with `confirm=true`. A mismatch needs the same click plus a plain warning.
5. Filing records provenance `user_supplied` on the candidate (new additive column) and in the manifest row, status `filed`, detail "supplied by the user (strong match)" or "... (confirmed by the user)". The run's counts follow from the candidate statuses as they do now.

The matcher is a pure function (inputs: probe result, candidate) with its own tests; no network, no model.

## 3. Batch view

A "Needs a PDF" list for the open run, in order (tier, then score): the papers above, each with Open at publisher and its drop slot, and a counter ("12 left"). After a paper is filed the next one is focused. A multi-file drop area at the top accepts several PDFs at once (at most 20 files and the usual size cap each): every file is matched against all open candidates of the run by the same rules, shown in a table (file, matched paper, strong, weak or none); only strong matches file automatically, weak ones wait for a click, unmatched files are listed and never stored. Dismiss stays available per paper.

## What the server must not do, and how that is kept

| Rule | How |
|---|---|
| Never fetch paywalled content | no new outbound request in this feature; the proxy prefix is only a string the browser opens |
| No credentials or cookies | the link opens in the user's browser; the upload carries only the file; the prefix is validated to hold no user-info and is never logged with a query |
| No circumventing access control | no server-side proxying, no cookie import, no scraping of publisher pages |
| Uploads are validated like any upload | existing size cap, PDF magic, name rules, duplicate check, atomic write; the probe is the existing bounded one; nothing is executed |

Rights note for the docs and one line shown on the queue: text-mining rights for subscription content are the operator's responsibility to confirm; the tool only files what a person with access supplies.

## App side versus deployment

All app-side. One optional setting (`F2W_LIBRARY_PROXY_PREFIX`) read from the process environment; no image, mount, profile or egress change, no new dependency. Additive storage only: a `provenance` column on candidates, a field on the manifest row.

## Effort and commit order

About 4 to 5 working days with tests. Order: (1) the matcher and its tests; (2) the verdict and `confirm` on the route, provenance column and manifest field, allowing the three statuses; (3) `F2W_LIBRARY_PROXY_PREFIX` validation and `open_url`; (4) UI: row actions, weak-match confirmation; (5) UI: Needs-a-PDF list and multi-file drop; (6) docs, including the rights note.

## Not verified

The probe's quality on scanned or encrypted PDFs (those fall to "weak"); how an institutional proxy behaves for DOIs the proxy does not cover; whether the first-pages text reliably carries the DOI for older papers (archive.org scans often do not, which is why the title fallback exists).

## Owner decisions (2026-11-24)

The open questions below were answered: (1) the `login?url=` prefix form is enough for v1, no host rewriting; (2) a strong DOI match files without a click, with a visible confirmation and an Undo that reverses the filing cleanly; (3) "File anyway" is allowed on a mismatch, showing both DOIs and titles with a plain warning, explicit confirmation and provenance recorded as a confirmed mismatch with both DOIs; (4) 20 files per drop, each file its own request; (5) provenance is shown in the Library table as a small badge. Additions: a single-row drop is matched against all open candidates of the run (`other_candidate`, `to=<id>`), `supplied_by` is recorded next to the provenance, and the rights note stays on the queue and in the docs.

## Open questions (as asked)

1. Is the "login?url=" prefix form enough for v1, or is a host-rewriting proxy needed?
2. Should a strong DOI match file without any click, as proposed?
3. Should a mismatch be allowed at all with "File anyway", or refused outright?
4. Cap for the multi-file drop: 20 files per drop acceptable?
5. Should the manifest row's provenance field be visible in the Library table?
