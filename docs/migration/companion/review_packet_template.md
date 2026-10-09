# Human review packet

Gate: H_
Approver: project owner
Status: awaiting owner decision

## Evidence
- Source/ref/commit locks and changed file list.
- Tasks completed; fixed decisions D01-D09 checked.
- Exact test commands/results and skipped/failed checks.
- Existing F2W LLM-class and QA-policy parity.
- Required Tiled/figure/SDK/FastAPI evidence where relevant.
- All paper-scope dispositions and unresolved items.
- Source-linked examples, resources/costs, risks and rollback.

## Requested authorization
State exact code/data/corpus/revision/cost scope.

## Owner decision
Decision, limits and date are left blank until the owner responds.
Changes outside that scope require a new review.


## v3 source / branch / UI evidence

- Exact F2W/Forge archive hashes and verified Git objects (distinguish archive comment from remote verification).
- forge_merge base and current commit; worktree/worker branch ownership; uncommitted/pre-existing changes.
- Frozen UI source/build hash; baseline/candidate backend and Tiled versions.
- B01-B36 and UI01-UI28 coverage changes; existing feature losses must be explicit blockers.
- Actual OpenAPI/payload/SSE differences and legacy token/ID mapping; screenshots/browser traces.
- Which tests used mocks versus real FastAPI and real disposable Tiled; paid/live tests separately authorized.
- Evidence that no source PDFs, keys, catalogs or runtime databases entered the commit or published bundle.
- Owner decision, exact approved scope and remaining blocks. Leave decision pending until the owner responds.


## v4 required evidence

- Binary required-case manifest and completed PASS/FAIL report; list every missing/error/skip explicitly.
- Tiled build and managed/external/disposable lifecycle evidence; real owner-permission/edit checks.
- Frontend-selected model/settings and coordinated rate-limit evidence (no secret values).
- Public-output source-disclosure tests and approved relevant exact-quote projection.
- Schema/version/overlay binding and preservation of current semantics.
- Knowledge Operations completeness/counting-unit/context-bound tests when applicable.
- Resolved O01/O02/O04 records; O03 decision/reference or explicit pending status. No forged approvals.

## Draft v6 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V6.md; source observations themselves remain unchanged. O03 remains pending alongside the deployment choices in OWNER_CLARIFICATIONS_V6.md, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.
