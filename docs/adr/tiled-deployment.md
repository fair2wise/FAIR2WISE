# ADR: Tiled deployment modes, artifact pin and test lifecycle

- **Status:** Proposed (T20A, gate H1). Not approved. No code, image or deployment changed.
- **Date:** 2026-10-09
- **Branch / baseline:** `forge_merge`; frozen baseline `222b1e7b130567dbf4a3242697ac2b465794a38d`.
- **Decisions this ADR must satisfy (fixed by the owner):** D13/D14 (managed and central modes; the app provisions a disposable test server), D22 (Tiled exactly 0.2.19), D23-D29 (three images, `/fair2wise`, Ansible sets the FQDN, F2W writes as an admin account, papers mounted), D30 (ALS sign-in; admins only for privileged work), D33 (owner confirms central servers run Graph-enabled 0.2.19), D34 (local full access without sign-in). H0 E3: the Tiled catalog DB stays out of every image except, optionally, `:local`.
- **Companion contracts:** `docs/migration/companion/TILED_DEPLOYMENT_SPEC.md`, `DEPLOYMENT_STRATEGY.md`, `AUTHORIZATION_SPEC.md`, `JSON_KG_PUBLICATION_SPEC.md`; T24A docs in `docs/migration/deployment/`.

Labels: **[Observed]** means checked directly in source, PyPI or package files on 2026-10-09. **[Proposed]** means a design that H1 must review.

---

> **Owner decision 2026-10-12 (H1-1, O-T1):** use Tiled 0.2.18 for now as an interim exception
> to D22. API references below come from 0.2.19rc2 and must be re-checked against 0.2.18 in
> T20B. See `docs/migration/reviews/H1/owner_decisions.md`.

## 1. Blocking finding: there is no `tiled==0.2.19` release on PyPI

**[Observed]** At 2026-10-09T22:27:56Z:

| Check | Result |
| --- | --- |
| `GET https://pypi.org/pypi/tiled/0.2.19/json` | HTTP 404 `{"message": "Not Found"}` |
| `pip download tiled==0.2.19 --no-deps` | `ERROR: No matching distribution found for tiled==0.2.19` |
| `GET https://pypi.org/pypi/tiled/json` → `info.version` | `0.2.18` (latest final release) |
| PEP 691 simple index, last versions | `0.2.17`, `0.2.18`, `0.2.19rc1`, `0.2.19rc2` |

Public artifacts nearest the pin, with SHA-256 taken from the PyPI simple index. The two wheels were also hashed locally after download and matched.

| File | SHA-256 | Uploaded (UTC) | Source URL |
| --- | --- | --- | --- |
| `tiled-0.2.19rc2-py3-none-any.whl` | `981bd63b45a27a8720bfb2a08e19761206b2ee747906f5eece2302cf26c2170b` | 2026-09-25 09:05:51 | `https://files.pythonhosted.org/packages/3a/c8/b7a93d753ac26d9e8df986473832613d4305258eeba54ba74d950b8cb347/tiled-0.2.19rc2-py3-none-any.whl` |
| `tiled-0.2.19rc2.tar.gz` | `e34586dbac7bef059b1f824375921cb438d58d0cfe5cde203615abc8b2c54105` | 2026-09-25 09:05:54 | `https://files.pythonhosted.org/packages/db/d8/51730acf5d77f59ea419e0d2c46d3474ea58b457bd440c58e3b984d11057/tiled-0.2.19rc2.tar.gz` |
| `tiled-0.2.19rc1-py3-none-any.whl` | `65f4d05de08f1006dcb311a8998877f0bafb2f2a6e9e0fdc1c13971141a54084` | 2026-09-22 08:56:56 | PyPI |
| `tiled-0.2.19rc1.tar.gz` | `31ecf11d149a803fae4b68fba3510a67624eac7345aee78d535a58779db361b2` | 2026-09-22 08:56:59 | PyPI |
| `tiled-0.2.18-py3-none-any.whl` | `416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06` | 2026-09-02 21:50:07 | `https://files.pythonhosted.org/packages/7d/70/85966182bc5fd7a5edd63290849ee4b7ebba8f61375780b782b1427f0bed/tiled-0.2.18-py3-none-any.whl` |
| `tiled-0.2.18.tar.gz` | `2f3943260c46824b162796fcb8a43c3d02f41205e2ccefc64e365b18b6ec1e27` | 2026-09-02 21:50:09 | `https://files.pythonhosted.org/packages/03/e5/558ce54947c9385b74129589ac91b20a54b4e7c327ac7749c272badb338d/tiled-0.2.18.tar.gz` |

**Consequence.** TILED_DEPLOYMENT_SPEC.md section 7 says to stop dependency approval when the exact artifact cannot be obtained. The 0.2.19 dependency approval is therefore **blocked**. This ADR does not substitute rc2, rc1 or 0.2.18. D33 (the central servers run 0.2.19) still stands. Those servers may run a build that is not on PyPI, such as a git tag or an internal image. The question goes to the owner and the Tiled team:

- **Q-T1.** What exact artifact do tiled-staging and tiled run? Possible answers: a PyPI release published later, a GitHub tag/commit, or an internal wheel or image. Give its SHA-256 or digest.
- **Q-T2.** May `:local` and the test fixture install that same artifact from an approved index or commit? The reproducibility rule in CI_CD_CONTRACT.md applies.

Once Q-T1 is answered, H1 records the final wheel and sdist filenames, their SHA-256, and the `about.library_version` string served by each central server (section 5).

Evidence file: `docs/adr/evidence/tiled/tiled_artifact_evidence.json`. Independently re-checked 2026-10-12: PyPI latest final is 0.2.18, `tiled/0.2.19` returns 404; GitHub newest tag `v0.2.19-rc2`, latest release `v0.2.18`; conda-forge latest 0.2.18.

### 1.1 What the API inspection is based on

**[Observed, with a caveat]** The only release-line code available was `tiled-0.2.19rc2`. I unzipped that wheel and the 0.2.18 wheel and read them. Nothing was installed and no server was started. Every package path cited below is from **rc2**. Treat it as the expected 0.2.19 surface until the final artifact can be checked. The rc2 GraphQL schema differs from 0.2.18 in ways that matter here (section 3.3), so the final release may also differ from rc2.

### 1.2 Declared dependencies

**[Observed]** In both 0.2.19rc2 and 0.2.18 metadata:

- `requires_python >=3.10`.
- Extras: `all, array, client, compression, dataframe, formats, minimal-client, minimal-server, server, sparse, xarray`.
- Base requirements: `httpx!=0.23.1,>=0.20.0`, `json-merge-patch`, `jsonpatch`, `jsonschema`, `msgpack>=1.0.0`, `orjson`, `platformdirs`, **`pydantic-settings<2.12.0,>=2`**, `pydantic<3,>=2`, `pyyaml`, `typer`.
- `server` extra (rc2) includes `fastapi>=0.132.0`, `starlette>=0.48.0`, `strawberry-graphql[fastapi]>=0.315.3`, `sqlalchemy[asyncio]<2.1,>=2`, `aiosqlite`, `asyncpg>=0.28`, `alembic`, `duckdb<1.4.0`, `numba>=0.59.0`, `pyarrow>=14.0.1`, `python-jose[cryptography]`, `uvicorn[standard]`, `zarr`, `h5py`, `hdf5plugin`, `blosc2`, `redis`, `minio`, `obstore` and more. rc2 adds the `sqlalchemy<2.1` upper bound, which 0.2.18 does not have.

**Lock conflict [Observed].** The F2W runtime lock pins `pydantic-settings==2.14.2` (`requirements/runtime.txt:306`). That violates Tiled's base requirement `<2.12.0`. You cannot add `tiled` (client or server) to the current F2W environment without changing the lock. This is the same conflict class the plan already records for Docling (H0 audit, Docling note). Tiled is not in `requirements/runtime.txt` or `requirements/semantic.in` at all today. The app reaches Tiled with `requests` (`app/modules/tiled_graph.py:30,748-768`) and imports `tiled.client` only optionally (`tiled_graph.py:886-889`).

**[Proposed]** Install the managed server in an **isolated environment** inside `:local`, for example `/opt/tiled/venv` with its own hash-pinned lock. The F2W app and both ALS images talk to Tiled over HTTP/GraphQL. If they need the Tiled Python client, it gets its own lock that is resolved together with the F2W lock. DEPLOYMENT_STRATEGY.md section 2 allows an isolated environment where the lock requires one.

---

## 2. Context: what the current repository assumes

**[Observed]** Baseline file:line references.

| Source | Assumption | Mismatch with the 0.2.19 pin or the plan |
| --- | --- | --- |
| `docker-compose.yml:79-98` | Dev sidecar `python:3.11-slim` runs `pip install 'tiled[server]>=0.2.18'` at container start, then `tiled serve config` on `0.0.0.0:8765` | A floating range resolves to **0.2.18** (pip skips pre-releases). Installs at runtime. Python 3.11 differs from the app's 3.12. The healthcheck calls `curl`, which the slim image does not have (assumption; verify in T20B). The service env sets only `TILED_API_KEY`, not `TILED_CATALOG_URI`, so `${TILED_CATALOG_URI}` is never expanded (`os.path.expandvars` leaves unset variables as-is, rc2 `tiled/utils.py:563`). |
| `docker-compose.yml:100-119` | `tiled_seed` runs `scripts/ingest_tiled_sim.py --to-tiled` and loads the whole host `.env` | An experimental automatic seed. The plan prohibits implicit corpus/seed imports at start (DEPLOYMENT_SOURCE_AUDIT.md). |
| `storage/tiled_config.yml:4-12` | `trees: [{path: /, tree: catalog, args: {uri: ${TILED_CATALOG_URI}, init_if_not_exists: true}}]` | Valid in rc2: the `catalog` alias is at `tiled/config.py:39`, and `from_uri(..., init_if_not_exists=...)` at `tiled/catalog/adapter.py:2693-2700`. rc2 recommends the simpler top-level `catalog:` key (`tiled/config.py:346-347,404-436`), which also reads `TILED_CATALOG_URI` natively (`CatalogConfig` env prefix `TILED_CATALOG_`, `tiled/config.py:60-75`). No `writable_storage` is set, so graph-only use works but array/table writes do not. |
| `storage/tiled_config.yml:14-16` | `single_user_api_key: ${TILED_API_KEY}` plus `allow_anonymous_access: true` | Anonymous callers get `PUBLIC_SCOPES = {read:metadata, read:data}` (`tiled/access_control/scopes.py:62-64`, `tiled/server/authentication.py:438`). Graph reads therefore work **unsigned** on this config, and only writes need the key. The key must be alphanumeric (`tiled/server/app.py:599-612`). With no `access_control`, graph checks reduce to the caller's scopes (`tiled/graph/schema.py:106-121`). |
| `storage/tiled_config.yml:18-22` | CORS allows browser origins `:8090` and `:5175` | Under the new design the browser never calls local Tiled. Remove these origins in the managed config. |
| `scripts/start_agent_backend.sh:212-240` | If `import tiled` succeeds, the API launcher `nohup`s a Tiled server, seeds sim data on every start, and sets `F2W_LIVE_TILED=1` | This is implicit start-as-side-effect, with no supervision, no stop or ownership record, and a seed on every start. The default key `devonlykeychangeinproduction000…` is a universal hard-coded credential (`:217`), which the strategy section 5 prohibits. The catalog defaults to `storage/tiled_catalog.db` inside the source tree (`:216`). In today's `agent` image this block never runs, because `tiled` is not installed. |
| `app/modules/tiled_graph.py:153-175,198-222` | `coerce_local_tiled_uri` silently swaps any `*.als.lbl.gov` host for `127.0.0.1:8765` | **Central mode is impossible with the current code.** This is an intentional safety guard today. It must become a profile-bound rule: a configured ALS profile requires its fixed URL, and local/test profiles reject ALS hosts. A silent swap is a forbidden fallback to a different graph (spec section 1). |
| `app/modules/tiled_sim.py:290-303` | Writes refuse any non-loopback host | Same: correct for local/test, but must be enforced by profile, not by hostname heuristics. |
| `app/modules/tiled_graph.py:183-195,748-768` | One process-wide `TILED_API_KEY` with scheme `Apikey`, read from `os.environ` on each call | ALS needs a per-caller identity (native sign-in) separate from the writer credential (D27/D30). Plan 4.5 prohibits `os.environ` as per-request configuration. |
| `app/modules/f2w_agent/api.py:2774-2779` | `PUT /settings` takes a browser-supplied `tiled_uri` (coerced to local; fallback `http://127.0.0.1:8000`) | The request can choose the target. In ALS this must be read-only, because the target is fixed by profile (AUTHORIZATION_SPEC). The fallback port 8000 disagrees with 8765. |
| `app/modules/tiled_graph.py:86-117,785-795` | The page query asks for `nodeId` and retries without it on error | rc2 `Entity` has no `nodeId`; it has `isNodeBound` (`tiled/graph/schema.py:239-247`). The retry handles this. 0.2.18 has `node_id` (0.2.18 `tiled/graph/schema.py:187-189`). |
| `app/modules/tiled_graph.py:40` and `tiled_sim.py:66-75` | GraphQL at `/api/graphql`; `createEntity(input:{entityType,name,uri,properties})`; `createLink(input:{subjectId,predicate,objectId})`; `entities(entityType,limit,offset)`; `links(predicate,limit,offset)` | Compatible with rc2 (section 3). |
| `tiled_graph.py:1-19`, `ENTITY_TYPES` `:44` | The live graph today holds experiment identity (ESAF/Proposal/Sample/BlueskyRun), not literature KG | The literature importer (T08B) is new work on the same graph API. |

---

## 3. Capability contract: the rc2 API surface

### 3.1 Server entry points

**[Observed in rc2]**

| Capability | Path | Package location |
| --- | --- | --- |
| Service info, including `library_version`, `api_version` and `authentication.providers/links` (whoami, apikey, refresh, logout) | `GET /api/v1/` | `tiled/server/router.py:210-301`, links at `:285-293` |
| Process health | `GET /healthz` → `{"status":"ready"}` | `tiled/server/app.py:293-295` |
| REST API | `/api/v1/*` | `tiled/server/app.py:451` |
| Entity/link graph (GraphQL with a GraphiQL IDE) | `/api/graphql` | `tiled/graph/router.py:99-133` (prefix at `:133`). Mounted **only when the tree is catalog-backed** (`tiled/server/app.py:462-469`). The router docstring says `/api/v1/links`, which is stale. |
| Identity and roles | `GET /api/v1/auth/whoami` returns a `Principal` with `roles` | `tiled/server/authentication.py:1642-1680`; `tiled/server/schemas.py:423-448` |
| Admin bootstrap | `authentication.tiled_admins: [{provider, id}]` | `tiled/config.py:138-146`; applied at `tiled/server/app.py:752-760` |
| Authenticators | Dummy, Dictionary, PAM, OIDC, ProxiedOIDC, Entra, SAML, LDAP | `tiled/authenticators.py:31-661` |
| Access policies | `TagBasedAccessPolicy`, `ExternalPolicyDecisionPoint`, `DummyAccessPolicy` | `tiled/access_control/access_policies.py:31,66,464` |
| Database checks | Starting on an older catalog DB raises `DatabaseUpgradeNeeded`; `tiled catalog upgrade-database` / `tiled admin upgrade-database` fix it | `tiled/alembic_utils.py:120-162`; `tiled/catalog/core.py:123-136`; `tiled/commandline/_catalog.py:77`; `tiled/commandline/_admin.py:61` |
| Config validation | `tiled admin check-config` | `tiled/commandline/_admin.py:141` |
| Server CLI | `tiled serve config <file> [--host] [--port] [--api-key] [--public] [--scalable]`; host defaults to `127.0.0.1` | `tiled/commandline/_serve.py:634-726` (default at `:711`) |
| Config env overrides | `TILED_*`, `TILED_CATALOG_*`, `TILED_AUTHN*`, `TILED_SINGLE_USER_API_KEY` | `tiled/config.py:60-75,189,376`; `tiled/server/app.py:259-273` |

rc2 adds four catalog migrations over 0.2.18, including the move from access blobs to access tags (`tiled/catalog/migrations/versions/3bc110ce44e9_…`, `de302a096358_…`, `0d4e1f2a3b4c_…`, `3e87cbeb195b_…`). A catalog created by 0.2.18, such as the tracked `storage/tiled_catalog.db`, needs an explicit upgrade. The managed lifecycle must never run it as an automatic side effect.

### 3.2 GraphQL operations

**[Observed in rc2, `tiled/graph/schema.py`]**

- **Query** (`:414-498`):
  - `entity(id)`
  - `entities(entityType, nodePathParts, limit=100, offset=0)`
  - `link(id)`
  - `links(subjectId, predicate, objectId, limit, offset)`
  - `namespaces`
  - Listing filters run in SQL before LIMIT (`:149-176`). A caller without `read:metadata` gets empty lists, not an error (`:427-433`, `:490-497`; graph router docstring `tiled/graph/router.py:44-49`).
- **Mutation** (`:500-715`):
  - `createEntity(input: {entityType, name, nodePathParts?, uri?, properties?: JSON, accessTags?})`
  - `createLink(input: {subjectId, predicate, objectId, properties?, accessTags?})`
  - `updateEntity(id, input: {name?, nodePathParts?, uri?, entityType?, accessTags?})`
  - `updateLink(id, input: {predicate?, accessTags?})`
  - `deleteEntity(id)` (also removes attached links)
  - `deleteLink(id)`
  - `upsertNamespace(prefix, uri)`
  - `deleteNamespace(prefix)`
- **Authorization:** creates need the `write:metadata` scope. Updates and deletes need `write:metadata` allowed by the access policy on the record's effective access tags (`:106-128`). Entities bound to a catalog node inherit that node's tags (`:131-141`).

### 3.3 Implications for F2W

- **Entity and link `properties` are create-only.** Neither update input has a `properties` field (`:369-384`). Editing an existing node's scientific fields or edge evidence therefore needs a revision pattern: create a new entity, re-link, retire the old one; or keep the effective record in F2W's journal. You cannot update properties in place. This matches the old donor note on create-only properties. Re-check it against the final 0.2.19 (spec section 3).
- There is no upsert. Idempotency has to come from F2W lookups by `uri`/`name`, as `tiled_sim.push_fixture_to_tiled` already does (`app/modules/tiled_sim.py:530-560`).
- Access tags (rc2) replace access blobs (0.2.18). The literature corpus scope can map to tags only once the central servers' `access_control` configuration is known (Q-T3 below).
- `isNodeBound` replaces `nodeId`. F2W must not depend on catalog integer IDs.

### 3.4 Required capability checks

**[Proposed]** These run at managed readiness, at external connect, and in the fixture.

| ID | Check | Pass condition | On failure |
| --- | --- | --- | --- |
| C1 | `GET {base}/api/v1/` | 200; `library_version` equals the pinned version string recorded at H1 (e.g. `0.2.19`). The string is compared exactly. | `unsupported_version`. Graph publication is not ready, and nothing falls back. |
| C2 | `POST {base}/api/graphql` `{__typename}` | 200 with `data.__typename == "Query"` | `graph_unavailable` (for example, the tree is not catalog-backed) |
| C3 | Introspect `__type(name:"Mutation")` and `__type(name:"UpdateEntityInput")` | `createEntity`, `createLink`, `updateEntity`, `deleteEntity`, `deleteLink` are present. The adapter records whether `UpdateEntityInput.properties` exists and picks the revision strategy from that. | `capability_missing:<field>`, and dependent operations are blocked |
| C4 | Writer identity: `GET /api/v1/auth/whoami` with the writer credential (ALS and fixture-with-auth) | Principal present. The `admin` role is present where the deployment says so. Only the principal identifier is recorded, never the token. | `writer_unauthorized`. Writes are blocked; reads may continue. |
| C5 | Caller identity (ALS only): `whoami` with the user's token | Verified principal; admin only if `roles` contains `admin`. The writer credential is never used here (D30). | Chat-only, or sign-in required |
| C6 | Read probe: `entities(limit:1)` | Reachable. Empty is allowed and reported as `empty`. | Report `unsigned`/`empty` as today (`tiled_graph.py:225-280`) |

The capability snapshot (version, mutation set, auth providers, writer principal ID, target URL) is attached to every import plan and receipt (JSON_KG_PUBLICATION_SPEC.md).

---

## 4. Decision: modes and lifecycle ownership

**[Proposed]** The trusted server-startup profile chooses one mode. No request, browser setting or model argument can choose or change it.

| | **managed** (`:local` only) | **external** (`:als-dev`, `:als-prod`; also tests against a fixture's URL) | **disposable test** (pytest/CI) |
| --- | --- | --- | --- |
| Owner of the process | F2W supervisor inside the container | Central Tiled operators | The test invocation that created it |
| Operations F2W may run | `start_managed`, `wait_until_ready`, `managed_status`, `stop_managed`; explicit `upgrade_catalog` operator command | `connect`, `check_capabilities`, reads, approved scoped imports | Same as managed, plus `destroy_owned` |
| Operations F2W must never run | Start at import time, delete data on stop, auto-seed, auto-migrate | Start, stop, upgrade, provision, reset; create privileged namespaces silently | Touch central URLs or keys; delete paths it did not create |
| URL | `http://127.0.0.1:8765` (private loopback; proposed internal) | Fixed per profile: `https://tiled-staging.als.lbl.gov/` or `https://tiled.als.lbl.gov/` | `http://127.0.0.1:<ephemeral>` |
| Storage | `/var/lib/fair2wise/tiled` mount (catalog SQLite or Postgres, plus `writable_storage`) | None local | `tmp_path`/`mkdtemp` under a fixture root |
| Credentials | Local writer key generated at first provisioning. Stored at `/var/lib/fair2wise/tiled/secrets/` with mode 0600, or `/run/secrets/tiled_local_writer`. Survives restart. No hard-coded default. | Writer secret from a runtime secret file (Ansible). User tokens come from native sign-in. | Random alphanumeric key per instance, never written outside the instance directory |
| CORS / exposure | No browser origins; not proxied through `/fair2wise` | Not proxied | Loopback only |

### 4.1 Proposed configuration names

These are design names, not existing APIs.

- `F2W_DEPLOYMENT_PROFILE` = `local | als-dev | als-prod | test`. Each image's entrypoint sets a default; the trusted operator can override it. Ansible must not be able to set `local` on an ALS image (T24A).
- `F2W_TILED_MODE` = `managed | external`. Each profile fixes it: local→managed, als-*→external. A mismatch is a startup error.
- `F2W_TILED_URL`. External only; must equal the profile's fixed URL. Normalize the trailing slash (`tiled_graph.normalize_tiled_uri`).
- `F2W_TILED_EXPECTED_VERSION`. Exact string, set from the H1 record.
- `F2W_TILED_WRITER_SECRET_FILE`. Path to a file; never the value itself.
- `F2W_TILED_CA_BUNDLE`. Optional; ALS TLS.
- `F2W_TILED_DATA_DIR`. Managed only; default `/var/lib/fair2wise/tiled`.
- Legacy `TILED_URI`, `TILED_API_KEY` and `F2W_LIVE_TILED` stay accepted for the standalone developer launcher (`scripts/start_agent_backend.sh`) only. The image entrypoints ignore them, so the ambient `.env` cannot redirect a deployed image.

Placement follows TILED_DEPLOYMENT_SPEC.md section 2:

- `app/modules/deployment/tiled_config.py`
- `app/modules/deployment/tiled_lifecycle.py`
- `scripts/tiled_server.py`
- `tests/fixtures/tiled_server.py`
- `app/modules/graph/backends/tiled.py`

### 4.2 Managed configuration for `:local`

**[Proposed]** This is the intent. It still needs `tiled admin check-config` against the final artifact.

```yaml
# /etc/fair2wise/tiled/managed.yml (image content: no secrets, no data)
catalog:
  uri: "sqlite:////var/lib/fair2wise/tiled/catalog.db"   # or TILED_CATALOG_URI
  writable_storage: ["/var/lib/fair2wise/tiled/data"]
  init_if_not_exists: true      # first provisioning only; see lifecycle
authentication:
  single_user_api_key: "${TILED_SINGLE_USER_API_KEY}"    # loaded from the secret file by the supervisor wrapper
  allow_anonymous_access: false # backend authenticates; nothing else reaches :8765
uvicorn: {host: 127.0.0.1, port: 8765}
```

- Turning off `allow_anonymous_access` changes the current dev config (`storage/tiled_config.yml:16`). Only the backend reaches local Tiled, and it holds the key. Whether local readers need to call Tiled directly at all is a T25A question. D34 gives the *application* no-login access, not raw Tiled.
- `init_if_not_exists: true` runs only when the data directory has no catalog yet. If the catalog exists but is at an older revision, startup stops with an actionable error that names `scripts/tiled_server.py upgrade-catalog`. The upgrade is never automatic.
- **H0 E3.** `:local` *may* ship a catalog DB. [Proposed] Do not ship one by default. If the owner wants a seeded local graph, ship a read-only seed at `/usr/share/fair2wise/tiled-seed/` and copy it into the empty mount only through an explicit `tiled_server.py init --from-seed`. The live catalog stays in the mounted volume and never in a writable image layer. The tracked `storage/tiled_catalog.db` is 0.2.18-era and is already excluded (`.dockerignore:29-30` on `forge_merge`).

### 4.3 Managed lifecycle

**[Proposed]**

1. **Start.** The supervisor launches the server, after the gateway is configured and before the API advertises graph readiness. The command is `/opt/tiled/venv/bin/tiled serve config /etc/fair2wise/tiled/managed.yml --host 127.0.0.1 --port 8765`.
2. **Ready.** C1-C3 pass within a bounded timeout. The API's `/health` reports liveness separately from `tiled_ready`.
3. **Stop.** SIGTERM, bounded wait, then SIGKILL. Data is kept.
4. **Restart.** The supervisor applies a backoff policy. Repeated failure marks graph publication not-ready. It must not hide the failure, and it must not restart the container in a loop that loses diagnostics (DEPLOYMENT_STRATEGY.md section 8).
5. **Not allowed.** Starting from `import app...`, from the API process (replaces `scripts/start_agent_backend.sh:219-231` for images), or from the seed step (replaces `:233-234`).

---

## 5. External mode (connect only)

**[Proposed]**

- **Connect.** Build a client from the profile URL, CA bundle and writer secret file. Run C1-C3 and C4. Record `{url, library_version, capability hash, writer principal id}`. The provisioner is never called: external-mode code must not import the lifecycle module, and a test proves this (spec section 5).
- **Version.** If `library_version` is not the H1-recorded string, graph publication is not ready. Reads may continue only if H1 approves that explicitly.
- **Target fixity.** `:als-dev` accepts only `https://tiled-staging.als.lbl.gov/`; `:als-prod` accepts only `https://tiled.als.lbl.gov/`. This replaces the hostname coercion in `tiled_graph.py:153-175` with a profile allowlist of one URL. Local and test profiles reject `*.als.lbl.gov`, which keeps the existing safety intent.
- **Identity.** User identity comes from native Tiled sign-in on that server (AUTHORIZATION_SPEC; T25A). F2W reads the provider list from `about.authentication` (`tiled/server/router.py:285-293`). It does not guess an auth endpoint. Admin status = `whoami.roles` contains `admin` (C5).
- **Writer.** The F2W admin account (D27) is used only inside the trusted publication path. Receipts record `executed_as` as the principal ID.
- **Q-T3 (owner/Tiled team, before H4).** Central `access_control` configuration: which access tags, if any, mark F2W literature scope, and is the F2W writer `admin` or a scoped service principal? This ADR does not assume either.

---

## 6. Failure behavior

**[Proposed; required by DEPLOYMENT_STRATEGY.md section 4 and plan 4.6.]**

| Situation | Behavior |
| --- | --- |
| Central Tiled unreachable or 5xx | `target_unavailable`. Imports stay pending and retryable. JSON snapshots are untouched. **Never start a local server**, never switch to the other central server, never report the import as done. Chat and JSON-only workflows keep working; health reports `tiled_ready=false`. |
| Central version or capability mismatch | `unsupported_version` / `capability_missing`. Writes are blocked and the reason is visible in Settings. |
| Writer credential denied or revoked | `writer_unauthorized`. No retry with a different key, no anonymous write, no empty-success response. |
| User token expired or revoked (ALS) | Sign-in required. Never fall back to `local_trusted` (D30, D34). |
| Managed process crash | The supervisor restarts it with backoff. Readiness is false until C1-C3 pass again. Data is kept. |
| Managed catalog at an older revision | Startup refuses graph readiness and shows the explicit upgrade command. No automatic migration. |
| Missing `/var/lib/fair2wise/tiled` mount on `:local` | Startup error with the required mount and UID/GID. Data never goes into the container layer. |
| Partial read during import planning | Destructive planning is blocked (spec section 5). |

---

## 7. Disposable test-server fixture

**[Proposed]** Design for T20B, which the application owns (plan T20A-T20B).

**Fixture shape.** `tests/fixtures/tiled_server.py` provides `tiled_instance(scope="session" | "function")`. It runs the same `tiled_lifecycle.start_managed` path that `:local` uses, with a test profile.

1. **Isolation.**
   - Create `root = mkdtemp(prefix="f2w-tiled-")` under pytest `tmp_path_factory`.
   - Pick a port by binding `127.0.0.1:0`, then release it. Retry if `serve` reports the port in use.
   - Create a random 64-character alphanumeric key per instance.
   - Write a config into `root` with the catalog at `sqlite:///{root}/catalog.db` and `writable_storage: [{root}/data]`.
   - Start the subprocess with an **explicit env dict**: no inherited `TILED_*`, `F2W_TILED_*` or `.env`, and `HOME={root}`. The subprocess gets its own process group.
2. **Refuse ambient targets.** Abort if any resolved URL is not loopback, or matches `*.als.lbl.gov`. The existing guard at `tiled_sim.py:290-303` can be generalized for this. The fixture never reads central URLs or keys from settings.
3. **Ownership manifest.** Write it *before* the process starts: `{run_id, nonce, root, port, pid, pgid, created_utc, tiled_version, owner: "f2w-test"}`, saved to `{root}/.f2w-owned.json`. Teardown acts only on resources listed in a manifest whose nonce matches the in-memory fixture.
4. **Health.**
   - Poll `GET /healthz`, then C1 (version), C2 and C3, with a timeout. Default 30 s, configurable.
   - On timeout, collect the log tail and tear down owned resources.
   - The test is marked failed. It is **never** skipped and never retried against another server.
5. **Seed.**
   - Load deterministic fixtures through the same importer path as production: synthetic literature JSON plus a FORGE-sentinel set (spec section 5).
   - Fixed seed, for example the existing `20260916` convention (`tiled_sim.py:51`).
   - Write a seed receipt.
6. **Identities.**
   - The default instance uses `single_user_api_key`, which models the local writer.
   - An `auth` variant configures `DictionaryAuthenticator` (`tiled/authenticators.py:46`) plus `tiled_admins` and `TagBasedAccessPolicy` (`tiled/access_control/access_policies.py:66`). It also needs an authn `database` under `root`. Users: `admin`, `reader`, `owner`, and a separate `writer` service principal. ALS-mode connect-only tests use this variant for role, revocation and downgrade checks (T25).
7. **External-mode coverage.** A second client connects to the fixture through `F2W_TILED_MODE=external` and the fixture URL. Assertions: no lifecycle hook is called; C1-C4 pass; denial gives no fallback.
8. **Teardown.**
   - SIGTERM the pgid, wait at most 10 s, SIGKILL if needed, and confirm the PID is gone.
   - `rmtree(root)` only if `root` is under the fixture base, the manifest nonce matches, and the realpath does not resolve through a symlink outside `root`.
   - Write a teardown receipt.
   - Cancellation and startup failure use the same path (`try/finally` and `atexit`).
   - The fixture never deletes a database it did not create and never calls a reset or delete mutation on any non-fixture URL.
9. **Persistence test.** Stop and restart the same `root`. Graph contents must survive. This proves normal stop keeps data.

**Never central.** No fixture, CI job or default test touches `tiled-staging` or `tiled`. Production and staging smoke checks are separate, explicitly authorized and read-only (spec section 4).

---

## 8. Consequences

- Every image and the fixture install the managed server from one hash-pinned isolated lock. That lock cannot be written until Q-T1 is answered.
- The F2W lock needs a reviewed `pydantic-settings` resolution only if the Tiled client goes into the app environment. HTTP-only access avoids that.
- The T08B importer needs a revision strategy for properties, unless the final 0.2.19 adds property updates (C3 detects this).
- The `tiled_graph` and `tiled_sim` local-only guards become profile rules, not silent rewrites.
- Settings `tiled_uri` becomes read-only in deployed profiles.

## 9. Alternatives considered

- **Keep the compose sidecar for `:local`.** Rejected: DEPLOYMENT_STRATEGY.md section 2 requires an all-in-one local image.
- **Use 0.2.18 or rc2 now.** Rejected: D22; spec section 7 forbids substitution.
- **Install Tiled into the app venv.** Rejected for now because of the `pydantic-settings` conflict (section 1.2).
- **Let the edge proxy expose local Tiled.** Rejected: the strategy section 2 says no general admin proxy.

## 10. Open items for H1

- Q-T1, Q-T2: final artifact identity and source.
- Q-T3: central ACL and writer principal.
- Owner check of the decision not to bundle a catalog in `:local` (section 4.2).
- `allow_anonymous_access: false` for managed mode.
