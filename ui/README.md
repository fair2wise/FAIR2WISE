# FAIR2WISE Web UI

The FAIR2WISE UI is a React 18/Vite 6 application for streamed agent chat,
knowledge-graph exploration and editing, publication discovery, sessions, and
runtime settings.

The maintained documentation is in the repository MkDocs site:

- [Web UI overview](../mkdocs/docs/frontend.md)
- [User guide](../mkdocs/docs/ui-user-guide.md)
- [Knowledge graph UI](../mkdocs/docs/ui-knowledge-graph.md)
- [UI architecture](../mkdocs/docs/ui-architecture.md)
- [Agent API integration](../mkdocs/docs/ui-api.md)
- [Development and testing](../mkdocs/docs/ui-development.md)

## Local development

From the repository root:

```bash
cd ui
npm ci
cd ..
./scripts/start_all.sh
```

Or, with the agent already running:

```bash
./scripts/start_agent_frontend.sh
```

Run UI checks with:

```bash
cd ui
npm test
npm run build
```

Docker Compose is the preferred complete deployment:

```bash
docker compose up --build
```

Open `http://127.0.0.1:5173`.

The original visual prototype came from the FAIR2WISE Figma design. Third-party
design attribution is recorded in [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
