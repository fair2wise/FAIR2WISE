"""``library_passages``: find supporting text for the terms of an existing KG bundle, with no model call (``:local`` only).

Design: ``mkdocs/docs/design-supporting-passages.md`` section f. For one bundle, under the bundle lock, for each source
paper the bundle's terms cite: read every page of the paper from the library (by file name; a name that matches two files
is settled by the manifest sha256, and when that does not settle it the paper is reported ``ambiguous_name`` and
skipped, never guessed), run the locator and quality rules of :mod:`..term_extractor.passages` once over the paper's pages
for all of its terms, and attach the best passages as ``kind: located`` (``verified: true``) to the terms that have no
passage from that paper. The new ``terms.json`` and the rebuilt ``kg.json`` replace the old ones atomically (a temporary
file next to each, then ``os.replace``), so a failure leaves the old files.

* Never overwrites: existing passages stay in place and new ones are appended; the union key is (paper, page, text).
* Idempotent: a second run finds nothing new and says so.
* One unit of work per paper, so progress, cancel and resume work as for an extraction; the terms file is written after
  each paper, the KG is rebuilt once at the end (also after a cancel).
* Relationship evidence does not exist yet: :func:`recover_relation_evidence` is the named hook for it and does nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any

from . import bundles as B
from ..contracts.errors import ContractError, ErrorCode
from ..jobs.keys import OperationInputs
from ..jobs.pipeline import StageBusy, StageContext, StageResult
from ..jobs.stages import WorkUnit, chunked_stage
from ..term_extractor import passages as P
from ..term_extractor import provenance
from ..term_extractor.pub_clean import merge_known_publication
from ..term_extractor.models import union_evidence
from .jobs import library_inputs
from .publication_records import PublicationRecords

if TYPE_CHECKING:
    from .service import LibraryService

logger = logging.getLogger(__name__)

#: Revision of the recovery logic (part of a unit's key).
RECOVERY_REVISION = "1"
#: Most located passages attached to one (term, paper).
LOCATED_PER_TERM_PAPER = 2
#: Reasons a paper is not processed (``recovery.reason`` of its unit result), each reported per paper.
REASON_AMBIGUOUS = "ambiguous_name"
REASON_MISSING = "missing"
REASON_NO_TEXT = "no_text_layer"
REASON_UNREADABLE = "unreadable"
REASON_TEXT = {
    REASON_AMBIGUOUS: "two library files have this name and the KG's record does not tell which one was used",
    REASON_MISSING: "this paper is not in the library",
    REASON_NO_TEXT: "the PDF has no text layer (a scan); nothing can be searched",
    REASON_UNREADABLE: "the PDF could not be read",
}
#: Typed ``reason`` values of the start route's refusals.
REASON_BUSY = "bundle_busy"
REASON_IN_PROGRESS = "passages_in_progress"
REASON_NO_PAPERS = "no_source_papers"
#: Keys of a per-paper manifest ``passages`` entry.
COUNT_KEYS = ("terms", "with_passage", "quoted", "located", "rejected", "uncovered")
#: The relation counts of a manifest entry (added with relation evidence); an entry without them predates it.
RELATION_KEYS = ("relations", "relations_with_evidence", "evidence_sentences", "relations_without")
#: Words below which a paper counts as having no text layer.
MIN_TEXT_WORDS = 20
#: The job notices a stop request at least this often inside one paper (terms between checks).
GUARD_EVERY = 100


def _now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as ``terms.json`` is written (``json.dump(indent=2)``): temp file next to ``path``, then ``os.replace``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def recover_relation_evidence(subject: str, obj: str, paper: str, pages: list[str]) -> list[dict[str, Any]]:
    """Located evidence of one relation in one paper: every sentence (or adjacent pair) naming both endpoints, best first.

    No model call. See :func:`app.modules.term_extractor.passages.relation_evidence` for the rules.
    """
    return P.locate_relation_evidence(pages, subject, obj, paper)


def bundle_is_locked(store: B.BundleStore, name: str) -> bool:
    """Whether another process holds the bundle's extraction lock right now (probes and releases at once)."""
    import fcntl

    from .extraction import LOCK_DIRNAME

    lock = store.root / LOCK_DIRNAME / f"{B.validate_name(name)}.lock"
    if not lock.is_file():
        return False
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def adjust_entry(entry: Mapping[str, Any] | None, *, terms: int, with_before: int, newly: int, located: int,
                 quoted_now: int, located_now: int, rel_total: int = 0, rel_before: int = 0, rel_newly: int = 0,
                 rel_added: int = 0, rel_sentences_now: int = 0) -> dict[str, int]:
    """The manifest ``passages`` entry of a paper after a recovery.

    With no entry yet (a KG extracted before coverage was recorded) the entry is absolute, read from the terms; with
    one, the recovery adds its ``located`` passages, the newly covered terms and lowers ``uncovered`` by them. The
    relation counts work the same way: absolute when the entry has none (it predates relation evidence), else the
    newly covered relations and the sentences added are summed on and ``relations_without`` is lowered.
    """
    if not entry:
        covered = with_before + newly
        out = {"terms": terms, "with_passage": covered, "quoted": quoted_now, "located": located_now, "rejected": 0,
               "uncovered": terms - covered}
    else:
        out = {k: int(entry.get(k, 0)) for k in COUNT_KEYS}
        out["with_passage"] += newly
        out["located"] += located
        out["uncovered"] = max(0, out["uncovered"] - newly)
    if not entry or "relations" not in entry:
        out.update({"relations": rel_total, "relations_with_evidence": rel_before + rel_newly,
                    "evidence_sentences": rel_sentences_now, "relations_without": rel_total - rel_before - rel_newly})
    else:
        rel = {k: int(entry.get(k, 0)) for k in RELATION_KEYS}
        rel["relations_with_evidence"] += rel_newly
        rel["evidence_sentences"] += rel_added
        rel["relations_without"] = max(0, rel["relations_without"] - rel_newly)
        out.update(rel)
    return out


class LibraryPassages:
    """Starts and runs ``library_passages`` for the bundles of one ``:local`` deployment.

    Args:
        service: The library catalog and folder (finds the PDFs by file name).
        store: Bundle store under ``<F2W_WORKDIR>/kgs``.
        clock: ISO timestamp source for the manifest (tests inject one).
    """

    def __init__(self, service: LibraryService, store: B.BundleStore, *, clock: Callable[[], str] = _now) -> None:
        self.service = service
        self.store = store
        self._clock = clock
        self._records: PublicationRecords | None = None
        self._shas: dict[str, str | None] = {}

    # ------------------------------------------------------------------ route side
    def source_papers(self, bundle: B.Bundle) -> list[str]:
        """File names of the papers the bundle's terms cite, else the manifest's papers; sorted, unique."""
        names: set[str] = set()
        try:
            terms = json.loads(bundle.file(B.TERMS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            terms = {}
        for term in (terms.get("terms") if isinstance(terms, dict) else None) or []:
            if isinstance(term, dict):
                names.update(str(p) for p in term.get("source_papers") or [] if p)
        return sorted(names)

    def inputs(self, name: str, *, nonce: str) -> OperationInputs:
        """The job inputs: the bundle name and a nonce (identical inputs would name the same finished job)."""
        return library_inputs({"bundle": name}, nonce=nonce, corpus_scope=(B.validate_name(name).lower(),))

    def check_startable(self, name: str, *, open_jobs: Sequence[tuple[str, str]] = ()) -> dict[str, Any]:
        """Refuse with a typed error unless a run could start now; returns ``{name, papers}`` otherwise.

        Raises:
            ContractError: ``NOT_FOUND`` (no such bundle), ``INVALID_INPUT`` ``no_source_papers`` (the terms cite no
                paper), ``CONFLICT`` ``bundle_busy`` (a running job holds the bundle) or ``passages_in_progress`` (a
                recovery of this bundle is queued or running, ``open_jobs`` as ``(kind, job_id)``).
        """
        bundle = self.store.get(name)  # NOT_FOUND for an unknown bundle, INVALID_INPUT for a bad name
        papers = self.source_papers(bundle)
        if not papers:
            raise ContractError(ErrorCode.INVALID_INPUT, "this KG records no source papers, so there is nothing to search",
                                reason=REASON_NO_PAPERS)
        if open_jobs:
            raise ContractError(ErrorCode.CONFLICT, "supporting text is already being searched for this KG",
                                retryable=False, reason=REASON_IN_PROGRESS)
        if bundle_is_locked(self.store, name):
            raise ContractError(ErrorCode.CONFLICT, "another job is working on this KG now; try again when it ends",
                                retryable=False, reason=REASON_BUSY)
        return {"name": name, "papers": len(papers)}

    # ------------------------------------------------------------------ library lookup
    def resolve_pdf(self, name: str, manifest_sha: str | None) -> tuple[Path | None, str | None]:
        """``(path, None)`` for the library file named ``name``, or ``(None, reason)``.

        A name that matches two files is settled by ``manifest_sha`` (the bundle's record of the extracted bytes) when
        exactly one has it; otherwise the paper is ``ambiguous_name``.
        """
        rows = [r for r in self.service.catalog.rows() if Path(str(r["relpath"])).name == name]
        if not rows:
            return None, REASON_MISSING
        if len(rows) > 1:
            same = [r for r in rows if manifest_sha and r.get("sha256") == manifest_sha]
            if len(same) != 1:
                return None, REASON_AMBIGUOUS
            rows = same
        root = self.service._require_root()  # noqa: SLF001 - one trusted package
        path = self.service.roots.policy("library").require_allowed(root / str(rows[0]["relpath"]))
        return Path(path), None

    # ------------------------------------------------------------------ the job
    def run(self, ctx: StageContext) -> StageResult:
        """The ``library_passages`` stage body (see the module docstring)."""
        from .extraction import BundleBusy, bundle_lock

        name = B.validate_name(str(ctx.inputs.params.get("bundle", "")))
        try:
            with bundle_lock(self.store, name, ctx.job.job_id):
                return self._run_locked(ctx, name)
        except BundleBusy as exc:
            if exc.holder == ctx.job.job_id:
                raise StageBusy("an earlier run of this job still holds the KG bundle") from exc
            raise

    def _run_locked(self, ctx: StageContext, name: str) -> StageResult:
        bundle = self.store.get(name)
        terms_path = bundle.file(B.TERMS_FILE)
        sha_by_name = {str(p.get("name")): p.get("sha256") for p in bundle.manifest.get("papers") or []}
        papers = self.source_papers(bundle)
        ctx.log(f"searching {len(papers)} papers of bundle {name} for supporting text (no model is used)")
        changed = {"added": 0}
        started = self._clock()
        self._records = PublicationRecords(self.service._require_root())  # noqa: SLF001 - one trusted package
        self._shas = sha_by_name

        def units(_sc: StageContext) -> list[WorkUnit]:
            return [WorkUnit(unit_id=p, unit_key=f"{p}:{sha_by_name.get(p) or ''}:{RECOVERY_REVISION}", payload=p)
                    for p in papers]

        def process(sc: StageContext, unit: WorkUnit) -> Mapping[str, Any]:
            paper = str(unit.payload)
            path, reason = self.resolve_pdf(paper, sha_by_name.get(paper))
            if path is None:
                ctx.log(f"{paper}: skipped, {REASON_TEXT[str(reason)]}", level="warning")
                return {"recovery": {"reason": reason, "text": REASON_TEXT[str(reason)]}}
            try:
                pages = P.page_texts(path)
            except Exception as exc:  # noqa: BLE001 - one unreadable PDF must not stop the others
                logger.warning("%s: unreadable (%s)", paper, type(exc).__name__)
                return {"recovery": {"reason": REASON_UNREADABLE, "text": REASON_TEXT[REASON_UNREADABLE]}}
            if sum(len(t.split()) for t in pages) < MIN_TEXT_WORDS:
                ctx.log(f"{paper}: skipped, {REASON_TEXT[REASON_NO_TEXT]}", level="warning")
                return {"recovery": {"reason": REASON_NO_TEXT, "text": REASON_TEXT[REASON_NO_TEXT]}}
            result = self._recover_paper(sc, bundle, terms_path, paper, pages)
            changed["added"] += result["located_added"] + result["evidence_added"] + result["publication_terms_corrected"]
            ctx.log(f"{paper}: {result['terms']} terms, {result['with_passage_before']} with text before, "
                    f"{result['located_added']} passages added for {result['newly_covered']} terms, "
                    f"{result['still_uncovered']} still without; {result['relations']} relations, {result['evidence_added']} sentences added "
                    f"for {result['relations_newly_covered']}, {result['relations_still_without']} without a sentence naming both ends"
                    + (f"; publication record corrected on {result['publication_terms_corrected']} terms"
                       if result["publication_terms_corrected"] else ""))
            return {"recovery": result}

        try:
            result = chunked_stage(units, process)(ctx)
        finally:
            self._finalize(bundle, name, changed["added"] > 0 or self._kg_is_stale(bundle))
        rows = ctx.store.unit_rows(ctx.job.job_id, ctx.stage, limit=5000)
        block = recovery_block({r["unit_id"]: (r["result"] or {}).get("recovery") for r in rows if r["status"] == "done"})
        self._record_recovery(name, block, started)
        total = block["total"]
        ctx.log(
            (f"found supporting text for {total['newly_covered']} more terms ({total['located_added']} passages) and "
             f"{total['evidence_added']} relation sentences for {total['relations_newly_covered']} relations; "
             if total["located_added"] or total["evidence_added"] else "nothing new to add; ")
            + f"{total['still_uncovered']} terms and {total['relations_still_without']} relations still have none; "
              f"{total['papers_skipped']} papers skipped")
        progress = {**dict(result.progress), **{f"recovery_{k}": v for k, v in total.items()}}
        return StageResult(progress=progress, data={"bundle": name, "recovery": block}, incomplete=result.incomplete)

    def _recover_paper(self, sc: StageContext, bundle: B.Bundle, terms_path: Path, paper: str,
                       pages: list[str]) -> dict[str, int]:
        """Locate passages for the terms of one paper and write the terms file atomically; returns the counts."""
        doc = json.loads(terms_path.read_text(encoding="utf-8"))
        terms = [t for t in doc.get("terms", []) if isinstance(t, dict) and paper in (t.get("source_papers") or [])]
        with_before = newly = added = 0
        todo: list[dict[str, Any]] = []
        for term in terms:
            if any(isinstance(s, dict) and s.get("source_paper") == paper for s in term.get("context_snippets") or []):
                with_before += 1
            else:
                todo.append(term)
        for index, term in enumerate(todo):
            if index % GUARD_EVERY == 0:
                sc.guard()
            found = P.best_passages(pages, str(term.get("term") or ""), limit=LOCATED_PER_TERM_PAPER)
            snippets = term.setdefault("context_snippets", [])
            seen = {(s.get("source_paper"), s.get("page"), s.get("text")) for s in snippets if isinstance(s, dict)}
            hit = 0
            for passage in found:
                key = (paper, passage.page, passage.text)
                if key in seen:
                    continue
                seen.add(key)
                snippets.append({"text": passage.text, "source_paper": paper, "page": passage.page, "kind": "located",
                                 "verified": True})
                hit += 1
            if hit:
                added += hit
                newly += 1
        rel_total, rel_before, rel_newly, rel_added, rel_sentences_now = self._recover_relations(sc, terms, paper, pages)
        pub_fixed = self._correct_publication(terms, paper)
        quoted_now = located_now = 0
        for term in terms:
            for s in term.get("context_snippets") or []:
                if isinstance(s, dict) and s.get("source_paper") == paper:
                    if s.get("kind") == "located":
                        located_now += 1
                    else:
                        quoted_now += 1
        if added or rel_added or pub_fixed:
            doc.setdefault("metadata", {})
            meta = doc["metadata"] if isinstance(doc["metadata"], dict) else {}
            if isinstance(meta, dict) and (added or rel_added):
                stats = meta.setdefault("passages", {})
                if isinstance(stats, dict):
                    stats[paper] = adjust_entry(stats.get(paper), terms=len(terms), with_before=with_before, newly=newly,
                                                located=added, quoted_now=quoted_now, located_now=located_now,
                                                rel_total=rel_total, rel_before=rel_before, rel_newly=rel_newly,
                                                rel_added=rel_added, rel_sentences_now=rel_sentences_now)
            doc["metadata"] = meta
            sc.guard()  # a stop requested now leaves the old terms file
            write_json_atomic(terms_path, doc)
        self._record_paper(bundle.name, paper, {"terms": len(terms), "with_before": with_before, "newly": newly,
                                                "added": added, "quoted_now": quoted_now, "located_now": located_now,
                                                "rel_total": rel_total, "rel_before": rel_before, "rel_newly": rel_newly,
                                                "rel_added": rel_added, "rel_sentences_now": rel_sentences_now})
        return {"terms": len(terms), "with_passage_before": with_before, "located_added": added, "newly_covered": newly,
                "still_uncovered": len(terms) - with_before - newly, "relations": rel_total,
                "relations_with_evidence_before": rel_before, "evidence_added": rel_added,
                "relations_newly_covered": rel_newly, "relations_still_without": rel_total - rel_before - rel_newly,
                "publication_terms_corrected": pub_fixed}

    def _correct_publication(self, terms: list[dict[str, Any]], paper: str) -> int:
        """Set the publication metadata of ``paper`` on the terms that cite it from the library's own record (in place).

        The library record (title, authors, year, DOI, venue the registry gave) wins for every field it has; the stored
        PDF-derived fields stay for what it lacks, except a junk title or author (a file path, an ``f2w://`` URI, a tool or
        account name, a postal address), which is dropped. Idempotent. Returns the number of terms changed.
        """
        records = getattr(self, "_records", None)
        if records is None:   # driven outside run(): read the library's records now
            records = self._records = PublicationRecords(self.service._require_root())  # noqa: SLF001 - one trusted package
        known = records.for_file(paper, getattr(self, "_shas", {}).get(paper))
        changed = 0
        for term in terms:
            source_metadata = term.get("source_metadata")
            if not isinstance(source_metadata, dict):
                source_metadata = term["source_metadata"] = {}
            held = source_metadata.get(paper) if isinstance(source_metadata.get(paper), dict) else {}
            new = provenance.clean_pub_meta(merge_known_publication(held, known))
            if new != provenance.clean_pub_meta(held):
                source_metadata[paper] = new
                term["publications"] = provenance.publications_from_source_metadata(source_metadata)
                changed += 1
        return changed

    def _recover_relations(self, sc: StageContext, terms: list[dict[str, Any]], paper: str,
                           pages: list[str]) -> tuple[int, int, int, int, int]:
        """Locate relation evidence for the relations of ``terms`` in this paper (in place).

        A relation that already has located evidence from this paper is left alone (idempotent); any other is searched for
        every sentence, or two adjacent sentences, naming both endpoints (:func:`recover_relation_evidence`).

        Returns:
            ``(relations, with_evidence_before, newly_covered, sentences_added, sentences_from_this_paper_now)``.
        """
        total = before = newly = added = 0
        for index, term in enumerate(terms):
            if index % GUARD_EVERY == 0:
                sc.guard()
            for relation in term.get("relations") or []:
                if not isinstance(relation, dict) or not relation.get("related_term"):
                    continue
                total += 1
                held = relation.setdefault("evidence", [])
                if not isinstance(held, list):
                    held = relation["evidence"] = [str(held)]
                if any(isinstance(e, dict) and e.get("source_paper") == paper for e in held):
                    before += 1
                    continue
                new = recover_relation_evidence(str(term.get("term") or ""), str(relation["related_term"]), paper, pages)
                before_len = len(held)
                if union_evidence(held, new):
                    newly += 1
                    added += len(held) - before_len
                if not held:
                    relation.pop("evidence")
        now = sum(1 for term in terms for r in term.get("relations") or [] if isinstance(r, dict)
                  for e in r.get("evidence") or [] if isinstance(e, dict) and e.get("source_paper") == paper)
        return total, before, newly, added, now

    # ------------------------------------------------------------------ files
    def _kg_is_stale(self, bundle: B.Bundle) -> bool:
        terms, kg = bundle.file(B.TERMS_FILE), bundle.file(B.KG_FILE)
        try:
            return terms.stat().st_mtime > kg.stat().st_mtime
        except OSError:
            return False

    def _finalize(self, bundle: B.Bundle, name: str, rebuild: bool) -> None:
        """Rebuild ``kg.json`` from the new terms into a temporary file, then replace the old one (atomic)."""
        if not rebuild:
            return
        from ..graph import kg_update

        kg = bundle.file(B.KG_FILE)
        fd, tmp = tempfile.mkstemp(dir=kg.parent, prefix=f".{kg.name}.", suffix=".tmp")
        os.close(fd)
        try:
            kg_update.rebuild_kg(str(bundle.file(B.TERMS_FILE)), tmp)
            Path(tmp).replace(kg)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        logger.info("rebuilt the KG of bundle %s with the new supporting text", name)

    def _record_paper(self, name: str, paper: str, facts: Mapping[str, int]) -> None:
        """Write the paper's ``passages`` entry into the manifest (at once, so a resumed or cancelled run keeps it)."""
        bundle = self.store.get(name)
        manifest = dict(bundle.manifest)
        passages = {str(k): dict(v) for k, v in (manifest.get("passages") or {}).items() if isinstance(v, Mapping)}
        passages[paper] = adjust_entry(passages.get(paper), terms=facts["terms"], with_before=facts["with_before"],
                                       newly=facts["newly"], located=facts["added"], quoted_now=facts["quoted_now"],
                                       located_now=facts["located_now"], rel_total=facts["rel_total"],
                                       rel_before=facts["rel_before"], rel_newly=facts["rel_newly"],
                                       rel_added=facts["rel_added"], rel_sentences_now=facts["rel_sentences_now"])
        manifest["passages"] = passages
        B._write_json(bundle.directory / B.MANIFEST_FILE, manifest)  # noqa: SLF001 - the bundle package's atomic writer

    def _record_recovery(self, name: str, block: Mapping[str, Any], started: str) -> None:
        """Write the additive ``recovery`` info (last run, counts) into the manifest."""
        bundle = self.store.get(name)
        manifest = dict(bundle.manifest)
        total = block["total"]
        manifest["recovery"] = {"last_run": self._clock(), "started_at": started, "papers": len(block["papers"]),
                                "located_added": total["located_added"], "terms_newly_covered": total["newly_covered"],
                                "evidence_added": total["evidence_added"],
                                "relations_newly_covered": total["relations_newly_covered"],
                                "relations_still_without": total["relations_still_without"],
                                "still_uncovered": total["still_uncovered"], "papers_skipped": total["papers_skipped"]}
        B._write_json(bundle.directory / B.MANIFEST_FILE, manifest)  # noqa: SLF001 - the bundle package's atomic writer


#: Counts of one paper's recovery result that are summed into the totals.
RESULT_KEYS = ("terms", "with_passage_before", "located_added", "newly_covered", "still_uncovered", "relations",
               "relations_with_evidence_before", "evidence_added", "relations_newly_covered", "relations_still_without",
               "publication_terms_corrected")


def recovery_block(units: Mapping[str, Mapping[str, Any] | None]) -> dict[str, Any]:
    """Per-paper results and totals from the ``recovery`` dicts of finished units (``{paper: result}``)."""
    papers = {p: dict(r) for p, r in units.items() if r}
    total = {**dict.fromkeys(RESULT_KEYS, 0), "papers_processed": 0, "papers_skipped": 0}
    for result in papers.values():
        if result.get("reason"):
            total["papers_skipped"] += 1
            continue
        total["papers_processed"] += 1
        for key in RESULT_KEYS:
            total[key] += int(result.get(key, 0))
    return {"papers": papers, "total": total}


__all__ = ("COUNT_KEYS", "LOCATED_PER_TERM_PAPER", "LibraryPassages", "REASON_AMBIGUOUS", "REASON_BUSY", "REASON_IN_PROGRESS",
           "REASON_MISSING", "REASON_NO_PAPERS", "REASON_NO_TEXT", "REASON_UNREADABLE", "adjust_entry", "bundle_is_locked",
           "RELATION_KEYS", "RESULT_KEYS", "recover_relation_evidence", "recovery_block", "write_json_atomic")
