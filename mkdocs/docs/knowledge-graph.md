# Knowledge graph

## Portable graph format

FAIR2WISE graph JSON has two top-level arrays:

```json
{
  "things": [
    {
      "id": "matkg:P3HT",
      "name": "P3HT",
      "category": "ConjugatedPolymer",
      "description": "..."
    }
  ],
  "associations": [
    {
      "subject": "matkg:P3HT",
      "predicate": "rel:has_property",
      "object": "matkg:Regioregularity",
      "has_evidence": "..."
    }
  ]
}
```

This is the interchange format between extracted terminology, local retrieval,
the UI graph payload, and Splash Links import/export.

## Conversion from terms

`app/modules/json2kg.py`:

- generates stable `matkg:` IDs by removing spaces and punctuation;
- converts each term to a node while preserving formula, properties,
  provenance, and publications;
- creates stub `Unknown` nodes for relationship targets not yet extracted;
- prefixes relationship predicates with `rel:`;
- deduplicates `(subject, predicate, object)` triples;
- creates code snippet nodes using a code hash to avoid collisions;
- rejects short, anchorless, or delimiter-unbalanced code fragments; and
- adds `rel:has_code_snippet` edges by same-page then same-paper provenance.

Run conversion directly:

```bash
python3 app/modules/json2kg.py \
  storage/terminology/extracted_terms.json \
  storage/kg/matkg.json \
  --verbose
```

## LinkML schema

`storage/schema/matkg_schema.yaml` defines:

- the `Graph` root;
- a `Thing` hierarchy for materials-science entities;
- `Publication`, `CodeSnippet`, and `DomainFeature`;
- abstract and specialized association classes;
- bibliographic, chemistry, device, property, technique, and code slots; and
- common relation slots including `related_to`, `part_of`, `has_property`,
  `processed_by`, `used_in`, `causes`, `affects`, `measures`, and `contains`.

The extraction layer uses it for normalization. The JSON graph remains a
property graph rather than a fully LinkML-validated serialization.

## JSON and Splash modes

| Mode | Reads | Edits | Persistence |
|---|---|---|---|
| `json` | A selected `storage/kg/*.json` file | Read-only through the current UI | File |
| `splash` | Entities and links from Splash | Node fields, publications, snippets, relationships | SQL database |

When Splash is unreachable, `KnowledgeGraph` can fall back to the configured
JSON file and reports `graph_source_used = "json_fallback"`.

## Import to Splash Links

`splash_links/scripts/import_kg.py` imports in two phases:

1. create entities and build a MatKG-ID to Splash-UUID map;
2. create links using the UUID map.

The original MatKG ID is retained as the entity URI and in
`properties.matkg_id`. Non-core node fields are kept in entity properties.
Link evidence and source file are kept in link properties.

```bash
cd splash_links
pixi run python scripts/import_kg.py \
  --url http://localhost:8081 \
  ../storage/kg/matkg_with_code.json
```

Use `--dry-run` to validate and count without writing.

## Rebuild and reimport

`app/modules/f2w_agent/kg_update.py` contains the runtime bridge:

- `rebuild_kg()` converts cumulative terms to session KG JSON;
- `splash_reimport()` uses the live API for a guarded wipe, then executes the
  importer;
- `load_splash_graph()` pages through entities/links and reconstructs MatKG;
- `export_splash_graph_to_json()` writes an editable graph snapshot.

The workflow reloads the in-memory retrieval graph after successful growth.

`scripts/reimport_merged_kg.sh` merges a current graph with a larger graph,
preferring non-empty current values, deduplicates nodes/edges, starts Splash if
needed, and imports the result.

## Graph editing contract

The UI sends a node patch containing any combination of:

- label, type, description, or code body;
- publication records;
- linked code snippet upserts/unlinks; and
- directed relationship additions/removals.

Relationship keys are exact `(source, predicate, target)` triples. Additions are
idempotent; removals only affect the selected direction. Predicates are
normalized to the `rel:` namespace.

Editing is blocked in JSON mode. In Splash mode, the API updates SQL records,
refreshes the session graph, and returns the normalized node.

## Generated graph collections

`storage/kg/` contains checkpoint graphs produced by multiple models and paper
counts, an X-ray graph, and merged/code-enriched graphs. These are data
artifacts, not Python modules. `storage/terminology/` contains their cumulative
term sources. Empty 40-byte graphs represent runs with no extracted nodes.
