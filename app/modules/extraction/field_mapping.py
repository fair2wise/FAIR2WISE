"""Approved H3 field mappings applied by the T06B validation stage (F1, F4).

F1 (quantity names). :data:`QUANTITY_FIELD_MAP` is the per-row table: for every
quantity name a strategy may propose, its dimension, where it lives on the
*current* schema (most names have no current slot and stay candidate quantity
records), and where it lives in schema set 0.3.0 (``QuantityValue`` with the
name as ``quantity_label``; never renamed). The 0.3.0 column is used only behind
an explicit schema-set selection (:mod:`.schema_selection`).

F4 (peaks). :func:`peak_slot_values` fills the existing string slots
``ExperimentalTechnique.peak_positions`` / ``d_spacing`` / ``peak_assignments`` from
peaks the paper *reports*. A cited, derived or assumed peak never fills them,
and q is never derived from d or back to fill a slot. The fill is a candidate
for the work's technique nodes; attaching it to a node is a graph write that
waits for canonical resolution (``canonical_merge``, C4) and publication (H5).

Nothing here reads the environment, a model or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..evidence.records import EvidenceRecord, EvidenceSource, ReportedQuantity, TagDecision
from ..evidence.units import (
    INSTRUMENT_QUANTITY_DIMENSION, QUANTITY_DIMENSION, base_quantity_name, expected_dimension,
)

#: Version of the F1/F4 tables below.
FIELD_MAPPING_VERSION = "h3-f1f4.1"


@dataclass(frozen=True)
class FieldRow:
    """One F1 row: a proposed quantity name and its F2W homes.

    Attributes:
        name: Quantity name as strategies emit it (base name; peaks carry ``[label]``).
        dimension: ``length``/``reciprocal``/``energy``/``angle``.
        current_slot: Slot on the current (base) schema the value may fill, or ``None``
            when the value stays a candidate quantity record only.
        fills_current_slot: Whether T06B fills ``current_slot`` (F4 peaks only).
        schema_030: Home in schema set 0.3.0 (explicit selection only).
        note: Why the row is as it is.
    """

    name: str
    dimension: str
    current_slot: Optional[str]
    fills_current_slot: bool
    schema_030: str
    note: str = ""


_QV = "QuantityValue (quantity_label = name, numeric_value/unit as printed, evidence_source_tag)"


def _rows() -> Tuple[FieldRow, ...]:
    rows: List[FieldRow] = [
        FieldRow(name, dimension, None, False, _QV, "no current F2W slot; candidate quantity record")
        for name, dimension in sorted(QUANTITY_DIMENSION.items())
    ]
    rows += [
        FieldRow("peak_q", "reciprocal", "ExperimentalTechnique.peak_positions", True, _QV,
                 "F4: reported peaks only, string as printed (q = <value> <unit>)"),
        FieldRow("peak_d_spacing", "length", "ExperimentalTechnique.d_spacing", True, _QV,
                 "F4: reported peaks only, string as printed (<value> <unit>)"),
    ]
    for name, dimension in sorted(INSTRUMENT_QUANTITY_DIMENSION.items()):
        slot = "RSoXSMeasurement.photon_energy_eV" if name in ("beam_energy", "photon_energy") else None
        rows.append(FieldRow(
            name, dimension, slot, False, _QV,
            "instrument setting: tagged assumed when assumed (F8), never presented as paper evidence; "
            + ("existing slot not filled: it sits on a technique, not a run" if slot else "no current slot"),
        ))
    return tuple(rows)


#: F1 per-row table, keyed by base quantity name.
QUANTITY_FIELD_MAP: Mapping[str, FieldRow] = {row.name: row for row in _rows()}

#: The peak label of a peak quantity fills this slot (F4).
PEAK_ASSIGNMENT_SLOT = "ExperimentalTechnique.peak_assignments"


def field_row(name: str) -> Optional[FieldRow]:
    """The F1 row for a (possibly peak-qualified) quantity name, or ``None`` when unmapped."""
    return QUANTITY_FIELD_MAP.get(base_quantity_name(name))


def _peak_label(name: str) -> str:
    return name[name.index("[") + 1:-1].strip() if "[" in name and name.endswith("]") else ""


def _printed(value: Optional[float], unit: str) -> str:
    return f"{value:g} {unit}".strip() if value is not None else ""


def peak_slot_values(
    accepted: Iterable[Tuple[ReportedQuantity, EvidenceRecord, TagDecision]],
) -> Dict[str, Any]:
    """F4: existing peak slot values from accepted *reported* peaks, each with its evidence ids.

    Returns ``{"peak_positions": [...], "d_spacing": [...], "peak_assignments": [...],
    "evidence_ids": {slot value: [ids]}, "skipped": [{name, tag}]}``, values in first-seen order.
    """
    slots: Dict[str, List[str]] = {"peak_positions": [], "d_spacing": [], "peak_assignments": []}
    evidence: Dict[str, List[str]] = {}
    skipped: List[Dict[str, str]] = []

    def add(slot: str, text: str, evidence_id: str) -> None:
        if not text:
            return
        if text not in slots[slot]:
            slots[slot].append(text)
        ids = evidence.setdefault(f"{slot}:{text}", [])
        if evidence_id not in ids:
            ids.append(evidence_id)

    for quantity, record, decision in accepted:
        base = base_quantity_name(quantity.name)
        if base not in ("peak_q", "peak_d_spacing"):
            continue
        if decision.tag is not EvidenceSource.REPORTED:
            skipped.append({"name": quantity.name, "tag": decision.tag.value})
            continue
        evidence_id = record.ref.evidence_id
        if base == "peak_q":
            add("peak_positions", f"q = {_printed(quantity.raw_value, quantity.raw_unit)}", evidence_id)
        else:
            add("d_spacing", _printed(quantity.raw_value, quantity.raw_unit), evidence_id)
        add("peak_assignments", _peak_label(quantity.name), evidence_id)
    return {**slots, "evidence_ids": evidence, "skipped": skipped, "target_class": "ExperimentalTechnique",
            "mapping_version": FIELD_MAPPING_VERSION, "status": "candidate"}


__all__ = [
    "FIELD_MAPPING_VERSION", "PEAK_ASSIGNMENT_SLOT", "QUANTITY_FIELD_MAP", "FieldRow", "expected_dimension",
    "field_row", "peak_slot_values",
]
