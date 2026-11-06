# UI graph sources: reading JSON KGs and Tiled KGs in the graph view (H8 review packet)

Status: implemented on branch `forge_merge-T-uigraph`. **Evidence for review, not owner approval.** Nothing here
approves a gate; owner-gated items are listed in section 7.

Owner instruction (2026-11-06, verbatim): "in the UI we need to support reading both json KGs and Tiled KGs.
Follow best practices and do what needs to be done".

## 1. Problem

The graph view loaded `GET /graph`, which always answered the one workspace JSON file chosen by Settings
(`storage/kg/matkg_rsoxs_v1.json` by default). The `:local` container publishes JSON releases into its own managed
Tiled (`e2e_demo/v1` today), so a user could not see that Tiled graph, nor any other JSON KG or adopted release,
without changing Settings (which also changes what chat retrieves from).

## 2. What was added

A request may name a **graph source**; the server answers in the same node/edge wire shape (`GraphPayload`,
`GraphNode`, `GraphNodeSearchResponse`) so the UI renders both kinds. No `source` means the old code path, byte for
byte (`tests/api_v1/test_v1_graph_sources.py::test_default_graph_is_unchanged_without_a_source`; the frozen legacy
contract fixtures are untouched).

### Routes

| Method and path | Entry capability (policy row) | Change |
|---|---|---|
| `GET /v1/graph/sources` | `chat.read_support` (V80, new; registered on `:local` and ALS) | lists the selectable sources |
| `GET /graph` | `chat.read_support` (R06) | optional query `source` |
| `POST /graph/nodes/search` | `chat.read_support` (R07) | optional query `source` (the body is unchanged) |
| `GET /graph/neighborhood/{node_id:path}` | `chat.read_support` (R08) | optional query `source` |
| `GET /graph/node/{node_id}` | `chat.read_support` (R09) | optional query `source`; wins over `json_graph_path`, which is then not read |
| `PATCH /graph/node/{node_id}` | `graph.edit` (R10) | optional query `source`: any value is `400 read_only_source` after the capability check |

The legacy routes keep their policy rows; only the OpenAPI snapshots gained the optional parameter
(`tests/contracts/legacy_ui/fixtures/openapi_ui.json`, `docs/migration/ui_contracts/openapi_baseline.json`). The
route count of `/v1` is 64 (47 on ALS); `docs/migration/api/README.md` has the V80 row.

### Source ids

`json:<name>` (a `storage/kg/*.json` file or registered KG bundle; name = file stem without `matkg_`),
`json:<corpus>/<vN>` (adopted release `<workdir>/storage_kg/<corpus>/<vN>/graph.json`, **`:local` only**),
`tiled:<graph_id>` (one F2W literature graph in the image's own Tiled, **`:local` only**).

Ids are **opaque and allowlisted**: the server builds the catalog from its own listings
(`GraphSources.listing`) and a request id is only a dictionary key into it. It is never joined to a path, parsed as
a URL or used to choose a Tiled target. A malformed or unlisted id is `404 {"detail": {"code": "unknown_source"}}`
before any file or Tiled access (path traversal, absolute path, URL, NUL and over-long cases are tested). Release
directories must match `[a-z0-9][a-z0-9_-]*/v<N>`, may not be symbolic links, and are looked up with `glob`, not
built from input.

### Listing body

```json
{"sources": [{"id": "tiled:e2e_demo", "kind": "tiled", "label": "e2e_demo / v1", "read_only": true,
              "available": true, "node_count": 268, "edge_count": 126, "revision": "v1@ab12...", "detail": null}],
 "tiled": {"status": "ok|unavailable|not_offered", "detail": null}}
```

`read_only` is `true` for both kinds (there is no existing editable JSON/Tiled mode behind this route: JSON mode is
already read-only and a Tiled edit needs the journaled Save path, not a source). JSON counts are `null` until the
file has been read once (counting would parse it). A Tiled outage is reported in `tiled` and never fails the listing.

### Tiled mapping

One complete entity scan and link scan (`TiledMutationClient.scan_entities/scan_links`, through a read-only wrapper
that exposes only those two methods). A record is a node/edge of graph `G` only by the T08A rule
(`identity_adapter.literature_entity_key` / `literature_link_key`: owner marker, URI and properties agree):

- pending, retired and the `f2w:Release` marker are never nodes (the marker only supplies the label and revision);
- entities of other owners (FORGE) and links to non-final or foreign endpoints are ignored;
- two final entities for one node mark the graph `available: false` and reads answer `502 source_inconsistent`
  (0.2.18 has no uniqueness; the reader never picks one).

The node is the stored node payload minus the reserved `f2w_*` markers, topped up from `name`/`entityType`, then run
through the same converter as JSON nodes (`_graph_node_from_raw`), so `id`, `label`, `type`, `description`,
`publications`, `properties`, provenance fields and linked code snippets look the same. Edges carry `predicate`.
`graph_id` is the Tiled graph id; `source_path` is `source:<id>` for every source payload.

## 3. Authorization and exposure

- Same entry capability as the existing graph reads (`chat.read_support`); no capability was added or weakened. The
  ALS role check and projection (`guard.project_route`, `project_api_response`) run exactly as for the old routes.
- **Tiled is a source on `:local` only, and only the managed loopback Tiled that is the deployment's own target**
  (`local_publication.local_graph_scanner`: the same condition as the H5 publication gate; wired by the trusted
  `local_publication` flag of `create_api_app`, never by a request, setting or environment value naming a target).
  On ALS profiles no scanner is wired (no new reachability); the listing says `not_offered`, and a `tiled:` id is
  `404`. The writer credential is read lazily per request by the client from the managed secret file and never
  reaches a response, log line or repr (`ContractError` texts that contain the base URL are replaced by a fixed
  message).
- Release snapshots are listed on `:local` only. Flat JSON files are listed on every profile, the same data class
  `GET /graph` already serves every reader (owner decision O-A5 allows `json_graph_path` for every reader).
- Edits: `PATCH` with any `source` is refused (`400`, `read_only_source`, after the `graph.edit` check, so an ALS
  non-editor still gets 403 first). The source layer has no write path.

## 4. Limits, caching, blocking

- A graph payload is cut at 100,000 nodes (edges between kept nodes only) and marked with the response header
  `X-F2W-Graph-Truncated: true`; a JSON file over 128 MiB is `413 source_too_large`; a Tiled scan stops at the
  client's own bounds (200,000 entities, 500,000 links) and a partial scan is an error, never a partial graph.
- Neighborhood: undirected, hops clamped to 1..4, at most 500 nodes. Search: lexical over label, id and
  description, at most 25 results.
- Tiled scans are shared by every source and request for 20 s (failures for 3 s, so a down Tiled is not hammered); JSON
  graphs are cached by `(path, mtime, size)`, four at most. Staleness bound: a published release shows within 20 s.
- Every source read runs in `asyncio.to_thread` (the scan and conversion are blocking); the lock serializes scans.

## 5. UI

`ui/src/app/components/GraphSourceSelect.tsx` (selector with kind badge, loading state, `Tiled unavailable`, error
alert), `graphSourceStore.ts` (selection and status, persisted per viewer in `localStorage` key
`fair2wise-graph-source-v1`, every access in try/catch), `graphSourcesApi.ts`/`graphSourcesHooks.ts` (listing),
`liveAgent.ts` (`source` on graph, search, node and neighborhood requests; absent for the default graph),
`GraphMockup.tsx` (selector in the KG viewer toolbar; requests carry the source of the displayed payload, Edit hidden
for a source), `App.tsx` (reload, stale-response guard, error state clears the canvas), `agentApiErrors.ts` (typed
`detail.message`). The selector hides itself when the listing route answers 401/403/404/405 (older server). The
chat/answer graph and Settings are unchanged: only the full-width KG viewer reads a source.
The new storage key is added to the persistence inventory (`docs/migration/ui_contracts/persistence_keys.json` and the
fixture copy, and the key list in `test_persistence_key_evidence`) as a deliberate, non-baseline entry.

## 6. Tests

Python: `tests/api_v1/test_v1_graph_sources.py` (52: mapping rules, ids, traversal/URL refusal, caching, outage,
inconsistent graph, read-only, default unchanged, `:local` and ALS routes), `tests/api_wiring/test_local_graph_sources.py`
(4: scanner wiring conditions, ALS never, and one end-to-end read through a real disposable Tiled that was published
to by the `:local` pipeline; skipped without `F2W_TILED_PYTHON`). Vitest: `GraphSourceSelect`, `graphSourceStore`,
`graphSourcesApi`, `liveAgentGraphSource`, `GraphMockupSource`. Playwright: three cases in `ui/e2e/kg-viewer.spec.ts`.

## 7. Owner-gated and open items (not self-approved)

1. **Route and capability.** `GET /v1/graph/sources` (V80) reuses `chat.read_support`. If the owner wants a separate
   capability for browsing Tiled content, it needs a new entry in the capability registry and a policy decision.
2. **ALS.** No Tiled source and no release snapshots on ALS. Offering ALS users the deployment's external Tiled graph
   would be a new reachability and role decision (and a per-corpus scope rule, `settings.global` vs graph id).
3. **Flat JSON files on ALS** are listed to every reader like `GET /graph` already serves; confirm that is intended
   (owner decision O-A5 covers `json_graph_path`).
4. **Persistence inventory.** The T18A inventory is a frozen baseline; one key was added deliberately.
5. **`:local` Tiled labelling.** Labels are `<graph_id> / <snapshot_id>` from the release marker. A later release of
   the same graph retires the previous revision's records (T08C state model); retired records are never nodes, so
   only the live release of a graph is a source. Listing several releases side by side is not offered.
6. **Not done:** editing a Tiled graph from the viewer (the journaled Save path is separate and untouched);
   FORGE/non-F2W entities in Tiled are not shown; counts of JSON files need a first read.
