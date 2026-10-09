# F2W / FORGE migration handoff - Draft v8

Start with AGENT_START_HERE.md and MIGRATION_PLAN.md; MIGRATION_PLAN.docx is the editable Word counterpart. D34 grants full application access without sign-in in :local. D30 retains native Tiled authentication and role checks only for :als-dev and :als-prod.

AUTHORIZATION_SPEC.md is the access contract. Local actions/approvals use local-operator audit; ALS uses verified Tiled users/admins. Both use the configured backend Tiled writer separately. Trusted server configuration selects the mode; no request can turn ALS into local_trusted. Local launcher binds to loopback by default.

Read the existing deployment, JSON/Git publication, source/UI compatibility and source-to-target port specifications. T25 and the binary inventories now cover no-login local capabilities plus ALS identity/role enforcement. Unrelated requirements and unresolved O03/O06/O07/O08 decisions remain unchanged.

CHANGES_V8.md and OWNER_CLARIFICATIONS_V8.md describe this revision. Earlier change/owner-note files and revision logs are historical. Source maps retain archive scope. No application test, Git commit, image build/publication, deployment, FORGE merge or human-gate approval occurred through this plan edit. VALIDATION_REPORT.json states the checks actually performed.
