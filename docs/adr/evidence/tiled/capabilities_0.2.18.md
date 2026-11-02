# Tiled 0.2.18 capability re-check (T20B)

- **Date:** 2026-10-14. **Branch:** `forge_merge-T20B-tiled` (from `forge_merge` b7650e3).
- **Why:** owner decision O-T1 pins `tiled==0.2.18` as an interim exception to D22. The
  T20A ADR (`docs/adr/tiled-deployment.md`) described the API from the **0.2.19rc2** wheel.
  This file records what 0.2.18 actually does, observed against disposable local servers.
- **Scope:** only loopback servers started by this task on ephemeral ports with temporary
  storage. No ALS host was contacted. The servers on ports 8011 and 8765 were not touched.
- **Reproduce:** `tests/tiled/test_tiled_capabilities_0218.py` asserts every row below
  (plus `test_tiled_server_lifecycle.py` for lifecycle facts). Run them with
  `F2W_TILED_PYTHON` set, as shown in section 1.

Labels: **[Observed]** means checked against a running 0.2.18 server. **[Source]** means
read in the installed 0.2.18 package (`site-packages/tiled/...`, line numbers from 0.2.18).

## 1. Artifact and environment

**[Observed]** The wheel was downloaded from PyPI into an empty directory and hashed
before install:

```
$ curl -sSfLO https://files.pythonhosted.org/packages/7d/70/85966182bc5fd7a5edd63290849ee4b7ebba8f61375780b782b1427f0bed/tiled-0.2.18-py3-none-any.whl
$ shasum -a 256 tiled-0.2.18-py3-none-any.whl
416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06  tiled-0.2.18-py3-none-any.whl
```

This matches O-T1 and the ADR table. It was installed into its own venv, never into the
app venv (the app lock pins `pydantic-settings==2.14.2`; Tiled requires `<2.12`):

```
$ uv venv --python <app-python-3.12.11> venv-tiled
$ cat req.txt
tiled[server] @ file:///.../tiled-0.2.18-py3-none-any.whl --hash=sha256:416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06
$ uv pip install --python venv-tiled/bin/python -r req.txt      # uv 0.6.10
$ venv-tiled/bin/python -c "import tiled; print(tiled.__version__)"
0.2.18
```

Only the `tiled` wheel is hash-checked. Its dependencies were resolved by uv at install
time (appendix A). Notable resolutions: `pydantic-settings==2.11.0`, `sqlalchemy==2.1.4`,
`strawberry-graphql==0.335.0`, `fastapi==0.143.1`, `uvicorn==0.54.0`. 0.2.18 does not
bound `sqlalchemy` (rc2 adds `<2.1`). All tests here passed on SQLAlchemy 2.1.4, but
images need a hash-pinned lock for this environment (ADR section 8).

## 2. Observed contract

| # | Item | 0.2.18 behaviour | Evidence |
| --- | --- | --- | --- |
| 1 | Service info (C1) | `GET /api/v1/` → `library_version: "0.2.18"`, `api_version: 0`; served without credentials | [Observed] |
| 2 | Health | `GET /healthz` → `{"status":"ready"}` | [Observed]; `server/app.py:264-266` |
| 3 | Graph endpoint (C2) | `POST /api/graphql {__typename}` → `Query`; mounted only for a catalog-backed tree | [Observed]; `server/app.py:433-440`, `graph/router.py:133` |
| 4 | Query fields | `entity(id)`, `entities(entityType, limit, offset)`, `catalogNodeId(path)`, `link(id)`, `links(subjectId, predicate, objectId, limit, offset)`, `namespaces` | [Observed] introspection; `graph/schema.py:355-441` |
| 5 | Mutation fields | `createEntity`, `createLink`, `updateEntity`, `updateLink`, `deleteEntity` (cascades to links), `deleteLink`, `upsertNamespace`, `deleteNamespace` | [Observed]; `graph/schema.py:444-617` |
| 6 | Create inputs | `CreateEntityInput{entityType, name, nodeId, uri, properties, accessBlob}`, `CreateLinkInput{subjectId, predicate, objectId, properties, accessBlob}` | [Observed] |
| 7 | Entity output | `id, nodeId, entityType, name, uri, properties, createdAt, outgoingLinks(predicate, limit, offset), incomingLinks(...)`; **no `accessBlob` field** | [Observed]; `graph/schema.py:186-238` |
| 8 | Link output | `id, subjectId, predicate, objectId, properties, accessBlob, createdAt, subject, object` | [Observed] |
| 9 | Create / read | Create returns the entity with server UUID `id`; `entity(id)`, `links(subjectId:)` and nested `subject`/`object` read back properties and names | [Observed] |
| 10 | Neighborhood | No neighborhood query. One-hop traversal is `entity { outgoingLinks { object } incomingLinks { subject } }`. Query depth is limited to 10 (`'anonymous' exceeds maximum operation depth of 10`) | [Observed]; `graph/schema.py:53,627` |
| 11 | Properties are create-only | `UpdateEntityInput{name, nodeId, uri, entityType, accessBlob}`, `UpdateLinkInput{predicate, accessBlob}`. Sending `properties` → `Expected value of type 'UpdateEntityInput' not to include unknown field 'properties'` (same for links) | [Observed] |
| 12 | **Update defect** | `updateEntity`/`updateLink` **without `accessBlob` fail**: `(builtins.TypeError) Type is not JSON serializable: UnsetType`. The schema passes strawberry's `UNSET` to the store, which checks its own sentinel. Passing `accessBlob` explicitly (`{}` without an access policy) works | [Observed]; `graph/schema.py:531,580` vs `graph/store.py:50,292,395` |
| 13 | Upsert / uniqueness | No upsert. Two `createEntity` calls with the same `uri` both succeed with different ids | [Observed] |
| 14 | Namespaces / CURIEs | After `upsertNamespace(prefix:"ex", uri:"http://example.org/")`, a predicate written as full IRI or as `ex:rel` reads back compacted (`ex:rel`); filtering by the full IRI matches | [Observed] |
| 15 | Access control model | **Access blobs**, not access tags. `accessTags` → `unknown field 'accessTags'. Did you mean 'accessBlob'?`. Tags live inside the blob, `{"tags": [...]}`, interpreted by `TagBasedAccessPolicy`. With no policy, any blob is stored as given | [Observed]; `access_control/access_policies.py:66-240` |
| 16 | Node binding | `nodeId` (catalog integer id) on input and output; no `isNodeBound`; `entities` has no `nodePathParts` argument; `catalogNodeId(path)` resolves a path to the integer id | [Observed] |
| 17 | Single-user API key mode | `about.authentication = {"required": true, "providers": [], "links": null}`. No `/api/v1/auth/*` routes: `whoami` is **404**. A wrong key → 401 `Invalid API key` | [Observed]; `server/app.py:453-491` |
| 18 | **Anonymous graph listing** | With `allow_anonymous_access: false` and no access policy, anonymous REST is 401 and anonymous `entity(id)` returns `null`, but **anonymous `entities` and `links` list every record**. Listing resolvers apply only access-policy filters, never the caller's `read:metadata` scope. `namespaces` does check the scope (`[]`). With `TagBasedAccessPolicy` configured, anonymous listing returns `[]` | [Observed]; `graph/schema.py:367-385,407-432` |
| 19 | Writes need `write:metadata` | Anonymous `createEntity` → `Not permitted` | [Observed]; `graph/schema.py:448` |
| 20 | `tiled serve config` leaks a key | The CLI prints `http://host:port?api_key=...` to stderr unless `--api-key` is given on the command line (visible in `ps`). The printed key is from the process default settings, not the configured key, so it is misleading as well. The F2W launcher (`app/modules/deployment/_tiled_entry.py`) avoids both | [Observed]; `commandline/_serve.py:723-729`, `server/app.py:1025-1056` |
| 21 | Config env precedence | `TILED_*` environment variables override the config file (`settings_customise_sources` puts env first). The managed launcher therefore runs with an explicit minimal environment | [Source] `config.py:47-57` |
| 22 | Startup time | 3.5-5 s from spawn to ready on macOS arm64 (SQLite catalog) | [Observed] |

### 2.1 Multi-user authentication (DictionaryAuthenticator)

**[Observed]** One test server process supports real principals:

- Config: `authentication.providers: [{provider: f2wtest, authenticator: tiled.authenticators:DictionaryAuthenticator}]`,
  `tiled_admins: [{provider: f2wtest, id: admin}]`, `database: {uri: sqlite:///<root>/authn.db}`,
  plus `secret_keys`. F2W injects passwords and keys from a 0600 file at launch.
- `about.authentication.providers[0]` = `{"provider": "f2wtest", "mode": "internal", "links": {"auth_endpoint": ".../api/v1/auth/provider/f2wtest/token"}, ...}`;
  `about.authentication.links` = `whoami, apikey, refresh_session, revoke_session, logout`.
- Password login: `POST /api/v1/auth/provider/f2wtest/token` (form `username`, `password`) →
  `access_token, refresh_token, expires_in, refresh_token_expires_in, token_type, identity, principal`.
- **whoami shape** (`GET /api/v1/auth/whoami`, `Bearer`): `{uuid, type: "user"|"service", identities: [{id, provider, latest_login}], roles: [{name, scopes}], api_keys: [...], sessions: [{uuid, expiration_time, revoked, state}], latest_activity}`.
  - The admin gets roles `["user", "admin"]`; the reader gets `["user"]`.
  - With no credential, whoami returns 200 with body `null`. A malformed bearer token → 401 `Could not validate credentials`.
- Default role `user` has `read:metadata, read:data, create:node, write:metadata, write:data, delete:revision, delete:node, create:apikeys, revoke:apikeys`.
  Role `admin` adds `register, admin:apikeys, read:principals, write:principals, metrics, read/write:webhooks`
  but **not** `create:apikeys`; admins get that from their `user` role (`authn_database/core.py:33-73`).
- **Without an access policy, a plain `reader` can create graph entities.** `write:metadata` comes from the `user` role.
- Listing principals (`GET /api/v1/auth/principal`) is admin-only: the reader gets 401 `Not enough permissions. Requires scopes ['read:principals']`.
- **Service writer:**
  - `POST /api/v1/auth/principal?role=admin` (admin token) creates `{type: "service", identities: [], roles: [admin]}`.
  - `POST /api/v1/auth/principal/{uuid}/apikey {"expires_in": 600, "scopes": ["inherit"]}` returns `{secret, first_eight, expiration_time, scopes, access_tags, note, latest_activity}`.
  - The key works with `Authorization: Apikey <secret>`, and whoami resolves it to the service principal.
- **Revocation:** `DELETE /api/v1/auth/principal/{uuid}/apikey?first_eight=...` → 204. The next whoami with that key → 401 `Invalid API key`.
- Refresh: `POST /api/v1/auth/session/refresh {"refresh_token"}` → new tokens.

### 2.2 Tag-based access policy (ACL variant)

**[Observed]** `TagBasedAccessPolicy` works in 0.2.18. It needs a tags database compiled
by `AccessTagsCompiler`, and 0.2.18 has no CLI for that step, so the F2W launcher does it
(`_tiled_entry.py --compile-tags`). The fixture tags are: `reader` may read
`f2w_literature`; `owner` may read and write it and owns it; `admin` is a Tiled admin.

| Actor | Result |
| --- | --- |
| anonymous `entities` | `[]` (policy closes item 18) |
| reader creates with `{"tags": ["f2w_literature"]}` | rejected: `user='reader' is not an owner of tag='f2w_literature'` |
| owner creates with `{"tags": ["public"]}` | rejected: only Tiled admins can apply `public` |
| untagged create by any user | allowed; blob becomes `{"user": <id>}` (user-private) |
| reader lists | sees tagged records + own private ones |
| owner lists | sees tagged + own |
| admin lists | sees everything (admin bypasses tag filters) |
| reader `deleteEntity` on a tagged record | `Not permitted` |

### 2.3 Edit-relevant facts (T20C, probes P1-P9)

**[Observed]** against disposable 0.2.18 servers (single-user, and multi-user with
`TagBasedAccessPolicy`); each row is one asserting test in
`tests/tiled/test_tiled_edit_facts_0218.py` (a server where a fact changes fails exactly that
test). The observation-only versions live in `tests/tiled/test_edit_plan_probes.py`. The facts
are what `docs/migration/ui_contracts/tiled_graph_editing_plan.md` sections 1-2 build on.

| # | Fact | 0.2.18 behaviour | Capability flag |
| --- | --- | --- | --- |
| P1 | `deleteEntity` deletes **foreign** links attached to the deleted entity, in both directions | FORGE incoming and outgoing links read back `null`; the FORGE entity survives | `delete_cascades_foreign_links = True` |
| P2 | Under `TagBasedAccessPolicy`, a tag owner deleting its own tagged entity also deletes an admin-created link the owner cannot read | Owner reads the link: `null`; owner `deleteLink` on it: `Not permitted`; after the owner's `deleteEntity`, admin reads the link: `null` | (same flag) |
| P3 | Changing an entity's URI and `entityType` (how F2W retires: `f2w:Retired`) keeps every link, including foreign ones | The foreign link keeps its endpoints; `incomingLinks`/`outgoingLinks` on the retired entity list it; `entities(entityType: "Material")` no longer lists it, `entities(entityType: "f2w:Retired")` does | - |
| P4 | Link endpoints cannot change | `updateLink` with `objectId` or `subjectId`: `unknown field`; `UpdateLinkInput = {accessBlob, predicate}`; incoming links are found with `links(objectId:)` | - |
| P5 | Query surface | `entities(entityType, limit, offset)` only (`Unknown argument 'uri'`, `'properties'`); `links(subjectId, predicate, objectId, limit, offset)`; mutations exactly `createEntity, createLink, updateEntity, updateLink, deleteEntity, deleteLink, upsertNamespace, deleteNamespace`: no batch, transaction or conditional write | - |
| P6 | An access-blob change is **silently ignored without an access policy** | `updateLink` with a new blob: response and re-read show the old blob while a `predicate` change in the same call applied | `blob_update_effective = False` |
| P7 | An entity's access blob cannot be read | `Cannot query field 'accessBlob' on type 'Entity'`; `Link` has the field | - |
| P8 | Two real snapshots of one corpus get two graph ids, and the per-version importer (`tiled_json`, T08B) leaves v1 live when v2 is imported | `create_snapshot` twice: `synthetic_v1`, `synthetic_v2`; after both imports 14 + 14 owned live entities; the v2 plan has 0 `retire_node` operations | - (T08C replaces the scope key by the corpus) |
| P9 | Revising a node in place (T08B) leaves a FORGE link on the retired revision and the import still verifies | Import `applied`, verified; the FORGE link's `objectId` is the old UUID whose URI is now `f2w:kg/<graph>/retired/...` | - (T08C's boundary-link report closes the gap) |
| ACL | Owner rights under the tag policy | A tag owner may change the URI of its own tagged entity; a reader may not update it (`Not permitted`); an admin deletes anything | - |

**[Source]** (0.2.18 package; editing plan section 4.2):

- Links reference entities with `ForeignKey("entities.id", ondelete="CASCADE")` on both ends
  (`tiled/graph/orm.py:138,145`). P1 and P2 are the database cascade, not the resolver, so no
  access policy can prevent them.
- `deleteEntity` checks only the entity's effective access blob (`graph/schema.py:508-519`);
  `deleteLink` checks the link's blob (`:561-570`).
- `_modify_access_blob` returns the **current** blob when no policy is configured
  (`graph/schema.py`), which is P6.
- The T18C closure test built "v2" by copying v1's manifest, so it kept graph id `synthetic_v1`
  and exercised in-place convergence; `create_snapshot` does not (P8).

The two flags are version-keyed in `tiled_capabilities.KNOWN_BEHAVIOUR` (unknown builds get the
fail-closed values: cascade assumed, blob updates assumed ineffective) and appear on every
`CapabilityReport`; the quirk strings `deleteEntity-cascades-foreign-links` and
`accessBlob-update-ignored-without-policy` join `KNOWN_QUIRKS["0.2.18"]`.

## 3. Differences from the rc2-based ADR (`docs/adr/tiled-deployment.md`)

1. **Access model.** The ADR (3.2, 3.3, 7.6) uses rc2 `accessTags` inputs and
   "access tags replace access blobs". In 0.2.18 the GraphQL inputs are `accessBlob` (JSON).
   Tags are carried inside the blob as `{"tags": [...]}`, and only `TagBasedAccessPolicy`
   interprets them. Inputs naming `accessTags` are rejected.
2. **Node binding.** ADR 3.2/3.3 uses `nodePathParts` and `isNodeBound`. 0.2.18 has neither.
   It has `nodeId` (an integer) and `catalogNodeId(path)`. `entities(nodePathParts:)` and
   `updateEntity(nodePathParts:)` do not exist. `tiled_graph.py`'s `nodeId` query works
   on 0.2.18 as written.
3. **Updates are broken without `accessBlob` (new).** The ADR lists `updateEntity`/`updateLink`
   as usable. On 0.2.18 every update must send `accessBlob` explicitly. Callers must also
   know that, under a tag policy, a changed blob goes through `modify_node` checks.
4. **Properties are create-only: confirmed** (ADR 3.3). It holds for both entities and links.
   The revision pattern is still required. C3 detects this (`entity_properties_updatable=False`).
5. **No neighborhood query.** The ADR did not list one; T08B and the UI must use nested
   `outgoingLinks`/`incomingLinks`. Depth is limited to 10, so multi-hop needs several queries.
6. **whoami (C4) is unavailable in single-user API key mode** (404). ADR C4 assumed whoami
   for the writer. In `:local`/fixture single-user mode, F2W checks the key with an
   authenticated `GET /api/v1/metadata/` instead (`tiled_capabilities._check_writer`) and
   records `writer_principal = "single-user-api-key"`. A required role cannot be verified
   in that mode, and the check fails closed.
7. **`allow_anonymous_access: false` does not protect graph listings** (item 18). ADR 4.2
   assumes it protects reads. On 0.2.18 any process that can reach the port can list every
   entity and link without a key, unless an access policy is configured. The managed
   server binds 127.0.0.1 only, so within one `:local` container this matters only for
   co-resident processes. It does matter for any multi-user host. Decision needed (open question 1).
8. **Admin vs reader write rights.** Spec section 4 and ADR 7.6 expect reader/owner ACL
   checks. In 0.2.18, without a policy, any signed-in user can write the graph. Only
   `TagBasedAccessPolicy` (section 2.2) restricts non-admins. Admins bypass tag checks, as
   spec section 1 already notes.
9. **Catalog migrations.** The rc2 migrations (access blobs → tags, and three others) are not
   in 0.2.18, so the tracked 0.2.18-era `storage/tiled_catalog.db` needs no upgrade under
   this pin. A later move to 0.2.19 needs the explicit `scripts/tiled_server.py upgrade-catalog`.
10. **Version check.** C1 is unchanged. A 0.2.19 central server would fail F2W's exact-0.2.18
    check with `unsupported_version` (as O-T1 requires: "fail clearly on mismatch").
11. **`tiled serve config` prints an API key URL** (item 20), and the ADR's managed start command
    (4.3 step 1) uses that CLI. F2W uses its own launcher instead, which reads the key from a
    file and prints nothing secret.
12. **docker-compose healthcheck (`curl` in `python:3.11-slim`)**: not re-checked here. No container was run.

Unchanged and confirmed: `GET /api/v1/` and `/healthz`; graph mounted only with a catalog;
`createEntity(input:{entityType,name,uri,properties})` and `createLink(input:{subjectId,
predicate,objectId})` as used by `tiled_graph.py`/`tiled_sim.py`; `entities(entityType,
limit, offset)`; `links(predicate, limit, offset)`; `deleteEntity` cascades; listing filters
run in SQL before LIMIT (under a policy); `tiled_admins` grants role `admin`; single-user
keys must be alphanumeric (`server/app.py:581`).

## 4. What T25C can rely on (auth and principals)

- A **single-process disposable server** supports real principals.
  `tests/fixtures/tiled_server.py` provides:
  - `tiled_auth_instance`: DictionaryAuthenticator, users `admin` (Tiled admin) and `reader`, no access policy.
  - `tiled_acl_instance`: users `admin`, `reader`, `owner`, with `TagBasedAccessPolicy` and `TEST_ACCESS_TAGS`.
  - Helpers: `login(user)` returns bearer tokens; passwords are generated per instance and kept in memory and in the 0600 instance file.
- A **separate writer service principal** is created by the admin at test time (2.1). It
  gets an `Apikey` credential and can be revoked. `check_capabilities(...,
  require_writer_role="admin")` verifies it and records only the principal uuid.
- Facts for the `tiled_identity.py` TODO(T25C) items:
  - **D-T4:** `tiled_admins` maps to the role named `admin` in `whoami.roles`.
  - **D-T8:** per-principal API keys with `Authorization: Apikey` work, and so does revocation.
  - **whoami:** anonymous → `null`; a bad token → 401.
- Not covered here, still for T25C:
  - OIDC/external providers (the ALS servers' real sign-in).
  - Access-token expiry timing (default 15 min) and the revoked-session window (D-T5/D-T6).
  - Role downgrade through the API (0.2.18 has no role-edit route in the auth router; it would need direct authn-DB edits or `tiled_admins` changes and a restart).
  - Whether the central 0.2.19 servers' `access_control` matches either fixture (Q-T3).

## 5. Changes needed outside T20B (not made here)

- `app/modules/tiled_graph.py:153-175,198-222`: `coerce_local_tiled_uri` silently rewrites any
  `*.als.lbl.gov` URL to `127.0.0.1:8765`. Replace it with the profile rule in
  `tiled_config.validate_external_url` (ALS profile = its one fixed URL; local/test reject
  ALS hosts with an error, never a rewrite).
- `tiled_graph.py`/`tiled_sim.py` read `TILED_API_KEY`/`TILED_URI` from `os.environ` per
  call. They should take the URL and credential from `TiledSettings` and the secret file.
- Any F2W graph update (T08B, UI edits) must send `accessBlob` with `updateEntity`/`updateLink` (item 12).
- `scripts/start_agent_backend.sh:212-240` and `docker-compose.yml:79-119` still start an
  unmanaged Tiled with a hard-coded default key, and seed it on every start. Replace them with
  `scripts/tiled_server.py start` (T24B).
- `requirements/`: add a hash-pinned lock for the isolated Tiled server environment (appendix A is the resolution observed here).

## Appendix A: resolved server environment (uv, 2026-10-14, macOS arm64, CPython 3.12.11)

`tiled[server] @ tiled-0.2.18-py3-none-any.whl` (sha256 verified) plus:
adbc-driver-manager 1.12.0, adbc-driver-postgresql 1.12.0, adbc-driver-sqlite 1.12.0,
aiofiles 25.1.0, aiosqlite 0.22.1, alembic 1.20.0, annotated-doc 0.0.5, annotated-types 0.8.0,
anyio 4.15.1, argon2-cffi 25.1.0, argon2-cffi-bindings 26.1.0, asgi-correlation-id 5.0.1,
asyncpg 0.32.0, attrs 26.1.0, awkward 2.14.0, awkward-cpp 57, blosc2 4.14.1, cachetools 7.2.1,
canonicaljson 2.0.0, certifi 2026.7.22, cffi 2.1.1, click 8.5.0, cloudpickle 3.1.2,
cross-web 0.7.0, cryptography 50.0.2, dask 2026.8.0, donfig 0.8.1.post1, duckdb 1.3.2,
ecdsa 0.19.2, entrypoints 0.4, et-xmlfile 2.0.0, fastapi 0.143.1, fsspec 2026.9.0,
google-crc32c 1.9.0, graphql-core 3.3.0, greenlet 3.5.6, h11 0.16.0, h2 4.4.1, h5netcdf 1.8.1,
h5py 3.16.0, hdf5plugin 7.1.0, hpack 4.2.0, httpcore 1.0.9, httptools 0.9.0, httpx 0.28.1,
hyperframe 6.1.0, idna 3.20, importlib-resources 7.1.0, jinja2 3.1.6, jmespath 1.1.0,
json-merge-patch 0.3.0, jsonpatch 1.35, jsonpointer 3.2.1, jsonschema 4.26.0,
jsonschema-specifications 2025.9.1, llvmlite 0.50.0, locket 1.0.0, lz4 4.4.5, mako 1.4.3,
markdown-it-py 4.2.0, markupsafe 3.0.4, mdurl 0.1.2, minio 7.2.20, msgpack 1.2.3,
msgspec 0.22.0, ndindex 1.10.1, numba 0.68.0, numcodecs 0.17.0, numexpr 2.14.2, numpy 2.5.4,
obstore 0.11.1, openpyxl 3.1.5, opentelemetry-api 1.45.1, orjson 3.13.0, packaging 26.3,
pandas 3.0.6, partd 1.4.2, pillow 12.3.0, platformdirs 4.13.0, prometheus-client 0.26.0,
pyarrow 26.0.0, pyasn1 0.6.4, pycparser 3.11, pycryptodome 4.0.0, pydantic 2.14.1,
pydantic-core 2.50.1, pydantic-settings 2.11.0, pygments 2.21.0, python-dateutil 2.9.0.post0,
python-dotenv 1.2.4, python-jose 3.5.0, python-multipart 0.0.32, pyyaml 6.0.3, ragged 0.3.0,
redis 8.1.0, referencing 0.37.0, rich 15.0.0, rpds-py 2026.9.1, rsa 4.9.1, shellingham 1.5.4,
six 1.17.0, sparse 0.19.2, sqlalchemy 2.1.4, stamina 26.1.0, starlette 1.8.0,
strawberry-graphql 0.335.0, tenacity 9.2.1, threadpoolctl 3.7.0, tifffile 2026.9.20,
toolz 1.2.0, typer 0.27.3, typing-extensions 4.16.0, typing-inspection 0.4.4, urllib3 2.8.0,
uvicorn 0.54.0, uvloop 0.23.0, watchfiles 1.3.0, websockets 17.2, xarray 2026.9.0,
zarr 3.4.1, zstandard 0.25.0.

## Open questions

1. **Anonymous graph listing (item 18).** Should managed `:local` always run with an access
   policy (for example a single-tag `TagBasedAccessPolicy`) to close it? Or is loopback-only binding
   inside the container enough? This T20B leaves the single-user default unchanged and documents it.
2. **Writer in `:local`.** In single-user mode no principal exists, so `executed_as` can only be
   `single-user-api-key`. Is that enough for local audit (D27)? The alternative is to run `:local`
   multi-user with a service principal.
3. **Dependency lock.** Is the appendix A resolution (notably SQLAlchemy 2.1.x, outside rc2's
   bound) acceptable as the basis of the isolated lock, or should it be pinned to SQLAlchemy 2.0.x?
