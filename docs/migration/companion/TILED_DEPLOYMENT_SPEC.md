# Required Tiled deployment and lifecycle contract

**Draft v8. Proposed configuration names and new files are design, not existing APIs.** D13-D16 settle hosting, test-server ownership and replacement of Splash. The owner has pinned Tiled 0.2.19. This specification does not invent new mutation endpoints.

## 1. Two supported modes

| Mode | Application responsibility | Prohibited implicit behavior |
| --- | --- | --- |
| Managed | Provision/configure an application-owned local Tiled Graph; use persistent app data; explicit start/stop/status/readiness; document restart and credentials. | Starting a server at Python import, deleting persistent data on normal stop, sharing production credentials, exposing a raw unauthenticated Tiled admin endpoint. D34 permits no-login F2W application access, not disclosure of service credentials. |
| External / central | Connect to explicitly configured endpoint with caller/service scope; discover/verify capabilities; use required ACLs and F2W literature ownership. | Starting/stopping/upgrading central Tiled; silently creating privileged namespaces; falling back to a different graph after denial. |
| Disposable test profile | Use the same provisioner with temporary owned storage, unique ports/identities, deterministic fixtures and teardown receipt. | Reading central URL/keys from ambient settings; wiping a shared test database; calling a live catalog because a fixture failed. |

Managed mode belongs to :local; :als-dev connects to https://tiled-staging.als.lbl.gov/ and :als-prod to https://tiled.als.lbl.gov/. The ALS variants do not bundle or start a managed server. Managed and external are alternatives for a configured connection, not automatic replication. A deployment may be hosted alongside FORGE, but F2W literature ownership and FORGE operational ownership remain distinct. Namespaces identify logical scope. F2W imports as the configured admin account under application policy; do not claim per-user Tiled enforcement constrains that admin. Test actual reader/owner roles separately where used.

## 2. Proposed placement

- `app/modules/deployment/tiled_config.py`: validated managed/external configuration and non-secret instance identity.
- `app/modules/deployment/tiled_lifecycle.py`: explicit `start_managed`, `wait_until_ready`, `managed_status`, `stop_managed` and owned-test cleanup operations.
- `scripts/tiled_server.py`: operator CLI facade over these functions; not an unrestricted agent shell command.
- `tests/fixtures/tiled_server.py`: fixture using the same lifecycle path.
- `app/modules/graph/backends/tiled.py`: graph read/write/effective-record adapter; shared by both modes.

Transport, authentication, corpus ownership, private source store and lifecycle are independently configured. Do not make a new graph library, new runtime or new provider to support local hosting. The full release must install and demonstrate managed mode; a mocked graph is not sufficient.

## 3. Version and permissions

Owner-confirmed: the target Tiled supports required per-owner writes, and the Tiled team knows about old-UI editing compatibility. Do not reopen these as feasibility questions. Use tiled==0.2.19 for the reviewed managed/test build and compatible client lock. H1 records package hashes/image digest, schema/capability response and ACL configuration. O04 is resolved; do not choose another version or use a floating latest. Check that build, rather than assuming an old donor note about create-only properties describes every supported deployment.

Use supported mutation semantics where available. Where values require revision/append patterns, preserve a stable external identity and effective-record projection for the existing frontend. Edits, relationship changes and code-snippet links must read back correctly. Reject unsupported semantics clearly; never fake an upsert, silently disable editing or route back to Splash.

## 4. Test and teardown ownership

The application creates disposable instances, waits for readiness and initializes fixtures with test-only administration. Test the selected importer path with a dedicated test admin credential, plus separate reader/owner identities for server ACL checks. The importer must reject unauthorized scopes even though its admin credential could write them. Browser/agent arguments never receive the admin token. Record a per-instance ownership manifest before allowing teardown. Normal stop retains managed data; disposable cleanup removes only paths/processes/containers owned by that test invocation. Cancellation/startup failure must not leave dangling shared credentials or target the central server.

A disposable server also exercises the external-connection mode by connecting through its configured URL rather than through the managed lifecycle API. Production-central smoke checks are separate, explicitly authorized and normally read-only. Do not treat tests on a disposable same-build instance as proof of central deployment ACL configuration; verify the intended central configuration at its deployment gate.

## 5. Required binary integration checks

- Managed startup/readiness/stop/restart succeeds; normal shutdown preserves graph contents.
- External mode connects without invoking provision/stop/upgrade hooks.
- Test instances use distinct storage and ports; failed startup cleans only owned resources.
- The admin-backed F2W importer writes only an approved literature plan and rejects out-of-scope/FORGE mutations at the application layer. Separately verify normal reader/owner ACLs without claiming those ACLs restrict an admin credential.
- UI node edits, relationship changes and snippet upsert/unlink preserve IDs and citation/bookmark readback.
- Graph edits and rollback leave FORGE-owned sentinel entities, links and provenance unchanged.
- Revoked/denied credentials do not fall back to privileged keys, empty-success responses or another server.
- Partial reads block destructive planning; interruption/replay preserves scoped receipts and audit.
- Graph projections and exports do not expose agent-private paper text/crops to user-readable scopes.

## 6. Splash disposition

Splash is already replaced by Tiled Graph. Port any remaining behavior from old source paths and UI enums to Tiled, but add no service dependency. Do not assume JSON contains every historical edit or that historical data is lost: if the actual environment supplies a legacy-only export, inventory it and request an owner-approved import plan before deleting it. This conditional safeguard is not a mandatory project to recover a server the owner says is retired.

T20A blocks H1; T20B precedes required T08 real-server checks and H4. H5 requires crash/rollback proof. H6 requires both modes and unchanged-UI editing proof under the approved source-disclosure policy.

## 7. Creation/import boundary and exact artifact verification

KG creation produces versioned JSON with no Tiled requirement. The adapter ingests that saved JSON into the selected 0.2.19 server; it performs no extraction, parser or model calls. Import receipts bind target identity, owner/corpus scope and snapshot hash. The same snapshot may be explicitly imported into another deployment with a distinct receipt; no automatic replication is implied. See JSON_KG_PUBLICATION_SPEC.md.

The central Graph-enabled 0.2.19 deployment is now owner-confirmed (D33), not independently runtime-verified here. Historical earlier drafting attempts to fetch the final 0.2.19 PyPI/tag/JSON endpoints returned cache misses; the retrievable public index showed 0.2.18 and 0.2.19rc2. Those historical results do not establish final-release absence or override D33. The receiving agent must obtain the requested exact artifact/build and record hashes. If it cannot, stop dependency approval; do not silently substitute rc2, 0.2.18 or an unpinned source checkout.

Verification references (not an instruction to replace the pin): https://pypi.org/project/tiled/ ; https://pypi.org/project/tiled/0.2.19rc2/ ; attempted exact release https://pypi.org/project/tiled/0.2.19/ . Checked during drafting; no install/runtime claim.

## 8. Three-image deployment and actor policy

DEPLOYMENT_STRATEGY.md and CI_CD_CONTRACT.md define the three tags, /fair2wise, mounted sources and protected image publication. D30/D34 settle user access: :local users have all application capabilities without sign-in; ALS requires native Tiled sign-in, admin privileges for ingestion/approval/publication/editing, and chat-only ordinary-user access. D27 keeps the separate technical F2W admin writer and requested_by/approved_by/executed_as audit. D31 commits reviewed JSON/manifests to Git; production imports the identical staging-validated artifact. Schema/JSON logic is preserved. Actual host/secret/backup references remain O07; AUTHORIZATION_SPEC.md and GIT_KG_PROMOTION_SPEC.md define implementation contracts.
## 9. Owner-confirmed central state and test boundary (v7)

The owner confirms both central endpoints run Tiled Graph 0.2.19 and acknowledges coordination for admin writing. Preserve the exact target; do not reinterpret historical package-cache misses as absence. Record actual artifact hashes, native auth/admin-resolution behavior, account/secret references and permitted target scope at implementation/deployment. This draft did not connect to the servers or verify those runtime facts independently. Separate end-user Tiled identity from the technical writer, and never use the writer's privileges to validate a user. The application-owned disposable fixture verifies local full access without user sign-in, then supplies real admin/non-admin identities to ALS-mode connect-only tests for role/revocation checks before H4/H6. Local backend writes still use the configured service identity.
