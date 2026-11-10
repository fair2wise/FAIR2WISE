# Owner queue, 2026-11-09

One page that collects what only the owner can do or decide, with where each item is documented.
Nothing here is approved by listing it. Decisions already given are recorded verbatim in
`reviews/owner_decisions_2026-11-03.md` (sets 1-19); this file lists what is still open.

State of the code: `forge_merge` passes the whole suite (not-slow, slow), mypy strict, ruff, isort, the
binary-case check and the docstring-style gate. The `:local` image is rebuilt from it and runs both E2E
corpora (`e2e_demo/v1`, `bl1101/v4`) in its own Tiled.

## A. Actions that are yours (no agent can do them)

| # | Action | How | Where |
|---|---|---|---|
| A1 | Review the B4 pilot labels, promote them, then decide C1 | open the three review sheets under `/Users/david/.f2w/pilot_labels/<A\|B\|C>/review_sheet.csv`; accept, correct or reject each row (61 quotes were not located: correct or reject those); `python -m evals.binary.label promote --paper A\|B\|C --reviewer "<Full Name>"`; paper A first (no unparsed calls); B and C need their 5 unparsed calls relabelled with `--force` | `reviews/H4/review_packet.md`, `reviews/H3/b4_c4_free_model_run.md` |
| A2 | Record the H5 decision | write `reviews/H5/decision.json` (same shape as the H3 one) after reading the H5 evidence; name the real production-profile target for the T26C rehearsal | `reviews/H5/local_publication.md`, `reviews/H5/t26c_rehearsal.md`, `reviews/H5/pointer_model_memo.md` |
| A3 | Gate reviews the evidence now supports | H4 (C1, the four apply gates, B4), H5, H6 (T19E), H7 (T27) | `reviews/H4/review_packet.md`, `reviews/H6/t19e_closure.md`, `reviews/H7/sniff_findings.md` |
| A4 | Look at the UI in a browser | KG Viewer Source dropdown, Operations tab, Pending Approvals page at `http://127.0.0.1:8080/fair2wise/` | `reviews/H8/ui_graph_sources.md`, `reviews/H6/pending_approvals.md` |
| A5 | Infrastructure and credentials | a staging host (T24E), the GitHub publishing setup for ghcr (`O06_APPROVED=true`, environments, public package), the staging sign-in check (T12) | plan T12, T24E |

## B. Open decisions

| # | Decision | Options (first = recommended) | Where |
|---|---|---|---|
| B1 | Tiled neighbourhood cap (500 nodes today, silent truncation) | a setting with a default near 2,000 plus the truncation flag (the flag is being added; the cap value is not yet changed) / keep 500 / remove | `reviews/H7/neighborhood_cap_evaluation.md` |
| B2 | `OpenAlexClient` key as a URL parameter in verify, intake, identifiers, tools, resolver | move to the Authorization header like the harvesters / leave | P1d section of `reviews/H8/legacy_ports_plan.md` |
| B3 | Five legacy contract fixtures had their `available_cborg_models` value changed to free ids (shape unchanged) | accept / keep paid ids visible but refused | free-models merge 2c24aac |
| B4 | Free list contents: `lbl/gemma-4` class, `glm-5.3-flash`, `inkling-small`; the live price check is a best-effort guess (the listing carried no prices); Nomic ids are not enforced on the local embedding path | confirm / trim | `app/modules/free_cborg_models.json` |
| B5 | `scripts/write_nersc_env.sh` defaults `NERSC_USER` and `OPENALEX_EMAIL` to a personal username and address | make both required / leave | script |
| B6 | `splash_links/links.sqlite` (24.5 MB) is NOT represented by any committed KG (best match 25% of names) | keep as a read-only archive file / export once to a versioned MatKG JSON legacy snapshot (a small export tool is needed) | Splash packet Q4 audit |
| B7 | Splash window (Q1): one canary cycle ending at the first green soak report after cutover; Q9: implement the `storage_backend=tiled` diagnostic the plan proposed | accept / change; implement / drop | Splash packet sections 5-6 |
| B8 | Pointer memo Q4, Q6, Q7 were implemented as proposed (Save and the pointer follow a corpus rollback; the "last two pointers" toggle is per corpus; "superseded" means another version of the same corpus is live) | confirm | memo section 8 |
| B9 | Scratch files `<workdir>/kg.edit.<corpus>.json` left by editing a corpus other than the one on screen | clean up after use / leave | `reviews/H5/local_publication.md` |
| B10 | Edit reconciliation design: the `release_commit.json` record beside each exported version, `EditRecord.data_commit`, the two-step command, journal advanced only after a hash-checked read-back; `--confirm-commit` does not by itself block a later promotion | confirm; require `data_commit` before promotion? | `reviews/H5/t26c_rehearsal.md`, mkdocs `local-image.md` |
| B11 | T07B: V81 capability is `graph.publish` (decided); gates stay closed until a vocabulary is wired (H5); which `canonical_*` node fields a merge writes and whether publication projects them; who emits `canonical_merge` proposals | decide at H4/H5 | `reviews/H4/apply_adapters.md` |
| B12 | Pending Approvals: "admin" is read as the admin status the Tiled identity reports; curation cases show ids and metadata only; the cost-cap item has no one-click action (a form would be a new UI write path); only budget-paused jobs are listed, not provider-paused | confirm each | `reviews/H6/pending_approvals.md` section 7 |
| B13 | UI graph sources: V80 reuses `chat.read_support`; ALS has no Tiled source; flat JSON files are listed to every reader on ALS; one key added to the frozen persistence inventory | confirm each | `reviews/H8/ui_graph_sources.md` section 7 |
| B14 | T19E: surfaces that need new policy rows (`GET /v1/operations/catalog`, a single-paper summary route, a job submit/poll panel with a new capability flag); OP04 and OP05 are not implemented anywhere (they depend on PubTemp parsing and PyMuPDF) | add rows / defer | `reviews/H6/t19e_closure.md` section 3 |
| B15 | Docstring-style CI gate (owner kept it, 2026-11-09) and the 13 suspected-bug list (all fixed) | none | `reviews/H7/readthrough_F25_F38.md` |
| B16 | H8 removal list: the alias shims and facades (tiled_graph, kg_update, orchestrator_agent, debate_agent, admitted_chat, bl1101_ingest), the 54-row Splash deletion list, `scripts/app/modules/kg_rag_api.py` stale copy, the retire list of 16 legacy files (held for your review) | approve at H8 | `reviews/H8/legacy_ports_plan.md`, `reviews/H8/splash_retirement.md`, `reviews/H8/legacy_audit.md` |
| B17 | The `tiled_sim` write path for bl1101 versions past v9 (being aligned with the standard writer) and the P3 shims | none beyond B16 | `reviews/H8/legacy_ports_plan.md` |
| B18 | Moving `arxiv` and `pyalex` to the dev environment (a lock re-solve changes CI inputs; in progress) | confirm after the diff is shown | dependency branch |

## C. Still not started because it needs B or A first

- Opening the four apply gates (after A1 and B11).
- Real production-profile target rehearsal (after A2).
- Canary and soak (T16A), cutover and Splash deletion (T16B): H7 and H8 gates and real infrastructure.
- T15 (FORGE consumer): excluded by the owner for now.
