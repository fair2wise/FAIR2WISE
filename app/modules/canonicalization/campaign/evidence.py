"""Campaign evidence assembly (C-08): decoupled graph reads, retained contexts, evidence-bound menus.

The donor read a KG store directly. Here rows arrive through a :class:`RowSource` and a partial
read raises :class:`IncompleteRead` (a partial scan must block planning, not shrink it). Every
term keeps its variants, node ids and one snippet per distinct publication.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_evidence.py (BSD-3-Clause).
Rewritten: row model, menu bounding, no store/catalog readers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any, Mapping, Protocol

from ..merge_safety import fold, is_acronym_of
from ..migration import sha256_json
from ..vocabulary import Vocabulary
from .models import MenuEntry, Snippet, TermEvidence, term_key

SNIPPET_WIDTH = 140
MAX_SNIPPET_CHARS = 280
MAX_MENU_ENTRIES = 40


class IncompleteRead(RuntimeError):
    """The row source could not read everything; planning on partial data is refused."""


@dataclass
class GraphRows:
    """Candidate-term node rows. Each row: node_id, surface, category, publication_id, section_id, text."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: dict[str, int] = field(default_factory=dict)
    complete: bool = True

    def revision(self) -> str:
        """Hash of the rows: the plan's source revision. Changes whenever any row changes."""
        return sha256_json(
            {"nodes": sorted(self.nodes, key=lambda n: str(n.get("node_id"))), "edges": sorted(self.edges.items())}
        )


class RowSource(Protocol):
    """Anything that can supply the graph rows."""

    def read(self) -> GraphRows:
        """Return the rows; ``complete`` is False when the read was only partial."""
        ...


def load_rows(source: RowSource) -> GraphRows:
    """Read all rows or raise :class:`IncompleteRead`."""
    rows = source.read()
    if not rows.complete:
        raise IncompleteRead("row source reported an incomplete read")
    return rows


def snippet_around(text: str, surface: str, width: int = SNIPPET_WIDTH) -> str:
    """A window of ``text`` around the first casefold occurrence of ``surface`` (whole text start if absent)."""
    flat = " ".join((text or "").split())
    at = flat.casefold().find(surface.casefold())
    if at < 0:
        return flat[:MAX_SNIPPET_CHARS]
    start = max(0, at - width)
    stop = at + len(surface) + width
    return flat[start:stop][:MAX_SNIPPET_CHARS]


def fold_key(surface: str) -> str:
    """Spelling-variant key: casefold, drop non-alphanumerics, strip a simple plural 's'."""
    key = re.sub(r"[^a-z0-9]+", "", fold(surface).replace(" ", ""))
    return key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key


def build_menu(
    vocab: Vocabulary, category: str, surface: str, limit: int = MAX_MENU_ENTRIES
) -> tuple[MenuEntry, ...]:
    """Same-category vocabulary entries, bounded to ``limit`` and ranked by closeness to ``surface``."""
    entries = vocab.entries_for(category) if category else []
    key = fold(surface)

    def rank(entry: Any) -> float:
        names = (entry.display_name, *entry.aliases)
        best = max(SequenceMatcher(None, key, fold(n)).ratio() for n in names)
        if any(is_acronym_of(surface, n) or is_acronym_of(n, surface) for n in names):
            best = max(best, 0.95)
        return best

    ranked = sorted(entries, key=lambda e: (-rank(e), e.slug))[:limit]
    return tuple(MenuEntry(e.slug, e.display_name, e.category, tuple(e.aliases)) for e in ranked)


def fold_terms(rows: GraphRows) -> dict[str, dict[str, Any]]:
    """Group node rows by (category, fold_key). ``variants`` is a Counter of raw surfaces."""
    terms: dict[str, dict[str, Any]] = {}
    for node in rows.nodes:
        category = str(node.get("category") or "unknown")
        folded = fold_key(str(node.get("surface") or ""))
        if not folded:
            continue
        slot = terms.setdefault(
            term_key(category, folded),
            {"category": category, "folded": folded, "variants": Counter(), "nodes": []},
        )
        slot["variants"][str(node["surface"]).strip()] += 1
        slot["nodes"].append(node)
    return terms


def assemble(rows: GraphRows, key: str, slot: Mapping[str, Any], vocab: Vocabulary, *, k: int = 5) -> TermEvidence:
    """Build the :class:`TermEvidence` for one folded term."""
    variants = slot["variants"]
    surface = sorted(variants.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    nodes = slot["nodes"]
    seen: set[str] = set()
    snippets: list[Snippet] = []
    for node in sorted(nodes, key=lambda n: (str(n.get("publication_id")), str(n.get("node_id")))):
        pub = str(node.get("publication_id") or "")
        if pub in seen or len(snippets) >= k or not node.get("text"):
            continue
        seen.add(pub)
        snippets.append(
            Snippet(
                pub, str(node.get("section_id") or ""), snippet_around(str(node["text"]), str(node["surface"]))
            )
        )
    pubs = {str(n.get("publication_id") or "") for n in nodes}
    return TermEvidence(
        key,
        slot["category"],
        surface,
        slot["folded"],
        tuple(sorted(variants)),
        tuple(sorted(str(n["node_id"]) for n in nodes)),
        tuple(snippets),
        len(pubs),
        len({(str(n.get("publication_id")), str(n.get("section_id"))) for n in nodes}),
        sum(rows.edges.get(str(n["node_id"]), 0) for n in nodes),
        build_menu(vocab, slot["category"], surface),
    )
