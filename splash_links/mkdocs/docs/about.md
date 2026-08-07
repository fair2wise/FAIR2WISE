# About Splash Links

Splash Links is maintained by the ALS Computing group. It began as
`splash-ml`; the older MongoDB implementation is retained under the `v0.1.0`
tag. The current service uses SQLAlchemy and SQL storage.

The package is independently installable as `splash-links` and is also
vendored by FAIR2WISE. Its public boundary consists of:

- a FastAPI application factory;
- a Strawberry GraphQL schema;
- REST endpoints for embedding records;
- synchronous Python and command-line clients; and
- local database inspection commands.

Splash Links intentionally keeps the graph model generic. Scientific meaning,
ontology constraints, provenance conventions, and predicate vocabularies are
owned by clients such as FAIR2WISE rather than enforced by the database.

The source is distributed under the license in `splash_links/LICENSE`.
