"""Which entity carries a figure's class: the figure, or its panels (V-01).

A figure that has classified panels is not itself a classification unit: its panels
are (a composite's single class summarizes a collage and is usually wrong about part
of it). A figure with no classified panels stays its own unit. Figure-versus-panel
identity and reading order stay explicit. Pure functions; nothing is written.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/classification_units.py
(``Unit``, ``verdict_classes``, ``panel_parents``, ``resolve_units``, ``panel_order_map``)
(BSD-3-Clause). The donor's knowledge-graph client queries are replaced by plain mappings.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Set, Tuple


class Unit(NamedTuple):
    """One classification unit: the thing a verdict is actually about."""

    subject_id: str
    subject_type: str            # "figure_panel" | "paper_figure"
    parent_figure_id: str
    verdict: Dict[str, Any]

    @property
    def is_panel(self) -> bool:
        """True when this unit is a panel."""
        return self.subject_type == "figure_panel"

    @property
    def figure_id(self) -> str:
        """The figure this unit belongs to, whichever kind it is."""
        return self.parent_figure_id or self.subject_id


def verdict_classes(verdict: Mapping[str, Any]) -> Set[str]:
    """Every class a verdict asserts: the primary plus any ``also``."""
    classes = {str(verdict.get("class") or "")}
    classes.update(str(x) for x in (verdict.get("also") or []))
    classes.discard("")
    return classes


def panel_parents(pairs: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    """``{panel id: parent figure id}`` from ``(panel_id, figure_id)`` pairs (rewritten: no graph client)."""
    return {panel: figure for panel, figure in pairs if panel and figure}


def resolve_units(
    verdicts: Mapping[str, Mapping[str, Any]],
    parents: Mapping[str, str],
    *,
    include_superseded: bool = False,
    panel_order: Optional[Mapping[str, int]] = None,
) -> List[Unit]:
    """Reduce a subject->verdict map to the units a consumer should act on (stable order)."""
    superseded: Set[str] = set()
    if not include_superseded:
        superseded = {parents[p] for p in verdicts if parents.get(p)}
    units = [
        Unit(sid, "figure_panel" if parents.get(sid) else "paper_figure", parents.get(sid, ""), dict(v))
        for sid, v in verdicts.items() if sid not in superseded
    ]
    order = panel_order or {}
    units.sort(key=lambda u: (u.figure_id, order.get(u.subject_id, 0), u.subject_id))
    return units


def panel_order_map(panels: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """``{panel id: index}`` read from panel records (not from verdicts); bad index -> 0."""
    order: Dict[str, int] = {}
    for panel in panels:
        pid = str(panel.get("id") or panel.get("panel_id") or "")
        if not pid:
            continue
        try:
            order[pid] = int(str(panel.get("index")))
        except (TypeError, ValueError):
            order[pid] = 0
    return order
