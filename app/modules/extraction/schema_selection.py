"""Explicit schema-set selection for validated T06 records (H3 F5/F6, gate ``new_schema_slots``).

The default extraction and build path stays on the base schema set: nothing in
:mod:`.record_validation` maps onto schema set 0.3.0 unless the caller names the
set *and* acknowledges that it is pending owner review (``allow_pending_review``,
until the owner signs off ``docs/migration/reviews/H3/schema_diff_T22C.md``).
This module only builds a json2kg-shaped *candidate* payload through T22C's
:func:`app.modules.schema.experiment_mapping.map_validated_records`; it writes no
graph and publishes nothing (``publish`` stays closed, H5).

The schema modules are imported lazily so importing the extraction package stays
free of schema loading.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

from ..evidence.records import EvidenceRecord, ReportedQuantity, TagDecision

#: Schema sets this module can map onto (explicit selection only). 0.3.1 (T22D) also fills the
#: normalized-value slots on QuantityValue from ``normalize_quantity`` (D1.4).
SELECTABLE_SCHEMA_SETS = ("0.3.0", "0.3.1")
#: Overlay used when a caller selects a set without naming the schema.
DEFAULT_MAPPING_SCHEMA = "rsoxs_schema"


def _record_id(index: int, name: str) -> str:
    return f"q{index}-" + (re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "value")


def validation_from_quantities(
    accepted: Sequence[tuple[ReportedQuantity, EvidenceRecord, TagDecision]],
) -> Any:
    """A :class:`~.record_validation.DocumentValidation` of observations for accepted quantities.

    Each observation carries its *decided* tag as ``status`` (so 0.3.0
    ``evidence_source_tag`` is the F2/F8 tag) and the located evidence ref. A
    ``sample_id`` string has no Sample record behind it on this path, so it is
    kept in the observation caveat rather than invented as a Sample node.
    """
    from .record_validation import DocumentValidation, EvidenceLocator, ScientificObservation

    out = DocumentValidation()
    for index, (quantity, evidence, decision) in enumerate(accepted):
        rid = _record_id(index, quantity.name)
        caveats = [c for c in (quantity.caveat,
                               f"sample_id={quantity.sample_id}" if quantity.sample_id else "",
                               f"processing_state={quantity.processing_state}" if quantity.processing_state else "")
                   if c]
        observation = ScientificObservation(
            observation_id=rid, measurement_id=None, sample_ids=[], quantity_label=quantity.name,
            value=quantity.raw_value, unit=quantity.raw_unit, qualitative_value=quantity.qualitative_value or None,
            uncertainty=quantity.uncertainty.value, uncertainty_kind=quantity.uncertainty.kind.value,
            status=decision.tag.value, caveat="; ".join(caveats),
            evidence=EvidenceLocator(section=evidence.ref.locator.section or "", quote=evidence.ref.quote or "",
                                     page=evidence.ref.locator.page, block_id=None, table_id=None,
                                     figure_panel=None),
        )
        out.observations.append(observation)
        out.evidence[f"observation:{rid}"] = evidence.ref
        out.tags[f"observation:{rid}"] = decision
    return out


def map_to_schema_set(
    validation: Any,
    document: Any,
    *,
    work_id: str,
    source_paper: str,
    schema_set: str,
    schema_id: str = DEFAULT_MAPPING_SCHEMA,
    allow_pending_review: bool = False,
    registry: Any = None,
) -> dict[str, Any]:
    """Map accepted records onto ``schema_id@schema_set`` (explicit selection only).

    Raises:
        PermissionError: ``new_schema_slots`` is closed.
        ValueError: ``schema_set`` is not selectable here.
        RegistryError: the set is pending owner review and ``allow_pending_review`` is false.
    """
    from . import require_gate

    require_gate("new_schema_slots")
    if schema_set not in SELECTABLE_SCHEMA_SETS:
        raise ValueError(f"schema set {schema_set!r} is not selectable; known: {list(SELECTABLE_SCHEMA_SETS)}")
    from ..schema.experiment_mapping import map_validated_records
    from ..schema.registry import SchemaRegistry

    registry = registry or SchemaRegistry.discover()
    entry = registry.select(f"{schema_id}@{schema_set}", "extraction", allow_pending_review=allow_pending_review)
    payload = map_validated_records(validation, document, work_id=work_id, source_paper=source_paper, entry=entry,
                                    allow_pending_review=allow_pending_review)
    return {"schema": entry.schema_id, "schema_set": schema_set, "review_status": entry.review_status,
            "status": "candidate", "payload": payload}


__all__ = [
    "DEFAULT_MAPPING_SCHEMA", "SELECTABLE_SCHEMA_SETS", "map_to_schema_set", "validation_from_quantities",
]
