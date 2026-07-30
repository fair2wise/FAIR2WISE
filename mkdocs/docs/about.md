# About

FAIR2WISE is a materials-science knowledge-graph and evidence workflow. It
combines publication processing, schema-aware extraction, provenance, graph
storage, retrieval-augmented generation, and a browser interface for inspecting
and editing the resulting graph.

The repository is licensed under the terms in `LICENSE.txt`.

## Documentation scope

This site documents the checked-in implementation, including:

- current application and agent paths;
- independently usable extraction and KG-RAG tools;
- the vendored Splash Links service;
- local, Docker, and NERSC operations;
- test boundaries; and
- explicitly marked legacy code.

The root `README.md` remains the concise user-facing entry point. This MkDocs
site is the developer and operator reference.

## Keeping it current

When a change affects behavior, update the closest page:

| Change | Documentation |
|---|---|
| Runtime/service boundary | `architecture.md` |
| Environment or default | `configuration.md` |
| Agent transition/approval | `agent-workflow.md` |
| HTTP request/response | `agent-api.md` or `splash-links.md` |
| UI behavior/state | `frontend.md` |
| New module/script | `repository-reference.md` and `scripts.md` |
| Deployment/startup | `operations.md` or `nersc.md` |
| Test command/scope | `testing.md` |

Validate with:

```bash
mkdocs build -f mkdocs/mkdocs.yml --strict
```
