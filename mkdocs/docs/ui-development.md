# UI development and testing

## Prerequisites

- Node.js 20 or newer;
- npm 10 or newer;
- the FAIR2WISE agent API for live features; and
- Splash when the agent uses the default editable graph source.

Dependencies are pinned in `ui/package-lock.json`. Use `npm ci`; no global Vite
installation is required.

## Start the complete local stack

From the repository root:

```bash
cd ui
npm ci
cd ..
./scripts/start_all.sh
```

This starts Splash on 8081, the agent on 8090, and Vite on 5173. Press
**Ctrl+C** in the launcher terminal to stop all managed processes.

## Start only the UI

Start Splash and the agent separately, then run:

```bash
./scripts/start_agent_frontend.sh
```

The helper verifies npm and the repository-local Vite executable. The direct
equivalent is:

```bash
cd ui
npm run dev -- --host 127.0.0.1 --port 5173
```

Local Vite defaults to `http://127.0.0.1:8090` for API requests. Override it
before starting Vite when necessary:

```bash
VITE_F2W_AGENT_API_URL=http://127.0.0.1:8091 npm run dev
```

The backend's default CORS allow-list contains only localhost/127.0.0.1 on port
5173. A different frontend origin also requires a corresponding backend CORS
configuration change.

## npm commands

| Command | Purpose |
|---|---|
| `npm ci` | Reproduce the lock-file dependency tree |
| `npm run dev` | Start Vite development server |
| `npm test` | Run Vitest once in jsdom |
| `npm run build` | Create the production bundle in `ui/dist` |

There is currently no dedicated UI lint or `tsc --noEmit` package script.
`vite build` validates bundling/transformation but should not be described as a
standalone strict TypeScript type-check.

The current production build succeeds but Vite reports a JavaScript chunk over
its 500 kB warning threshold. This is performance debt rather than a build
failure. Prefer route/feature-level dynamic imports or deliberate Rollup chunk
groups when addressing it; do not merely raise the warning limit without
measuring load behavior.

## Production and Docker

The root `Dockerfile` uses two frontend stages:

1. `frontend-build` starts from Node 20, runs `npm ci`, sets the build-time API
   base, and runs `npm run build`.
2. `frontend` starts from Nginx Alpine and copies `ui/dist` into the web root.

Compose builds with `VITE_F2W_AGENT_API_URL=/api`. Nginx:

- serves the SPA and falls back to `index.html`;
- exposes `/healthz`;
- proxies `/api/` to the private agent service;
- disables buffering/caching for streaming; and
- permits 900-second agent requests.

Only the frontend binds a host port:

```text
127.0.0.1:5173 -> frontend:80
```

Rebuild the frontend after changing UI source, npm dependencies, Vite
configuration, or the compiled API base:

```bash
docker compose up -d --build frontend
```

Compose waits for the private agent to become healthy before marking the
frontend service ready.

## Project structure

```text
ui/
├── index.html
├── package.json
├── package-lock.json
├── vite.config.ts
├── vitest.config.ts
├── public/
│   └── wise_owl.svg
└── src/
    ├── main.tsx
    ├── app/
    │   ├── App.tsx
    │   └── components/
    │       ├── data/
    │       ├── ui/
    │       └── *.tsx / *.ts / *.test.ts
    └── styles/
        ├── index.css
        ├── tailwind.css
        ├── theme.css
        ├── globals.css
        └── fonts.css
```

## Styling

- Import global styles only through `src/styles/index.css`.
- Use Tailwind utilities for feature layout and component-specific styling.
- Reuse components under `components/ui/` for dialogs, sheets, inputs,
  resizable panels, and other primitives.
- Use Finch components for the application shell and common header buttons.
- Add shared color/design tokens to `theme.css` rather than duplicating CSS
  variables.
- Add graph schema classes/colors in `kgNodeColors.ts`.

`vite.config.ts` must retain both the React and Tailwind plugins. The
`figmaAssetResolver` supports legacy `figma:asset/...` imports; normal static
assets belong in `public/` or should be imported from source.

The application includes shadcn/Figma attribution in `ui/ATTRIBUTIONS.md`.

## Add or change an API operation

1. Define request/response types in `data/liveAgent.ts`.
2. Add the fetch function there so base URL, error, signal, and SSE conventions
   stay centralized.
3. Keep credentials server-side; never add them to `VITE_*` variables.
4. Add mocked-fetch tests in `data/liveAgent.test.ts`.
5. Update [UI and agent API integration](ui-api.md) and
   [Agent API](agent-api.md).

## Add a UI feature

1. Identify the state owner before adding local state.
2. Reuse `PublicationList`, graph helpers, settings/session helpers, and local
   UI primitives where possible.
3. Keep backend workflow state keyed by the browser session ID.
4. Abort requests and ignore stale completions when the user can switch views
   or sessions.
5. Add labels to icon-only controls and account for reduced motion.
6. Add focused tests for pure behavior and API serialization.
7. Run both the test and production-build commands.

## Frontend tests

```bash
cd ui
npm test
npm run build
```

Vitest uses jsdom and discovers `src/**/*.test.ts`.

| Test file | Coverage |
|---|---|
| `ChatSidebar.test.ts` | Publication labels and copied response text after extraction |
| `GraphMockup.test.ts` | One-hop neighborhoods, predicate normalization, connected viewer subsets |
| `agentApiErrors.test.ts` | FastAPI details, 404 guidance, network messages |
| `agentSettings.test.ts` | Defaults, aliases, API mapping, persistence, comparison |
| `chatSessions.test.ts` | IDs, titles, migration, ordering, 80-message cap |
| `kgCitations.test.ts` | KG/code/PDF/publication citation resolution and ordering |
| `kgNodeColors.test.ts` | Schema, invented, and Unknown category colors |
| `publicationLinks.test.ts` | DOI filename parsing and outbound links |
| `data/liveAgent.test.ts` | Chat history, streamed actions, sessions, node search, edits |

The current suite primarily tests pure helpers and mocked wire behavior. It is
not a full end-to-end browser test. For release acceptance, start the complete
stack and verify a real chat, graph selection, settings save, paper search, and
session switch manually.

## Manual smoke checklist

1. Load `/` without console errors.
2. Confirm Settings loads models and graph files.
3. Ask a question and observe progress plus a terminal answer.
4. Stop one run and confirm a later request still succeeds.
5. Select an answer's graph and search for a node.
6. In Splash mode, edit a disposable node and confirm the refresh.
7. Create/switch/delete chats and reload the browser.
8. Search/bookmark a publication and confirm the bookmark survives reload.
9. Run `npm test` and `npm run build`.

## Debugging

| Symptom | Action |
|---|---|
| Vite executable missing | Run `cd ui && npm ci` |
| `Failed to fetch` | Check the compiled API base and agent port |
| CORS error | Use port 5173 or update the backend origin allow-list |
| `/api` works locally but not in a custom image | Confirm the Nginx proxy and build arg |
| Stream appears buffered | Confirm requests pass through the supplied Nginx config with buffering off |
| Settings 404 | Restart the backend from current source |
| Graph edit 400 | Confirm Splash mode, valid endpoints, and predicate format |
| Stale UI after source change | Restart Vite or rebuild/recreate the frontend container |
| Tests pass but interactive feature fails | Run the manual stack smoke checklist; unit tests do not drive a real browser |

Use browser developer tools for request payloads and responses, and use
`docker compose logs frontend agent splash` for the container stack.
