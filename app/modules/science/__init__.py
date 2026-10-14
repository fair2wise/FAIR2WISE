"""Deterministic paper-science helpers (T09): unit normalization and q <-> spacing."""

from .normalize import (
    NormalizedQuantity,
    ScienceError,
    science_normalize_quantity,
    science_q_to_spacing,
    science_spacing_to_q,
)

__all__ = (
    "NormalizedQuantity",
    "ScienceError",
    "science_normalize_quantity",
    "science_q_to_spacing",
    "science_spacing_to_q",
)
