# API and clients

## Endpoints

| Interface | Path | Purpose |
|---|---|---|
| GraphQL/GraphiQL | `/splash_links/graphql` | Entity/link CRUD, traversal, nearest embeddings |
| REST | `GET /splash_links/health` | Liveness check |
| REST | `POST /splash_links/embeddings` | Create an entity embedding |
| REST | `GET /splash_links/embeddings` | List/filter embeddings |
| REST | `GET /splash_links/embeddings/{id}` | Fetch one embedding |
| REST | `DELETE /splash_links/embeddings/{id}` | Delete one embedding |

GraphQL field names are camelCase on the wire even though the Python schema
uses snake_case.

## GraphQL operations

Queries:

- `entity(id)` and `entities(entityType, limit, offset)`;
- `link(id)` and `links(subjectId, predicate, objectId, limit, offset)`;
- `Entity.outgoingLinks(predicate, limit, offset)`;
- `Entity.incomingLinks(predicate, limit, offset)`; and
- `nearestEmbeddings(vector, embeddingModel, entityId, limit, offset)`.

Mutations:

- `createEntity` and `updateEntity`;
- `deleteEntity`;
- `createLink` and `updateLink`; and
- `deleteLink`.

Example entity query:

```graphql
query ListMaterials {
  entities(entityType: "Material", limit: 20, offset: 0) {
    id
    name
    uri
    properties
    outgoingLinks {
      predicate
      object { id name entityType }
    }
  }
}
```

Example mutations:

```graphql
mutation CreateMaterial {
  createEntity(input: {
    entityType: "Material"
    name: "P3HT"
    uri: "matkg:P3HT"
    properties: {formula: "C10H14S"}
  }) {
    id
    name
  }
}
```

```graphql
mutation Connect($subject: ID!, $object: ID!) {
  createLink(input: {
    subjectId: $subject
    predicate: "rel:has_property"
    objectId: $object
  }) {
    id
    predicate
  }
}
```

## Embedding REST

```bash
curl -X POST http://127.0.0.1:8081/splash_links/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "entityId": "ENTITY_UUID",
    "embeddingModel": "example-model",
    "vector": [0.12, -0.03, 0.88],
    "properties": {"chunk": 1}
  }'
```

Filter the collection with `entityId`, `embeddingModel`, `limit`, and `offset`:

```bash
curl 'http://127.0.0.1:8081/splash_links/embeddings?entityId=ENTITY_UUID&limit=20'
```

Nearest-neighbor search is GraphQL:

```graphql
query {
  nearestEmbeddings(
    vector: [0.11, -0.02, 0.90]
    embeddingModel: "example-model"
    limit: 5
  ) {
    distance
    embedding { id entityId dimensions }
  }
}
```

## Python client

```python
from splash_links.client.base import from_uri

client = from_uri("splash://localhost:8081")
material = client.create_entity(
    "Material",
    name="P3HT",
    uri="matkg:P3HT",
    properties={"formula": "C10H14S"},
)
prop = client.create_entity("Property", name="Mobility", uri="matkg:Mobility")
client.create_link(material, "rel:has_property", prop)
links = client.find_links(material, predicate="rel:has_property")
```

`from_uri()` accepts `splash://`, `http://`, and `https://`. Entity parameters
can be returned entity models, UUID strings, or supported Tiled nodes.

## Command-line clients

The remote CLI talks to a running service:

```bash
pixi run splash-links-client --help
pixi run splash-links client --help
```

It provides `create-entity`, `create-link`, `find-links`, `create-embedding`,
and `nearest-embeddings`. Use `--uri` or `SPLASH_LINKS_URI` to select the
server.

The local CLI opens `SPLASH_LINKS_DB` directly:

```bash
pixi run entities -- --type Material --limit 10
pixi run links -- --predicate rel:has_property
pixi run embeddings -- --entity ENTITY_UUID
pixi run db
```

Do not use the local write shell against a database being modified by the
service. Prefer GraphQL mutations for application edits.
