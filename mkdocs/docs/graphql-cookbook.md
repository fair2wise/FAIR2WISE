# GraphQL cookbook (RSoXS v1 / Tiled Graph)

Named operations live in
`splash_links/examples/rsoxs_v1_cookbook.graphql`.
Use them in GraphiQL, from an agent, or as extraction QA after
`matkg_rsoxs_v1.json` is imported into **Tiled’s entity/link graph**.

This page is the **Graph Query** box on the 11.0.1.2 diagram: Tiled GraphQL
over the same catalog server that holds Bluesky scans. Splash Links in this
repo is the F2W demo graph (`json` | `splash` only). The Tiled graph API was
ported from splash_links, so the same `entities` / `links` operations work;
the beamline endpoint and extras (`nodeId`, namespaces, API key) differ.

The Tiled graph feature is **experimental**; field names may move.

## Endpoint

| Where you run | URL |
|---|---|
| 11.0.1.2 / local Tiled catalog | `http://<tiled-host>/api/graphql` |
| GraphiQL | same URL in a browser |
| Tiled demo (`example_configs/graphs/`) | `http://127.0.0.1:8000/api/graphql` |
| F2W Splash (local UI only, not the beamline store) | `http://127.0.0.1:8081/splash_links/graphql` |

Tiled requires an API key. Missing auth **returns empty lists**, not an error.

```bash
curl -s http://127.0.0.1:8000/api/graphql \
  -H "Authorization: Apikey secret" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { entities(limit: 5) { name entityType uri nodeId } }"}'
```

In GraphiQL, set Headers to `{"Authorization": "Apikey <key>"}`.

Wire field names are **camelCase** (`entityType`, `outgoingLinks`, `nodeId`).

## What GraphQL can and cannot do

Tiled graph GraphQL is a thin CRUD/traversal API (same filters as Splash).
It does **not** search JSON properties or SPARQL.

| Can filter | Cannot filter (page, then match in the client) |
|---|---|
| `entities(entityType, limit, offset)` | `name`, `uri` (`matkg:…`), DOI, year |
| `entity(id)` where `id` is the **graph UUID** | photon energy, edge, polarization |
| `links(subjectId, predicate, objectId)` | `properties.publications`, `source_papers` |
| `outgoingLinks(predicate)` / `incomingLinks(predicate)` | full-text in `description` / snippets |
| `catalogNodeId(path)` → set entity `nodeId` | SPARQL / inference |

`predicate` may be a CURIE (`prov:wasDerivedFrom`) if that prefix is
registered via `upsertNamespace`. Default page size is 100. Loop
`offset += limit` until a page is short.

There is no `entityByUri`: resolve `matkg:P3HT` by paging types and comparing
`uri`.

Bibliographic data is usually **on the node**, not a `Publication` entity:
`properties.publication_year`, `properties.doi`, `properties.source_papers`,
`properties.source_metadata`, `properties.publications`.

## Shared fragment

```graphql
fragment EntityCore on Entity {
  id
  entityType
  name
  uri
  properties
}
```

On Tiled, also request `nodeId` (catalog tree pointer). Splash has no such
field.

## Agent recipe index

Pick a named operation, then apply the client filter.

| Question | Operation | Then filter |
|---|---|---|
| What did we extract? | `InventoryUntyped` (paginate, group `entityType`) | — |
| Are relation targets stubbed? | `PageUnknownStubs` | fail if count is high vs. measurements |
| List RSoXS measurements | `PageRSoXSMeasurements` | — |
| Measurements missing energy | `PageRSoXSMeasurements` | `properties.photon_energy_eV` empty |
| C K-edge only | `PageRSoXSMeasurements` | edge / energy near 280–290 eV |
| Papers from year *Y* | page types that carry `source_metadata` | `publication_year == Y` |
| What was measured on P3HT? | page polymers → `Neighborhood` | `uri` contains `P3HT` |
| RSoXS → property links | `MeasuresLinks` | — |
| Analysis code without a paper | `PageCodeSnippets` | missing `source_paper` / `publications` |
| Linked 11.0.1.2 runs | `PageBlueskyRuns` + `CatalogNodeId` + `MeasuredFromRun` | empty is OK if catalog has runs; `nodeId` only after an app writes the entity |
| Simulated ESAFs / proposals / samples | `PageESAFs` / `PageProposals` / `PageSamples` / `PageBlueskyRuns` | JSON overlay `matkg_bl1101_v5` is the **fallback** when live Tiled is off or unreachable |
| Live Tiled ESAF / proposal / sample / scan (chat) | same Page* queries against `TILED_URI/api/graphql` | Requires `F2W_LIVE_TILED` or a configured `TILED_URI`; set `TILED_API_KEY` or lists are empty |
| Summarize my last scan | **not GraphQL** — `tiled.client` newest `start.time` in proposal | start/plan properties |
| Papers similar to this scan | catalog run card, then `PageRSoXSMeasurements` | client overlap on shared slots |

Until Tiled import, run the same checks on JSON with `jq` (below).

## Inventory and extraction QA

### Count types

GraphQL has no `groupBy`. Page `InventoryUntyped` and tally `entityType`.

```python
import json, urllib.request

URL = "http://127.0.0.1:8000/api/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Apikey secret",
}
QUERY = """
query InventoryUntyped($limit: Int!, $offset: Int!) {
  entities(limit: $limit, offset: $offset) { entityType name uri }
}
"""

def graphql(query, variables):
    req = urllib.request.Request(
        URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers=HEADERS,
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode())
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]

def page_entities(entity_type=None, page_size=100):
    query = """
    query Page($entityType: String, $limit: Int!, $offset: Int!) {
      entities(entityType: $entityType, limit: $limit, offset: $offset) {
        id entityType name uri properties
        outgoingLinks(limit: 50) { predicate object { id name entityType uri } }
      }
    }
    """
    offset, rows = 0, []
    while True:
        data = graphql(query, {
            "entityType": entity_type, "limit": page_size, "offset": offset
        })
        batch = data["entities"]
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        offset += page_size
```

**Pass:** `rsoxs_v1` should show `RSoXSMeasurement` and/or
`ExperimentalTechnique`, plus materials/polymers, few `Unknown` stubs, and
`CodeSnippet` only when papers included analysis code.

**Fail:** empty graph; only `Unknown`; every measurement has degree 0.

The locked corpus QA suite (technique_role counts, soft_matter_relevance
buckets, evidence coverage, missing edge/energy, duplicate DOI/arXiv Works,
materials with many aliases, publications with zero measurements, impossible
energy/edge pairs) is specified in the RSoXS pipeline plan. Extend these
inventory queries to match that list; do not treat Unknown stubs and missing
energy as the whole gate.

### Unknown stubs (dangling relation targets)

`json2kg.py` creates `category: Unknown` nodes when a relation points at a
term that was never extracted.

```graphql
query PageUnknownStubs($limit: Int = 100, $offset: Int = 0) {
  entities(entityType: "Unknown", limit: $limit, offset: $offset) {
    name uri
    incomingLinks(limit: 20) {
      predicate
      subject { name entityType }
    }
  }
}
```

**Pass:** stubs are rare and still have incoming links (so they are join
points, not orphans). **Fail:** stubs with no links, or stubs that should have
been `RSoXSMeasurement` (`R-SoXS`, `P-RSoXS` aliases missing from the schema
helper).

### Measurements missing method slots

Page `PageRSoXSMeasurements` (and `PageExperimentalTechniques` as a fallback
if the LLM used the parent class). Client-side:

```python
def missing_rsoxs_slots(nodes):
    needed = ("photon_energy_eV", "absorption_edge", "scattering_technique")
    bad = []
    for node in nodes:
        props = node.get("properties") or {}
        if not any(props.get(k) not in (None, "", []) for k in needed):
            bad.append(node)
    return bad
```

Also accept the same keys nested under `properties.properties` (term property
records) or `domain_features`. Extraction does not guarantee top-level slots
until the RSoXS schema prompt is in place; this check is the gate.

### Provenance: can we get back to a PDF?

For each measurement or material, require at least one of:

- `properties.source_papers`
- `properties.source_metadata`
- `properties.publications`
- `properties.doi`

**Fail** if a node has relations but no source paper. That extraction cannot
be cited in the F2W UI.

### Unique papers vs. manifest

Collect every `source_papers` filename and `source_metadata` key. Compare to
`papers/rsoxs/manifest.json` rows with `ingestion.status == "in_kg"`. Extra
PDFs in the graph or missing PDFs in the graph mean the extract driver and
manifest drifted.

### Code snippets

`PageCodeSnippets`: require `code_snippet` / `function_name` and a paper
field. Incoming `rel:has_code_snippet` should exist; snippets with no incoming
link were not wired by `json2kg`.

## Science questions (literature lane)

These are the queries the F2W agent (or a beamline scientist) will want after
v1 is in Tiled Graph.

### RSoXS measurements and what they measure

```graphql
query PageRSoXSMeasurements($limit: Int = 100, $offset: Int = 0) {
  entities(entityType: "RSoXSMeasurement", limit: $limit, offset: $offset) {
    ...EntityCore
    outgoingLinks(limit: 50) {
      predicate
      properties
      object { name entityType uri }
    }
  }
}
```

Follow `rel:measures` and `rel:used_in`. Link `properties.has_evidence` is the
sentence to show the user.

### Materials / polymers characterized by scattering

```graphql
query PageConjugatedPolymers($limit: Int = 100, $offset: Int = 0) {
  entities(entityType: "ConjugatedPolymer", limit: $limit, offset: $offset) {
    ...EntityCore
    outgoingLinks(limit: 50) {
      predicate
      object { name entityType }
    }
    incomingLinks(limit: 50) {
      predicate
      subject { name entityType }
    }
  }
}
```

Client: keep nodes whose neighborhood mentions RSoXS / `RSoXSMeasurement` /
`rel:measures`.

### One-hop neighborhood (after you have a graph UUID)

```graphql
query Neighborhood($id: ID!, $limit: Int = 50) {
  entity(id: $id) {
    ...EntityCore
    outgoingLinks(limit: $limit) {
      predicate
      object { id name entityType uri }
    }
    incomingLinks(limit: $limit) {
      predicate
      subject { id name entityType uri }
    }
  }
}
```

### Papers from a given year

There is no year argument. Page candidate types, then:

```python
def papers_in_year(nodes, year):
    hits = []
    for node in nodes:
        props = node.get("properties") or {}
        years = {props.get("publication_year")}
        for pub in props.get("publications") or []:
            if isinstance(pub, dict):
                years.add(pub.get("publication_year"))
        for meta in (props.get("source_metadata") or {}).values():
            if isinstance(meta, dict):
                years.add(meta.get("publication_year"))
        years.discard(None)
        if year in years:
            hits.append(node)
    return hits
```

Sort hits by `publication_year` descending to match the corpus manifest.

### All `rel:measures` triples

```graphql
query MeasuresLinks($limit: Int = 100, $offset: Int = 0) {
  links(predicate: "rel:measures", limit: $limit, offset: $offset) {
    properties
    subject { name entityType uri }
    object { name entityType uri }
  }
}
```

**Pass:** subjects are techniques/measurements, objects are properties or
structures (domain spacing, orientation, composition contrast). **Fail:**
everything is `rel:related_to`.

## Tiled catalog join (11.0.1.2)

**GraphQL is not the run list.** A Bluesky run in Tiled (data) is not an
`entity` until a User Application (or the optional F2W indexer) creates it
and adds links. F2W Q/A must use two clients on the same host:

- Catalog: `tiled.client` + `bluesky-tiled-plugins` for `start` / `stop` /
  plan / sample (source of truth for new scans)
- Graph: `POST /api/graphql` for literature and whatever links already exist

Empty `MeasuredFromRun` with live scans in the tree is expected. Do not
block scan Q/A on GraphQL.

When an app *does* promote a run into the graph:

1. `catalogNodeId(path: ["<proposal>", "<run-uid>"])` → internal catalog id
2. `createEntity` for the run with that `nodeId` plus `uri` = the Tiled
   metadata URL
3. Copy the start/plan whitelist onto properties; create Sample /
   RSoXSMeasurement nodes as needed
4. `createLink` `prov:wasDerivedFrom` (measurement or sample → run)

Register namespaces once per catalog:

```graphql
mutation {
  matkg: upsertNamespace(prefix: "matkg", uri: "https://w3id.org/matkg/") { prefix uri }
  prov: upsertNamespace(prefix: "prov", uri: "http://www.w3.org/ns/prov#") { prefix uri }
  ro: upsertNamespace(prefix: "ro", uri: "http://purl.obolibrary.org/obo/ro.owl") { prefix uri }
}
```

```graphql
query CatalogNodeId($path: [String!]!) {
  catalogNodeId(path: $path)
}

query MeasuredFromRun($limit: Int = 100, $offset: Int = 0) {
  links(predicate: "prov:wasDerivedFrom", limit: $limit, offset: $offset) {
    subject { name uri entityType nodeId }
    object { name uri entityType nodeId }
  }
}

mutation CreateMeasuredFromLink($measurementId: ID!, $runEntityId: ID!) {
  createLink(input: {
    subjectId: $measurementId
    predicate: "prov:wasDerivedFrom"
    objectId: $runEntityId
  }) {
    id
    predicate
    subject { name }
    object { uri nodeId }
  }
}
```

Prefer `prov:wasDerivedFrom` / `prov:wasGeneratedBy` for run↔result (written
by the Bluesky start/plan indexer) and keep `rel:measures` for literature
method→property. Do **not** use create/delete for extraction QA. Literature
`rsoxs_v1` promote skips existing `matkg:` uris. The run indexer is
idempotent on uid and is a separate write path.

F2W reads experiment-identity questions (ESAF / proposal / sample / scan)
through **live Tiled GraphQL** when `F2W_LIVE_TILED` is on or `TILED_URI` is
set. The agent pages `PageESAFs` / `PageProposals` / `PageSamples` /
`PageBlueskyRuns` into retrieval context, then still fans out over the JSON
science + ops KGs for hardware and literature. If Tiled is unreachable or
unsigned, chat falls back to the JSON sim overlay (`matkg_bl1101_v5`).

A full `KG_RAG_GRAPH_SOURCE=tiled` pager (load the entire Tiled graph into
MatKG the same way `_load_splash_links_graph` pages Splash) remains optional
follow-up; chat does not wait on it.

Scan/run questions also try **Tiled catalog** (`tiled.client`) when GraphQL
has no BlueskyRun entities yet. If a ranked graph node already has `nodeId`,
follow it. Detector arrays stay out of the prompt.

See the RSoXS pipeline plan (Cursor canvas: source of truth) for locked
data-model and retrieval contracts: canonical Work identity, extraction
confidence, SimilarityDocument / RunCard, versioning axes, import ledger,
auth cache tests, and corpus QA stats beyond Unknown stubs. This page is
GraphQL operations and inventory queries only.

## Validate before Tiled import (JSON)

Same gates on the working copy:

```bash
python3 -c "
import json
from collections import Counter
g = json.load(open('storage/kg/matkg_rsoxs_v1.json'))
print(Counter(t.get('category') for t in g['things']))
print('edges', len(g['associations']))
print('unknown', sum(1 for t in g['things'] if t.get('category')=='Unknown'))
print('no source', sum(1 for t in g['things']
    if not t.get('source_papers') and not t.get('publications')
    and t.get('category') not in ('Unknown',)))
"
```

Promote to Tiled GraphQL only when these counts look right; then rerun the
inventory against `/api/graphql`.

## Predicates to expect

| Predicate | Typical subject → object |
|---|---|
| `rel:measures` | RSoXSMeasurement → Property / Structure |
| `rel:has_property` | Material → Property |
| `rel:used_in` | Technique → Material / Device |
| `rel:has_code_snippet` | Term → CodeSnippet |
| `rel:related_to` | Weak leftover; prefer a typed predicate |
| `prov:wasDerivedFrom` | measurement / sample extracted from start+plan → catalog run (`nodeId` set) |
| `prov:wasGeneratedBy` | dataset → activity / measurement |

## RSoXS v1 / bl1101 v6 Queries

The following named operations were added to `rsoxs_v1_cookbook.graphql` for
the BL 11.0.1.2 beamline graph (`matkg_bl1101_v6`).  They cover beam-path
traversal, hardware neighborhood lookup, experiment tree navigation, and
contact discovery.

> **Name-index note:** the Tiled GraphQL API does not expose a `name` filter
> on `entities(...)`.  Operations that accept a `$name: String!` variable must
> page by `entityType` and filter the response client-side where
> `entity.name == $name`.  The variable documents the intended filtering key.

| Operation | Purpose | Key variables |
|---|---|---|
| `PageBeamPathChain` | Page all `rel:beam_path_next` links to reconstruct the ordered photon beam path (EPU → M101 → … → Detector). | `$limit: Int = 50`, `$offset: Int = 0` |
| `NeighborsOfDevice` | Return all outgoing and incoming links of a named beamline device across every predicate ("how is X connected?"). Page by `$entityType`, filter client-side by name. | `$name: String!`, `$entityType: String = "OphydDevice"`, `$limit`, `$offset` |
| `MotorToStage` | Page `rel:part_of` links; filter client-side where `subject.entityType == "Motor"` and `object.id` contains `"stage"` to find which stage each motor belongs to. | `$limit: Int = 200`, `$offset: Int = 0` |
| `PageESAFsWithProposals` | Page ESAF entities and inline each proposal's `rel:hasScan` scan list, enabling scan-count aggregation per proposal without a second round-trip. | `$limit: Int = 20`, `$offset: Int = 0` |
| `SamplesByProposal` | Given a proposal graph UUID, return all `rel:hasSample` samples and the `prov:used` BlueskyRun IDs that scanned each sample. | `$proposalId: ID!`, `$limit: Int = 50` |
| `BlueskyRunsByDateRange` | Page BlueskyRun entities with plan, sample, and measurement links included. Filter client-side by `properties.start_time` for the desired date range. Uses field aliases (`plan:`, `usedSample:`, `measurement:`). | `$limit: Int = 100`, `$offset: Int = 0` |
| `UpstreamPath` | Follow `rel:upstream_of` incomingLinks up to 3 hops from a named BeamlineStage to identify the full upstream feed chain. Client-side: keep the entity whose `name == $name`. | `$name: String!`, `$limit: Int = 50`, `$offset: Int = 0` |
| `BeamlineContacts` | Page Person entities; return their `rel:hasRole` (filter for `Role-BeamlineScientist`) and `rel:supports` beamline links. Email is in `entity.description`. Uses field aliases (`roles:`, `beamlines:`). | `$limit: Int = 20`, `$offset: Int = 0` |

### Schema notes for bl1101 v6

- **Beam-path predicates in the KG:** `rel:beam_path_next` (17 links),
  `rel:upstream_of` (19 links), `rel:feeds`, `rel:connected_to`, `rel:part_of`.
  `beam_path_next` and `upstream_of` share the same directionality
  (subject → object = earlier → later in the beam path).
- **Motor → Stage:** 87 `rel:part_of` Motor→stage pairs; 177 Motor→beamline
  pairs in the same predicate batch.  Filter by `object.id` containing
  `"stage"` to isolate the stage membership rows.
- **BlueskyRun ↔ Sample:** `BlueskyRun → prov:used → Sample` (222 links).
  The inverse direction (`Sample` incomingLinks predicate `prov:used`) returns
  the runs that scanned a given sample.
- **Persons:** only 2 Person entities (`Cheng Wang`, `Thomas Ferron`).  Email
  is stored in `entity.description`, not a dedicated property field.
  Both persons have `rel:hasRole → Role-BeamlineScientist` and
  `rel:supports → BL-11-0-1-2`.
- **Field aliasing:** `BlueskyRunsByDateRange` and `BeamlineContacts` request
  `outgoingLinks` twice with different `predicate` arguments; GraphQL requires
  aliases in that case (`plan:`, `usedSample:`, `roles:`, `beamlines:`).  The
  Tiled GraphQL server honours aliases per the GraphQL spec.
