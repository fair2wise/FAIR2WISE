"""Thread-safe in-memory store of extracted terms and code snippets, persisted to JSON."""

from collections.abc import Callable
import datetime
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any

from . import provenance
from .models import ContextSnippet, PropertyRecord, RelationRecord, TermRecord, union_aliases, union_evidence

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
        self._records: dict[str, TermRecord] = {}
        self._display_to_key: dict[str, str] = {}
        self.code_snippets: list[dict[str, Any]] = []
        self._snippet_seen: set[tuple[str, str, str]] = set()
        self._state_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._tl = threading.local()
        #: Passage coverage of this run (per paper; sets of term keys) and of earlier runs read from the file.
        self._passage_run: dict[str, dict[str, Any]] = {}
        #: ``(outcome, relation, subject_class, object_class) -> count`` of the relation repairs of this run.
        self._relation_checks: dict[tuple[str, str, str, str], int] = {}
        #: ``(paper, class) -> count`` of rejected model sentences and a bounded sample of them (diagnostics only).
        self._rejected_classes: dict[tuple[str, str], int] = {}
        self._rejected_samples: list[dict[str, Any]] = []
        self._passage_base: dict[str, dict[str, int]] = {}
        #: Running tally of what :meth:`upsert` did since this store was opened (a job's live KG growth counters).
        self._growth = {"entities_new": 0, "entities_merged": 0, "relations_new": 0, "relations_merged": 0}
        #: Returns ``True`` once the run owning this store was fenced out (another owner took its job over).
        #: From then on every write is ignored and the answer is latched: the stale run's page threads may
        #: still be mid-call, but nothing they produce may reach memory or the shared terms file.
        self.write_fence: Callable[[], bool] | None = None
        self._fenced = False
        self.metadata: dict[str, Any] = {
            "extraction_date": datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z",
            "processed_files": 0,
            "processed_pages_total": 0,
            "processed_pages_with_terms": 0,
            "version": "2.1",
        }
        Path(os.path.abspath(output_file)).parent.mkdir(parents=True, exist_ok=True)  # noqa: PTH100 - lexical abspath, not resolve()
        self._load()

    def _is_fenced(self) -> bool:
        """Whether writes are refused (the fence tripped at any earlier point stays tripped)."""
        if self._fenced:
            return True
        fence = self.write_fence
        if fence is not None and fence():
            self._fenced = True
            logger.warning("term store %s fenced: the owning run lost its job; writes are ignored", self.output_file)
        return self._fenced

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Populate ``_records`` from the JSON file on disk. No-op if the file is absent."""
        if not Path(self.output_file).exists():
            return
        try:
            with Path(self.output_file).open() as fh:
                data = json.load(fh)
            skipped = 0
            for index, raw in enumerate(data.get("terms", [])):
                try:
                    record = TermRecord.from_dict(raw)
                    key = self.normalize(record.term)
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    skipped += 1
                    logger.warning(
                        "Skipping unreadable term record #%d in %s: %s: %s",
                        index, self.output_file, type(exc).__name__, exc,
                    )
                    continue
                if key in self._records:
                    logger.warning(
                        "Term %r in %s collides with %r on key %r; the later record wins",
                        record.term, self.output_file, self._records[key].term, key,
                    )
                self._records[key] = record
                self._display_to_key[record.term] = key
            if skipped:
                logger.warning("Skipped %d unreadable term record(s) in %s", skipped, self.output_file)
            for snip in data.get("code_snippets", []):
                self.code_snippets.append(snip)
                self._snippet_seen.add(provenance.snippet_key(snip))
            # ``None`` marks an unknown value (reconstructed files): keep the in-memory default instead.
            self.metadata.update({k: v for k, v in (data.get("metadata") or {}).items() if v is not None})
            base = self.metadata.pop("passages", None)
            if isinstance(base, dict):
                self._passage_base = {
                    str(p): {str(k): int(v) for k, v in row.items() if isinstance(v, int)}
                    for p, row in base.items() if isinstance(row, dict)
                }
            logger.info(
                "Loaded %d existing terms and %d code snippets from %s",
                len(self._records), len(self.code_snippets), self.output_file,
            )
        except Exception as e:
            logger.warning("Could not load previous terms from %s: %s", self.output_file, e)

    def save(self) -> None:
        """Write current state to disk. Thread-safe; takes a snapshot under the state lock."""
        if self._is_fenced():
            return
        with self._state_lock:
            self._sync_publications_locked()
            terms_out = [r.to_dict() for r in self._records.values()]
            metadata_snapshot = dict(self.metadata)
            merged_stats = self._merged_stats_locked()
            if merged_stats:
                metadata_snapshot["passages"] = merged_stats
            snippets_out = list(self.code_snippets)
        with self._save_lock:
            try:
                out = {"metadata": metadata_snapshot, "terms": terms_out, "code_snippets": snippets_out}
                with Path(self.output_file).open("w") as fh:
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

    def get(self, key: str) -> TermRecord | None:
        """Return the ``TermRecord`` for the normalised *key*, or ``None`` if absent."""
        with self._state_lock:
            return self._records.get(key)

    def all_records(self) -> list[TermRecord]:
        """Return a snapshot list of all ``TermRecord`` objects in the store."""
        with self._state_lock:
            return list(self._records.values())

    def all_display_names(self) -> list[str]:
        """Return a snapshot list of original (non-normalised) term strings."""
        with self._state_lock:
            return list(self._display_to_key.keys())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, record: TermRecord) -> tuple[str, bool]:
        """Insert or merge a TermRecord. Returns (key, was_modified).

        Merge rules:
        - Pages and source papers are appended (deduped).
        - Context snippets are unioned by (source_paper, page, text); the same text as both kinds keeps ``quoted``;
          new ones go after the existing ones, except a quoted+verified one goes before the first located one.
        - Longer definition wins.
        - New relations are appended (deduped by (relation, related_term)); the evidence of a repeated relation is unioned by
          (paper, page, text).
        - Chemical fields (formula, smiles, etc.) fill in only if currently None.
        """
        key = self.normalize(record.term)
        if self._is_fenced():
            return key, False
        with self._state_lock:
            if key not in self._records:
                self._records[key] = record
                self._display_to_key[record.term] = key
                self._growth["entities_new"] += 1
                self._growth["relations_new"] += len(record.relations)
                self._tl.updated = True
                return key, True

            existing = self._records[key]
            modified = False
            self._growth["entities_merged"] += 1

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
            for snippet in record.context_snippets:   # union per paper, page and text: a second passage on a page is kept
                if self._union_snippet(existing.context_snippets, snippet):
                    modified = True

            if len(record.definition) > len(existing.definition):
                existing.definition = record.definition
                modified = True
            if record.raw_category and not existing.raw_category:
                existing.raw_category = record.raw_category
                modified = True

            existing_rels = {(r.relation, r.related_term): r for r in existing.relations}
            for rel in record.relations:
                held = existing_rels.get((rel.relation, rel.related_term))
                if held is None:
                    existing.relations.append(rel)
                    existing_rels[(rel.relation, rel.related_term)] = rel
                    self._growth["relations_new"] += 1
                    modified = True
                else:
                    self._growth["relations_merged"] += 1
                    if union_evidence(held.evidence, rel.evidence):   # the evidence of a repeated relation is kept, never dropped
                        modified = True

            if union_aliases(existing.aliases, record.aliases, existing.term):
                modified = True
            held_props = {(p.property, str(p.value)) for p in existing.properties}
            for prop in record.properties:   # attributes of the term (a literal-range slot): unioned, never duplicated
                if (prop.property, str(prop.value)) not in held_props:
                    existing.properties.append(prop)
                    held_props.add((prop.property, str(prop.value)))
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

    def add_aliases(self, term: str, aliases: Sequence[str]) -> bool:
        """Union ``aliases`` onto an existing term (case-insensitive, never the term itself); ``False`` when the term is unknown."""
        key = self.normalize(term)
        if self._is_fenced():
            return False
        with self._state_lock:
            existing = self._records.get(key)
            if existing is None:
                return False
            changed = union_aliases(existing.aliases, aliases, existing.term)
            if changed:
                self._tl.updated = True
            return changed

    def add_relation(self, term: str, rel: RelationRecord) -> bool:
        """Add one relation to an existing term (merging a repeated one's evidence); ``False`` when the term is unknown.

        Used when a relation turns out to belong to the other end of the pair: it is stored on the term that is its real
        subject without counting that term as a new mention.
        """
        key = self.normalize(term)
        if self._is_fenced():
            return False
        with self._state_lock:
            existing = self._records.get(key)
            if existing is None:
                return False
            held = next((r for r in existing.relations if (r.relation, r.related_term) == (rel.relation, rel.related_term)), None)
            if held is None:
                existing.relations.append(rel)
                self._growth["relations_new"] += 1
            else:
                self._growth["relations_merged"] += 1
                union_evidence(held.evidence, rel.evidence)
            self._tl.updated = True
            return True

    @staticmethod
    def _union_snippet(snippets: list[ContextSnippet], new: ContextSnippet) -> bool:
        """Add ``new`` to ``snippets`` unless (paper, page, text) is there; returns whether the list changed.

        The same text arriving as ``quoted`` over a stored ``located`` one upgrades that entry in place. A new
        quoted+verified sentence is inserted before the first located one (the best passage stays first, the
        disclosure caps cut in stored order); anything else is appended.
        """
        for old in snippets:
            if old.source_paper == new.source_paper and old.page == new.page and old.text == new.text:
                if old.kind == "located" and new.kind == "quoted":
                    old.kind, old.verified = "quoted", new.verified or old.verified
                    return True
                return False
        if new.kind == "quoted" and new.verified:
            at = next((i for i, s in enumerate(snippets) if s.kind == "located"), len(snippets))
            snippets.insert(at, new)
        else:
            snippets.append(new)
        return True

    # ------------------------------------------------------------------
    # Passage coverage (design: supporting passages, section d)
    # ------------------------------------------------------------------

    #: Most rejected sentences kept as a sample per run, and per class.
    REJECT_SAMPLE_TOTAL = 20
    REJECT_SAMPLE_PER_CLASS = 6

    def note_rejection(self, paper: str, reason: str, sentence: str, page: int | None) -> None:
        """Count one rejected model sentence by class and keep a bounded, spread-out sample of the text."""
        with self._state_lock:
            key = (paper, reason)
            self._rejected_classes[key] = self._rejected_classes.get(key, 0) + 1
            same = sum(1 for item in self._rejected_samples if item["reason"] == reason)
            if len(self._rejected_samples) < self.REJECT_SAMPLE_TOTAL and same < self.REJECT_SAMPLE_PER_CLASS:
                self._rejected_samples.append({"paper": paper, "reason": reason, "page": page, "text": " ".join(sentence.split())[:300]})

    def rejection_report(self) -> dict[str, Any]:
        """Rejected model sentences of this run by class (``by_reason``, ``by_paper``) and the sample; ``{}`` when none."""
        with self._state_lock:
            classes = dict(self._rejected_classes)
            samples = [dict(item) for item in self._rejected_samples]
        if not classes:
            return {}
        by_reason: dict[str, int] = {}
        by_paper: dict[str, dict[str, int]] = {}
        for (paper, reason), count in classes.items():
            by_reason[reason] = by_reason.get(reason, 0) + count
            by_paper.setdefault(paper, {})[reason] = count
        return {"by_reason": by_reason, "by_paper": by_paper, "samples": samples}

    def note_relation_check(self, outcome: str, relation: str, subject_class: str, object_class: str) -> None:
        """Count one relation repair of this run (``attribute``, ``swapped`` or ``invalid``) under its pattern."""
        with self._state_lock:
            key = (outcome, relation, subject_class, object_class)
            self._relation_checks[key] = self._relation_checks.get(key, 0) + 1

    def relation_check_report(self) -> dict[str, Any]:
        """The relation repairs of this run: totals per outcome and one row per distinct pattern, most frequent first.

        Empty (``{}``) when nothing was repaired, so a run on a shipped schema has no such block.
        """
        with self._state_lock:
            items = sorted(self._relation_checks.items(), key=lambda kv: (-kv[1], kv[0]))
        if not items:
            return {}
        totals: dict[str, int] = {}
        for (outcome, _, _, _), count in items:
            totals[outcome] = totals.get(outcome, 0) + count
        return {**totals, "patterns": [{"outcome": o, "relation": r, "subject_class": sc, "object_class": oc, "count": n}
                                       for (o, r, sc, oc), n in items]}

    def note_passage(self, paper: str, term_key: str, *, kind: str | None = None, rejected: int = 0) -> None:
        """Record what grounding did for one registered term of ``paper``.

        Args:
            paper: Source paper file name.
            term_key: Normalised key of the term.
            kind: ``"quoted"`` or ``"located"`` when a passage was stored, ``None`` when none was.
            rejected: Number of model sentences rejected (not found in the page text).
        """
        with self._state_lock:
            row = self._run_row(paper)
            row["terms"].add(term_key)
            if kind in ("quoted", "located"):
                row["covered"].add(term_key)
                row[kind] += 1
            row["rejected"] += rejected

    def _run_row(self, paper: str) -> dict[str, Any]:
        return self._passage_run.setdefault(paper, {
            "terms": set(), "covered": set(), "quoted": 0, "located": 0, "rejected": 0,
            "relations": set(), "rel_covered": set(), "evidence": 0,
        })

    def note_relation(self, paper: str, relation_key: str, sentences: int) -> None:
        """Record the located evidence of one registered relation of ``paper``.

        Args:
            paper: Source paper file name.
            relation_key: Identity of the relation (subject key, predicate, object key).
            sentences: Number of sentences naming both endpoints that were stored (0 when none was found).
        """
        with self._state_lock:
            row = self._run_row(paper)
            row["relations"].add(relation_key)
            if sentences > 0:
                row["rel_covered"].add(relation_key)
                row["evidence"] += sentences

    def _run_stats_locked(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for paper, row in self._passage_run.items():
            terms, covered = len(row["terms"]), len(row["covered"])
            relations, rel_covered = len(row["relations"]), len(row["rel_covered"])
            out[paper] = {"terms": terms, "with_passage": covered, "quoted": row["quoted"], "located": row["located"],
                          "rejected": row["rejected"], "uncovered": terms - covered, "relations": relations,
                          "relations_with_evidence": rel_covered, "evidence_sentences": row["evidence"],
                          "relations_without": relations - rel_covered}
        return out

    def run_passage_stats(self) -> dict[str, dict[str, int]]:
        """Per-paper passage coverage of this run only: terms, with_passage, quoted, located, rejected, uncovered."""
        with self._state_lock:
            return self._run_stats_locked()

    def passage_stats(self) -> dict[str, dict[str, int]]:
        """Per-paper passage coverage including earlier runs of this terms file (summed per paper)."""
        with self._state_lock:
            return self._merged_stats_locked()

    def _merged_stats_locked(self) -> dict[str, dict[str, int]]:
        merged = {paper: dict(row) for paper, row in self._passage_base.items()}
        for paper, row in self._run_stats_locked().items():
            into = merged.setdefault(paper, {k: 0 for k in row})
            for key, value in row.items():
                into[key] = int(into.get(key, 0)) + value
        return merged

    def growth(self) -> dict[str, int]:
        """Snapshot of the KG growth counters of this run.

        Returns:
            ``entities_new`` / ``entities_merged`` (a mention that created a term versus one folded into an existing
            term, including terms loaded from an earlier bundle), ``relations_new`` / ``relations_merged`` (the same
            for relations), and ``entities_total`` / ``relations_total`` (what the store holds now).
        """
        with self._state_lock:
            return {**self._growth, "entities_total": len(self._records),
                    "relations_total": sum(len(r.relations) for r in self._records.values())}

    def attach_property(self, key: str, prop: PropertyRecord) -> bool:
        """Append a property to an existing term if not already present. Returns True if added."""
        if self._is_fenced():
            return False
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
        snips: list[dict[str, Any]],
        pub_meta: dict[str, Any] | None = None,
    ) -> bool:
        """Dedup-append code snippets, stamping PDF-derived publication metadata.

        Publication fields come only from ``pub_meta`` (PDF-derived); never from
        the term LLM. Returns True if any snippet was added or updated.
        """
        if not snips or self._is_fenced():
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

    def stamp_source_metadata(self, source_paper: str, pub_meta: dict[str, Any] | None) -> bool:
        """Merge PDF-derived ``pub_meta`` into the metadata of terms/snippets citing ``source_paper``.

        Returns True if anything changed.
        """
        if not source_paper or not pub_meta or self._is_fenced():
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
        self.metadata[field] = (self.metadata.get(field) or 0) + by

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def importance_tier(pages: list[int], source_papers: list[str]) -> str:
        """Return the importance tier for a term seen on ``pages`` in ``source_papers``.

        Args:
            pages: Page numbers where the term appears.
            source_papers: Paper identifiers that mention the term.

        Returns:
            ``"high"`` (more than one paper, or more than 5 pages), ``"medium"`` (more than 2 pages) or ``"low"``.
        """
        occ = len(pages)
        papers = len(set(source_papers))
        if papers > 1 or occ > 5:
            return "high"
        if occ > 2:
            return "medium"
        return "low"

    def assign_importance(self) -> None:
        """Set ``importance`` on every record based on occurrence frequency.

        Rules:
        - ``"high"``   — appears in more than one paper, or on more than 5 pages.
        - ``"medium"`` — appears on more than 2 pages (single paper).
        - ``"low"``    — everything else.
        """
        with self._state_lock:
            for record in self._records.values():
                record.importance = self.importance_tier(record.pages, record.source_papers)
