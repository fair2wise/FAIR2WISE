"""Extraction bridge for the FORGE-to-F2W migration (tasks T06/T06B).

The existing F2W term extractor is the default strategy (``legacy_f2w``); the
FORGE strategies and the term_extractor agent path are optional and opt-in. All
strategies return one normalized :class:`~app.modules.extraction.contract.ExtractionResult`,
and a separate validation stage (:mod:`~app.modules.extraction.record_validation`)
attaches evidence and an evidence-source tag, or withholds proposals.

:data:`H3_GATES` records what the owner's H3 decision (docs/migration/reviews/H3/decision.json,
2026-10-16) enables. They are constants, not configuration; code in this package
never flips them, and any code that applies a mapping, uses new schema slots,
merges canonical entities or publishes calls :func:`require_gate` first.

- ``apply_validation_mappings``: open (F1/F2/F4/F8 applied in the validation stage).
- ``new_schema_slots``: open, but used only behind an explicit schema-set 0.3.0
  selection that also acknowledges its pending owner review (:mod:`.schema_selection`).
- ``canonical_merge``: closed until the owner-approved C4 real-model rerun.
- ``publish``: closed until H5.

Importing this package has no side effects (no environment, network or model).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .contract import (
    EXTRACTION_CONTRACT_VERSION,
    ChunkCoverage,
    ExtractedMention,
    ExtractionResult,
    ExtractionSource,
    compare_results,
)
from .strategies import DEFAULT_STRATEGY, OPTIONAL_STRATEGIES, run_strategies, strategy_class

#: H3 gates (owner decision 2026-10-16). Changing them is an owner decision, not configuration.
H3_GATES: Mapping[str, bool] = MappingProxyType({
    "apply_validation_mappings": True,
    "new_schema_slots": True,
    "canonical_merge": False,
    "publish": False,
})


_CLOSED_BECAUSE = {"canonical_merge": "C4 real-model rerun", "publish": "H5"}


def require_gate(name: str) -> None:
    """Raise ``PermissionError`` unless the named H3 gate is open."""
    if name not in H3_GATES:
        raise KeyError(f"unknown gate {name!r}")
    if not H3_GATES[name]:
        raise PermissionError(f"{name} is disabled pending owner approval ({_CLOSED_BECAUSE.get(name, 'H3')})")


__all__ = [
    "DEFAULT_STRATEGY", "EXTRACTION_CONTRACT_VERSION", "H3_GATES", "OPTIONAL_STRATEGIES", "ChunkCoverage",
    "ExtractedMention", "ExtractionResult", "ExtractionSource", "compare_results", "require_gate",
    "run_strategies", "strategy_class",
]
