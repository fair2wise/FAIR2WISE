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

## Eleventh set (2026-11-06)

| Question | Owner answer |
|---|---|
| Approval of the E2E publication plan `job-9166b202380119cedfdf1dbc` (plan_sha256 `0c4fd3feb…`) | "You approve it in the UI" (the owner approves; the agent does not submit the approval) |
| E3 spec errata | "Explain it to me first" (not decided; the agent explains the draft in plain words, then asks again) |
| Caption assertions dropped from the real-weights parsing test | "Restore with a real PDF fixture" |
| Retire list of 16 legacy files | "Not now" (the list stays held, nothing is deleted) |

## Twelfth set (2026-11-06)

The owner reported the E2E plan `job-9166b202380119cedfdf1dbc` approved ("ok its done and approved"); it published `e2e_demo/v1` (269 entities, 126 links read back from the container's Tiled).

| Question | Owner answer |
|---|---|
| Second Tiled route | "let's import another existing graph to test the second tiled route" |
| UI graph sources | "in the UI we need to support reading both json KGs and Tiled KGs. Follow best practices and do what needs to be done" |
| Rollback rehearsal | "we can test the rollback rehearsal on the results from the end-to-end test after we promote and existing KG into Tiled. Then we can check to make sure that the rollback only touches one KG not the other" |
| E3 spec errata | "E3 errata. Change what you need to in order to allow KGs to ship with the image" (read as approval to apply the E3 draft entries to the frozen documents and to change the build context so KGs ship, per D3.24: flat `storage/kg/matkg_*.json` copies in, nested `<corpus>/v<N>/graph.json` copies out; papers, `storage/source_index/`, secrets stay out) |

Approvals of plans stay with the owner (each import/rollback plan is approved by the owner; the agent never submits an approval).

### E2E rehearsal record (2026-11-06)

Owner-approved steps on the `:local` container (approvals submitted by the owner with curl; the agent never approved):
`e2e_demo/v1` published (job-9166b202380119cedfdf1dbc, pointer v1); `bl1101/v4` published (job-1f6f9abbe056f354ebb80339,
pointer v2); rollback-replacement first attempt (job-de67da9f0e36f9367aa8eee6) quarantined at verify (noop-receipt bug,
fixed in d25a32f; Tiled unchanged); second attempt (job-59e3a33d4342333cd5f3dd67, same plan hash ab8af850…) succeeded:
pointer v3 = e2e_demo/v1, thin receipt receipt-fe1e4d0bc34c17088635, Tiled entities/links identical before and after
(691/1063; e2e_demo 268 final, bl1101 421 final; both markers unchanged). Owner's check "the rollback only touches one KG
not the other": confirmed (the plan's scope was e2e_demo only and bl1101's records and marker were untouched).

## Thirteenth set (2026-11-06)

| Question | Owner answer |
|---|---|
| T13 / H5 remaining steps | explained (owner sign-off in `reviews/H5/decision.json`; real-target T26C rehearsal needs the owner to name the target); no answer yet |
| T07B canonicalization apply adapters (previously "Not yet") | "go on this: T07B: canonicalization apply adapters" (read as: wire the apply adapters now, behind the existing approval gate; the C4 paid real-model rerun is still the owner's own run, no paid model is used by agents) |
| Parallel work | "finish T27's read-through, T17B's clean ports, T19E's loose ends" and "also work on T16B splash_links retirement" (read as: prepare the retirement, inventory and deletion list, safe code-side removals; no production cutover, which stays H8-gated) |

| Paid CBORG model run (C4 rerun, B4 labelling) | "for the paid cborg run, let's just use the best cborg model available that is free" (2026-11-06) |

| Suspected bugs B1-B13 of the T27 read-through (`reviews/H7/readthrough_F25_F38.md`) | "let's fix these bugs" (B11, B3, the dead `missing_ok`, the Tiled neighborhood ignoring `hops`, the stale `_skipped` reset, and the others listed there) (2026-11-06) |

## Fourteenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| Publication pointer model (memo `reviews/H5/pointer_model_memo.md`) | "Model C (Recommended)" (per-target pointer stays 'last activated'; add a per-corpus live-version view; rollback and the pointer read take an optional corpus) |
| P2 agent base for the orchestrator and debate agents | "LangGraph, via agent_runtime.admission (Recommended)" (LLM calls through admission and counted against curation budgets) |
| Fate of `splash_links/` at cutover (packet Q1-Q3) | "Delete, keep an archive tag (Recommended)" (tag the last commit containing it, delete at cutover after one canary cycle; its `_tests` go with it; the wire tokens stay) |
| Approval of a zero-operation rollback plan | "Auto-approve on :local only" (zero-operation rollback plans on the `:local` profile may skip the human approval step; every other profile and every non-empty plan still needs it) |

## Fifteenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| V81 capability (`POST /v1/curation/merges/apply`) | "graph.publish (Recommended)" (a real apply also needs `curation.approve` through `create_approval`) |
| Image `--kg-mode` default on `:local` | "Default to editable on :local" (UI Save becomes available as soon as a publication exists; the lazy target still answers the typed 501 before one; edits block the next publication of that corpus until reconciled) |
| H4 gate packet | "Yes, prepare it (Recommended)" (assemble from the C4 report, the provisional B4 binary report, T07B's design and the owner-gated items; nothing marked approved) |
| Legacy `splash` enum value after the compatibility window (packet Q8) | "Accept but alias to json/tiled (Recommended)" (the token is accepted as the editable-graph alias and never contacts a Splash service) |

## Sixteenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| UI: pending approvals | "I think we need another page in the UI left side bar for \"Pending Approvals\" that show up in :local mode and in the :als-dev and :als-prod images when an admin is logged in" (read as: a new sidebar page listing the items awaiting a human approval, visible on `:local` to the local operator and on the ALS images only to a signed-in admin; approvals go through the existing approval routes and role checks, never a new approval path) |

## Seventeenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| CONFLICT retryability default (audit `reviews/H5/conflict_retryability.md`) | "Flip the default (Recommended)" (CONFLICT non-retryable by default; the transient sites marked `retryable=True` explicitly; AST guard kept) |
| bl1101 `fetch.py` on the hardened getter (P3 gated item 1) | "Yes, move to hardened getter (Recommended)" (the two retained tests are rewritten to fake the hardened getter) |
| bl1101 snapshot writer for new versions (P3 gated item 2) | "Standard create_snapshot from v10 on (Recommended)" (v1-v9 bytes stay identical) |
| P2 budget accounting (flat 0.05 USD reservation; cost-control enabled switch) | "we should only allow free models via CBORG at the moment. We get 50 free usage credits per month (mileage varies per model) on CBORG, so I want to avoid using those" (read as a model policy: every CBORG call path accepts only the free LBL-hosted model ids and refuses any other id with a typed error; model pickers show only free ids; LLM use by the chat helper agents is minimised, with the deterministic path as the default and model calls opt-in; the flat reservation and the enabled switch stay as implemented) |

## Eighteenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| Pointer memo Q3: which corpus UI Save edits when several are live | "The corpus shown in the graph view (Recommended)" |
| Corpus removal and the target pointer under Model C | "Move the pointer to a still-live corpus (Recommended)" |
| T12 pilot size | "Yes, three papers satisfy T12 (Recommended)" (the three-paper pilot A, B, C satisfies T12 for H4; the 13 label cases rest on them) |
| T27 small items: docstring-style CI gate; 500-node cap on the Tiled neighbourhood route | "Keep the CI gate. I need more information about the Tiled neighborhood decision. Is that for testing, or during normal usage? I am tempted to forgoe the limits and maybe do a performance evaluation to determine if we need a cap" (CI gate kept; the cap is left as is pending a performance evaluation on the real graphs; not decided) |

## Nineteenth set (2026-11-09)

| Question | Owner answer |
|---|---|
| Pointer memo Q2: corpus-less rollback while several corpora are live | "Refuse when several corpora are live (Recommended)" (409 `corpus_required` naming the live corpora; a single live corpus works as today) |
| Splash packet Q5-Q7: remaining Splash-era defaults | "Flip all three now (Recommended)" (config.yml and .env.example default `graph_source` to json; the UI Settings text corrected; NERSC scripts default to json with the path a parameter; wire tokens stay) |
| Harvest politeness (arXiv 3 s spacing; honour 429 Retry-After) | "Add the 3 s arXiv pause and honour Retry-After (Recommended)" |
| OpenAlex API key use | "Yes, header-only when exported (Recommended)" (read from the process environment only, never `.env`, never logged, never in a URL) |

## Twentieth set (2026-11-10)

| Question | Owner answer |
|---|---|
| First target deployment and priorities | "OK so the first target deployment will be a locally run demo in 1 month (december 10 or 11) not on this machine, but either my mac studio or another one. So we must prioritize the remaining work around this (ignore :als-dev and :als-prod work/decisions for now) to show an end-to-end demo. If possible, we want to test against our tiled-staging.als.lbl.gov/ tiled server, but let's make sure everything else is squared away first" (read as: the `:local` profile on a machine other than the development one is the first target; ALS image work and decisions are deferred; a test against tiled-staging.als.lbl.gov is a stretch goal after the local demo path is solid; it is a reachability and credential decision for the owner, never done by an agent without it) |

## Twenty-first set (2026-11-10)

| Question | Owner answer |
|---|---|
| Library-to-publish route for the demo (supersedes the "Not now" of the tenth set) | "Yes: a 'Publish to Tiled' action in the Library KG tab (Recommended)" (export, adopt and stage the publication plan from one button on `:local`; the plan then appears on Pending Approvals for the owner to approve; no new approval path) |
| QA when the knowledge graph has no information | "we should still get a regular chat answer, but an honest sentence "the knowledge graph has no information on this" with the answer. but the chat shouldn't break if the LLM can still answer the question" (read as: when retrieval is insufficient the chat still returns the language model's normal answer, with an honest statement that the knowledge graph has no information on the question; it must not turn into a refusal or an error) |
| Demo machine checks | "I won't be able to test on the mac studio until next week when I'm back on site, so let's prioritize everything else first today" |

## Twenty-second set (2026-11-10)

| Question | Owner answer |
|---|---|
| Which corpus(es) the demo QA chat uses (after the live probe showed chat answers only from the pointer's corpus) | "let's move from the e2e_demo KG to the most complete RSoXS kg, as well as the 580 paper OPV KG" (read as: the demo corpora become the most complete RSoXS KG, taken to be `matkg_rsoxs_v4.json` (22,012 things, 57,575 associations, 246 source papers; adopts as `rsoxs/v4`; to be confirmed by the owner) and the 580-paper OPV KG `matkg_qwen3_235b_580papers.json` (15,815 things, 20,096 associations; adopts as `qwen3_235b_580papers/v1`); both pass the dry-run adoption; chat must answer over both, i.e. over all live corpora, with the corpus named on each citation; e2e_demo stays the small live-extraction example) |
| QA speed | "10 s is fine for now; revisit after the rehearsal (Recommended)" |
