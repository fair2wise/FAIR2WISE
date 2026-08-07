# Splash Links

Splash Links is a lightweight graph service for storing arbitrary entities,
directed predicate-labelled links, and vector embeddings in SQL. It exposes
graph operations through Strawberry GraphQL and embedding operations through
REST and GraphQL.

Use Splash Links when an application needs editable graph persistence without
operating a full SPARQL triplestore. SQLite is the default for local and
single-user deployments; PostgreSQL with `pgvector` supports multi-user
deployments and database-side vector search.

## Quick start

Install [Pixi](https://pixi.sh/latest/), then run from the `splash_links`
directory:

```bash
pixi install
pixi run serve
```

The service listens on `0.0.0.0:8081`. Verify it and open GraphiQL:

```bash
curl -fsS http://127.0.0.1:8081/splash_links/health
```

```text
http://127.0.0.1:8081/splash_links/graphql
```

`pixi run serve` uses `links.sqlite` through the package's normal launcher.
Set `SPLASH_LINKS_DB` explicitly to select another SQLite file or a SQLAlchemy
database URL:

```bash
SPLASH_LINKS_DB=/data/links.sqlite pixi run serve
```

## Core capabilities

- Create, read, update, list, and delete graph entities and links.
- Traverse incoming and outgoing relationships.
- Store normalized vectors attached to entities.
- Search embeddings by cosine distance.
- Use SQLite files, in-memory SQLite, or PostgreSQL.
- Apply Alembic migrations during service startup.
- Access the service from Python, a remote client CLI, or GraphQL directly.
- Import FAIR2WISE/MatKG JSON graphs with preserved provenance properties.

## FAIR2WISE integration

FAIR2WISE vendors this package under `splash_links/`. In the root Compose
deployment, Splash is private to the Compose network: the frontend is the only
published service. A one-shot initializer copies the tracked `links.sqlite`
seed into a writable named volume, so normal container writes never modify the
repository seed.

The root FAIR2WISE deployment and the standalone `splash_links/docker-compose.yml`
serve different purposes:

| Deployment | Storage | Published ports | Intended use |
|---|---|---|---|
| FAIR2WISE root Compose | SQLite named volume initialized from the tracked seed | Frontend only | Complete FAIR2WISE application |
| Splash standalone Compose | PostgreSQL volume | Splash `8081`, PostgreSQL loopback `5432` | Splash development and multi-user testing |
| Pixi | Selected by `SPLASH_LINKS_DB` | Splash `8081` | Local service development |

## Documentation map

- [Concepts and storage](concepts.md) explains records, identifiers, deletion,
  backends, embeddings, and migrations.
- [API and clients](api.md) covers GraphQL, REST, Python, and both CLIs.
- [Importing MatKG](importing.md) documents the two-phase importer and seed
  lifecycle.
- [Deployment](deployment.md) covers Pixi, containers, persistence, health, and
  backup considerations.
- [Development and testing](test.md) lists the project layout and validation
  commands.
