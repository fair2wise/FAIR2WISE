# Ansible input contract (T24A)

**Status:** Proposed for H1.

This document defines the inputs the ALS Ansible deployment must supply to run `:als-dev` and `:als-prod`. The owner has not identified the Ansible repository, roles, inventory paths, secret backend or hosts (O07). This document therefore names *inputs and constraints*, not infrastructure. The `fair2wise_` variable prefix is a placeholder. Apply the existing role's conventions once that role is known (ANSIBLE_HANDOFF.md).

The image owns:
- the profile contract
- the gateway
- the supervisor
- health checks
- JSON build and import

Ansible owns:
- host, FQDN, TLS and edge proxy
- image digest
- mounts
- secrets
- service identity
- resource and restart settings
- backups

## 1. Identity and release inputs

| Variable (placeholder) | Required | Meaning / validation |
| --- | --- | --- |
| `fair2wise_environment` | yes | `als-dev` or `als-prod`. Must match the image's `f2w.profile` label and the Tiled URL below. |
| `fair2wise_image_ref` | yes | `ghcr.io/<org>/<repo>@sha256:<digest>`. Roll out by digest; the tag is for humans only. The release manifest supplies it (CI_CD_CONTRACT.md). |
| `fair2wise_image_tag` | info | `als-dev` / `als-prod`. Never the deployment key. |
| `fair2wise_previous_image_ref` | yes | Retained for rollback. |
| `fair2wise_kg_release` | when importing | Repository, data commit, allowlisted paths, snapshot and manifest SHA-256, and the staging validation reference (D31). Not used by image rollout. |

## 2. Network, FQDN, TLS and proxy

| Variable | Required | Meaning / validation |
| --- | --- | --- |
| `fair2wise_fqdn` | yes | External hostname, from inventory. Never an image build argument. The images must work under a test hostname without a rebuild. |
| `fair2wise_trusted_hosts` | yes | Hostnames the API accepts for generated links. Never derived from an untrusted `Host` header. |
| `fair2wise_base_path` | yes, fixed | `/fair2wise`. The entrypoint rejects any other value. |
| `fair2wise_edge_prefix_mode` | yes | `preserve` (recommended: edge forwards `/fair2wise/...` unchanged). The alternative `strip` is allowed only after its own review and end-to-end test. Never strip twice. |
| `fair2wise_tls_termination` | yes | Where TLS ends (edge proxy). The container listens on plain HTTP `:8080` on a private network. |
| `fair2wise_trusted_proxy_cidrs` | yes | Proxy hops trusted for `X-Forwarded-*`. The in-container gateway forwards to the API over loopback only. |
| `fair2wise_container_port` | fixed | `8080`. The host mapping is reachable only by the authorized proxy. No published `8090`, `8765` or `11435`. |
| `fair2wise_proxy_timeouts` | yes | Edge read/send timeouts of at least the gateway's 900 s for SSE (`docker/nginx.conf:22-23`). Buffering off for `/fair2wise/api/`. |
| `fair2wise_max_body_size` | yes | Upload limit for approved PDF intake. Not yet specified by the owner. |
| `fair2wise_cors_allowed_origins` | optional | Empty by default (same-origin). Origins only (scheme/host/port), never paths. No wildcard with credentials. |
| `fair2wise_tiled_url` | fixed per env | `https://tiled-staging.als.lbl.gov/` (als-dev) or `https://tiled.als.lbl.gov/` (als-prod). Validated, not free-form. |
| `fair2wise_tiled_expected_version` | yes | The exact `library_version` string recorded at H1. See the ADR: the 0.2.19 artifact is currently unresolved. |
| `fair2wise_tiled_ca_bundle` | optional | CA bundle mount if ALS TLS needs it. TLS verification is never disabled. |
| `fair2wise_tiled_signin_return_paths` | yes (T25A) | Allowed return URLs under `https://<fqdn>/fair2wise/` for native Tiled sign-in. Depends on the providers `GET /api/v1/` advertises on the target server. |
| `fair2wise_egress` | yes | Allowed egress to the Tiled URL and the configured model endpoints. CBorg needs IPv6 per current source (`compose.yaml:22-25`; `scripts/start_agent_backend.sh:134-172`). Verify deployed egress; do not assume it. |

## 3. Mounts

Container paths are fixed by the image. Host sources are Ansible inputs.

| Container path | Mode | Owner (proposed UID:GID 10001:10001) | Contents | Backup class (O07) |
| --- | --- | --- | --- | --- |
| `/var/lib/fair2wise/papers` | `ro` (or `rw` only if the ingest mode writes to it) | readable by UID | Source PDFs and source manifest (D28) | Private source, required |
| `/var/lib/fair2wise/incoming` | `rw` | UID, 0750 | Authorized uploads and downloads; quota-bounded | Transient, policy TBD |
| `/var/lib/fair2wise/artifacts` | `rw` | UID, 0750 | Parsed text, tables, crops, private evidence. Never web-served. | Private, required |
| `/var/lib/fair2wise/kg` | `rw` | UID, 0750 | Versioned KG JSON, manifests, edit journals, import receipts. Frozen files are immutable. | Required, consistent with receipts |
| `/var/lib/fair2wise/state` | `rw` | UID, 0700 | Jobs, checkpoints, sessions, approvals, audit (single writer) | Required, consistent snapshot |
| `/var/lib/fair2wise/cache` | `rw` | UID, 0750 | Rebuildable caches. May contain source-derived data, so private. | Optional |
| `/var/lib/fair2wise/source_index` | `ro` | readable by UID | Source-RAG index (H0 E4). Pointed to by `HYBRID_RAG_LITERATURE_INDEX` / `HYBRID_RAG_OPS_INDEX`. Only needed when source RAG is enabled. | Rebuildable or private |
| `/run/secrets/tiled_writer` | `ro`, 0400 | UID | F2W admin-account credential for this environment (D27) | Managed by the secret backend |
| `/run/secrets/<model provider>` | `ro`, 0400 | UID | For example a CBorg key. Name follows the existing F2W settings. | Secret backend |
| `/var/lib/fair2wise/tiled` | **must not exist** on ALS | n/a | Local-only | n/a |

**Rules:**
- Ansible creates the directories and their ownership.
- The container validates ownership and mode at startup and reports the required fix. It never `chown -R`s a host tree.
- A mount is not a backup.

## 4. Secrets

These are references only. The plan supplies no values or account names.

| Reference | Rule |
| --- | --- |
| `fair2wise_tiled_writer_secret_ref` | Per environment. Production and staging use different credentials. Delivered as a file. Never passed as an env var value in the inventory, an `ARG`, a `VITE_*` variable, logs or a Settings response. |
| `fair2wise_tiled_writer_principal_id` | Non-secret identifier recorded as `executed_as`. Supplied by the Tiled operators. |
| `fair2wise_model_secret_refs` | Existing F2W provider settings. The profile does not hard-code a paid-model switch. |
| `fair2wise_secret_backend` | O07. Unknown; do not assume Vault, ansible-vault or SOPS. |

Production keys never reach a development workflow, a pull request or a local image.

## 5. Resources and restart

| Variable | Meaning |
| --- | --- |
| `fair2wise_cpu_limit`, `fair2wise_memory_limit`, `fair2wise_pids_limit` | Set from the tested workload. No numbers have been measured yet. The torch/sentence-transformers stack (`requirements/semantic.in:4-6`) sets the memory floor. |
| `fair2wise_restart_policy` | Container restart on non-zero exit, with backoff. The in-container supervisor handles per-service restarts. |
| `fair2wise_stop_grace_seconds` | At least the job drain/checkpoint timeout, so SIGTERM can drain before SIGKILL. |
| `fair2wise_worker_concurrency` | Existing F2W settings (for example `F2W_WORKERS`, `scripts/start_agent_backend.sh:47,128`). No invented quotas. |
| `fair2wise_log_driver` | Must not ship secrets or private paper text. Log redaction is the app's job; retention is Ansible's. |

## 6. Deployment sequence

The binding sequence is ANSIBLE_HANDOFF.md, "Deployment sequence". Its verification points as they apply to these inputs:

1. Validate environment, digest and Tiled URL, plus the mounts and secret file readability. Never log secret values.
2. Take a backup of state and config. The procedure is O07.
3. Quiesce: stop admitting heavy jobs, drain or checkpoint.
4. Replace the container by digest.
5. Wait for gateway liveness, then API health, then the Tiled capability checks C1-C4. C4 uses the writer principal and only reads.
6. Verify `https://<fqdn>/fair2wise/` assets, `/fair2wise/api/settings`, an SSE stream, and the authenticated read path.
7. Never run a central write as a health check. Data import is a separate approved operation.

## 7. Questions the infrastructure owner must answer (part of O07)

- Ansible repository and role name; branch policy.
- Edge proxy product and its path-forwarding behavior (preserve or strip).
- Host CPU architecture (amd64 or arm64; also O08).
- Secret backend and how secret files reach `/run/secrets`.
- Host mount roots, UID/GID mapping, quota.
- Backup owner, destination, retention, RPO/RTO, encryption.
- The writer principal identifier for each environment, and the escalation contact for the Tiled operators.
- Whether `/fair2wise/model-api` is exposed on ALS.
