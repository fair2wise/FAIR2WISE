# Deployment source audit and changes required

**Static inspection of the supplied F2W rsoxs archive only.** Archive-recorded commit: `222b1e7b130567dbf4a3242697ac2b465794a38d`. The machine map contains exact hashes and excerpts. No builds, container launches, remote CI or Ansible deployments were executed.

| Existing source | Verified behavior | Required migration |
| --- | --- | --- |
| `.github/workflows/publish-image.yml:3-18,25-46` | main/v* triggers; GHCR publication; a single Docker `agent` target | Three final variants, forge_merge testing, digest manifest and owner-approved channel promotion. Keep runtime secrets out of CI. |
| `.github/workflows/build-app.yml:3-7,37-46` | main-only tests; documentation deployment and README update guarded to main pushes | Add forge_merge/PR checks without broadening deploy side effects. |
| `Dockerfile:3-18` | Separate frontend stage; compile-time API URL defaults to browser localhost:8090 | Production UI in every final variant; /fair2wise/ base and same-origin /fair2wise/api; no baked FQDN. |
| `Dockerfile:31-72` | Agent runtime copies/installs Splash via pixi; broad `COPY . .`; independent backend entrypoint | Remove deprecated service/dependency; shared app runtime; local-only server environment; allowlisted non-corpus image content. |
| `.dockerignore:1-25` | Excludes papers, dotenv, runs and some caches, but not every source-bearing artifact location | Expand exclusions/sanitized context; inspect layers and caches, including generated graphs/private text/crops/DBs outside papers. |
| `docker/nginx.conf:14-27` | /api/ -> agent:8090/ with SSE buffering off; root static fallback | /fair2wise/api stripping once; prefixed static routes; missing API/assets never become HTML; private loopback upstream in combined image. |
| `ui/vite.config.ts:20-38` | No explicit public base in this configuration | Add validated /fair2wise/ build base without changing UI behavior. |
| `ui/src/app/components/data/liveAgent.ts:221` | Existing VITE_F2W_AGENT_API_URL or localhost fallback, with relative suffix concatenation | Use existing setting /fair2wise/api in images; preserve standalone/developer configuration. |
| `compose.yaml:8-25,27-98` | Separate agent/frontend; extraction process documented as external; named state/cache volumes; CBORG networking caveat | All-in-one image process ownership and durable job recovery; keep model/network configurability and test actual deployment egress rather than assume it. |
| `docker-compose.yml:79-118` | Developer sidecar Tiled installs >=0.2.18 at startup; experimental seed command | Do not use as final deployment: exact 0.2.19 built into local server environment; no production runtime pip install or experimental auto-seed. |

The donor/target archives inspected here do not provide the organization's ALS Ansible role or a Traefik deployment definition. Those interfaces remain owner/infrastructure inputs, not facts inferred from filenames.

No requirement is satisfied by renaming the old `agent` image three times: local must include UI/backend/managed Tiled, and both ALS images must include UI/backend without a managed Tiled server. Client code may share the Tiled distribution; verify actual dependency/entrypoint composition, not just package-name presence.
