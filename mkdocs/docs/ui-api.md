# UI and agent API integration

The browser client is implemented in
`ui/src/app/components/data/liveAgent.ts`. It owns the UI wire types, HTTP
requests, SSE parsing, abort signals, and stream fallback behavior.

For the complete server contract and examples, see [Agent API](agent-api.md).

## API base URL

The client reads `VITE_F2W_AGENT_API_URL` at build time and removes one trailing
slash.

| Deployment | Value | Browser behavior |
|---|---|---|
| Local Vite | unset | Defaults to `http://127.0.0.1:8090` |
| Docker Compose | `/api` | Same-origin calls go through Nginx to private `agent:8090` |
| Custom build | absolute URL or path | Uses exactly the build-time value |

Vite variables are compiled into browser assets. Changing the variable after
`npm run build` does not rewrite an existing bundle. Never place secrets in a
`VITE_*` variable.

## Endpoints used by the UI

| Method | Path | Client function | UI consumer |
|---|---|---|---|
| `GET` | `/settings` | `fetchAgentSettings` | Settings sheet |
| `PUT` | `/settings` | `updateAgentSettings` | Application boot and settings save |
| `POST` | `/chat` | `queryLiveAgentWithHistory` | Pre-stream fallback |
| `POST` | `/chat/stream` | `queryLiveAgentStream` | Normal chat path |
| `POST` | `/chat/action` | internal action fallback | Pre-stream action fallback |
| `POST` | `/chat/action/stream` | `queryAgentActionStream` | Extraction approval/skip |
| `POST` | `/session/reset` | `resetAgentSession` | Available client operation |
| `DELETE` | `/session/{id}` | `deleteAgentSession` | Delete chat |
| `GET` | `/graph` | `fetchLiveGraph` | Boot, settings reload, edit refresh |
| `POST` | `/graph/nodes/search` | `searchGraphNodes` | Graph search and relationship target search |
| `GET` | `/graph/node/{id}` | `fetchGraphNodeDetail` | Node detail panel |
| `PATCH` | `/graph/node/{id}` | `updateGraphNode` | Splash node editor |
| `POST` | `/publications/search` | `searchPublications` | Paper-search sheet |

The backend also exposes health, graph upload, and OpenAPI routes that the
current UI client does not call directly.

## Settings exchange

The browser sends a partial/full runtime update:

```json
{
  "backend": "cborg",
  "model": "lbl/cborg-chat",
  "graph_source": "splash",
  "workflow_mode": "agentic",
  "extraction_mode": "targeted",
  "targeted_max_pages": 6,
  "json_graph_path": null
}
```

The response returns normalized values plus available CBORG models, JSON graph
paths, and the default Ollama model. The browser uses `splash_links` in its
local settings model but maps it to the API value `splash`.

When JSON is selected, the chosen `storage/kg` path is sent. In Splash mode the
browser sends `json_graph_path: null`.

## Chat request

```json
{
  "message": "What is grazing-incidence X-ray scattering used for?",
  "messages": [
    {"role": "user", "content": "What is GIWAXS?"},
    {"role": "assistant", "content": "..."}
  ],
  "session_id": "4f52f..."
}
```

The client removes blank history entries. `ChatSidebar` supplies at most eight
recent messages and excludes the new user message from that history snapshot.

The terminal response includes answer/status, sufficiency, confidence, node
IDs, publications, round details, graph payload, graph source, work directory,
pending workflow data, and the last orchestration decision.

## Server-sent events

Streaming endpoints return `text/event-stream` blocks:

```text
event: progress
data: {"phase":"retrieval_started","message":"Retrieval agent searching the KG"}

event: complete
data: {"status":"answered","answer":"..."}
```

The parser supports `event:` and multiple `data:` lines. Recognized terminal
events are:

| Event | UI behavior |
|---|---|
| `progress` | Update the active thinking step or live graph |
| `complete` | Resolve the request as `AgentChatResponse` |
| `error` | Throw the supplied answer/message/status as an error |

Common progress phases include retrieval start/result, graph update,
orchestrator decision, download start/result, extraction start/result, KG
rebuild start/result, Splash reimport start/result, reload start/result, and
waiting for extraction approval.

`graph_update` is special: its nodes are merged by ID and edges by the tuple
`source + predicate + target`. Other progress events become the current status
line; prior steps are marked complete internally.

## Stream fallback

The client falls back to `/chat` or `/chat/action` only if the streaming call
fails before any SSE event is observed. Once a progress event arrives, a later
failure is surfaced instead of repeating the workflow through a second
non-streaming request.

This prevents duplicate downloads, extraction, or graph mutation after a
partially completed stream.

## Cancellation and stale requests

Chat and action calls accept an `AbortSignal`. `ChatSidebar` owns the active
`AbortController` and a monotonically increasing request sequence.

- Stop aborts the current fetch.
- Session switching aborts the previous session's request.
- Component unmount aborts the request.
- Late progress/completion from an obsolete sequence is ignored.
- An abort never triggers the non-streaming fallback.

## Publication search

```json
{
  "query": "polymer crystallinity in organic photovoltaics",
  "max_results": 20,
  "include_external": false
}
```

The response returns normalized publication records, supporting node IDs, and
a source label such as `kg` or `kg+openalex`. External search is explicitly
opted in by the checkbox.

## Node search and detail

Node search sends:

```json
{"query":"scattering peak analysis","limit":10}
```

Results include the active retrieval backend and scored node records. Detail
requests URL-encode the node ID and may include `json_graph_path` when the
active graph is an uploaded/selected JSON file.

## Node update

The editor can send a single patch containing scalar properties, publication
replacement data, linked-snippet operations, and relationship operations:

```json
{
  "label": "Updated label",
  "type": "Method",
  "description": "Updated description",
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

Removing a linked snippet uses `_action: "unlink"`; relationships use `add`
or `remove`. The backend rejects updates outside Splash mode.

## Error handling

The client reads error bodies as text. Settings errors additionally parse
FastAPI JSON `detail` strings or arrays into a concise message.

| Condition | UI behavior |
|---|---|
| Network fetch failure | Explain which agent base URL cannot be reached |
| Settings/search route returns 404 | Suggest restarting an older backend |
| Chat HTTP error | Add an **Agent run failed** assistant card |
| SSE `error` | Add an agent error card with server detail |
| User abort | Stop quietly and clear transient state |
| Clipboard unavailable | Leave UI functional without a notification |

The frontend sends no CBORG or external-service authorization header. The
agent reads those credentials from its environment and makes upstream calls on
the server side.

## Compose proxy behavior

Nginx handles `/api/` with buffering and caching disabled and 900-second read
and send timeouts, which permits long SSE workflows. `proxy_pass` removes the
`/api/` prefix before forwarding to `agent:8090`. All other paths use SPA
fallback to `index.html`.
