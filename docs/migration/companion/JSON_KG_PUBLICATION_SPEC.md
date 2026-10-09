# Versioned JSON KG creation and Tiled Graph import

**Draft v8. D21 is an owner requirement.** F2W creates versioned JSON KG artifacts; Tiled Graph 0.2.19 is an ingestion/publication/query/edit target. The importer is required but is not the KG constructor.

## 1. Artifact flow

```text
private papers / parsed documents / existing extracted-term records
                         |
         extraction / evidence / canonicalization / current schema
                         |
              preserved json2kg graph construction
                         |
              versioned KG.json + manifest.json
                   /                       \
        local QA / operations        validate + reviewed import plan
                                             |
                                   JSON -> Tiled adapter
                                   no parsing or inference
                                             |
                              managed or central Tiled 0.2.19
                                             |
                        scoped queries / F2W and FORGE consumption
```

KG creation must finish with Tiled unavailable. D31 requires reviewed versioned graph JSON/manifests to be committed in the F2W repository. Production receives the exact committed artifact that passed staging binary validation, onto mounted storage, and imports without extraction or model calls. Local imports locally; ALS profiles use their designated central servers. The technical F2W admin writer acts only after a verified admin's scoped authorization. The full release still requires managed/external hosting, importer integration and authorized old-UI editing. A parser's paper.json is not automatically a KG snapshot. Operational state does not replace the committed scientific artifact.

## 2. Source anchors and eligible reuse

**F2W baseline B24:** app/modules/json2kg.py contains make_id (L85-100), copy_passthrough_slots (L61-73), normalize_publications (L134-181), make_code_snippet_node (L204-260), build_graph (L263-454), convert_terms_to_graph (L457-482), main (L505-533). Preserve their current identity and field-retention behavior, MatKG things/associations, code/source/license provenance, CLI contract and tests/test_json2kg*.py coverage. These line references are to the supplied rsoxs archive, not a remote tip.

**FORGE mechanics:** P/paper_staging.py, P/import_staged.py and P/ingest_journal.py, with exact symbols in J-01 to J-05 of the port catalog, supply staging, verification, replay and receipt patterns. docs/json-paper-staging.md explicitly separates parsing/extraction from later publication and says the importer uses saved artifacts without OpenAlex/parser/LLM calls. Port those mechanics through an F2W JSON adapter; do not load donor parsed-text packages directly as an alternative scientific ontology.

## 3. Proposed modules and functions

| Proposed destination | Responsibility / proposed API |
| --- | --- |
| app/modules/json2kg.py | Keep existing build_graph, convert_terms_to_graph and CLI facades; delegate only after parity. |
| app/modules/graph/snapshot.py | build_candidate_snapshot, validate_snapshot, freeze_snapshot; reuse current construction, no graph-server writes. |
| app/modules/graph/snapshot_manifest.py | SnapshotManifest, load_manifest, verify_snapshot_hash; sidecar metadata without breaking old JSON readers. |
| app/modules/graph/imports/tiled_json.py | plan_import_json, import_json_snapshot, verify_import_projection; consume frozen artifacts with authorized scope. |
| app/modules/graph/imports/receipts.py | ImportReceipt and operation ledger; reconcile retry against the exact target and snapshot. |
| app/modules/graph/edit_journal.py | Record approved UI edits and reconcile into the next version; preserve base revision and stable external IDs. |
| app/modules/graph/backends/tiled.py | Version-pinned transport, scoped queries, supported mutation/effective-record projection; no inference. |
| scripts/import_kg.py | Proposed dry-run/apply operator facade over the shared importer; no alternate implementation. |
| tools/graph.py + public/API facades | Typed create/validate/plan/request-publication actions through existing policy; no unrestricted graph mutation Tool. |

New names above are implementation proposals; only the source anchors are verified symbols. Use the existing registry and services, not a second framework.

## 4. JSON and manifest contract

Keep the graph payload's existing things/associations fields and compatibility behavior. Do not recreate IDs from labels under a new normalization rule. Use a sidecar when additional metadata would break a consumer. At minimum capture corpus/graph_id, schema identity and exact import fingerprint, extraction revision, corpus revision, graph_snapshot content hash, parent snapshot, creation run, validation report hash and relevant source/evidence checksums. Record contributing settings revisions rather than falsely assigning a multi-run corpus one model.

Hash the exact committed artifact bytes; define deterministic serialization before relying on regenerated byte equivalence. Publish final files atomically and never overwrite a frozen version. Changing records, provenance or scientific values creates a new snapshot. A mutable working copy remains explicitly draft. Snapshot creation does not itself grant Tiled publication approval.

A complete restricted JSON may include retained evidence excerpts or private evidence references. Apply source-access rules at import/public projection; do not publish full paper text through graph properties or an export endpoint. Relevant exact cited quotations are permitted, but a raw parsed-paper dump is not. Keep private fields under appropriate access controls or in the private document store with protected references. Disclose and test any projection differences without silently dropping source information.

## 5. Import contract and failure behavior

1. Validate path/artifact type, JSON structure, source hash, manifest, current schema and approved scope before contacting the target. Resolve request authority outside model arguments.
2. Create a dry-run plan for entities, edges, aliases/revisions, evidence and metadata. Bind approval to input hash, target instance, owner/corpus scope and expected base revision. A changed target/schema/file invalidates the plan.
3. Use Tiled 0.2.19 capabilities verified in the chosen deployment. Imported IDs/predicates map deterministically; retain JSON IDs and explicit remote IDs separately. Do not assume mutable properties, multi-request transactions or backend-native upsert that is not provided.
4. Journal every operation. Reconcile a crash after effects but before receipt; do not duplicate assertions/links on retry. Key completion by snapshot, adapter version and target/scope, not snapshot hash alone. A receipt for local Tiled cannot skip a central-server import.
5. Verify node/edge/evidence/link projection and content fields before writing the durable completed receipt and activating the graph/index revision. Counts alone are insufficient. No publication of a half-imported graph as the new default.
6. Keep immutable JSON untouched on import failure. Retry import without calling a model, parser, downloader or provider. Rollback/reactivation preserves earlier artifacts and audit history, and never modifies FORGE-owned operational records.

Reads and Knowledge Operations must declare the snapshot/publication revision they use. If a graph/index switch cannot be server-atomic, implement and test a revision manifest and pinned-reader policy; do not assert cross-store atomicity.

## 6. Existing frontend graph edits

Preserve edit UX, IDs, citations, relationship changes and snippet upsert/unlink for every local user without sign-in and for verified Tiled admins on ALS. D30 denies scientific graph/corpus edits to non-admin ALS users; D34 grants local-operator capabilities. Journal explicit approval, profile/mode and actor with the base snapshot/target revision. Reconcile into a new JSON version, validate and commit before cross-environment promotion. A local approval does not authorize an ALS import; do not overwrite a frozen version or erase acknowledged edits with an older import.

The exact scheduling/projection design is reviewed at H1. Admin edits may be acknowledged after durable journaling and the required scoped Tiled effect without rewriting an entire corpus synchronously. Acknowledge the true state, preserve immediate readback and avoid false cross-store atomicity claims. Every acknowledged edit must survive crash/recovery, next-JSON generation and re-import or fail visibly through the existing error contract. Admin status and effect scope are verified before execution, not accepted from browser fields.

## 7. Acceptance and parallel work

T23A freezes creation/manifest/import/edit contracts before H1. T23B wraps JSON construction after H2/T22B. The Tiled deployment team can proceed independently from the JSON builder once those contracts are frozen; T08B joins them with the importer. Extraction/figures produce records, not direct Tiled writes.

Required binary cases include build without Tiled; unchanged json2kg fields/IDs; immutable versions; importer has zero parser/model/provider calls; same-target retry without duplicate facts; separate-target receipts; tampered JSON rejected; correct edge/evidence/code-link readback; failure after partial effects; source-disclosure projection including allowed quotes; and edit preservation on re-import. H4 evaluates the versioned JSON product; H5 authorizes exact import/recovery; H6 proves UI/FORGE consumption and editing compatibility.

## Deployment and credential additions in v6

Snapshots, manifests, edit journals and receipts are deployment mounts, never shipped private image content. Add environment/target/admin-execution identity to receipt audit without placing the credential in JSON. Image promotion does not publish data. Require an explicit approved production import even when an equivalent snapshot was imported to local/staging. Ansible/container rollback and JSON/Tiled rollback remain different operations. DEPLOYMENT_STRATEGY.md defines profile defaults and mounted-state recovery.

## 9. Committed JSON promotion (v7)

GIT_KG_PROMOTION_SPEC.md defines immutable Git commit/path/hash selection, staging report binding, production-mount export, dependency checks and target-specific approvals/receipts. Record data commit separately from code/image digest and from any container tag. Source papers/full text/crops/secrets are not committed. Git-tracked graph artifacts remain excluded from every Docker layer and public static route. Application deployment does not authorize graph import; a staging admin token or receipt does not authorize production. No re-extraction or schema reinterpretation is part of moving a tested graph.
