"""Versioned section projection of a parsed document (L-15, T29 B5).

Sections are projected as ``parser_output`` statements (structure the parser found, not a
claim of the paper). The projection is content- and parser-addressed: a corrected parse is a
new ``projection_id``, history is never overwritten, and :func:`active_sections` selects the
newest *complete* projection. There is no store singleton and no write: persisting is an
approved graph change plan elsewhere.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/section_projection.py (``section_records``,
``persist_sections``, ``active_sections``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from ..contracts.evidence import SourceLocator
from .parsed_document import SECTION_TEXT_MAX_CHARS, extract_sections, without_embedded_images
from .statements import Basis, ProvenanceStatement

PROJECTION_VERSION = 2


def section_records(parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sections with a unique ``key`` (repeated headings get `` [2]``) and a structural id.

    ``parsed`` is a :meth:`ParsedDocument.to_record` dict; with no ``sections`` the markdown is split.
    """
    sections = parsed.get("sections") or [
        {"name": name, "text": text} for name, text in extract_sections(str(parsed.get("markdown", ""))).items()]
    records: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, section in enumerate(sections):
        name = str(section["name"])
        key, suffix = name, 2
        while key in used:
            key, suffix = f"{name} [{suffix}]", suffix + 1
        used.add(key)
        records.append({**section, "text": without_embedded_images(str(section["text"])), "key": key,
                        "structural_id": section.get("structural_id") or f"section-{index:05d}"})
    return records


def projection_id(parsed: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> str:
    """Digest of the source, parser identity and the records: any change makes a new projection."""
    blob = [parsed.get("source_ref"), parsed.get("parser"), parsed.get("parser_version"),
            parsed.get("parser_config", {}), list(records)]
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


def section_statements(parsed: Mapping[str, Any], *, work_id: str) -> list[ProvenanceStatement]:
    """One ``has_section`` statement per non-empty section, tagged with its projection."""
    records = section_records(parsed)
    pid = projection_id(parsed, records)
    non_empty = [r for r in records if r["text"].strip()]
    producer = f"sections@v{PROJECTION_VERSION}:{parsed.get('parser', '')}-{parsed.get('parser_version', '')}"
    out: list[ProvenanceStatement] = []
    for rec in non_empty:
        text = rec["text"]
        digest = hashlib.sha256(json.dumps([pid, rec["structural_id"], text]).encode()).hexdigest()[:24]
        page = rec.get("page")
        out.append(ProvenanceStatement(
            kind="section", subject=work_id, predicate="has_section", object=f"paper_section:{digest}",
            basis=Basis.PARSER_OUTPUT, producer=producer, source_ref=str(parsed.get("source_ref") or ""),
            locator=SourceLocator(section=str(rec["name"]), page=page if isinstance(page, int) and page >= 1 else None),
            attributes={"projection_id": pid, "projection_version": PROJECTION_VERSION,
                        "projection_section_count": len(non_empty), "structural_id": rec["structural_id"],
                        "section_name": rec["name"], "level": rec.get("level", 0),
                        "text": text[:SECTION_TEXT_MAX_CHARS], "full_text_length": len(text),
                        "text_truncated": len(text) > SECTION_TEXT_MAX_CHARS},
        ))
    return out


def active_sections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest complete projection's rows (flat attribute dicts); legacy rows only if none is complete."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("projection_id"):
            groups.setdefault(str(row["projection_id"]), []).append(row)
    complete = [g for g in groups.values() if len(g) == int(g[0].get("projection_section_count", len(g)))]
    if not complete:
        return [r for r in rows if not r.get("projection_id")]
    latest = max(complete, key=lambda g: max(str(r.get("projected_at", "")) for r in g))
    return sorted(latest, key=lambda r: str(r.get("structural_id", "")))


__all__ = ("PROJECTION_VERSION", "active_sections", "projection_id", "section_records", "section_statements")
