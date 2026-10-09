# Ansible deployment handoff

**Draft v8. Contract for the ALS deployment repository; hostnames/roles/secret backend are not supplied.**

## Fixed environments

- als-dev image -> `https://tiled-staging.als.lbl.gov/`.
- als-prod image -> `https://tiled.als.lbl.gov/`.
- Both expose the application at `/fair2wise` on the separately configured FQDN.
- Neither profile provisions, upgrades, stops or stores a central Tiled server.
- Reviewed JSON KG snapshots/manifests are committed in the F2W repo; export the exact staging-validated commit/path/hash to deployment mounts, then explicitly import to the selected central target as the F2W admin writer. Production does not rerun extraction.

## Required deployment inputs

| Input | Required meaning |
| --- | --- |
| environment | als-dev or als-prod; validated against target endpoint |
| image_ref | Approved immutable image digest and profile; retain friendly tag for humans |
| fqdn / trusted hosts | Inventory-provided external name; not an image build argument |
| base_path | /fair2wise; must match compiled frontend and gateway settings |
| ingress mapping | Host + path boundary, TLS termination, trusted proxy hops and path forwarding contract |
| bind network / ports | Container ingress reachable by authorized proxy, not raw backend/Tiled admin exposure |
| Tiled URL / expected version | Environment-fixed target, verified 0.2.19 contract, TLS validation and any required CA mount |
| Tiled admin secret reference | Runtime-only credential, environment-separated, no browser serialization or log value |
| read credential / authentication | Native Tiled sign-in and verified user/admin identity on this environment; read scope must match caller permissions. The service writer is separate, not per-user impersonation |
| private mounts | Host sources for papers, intake, parsed artifacts, JSON, jobs/checkpoints/audit and caches |
| UID/GID and file access | Deployment-created directories with least required permissions; gateway cannot serve them |
| model config/secrets | Existing F2W settings and provider classes; no profile hardcoded paid-model switch |
| resource controls | CPU/RAM/process/restart/job-drain limits based on tested workload |
| backup integration | Owner, destination, retention, consistent backup method, RPO/RTO and restore evidence |
| UI write policy | D30: verified Tiled admins can ingest/approve/publish/edit; non-admins are chat-only; recheck server-side |
| KG data release | Repository + immutable data commit + allowlisted paths + snapshot/manifest hashes + staging validation reference |

Proposed variable names may be prefixed `fair2wise_` in the existing role; do not create a second role tree until the owner identifies the deployment repository/conventions. Tiled URLs and the application FQDN are different configuration values.

## Deployment sequence

Validate profile/endpoint/digest/base path and mount permissions; resolve runtime secret references without logging their values; take the required state/config backup; quiesce/checkpoint in-flight jobs; replace the container by approved digest; wait for liveness and capability-specific readiness; check the actual HTTPS/FQDN/fair2wise path, UI assets, settings, SSE and authenticated read path; leave production data imports as a separate approved operation. Do not run a real central write as an automatic health check.

The edge forwards /fair2wise unchanged to the internal gateway under the recommended contract. A different existing proxy contract is allowed only if explicitly reviewed and tested end to end. Preserve external HTTPS in generated links while trusting only known proxy hops. Same-origin UI/API is the default; any CORS allowlist lists origins, not paths. For these ALS profiles, Tiled is the selected user identity/admin authority; reject local_trusted mode even if requested in runtime overrides. Configure its verified native sign-in/session flow and safe return paths under /fair2wise; do not assume OAuth2-proxy or introduce a separate maintainer allowlist.

## Rollback and private storage

Retain the previous digest and configuration, validate state-version compatibility, and restore the previous container without deleting mounts. A JSON/Tiled data rollback is a different versioned operation. A mounted volume is durable storage, not a backup; test recovery on a new disposable host/instance using consistent backups and recorded receipts. Central Tiled backup responsibilities belong to its operator, while this role protects F2W's private paper/artifact/JSON/state files. There is no promise of a specific backup product, schedule or retention until O07 is settled.

## Owner questions for H1/H7

Identify the existing Ansible role/repository, registry acceptance, host CPU architectures, approved secrets mechanism and actual host mount roots. Confirm tag promotion rules and the native Tiled auth implementation at H1; the admin-only user-edit policy is already settled. Record backup ownership/retention/RPO/RTO and restore evidence before H7. FQDN values may be supplied through inventory later; images must not depend on them.

## Owner-confirmed central services and remaining deployment references

The owner confirms staging and production run Graph-enabled 0.2.19 and acknowledges admin-writer coordination. Connection/role/readback checks still run under explicit authorization; failed historical public package lookup is not evidence that these services lack the version. Actual admin-account identifiers, issued secret references, operator contact/escalation path and supported login-provider configuration are deployment inventory inputs, not values supplied by this plan. Do not fabricate them or request secret values in a chat.

Verify Git-exported JSON hashes before production import; use a production-specific authenticated-admin approval and receipt. Image deployment and graph promotion remain separate. Mount private evidence dependencies through their authorized storage path; Git does not replace those files or their backups.
