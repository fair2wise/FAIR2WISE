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

The node control offers **All** and 10-node increments through 100; the default
is 100. Limited views choose a deterministic, connected-first subset beginning
with high-degree nodes and then continue across disconnected components. The
edge set is induced from the selected nodes.

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

Editing is available only when the saved graph source is `splash_links`.
Select **Edit** in a node detail panel to stage changes.

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
- Limited viewer modes use connected subsets rather than arbitrary slicing.
- Node-search neighborhoods avoid laying out the entire graph.
- Streaming updates deduplicate nodes by ID and edges by
  source/predicate/target.
- Citation nodes remain mounted only where animation continuity matters.

For the server-side graph contract and validation rules, see
[Agent API](agent-api.md). For component/state ownership, see
[UI architecture](ui-architecture.md).
