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
