"""Synthetic fixture for the H3 measurement harness: known true merges and named negative controls.

Everything here is invented text for measurement; it is not scientific data and not a
vocabulary proposal. ``kind`` values: ``true_merge`` (must resolve to ``expected``),
``negative_control`` (must NEVER merge; ``expected`` is ``None``) and ``novel`` (a genuinely
new term; ``expected`` is ``None``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .candidate_search import IndexedEntity
from .vocabulary import VocabEntry, Vocabulary

#: The controls the plan names. Each must never merge, by any strategy's gate, with any judge.
NAMED_NEGATIVE_CONTROLS: Tuple[Tuple[str, str], ...] = (
    ("Princeton Instruments PI-MTE", "CCD area detector"),  # specific instrument vs generic class
    ("MoTe2", "WTe2"),  # distinct chemicals
    ("Al2O3[0001]", "Al2O3"),  # specific surface vs generic material
    ("SEM", "TEM"),  # distinct techniques
    ("STEM", "TEM"),
    ("USAXS", "SAXS"),
    ("GIWAXS", "GISAXS"),
    ("PC71BM", "PC61BM"),
    ("gamma-Fe2O3", "alpha-Fe2O3"),  # polymorphs
)


@dataclass(frozen=True)
class FixtureMention:
    surface: str
    category: str
    kind: str
    expected: Optional[str]


def _distractors() -> List[IndexedEntity]:
    out: List[IndexedEntity] = []
    for i in range(1, 41):
        out.append(IndexedEntity(f"d:scat{i}", f"wide angle scattering study {i}", "technique"))
        out.append(IndexedEntity(f"d:film{i}", f"thin film sample batch {i}", "material"))
        out.append(IndexedEntity(f"d:inst{i}", f"x-ray detector assembly {i}", "instrument"))
    return out


def entities() -> List[IndexedEntity]:
    """Existing entities: the labelled ones below plus 120 distractors that crowd any top-k."""
    base = [
        IndexedEntity("t:giwaxs", "grazing incidence wide angle x-ray scattering", "technique"),
        IndexedEntity("t:saxs", "SAXS", "technique"),
        IndexedEntity("t:rsoxs", "resonant soft x-ray scattering", "technique"),
        IndexedEntity("t:tem", "transmission electron microscopy", "technique"),
        IndexedEntity("t:xrd", "x-ray diffraction", "technique"),
        IndexedEntity("m:mote2", "MoTe2", "material"),
        IndexedEntity("m:al2o3", "Al2O3", "material"),
        IndexedEntity("m:pc61bm", "PC61BM", "material"),
        IndexedEntity("m:alpha_fe2o3", "alpha-Fe2O3", "material"),
        IndexedEntity("m:p3ht", "poly(3-hexylthiophene)", "material"),
        IndexedEntity("t:soxhlet", "Soxhlet extraction", "technique"),
        IndexedEntity("i:ccd", "CCD area detector", "instrument"),
        IndexedEntity("i:pilatus", "PILATUS 2M detector", "instrument"),
    ]
    return base + _distractors()


MENTIONS: Tuple[FixtureMention, ...] = (
    # true merges: case, punctuation, acronym <-> expansion, typo, word order
    FixtureMention("GIWAXS", "technique", "true_merge", "t:giwaxs"),
    FixtureMention("GI-WAXS", "technique", "true_merge", "t:giwaxs"),
    FixtureMention("small angle x-ray scattering", "technique", "true_merge", "t:saxs"),
    FixtureMention("RSoXS", "technique", "true_merge", "t:rsoxs"),
    FixtureMention("TEM", "technique", "true_merge", "t:tem"),
    FixtureMention("XRD", "technique", "true_merge", "t:xrd"),
    FixtureMention("X-ray diffraction", "technique", "true_merge", "t:xrd"),
    FixtureMention("Sohxlet extraction", "technique", "true_merge", "t:soxhlet"),
    FixtureMention("P3HT", "material", "true_merge", "m:p3ht"),
    FixtureMention("mote2", "material", "true_merge", "m:mote2"),
    FixtureMention("Pilatus 2M", "instrument", "true_merge", "i:pilatus"),
    FixtureMention("charge coupled device area detector", "instrument", "true_merge", "i:ccd"),
    # approved-vocabulary alias that no spelling rule can find
    FixtureMention("wide-angle scattering at grazing incidence", "technique", "true_merge", "t:giwaxs"),
    # named negative controls: the mention must stay separate from the entity of the pair's second item
    FixtureMention("Princeton Instruments PI-MTE", "instrument", "negative_control", None),
    FixtureMention("WTe2", "material", "negative_control", None),
    FixtureMention("Al2O3[0001]", "material", "negative_control", None),
    FixtureMention("SEM", "technique", "negative_control", None),
    FixtureMention("STEM", "technique", "negative_control", None),
    FixtureMention("USAXS", "technique", "negative_control", None),
    FixtureMention("GISAXS", "technique", "negative_control", None),
    FixtureMention("PC71BM", "material", "negative_control", None),
    FixtureMention("gamma-Fe2O3", "material", "negative_control", None),
    # genuinely new
    FixtureMention("atomic force microscopy", "technique", "novel", None),
    FixtureMention("spin coating", "technique", "novel", None),
)


def approved_alias_vocabulary() -> Vocabulary:
    """A tiny CANDIDATE vocabulary whose revision a test or the harness may approve explicitly."""
    return Vocabulary(
        1,
        [
            VocabEntry(
                "t:giwaxs",
                "grazing incidence wide angle x-ray scattering",
                "technique",
                aliases=("wide-angle scattering at grazing incidence",),
            )
        ],
        source="synthetic-fixture",
    )
