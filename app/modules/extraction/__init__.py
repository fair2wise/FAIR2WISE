"""Extraction bridge for the FORGE-to-F2W migration (task T06, pre-H3).

The existing F2W term extractor is the default strategy (``legacy_f2w``); FORGE
strategies are optional and opt-in. All strategies return one normalized
:class:`~app.modules.extraction.contract.ExtractionResult`, and a separate
validation stage (:mod:`~app.modules.extraction.record_validation`) attaches
evidence or withholds proposals.

:data:`H3_GATES` records what is *not* enabled before the owner approves binary
cases and field mappings at H3. Code in this package never flips them; any code
that would apply a mapping, merge canonical entities or publish must call
:func:`require_gate` first.

Importing this package has no side effects (no environment, network or model).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .contract import (
    EXTRACTION_CONTRACT_VERSION, ChunkCoverage, ExtractedMention, ExtractionResult, ExtractionSource,
    compare_results,
)
from .strategies import DEFAULT_STRATEGY, OPTIONAL_STRATEGIES, run_strategies, strategy_class

#: Pre-H3 gates. All closed; changing them is an owner decision, not configuration.
H3_GATES: Mapping[str, bool] = MappingProxyType({
    "apply_validation_mappings": False,
    "new_schema_slots": False,
    "canonical_merge": False,
    "publish": False,
})


def require_gate(name: str) -> None:
    """Raise ``PermissionError`` unless the named H3 gate is open (none are, pre-H3)."""
    if name not in H3_GATES:
        raise KeyError(f"unknown gate {name!r}")
    if not H3_GATES[name]:
        raise PermissionError(f"{name} is disabled until owner approval at H3")


__all__ = [
    "DEFAULT_STRATEGY", "EXTRACTION_CONTRACT_VERSION", "H3_GATES", "OPTIONAL_STRATEGIES", "ChunkCoverage",
    "ExtractedMention", "ExtractionResult", "ExtractionSource", "compare_results", "require_gate",
    "run_strategies", "strategy_class",
]
