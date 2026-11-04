# Owner decisions, 2026-11-03 (answers verbatim)

| Question | Owner answer |
|---|---|
| H4 / T17B: approve moving the 5 class-(a) files, porting the 4 class-(b) files and promoting 11 scripts per `reviews/H8/legacy_audit.md` | "Approve all three (Recommended)" |
| D3.22: R3 entropy secret-scan rule false positives | "Tune the rule (Recommended)" |
| No Tiled publisher configured: may a job report "skipped"; should the CLI coordinator get an explicit publisher | "maybe another indicator like "JSON available, Tiled unavailable" so it is clear" |
| Is the import-time conflict block sufficient for "no unresolved edits" | "Sufficient (Recommended)" |

Interpretation notes (agent, for the owner to correct):
- H4 answer covers the T17B moves, ports and script promotions only; it is not an approval of the 16-file retire list or the (r) reclassification (asked separately).
- Publisher answer: not "skipped"; a distinct, explicit status/indicator naming the state (JSON available, Tiled unavailable). Whether the CLI coordinator gets a publisher is not answered by this reply.

## Second batch

| Question | Owner answer |
|---|---|
| Approve retiring the 16 class-(d) files | "I'll review the list" (not approved; held) |
| Rollback V20 on replacement-importer deployments | "i dont understand what this is about" (not decided; explained, asked again) |
| Capability for paper job kinds | "jobs.admin only (Recommended)" |
| Default `F2W_FOLDER_ROOTS` for the :local image | "it should mount the papers/ folder in the repo" |

## Third batch

| Question | Owner answer |
|---|---|
| Rollback V20 (pointer-only) on replacement-importer deployments, after the plain-language explanation (return 409 and point to `rollback_replacement`, or allow) | "let's do this" / "rollback_replacement undoes the replacement properly, in order" (read as: V20 returns 409 there and callers use `rollback_replacement`) |

| Question | Owner answer |
|---|---|
| Partial Save recovery with a foreign link | "Keep it, finish the rest" |
| Retire-all versus purge `include_previous` | "Retire all; purge excludes previous by default (Recommended)" |
| Who may approve a plan that adopts legacy records | "Auto-approve on :local only" (note: this relaxes the earlier safe-plan definition, which excluded legacy adoption from auto-approval; ALS stays never-auto) |
| Move `scripts/harvest_*.py` to the hardened downloader | "Yes, move them (Recommended)" |

## Fifth set

| Question | Owner answer |
|---|---|
| T26A Q5: may the agent draft errata to plan 4.14, GIT_KG_PROMOTION_SPEC 4, JSON_KG_PUBLICATION_SPEC for E3 | "Yes, draft them (Recommended)" (draft for owner review; frozen docs untouched until approved) |
| T26A Q6: where release descriptors live | "Under docs/migration/data_releases/releases/ (Recommended)" |
| D3.23: handling of bl1101 v1-v7 `api_key` literal / `/tmp/` strings | "Scrub the literals from the files" |
| D3.23: matkg_rsoxs_v3 format, bl1101 v8/v9 serialization | "Rebuild them (Recommended)" |

## Sixth set

| Question | Owner answer |
|---|---|
| D3.24: adoption naming / nested copies | "Extend flat allowlist to legacy names" |
| Capability for the cost-control GET | "jobs.admin" |
| Unported enrichment / topic-sweep / PDF-parse handlers | "is that from forge or f2w?" (not decided; answered: the donor is FORGE's graph-enrichment agent, whose code is not in this repo; asked again) |
| Rebuild and restart the local container from the current tip | "Yes, rebuild and restart" |

## Seventh set

| Question | Owner answer |
|---|---|
| Test-suite speed-up (parallel run took the suite from 40 to 10 minutes; scale tests and subprocess tests dominate) | "Also shrink the scale tests" (over "Parallel default + slow marker (Recommended)" and "Parallel only") |

## Eighth set

| Question | Owner answer |
|---|---|
| T27 sniff findings: handling of the remaining rows | "Accept proposals as written (Recommended)" (each row's proposed disposition in `reviews/H7/sniff_findings.md` becomes the owner's decision) |
| T30: OCR engine behind the figure-OCR interface | "Docling's built-in OCR" |

## Ninth set (2026-11-04)

| Question | Owner answer |
|---|---|
| Live end-to-end run: harvest and capped paid extraction | "yes you can run a live harvest and extraction with cborg, but ONLY use a free model such as "cborg-chat". you are NOT ALLOWED to use a paid cborg model right now: https://cborg.lbl.gov/models/" |
| Tiled step of the end-to-end test | "I want both :local-only publish into container's own tiled, and import exported json through the existing json-to-tiled import path" |
| matkg_rsoxs_v4.json | "Merge v4 as a new version" |
| Five legacy files (tiled_graph, orchestrator/debate agents, download_agent, kg_update, bl1101_ingest) | "reclassify as retained, stay in place. add a note in the addendum we want real ports" |

| Question | Owner answer |
|---|---|
| OCR model files from modelscope.cn at image build | "Allow it for both local and CI" |
| bl1101 v8/v9 | "Scrub and re-serialize in place (Recommended)" |
| E3 spec errata | "i dont know what this means" (not decided; explained and asked again) |
| Apply adapter for accepted proposals | "Not yet" |

## Tenth set (2026-11-04)

| Question | Owner answer |
|---|---|
| E3 spec errata (edit frozen docs / leave frozen with a note / keep draft) | "KGs can go in the image that is fine" (not one of the offered choices: read as a confirmation of the E3 exception only; no frozen document is edited and the draft stays unapplied until the owner picks an option) |
| SaveTarget on :local | "Wire lazily after the first :local publication (Recommended)" |
| Corpus scope default on the :local image | "Set none; name one per run (Recommended)" |
| Library-to-publish route/button | "Not now; do the E2E with the existing steps (Recommended)" |
