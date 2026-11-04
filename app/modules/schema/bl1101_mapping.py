"""Map bl1101 ops records onto the schema-set 0.3.x literature classes, and back (T22D; owner decision D1.3, O-S2).

The mapping table is not written here: it is read from the bl1101_v3 overlay (``storage/schema/v0.3.1/
bl1101_schema.yaml``), whose ``Sample``, ``Detector`` and ``Endstation`` classes carry the annotations
``f2w_maps_to_class`` (the 0.3.x class an ops record maps onto) and, for instruments,
``f2w_maps_to_instrument_category`` (the ``InstrumentKindEnum`` value). The owner reviews that table in the
schema diff; the code only applies it:

=============  ==============  ==========================================================================
ops class      0.3.x class     what the mapped record gets
=============  ==============  ==========================================================================
``Sample``     ``Sample``      ``sample_label`` = the ops ``name``
``Detector``   ``Instrument``  ``instrument_category`` = ``detector``
``Endstation`` ``Instrument``  ``instrument_category`` = ``endstation``
=============  ==============  ==========================================================================

A mapped record keeps every other key verbatim (id, name, description, ``source_papers``, PV names...), moves
the ops class name into the existing ``raw_category`` slot, and records what changed under ``ops_mapping`` so
:func:`unmap_ops_nodes` restores the ops record exactly. Nodes of other classes and all edges pass through
untouched: ops predicates (``hasDetector``, ``part_of``...) keep their beamline meaning and are not
reinterpreted as literature links. Nothing is guessed: a node whose class has no mapping row is returned as
it is.

This module is pure and nothing calls it by default. bl1101_v3 belongs to schema set 0.3.1, which is pending
owner review, so mapping is refused unless the caller passes ``allow_pending_review=True`` (as with
:mod:`.experiment_mapping`). The ops ingest (``bl1101_ingest``, ``bl1101_v2``) and the motor/stage map are
not changed by this module.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .loading import load_schema_view
from .registry import RegistryError, SchemaEntry

MAPPING_VERSION = "1"
DECISION = "D1.3"
CLASS_ANNOTATION = "f2w_maps_to_class"
KIND_ANNOTATION = "f2w_maps_to_instrument_category"
#: Key under which a mapped record keeps what the mapping changed (so the reverse mapping is exact).
PROVENANCE_KEY = "ops_mapping"
_KINDS = frozenset({"beamline", "endstation", "detector"})


class OpsMappingError(ValueError):
    """The overlay carries no usable mapping table, or a mapped record cannot be reversed."""


@dataclass(frozen=True)
class MappingRow:
    """One row of the overlay's mapping table.

    Attributes:
        ops_class: bl1101 class name (``Detector``).
        target_class: 0.3.x class it maps onto (``Instrument``).
        instrument_category: ``InstrumentKindEnum`` value for instruments, else ``None``.
    """

    ops_class: str
    target_class: str
    instrument_category: str | None = None


def _annotation(cls: Any, tag: str) -> str | None:  # noqa: ANN401  # Any: linkml ClassDefinition (untyped)
    ann = (cls.annotations or {}).get(tag)
    value = getattr(ann, "value", ann)
    return str(value) if value is not None else None


def mapping_table(entry: SchemaEntry, *, allow_pending_review: bool = False) -> dict[str, MappingRow]:
    """The mapping rows declared by ``entry`` (a bl1101 overlay), keyed by ops class name.

    Raises:
        RegistryError: ``entry`` is pending owner review and ``allow_pending_review`` is false.
        OpsMappingError: the overlay declares no mapping row (bl1101_v2 and earlier), or a row names a class or
            instrument kind the closure does not define.
    """
    if entry.pending_review and not allow_pending_review:
        raise RegistryError(f"{entry.path} is pending owner review; pass allow_pending_review=True")
    view = load_schema_view(entry)
    classes = view.all_classes()
    rows: dict[str, MappingRow] = {}
    for name, cls in classes.items():
        target = _annotation(cls, CLASS_ANNOTATION)
        if target is None:
            continue
        if target not in classes:
            raise OpsMappingError(f"{entry.path}: {name} maps to {target!r}, which the closure does not define")
        kind = _annotation(cls, KIND_ANNOTATION)
        if kind is not None and kind not in _KINDS:
            raise OpsMappingError(f"{entry.path}: {name} names instrument kind {kind!r}, not one of {sorted(_KINDS)}")
        rows[name] = MappingRow(name, target, kind)
    if not rows:
        raise OpsMappingError(f"{entry.path} ({entry.version}) declares no {CLASS_ANNOTATION} mapping; needs bl1101_v3")
    return rows


def map_ops_node(node: Mapping[str, Any], rows: Mapping[str, MappingRow], entry: SchemaEntry) -> dict[str, Any]:
    """One ops node as a 0.3.x record, or an unchanged copy when its class has no mapping row."""
    out = dict(node)
    row = rows.get(str(node.get("category")))
    if row is None or PROVENANCE_KEY in node:
        return out
    added: list[str] = []
    if row.target_class == "Sample" and "sample_label" not in node and node.get("name"):
        out["sample_label"] = node["name"]
        added.append("sample_label")
    if row.instrument_category and "instrument_category" not in node:
        out["instrument_category"] = row.instrument_category
        added.append("instrument_category")
    out["category"] = row.target_class
    out["type"] = f"matkg:{row.target_class}"
    out["raw_category"] = row.ops_class
    provenance: dict[str, Any] = {
        "schema": entry.schema_id, "schema_version": entry.version, "schema_sha256": entry.sha256,
        "mapping_version": MAPPING_VERSION, "decision": DECISION, "category": row.ops_class, "added": added,
    }
    if "type" in node:
        provenance["type"] = node["type"]
    if "raw_category" in node:
        provenance["raw_category"] = node["raw_category"]
    out[PROVENANCE_KEY] = provenance
    return out


def unmap_ops_node(node: Mapping[str, Any]) -> dict[str, Any]:
    """The ops record a mapped node came from (exact), or an unchanged copy of an unmapped node."""
    out = dict(node)
    provenance = out.pop(PROVENANCE_KEY, None)
    if provenance is None:
        return out
    if not isinstance(provenance, Mapping) or "category" not in provenance:
        raise OpsMappingError(f"node {node.get('id')!r} carries an unreadable {PROVENANCE_KEY}")
    for key in provenance.get("added", []):
        out.pop(key, None)
    out["category"] = provenance["category"]
    if "type" in provenance:
        out["type"] = provenance["type"]
    else:
        out.pop("type", None)
    if "raw_category" in provenance:
        out["raw_category"] = provenance["raw_category"]
    else:
        out.pop("raw_category", None)
    return out


def map_ops_nodes(
    nodes: Iterable[Mapping[str, Any]], *, entry: SchemaEntry, allow_pending_review: bool = False
) -> list[dict[str, Any]]:
    """Map every ``Sample``/``Detector``/``Endstation`` node under ``entry``'s table; other nodes pass through."""
    rows = mapping_table(entry, allow_pending_review=allow_pending_review)
    return [map_ops_node(n, rows, entry) for n in nodes]


def unmap_ops_nodes(nodes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reverse :func:`map_ops_nodes`; nodes without mapping provenance pass through."""
    return [unmap_ops_node(n) for n in nodes]


def map_ops_graph(
    payload: Mapping[str, Any], *, entry: SchemaEntry, allow_pending_review: bool = False
) -> dict[str, Any]:
    """A json2kg payload with its ops nodes mapped; edges and other keys are copied as they are."""
    nodes_key = "things" if "things" in payload else "nodes"
    out = dict(payload)
    out[nodes_key] = map_ops_nodes(payload.get(nodes_key) or [], entry=entry, allow_pending_review=allow_pending_review)
    return out


def unmap_ops_graph(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reverse :func:`map_ops_graph`."""
    nodes_key = "things" if "things" in payload else "nodes"
    out = dict(payload)
    out[nodes_key] = unmap_ops_nodes(payload.get(nodes_key) or [])
    return out


def mapped_categories(rows: Mapping[str, MappingRow]) -> Sequence[str]:
    """The ops class names the table maps (sorted)."""
    return sorted(rows)


__all__ = [
    "CLASS_ANNOTATION", "DECISION", "KIND_ANNOTATION", "MAPPING_VERSION", "MappingRow", "OpsMappingError",
    "PROVENANCE_KEY", "map_ops_graph", "map_ops_node", "map_ops_nodes", "mapped_categories", "mapping_table",
    "unmap_ops_graph", "unmap_ops_node", "unmap_ops_nodes",
]
