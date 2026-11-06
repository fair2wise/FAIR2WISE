# Deployment profile matrix (T24A)

**Status:** Proposed for H1. Design only: nothing here is deployed, and no image yet implements it.

**Fixed owner decisions:**
- D23-D29: three images, served under `/fair2wise`; Ansible sets the FQDN; F2W writes through an admin account; papers are mounted, not baked in.
- D30: ALS profiles require Tiled sign-in, and only admins can do privileged work.
- D34: `:local` gives full access with no sign-in.
- H0 E3/E4: `storage/` (kg, terminology, schema) may be included in an image (owner exception). The Tiled catalog DB may be included only in `:local`. `storage/source_index` and papers are always excluded and must be mounted.

**Machine-readable source:** `docs/migration/companion/deployment_profiles.json`. Where it conflicts with E3, see the note at the end.

## 1. Matrix

| Dimension | `:local` | `:als-dev` | `:als-prod` |
| --- | --- | --- | --- |
| Docker final target | `local` | `als-dev` | `als-prod` |
| Shared stages | ui-build, app-runtime, gateway | ui-build, app-runtime, gateway, external-runtime | same as als-dev |
| Bundled Tiled server | **Yes**: Tiled 0.2.19 in an isolated venv. The exact artifact is blocked; see `docs/adr/tiled-deployment.md` section 1. | No server runtime, config or DB | No server runtime, config or DB |
| `F2W_DEPLOYMENT_PROFILE` (proposed) | `local` | `als-dev` | `als-prod` |
| `F2W_TILED_MODE` | `managed` | `external` | `external` |
| Tiled URL | `http://127.0.0.1:8765`, private loopback inside the container | `https://tiled-staging.als.lbl.gov/` (fixed) | `https://tiled.als.lbl.gov/` (fixed) |
| Graph publication target | Local managed Tiled only | ALS staging only | ALS production only |
| FQDN | Operator's choice; default `localhost` | Ansible inventory, never baked into the image | Ansible inventory, never baked into the image |
| Public base path | `/fair2wise` | `/fair2wise` | `/fair2wise` |
| UI entry | `/fair2wise/` | same | same |
| UI API (browser) | Same origin `/fair2wise/api`; the gateway strips the prefix once | same | same |
| Model-compatible API | `/fair2wise/model-api/*`, served in the API process (no sign-in) | `/fair2wise/model-api/*` for signed-in users, same session cookie as `/fair2wise/api` (owner B7, O-A9, 2026-10-30); non-browser token sign-in is D-T8 (open) | same |
| Container ingress | One port, `8080` (gateway) | same | same |
| Default host bind | `127.0.0.1:<host port>` | Reachable only from the authorized proxy (Ansible) | same |
| Authorization mode | `local_trusted` (no sign-in, all capabilities) | `tiled_authenticated` | `tiled_authenticated` |
| User sign-in | None | Native Tiled sign-in on tiled-staging | Native Tiled sign-in on tiled |
| Ordinary user | Every application capability | Chat and bounded read support | Chat and bounded read support |
| Admin user (verified `whoami.roles` contains `admin`) | Not applicable | Ingest, approve, publish, edit (scoped) | same |
| `local_trusted` allowed? | Yes, chosen at startup only | **Rejected** at startup, even through overrides | **Rejected** |
| Audit actor | `local-operator` | Verified subject and issuer | Verified subject and issuer |
| Technical writer (`executed_as`) | Local writer principal (generated key) | F2W staging admin account (D27) | F2W production admin account (D27) |
| Writer secret source | Generated at first provisioning and kept in the Tiled data mount, or `/run/secrets/tiled_local_writer` | `/run/secrets/tiled_writer` from Ansible's secret mechanism (O07) | same, but a separate production credential |
| Model secrets | `/run/secrets/…` or an operator env file | Ansible runtime secret (O07) | same, production-scoped |
| Papers mount `/var/lib/fair2wise/papers` | Required. Read-only unless an ingest mode needs writes. | Required | Required |
| Intake `/var/lib/fair2wise/incoming` | Read-write | Read-write | Read-write |
| Artifacts `/var/lib/fair2wise/artifacts` | Read-write, private | Read-write, private | Read-write, private |
| KG JSON `/var/lib/fair2wise/kg` | Read-write; frozen versions are immutable | Read-write | Read-write. Holds the Git-exported staging-validated snapshot; no extraction on promotion. |
| State `/var/lib/fair2wise/state` | Read-write | Read-write | Read-write |
| Cache `/var/lib/fair2wise/cache` | Read-write, rebuildable | Read-write | Read-write |
| Source-RAG index (`HYBRID_RAG_LITERATURE_INDEX`, `HYBRID_RAG_OPS_INDEX`) | Mounted if source RAG is enabled (E4) | Mounted if enabled | Mounted if enabled |
| Local Tiled data `/var/lib/fair2wise/tiled` | **Required**, read-write, persistent | Must be absent; startup fails if Tiled config is present | Must be absent |
| `storage/` in the image (E3) | kg/terminology/schema allowed; optional catalog seed (see ADR section 4.2) | kg/terminology/schema allowed; **no catalog DB** | same as als-dev |
| Central-outage behavior | Not applicable | `target_unavailable`; never starts local Tiled | same |
| Tag promotion | moves on every push to `main` (O06 decided 2026-10-30, D2.1) | same push, same digest set | same push, environment `ghcr-als-prod` (any tested digest, D2.3) |
| Platforms | O08 (unresolved). Host for local use is arm64 macOS. | ALS host architecture is not yet known (O08) | same |

## 2. Invariants (each needs a binary check in T24B/T24E)

1. Every profile is chosen by the trusted startup profile, never by a request, header, hostname guess or model argument.
2. An ALS image with `F2W_DEPLOYMENT_PROFILE=local`, `F2W_TILED_MODE=managed`, or a Tiled URL other than its fixed URL exits non-zero at startup.
3. ALS images have no `tiled serve` entry point, no Tiled server config, and no catalog DB in any layer. When central Tiled is unavailable they never install or start one.
4. `/fair2wise` is the only public prefix. Built assets, gateway and API `root_path` must all agree. On a mismatch the container refuses to start.
5. Images contain no papers, `storage/source_index`, secrets, `.env`, run state, or a live catalog DB. The one allowed exception is an optional read-only catalog seed in `:local`.
6. Mounts are not backups (O07).

## 3. Note: plan text versus H0 E3

`deployment_profiles.json` (`kg_promotion.bundled_in_images: false`) and plan 4.14/13.4 ("Docker build contexts/layers must continue excluding storage/kg corpus artifacts") were written before H0. E3 later allowed `storage/` (kg, terminology, schema) in the image.

The E3 errata (applied 2026-11-06, `reviews/H8/spec_errata_E3_draft.md`) amended plan 4.14 and the two companion KG specs accordingly; `deployment_profiles.json` is a frozen data file and still carries the pre-E3 flag. This document follows E3. The consistency points are:

- KG JSON in the image is a **default or compatibility payload only**. It is not an import source.
- Imports and promotion still read the commit/path/SHA-256-selected bytes from the `/var/lib/fair2wise/kg` mount (D31).
- E3 does not override the T21A/T26A source-excerpt disclosure review of those files (H0 audit E3, "These files were not checked for source-text excerpts").
