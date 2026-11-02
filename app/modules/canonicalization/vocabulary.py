"""Controlled vocabulary definitions (C-01) with candidate / approved status.

Four things stay distinct (plan 7, T07):

* **definitions**: :class:`VocabEntry` rows inside a :class:`Vocabulary` (this module);
* **observations**: what papers said (:mod:`.growth`, ``Observation``);
* **proposals**: reviewable promotions or alias additions (:mod:`.growth`, ``VocabularyProposal``);
* **approved versions**: a :class:`Vocabulary` whose ``status`` is ``"approved"``, produced only
  from an explicit :class:`VocabularyApproval` for the exact content revision.

The donor ``vocabulary.yaml`` is loaded by :func:`load_vocabulary_asset` as a *candidate*
governed asset. A candidate never authorizes an accepted mapping; it is reviewed (see
:mod:`.audit`) and approved at H3. Stable F2W IDs are never regenerated from labels:
``slug`` is read from the file, ``entity_slug`` only normalizes lookup keys.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary.py (BSD-3-Clause).
Rewritten: slug function (no donor ``forge.kg.paths`` dependency), candidate/approved status,
content revision. Dropped: effective-vocabulary SQLite overlay (replaced by :mod:`.growth`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

CATEGORIES = ("technique", "analysis", "instrument", "material")

#: Shortest alias allowed to match INSIDE a longer surface (descriptive phrases only).
_CONTAINMENT_MIN_CHARS = 12
#: Containment is not evidence for compound names (silicon nitride is not silicon).
_NO_CONTAINMENT_CATEGORIES = frozenset({"material"})


def entity_slug(text: str) -> str:
    """Normalize a lookup key: NFKD, lowercase ASCII words joined by underscores; ``""`` if empty."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")


@dataclass(frozen=True)
class VocabEntry:
    """One canonical term definition."""

    slug: str
    display_name: str
    category: str
    aliases: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    notes: str = ""
    smiles: str = ""

    def compiled_patterns(self) -> list[re.Pattern[str]]:
        """Compile this entry's regex patterns, case-insensitively."""
        return [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def as_dict(self) -> dict[str, Any]:
        """Return the entry as a plain, JSON-serializable dict (tuples become lists)."""
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "category": self.category,
            "aliases": list(self.aliases),
            "patterns": list(self.patterns),
            "notes": self.notes,
            "smiles": self.smiles,
        }


@dataclass(frozen=True)
class VocabularyApproval:
    """Owner approval of one vocabulary content revision. Never created by a default flow."""

    approval_id: str
    vocabulary_revision: str
    approved_by: str
    approved_at: datetime


def _separator_insensitive(alias: str) -> str:
    """A word-boundary regex where any run of spaces/hyphens matches any other (words stay literal)."""
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", alias.strip()) if p]
    return r"\b" + r"[\s\-]+".join(parts) + r"\b"


class Vocabulary:
    """Loaded definitions: lookup by slug or alias.

    Attributes:
        status: ``"candidate"`` (default, non-authoritative) or ``"approved"``.
        revision: SHA-256 of the canonical entry content; changes whenever an entry changes.
        source: Where the content came from (path and file hash for loaded assets).
    """

    def __init__(
        self,
        version: int,
        entries: Iterable[VocabEntry],
        *,
        status: str = "candidate",
        source: str = "",
        approval_id: str | None = None,
    ) -> None:
        if status not in ("candidate", "approved"):
            raise ValueError(f"unknown vocabulary status {status!r}")
        self.version = version
        self.entries: list[VocabEntry] = list(entries)
        self.status = status
        self.source = source
        self.approval_id = approval_id
        self._by_slug: dict[str, VocabEntry] = {}
        self._by_alias: dict[str, VocabEntry] = {}
        self._by_structure: dict[str, VocabEntry] | None = None
        for entry in self.entries:
            if entry.category not in CATEGORIES:
                raise ValueError(f"vocabulary entry {entry.slug!r}: unknown category {entry.category!r}")
            if entry.slug in self._by_slug:
                raise ValueError(f"duplicate vocabulary slug: {entry.slug!r}")
            self._by_slug[entry.slug] = entry
        # A collision between two entries is a data error to resolve, never a tie to break silently.
        for entry in self.entries:
            for alias in (*entry.aliases, entry.display_name, entry.slug):
                key = entity_slug(alias)
                if not key:
                    continue
                other = self._by_alias.get(key)
                if other is not None and other.slug != entry.slug:
                    raise ValueError(f"alias {alias!r} claimed by both {other.slug!r} and {entry.slug!r}")
                self._by_alias[key] = entry
        self.revision = hashlib.sha256(
            json.dumps(
                {
                    "version": version,
                    "entries": sorted((e.as_dict() for e in self.entries), key=lambda d: d["slug"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def approve(self, approval: VocabularyApproval) -> Vocabulary:
        """Return an approved copy; the approval must name exactly this content revision."""
        if approval.vocabulary_revision != self.revision:
            raise ValueError("approval is for a different vocabulary revision")
        return Vocabulary(
            self.version, self.entries, status="approved", source=self.source, approval_id=approval.approval_id
        )

    @property
    def approved(self) -> bool:
        """Whether the vocabulary has been approved."""
        return self.status == "approved"

    def get(self, slug: str) -> VocabEntry | None:
        """Return the entry with exactly this slug, or ``None``."""
        return self._by_slug.get(slug)

    def match(self, term: str, category: str | None = None) -> VocabEntry | None:
        """The entry *term* denotes by exact slug/alias equality after normalization, or ``None``.

        No fuzzy matching: a wrong merge in a provenance graph is effectively unrecoverable.
        """
        if (term or "").lower().startswith("smiles:"):
            return self.match_smiles(term.split(":", 1)[1]) if category in (None, "material") else None
        key = entity_slug(term)
        entry = self._by_alias.get(key) if key else None
        if entry is None or (category is not None and entry.category != category):
            return None
        return entry

    def match_smiles(self, smiles: str) -> VocabEntry | None:
        """Resolve a typed SMILES to its named entity by canonical structure (needs RDKit)."""
        from .chemical_identity import canonical_smiles

        normalized = canonical_smiles(smiles)
        if normalized is None:
            return None
        if self._by_structure is None:
            index: dict[str, VocabEntry] = {}
            for entry in self.entries:
                key = canonical_smiles(entry.smiles) if entry.category == "material" and entry.smiles else None
                if key:
                    if key in index and index[key].slug != entry.slug:
                        raise ValueError(f"duplicate chemical structure: {entry.slug!r}, {index[key].slug!r}")
                    index[key] = entry
            self._by_structure = index
        return self._by_structure.get(normalized)

    def match_within(self, term: str, category: str | None = None) -> VocabEntry | None:
        """The single entry whose own pattern or long alias fires inside *term*; ambiguous -> ``None``.

        Materials are never matched by containment. The longest span wins; an exact tie is refused.
        """
        text = (term or "").strip()
        if not text:
            return None
        hits: dict[str, tuple[int, VocabEntry]] = {}
        for entry in self.entries:
            if (
                category is not None and entry.category != category
            ) or entry.category in _NO_CONTAINMENT_CATEGORIES:
                continue
            spans = [m.end() - m.start() for p in entry.compiled_patterns() if (m := p.search(text))]
            spans += [
                m.end() - m.start()
                for alias in entry.aliases
                if len(alias) >= _CONTAINMENT_MIN_CHARS
                and (m := re.search(_separator_insensitive(alias), text, re.IGNORECASE))
            ]
            if spans:
                hits[entry.slug] = (max(spans), entry)
        if not hits:
            return None
        ranked = sorted(hits.values(), key=lambda pair: -pair[0])
        if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
            return ranked[0][1]
        return None

    def entries_for(self, category: str) -> list[VocabEntry]:
        """Return the entries belonging to `category`."""
        return [e for e in self.entries if e.category == category]


def _entry_from_raw(raw: dict[str, Any]) -> VocabEntry:
    return VocabEntry(
        slug=str(raw["slug"]),
        display_name=str(raw.get("display_name") or raw["slug"]),
        category=str(raw["category"]),
        aliases=tuple(str(a) for a in raw.get("aliases") or ()),
        patterns=tuple(str(p) for p in raw.get("patterns") or ()),
        notes=str(raw.get("notes") or ""),
        smiles=str(raw.get("smiles") or ""),
    )


def vocabulary_from_mapping(raw: dict[str, Any], *, source: str = "") -> Vocabulary:
    """Build a *candidate* vocabulary from a parsed ``{version, entries}`` mapping."""
    if not isinstance(raw, dict) or "entries" not in raw:
        raise ValueError("not a vocabulary mapping (no 'entries' key)")
    return Vocabulary(int(raw.get("version", 1)), [_entry_from_raw(e) for e in raw["entries"]], source=source)


def load_vocabulary_asset(path: Path) -> Vocabulary:
    """Import a donor ``vocabulary.yaml`` as a candidate governed asset (never approved, never active)."""
    import yaml

    data = Path(path).read_bytes()
    vocab = vocabulary_from_mapping(
        yaml.safe_load(data.decode("utf-8")), source=f"{Path(path).name}@sha256:{hashlib.sha256(data).hexdigest()}"
    )
    return vocab
