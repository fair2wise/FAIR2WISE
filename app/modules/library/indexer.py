"""Library papers in the source index (A8, T36).

``library_index`` chunks every catalogued library PDF into the **literature index directory**
(``HYBRID_RAG_LITERATURE_INDEX``) with ``corpus="library"``, so the existing ``hybrid_rag`` search path
(``SourceIndex`` / ``PaperRetriever``) answers over them and cites them. Nothing else about that index
changes: the harvest literature chunks stay, and the two builders share the directory under
:func:`~app.modules.hybrid_rag.index_store.index_lock`.

* ``doc_id = lib:<relpath>``; ``work_id = doi:<doi>`` when a DOI is known (the form the KG overlap boost
  matches), else ``lib:<sha256[:16]>``.
* ``meta["docs"]`` is the record of this corpus: ``doc_id -> {sha256, n_chunks, title, doi, pages, status,
  chunker_version}`` with ``status`` ``ok`` or ``no_text`` (a scanned or unreadable PDF has no chunks; it is
  reported, never a failure). It is what makes a re-run incremental: an unchanged paper (same sha256 and
  chunker version) is skipped and an unchanged library writes nothing.
* A changed paper's chunks are replaced and a removed paper's are pruned; only ``corpus == "library"`` chunks
  are ever touched.
* Work is committed in batches (and at the end, also on cancel or failure) as an atomic, locked
  read-modify-write; each paper is one checkpointed ``WorkUnit`` (progress, cancel, resume).

The PDF text comes from :func:`app.modules.hybrid_rag.chunker.chunk_pdf_detailed` (``open_pdf`` and page
``get_text``). Chunk ``path`` is the relative path in the library, never an absolute one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hybrid_rag.chunker import CHUNKER_VERSION, PdfChunks, chunk_pdf_detailed, iter_nonempty
from ..hybrid_rag.index_store import SourceIndex, index_lock, merge_chunks, write_index
from ..jobs.pipeline import StageContext, StageResult
from ..jobs.stages import WorkUnit, chunked_stage
from ..literature.intake import extract_doi_candidates, filename_doi_candidates
from .catalog import sha256_file
from .probe import clean_title

if TYPE_CHECKING:  # pragma: no cover
    from .service import LibraryService

logger = logging.getLogger(__name__)

CORPUS = "library"
DOC_PREFIX = "lib:"
STATUS_OK = "ok"
STATUS_NO_TEXT = "no_text"
#: Papers whose chunks are held in memory before one locked write.
DEFAULT_FLUSH_EVERY = 8
#: ``chunker(path, work_id=, doc_id=, corpus=, extra=) -> PdfChunks`` (injectable for tests).
Chunker = Callable[..., PdfChunks]

#: ``meta.json`` keys the writer regenerates (everything else of an existing meta is carried over).
_GENERATED_META = frozenset({"index_name", "chunker_version", "lexical_index_version", "embedding_model",
                             "corpus_revision", "n_chunks", "n_docs", "corpora", "doc_ids"})


def doc_id_for(relpath: str) -> str:
    """The index ``doc_id`` of a library paper."""
    return f"{DOC_PREFIX}{relpath}"


def work_id_for(doi: str | None, sha256: str) -> str:
    """``doi:<doi>`` (lower case) when known, else ``lib:<sha256[:16]>``."""
    cleaned = (doi or "").strip().lower()
    return f"doi:{cleaned}" if cleaned else f"{DOC_PREFIX}{sha256[:16]}"


def _utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LibraryIndexer:
    """Builds and maintains the ``library`` corpus of the literature index directory.

    Args:
        service: The :class:`~app.modules.library.service.LibraryService` (catalog and folder).
        index_dir: The literature index directory (``None``: not configured, the job fails closed).
        chunker: PDF reader and chunker; defaults to ``chunk_pdf_detailed``.
        flush_every: Papers per locked write.
    """

    def __init__(self, service: LibraryService, index_dir: Path | None, *, chunker: Chunker | None = None,
                 flush_every: int = DEFAULT_FLUSH_EVERY) -> None:
        self.service = service
        self.index_dir = Path(index_dir) if index_dir else None
        self._chunker: Chunker = chunker or chunk_pdf_detailed
        self.flush_every = max(1, int(flush_every))

    # -- records ---------------------------------------------------------------------------------

    def docs_record(self) -> dict[str, dict[str, Any]]:
        """``meta["docs"]`` of the index (empty when absent or malformed)."""
        if self.index_dir is None:
            return {}
        docs = SourceIndex(self.index_dir).meta().get("docs")
        return {str(k): dict(v) for k, v in docs.items() if isinstance(v, dict)} if isinstance(docs, dict) else {}

    def _needs_work(self, row: Mapping[str, Any], docs: Mapping[str, Mapping[str, Any]]) -> bool:
        entry = docs.get(doc_id_for(row["relpath"]))
        return entry is None or entry.get("sha256") != row["sha256"] or entry.get("chunker_version") != CHUNKER_VERSION

    def sync_catalog(self) -> int:
        """Make the catalog's index state match ``meta["docs"]`` (the truth); returns how many rows changed."""
        docs = self.docs_record()
        changed = 0
        for row in self.service.catalog.rows():
            entry = docs.get(doc_id_for(row["relpath"]))
            status, sha = (entry.get("status"), entry.get("sha256")) if entry else (None, None)
            if status not in (STATUS_OK, STATUS_NO_TEXT):
                status, sha = None, None
            if (row["index_status"], row["indexed_sha256"]) != (status, sha):
                self.service.catalog.set_index_state(row["relpath"], status=status, sha256=sha,
                                                     pages=(entry or {}).get("pages"))
                changed += 1
        return changed

    # -- one paper ---------------------------------------------------------------------------------

    def _chunk_one(self, root: Path, row: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Read and chunk one paper: its ``docs`` entry and its chunk records (empty for ``no_text``)."""
        rel = str(row["relpath"])
        path = self.service.roots.policy("library").require_allowed(root / rel)
        sha = sha256_file(path)  # the bytes read now, not the catalog's earlier hash
        doc_id = doc_id_for(rel)
        title = (row.get("verified_title") if row.get("verify_status") == "verified" else None) or row.get("title") or ""
        doi = (row.get("verified_doi") if row.get("verify_status") == "verified" else None) or row.get("doi") or ""
        read = self._chunker(path, work_id=work_id_for(doi, sha), doc_id=doc_id, corpus=CORPUS,
                             extra={"title": title, "doi": doi, "sha256": sha, "corpus_revision": CORPUS})
        if not title:
            title = clean_title(read.meta_title)
        if not doi:
            found = extract_doi_candidates(read.first_text) if read.first_text else []
            found = found or filename_doi_candidates(path.name)
            doi = found[0] if found else ""
        work_id = work_id_for(doi, sha)
        chunks = iter_nonempty(read.chunks)
        for chunk in chunks:
            chunk.update(path=rel, work_id=work_id, title=title, doi=doi, sha256=sha, corpus=CORPUS)
        entry = {"sha256": sha, "n_chunks": len(chunks), "title": title or None, "doi": doi or None,
                 "pages": read.pages or None, "status": STATUS_OK if chunks else STATUS_NO_TEXT,
                 "chunker_version": CHUNKER_VERSION}
        if not chunks and not read.readable:
            entry["reason"] = "unreadable"
        return entry, chunks

    # -- the locked read-modify-write ----------------------------------------------------------------

    def _commit(self, entries: Mapping[str, dict[str, Any]], chunks: list[dict[str, Any]],
                removed: set[str]) -> dict[str, Any]:
        """Replace the chunks and ``docs`` entries of ``entries``, prune ``removed``; one atomic write."""
        assert self.index_dir is not None
        with index_lock(self.index_dir):
            current = SourceIndex(self.index_dir)
            old_meta = current.meta()
            existing = current.load() if current.available else []
            docs = {k: v for k, v in (old_meta.get("docs") or {}).items() if isinstance(v, dict)} \
                if isinstance(old_meta.get("docs"), dict) else {}
            replace = set(entries) | removed
            merged = merge_chunks(existing, chunks, replace_doc_ids=replace)
            for doc_id in removed:
                docs.pop(doc_id, None)
            docs.update(entries)
            extra = {k: v for k, v in old_meta.items() if k not in _GENERATED_META}
            if extra.get("status") == "no_pdfs":
                extra.pop("status")
            extra["docs"] = docs
            extra["library_revision"] = _utcnow()
            return write_index(self.index_dir, merged, index_name=str(old_meta.get("index_name") or "literature"),
                               corpus_revision=f"library-{extra['library_revision']}", extra_meta=extra)

    # -- the job ---------------------------------------------------------------------------------------

    def run(self, ctx: StageContext) -> StageResult:
        """The ``library_index`` stage body (see the module docstring)."""
        from ..contracts.errors import ContractError, ErrorCode

        if self.index_dir is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "no source index directory is configured (HYBRID_RAG_LITERATURE_INDEX)")
        root = self.service._require_root()  # noqa: SLF001 - one trusted package
        self.service.refresh(max_probe=0)  # hash new and changed files; no probe, no network
        rows = {r["relpath"]: r for r in self.service.catalog.rows()}
        docs = self.docs_record()
        current_ids = {doc_id_for(rel) for rel in rows}
        removed = {d for d in docs if d.startswith(DOC_PREFIX) and d not in current_ids}
        todo = [r for r in rows.values() if self._needs_work(r, docs)]
        counts = {"indexed": 0, "no_text": 0, "chunks": 0, "removed": len(removed),
                  "unchanged": len(rows) - len(todo)}
        if not todo and not removed:
            self.sync_catalog()
            ctx.progress(units_done=0, units_total=0, **counts)
            ctx.log(f"library index unchanged: {len(rows)} papers")
            return StageResult(progress={"units_done": 0, "units_total": 0, **counts}, data={"changed": False})

        pending_entries: dict[str, dict[str, Any]] = {}
        pending_chunks: list[dict[str, Any]] = []
        pending_removed = set(removed)

        def flush() -> None:
            nonlocal pending_chunks, pending_removed
            if not pending_entries and not pending_removed:
                return
            self._commit(pending_entries, pending_chunks, pending_removed)
            pending_entries.clear()
            pending_chunks = []
            pending_removed = set()

        def process_row(row: Mapping[str, Any]) -> dict[str, Any]:
            entry, chunks = self._chunk_one(root, row)
            doc_id = doc_id_for(row["relpath"])
            pending_entries[doc_id] = entry
            pending_chunks.extend(chunks)
            counts["indexed" if entry["status"] == STATUS_OK else "no_text"] += 1
            counts["chunks"] += len(chunks)
            if len(pending_entries) >= self.flush_every:
                flush()
            return {"doc_id": doc_id, "sha256": entry["sha256"], "status": entry["status"],
                    "n_chunks": entry["n_chunks"], "pages": entry["pages"]}

        def units(_sc: StageContext) -> list[WorkUnit]:
            return [WorkUnit(unit_id=r["id"], unit_key=f"{r['sha256']}:{CHUNKER_VERSION}", payload=r["relpath"])
                    for r in todo]

        def process(_sc: StageContext, unit: WorkUnit) -> Mapping[str, Any]:
            return process_row(rows[unit.payload])

        try:
            result = chunked_stage(units, process)(ctx)
            # A unit checkpointed by an interrupted run is skipped above but its chunks never reached the
            # index: whatever the record still lacks is indexed here, without a checkpoint.
            flush()
            leftover = [r for r in todo if self._needs_work(r, self.docs_record())]
            failed = set(result.incomplete)
            for row in leftover:
                if row["id"] in failed:
                    continue
                ctx.guard()
                process_row(row)
        finally:
            flush()  # also on cancel or failure: finished papers are kept
        self.sync_catalog()
        ctx.log(f"library index: {counts['indexed']} indexed, {counts['no_text']} no text, "
                f"{counts['unchanged']} unchanged, {counts['removed']} removed, {counts['chunks']} chunks")
        return StageResult(progress={**dict(result.progress), **counts},
                           data={"changed": True, "no_text": counts["no_text"]}, incomplete=result.incomplete)


__all__ = ("CORPUS", "DOC_PREFIX", "LibraryIndexer", "doc_id_for", "work_id_for")
