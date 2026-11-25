"""Schema choices for the Extraction tab (A8, T38): the v0.3.x sets of the schema registry.

Choices come from :class:`~app.modules.schema.registry.SchemaRegistry` (``storage/schema/v0.3.x``), never
from a list kept here. A choice is identified by its registry id (``matkg_schema@0.3.1``) and carries the
file hash, so the bundle manifest records exactly what was chosen. Sets are pending owner review, so they
are selectable only on explicit request (``allow_pending_review=True``), which is what choosing one in the
Extraction tab is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from ..contracts.errors import ContractError, ErrorCode
from ..schema.registry import RegistryError, SchemaEntry, SchemaRegistry
from .user_schemas import UserSchema, is_user_schema_id, schema_origin

if TYPE_CHECKING:
    from .user_schemas import UserSchemaStore

#: ``review_status`` of a schema a user (or a model for the user) wrote: usable for KGs at once, never published.
USER_REVIEW_STATUS = "user_generated"

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
        "path": entry.path, "origin": "shipped",
    }


def _title(text: str, fallback: str) -> str:
    """The schema's own ``title`` (else its ``name``) for a label; the text was validated when it was saved."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return fallback
    value = doc.get("title") or doc.get("name") if isinstance(doc, dict) else None
    return " ".join(str(value).split())[:80] if value else fallback


def user_label(title: str, version: int) -> str:
    """``<title> (user schema, v3)``."""
    return f"{title} (user schema, v{version})"


def choice_for_user(schema: UserSchema) -> dict[str, Any]:
    """The choice of one stored user schema version: origin ``user``, status ``user_generated``, no host path.

    ``path`` is relative to the schema store (``<name>/<name>@vN.yaml``); the extractor resolves it there.
    """
    title = _title(schema.text, schema.name)
    return {
        "name": schema.id, "label": user_label(title, schema.version),
        "description": f"User schema {schema.name}, version {schema.version}, created {schema.created_at}",
        "version": f"v{schema.version}", "review_status": USER_REVIEW_STATUS, "sha256": schema.sha256,
        "path": schema.relpath, "origin": "user", "schema_name": schema.name, "schema_version": schema.version,
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


def _user_choices(store: UserSchemaStore | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    out = []
    for row in store.list():
        for version in reversed(row["versions"]):  # newest first
            try:
                out.append(choice_for_user(store.get(f"user/{row['name']}@v{version}")))
            except ContractError:  # a file that vanished or is not readable is not offered
                continue
    return out


def list_schemas(registry: SchemaRegistry | None = None, user_store: UserSchemaStore | None = None) -> list[dict[str, Any]]:
    """Every v0.3.x schema selectable for extraction, then every stored user schema version (newest first per name)."""
    return [choice_for(e) for e in _offered(registry or SchemaRegistry.discover())] + _user_choices(user_store)


def resolve_schema(schema_id: str, registry: SchemaRegistry | None = None,
                   user_store: UserSchemaStore | None = None) -> dict[str, Any]:
    """The choice for ``schema_id``; ``INVALID_INPUT`` when it is neither an offered v0.3.x schema nor a stored user schema."""
    if is_user_schema_id(schema_id):
        if user_store is None:
            raise ContractError(ErrorCode.INVALID_INPUT, "unknown extraction schema")
        try:
            return choice_for_user(user_store.get(schema_id))
        except ContractError:
            raise ContractError(ErrorCode.INVALID_INPUT, "unknown extraction schema") from None
    reg = registry or SchemaRegistry.discover()
    for entry in _offered(reg):
        if entry.schema_id == schema_id:
            return choice_for(entry)
    raise ContractError(ErrorCode.INVALID_INPUT, "unknown extraction schema")


def manifest_block(choice: dict[str, Any]) -> dict[str, Any]:
    """The ``schema`` block stored in ``manifest.json``."""
    return {"id": choice["name"], "version": choice["version"], "sha256": choice["sha256"], "path": choice["path"]}


__all__ = ("OFFERED_SET_PREFIX", "USER_REVIEW_STATUS", "choice_for", "choice_for_user", "list_schemas", "manifest_block",
           "resolve_schema", "schema_origin", "user_label")
