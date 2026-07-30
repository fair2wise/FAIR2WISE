# Web application

The current browser application is a React 18/Vite 6 project under `ui/`.
`App.tsx` places a FAIR2WISE route inside the Finch shell and composes the
header actions, multi-session chat, and graph panel.

## Component map

| Component/module | Responsibility |
|---|---|
| `App.tsx` | Finch shell, session selection, settings bootstrap, graph loading |
| `ChatSidebar.tsx` | Live chat, SSE progress, approvals, citations, publications, graph/chat split |
| `GraphMockup.tsx` | SVG graph layout, pan/zoom, viewer, node search, node/relationship editing |
| `KGInfoPanel.tsx` | Node and edge hover details |
| `PublicationList.tsx` | Publication cards and expansion |
| `PublicationFavoriteButton.tsx` | Saved-paper state |
| `AsciiOrb.tsx` | Empty/loading animation |
| `AppSettingsButton.tsx` | Backend/model/graph/workflow/extraction settings |
| `AppPaperSearchButton.tsx` | KG and optional OpenAlex publication search |
| `AppSearchChatsButton.tsx` | Session search, selection, and deletion |
| `data/liveAgent.ts` | Typed agent API and SSE client |
| `chatSessions.ts` | Browser chat persistence and migration |
| `agentSettings.ts` | Browser settings persistence and API conversion |

`ChatPanel.tsx`, `mockRag.ts`, `materialsData.ts`, and `mockupData.ts` support
prototype/mock paths and are not the current live chat surface.

## Chat behavior

The sidebar:

- retains at most 80 messages per browser session;
- sends at most the latest eight non-empty history messages to the API;
- streams progress and terminal results;
- shows a stoppable in-flight state and elapsed time;
- renders pending paper/extraction decision cards;
- highlights KG citation identifiers found in answers;
- displays publications inside the same response card;
- supports copying answers; and
- pins the graph associated with a prior assistant response.

The frontend attempts streaming first. It falls back to non-streaming only when
the server failed before emitting any SSE event.

## Session persistence

Browser state uses:

| Key | Contents |
|---|---|
| `fair2wise.chat.sessions.v2` | Sessions, active ID, and up to 80 messages/session |
| `fair2wise.chat.messages.v1` | Legacy single-session key, migrated on load |
| `fair2wise-agent-settings-v1` | Backend, model, graph, workflow, extraction settings |

Session titles come from the first user prompt and are capped at 60 characters.
Malformed or unavailable local storage never prevents the app from starting.

Backend session memory is separate. Selecting sessions sends its ID with each
turn; deleting a browser session also requests backend deletion.

## Knowledge graph canvas

The canvas is an SVG with a deterministic force-style layout. It supports:

- directed edges and arrowheads;
- node/edge hover popups;
- click-to-open node details;
- pointer panning, wheel zoom, zoom buttons, and focus/reset;
- citation highlighting and synchronized pulse animation;
- viewport culling for nodes and edges; and
- schema-driven category colors.

Unknown placeholder nodes are omitted from the visible graph and node search.

### Normal agent view

The graph renders nodes retrieved for the selected assistant response. Closing
a searched/pinned node returns to that response's retrieved nodes. Citation
nodes remain mounted during panning so their animations stay phase-aligned.

### KG Viewer

“KG Viewer” replaces the chat split with a full-width graph. The button becomes
“Agent KG Viewer” to return to the normal layout.

The node-count selector offers `All` and increments of ten through 100. The
default is 100. Limited views select connected components beginning with
high-degree nodes. “All” lays out the full graph, while viewport culling avoids
mounting off-screen SVG elements during pan/zoom.

### Node search

“Search Nodes” calls `/graph/nodes/search`. Results show type, score, and the
reported lexical/semantic backend. Selecting a result renders the node and its
one-hop neighborhood and opens its detail panel.

### Editing

In Splash mode, the detail panel can edit:

- name, schema category, description, and code;
- publication records;
- linked code snippets;
- incoming or outgoing relationships; and
- custom or schema-listed predicates.

Changes are staged locally and sent in one node patch. JSON mode is read-only.

## API client

`data/liveAgent.ts` defines the wire types and functions for:

- settings read/update;
- regular and streaming chat;
- regular and streaming actions;
- publication search;
- session reset/delete;
- graph fetch;
- node search/detail; and
- node update.

The API base is `VITE_F2W_AGENT_API_URL`, with
`http://127.0.0.1:8090` as fallback.

## Styling

- Finch supplies the application shell.
- Tailwind utilities are compiled by `@tailwindcss/vite`.
- `src/styles/` contains theme, font, Tailwind, and global layers.
- `components/ui/` contains reusable Radix/shadcn-style primitives.
- Node category colors and schema class lists live in `kgNodeColors.ts`.

## Development commands

```bash
cd ui
npm ci
npm run dev
npm test
npm run build
```

`start_agent_frontend.sh` verifies npm and the local Vite executable before
starting the dev server.
