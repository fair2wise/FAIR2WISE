"""Extraction strategies behind one normalized result contract.

``legacy_f2w`` (the existing F2W extractor) is the default. The FORGE strategies and
the ``term_extractor`` agent path (H3 F9, for comparison) are optional: selecting
one requires ``allow_optional=True`` from the caller, so no configuration default
can switch extraction away from the current behaviour.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Protocol, Type

from ..contract import ExtractionResult, ExtractionSource
from .forge_paper import ForgePaperStrategy
from .legacy_f2w import LegacyF2WStrategy
from .paper_structure import PaperStructureStrategy
from .term_extractor_agent import TermExtractorAgentStrategy

DEFAULT_STRATEGY = LegacyF2WStrategy.name
OPTIONAL_STRATEGIES = (ForgePaperStrategy.name, PaperStructureStrategy.name, TermExtractorAgentStrategy.name)

_REGISTRY: Dict[str, Type[Any]] = {
    LegacyF2WStrategy.name: LegacyF2WStrategy,
    ForgePaperStrategy.name: ForgePaperStrategy,
    PaperStructureStrategy.name: PaperStructureStrategy,
    TermExtractorAgentStrategy.name: TermExtractorAgentStrategy,
}


class ExtractionStrategy(Protocol):
    """A strategy: ``name``, ``version`` and ``extract(source) -> ExtractionResult``."""

    name: str
    version: str

    def extract(self, source: ExtractionSource) -> ExtractionResult:
        """Extract from one source version."""


def strategy_class(name: str = DEFAULT_STRATEGY, *, allow_optional: bool = False) -> Type[Any]:
    """Return the strategy class for ``name``; optional strategies need explicit opt-in.

    Raises:
        KeyError: Unknown strategy.
        PermissionError: An optional strategy was requested without ``allow_optional``.
    """
    if name not in _REGISTRY:
        raise KeyError(f"unknown extraction strategy {name!r}; known: {sorted(_REGISTRY)}")
    if name in OPTIONAL_STRATEGIES and not allow_optional:
        raise PermissionError(f"strategy {name!r} is optional and off by default; pass allow_optional=True")
    return _REGISTRY[name]


def run_strategies(strategies: Iterable[ExtractionStrategy], source: ExtractionSource) -> List[ExtractionResult]:
    """Run several strategies on the identical source, for side-by-side comparison."""
    return [strategy.extract(source) for strategy in strategies]


__all__ = [
    "DEFAULT_STRATEGY", "OPTIONAL_STRATEGIES", "ExtractionStrategy", "ForgePaperStrategy", "LegacyF2WStrategy",
    "PaperStructureStrategy", "TermExtractorAgentStrategy", "run_strategies", "strategy_class",
]
