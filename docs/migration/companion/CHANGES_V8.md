# Draft v8 changes: trusted local access

## Scope

D34 narrows D30's sign-in/role requirement to :als-dev and :als-prod. :local grants all application functionality without user Tiled authentication. This is the only product-policy change in this revision.

## Updated artifacts

- Main plan (Markdown and Word): decisions D27/D30/D34, architecture/access section, RequestContext/ApprovalRecord, worker policy, UI contract, T25 and H1/H2/H4/H6 acceptance.
- AUTHORIZATION_SPEC.md: complete local/ALS caller matrix, local-operator audit, shared enforcement, trusted profile selection and profile-bound jobs.
- Deployment, Tiled lifecycle, frontend, API/consumer and source-disclosure companion specifications: no local login prerequisite; unchanged ALS auth and unchanged source privacy.
- deployment_profiles.json, task_graph.json and editable Mermaid diagrams: profile-specific access, not an all-profile identity gate.
- Binary inventories: existing ALS tests retain IDs and are explicitly ALS-scoped; AUTH-09 tests ALS-prefixed sign-in. New LOCAL cases test no-login local functionality and profile isolation.

## Unchanged

Paper-port mappings, scientific semantics, model/QA constraints, rate limiting, versioned JSON/Git promotion, Tiled targets/version, admin service writer, private mounted papers, forge_merge, owner approval gates and Alex-coordinated FORGE merge.

## Validation

See VALIDATION_REPORT.json for current document/package checks. Application runtime tests, Git commits, image builds/publication and deployments were not executed. Historical v7 checks remain recorded separately rather than being relabeled as new runtime evidence.
