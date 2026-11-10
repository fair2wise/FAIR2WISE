# Knowledge graph UI

The active graph surface is `GraphMockup.tsx`. Despite its historical name, it
renders live graph payloads from the agent API and provides the maintained
viewer/editor. `GraphCanvas.tsx` is an earlier mock-data renderer and is not the
current application surface.

## Graph payload

The browser works with normalized nodes and directed edges:

```json
{
  "nodes": [
    {
      "id": "matkg:P3HT",
      "label": "P3HT",
      "type": "ConjugatedPolymer",
      "description": "...",
      "publications": [],
      "linked_code_snippets": []
    }
  ],
  "edges": [
    {
      "source": "matkg:P3HT",
      "predicate": "rel:has_property",
      "target": "matkg:Mobility"
    }
  ],
  "source_path": "..."
}
```

Node IDs are stable MatKG identifiers when possible. Edges preserve source,
predicate, and target direction.

## Display modes

### Answer graph

The normal chat/graph split displays only nodes retrieved for the selected
assistant answer and edges induced between them. Select **View Knowledge
Graph** below an answer to pin or unpin its retrieved nodes.

During a streamed run, `graph_update` events merge nodes and edges into the
current graph so evidence appears before the terminal response.

### Search neighborhood

Choosing a node-search result replaces the current subset with that node and
its incoming/outgoing one-hop neighbors. Closing the selected detail returns
to the previously retrieved or viewer graph.

### KG Viewer

Select **KG Viewer** for a full-width graph. Select **Agent KG Viewer** to
return to the resizable chat/graph layout.

The node control offers **All**, 10-node increments through 100, 1,000 and
10,000; the default is **All** (graphs above 1,500 nodes behave differently, see
[Large graphs](#large-graphs)). Limited views choose a deterministic, connected-first subset beginning
with high-degree nodes and then continue across disconnected components. The
edge set is induced from the selected nodes.

#### Large graphs

A graph of more than 1,500 nodes (the two demo graphs: the RSoXS KG with 22,012
nodes and the 580-paper OPV KG with 15,815) never opens on **All**. Drawing the
whole graph used to block the browser tab for tens of seconds, so the viewer
opens on an **overview** instead:

- The overview is the 300 most connected nodes (degree counted among the shown
  candidates, ties by label) with the edges between them. A banner says
  "Showing the 300 most connected of 22,012 nodes: search or focus a node to
  explore, or show more", and the header reads "300 of 22,012 nodes".
- `Unknown` placeholder nodes, which paper-derived graphs use as the target of
  most edges, are hidden by default. The banner says how many are hidden, and
  **Include Unknown placeholders** brings them back.
- **Show more** steps the overview 300, 1,000, 2,000 and 5,000. The **Nodes**
  control lists the same sizes and always matches the banner. Sizes above 2,000
  are marked "(slow)": the banner warns that the layout will be slow and
  **Force-directed** is disabled (fixed layout only) above 2,000 displayed
  nodes. Expect several seconds for 2,000 nodes and much longer for 5,000.
- **Search nodes** still covers every node of the source, not only the overview.
  Choosing a result shows that node's neighbourhood. A neighbourhood read out of
  a large graph is capped at 500 nodes (the node plus the most connected others),
  with the notice "Neighbourhood limited to 500 nodes; N nodes match" (the same
  cap as `GET /graph/neighborhood/{id}`). **Back to overview** returns.
- Choosing another graph source starts again at the 300-node overview.

Graphs of 1,500 nodes or fewer, including every current demo graph of 268 to 421
nodes, are unchanged: **Nodes** defaults to **All** and no banner appears.

#### Graph source

In the full-width viewer the **Source** control chooses what the viewer reads:

- **Default graph**: the graph picked in Settings (no `source` parameter is sent).
- **JSON KGs**: the `storage/kg/*.json` files the server lists and, on the local
  image, the adopted release snapshots (`<corpus> / vN (release)`).
- **Tiled**: each F2W graph in the local image's own Tiled (`<graph> / <release>`),
  showing only the live release (staged and retired records are never nodes).

The badge next to the control shows the kind of the selected source. A JSON source
is read-only: Edit is hidden and the server refuses edits (`400 read_only_source`).
On the local image a Tiled source whose corpus is live (a published corpus) is
editable: Edit is shown and Save edits **that** corpus, the one the view shows.
The choice is remembered per browser; a remembered source the server no longer
lists falls back to the default graph. When Tiled is down the control says
**Tiled unavailable** and a read of a Tiled source shows the error instead of an
empty or stale graph. Search, node details and neighborhoods all carry the same
`source`. Source ids are opaque names listed by `GET /v1/graph/sources`; the server
never accepts a path, URL or Tiled address. They look like:

| Id | Meaning | Where |
|---|---|---|
| `json:<name>` | a `storage/kg/*.json` file, `<name>` being the file stem without `matkg_` (`json:rsoxs_v1`) | every profile |
| `json:<corpus>/<vN>` | an adopted release snapshot `<workdir>/storage_kg/<corpus>/<vN>/graph.json` (`json:e2e_demo/v1`) | `:local` only |
| `tiled:<graph_id>` | one F2W graph in the image's own Tiled (`tiled:e2e_demo`) | `:local` only |

An unknown or malformed id is `404 unknown_source`. On an ALS image the
listing reports Tiled as `not_offered` and only JSON files appear. The listing
marks JSON sources `read_only: true`; a Tiled source is `read_only: false` only
when its corpus is live on the image's Save target, and an edit that names any
other source is `400 read_only_source`. Search, node detail and neighborhood accept
the same `source` parameter, and so does Save (`PATCH /graph/node/{id}?source=`). Design and limits: `docs/migration/reviews/H8/ui_graph_sources.md`.
To get a Tiled graph to show, publish it first
([The `:local` image, end to end](local-image.md)). A graph is cut at 100,000 nodes and
Tiled is scanned at most once every 20 seconds.

A focused neighbourhood (`GET /graph/neighborhood/{id}`) is capped at 500 nodes. When that cap cuts it, the
response carries the header `X-F2W-Graph-Truncated: true` (the body is unchanged) and the viewer shows a small notice
beside the node count: "Neighbourhood limited to N nodes; N+ nodes match". The exact total is not computed.

## Navigation and inspection

| Interaction | Result |
|---|---|
| Pointer drag on the background | Pan the graph |
| Mouse wheel | Zoom around the pointer |
| Zoom buttons | Zoom around the viewport center |
| Focus button | Fit the currently displayed nodes |
| Hover node | Show type, label, and description preview |
| Hover edge | Show source, predicate, and target |
| Click node | Pin its detail panel |
| Drag panel handle | Resize graph canvas versus detail area |

The SVG uses a deterministic force-style layout. View boxes are clamped to the
calculated graph world. Off-screen nodes and edges are culled for performance;
cited nodes remain mounted in normal answer view so their pulse animations
stay synchronized.

## Search nodes

**Search Nodes** sends the query to `POST /graph/nodes/search`. Results include
the node type, descending score, and the active retrieval backend reported by
the server (`lexical` or `semantic`). The UI requests ten results; the API
supports limits from 1 to 25.

Selecting a result:

1. sets it as the active searched node;
2. computes its one-hop neighborhood from the loaded graph;
3. fits that subset in the canvas; and
4. opens the node detail panel.

Unknown placeholder nodes are excluded from rendered graphs and search
results.

## Citations and highlighting

The UI maps answer evidence back to nodes in first-mention order. It recognizes:

- explicit `[KG: node label]` citations;
- code-snippet labels and function names;
- fenced or partially streamed function definitions;
- PDF filenames;
- DOI, arXiv, title, and publication metadata attached to response nodes; and
- publications returned with the response even when not named verbatim.

Matched nodes pulse in citation order. Retrieved nodes that were not explicitly
cited remain part of the answer subset without the citation pulse.

## Node details

The pinned detail panel can show:

- identifier, label, schema category, and description;
- code language, function name, and source code;
- publication metadata and outbound publication links;
- linked code-snippet nodes and their publications; and
- incoming and outgoing relationships.

Code blocks and assistant answers provide clipboard actions. Publication
records can be bookmarked from the detail panel.

## Editing

Editing is available only when the saved graph source is the editable one
(`splash_links` in Settings) and the server says Save is possible
(`can_save` in `GET /v1/capabilities`). Select **Edit** in a node detail panel
to stage changes. With the graph **Source** selector on a live Tiled corpus,
Save edits that corpus; JSON sources are read-only.

On the `:local` image the editable source is backed by Tiled, not Splash: the
image starts in JSON mode (Save off), and after you choose the editable source
Save works only once a first publication exists; before that the server answers
`501` and the Save controls are hidden. See
[Save on `:local`](local-image.md#save-on-local).

### Editable node data

- label;
- schema category;
- description;
- code snippet for code-like nodes;
- publication title, source PDF, DOI, year, journal, authors, and related
  metadata represented by the form;
- linked code-snippet label, function name, language, and source; and
- directed relationships.

All staged changes are sent in one `PATCH /graph/node/{node_id}` request.
After a successful save, the UI refreshes the graph and updates the selected
node. A failed request leaves the editor open with an error.

### Relationships

Relationships can be incoming or outgoing. Search for the other endpoint,
choose a predicate, and stage the addition. Existing and staged relationships
can also be removed before saving.

The UI proposes these schema predicates:

```text
rel:related_to       rel:part_of          rel:has_property
rel:processed_by     rel:used_in          rel:causes
rel:affects          rel:measures         rel:occurs_in
rel:contains         rel:applied_to       rel:composed_of
rel:belongs_to       rel:forms_on         rel:provides_site_for
rel:has_code_snippet
```

Custom predicates are accepted. A bare value is normalized to a lowercase
`rel:` CURIE with underscores. An explicit prefix must match the CURIE-like
validation pattern. The backend performs final endpoint, self-link, predicate,
and persistence validation.

### JSON mode

JSON graphs are read-only. Detail, search, navigation, citation highlighting,
and publication links still work, but the edit action is hidden. Uploaded JSON
graph paths are passed back to detail requests so the backend resolves the
selected file consistently.

## Node colors

`kgNodeColors.ts` assigns a stable rainbow palette to known MatKG schema
classes. Unknown schema categories invented during extraction use sky blue.
Missing/`Unknown` placeholder categories use gray and are filtered from the
interactive graph.

Known classes include general MatKG concepts (`Material`, `Process`,
`Property`, `Method`, `Measurement`, `Publication`, `CodeSnippet`) and domain
classes such as `ConjugatedPolymer`, `PhotovoltaicCell`, `OFET`, and
`ExperimentalTechnique`.

## Performance behavior

- Layout is memoized by the displayed graph subset.
- Large viewer graphs use viewport culling.
- Graphs above 1,500 nodes open on a 300-node overview, not on **All**
  (see [Large graphs](#large-graphs)); id lookups on them use an index, so
  focusing a node is immediate.
- Limited viewer modes use connected subsets rather than arbitrary slicing.
- Node-search neighborhoods avoid laying out the entire graph.
- Streaming updates deduplicate nodes by ID and edges by
  source/predicate/target.
- Citation nodes remain mounted only where animation continuity matters.

For the server-side graph contract and validation rules, see
[Agent API](agent-api.md). For component/state ownership, see
[UI architecture](ui-architecture.md).
