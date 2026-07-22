# splash_links — Issue Notes

**Date:** 2026-06-09
**Repo:** <https://github.com/als-computing/splash_links>

---

## Issue 1: `pixi run serve` crashes on startup

**Severity:** Critical — app cannot start at all

**Error:**
```
TypeError: Can't instantiate abstract class SQLAlchemyStore without an implementation
for abstract methods 'create_embedding_model', 'delete_embedding_model',
'get_embedding_model', 'list_embedding_models'
```

**Root Cause:**
The `Store` ABC in `src/splash_links/store.py` declares 4 abstract methods for embedding model CRUD:
- `create_embedding_model`
- `get_embedding_model`
- `list_embedding_models`
- `delete_embedding_model`

The concrete `SQLAlchemyStore` class never implemented them, despite the `_embedding_models` SQLAlchemy table already being defined. Python raises `TypeError` when attempting to instantiate an abstract class with unimplemented methods.

**Fix:**
Added all 4 method implementations to `SQLAlchemyStore` following the same patterns used by existing entity and link CRUD operations.

**Scope:** Repo-level bug. Affects all users, not device-specific.

---

## Issue 2 (Observed, Not Yet Fixed): `create_embedding` signature mismatch

**Severity:** Medium — latent bug, not currently causing a crash

**Details:**
The `Store` ABC declares:
```python
def create_embedding(self, entity_id: str, vector: list[float], embedding_model_id: str, ...) -> EmbeddingRecord
```

The `SQLAlchemyStore` concrete implementation declares:
```python
def create_embedding(self, entity_id: str, vector: list[float], embedding_model: str = "default", ...) -> EmbeddingRecord
```

The parameter is named `embedding_model` (a plain string) in the concrete class vs `embedding_model_id` (a foreign key reference) in the ABC. This mismatch means:
- Callers using the abstract `Store` interface would pass `embedding_model_id` as a keyword argument and get a `TypeError`.
- The concrete method inserts `embedding_model` as a column value, but the `_embeddings` table column is `embedding_model_id` (a FK to `embedding_models.id`), so the insert would also fail at the DB level.

**Status:** Not fixed yet. Needs alignment of parameter name and column reference.

---

## Import Script: `scripts/import_kg.py`

**Added:** 2026-06-09

Imports matkg JSON files (from `f2wlocal/storage/kg/`) into splash-links via the GraphQL client. Maps `things` → entities and `associations` → links, preserving all metadata in entity properties.
