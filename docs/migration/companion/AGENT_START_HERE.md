# Start here: F2W / FORGE migration v7

Read MIGRATION_PLAN.md, DECISIONS.md and OWNER_CLARIFICATIONS_V8.md. Then AUTHORIZATION_SPEC.md, GIT_KG_PROMOTION_SPEC.md, FORGE_INTEGRATION_HANDOFF.md, the deployment/CI/Ansible/JSON specs and exact source/port/UI crosswalks. task_graph.json is the task dependency authority.

## Fixed requirements

Keep physical app/modules and integrate accepted F2W code/data changes into forge_merge. Preserve current scientific schema logic, F2W model classes/settings/QA constraints, allowed old UI/API/SSE workflows, all paper infrastructure and figures. FORGE retains experimental simulation/analysis. It imports F2W agents and can use FastAPI.

Access is profile-specific. :local is trusted full-access mode: every application action works without user Tiled sign-in or admin checks, using an explicit local-operator context. :als-dev/:als-prod require native Tiled sign-in; ordinary users are chat-only and verified admins may ingest/approve/publish/edit. The configured F2W admin account remains the backend writer. Never use its privilege to authenticate an ALS user, invent a local Tiled identity, or allow a request to select trusted mode.

Create frozen versioned JSON KGs, commit reviewed JSON/manifests to Git, and promote identical hash-verified artifacts that passed staging validation to production mounts for separate Tiled import. Do not rebuild production by re-extraction, bake tracked snapshots into Docker, or commit raw papers/private source payloads. All imports remain snapshot/target scoped with separate receipts.

Build/publish local, als-dev and als-prod images under /fair2wise. Local includes Tiled; ALS images use their respective staging/production servers without bundling a server. The owner confirms both central services run Graph-enabled 0.2.19. Ansible resolves FQDN, mounts and runtime secret references. No automatic data import on startup/push.

## First work packet and stops

Verify archive-recorded commit in real Git, prepare/resume forge_merge safely and execute isolated baseline tests, then present H0. T25A profile-specific access and T26A Git data-release contracts join H1. T25B protects the H2 unchanged-QA demonstration; T25C no-login local functionality plus real ALS roles and T26B committed artifact checks join H4; T26C proves exact-byte cross-environment import/rollback for H5/H6.

Prepare T15 as a separate FORGE consumer patch. The project owner coordinates the target branch, submission/review and merge with Alex. Never assume forge_merge is also the FORGE branch or push/merge without that coordination. Report prepared/tested/PR/merged states honestly.

## Parallel work

After H1/H2, domain ports, required figures, Tiled adapter/lifecycle, Knowledge Operations, authentication, Git artifact validation, API/consumer and independent evaluation can run in owned worktrees against frozen contracts. Shared DTOs, locks, schema policy, role mapping and publication state have one integrator. All required tests are binary; no model/agent self-approval.

O03/O06/O07/O08 details remain from the prior draft. Do not reopen Tiled identity selection, admin-only writes, Git JSON versioning or central Graph 0.2.19. No gate is passed just because this plan exists.
