# Development and testing

## Environment

Pixi owns the supported development environment:

```bash
cd splash_links
pixi install
```

Python 3.11 and 3.12 are supported by package metadata; the Pixi environment
constrains Python to `>=3.11,<3.13`.

## Validation

```bash
pixi run test
pixi run lint
pixi run fmt
```

`pixi run test` runs `_tests/` with package coverage and enforces a 90 percent
minimum. `fmt` changes files; use `lint` for a read-only check.

Build the documentation strictly before publishing:

```bash
pixi run mkdocs build -f mkdocs/mkdocs.yml --strict
```

Serve it locally with:

```bash
pixi run docs
```

## Test surfaces

| File | Coverage |
|---|---|
| `_tests/test_service.py` | FastAPI, GraphQL, store, embeddings, and migrations |
| `_tests/test_base_client.py` | Python client models, requests, and URI handling |
| `_tests/test_client_cli.py` | Remote client CLI parsing and output |
| `_tests/test_cli.py` | Local database inspection CLI |

FAIR2WISE also has root-level integration tests for Splash loading, fallback,
MatKG conversion, imports, graph updates, and Compose behavior. Those tests are
outside this package's local `_tests/` discovery.

## Project layout

| Path | Responsibility |
|---|---|
| `src/splash_links/app.py` | FastAPI factory, lifecycle, migrations, REST routes, GraphQL mount |
| `src/splash_links/schema.py` | Strawberry graph schema and resolvers |
| `src/splash_links/store.py` | Store interface, SQL tables, records, and SQLAlchemy implementation |
| `src/splash_links/client/base.py` | Synchronous Python service client |
| `src/splash_links/client/cli.py` | Remote client CLI |
| `src/splash_links/client/tiled.py` | Optional Tiled node conversion |
| `src/splash_links/cli.py` | Local database inspection and SQLite shell |
| `alembic/` | Schema migrations |
| `scripts/import_kg.py` | MatKG JSON importer |
| `scripts/seed_db.py` | Idempotent SQLite volume initializer |
| `Containerfile` | Two-stage standalone service image |

## Change checklist

- Store or migration changes: test SQLite migrations, cascades, pagination, and
  embedding dimensions.
- GraphQL changes: update schema/client operations together and test camelCase
  wire names.
- REST changes: update Pydantic payloads, HTTP status behavior, and client code.
- Importer changes: test missing endpoints, provenance properties, dry-run, and
  non-empty graph behavior.
- Container changes: rebuild the standalone image and verify the health route.
