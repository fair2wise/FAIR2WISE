> Historical record. Superseded where inconsistent by Draft v7 and OWNER_CLARIFICATIONS_V7.md.

# Owner decisions incorporated in Draft v5

The architecture remains literature-only, under app/modules/, on forge_merge, with backward compatibility for the existing F2W UI and its LLM/QA paths. All human gates remain unapproved until the owner evaluates implementation evidence.

| Topic | Resolved requirement |
| --- | --- |
| KG creation | Keep creating versioned JSON KG files. Implement a separate adapter that ingests those artifacts into Tiled Graph. JSON is a primary build artifact, not merely a cache/export. |
| Schema semantics (O01) | Preserve existing logic/classes/inheritance. Add explicit loading, validation and versioning. Do not split RSoXSMeasurement from ExperimentalTechnique or retype historic graphs in this migration. |
| Exact evidence quotes (O02) | Permit relevant bounded exact quotations with source citations. Full paper bodies/PDFs and original image crops remain agent-private. |
| Tiled version (O04) | Target 0.2.19 exactly. Resolve and record artifact hashes and verify capabilities; never silently substitute a release candidate or a floating latest. |
| Model settings (O03) | The owner asked what FORGE does; that question is answered by a source audit, not treated as approval of a new F2W policy. |

## FORGE answer and proposed F2W behavior

FORGE's audited bulk-paper paths pass the chosen model as a start-request argument, persist it in the ingestion journal and pass it through worker calls. Recovery reuses the saved request model but recomputes the pipeline fingerprint. An omitted model can still resolve through current server defaults; this is not a universal immutable settings snapshot.

The recommendation is to preserve the fixed explicit-model behavior, resolve and record effective nonsecret settings at submission, and require an explicit execution/settings revision for a changed-model resume. Rate limits, cancellation and authorization remain live. See FORGE_JOB_SETTINGS_AUDIT.md and JOB_SETTINGS_CONTRACT.md.

**One remaining question:** approve that recommended per-job model/settings rule for F2W?

## Application effect

Main plan, JSON publication spec, schema/content/deployment specs, task graph and binary cases are updated together. This revision creates no implementation commit, does not create forge_merge, and claims no application/runtime tests or approval outcomes. The Tiled version is an owner pin; independent final-release artifact retrieval was not successful in this drafting environment.
