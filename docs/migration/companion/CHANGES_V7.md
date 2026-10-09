# Changes in Draft v7

- D30 closes O05: native Tiled sign-in, server-verified admin role, chat-only non-admin users. Ingestion, approval, publication and graph edits use the shared application guard; the separate F2W admin account remains the technical writer.
- D31 makes Git the approved snapshot version channel. Production receives the exact committed JSON validated on staging, not a fresh extract. Git-tracked snapshots remain excluded from images; private paper data remains mounted/out of Git.
- D32 scopes T15 remote work to owner/Alex coordination. No FORGE branch is invented; patch/test status and merge status remain separate.
- D33 records owner-confirmed Graph-enabled 0.2.19 central servers and admin-writer coordination. Historical fetch failures are retained only as historical evidence; artifact/connection/permission tests remain required.
- Added AUTHORIZATION_SPEC.md, GIT_KG_PROMOTION_SPEC.md and FORGE_INTEGRATION_HANDOFF.md; updated main plan, deployment/Ansible/CI/JSON/UI/content guidance and machine-readable state.
- Added T25A-C identity/role work and T26A-C Git release/promotion work, explicit H1/H2/H4/H5/H6 joins and mandatory proposed binary cases.
- Prior source-file/port catalogs are retained without claiming a new source revision. No application code, Git history, registry or central service was modified.
