# Break report (owner away from 2026-11-26)

One page for the owner's return. The running detail is in the [break log](break-log.md); this page is the summary the
owner asked for: where `forge_merge` stands, what of the backlog was done, every decision taken on the owner's behalf,
every re-recorded baseline, the design pages waiting for review, a browser script for what is new, and the model calls used.

_Status of this page: kept current during the break; the CI line is updated after each `[ci full]` run._

## 1. Where `forge_merge` stands

| | |
|---|---|
| Pilot pin at departure | `987762a2` (run 45713088709) |
| Latest commit whose own `[ci full]` run executed every job and was fully green | **`bee874a6`** (run 45872367330: all 14 jobs success, the new browser job included; the nightly slow suite skipped as designed). Before it: `fdc2a6b6` (run 45728318670). |
| Latest pushed commit | `bee874a6` plus the docs commit that records its run (pushed with `[ci full]` so the head has its own full run too) |
| `FROZEN_UI_TREE` | `861e1592f380f6812a0b32d2425e918aa2cbf880` (re-pinned in `fdc2a6b6`; unchanged since: the browser suite lives outside `ui/`) |
| Deployment files vs `987762a2` | `docker/`, `deploy/`, `Dockerfile`, `pixi.toml`, `pixi.lock`: no change. No new dependency, no new egress host. |
| Re-pin recommendation | `bee874a6`. |

Jobs of run 45872367330 (`bee874a6`): guard, contracts, unit shards 1-3, quality, syntax/entrypoints, frontend build (both
bases), MkDocs, Tiled-backed suites, mocked Playwright, real-backend library browser smoke, Docling/coverage: all success;
slow suite skipped (nightly only).

Known red: run 45730388623 (`900751fb`, docs and a script only) failed unit shard 2 of 3 with exit code 1 and no
failing-test annotation; the shard is green locally from a clean export in CI's environment shape and green again in run
45737700087, so it was a one-off of that runner.

## 2. Backlog, item by item

| Item | State | Commit(s) |
|---|---|---|
| 1A-4 Repair relations job (no model), beside Find supporting text | done | `a221bb64`, `9e86d165`, `383719d8` |
| 1A-1 General aliases from papers; KG retry with model-proposed terms; pleco map removed | done | `c7c38249`, `cbd6d2d1` |
| 1A-2 Paper search order: KG's own registries first, else OpenAlex first | done | `c7c38249` |
| 1A-5 Passage coverage: factual clauses, OCR-tolerant quotes, table rows, pages without text | done | `2eee8f05`, `ad031fc9` |
| 1B Threshold suite 90/50/80, docs, skip empty pairs by sha256, continued-KG note, Tiled timings | done | `66d65516` (timings on the passages design page) |
| 1C Coverage block, Harvester schema wording, server-side cancel of generation, references from corrected record | done | `2ad27514`, `dc749bb1`, `2bca7f01` (coverage block landed before the break; references already flow from the corrected records) |
| 2D UI contract sweep generated from Python | done, no mismatches left | `f95b088a`, `339ec3ff`, `28062b02`, `fdc2a6b6` |
| 2E Real-backend Playwright smoke suite, `[ci full]` only | done (6 steps green locally and in CI run 45872367330) | `8161cbc6`, `bee874a6` |
| 2F Security review of the new routes, uploads, prompts, proxy prefix, workdir writes, logs | done, with four bounds added | `c9924738` (`security-review-2026-11.md`) |
| 2G Generalisation check on three unrelated domains, free model | done (13 calls) | `c9924738` (table on the schema-generation design page) |
| 3H-M Design pages only | written, awaiting review | `900751fb` (`design-tier3-2026-11.md`) |
| 4N Tidy: stream routes, placeholder key, worktrees | partly: key and worktrees done; V05 stream route kept (frozen contract), no Harvester stream route exists | `900751fb` |

## 3. Decisions taken for the owner

Each is in the break log with its reasoning; the list here is the index.

1. Repair relations refuses a bundle on a shipped schema (`not_user_schema`) rather than running as a no-op.
2. The previous generation of a repaired bundle lives in `previous/` inside the bundle; nothing outside it is written.
3. A relation whose object is not a term of the bundle is left as it was and counted `unknown_object`.
4. The KG retry with model-proposed terms runs for every graph source, once per turn, only after an insufficient first
   verdict and only when the KG has a vocabulary; a failed or non-JSON helper answer is ignored.
5. Domain word lists found and left in place, listed for the owner: `GENERAL_CATEGORY_ALIASES` and
   `GENERAL_RELATION_ALIASES` (shipped MatKG mapping), the RSoXS/ops wording of the chat judge prompts (frozen hashes), the
   repository host ranking of the download agent.
6. A table row counts as `located` in the coverage counts; the snippet carries `kind: table_row`.
7. No OCR pass for scanned pages; the pages without a text layer are counted and reported.
8. No relation evidence over table rows.
9. "80" in 90/50/80 is read as the share of stored passages that are the model's quoted sentence verified in the paper.
10. The real-backend suite runs without a disposable Tiled (Publish to Tiled stays with the T18C/T20B suites) and with
    `F2W_DISCLOSE_SNIPPETS=1`, as the pilot opts in.
11. `GET /v1/jobs/{id}/stream` is not retired: part of the frozen `/v1` contract; the owner's call.
12. The RSoXS KG is not rebuilt or swapped (a rebuild from the tracked terms file is a different, 4x larger release; see L).
13. The relevance bar is unchanged on small KGs; the behaviour and two one-line options are reported (break log).
14. The CI red on `900751fb` shard 2 was not "fixed" blind: not reproducible locally in CI's layout; re-run by the next pushes.

## 4. Re-recorded or newly pinned baselines during the break

- `FROZEN_UI_TREE`: re-pinned five times for UI changes (`383719d8`, `ad031fc9`, `dc749bb1`, `2bca7f01`, `339ec3ff`) and
  once for stable fixtures (`fdc2a6b6`); the pin in `GITHUB_READINESS.md` follows each.
- Evals binary case hashes regenerated twice after new routes (`486329c6` after V100, `ee51a633` after V101) with
  `import_companion`; nothing approved.
- New pins, not re-recordings: `ui/src/app/components/__fixtures__/ui_contracts.json` (generated from the real routes), the
  threshold suite's 90/50/80 numbers, the shipped-schema prompt pins already re-recorded before the break (CodeSnippet
  removal) and the QA-01 judge prompt hash (decision 6 of the owner's round).
- Route-policy counts: 85 local policies (V100, V101 added), 35 local-only on ALS.

## 5. Design pages awaiting review (nothing built)

`design-tier3-2026-11.md`: H numeric and aggregate questions (3 questions), I second model pass for relations without
evidence (3), J bioRxiv and other sources through OpenAlex (2), K extraction speed: where the 35 minutes go (a measured plan
offered, cap 60 calls), L the RSoXS rebuild report (2), M the rest of the standalone install (2). The security review has one
open point (`note_error` text may name a provider base URL). The T18C manual suite's decline expectation is stale since the
owner's round 2 (`stopped_by_user` -> `stop_insufficient`); re-record with that suite's own run.

## 6. Browser test script for what is new (in order)

On the re-pinned pilot, with the plecos library and KG:

1. **Extract page, "Continue existing KG", pick the plecos KG.** Beside Find supporting text there is **Repair relations**
   (user-schema KGs only; on a shipped-schema KG the button is absent and the route answers `not_user_schema`). Run it: the
   result line counts swapped, attributes, invalid, unknown-object and unchanged relations; `previous/` holds the earlier
   generation; a second run reports everything unchanged.
2. **Find supporting text on the same KG.** The result table has the new columns (aliases added, pages without text) and
   the skipped-searches note; the summary names pages with no text layer per paper. Rejected sentences are classified in the
   log (near match, table row, partial overlap, not in paper).
3. **Node cards.** Open a species node of the plecos KG: aliases from the papers appear with the name; a passage from a
   table shows as **table row** with its column header; OCR'd papers show the page's own wording.
4. **Chat on the plecos KG.** Ask a question with a common name the KG cannot answer directly: one retry with the model's
   alternative terms before any paper is offered; if papers are offered, the source line says which registry was asked
   first (the KG's own, else OpenAlex). Ask an unrelated question while an offer is pending: answered, offer dropped. Say
   "no" to a fresh offer: ended, no claim that the KG has no evidence.
5. **Schema panel.** Generate, then press **Stop** during a generation: the server stops after the call in flight
   (`stopped: cancelled`); nothing is stored. The Harvester's schema field reads "Schema to record with the run".
6. **Harvester.** Start a run; the Needs-a-PDF list shows paywalled, blocked and failed papers; drop a PDF: a strong match
   files it, a weak one asks first.
7. **Extraction run view.** The coverage block is the compact grid; a run on a user schema with a long schema shows the
   condensed-prompt notice.

## 7. Model calls

13 of the 400 allowed, all `lbl/cborg-chat` (the generalisation check, cap stated 24). Nothing else called a model.
