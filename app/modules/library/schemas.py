"""Schema choices for the Extraction tab (A8, T38): the v0.3.x sets of the schema registry.

Choices come from :class:`~app.modules.schema.registry.SchemaRegistry` (``storage/schema/v0.3.x``), never
from a list kept here. A choice is identified by its registry id (``matkg_schema@0.3.1``) and carries the
file hash, so the bundle manifest records exactly what was chosen. Sets are pending owner review, so they
are selectable only on explicit request (``allow_pending_review=True``), which is what choosing one in the
Extraction tab is.
"""

from __future__ import annotations

from typing import Any

from ..contracts.errors import ContractError, ErrorCode
from ..schema.registry import RegistryError, SchemaEntry, SchemaRegistry

#: Registry ``schema_set`` versions offered for extraction.
OFFERED_SET_PREFIX = "0.3."

_LABELS = {"matkg_schema": "General materials KG", "rsoxs_schema": "RSoXS"}


def _label(entry: SchemaEntry) -> str:
    base = entry.schema_id.split("@", 1)[0]
    return f"{_LABELS.get(base, base)} (v{entry.schema_set})"


def choice_for(entry: SchemaEntry) -> dict[str, Any]:
    """``SchemaChoice`` of ``extractionApi.ts`` plus version, review status and hash (no host path)."""
    return {
        "name": entry.schema_id, "label": _label(entry),
        "description": f"{entry.path}; schema set {entry.schema_set}, {entry.review_status}",
        "version": entry.schema_set, "review_status": entry.review_status, "sha256": entry.sha256,
        "path": entry.path,
    }


def _offered(registry: SchemaRegistry) -> list[SchemaEntry]:
    out = []
    for entry in registry:
        if entry.schema_set and entry.schema_set.startswith(OFFERED_SET_PREFIX):
            try:
                registry.select(entry.schema_id, "extraction", allow_pending_review=True)
            except RegistryError:  # not selectable for extraction: not offered
                continue
            out.append(entry)
    return sorted(out, key=lambda e: (e.schema_id.split("@", 1)[0], str(e.schema_set)))


def list_schemas(registry: SchemaRegistry | None = None) -> list[dict[str, Any]]:
    """Every v0.3.x schema selectable for extraction."""
    return [choice_for(e) for e in _offered(registry or SchemaRegistry.discover())]


def resolve_schema(schema_id: str, registry: SchemaRegistry | None = None) -> dict[str, Any]:
    """The choice for ``schema_id``; ``INVALID_INPUT`` when it is not an offered v0.3.x schema."""
    reg = registry or SchemaRegistry.discover()
    for entry in _offered(reg):
        if entry.schema_id == schema_id:
            return choice_for(entry)
    raise ContractError(ErrorCode.INVALID_INPUT, "unknown extraction schema")


def manifest_block(choice: dict[str, Any]) -> dict[str, Any]:
    """The ``schema`` block stored in ``manifest.json``."""
    return {"id": choice["name"], "version": choice["version"], "sha256": choice["sha256"], "path": choice["path"]}


__all__ = ("OFFERED_SET_PREFIX", "choice_for", "list_schemas", "manifest_block", "resolve_schema")
