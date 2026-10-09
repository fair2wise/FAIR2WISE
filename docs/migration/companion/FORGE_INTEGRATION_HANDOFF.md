# FORGE consumer integration and merge ownership

**Draft v8. D32: the project owner works with Alex to merge into FORGE.**

## Repository and scope

Source donor: als-computing/forge_rsoxs_analysis, supplied archive recorded commit f1cd2ded95f26d8e43569dc94613b1dfde6b2045. This is the migration reference, not a claim about today's remote tip or permission to write a branch.

F2W migration code and reviewed JSON versions integrate into fair2wise/FAIR2WISE:forge_merge. That branch rule does not name a branch in FORGE. Before any remote write, the owner and Alex identify the FORGE target branch/base commit, approved integration branch/PR workflow and merge authority. The implementation agent must not guess these, push directly to main, force-push or merge on either person's behalf.

## Agent deliverable

Prepare a small reviewable patch/commit series in a separately approved FORGE working copy, plus an integration report containing:

1. Pinned F2W package/code revision and consumer interface version; exact FORGE base revision and changed callsites.
2. Adapters for literature search/read/evidence, paper ingestion/jobs/figures/review and scoped literature graph consumption, with donor routes mapped to F2W services. Reuse PORT_CATALOG.md and PAPER_ROUTE_INVENTORY.md.
3. Direct Python import, delegated F2W agent and FastAPI tests. Preserve existing FORGE analysis/simulation tools and keep one literature provider/parser implementation after cutover.
4. Caller context propagation: a local F2W deployment grants full local-operator application access without Tiled sign-in. An ALS F2W deployment independently verifies the caller or scoped grant and keeps non-admin users chat-only; a local client flag/grant cannot elevate ALS access. Neither mode exposes full papers publicly or confuses technical writer credentials with caller identity.
5. Environment/data pairing: production uses production F2W and its central graph release, development staging, local only by explicit connection. Bind evidence to the committed JSON/publication revision.
6. Dependency/install evidence, rollback/toggle plan, route/event/disclosure changes and remaining issues.

Record preparation, local test, PR submission, review and merge as separate statuses. Never mark 'merged' because a patch exists or an F2W gate passed. Local FORGE integration tests may use an unmerged patch, but the report must say so.

## Stops and sequencing

T15 can prototype against F2W public mocks after H1 and test real integration after the package/API exists. The owner and Alex review branch/merge details before remote submission. H6 reviews working integration evidence and records whether merge is pending. The final claim that FORGE has switched to F2W requires the coordinated merge/deployment to have occurred. If coordination is pending, stop that external cutover; independent F2W work can continue within approved gates.

H0-H7 remain F2W project-owner decisions. They do not override FORGE repository protection or its maintainers. No FORGE merge or central deployment was performed by this planning revision.
