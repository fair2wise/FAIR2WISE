# Deployment strategy: three image variants and one application contract

**Draft v8 | 8 October 2026 | Required deployment work, not deployed infrastructure**

This specification supersedes conflicting v5 deployment assumptions. D23-D33 below are the owner's requirements. Proposed file names, ports, mount paths, process-manager choice and release triggers are implementation designs to review at H1, not existing deployed configuration. The domain architecture, `forge_merge`, preserved F2W UI/LLM/QA behavior, JSON-first KG creation and Tiled 0.2.19 pin remain unchanged.

## 1. Required image matrix

Retain the repository's GHCR publication convention unless the owner changes the registry. Proposed normalized image name: `ghcr.io/fair2wise/fair2wise`. Publish the three named variants from the same reviewed source commit. These are image tags; Ansible or the local operator creates running containers from them.

| Image tag | Contents and Tiled mode | Publication target |
| --- | --- | --- |
| `:local` | Production-built UI, private gateway, F2W FastAPI/agents, durable worker and paper-processing dependencies, plus an application-managed Tiled Graph 0.2.19 server | The Tiled Graph instance inside this local application deployment |
| `:als-dev` | Same UI, application, agents and paper-processing capability; Tiled client/import adapter only, no bundled managed-server service | `https://tiled-staging.als.lbl.gov/` |
| `:als-prod` | Same UI, application, agents and paper-processing capability; Tiled client/import adapter only, no bundled managed-server service | `https://tiled.als.lbl.gov/` |

All three serve the UI and application APIs beneath `/fair2wise`. The :local application grants full access without user sign-in and its host port binds to loopback by default; exposing it more broadly is an explicit operator choice. ALS builds reject local_trusted authorization mode. Their FQDN is not compiled into the image. Ansible configures ALS hostnames, TLS/edge routing, runtime configuration, secrets and mounts. `als-dev` is an environment profile, not a Vite hot-reload server or a weaker dependency profile.

Local, staging and production are distinct publication targets. There is no implicit replication, cross-environment fallback or graph copying. FORGE production obtains literature tasks through the production F2W agents/API and reads their published literature from production Tiled. FORGE development uses the staging pairing. A local FORGE instance sees locally published graphs only by explicitly connecting to that local F2W/Tiled deployment.

## 2. Packaging and process topology

Use a common multi-stage Dockerfile with shared frontend-build and application-runtime stages, then explicit final targets `local`, `als-dev` and `als-prod`. The two ALS finals inherit a common external-Tiled runtime; they do not inherit the local/server final. Differences should be environment defaults and managed-server inclusion, not independently maintained implementations.

```text
                       shared UI build + F2W runtime
                          /                 \
                         /                   \
       local final target                 external-Tiled runtime
       gateway + UI                          /           \
       API + agents/worker              als-dev          als-prod
       Tiled 0.2.19 server               staging URL      production URL
       private mounted state            no server       no server
```

The requested local image is an all-in-one application image, not merely a Compose file referencing an independently required Tiled container. Compose may remain as a convenience launcher or a separate developer workflow, but cannot substitute for the local image requirement. ALS images also contain the UI and backend, not just the previous `agent` build target.

Bundle only the dependencies needed for F2W literature work: figures, parsers, schemas, runtime, inference adapters and Tiled integration. No FORGE simulation/reduction/experimental-estimator stack. Reuse an explicitly isolated parser environment if the dependency lock requires it. ALS images may install the Tiled client distribution and shared client code; 'no Tiled server' means no managed-server runtime, server-specific environment/configured service/database, or startup. Do not mutilate a client distribution solely because it contains shared modules.

A supervised init/process manager must own the gateway, API and worker processes, plus Tiled in local mode. Review one manager at H1; `tini` alone reaps children but is not a full restart/readiness supervisor. Propagate termination, bound shutdown time, drain or journal jobs, reap children, and exit nonzero when a required component cannot be recovered. Avoid `cmd1 & cmd2 & tail -f /dev/null`, which can report a healthy container after service death. Never start services as a side effect of importing the SDK.

Proposed network layout: one container ingress on port 8080; gateway is public within the deployment network, API is private loopback on 8090, and local Tiled is private loopback on 8765. Retain existing standalone Python launchers and configurable ports. Bind a local host mapping to loopback by default. The local Tiled admin API is not publicly proxied through `/fair2wise`; local application access requires no user Tiled sign-in. Keep the bundled server credential internal rather than exposing a general admin proxy. Optional direct developer access requires explicit configuration. Do not mount the host Docker socket or use Docker-in-Docker to start local Tiled.

Normal startup never downloads papers, pulls model weights, runs a corpus import, performs an unapproved database/schema migration, or installs Python packages. Images contain code and supported dependencies, not a corpus. An empty but valid mounted corpus is acceptable; missing required mounts must produce an actionable startup/feature-readiness error rather than writing private data into the image layer.

The scope of a bundled local inference server has not been specified. Preserve selected F2W CBORG/Ollama paths; do not add Ollama or model-weight downloads merely by interpreting 'everything' as an inference deployment. Document a separately configured model endpoint and ask the owner if a bundled inference process is required.

## 3. Base-path contract: prefix once, everywhere

Required public prefix: `/fair2wise`; canonical UI entry: `/fair2wise/`. Treat it as validated configuration shared by the frontend build, gateway and API. Rebuild the frontend for a different base path unless a separately tested runtime-base mechanism is adopted. A runtime environment change alone must not pretend to rewrite already-built Vite assets. Reject build/runtime prefix mismatch at startup with a helpful message.

Recommended routing contract:

| Public route | Internal behavior |
| --- | --- |
| `/fair2wise` | Redirect to `/fair2wise/`, retaining query parameters |
| `/fair2wise/` and UI deep links | Static production UI; SPA fallback only in the UI namespace |
| `/fair2wise/assets/...` | Built assets; missing assets return 404, never index.html |
| `/fair2wise/api/<legacy-path>` | Gateway removes `/fair2wise/api` exactly once; unchanged UI API handles `/<legacy-path>` |
| `/fair2wise/api/chat/stream` | Existing `/chat/stream` with unchanged progress/complete/error payloads and streaming |
| `/fair2wise/api/docs`, `/fair2wise/api/openapi.json` | Correct prefixed documentation URLs, subject to deployment access policy |
| `/fair2wise/model-api/<existing-model-path>` | Separate model-compatible API, if exposed by the release profile; preserve all six internal routes, including their existing `/api` or `/v1` segment |
| `/fair2wise/healthz` | Gateway/process health; no secrets or corpus contents |

For example, the existing model route `/v1/chat/completions` becomes `/fair2wise/model-api/v1/chat/completions`, not a collision with the UI agent's `/chat`. Existing raw standalone model launchers stay callable independently. Deployment URL prefixes do not authorize changing method/path suffixes, DTOs or event framing.

The ALS edge proxy managed by Ansible should forward the `/fair2wise` prefix unchanged to the container. The in-container gateway is the sole prefix-stripping boundary for the API. If the existing edge is forced to strip it, explicitly select and test the alternative contract; never strip twice or configure both a physical mount and `root_path` as if they were interchangeable.

Set Vite `base` to `/fair2wise/` and its existing `VITE_F2W_AGENT_API_URL` to the same-origin `/fair2wise/api`. `liveAgent.ts` already concatenates paths onto this setting. Configure any router basename and audit dynamically built URLs, docs links, CSS/public assets, image references, citation links and API calls outside that module. Relative URLs must not resolve against the currently selected deep-link path. The new images must not retain the old compiled `http://127.0.0.1:8090` browser default. Local development can still explicitly use that separate-server setting.

The UI backend keeps rooted route handlers and receives the actual ASGI `root_path` `/fair2wise/api` behind the stripping gateway. `root_path` is not a substitute for gateway routing. Test redirects, OpenAPI servers, error URLs and mounted lifespan/auth behavior. Preserve upstream HTTPS/host only from trusted proxies; an untrusted browser must not spoof its origin using forwarded headers. Derive host-specific settings from Ansible configuration, not an untrusted Host header when it grants authority.

Disable proxy buffering/caching for SSE and preserve cancellation and exactly one terminal event. Coordinate edge/gateway timeout, heartbeat and graceful-drain behavior; do not impose an arbitrary short request timeout on long agent streams. Bound ingress upload sizes and honor forwarding/body limits without breaking approved PDF intake.

Serve UI/API same-origin by default. On ALS, native Tiled sign-in may require its supported redirect/token exchange; verify that specific flow rather than exposing a broad cross-origin admin interface. If another origin is needed, Ansible supplies an explicit allowlist; CORS origins are scheme/host/port, not `/fair2wise` paths, and CORS is not authentication. Do not introduce permissive wildcard credentials. Scope cookies to the application base path when cookies are used, with deployment-appropriate security settings.

**Compatibility consequence:** rebuild the frozen frontend source with reviewed deployment URL/base settings and test its unchanged workflows. This user-requested URL-prefix configuration is not a UI feature rewrite. Preserve existing source/dev launcher URLs and rooted internal API contracts; do not expose public root-path aliases solely to make a broken prefix configuration appear to work.

## 4. Versioned JSON and environment-specific publication

F2W creates versioned JSON on mounted working storage, freezes/tests it and commits reviewed JSON/manifests to the F2W repository. Staging validates an exact committed artifact; production promotion exports those identical commit/path/hash-selected bytes to its mounts before a separately approved import. No production re-extraction occurs. JSON creation still works without Tiled. Images remain corpus-free even when snapshots are tracked by Git. GIT_KG_PROMOTION_SPEC.md defines the full release contract.

| Profile | Create | Import |
| --- | --- | --- |
| local | Mounted JSON snapshots and manifests | Local managed Tiled only |
| als-dev | Staging-mounted JSON snapshots and manifests | Staging Tiled only |
| als-prod | Exact committed JSON/manifests validated on staging, exported to production mounts | Production Tiled only; no extraction on promotion |

Bind import receipts and authorization to snapshot hash, schema/version, deployment environment, target instance/URL, literature scope and expected base revision. A local or staging receipt never satisfies a production import. Code release promotion and data publication are separate actions; moving an image tag must not copy a graph, alter a corpus or switch active data revisions.

A central outage is a target-unavailable result. It must not start a local server, redirect to the other central server, or claim an import succeeded into JSON. Preserve explicitly selected JSON-only workflows; they are not an invisible replacement for failed central publication.

## 5. Admin-account writer and application authorization

The owner selected an **F2W admin account** for Tiled writes, not per-end-user credential delegation. Retain logical literature ownership, corpus scope and FORGE sentinel-record protection, but do not claim Tiled rejected a cross-owner operation when an admin credential would allow it. Normal reader/owner ACL tests still validate supported server roles where used; additional tests must prove F2W's own scope guard prevents unauthorized operations even with an admin writer available.

Create the writer in the trusted publication path only. Browser chat, model tool arguments and arbitrary SDK requests cannot acquire admin authority simply because the server possesses a credential. Reject arbitrary target URLs/namespaces, raw GraphQL passthrough, global reset/delete and requests outside the reviewed plan. Prefer a separate configured read credential for retrieval when available; the read-account arrangement remains a deployment configuration decision. Admin identity is not Linux/container root.

Provide environment-specific admin credentials through runtime secret files or the approved deployment secret mechanism. Never bake them into images, `ARG`, `VITE_*`, OpenAPI examples, logs or client settings responses. Separate local credentials from staging/production; no development workflow or pull request receives production keys. Provision local credentials without a universal hardcoded password and retain their protected state across normal restart. Import approval remains necessary even for admin execution.

Record `requested_by`, `approved_by`, `executed_as` (admin principal identifier, not token), environment, target instance, corpus scope, operation ID, snapshot hash, revisions, time and outcome. Do not misrepresent an admin write as a native Tiled action by the end user. Maintain durable audit before acknowledging sensitive effects.

**O05 is resolved by D30/D34.** :local is trusted full-access application mode: any user can chat, ingest, extract, use figures/Knowledge Operations, curate, approve, publish and edit without a Tiled account, login or role check. ALS profiles retain native Tiled sign-in, verified-admin-only privileged work and chat-only ordinary users. Select the mode at trusted server startup; neither a request field nor failed ALS authentication can enable local_trusted. All profiles retain scoped effects, explicit approvals, JSON/evidence validation and private-source handling. AUTHORIZATION_SPEC.md defines the actor and technical-writer distinction.

Existing mutations of local candidate/session state, settings and annotations are not automatically Tiled publication. Preserve their established semantics and inspect each path rather than banning every state change under a blanket 'read only' rule.

The owner confirms both central endpoints run Tiled Graph 0.2.19 and acknowledges operator coordination for admin writing. Record target/build identities and issued secret references without inventing account names. Actual deployment login, admin/non-admin, revocation and scoped writer tests are still required. The app receives no authorization from proxy headers unless verified through the selected Tiled authentication contract.

## 6. Mounted private files and durable application state

Papers are mounted at deployment in every profile, never bundled into any image layer, build context, build cache, registry artifact or public static directory. Derived full text, original figure crops, corpus-bearing KG snapshots, operational databases and secrets are likewise runtime state. Approved schema definitions and code may be packaged. Synthetic test assets can be mounted by CI; do not ship the user's corpus as a fixture.

Proposed container paths (host paths are Ansible/local inputs):

| Mount | Contents | Access/lifecycle |
| --- | --- | --- |
| `/var/lib/fair2wise/papers` | Existing source PDFs and source manifest | Read-only when ingest does not need to mutate it; private to source-reading processes |
| `/var/lib/fair2wise/incoming` | Authorized uploads/downloads | Writable intake staging, quota/size/path validation |
| `/var/lib/fair2wise/artifacts` | Parsed text, tables, crops, private evidence | Private persistent store, not web-served |
| `/var/lib/fair2wise/kg` | Versioned KG JSON, manifests, edit journals and import receipts | Durable; frozen versions never overwritten |
| `/var/lib/fair2wise/state` | Jobs, checkpoints, sessions, approvals and audit | Persistent, with tested recovery and single-writer/lease rules |
| `/var/lib/fair2wise/cache` | Rebuildable indexes/model/parser caches | Still private when source-bearing; quota and version aware |
| `/var/lib/fair2wise/tiled` | Local Tiled catalog/database data | Local profile only; no central-server filesystem assumed |
| `/run/secrets/...` | Tiled admin and configured model secret files | Read-only secret mounts, never public or copied into job state |

Reuse configurable F2W path resolution and provide explicit legacy path mapping instead of moving existing files blindly. A read-only source corpus requires a separate writable intake root; downloads must not silently fail or fall back to container-local storage. Persist package-relative/opaque artifact IDs where possible so paths remain valid on another host. Do not recursively chmod/chown arbitrary host trees on startup; validate mount UID/GID and report the required fix. Use restrictive directory permissions and distinct process identities where feasible; an all-in-one container remains one trusted deployment boundary, not a security sandbox against its administrator.

The gateway serves only the built UI root. Private file paths, caches and Tiled admin endpoints are not proxied. Protect the same content in exports, exceptions, visible tool traces, SSE and Ansible/GitHub logs. Relevant cited exact quotations remain allowed under D19; complete papers and original crops do not become publicly served merely because they are bind-mounted.

**Mounting is not backup.** Ansible supplies host paths and a backup integration. Before H7, record the backup owner/location, retention, recovery-point/recovery-time targets and encryption/access policy; these values have not been supplied. Back up consistent JSON/manifests/receipts, private sources/artifacts, state/checkpoints/audit and local Tiled data. Use a supported database-consistent snapshot/backup procedure rather than blindly copying a live database. Coordinate a safe publication/checkpoint boundary and demonstrate restore onto a new disposable instance. Central Tiled database backups are owned by the central service; F2W still backs up its JSON/import receipts/private files and documents that dependency. Do not invent a nightly schedule or claim retained volumes alone meet recovery requirements.

## 7. GitHub Actions build and publication contract

See CI_CD_CONTRACT.md. The existing source publishes one `agent` target through GHCR; it must be changed to build, test and publish all three variants. Tests and candidate builds run on `forge_merge` and PRs targeting it without enabling existing production/documentation deployment side effects.

Build from one resolved commit with one reviewed lock and frontend source, label the revision/profile/base path/Tiled mode, and capture the digest for each final variant. Keep `:local`, `:als-dev` and `:als-prod` as the required human-friendly tags; also record immutable variant+commit references or digests for reproducible Ansible rollout/rollback. Tags are not a data-version identifier.

No untrusted PR job receives registry-write or runtime credentials. Use minimum job-scoped permissions, reviewed SHA-pinned actions, and protected promotion for `:als-prod`. Exact tag-moving triggers remain O06. Proposed policy: ordinary branch CI builds/tests candidate variants; trusted approved publication updates local/dev; an owner-approved release promotes the already-tested prod digest. The implementation must still publish all three required tags under the approved workflow; protection is not an excuse to omit the prod artifact. A required check failure blocks promotion, and promotion records the actual tested digest rather than rebuilding untested bytes.

Scan exported image contents and layers, not just the final filesystem, for forbidden source corpus/secrets. `.dockerignore` is necessary but cannot compensate for an earlier sensitive COPY followed by deletion. Prefer allowlisted copies or a sanitized context; exclude bundled SQLite files, generated graphs, parser text/crops, run traces and private symlink targets. Do not publish private papers in CI test reports or cache archives.

## 8. Ansible handoff, operational checks and rollback

Ansible owns environment host/FQDN, approved image digest, proxy/TLS integration, mounts, secrets, service identity and resource/restart settings. Container code owns the profile contract, internal gateway, process supervision, health, JSON build/import and app-managed local Tiled. Do not guess the organization's Traefik role, inventory path, secret backend or hosts; ANSIBLE_HANDOFF.md records the required variables and unresolved inputs.

Keep all ALS ingress under `/fair2wise`. Preserve the prefix through the edge using the agreed mapping; trust only configured proxy hops. Do not put a public FQDN in an image build arg. Both environment images should work under another test hostname without a rebuild. Any forwarded-identity authentication needs an explicit trusted boundary, not a bare header a client can forge.

Provide liveness distinct from capability readiness: the UI and JSON-only operations can be usable while central Tiled is unavailable. Report graph publication readiness separately and block only dependent operations. A missing local Tiled process means local Tiled readiness fails; supervisor behavior must not hide it. Health endpoints must not start imports or run inference.

On image upgrade, stop admitting new heavy jobs, checkpoint/drain within a configured timeout, retain mount state, start the approved new digest and verify UI/API/stream/worker checks. A rollback restores the previous code digest/config while respecting state-format compatibility; graph/snapshot rollback is separate and requires its own scoped plan. No automatic central graph reset, paper deletion or destructive migration on container restart.

## 9. Work packets and human stops

| Task | Work and dependencies | Stop / acceptance |
| --- | --- | --- |
| T24A | Freeze three profiles, base-path routing, mounted state, admin identity and release/Ansible contract. Depends H0; parallel with T01/T18A/T20A. | H1 reviews Tiled login/admin enforcement, Git-artifact promotion and O06 image-tag triggers; O05 is settled. |
| T24B | Build shared runtime and three image finals; supervised services; local-only Tiled; private mounts. Depends H2/T04/T20B/T24A. | Candidate images pass local lifecycle and no-corpus/no-server-in-ALS checks before H4. |
| T24C | Configure Vite/gateway/API prefix; preserve raw API suffixes, SSE and old UI workflows. Depends H2/T24A/T18A. | URL/base-path browser checks; joins real app parity at H6. |
| T24D | GitHub Actions matrix, immutable build records, registry publication and protected tag promotion. Depends T24B/T24C; contracts can be prototyped earlier. | No live keys or untrusted publishing; all three tested variants, owner-approved promotion rules. |
| T24E | Integrated three-profile smoke tests and Ansible deployment/restore rehearsal. Depends T24B/T24C/T24D/T14/T18C. | H6 evidence includes real container prefix/streaming; H7 requires safe rollout/restore and environment-specific import. |

JSON/figure/extraction/canonicalization work stays parallel after contracts. Docker/Ansible work has one owner for shared Dockerfile, locks, gateway routing, workflows and infrastructure templates; leaf streams do not each introduce their own startup scripts. All F2W changes integrate into forge_merge; the external Ansible repository, if separate, needs its own approved branch.

## 10. Evidence and unresolved choices

DEPLOYMENT_SOURCE_AUDIT.md separates verified source facts from these designs. Official implementation references are cataloged in DEPLOYMENT_REFERENCES.md. Required deployment cases are added to binary_case_inventory.json; no case is marked executed by this planning revision.

Open inputs: O06 exact image tag-promotion trigger; O07 remaining Ansible/host/mount/backup/secret references; O08 local inference-server expectation and supported image CPU architectures; O03 per-job settings proposal. Access is settled: :local full functionality without sign-in; ALS Tiled sign-in with admin-only privileged actions. Central Graph-enabled 0.2.19 is owner-confirmed; verify deployed connections and permissions. No new allowlist or auth provider is selected.