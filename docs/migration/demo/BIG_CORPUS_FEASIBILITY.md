# Big demo corpora: can they be published to the local Tiled and used? (measured 2026-11-10)

Owner decision 2026-11-10 (twenty-second set): the demo corpora are `rsoxs/v4` (22,012 things, 57,575 associations)
and `qwen3_235b_580papers/v1` (15,815 things, 20,096 associations). Measured on a THROWAWAY container `f2w-bigtest`
(port 8091, new volumes, image `f2w:local` built from `3a94cca`, no CBORG key, Colima VM 6 CPU / 32 GB), code commit
`ad03e9d`. The approvals were made by the measuring agent through `POST /v1/publications/<job>/approval` with the plan
hash, on the throwaway only (it held no owner data; the owner asked for the measurement). The owner approved nothing.
The throwaway container and both volumes were removed afterwards. The run was cut short at the owner's request, so
**browser (section 5), rollback-replacement and a re-test of the fixes in the image are NOT measured**.

## 1. Headline

| Item | Result |
|---|---|
| Adopt (dry run + real), rsoxs / qwen | 19.8 s + 18.7 s / 15.9 s + 14.6 s (mostly interpreter start) |
| Stage (plan), empty Tiled | rsoxs 4.9 s; qwen 54 s once rsoxs was in Tiled |
| Apply rsoxs (101,602 records) | about 41 min with the lease fix (stage nodes 6.0 min, stage links 27 min, promote 7.3 min, verify 26 s, activate 27 s) |
| Apply qwen (51,726 records) | 16.4 min (nodes 4.4, links 4.5, promote 3.0, revalidate/review 26 s each, verify 42 s, activate 43 s) |
| Seeding both | about 65 min of apply plus about 3 min of adopt and plan |
| Tiled catalog after both | 107 MB `catalog.db` (145 MB tiled dir), state volume 243 MB |
| Container memory | 0.57 GiB idle, 1.0-1.6 GiB during apply (one core at 80-100 percent), 1.8-2.2 GiB after reads and QA |
| Re-publish of rsoxs/v4 (no-op plan: 0 creates, 0 links) | plan 100 s, approve call 42 s, total 378 s |

## 2. What broke (product bugs)

1. **Publisher lease lost during import (blocked publishing; fixed).** The import guard only checked the
   `publish:<target>` lease (120 s) and never renewed it. Every 120 s the run died with `LeaseLost`, the job was
   re-claimed and resumed from the journal, and after the retry cap (5) it failed with `unavailable: ReadTimeout`
   (the resume re-reads the whole Tiled state). Journal progress survived each bounce, so a manual resume finished.
   Evidence: lease table sampled every 10 s showed the job lease renewed every 30 s but the publisher lease counting
   down to 0. Failing test `87e5752`, fix `8f76fb1` (renew every quarter lease; expired or taken-over still stops).
   The measured apply used the patched `publication.py` copied into the running throwaway container.
2. **Tiled scan cache never hits (not fixed).** `GraphSources._tiled_scan` stamps the scan with its start time and
   the TTL is 20 s, but one scan of 37,827 nodes and 77,671 links takes 20-29 s, so every request after the first
   rescans (and requests queue on the lock). Not fixed here; fix: stamp after the scan and make the TTL at least 3x the
   scan time (or env-configurable).
3. **Whole-Tiled reads on routine routes (not fixed).** `GET /v1/publications/pointer` (the `corpora` live view)
   took 88-96 s with both corpora in Tiled; stage plan, approval POST, review, revalidate, verify and activate each
   cost 25-45 s of Tiled reads. `scripts/demo/seed.sh` used `curl --max-time 60` and `smoke.sh` 30 s, which would
   fail on the pointer call; both are now 300 s. seed.sh waits were 180 s (plan) and 300 s (publish): now 900 s and 5400 s
   (env `DEMO_STAGE_WAIT_SECONDS`, `DEMO_PUBLISH_WAIT_SECONDS`).

## 3. Reads after both were published (before any read-side fix)

| Request | rsoxs | qwen |
|---|---|---|
| `GET /v1/graph/sources` (both calls) | 21.6 s, 19.6 s (15 KB; Tiled counts correct: 22,012/57,575 and 15,815/20,096) | same call |
| `GET /graph?source=tiled:<c>` first (scan cached) | 2.8 s, 26.9 MB, 22,012 nodes, 57,575 edges | 1.5 s, 13.3 MB, 15,815 / 20,096 |
| same, later runs (cache expired) | 22-24 s | 22-29 s |
| neighbourhood hops 1 and 3, cache cold | 19-20 s | 19-28 s |
| neighbourhood, cache warm | 0.1-0.3 s | n/a |

No truncation header appears (both are under the 100,000-node cut; the only header extras are CORS and nosniff).
Neighbourhoods are cut to 500 nodes (e.g. hub `matkg:RSoXS` hops 1: 500 of its neighbours). Response sizes:
0.75-1.4 MB for 500 nodes.

## 4. QA without a model (`Answered without a language model`, 3 questions of `qa_questions.json`)

Chat reads the active pointer's corpus only (JSON record, not Tiled), 1,000 nodes returned (1-2 MB).

| Active corpus | Q1 | Q2 | Q3 |
|---|---|---|---|
| qwen3_235b_580papers | 17.7 s | 17.5 s | 17.1 s |
| rsoxs | 57.5 s | 40.1 s | 39.5 s |

Retrieval itself is small: offline profile of the same code on `matkg_rsoxs_v4.json` gave 0.9 s graph load and 0.3 s per
retrieval. The rest of each turn (17-57 s) was not located; the pointer read (90 s alone, concurrent calls overlapped)
is the suspect and was not confirmed. `ui_settings` in the chat body did not reduce it. Model time comes on top.

## 5. Not measured

Headless-Chromium load of the KG Viewer (default "Nodes: All" and a filter; `bc_browser.cjs` was written, not run),
rollback-replacement of one corpus with the other untouched (a per-corpus rollback answers 409
`no_previous_corpus_version` when the corpus has one version; it needs a second version first), the effect of fixes 2 and 3.
Expect the viewer to be slow: a 27 MB graph body, 22-29 s per cold read, and no cache hit.

## 6. Recommendation and go/no-go

* **Publishing both live on stage: NO-GO.** 65 minutes of apply, and any failure mid-way needs a resume.
* **Pre-seeding the day before: GO**, with the lease fix in the image (`8f76fb1`; without it the apply fails). Keep the
  volumes (`backup.sh`) and verify with `smoke.sh` (now 300 s timeouts; the pointer call alone takes about 90 s).
* **Stage use is risky until fixes 2 and 3 land:** each KG Viewer source switch, neighbourhood click and QA turn costs
  20-57 s. Prefer showing the small corpora live (`e2e_demo`, `bl1101`) and the big ones as backdrop, or fix the scan
  cache and the pointer read first and re-measure.
* `DEMO_CORPORA` default (now in `scripts/demo/seed.sh`):
  `rsoxs:matkg_rsoxs_v4.json:rsoxs/v4 qwen3_235b_580papers:matkg_qwen3_235b_580papers.json:qwen3_235b_580papers/v1`;
  `e2e_demo:matkg_e2e_demo.json:e2e_demo/v1` is added only when its file is passed with `--file`.
* Seeding takes about 65 minutes of apply (rsoxs 45, qwen 20 with the other in Tiled); the script header says so.
* Other demo docs (DEMO_CONTENT, RUNBOOK_DRAFT, mkdocs local-image) still name the old two corpora: not updated.
