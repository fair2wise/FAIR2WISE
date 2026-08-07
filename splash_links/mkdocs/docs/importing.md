# Importing MatKG

`scripts/import_kg.py` imports FAIR2WISE/MatKG JSON documents shaped as:

```json
{
  "things": [],
  "associations": []
}
```

The Splash service must be running before import.

## Validate first

```bash
pixi run python scripts/import_kg.py --dry-run ../storage/kg/graph.json
```

Dry-run validates file loading and reports graph counts without writing.

## Import

```bash
pixi run python scripts/import_kg.py ../storage/kg/graph.json
```

Multiple files are accepted, and `--url` selects another service:

```bash
pixi run python scripts/import_kg.py \
  --url splash://localhost:8081 \
  ../storage/kg/graph-a.json \
  ../storage/kg/graph-b.json
```

Use `--if-empty` for idempotent startup/bootstrap behavior. It exits
successfully without importing when the remote service already contains an
entity:

```bash
pixi run python scripts/import_kg.py --if-empty ../storage/kg/graph.json
```

## Mapping rules

Import runs in two phases:

1. Every item in `things` becomes an entity, and the importer records a
   MatKG-ID-to-Splash-UUID map.
2. Associations become links only when both endpoints were created from the
   same input file.

| MatKG field | Splash destination |
|---|---|
| `id` | Entity `uri` and `properties.matkg_id` |
| `name` | Entity `name`; falls back to `id` |
| `category` | Entity `entity_type`; falls back to `Thing` |
| Other non-null node fields | Entity `properties` |
| Association `subject` / `object` | Resolved Splash UUIDs |
| Association `predicate` | Link `predicate`; defaults to `rel:related_to` |
| Association `has_evidence` | Link property |
| Input filename | `properties.source_file` on entities and links |

Associations with a missing endpoint are skipped and counted. Non-JSON inputs
are skipped; missing input paths stop the command.

## Duplicate behavior

The importer creates records; it does not reconcile entities by URI or delete
old graph content. Reimporting into a populated database can therefore create
duplicate or stale records. Use `--if-empty` for bootstrap, or deliberately
reset/replace the target database before a full authoritative reimport.

In FAIR2WISE, use the guarded root maintenance scripts for destructive reset
and reimport. Do not manually delete the tracked `links.sqlite` seed while the
service or root Compose stack is running.

## Compose seed initialization

`scripts/seed_db.py` is used by the FAIR2WISE root Compose initializer. It:

- verifies the seed with SQLite `PRAGMA integrity_check`;
- counts entities and links;
- hashes the seed;
- preserves an existing initialized volume;
- backs up an unmarked legacy database before replacement; and
- copies through a temporary file before atomically replacing the destination.

The marker `.fair2wise-splash-seed` records initialization inside the volume.
Changing the repository seed does not overwrite an already initialized marked
volume.
