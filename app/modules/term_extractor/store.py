import datetime
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import provenance
from .models import PropertyRecord, TermRecord

logger = logging.getLogger(__name__)


class TermStore:
    """Thread-safe in-memory store for TermRecord objects with JSON persistence.

    Two locks are used deliberately:
    - ``_state_lock``: guards ``_records`` and ``_display_to_key`` for all reads/writes.
    - ``_save_lock``: serialises file I/O so concurrent saves don't interleave writes.

    A thread-local ``_tl.updated`` flag lets the orchestrator detect whether the
    graph invocation running in *this* thread produced any mutations, without
    interfering with sibling threads processing other pages concurrently.
    """

    def __init__(self, output_file: str):
        """Initialise the store and load any pre-existing terms from *output_file*.

        Args:
            output_file: Path to the JSON file used for persistence. The parent
                directory is created automatically if it does not exist.
        """
        self.output_file = output_file
        self._records: Dict[str, TermRecord] = {}
        self._display_to_key: Dict[str, str] = {}
        self.code_snippets: List[Dict[str, Any]] = []
        self._snippet_seen: set = set()
        self._state_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._tl = threading.local()
        self.metadata: Dict[str, Any] = {
            "extraction_date": datetime.datetime.utcnow().isoformat() + "Z",
            "processed_files": 0,
            "processed_pages_total": 0,
            "processed_pages_with_terms": 0,
            "version": "2.1",
        }
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Populate ``_records`` from the JSON file on disk. No-op if the file is absent."""
        if not os.path.exists(self.output_file):
            return
        try:
            with open(self.output_file) as fh:
                data = json.load(fh)
            for raw in data.get("terms", []):
                record = TermRecord.from_dict(raw)
                key = self.normalize(record.term)
                self._records[key] = record
                self._display_to_key[record.term] = key
            for snip in data.get("code_snippets", []):
                self.code_snippets.append(snip)
                self._snippet_seen.add(provenance.snippet_key(snip))
            self.metadata.update(data.get("metadata", {}))
            logger.info(
                "Loaded %d existing terms and %d code snippets from %s",
                len(self._records), len(self.code_snippets), self.output_file,
            )
        except Exception as e:
            logger.warning("Could not load previous terms from %s: %s", self.output_file, e)

    def save(self) -> None:
        """Write current state to disk. Thread-safe; takes a snapshot under the state lock."""
        with self._state_lock:
            self._sync_publications_locked()
            terms_out = [r.to_dict() for r in self._records.values()]
            metadata_snapshot = dict(self.metadata)
            snippets_out = list(self.code_snippets)
        with self._save_lock:
            try:
                out = {"metadata": metadata_snapshot, "terms": terms_out, "code_snippets": snippets_out}
                with open(self.output_file, "w") as fh:
                    json.dump(out, fh, indent=2)
                logger.debug("Saved %d terms to %s", len(self._records), self.output_file)
            except Exception as e:
                logger.error("Failed to save terms: %s", e)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(term: str) -> str:
        """Return the canonical lookup key for *term* (stripped, lowercased)."""
        return term.strip().lower()

    def __len__(self) -> int:
        """Return the number of unique terms currently held in the store."""
        with self._state_lock:
            return len(self._records)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[TermRecord]:
        """Return the ``TermRecord`` for the normalised *key*, or ``None`` if absent."""
        with self._state_lock:
            return self._records.get(key)

    def all_records(self) -> List[TermRecord]:
        """Return a snapshot list of all ``TermRecord`` objects in the store."""
        with self._state_lock:
            return list(self._records.values())

    def all_display_names(self) -> List[str]:
        """Return a snapshot list of original (non-normalised) term strings."""
        with self._state_lock:
            return list(self._display_to_key.keys())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, record: TermRecord) -> Tuple[str, bool]:
        """Insert or merge a TermRecord. Returns (key, was_modified).

        Merge rules:
        - Pages and source papers are appended (deduped).
        - Context snippets are deduped by (source_paper, page).
        - Longer definition wins.
        - New relations are appended (deduped by (relation, related_term)).
        - Chemical fields (formula, smiles, etc.) fill in only if currently None.
        """
        key = self.normalize(record.term)
        with self._state_lock:
            if key not in self._records:
                self._records[key] = record
                self._display_to_key[record.term] = key
                self._tl.updated = True
                return key, True

            existing = self._records[key]
            modified = False

            for page in record.pages:
                if page not in existing.pages:
                    existing.pages.append(page)
                    modified = True
            for paper in record.source_papers:
                if paper not in existing.source_papers:
                    existing.source_papers.append(paper)
                    modified = True
            for source_paper, meta in record.source_metadata.items():
                if provenance.merge_into_source_metadata(existing.source_metadata, source_paper, meta):
                    existing.publications = provenance.publications_from_source_metadata(existing.source_metadata)
                    modified = True
            for snippet in record.context_snippets:
                if not any(
                    s.source_paper == snippet.source_paper and s.page == snippet.page
                    for s in existing.context_snippets
                ):
                    existing.context_snippets.append(snippet)
                    modified = True

            if len(record.definition) > len(existing.definition):
                existing.definition = record.definition
                modified = True
            if record.raw_category and not existing.raw_category:
                existing.raw_category = record.raw_category
                modified = True

            existing_rel_keys = {(r.relation, r.related_term) for r in existing.relations}
            for rel in record.relations:
                if (rel.relation, rel.related_term) not in existing_rel_keys:
                    existing.relations.append(rel)
                    existing_rel_keys.add((rel.relation, rel.related_term))
                    modified = True

            for attr in ("formula", "formula_validation", "chebi", "smiles", "charge", "inchi", "inchikey", "mass"):
                if getattr(record, attr) is not None and getattr(existing, attr) is None:
                    setattr(existing, attr, getattr(record, attr))
                    modified = True

            if record.publications:
                existing_keys = {
                    str(pub.get("source_paper", ""))
                    for pub in existing.publications
                    if isinstance(pub, dict)
                }
                for pub in record.publications:
                    if not isinstance(pub, dict):
                        continue
                    source = str(pub.get("source_paper", ""))
                    if source and source not in existing_keys:
                        existing.publications.append(pub)
                        existing_keys.add(source)
                        modified = True

            if modified:
                self._tl.updated = True
            return key, modified

    def attach_property(self, key: str, prop: PropertyRecord) -> bool:
        """Append a property to an existing term if not already present. Returns True if added."""
        with self._state_lock:
            record = self._records.get(key)
            if record is None:
                return False
            existing = {(p.property, p.value, p.unit, p.context) for p in record.properties}
            if (prop.property, prop.value, prop.unit, prop.context) in existing:
                return False
            record.properties.append(prop)
            self._tl.updated = True
            return True

    # ------------------------------------------------------------------
    # Code snippets + source-scoped provenance
    # ------------------------------------------------------------------

    def add_code_snippets(
        self,
        snips: List[Dict[str, Any]],
        pub_meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Dedup-append code snippets, stamping PDF-derived publication metadata.

        Publication fields come only from ``pub_meta`` (PDF-derived); never from
        the term LLM. Returns True if any snippet was added or updated.
        """
        if not snips:
            return False
        _pm = pub_meta or {}
        updated = False
        with self._state_lock:
            for snip in snips:
                key = provenance.snippet_key(snip)
                source_paper = str(snip.get("source_paper", ""))
                if key in self._snippet_seen:
                    for existing in self.code_snippets:
                        if provenance.snippet_key(existing) == key:
                            sm = existing.setdefault("source_metadata", {})
                            if provenance.merge_into_source_metadata(sm, source_paper, _pm):
                                existing["publications"] = provenance.publications_from_source_metadata(sm)
                                updated = True
                            break
                    continue
                if not snip.get("paper_title"):
                    snip["paper_title"] = _pm.get("paper_title")
                if not snip.get("doi"):
                    snip["doi"] = _pm.get("doi")
                if not snip.get("paper_authors"):
                    snip["paper_authors"] = _pm.get("authors") or []
                if not snip.get("publication_year"):
                    snip["publication_year"] = _pm.get("publication_year")
                sm = snip.setdefault("source_metadata", {})
                provenance.merge_into_source_metadata(sm, source_paper, _pm)
                snip["publications"] = provenance.publications_from_source_metadata(sm)
                self._snippet_seen.add(key)
                self.code_snippets.append(snip)
                updated = True
        if updated:
            self._tl.updated = True
        return updated

    def stamp_source_metadata(self, source_paper: str, pub_meta: Optional[Dict[str, Any]]) -> bool:
        """Merge PDF-derived ``pub_meta`` into the source-scoped metadata of every
        term/snippet that cites ``source_paper``. Returns True if anything changed."""
        if not source_paper or not pub_meta:
            return False
        updated = False
        with self._state_lock:
            for record in self._records.values():
                if source_paper not in record.source_papers:
                    continue
                if provenance.merge_into_source_metadata(record.source_metadata, source_paper, pub_meta):
                    record.publications = provenance.publications_from_source_metadata(record.source_metadata)
                    updated = True
            for snip in self.code_snippets:
                if str(snip.get("source_paper", "")) != source_paper:
                    continue
                sm = snip.setdefault("source_metadata", {})
                if provenance.merge_into_source_metadata(sm, source_paper, pub_meta):
                    snip["publications"] = provenance.publications_from_source_metadata(sm)
                    updated = True
        if updated:
            self._tl.updated = True
        return updated

    def _sync_publications_locked(self) -> None:
        """Derive embedded publications from source_metadata before persistence."""
        for record in self._records.values():
            pubs = provenance.publications_from_source_metadata(record.source_metadata)
            if pubs:
                record.publications = pubs
        for snip in self.code_snippets:
            pubs = provenance.publications_from_source_metadata(snip.get("source_metadata"))
            if pubs:
                snip["publications"] = pubs

    # ------------------------------------------------------------------
    # Thread-local dirty flag
    # ------------------------------------------------------------------

    def consume_updated(self) -> bool:
        """Return True if this thread made any mutations since the last call, then reset."""
        updated = getattr(self._tl, "updated", False)
        self._tl.updated = False
        return updated

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def increment(self, field: str, by: int = 1) -> None:
        """Increment a numeric metadata counter by *by* (default 1), initialising to 0 if absent."""
        self.metadata[field] = self.metadata.get(field, 0) + by

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def assign_importance(self) -> None:
        """Set ``importance`` on every record based on occurrence frequency.

        Rules:
        - ``"high"``   — appears in more than one paper, or on more than 5 pages.
        - ``"medium"`` — appears on more than 2 pages (single paper).
        - ``"low"``    — everything else.
        """
        with self._state_lock:
            for record in self._records.values():
                occ = len(record.pages)
                papers = len(set(record.source_papers))
                if papers > 1 or occ > 5:
                    record.importance = "high"
                elif occ > 2:
                    record.importance = "medium"
                else:
                    record.importance = "low"
