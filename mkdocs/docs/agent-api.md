# Agent API

The React-facing FastAPI application is created by
`app.modules.f2w_agent.api.create_app()`. The default launcher binds it to
`127.0.0.1:8090` and allows the local Vite origins.

Interactive OpenAPI documentation is available at `/docs`.

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Runtime, workflow, memory, and pending-approval status |
| `GET` | `/settings` | Current runtime settings and available models/graphs |
| `PUT` | `/settings` | Change backend, model, graph source, or extraction settings |
| `POST` | `/session/reset` | Clear a default or named session |
| `DELETE` | `/session/{session_id}` | Delete named backend session state |
| `GET` | `/graph` | Return the active normalized graph |
| `POST` | `/graph/nodes/search` | Rank nodes in the active KG |
| `GET` | `/graph/node/{node_id}` | Return detailed node data |
| `PATCH` | `/graph/node/{node_id}` | Edit a Splash-backed node and relationships |
| `POST` | `/graph/upload` | Validate and save an uploaded JSON graph |
| `POST` | `/publications/search` | Search KG publications, optionally OpenAlex |
| `POST` | `/chat` | Run a chat turn |
| `POST` | `/chat/action` | Approve or decline a pending action |
| `POST` | `/chat/stream` | Streaming chat turn |
| `POST` | `/chat/action/stream` | Streaming approval action |

## Chat

```json
{
  "message": "What does P3HT do in an organic photovoltaic?",
  "messages": [
    {"role": "user", "content": "Tell me about organic photovoltaics."},
    {"role": "assistant", "content": "…"}
  ],
  "session_id": "project-a",
  "graph_source": "splash",
  "json_graph_path": null
}
```

`session_id` accepts letters, digits, `_`, and `-`, starts with an alphanumeric
character, and is at most 64 characters. Blank legacy assistant history entries
are accepted but discarded.

The response includes:

- status and answer;
- evidence sufficiency and confidence;
- selected node IDs and publication records;
- round summaries;
- the graph subset/full payload needed by the UI;
- requested and used graph sources;
- active work directory;
- pending approval data; and
- the last orchestration decision.

## Approval actions

```json
{
  "decision": "yes",
  "kind": "download",
  "candidate_index": 0,
  "session_id": "project-a"
}
```

`decision` is `yes` or `no`. `kind` is `download` or `extraction`.
`candidate_index` selects one of the pending literature cards.

The workflow state, rather than client history alone, determines what can be
resumed. Sending an action without a matching pending state returns a safe
no-pending response.

## Streaming

Streaming endpoints use `text/event-stream`.

```text
event: progress
data: {"phase":"retrieval","message":"Searching the knowledge graph"}

event: complete
data: {"status":"success", ...}
```

The server runs work in a task, pushes events through an async queue, cancels
the task if the client disconnects, and emits exactly one terminal `complete`
or `error` event.

## Graph payload

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
      "target": "matkg:Mobility",
      "predicate": "rel:has_property"
    }
  ],
  "source_path": "runs/ui_session_splash/kg.json"
}
```

Splash UUIDs are not exposed as UI node IDs when a MatKG URI/property is
available.

## Node search

```bash
curl -sS http://127.0.0.1:8090/graph/nodes/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"polymer used in organic solar cells","limit":10}'
```

Limits range from 1 to 25. The response contains the active
`retrieval_backend`, node records, and descending similarity scores. Unknown
placeholder nodes are filtered from user-facing results.

## Node updates

Updates are only allowed in Splash mode.

```json
{
  "label": "Updated name",
  "description": "Updated description",
  "publications": [],
  "linked_code_snippets": [
    {
      "function_name": "analyze",
      "code_language": "python",
      "code_snippet": "def analyze(data):\n    return data",
      "_action": "upsert"
    }
  ],
  "relationship_updates": [
    {
      "action": "add",
      "source": "matkg:A",
      "predicate": "rel:related_to",
      "target": "matkg:B"
    }
  ]
}
```

The service validates both relationship endpoints, rejects self-links and
invalid predicates, maps MatKG IDs to Splash entities, and persists idempotent
add/remove operations.

## Publication search

`include_external: false` searches normalized publications attached to KG
nodes. `include_external: true` also queries OpenAlex and merges results without
overwriting richer KG metadata. Results are deduplicated by DOI or stable
publication identity and ranked by query overlap.

## Settings

Runtime settings can switch:

- CBORG/Ollama backend and model;
- Splash/JSON graph source;
- deterministic/agentic workflow;
- full/targeted extraction; and
- targeted page limit.

JSON graph paths are constrained to JSON files under `storage/kg` or the
managed upload directory. Changing settings rebuilds the affected agents and
reloads the graph.

## Error boundaries

- invalid settings, paths, requests, or workflow actions return `400`;
- missing nodes/files return `404`;
- retrieval or Splash upstream failures return `502`;
- stream exceptions become an SSE `error` event;
- CORS defaults only allow the local Vite origins.
