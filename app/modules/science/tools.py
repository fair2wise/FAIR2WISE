"""Science tools (pure, deterministic): unit normalization and q <-> spacing conversion with provenance."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid

from ..contracts.errors import ErrorCode
from ..contracts.tools import CostClass, ToolResult, ToolSpec, ToolStatus
from .normalize import (
    ScienceError,
    science_normalize_quantity,
    science_q_to_spacing,
    science_spacing_to_q,
)

TOOL_VERSION = "1.0.0"
_VALUE = {"type": "number", "exclusiveMinimum": 0}
_UNIT = {"type": "string", "minLength": 1, "maxLength": 20}


def _spec(name: str, description: str, when: str, props: dict[str, Any], required: tuple[str, ...]) -> ToolSpec:
    return ToolSpec(
        name=name, version=TOOL_VERSION, domain="science", description=description, when_to_use=when,
        when_not_to_use="Values or units that a source does not state; this converts, it never infers a unit.",
        input_schema={"type": "object", "properties": props, "required": list(required),
                      "additionalProperties": False},
        output_schema={"type": "object"}, cost_class=CostClass.FREE, required_capabilities=frozenset({"chat.ask"}),
        evidence_contract="none",
    )


SCIENCE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    _spec("science_normalize_quantity",
          "Normalize a length to nm or an inverse length to nm^-1 (Å^-1, nm^-1, µm, ...) with provenance.",
          "A paper value needs a canonical unit before comparison.",
          {"value": _VALUE, "unit": _UNIT, "kind": {"enum": ["length", "inverse_length"]}}, ("value", "unit")),
    _spec("science_q_to_spacing",
          "Real-space spacing d = 2*pi/q from q in nm^-1 or A^-1.",
          "Converting a scattering q position into a d-spacing.",
          {"q": _VALUE, "unit": _UNIT}, ("q", "unit")),
    _spec("science_spacing_to_q",
          "q = 2*pi/d from a spacing in nm, um or A.",
          "Converting a d-spacing into q.", {"d": _VALUE, "unit": _UNIT}, ("d", "unit")),
)


def _wrap(tool: str, fn: Callable[[Any], dict[str, Any]]) -> Callable[[Any], ToolResult]:
    def run(inv: Any) -> ToolResult:
        provenance = f"science:{uuid.uuid4().hex}"
        try:
            data = fn(inv.args)
        except ScienceError as exc:
            return ToolResult(tool=tool, status=ToolStatus.INVALID, error_code=ErrorCode.INVALID_INPUT,
                              provenance_id=provenance, data={"reason": exc.reason}, warnings=(str(exc),))
        return ToolResult(tool=tool, status=ToolStatus.OK, data=data, provenance_id=provenance)
    return run


def science_registrations() -> tuple[tuple[ToolSpec, Callable[[Any], ToolResult]], ...]:
    """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
    fns = (
        lambda a: science_normalize_quantity(a["value"], a["unit"], a.get("kind")).as_dict(),
        lambda a: science_q_to_spacing(a["q"], a["unit"]),
        lambda a: science_spacing_to_q(a["d"], a["unit"]),
    )
    return tuple((spec, _wrap(spec.name, fn)) for spec, fn in zip(SCIENCE_TOOL_SPECS, fns))


__all__ = ("SCIENCE_TOOL_SPECS", "TOOL_VERSION", "science_registrations")
