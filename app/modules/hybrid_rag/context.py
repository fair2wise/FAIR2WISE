"""ContextBuilder: intent-weighted prompt packing with distinct citations."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from .evidence import Evidence

DEFAULT_SOURCE_BUDGET = 6000


def _pdf_cite(item: Evidence) -> str:
    name = Path(str(item.paper_id or item.provenance.get("path") or "paper.pdf")).name
    if item.page:
        return f"[PDF: {name} p.{item.page}]"
    return f"[PDF: {name}]"


def _ops_cite(item: Evidence) -> str:
    path = str(item.provenance.get("path") or item.paper_id or item.locator or "ops-doc")
    short = Path(path).name
    heading = item.heading.strip()
    locator = item.locator or short
    if heading:
        return f"[OPS: {short} §{heading}]"
    return f"[OPS: {locator}]"


def _kg_cite(item: Evidence) -> str:
    gid = item.graph_id or item.provenance.get("graph_id") or ""
    name = item.name or item.source_id
    if gid:
        return f"[KG:{gid}: {name}]"
    return f"[KG: {name}]"


class ContextBuilder:
    """Pack ranked evidence. Literature and ops stay in labeled sections."""

    def build(
        self,
        items: Sequence[Evidence],
        *,
        char_budget: int = DEFAULT_SOURCE_BUDGET,
        intent: str = "factual",
    ) -> str:
        kg_items = [i for i in items if i.source_kind == "kg"]
        lit_items = [
            i for i in items if i.source_kind == "paper_chunk" and i.source_type == "literature"
        ]
        ops_items = [
            i for i in items if i.source_kind == "paper_chunk" and i.source_type == "ops"
        ]
        tiled_items = [i for i in items if i.source_kind == "tiled_run"]

        # Intent share of the source-RAG budget (KG nodes are rendered separately).
        if intent == "layout":
            lit_budget = int(char_budget * 0.15)
            ops_budget = int(char_budget * 0.85)
        elif intent == "why_how":
            lit_budget = int(char_budget * 0.7)
            ops_budget = int(char_budget * 0.3)
        else:
            lit_budget = int(char_budget * 0.55)
            ops_budget = int(char_budget * 0.45)

        parts: List[str] = []
        remaining = char_budget
        if kg_items:
            block = self._kg_index(kg_items)
            if remaining > 0:
                parts.append(block[:remaining])
                remaining -= min(len(block), remaining)
        lit_block = self._section(
            "Literature source evidence",
            "Cite passages as [PDF: filename p.N]. These are journal/preprint chunks, not beamline docs.",
            lit_items,
            cite=_pdf_cite,
            budget=min(lit_budget, remaining),
        )
        if lit_block:
            parts.append(lit_block)
            remaining -= len(lit_block)
        ops_block = self._section(
            "Beamline ops-doc evidence (bl1101)",
            "Cite passages as [OPS: file §heading]. These are 11.0.1.2 / ALS ops docs, not papers.",
            ops_items,
            cite=_ops_cite,
            budget=min(ops_budget, max(0, remaining)),
        )
        if ops_block:
            parts.append(ops_block)
            remaining -= len(ops_block)
        if tiled_items and remaining > 0:
            tiled_block = self._section(
                "Tiled run metadata",
                "Cite as [TILED: run]. Authenticated scan metadata — not mixed with public literature.",
                tiled_items,
                cite=lambda ev: f"[TILED: {ev.source_id}]",
                budget=min(800, remaining),
            )
            if tiled_block:
                parts.append(tiled_block)
        return "\n\n".join(p for p in parts if p.strip())

    def _kg_index(self, items: Sequence[Evidence]) -> str:
        lines = [
            "### Knowledge-graph candidates",
            "Structured KG hits (not PDF text). Cite as [KG:graph_id: name].",
        ]
        for item in items[:12]:
            lines.append(f"- {_kg_cite(item)} ({item.category or 'node'}) score={item.score:.3f}")
        return "\n".join(lines)

    def _section(
        self,
        title: str,
        hint: str,
        items: Sequence[Evidence],
        *,
        cite,
        budget: int,
    ) -> str:
        if not items or budget <= 0:
            return ""
        lines = [f"### {title}", hint]
        used = sum(len(line) + 1 for line in lines)
        for item in items:
            tag = cite(item)
            body = (item.text or "").strip()
            if not body:
                continue
            block = f"{tag}\n{body}"
            if used + len(block) + 2 > budget:
                remain = budget - used - len(tag) - 2
                if remain < 80:
                    break
                block = f"{tag}\n{body[:remain]}"
                lines.append(block)
                break
            lines.append(block)
            used += len(block) + 2
        if len(lines) <= 2:
            return ""
        return "\n".join(lines)
