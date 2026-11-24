"""LibraryService: catalog, upload and verify of the mounted papers folder (A8, T35; supersedes T32).

The mount is ``F2W_LIBRARY_DIR`` (trusted startup setting, ``:local`` only). Root resolution,
containment and the recursive PDF inventory are **the T32 folder machinery**
(:class:`app.api.folders.FolderRoots`, label ``library``): there is no second listing implementation.
This module adds what a catalog needs on top:

* **Local metadata, no network.** New files are hashed and probed (:mod:`.probe`, in a child process with
  a timeout) for the PDF Info title; DOIs come from the first two pages and the file name
  (:func:`~app.modules.literature.intake.extract_doi_candidates` /
  :func:`~app.modules.literature.intake.filename_doi_candidates`). At most ``max_probe`` files are probed per
  refresh so a first listing of a large folder cannot hold a request for minutes; the rest report
  ``metadata_status="pending"`` and fill in on later listings.
* **Upload** into the mounted folder: ``require_pdf`` and ``safe_child`` from ``upload_limits``, sha256 dedupe
  (a duplicate returns the existing paper and writes nothing), a no-clobber atomic write (temporary file in
  the same folder, ``link`` into place), never through or over a link.
* **Verify**: :func:`~app.modules.literature.intake.prepare_local_pdf` with ``verify="openalex"`` over the
  existing quota breaker; settled results (``verified``, ``not_found``) are cached by sha256 in the
  catalog, an open breaker or an outage is reported and not cached.

Nothing here returns PDF bytes or an absolute path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any
import unicodedata
import uuid

from fastapi import HTTPException

from ...api.folders import FolderRoots
from ...api.upload_limits import DEFAULT_MAX_BODY_BYTES, require_pdf, safe_child
from ..contracts.context import ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..literature.intake import Fetch, extract_doi_candidates, filename_doi_candidates, prepare_local_pdf
from ..literature.openalex import (
    DEFAULT_BREAKER,
    OpenAlexClient,
    OpenAlexSettings,
    QuotaBreaker,
    ResponseCache,
    default_http_client,
)
from ..metrics import counted
from ..security.authorization import StartupConfigError, local_operator_context
from .arrange import category_problem, guess_year, is_valid_folder_name, year_folder
from .bibmeta import openalex_abstract, openalex_authors
from .catalog import LibraryCatalog, sha256_file
from .manifest import PDF_PATH_PREFIX, fill_row_metadata, record_upload, remove_upload
from .metadata import ManifestMetadata
from .probe import DEFAULT_TIMEOUT_SECONDS, Probe, ProbeResult, isolated_probe

if TYPE_CHECKING:
    from .estimate import PaperFacts

logger = logging.getLogger(__name__)

#: Trusted startup setting naming the mounted papers folder (absolute path).
LIBRARY_DIR_ENV = "F2W_LIBRARY_DIR"
#: Trusted startup setting naming the literature index directory the library is chunked into (T36; the same
#: variable ``hybrid_rag`` reads, absolute path, ``:local`` profile only).
INDEX_DIR_ENV = "HYBRID_RAG_LITERATURE_INDEX"
#: The ``FolderRoots`` label of the mount (never shown to a client).
ROOT_LABEL = "library"
#: Files probed per refresh at most (the rest stay ``pending``).
DEFAULT_MAX_PROBE = 20
#: Longest accepted upload file name.
MAX_NAME_CHARS = 200
#: Fixed message when no folder is mounted.
NOT_MOUNTED = "no papers folder is mounted (F2W_LIBRARY_DIR)"

#: ``prepare_local_pdf`` outcomes that mean the registry has no matching work (settled, cached).
_NOT_FOUND = frozenset({"skipped:no_doi", "skipped:doi_unresolved", "skipped:title_mismatch",
                        "skipped:filename_mismatch"})


class VerifyUnavailable(Exception):
    """The registry lookup cannot run at all (for example no OpenAlex key); the message is shown to the operator."""


class _NullStore:
    """``prepare_local_pdf`` only asks ``exists`` when ``apply`` is false; nothing is ever stored."""

    def exists(self, ctx: object, ref: str) -> bool:
        """Never a duplicate: the library has no candidate store."""
        return False


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds") if epoch else None


def index_status_of(row: Mapping[str, Any]) -> str:
    """``indexed`` / ``no_text`` for the bytes that were indexed, ``stale`` when the bytes changed since, else ``not_indexed``."""
    status = row.get("index_status")
    if not status:
        return "not_indexed"
    if row.get("indexed_sha256") != row.get("sha256"):
        return "stale"
    return "indexed" if status == "ok" else str(status)


def clean_upload_name(filename: str | None) -> str:
    """The plain ``*.pdf`` file name of an uploaded name (directories dropped, ``.pdf`` appended when missing).

    Raises:
        HTTPException: 422 for an empty, over-long or control-character name.
    """
    raw = unicodedata.normalize("NFC", (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip())
    if not raw or len(raw) > MAX_NAME_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise HTTPException(status_code=422, detail="unsafe file name")
    return raw if raw.lower().endswith(".pdf") else raw + ".pdf"


def folder_of(relpath: str) -> str:
    """Category of a paper: its top-level folder under the library root (``rsoxs`` for ``rsoxs/2013/x.pdf``).

    Files directly in the root have no category and report ``""``.
    """
    parts = relpath.split("/")
    return parts[0] if len(parts) > 1 else ""


def library_dir_from(startup: Mapping[str, str]) -> Path | None:
    """The mounted papers folder named by ``F2W_LIBRARY_DIR`` (no built-in default: ``None`` when unset).

    Raises:
        StartupConfigError: The value is not an absolute path.
    """
    raw = (startup.get(LIBRARY_DIR_ENV) or "").strip()
    library_dir = Path(raw).expanduser() if raw else None
    if library_dir is not None and not library_dir.is_absolute():
        raise StartupConfigError(f"config_rejected: {LIBRARY_DIR_ENV} must be an absolute path")
    return library_dir


class LibraryService:
    """Catalog, upload and verify over one mounted papers folder (see the module docstring).

    Args:
        library_dir: The mount (absolute), or ``None`` for "not mounted".
        catalog_path: The sidecar ``catalog.sqlite`` in the state volume.
        settings: Startup ``DeploymentSettings`` (the worker context of a verify job is built from it).
        probe: ``path -> ProbeResult``; defaults to the isolated child-process probe.
        fetch_factory: ``() -> fetch`` for OpenAlex (raises ``VerifyUnavailable`` when it cannot run); lazy.
        breaker: The OpenAlex quota breaker whose state the status reports.
        max_upload_bytes: Upload size cap.
        max_probe: Files probed per refresh.
        clock: Epoch seconds, injectable.
        index_dir: The literature index directory ``library_index`` writes (``None``: the job fails closed).
        chunker: PDF reader and chunker of ``library_index`` (default ``hybrid_rag.chunker.chunk_pdf_detailed``).
    """

    def __init__(self, library_dir: Path | None, catalog_path: Path, *, settings: Any = None, probe: Probe | None = None,  # noqa: ANN401  # Any: None for catalog-only construction; worker_context needs it
                 fetch_factory: Callable[[], Fetch] | None = None, breaker: QuotaBreaker | None = None,
                 max_upload_bytes: int = DEFAULT_MAX_BODY_BYTES, max_probe: int = DEFAULT_MAX_PROBE,
                 clock: Callable[[], float] = time.time, index_dir: Path | None = None,
                 chunker: Callable[..., Any] | None = None) -> None:
        self.roots = FolderRoots.from_paths({ROOT_LABEL: library_dir}) if library_dir else FolderRoots()
        self.catalog = LibraryCatalog(catalog_path, clock=clock)
        self.settings = settings
        self._probe: Probe = probe or (lambda p: isolated_probe(p, timeout=DEFAULT_TIMEOUT_SECONDS))
        self._fetch_factory = fetch_factory
        self._fetch: Fetch | None = None
        self.breaker = breaker if breaker is not None else DEFAULT_BREAKER
        self.max_upload_bytes = int(max_upload_bytes)
        self.max_probe = int(max_probe)
        self._clock = clock
        self._refresh_lock = threading.RLock()
        self._upload_lock = threading.Lock()
        self._stored_metadata = ManifestMetadata()
        self.index_dir = Path(index_dir) if index_dir else None
        self._chunker = chunker
        self._indexer: Any = None
        #: Polite-pool contact for OpenAlex lookups the harvester makes (``""``: none); set by :meth:`from_startup`.
        self.contact_email = ""

    @property
    def indexer(self) -> Any:  # noqa: ANN401  # Any: donor indexer (untyped legacy class)
        """The :class:`~app.modules.library.indexer.LibraryIndexer` over this service (T36)."""
        if self._indexer is None:
            from .indexer import LibraryIndexer

            self._indexer = LibraryIndexer(self, self.index_dir, chunker=self._chunker)
        return self._indexer

    # -- construction ---------------------------------------------------------------------------

    @classmethod
    def from_startup(cls, startup: Mapping[str, str], workdir: Path, settings: Any, **kw: Any) -> LibraryService:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """Build from the trusted startup mapping: ``F2W_LIBRARY_DIR``, ``HYBRID_RAG_LITERATURE_INDEX`` and the OpenAlex settings.

        The catalog and the OpenAlex response cache live under ``<workdir>/library/``.

        Raises:
            StartupConfigError: ``F2W_LIBRARY_DIR`` or ``HYBRID_RAG_LITERATURE_INDEX`` is not an absolute path.
        """
        library_dir = library_dir_from(startup)
        state = Path(workdir) / "library"
        raw_index = (startup.get(INDEX_DIR_ENV) or "").strip()
        index_dir = Path(raw_index).expanduser() if raw_index else None
        if index_dir is not None and not index_dir.is_absolute():
            raise StartupConfigError(f"config_rejected: {INDEX_DIR_ENV} must be an absolute path")
        kw.setdefault("index_dir", index_dir)
        breaker = kw.pop("breaker", None)
        kw.setdefault("fetch_factory", _openalex_fetch_factory(
            startup, state / "openalex_cache.sqlite", breaker if breaker is not None else DEFAULT_BREAKER))
        service = cls(library_dir, state / "catalog.sqlite", settings=settings, breaker=breaker, **kw)
        service.contact_email = OpenAlexSettings.from_mapping(startup).polite_mailto
        return service

    # -- the mount -------------------------------------------------------------------------------

    def _root(self) -> Path | None:
        """The resolved mount directory, or ``None`` when absent (the T32 containment applies)."""
        if not self.roots.configured:
            return None
        try:
            return self.roots.resolve(ROOT_LABEL, None)[0]
        except ContractError:  # no configured library root
            return None

    @property
    def mounted(self) -> bool:
        """Whether the papers folder exists right now."""
        return self._root() is not None

    def _require_root(self) -> Path:
        root = self._root()
        if root is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, NOT_MOUNTED)
        return root

    # -- catalog refresh -------------------------------------------------------------------------

    @staticmethod
    def _doi_from(name: str, probe: ProbeResult) -> tuple[str | None, str | None]:
        """First DOI printed in the PDF, else one the file name implies (candidates; ``verify`` checks them)."""
        printed = extract_doi_candidates(probe.text) if probe.text else []
        if printed:
            return printed[0], "pdf"
        named = filename_doi_candidates(name)
        return (named[0], "filename") if named else (None, None)

    def _record(self, path: Path, relpath: str, sha256: str, size: int, mtime: float, *, probe: bool,
                result: ProbeResult | None = None) -> None:
        title: str | None = None
        doi: str | None = None
        source: str | None = None
        status = "pending"
        pages: int | None = None
        if probe:
            result = result or self._probe(path)
            title = result.title or None
            doi, source = self._doi_from(path.name, result)
            status = result.status
            pages = result.pages or None
        self.catalog.upsert_paper(relpath=relpath, sha256=sha256, size=size, mtime=mtime, title=title, doi=doi,
                                  doi_source=source, meta_status=status, pages=pages)

    @counted("library", "refresh")
    def refresh(self, *, max_probe: int | None = None) -> dict[str, Any]:
        """Bring the catalog in line with the folder: hash new or changed files, probe up to ``max_probe`` of them.

        Returns:
            ``{"mounted", "count", "pending", "truncated"}``.
        """
        root = self._root()
        if root is None:
            return {"mounted": False, "count": 0, "pending": 0, "truncated": False}
        limit = self.max_probe if max_probe is None else max_probe
        policy = self.roots.policy(ROOT_LABEL)
        with self._refresh_lock:
            inventory = self.roots.index_folder(ROOT_LABEL, None)
            keep: list[str] = []
            probed = pending = 0
            for doc in inventory["documents"]:
                rel = str(doc["relative_path"])
                try:
                    path = policy.require_allowed(root / rel)
                except ValueError:
                    continue  # a link or an escape appeared since the walk: not a library paper
                row = self.catalog.paper(rel)
                if row is None or row["size"] != doc["size"] or abs(row["mtime"] - doc["modified"]) > 0.0015:
                    try:
                        sha = sha256_file(path)
                    except OSError as exc:
                        logger.debug("library paper unreadable; not hashed: %s", type(exc).__name__)
                        continue
                    self._record(path, rel, sha, int(doc["size"]), float(doc["modified"]), probe=False)
                    row = self.catalog.paper(rel)
                keep.append(rel)
                if row is not None and row["meta_status"] == "pending":
                    if probed < limit:
                        probed += 1
                        self._record(path, rel, row["sha256"], row["size"], row["mtime"], probe=True)
                    else:
                        pending += 1
            if not inventory["truncated"]:
                self.catalog.prune(keep)
            return {"mounted": True, "count": len(keep), "pending": pending, "truncated": bool(inventory["truncated"])}

    # -- DTOs --------------------------------------------------------------------------------------

    @staticmethod
    def _dto(row: Mapping[str, Any], override: Mapping[str, Any] | None = None) -> dict[str, Any]:
        status = row.get("verify_status") or "unverified"
        verified = status == "verified"
        out: dict[str, Any] = {
            "id": row["id"], "relpath": row["relpath"], "file_name": Path(row["relpath"]).name,
            "folder": folder_of(row["relpath"]), "sha256": row["sha256"], "size_bytes": row["size"],
            "title": row.get("verified_title") if verified and row.get("verified_title") else row["title"],
            "doi": row.get("verified_doi") if verified and row.get("verified_doi") else row["doi"],
            "verify_status": status, "verified_at": _iso(row.get("verified_at")),
            "index_status": index_status_of(row), "pages": row.get("pages"), "verify_detail": row.get("verify_detail"),
            "metadata_status": row["meta_status"],
        }
        out.update(override or {})
        return out

    def paper_facts(self, paper_id: str) -> PaperFacts | None:
        """Facts the T38 cost estimate needs of one paper (``PaperLookup``); ``None`` for an unknown id.

        ``pages`` is the catalog's page count (filled by the metadata probe or the index run); ``None`` while
        unknown, and the estimate then reports the paper under ``unknown_pages``.
        """
        from .estimate import PaperFacts

        row = self.catalog.paper_by_id(paper_id)
        if row is None:
            return None
        return PaperFacts(id=row["id"], name=Path(row["relpath"]).name, sha256=row["sha256"],
                         pages=int(row["pages"]) if row.get("pages") else None)

    def _paper_dto(self, relpath: str, override: Mapping[str, Any] | None = None) -> dict[str, Any]:
        for row in self.catalog.rows():
            if row["relpath"] == relpath:
                return self._dto(row, override)
        raise ContractError(ErrorCode.NOT_FOUND, "paper not found")

    # -- reads -------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """``{mounted, paper_count, indexed_count, quota_open}``; a cheap walk, no hashing or parsing."""
        quota_open = self.breaker.state() is not None
        if self._root() is None:
            return {"mounted": False, "paper_count": 0, "indexed_count": 0, "quota_open": quota_open}
        inventory = self.roots.index_folder(ROOT_LABEL, None)
        return {"mounted": True, "paper_count": int(inventory["count"]),
                "indexed_count": self.catalog.indexed_count(), "quota_open": quota_open}

    def list_papers(self) -> dict[str, Any]:
        """Every paper of the folder with its local or verified title and DOI, plus the stored year, authors and abstract.

        The last three come from the category manifests (``year`` also from the year folder); a field no source holds
        is ``None`` and listed in the paper's ``metadata_missing`` (see :mod:`.metadata`).
        """
        info = self.refresh()
        root = self._root()
        papers = [self._dto(r) for r in self.catalog.rows()] if info["mounted"] else []
        if papers and root is not None:  # year, authors and abstract as stored in the category manifests
            stored = self._stored_metadata.index(root)
            for dto in papers:
                dto.update(stored.describe(dto["relpath"], dto["sha256"], dto["doi"]))
        return {"papers": papers, "total": len(papers), "pending_metadata": info["pending"],
                "truncated": info["truncated"]}

    # -- categories ----------------------------------------------------------------------------------

    def list_categories(self) -> dict[str, Any]:
        """The categories (top-level folders of the library, empty ones included) with their paper counts.

        Returns:
            ``{"categories": [{"name", "count"}]}`` sorted by name, case-insensitively; ``[]`` when nothing is mounted.
        """
        root = self._root()
        if root is None:
            return {"categories": []}
        counts: dict[str, int] = {}
        for row in self.catalog.rows():
            name = folder_of(row["relpath"])
            if name:
                counts[name] = counts.get(name, 0) + 1
        names = {n for n in counts if is_valid_folder_name(n)}
        try:
            names |= {c.name for c in root.iterdir()
                      if is_valid_folder_name(c.name) and c.is_dir() and not c.is_symlink()}
        except OSError as exc:
            logger.warning("category listing could not read the library folder: %s", exc)
        return {"categories": [{"name": n, "count": counts.get(n, 0)} for n in sorted(names, key=str.lower)]}

    @counted("library", "create_category")
    def create_category(self, raw: object) -> dict[str, Any]:
        """Create the category ``raw`` (an empty top-level folder); an existing one (any case) is returned as is.

        Returns:
            ``{"name", "created", "count"}``: the stored spelling, whether this call created it, its paper count.

        Raises:
            ContractError: ``INVALID_INPUT`` for a bad name or a name taken by something that is not a real folder;
                ``UNSUPPORTED_CAPABILITY`` when nothing is mounted.
        """
        problem = category_problem(raw)
        if problem is not None:
            raise ContractError(ErrorCode.INVALID_INPUT, problem)
        name = str(raw).strip()
        root = self._require_root()
        with self._upload_lock:
            for child in root.iterdir():
                if child.name.lower() == name.lower():
                    if child.is_dir() and not child.is_symlink():
                        existing = next((c for c in self.list_categories()["categories"] if c["name"] == child.name), None)
                        return {"name": child.name, "created": False, "count": existing["count"] if existing else 0}
                    raise ContractError(ErrorCode.INVALID_INPUT, "that name is taken by something that is not a folder")
            (root / name).mkdir()
        return {"name": name, "created": True, "count": 0}

    # -- upload ------------------------------------------------------------------------------------

    @counted("library", "upload")
    def upload(self, filename: str | None, data: bytes, folder: str | None = None, *,
               known: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Store one PDF in the mounted folder and catalogue it.

        The bytes are already read (``read_upload_limited``); this checks the PDF magic, the name and the
        sha256 duplicate, then writes atomically without ever replacing or following an existing entry.

        With ``folder`` the paper is filed under that category in a sub-folder of its publication year
        (``rsoxs/2013/x.pdf``; ``rsoxs/unknown/x.pdf`` when no year can be read locally), a new category is
        created on first use, and the category's ``manifest.json`` gets a row for it. Without ``folder`` the
        paper goes into the library root, as before.

        ``known`` is what the caller already knows of the paper (the harvester has it from the registry):
        ``year`` replaces the local year guess, and ``title``, ``doi``, ``source``, ``authors`` and ``abstract``
        fill the manifest row (authors and abstract stay in the manifest; they are never logged).

        Returns:
            ``{"paper": dto, "duplicate": bool}``; a duplicate writes nothing. A filed paper also carries
            ``"manifest"``: ``{"updated", "path", "detail"}``.

        Raises:
            HTTPException: 413 (too large), 415 (not a PDF), 422 (unsafe name or folder).
            ContractError: ``UNSUPPORTED_CAPABILITY`` when nothing is mounted, ``CONFLICT`` when no free name remains.
        """
        if len(data) > self.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"upload exceeds the {self.max_upload_bytes} byte limit")
        require_pdf(data)
        name = clean_upload_name(filename)
        category = (folder or "").strip()
        if category and not is_valid_folder_name(category):
            raise HTTPException(status_code=422, detail="unsafe folder name")
        root = self._require_root()
        sha = hashlib.sha256(data).hexdigest()
        with self._upload_lock:
            self.refresh(max_probe=0)  # known hashes of what is already in the folder
            existing = self.catalog.paper_by_sha(sha)
            if existing is not None:
                return {"paper": self._paper_dto(existing["relpath"]), "duplicate": True}
            directory, result, year = root, None, None
            if category:
                result = self._probe_bytes(root, data)
                year = known.get("year") if known and known.get("year") else None
                year = int(year) if year else guess_year(name, result.page_text, result.meta_text)
                directory = self._ensure_folder(root, category, year_folder(year))
            stem, suffix = name[:-4], name[-4:]
            target: Path | None = None
            for attempt in range(100):
                candidate = f"{stem}{'' if attempt == 0 else f' ({attempt})'}{suffix}"
                if os.path.lexists(directory / candidate):
                    continue  # a file, a link or a dangling link already has this name: never replaced
                path = safe_child(directory, candidate)  # 422 for separators, dot-names, escapes
                if self._write_new(directory, path, data):
                    target = path
                    break
            if target is None:
                raise ContractError(ErrorCode.CONFLICT, "no free file name for this upload", retryable=False)
            stat = target.stat()
            relpath = target.relative_to(root).as_posix()
            self._record(target, relpath, sha, stat.st_size, round(stat.st_mtime, 3), probe=True, result=result)
            if known:
                self.catalog.fill_identity(relpath, title=known.get("title"), doi=known.get("doi"),
                                           source=str(known.get("source") or "upload"))
            out: dict[str, Any] = {"paper": self._paper_dto(relpath), "duplicate": False}
            if category:
                paper = out["paper"]
                out["manifest"] = record_upload(
                    root, relpath.split("/", 1)[0], relpath=relpath, sha256=sha,
                    doi=(known or {}).get("doi") or paper.get("doi"), title=(known or {}).get("title") or paper.get("title"),
                    year=year, now=datetime.now(UTC).isoformat(), source=str((known or {}).get("source") or "upload"),
                    arxiv_id=(known or {}).get("arxiv_id"),
                    authors=(known or {}).get("authors"), abstract=(known or {}).get("abstract"),
                    provenance=(known or {}).get("provenance"), supplied_by=(known or {}).get("supplied_by"))
            return out

    def remove_filed(self, relpath: str, sha256: str) -> dict[str, Any]:
        """Undo one filed upload: delete that file, its manifest row and its catalog row (nothing else).

        The file is removed only when it is a regular file (never a link) under the library root whose sha256 is the
        given one; any other file, even at the same path, is left alone. The category folder and its other papers are
        untouched.

        Returns:
            ``{"file": bool, "manifest": bool}``: what was removed.

        Raises:
            ContractError: ``UNSUPPORTED_CAPABILITY`` when nothing is mounted.
        """
        root = self._require_root()
        category = relpath.split("/", 1)[0]
        target = root / relpath
        removed_file = False
        with self._upload_lock:
            if (not target.is_symlink() and target.is_file() and target.resolve().is_relative_to(root.resolve())
                    and hashlib.sha256(target.read_bytes()).hexdigest() == sha256):
                target.unlink()
                removed_file = True
            removed_row = remove_upload(root, category, sha256=sha256) if "/" in relpath else False
            self.refresh(max_probe=0)
        return {"file": removed_file, "manifest": removed_row}

    def _probe_bytes(self, root: Path, data: bytes) -> ProbeResult:
        """Probe ``data`` through a hidden temporary file in ``root`` (the probe reads paths)."""
        tmp = root / f".probe-{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            return self._probe(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _ensure_folder(root: Path, category: str, year: str) -> Path:
        """``root/<category>/<year>``, created on demand; an existing category matches case-insensitively.

        Raises:
            HTTPException: 422 when a path component exists as something other than a real directory.
        """
        for child in root.iterdir():
            if child.name.lower() == category.lower() and child.is_dir() and not child.is_symlink():
                category = child.name
                break
        directory = root
        for part in (category, year):
            directory = directory / part
            if directory.is_symlink() or (os.path.lexists(directory) and not directory.is_dir()):
                raise HTTPException(status_code=422, detail="unsafe folder name")
            directory.mkdir(exist_ok=True)
        return directory

    @staticmethod
    def _write_new(root: Path, target: Path, data: bytes) -> bool:
        """Write ``data`` to a hidden temporary file in ``root`` and link it to ``target`` (no clobber).

        Returns ``False`` when ``target`` appeared meanwhile (the caller picks another name).
        """
        tmp = root / f".upload-{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o644)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, target)  # atomic, fails if the name exists
            except FileExistsError:  # another writer already took the name
                return False
            except OSError:  # a filesystem without hard links: rename after an existence check
                if os.path.lexists(target):
                    return False
                tmp.rename(target)
            return True
        finally:
            tmp.unlink(missing_ok=True)

    # -- verify ------------------------------------------------------------------------------------

    def _get_fetch(self) -> Fetch:
        if self._fetch is None:
            if self._fetch_factory is None:
                raise VerifyUnavailable("no registry lookup is configured")
            self._fetch = self._fetch_factory()
        return self._fetch

    def worker_context(self, request_id: str) -> RequestContext:
        """The trusted-local worker context used for a verify run outside a request."""
        return local_operator_context(self.settings, request_id=request_id, actor_kind=ActorKind.WORKER,
                                      run_id=request_id)

    @counted("library", "verify_paper")
    def verify_paper(self, pid: str, ctx: RequestContext, *, force: bool = False) -> dict[str, Any]:
        """Verify one paper against OpenAlex and cache a settled result by sha256.

        A cached ``verified`` / ``not_found`` result is returned without a lookup unless ``force``. An open
        quota breaker answers ``verify_status="quota_open"`` and an unreachable registry ``"error"``;
        neither is cached.

        Raises:
            ContractError: ``NOT_FOUND`` for an unknown id, ``UNSUPPORTED_CAPABILITY`` when nothing is mounted.
        """
        root = self._require_root()
        self.refresh(max_probe=0)
        row = self.catalog.paper_by_id(pid)
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "paper not found")
        return {"paper": self._verify_row(root, row, ctx, force=force)}

    def _verify_row(self, root: Path, row: Mapping[str, Any], ctx: RequestContext, *, force: bool) -> dict[str, Any]:
        rel, sha = str(row["relpath"]), str(row["sha256"])
        if self.catalog.verification(sha) is not None and not force:
            return self._paper_dto(rel)
        try:
            fetch = self._get_fetch()
        except VerifyUnavailable as exc:
            return self._paper_dto(rel, {"verify_status": "error", "verify_detail": str(exc)})
        failure: dict[str, str] = {}

        def extractor(path: Path, pages: int) -> str:
            result = self._probe(path)
            if not result.ok:
                failure["status"] = result.status
                return ""
            return result.text

        report = prepare_local_pdf(
            ctx, root / rel, store=_NullStore(), fetch=fetch, text_extractor=extractor,  # type: ignore[arg-type]
            apply=False, verify="openalex", allowed=self.roots.policy(ROOT_LABEL).require_allowed)
        if report.ok:
            self.catalog.save_verification(sha, status="verified", title=report.title or None,
                                           doi=report.doi or None, detail=None)
            self._store_work_metadata(root, rel, sha, report.work)
            return self._paper_dto(rel)
        if report.status == "skipped:openalex_quota_paused" or (report.retryable and self.breaker.state() is not None):
            return self._paper_dto(rel, {"verify_status": "quota_open",
                                         "verify_detail": report.detail or "OpenAlex quota is paused"})
        if failure:
            return self._paper_dto(rel, {"verify_status": "error",
                                         "verify_detail": f"PDF could not be read ({failure['status']})"})
        if report.status in _NOT_FOUND:
            self.catalog.save_verification(sha, status="not_found", title=None, doi=None, detail=report.detail or None)
            return self._paper_dto(rel)
        return self._paper_dto(rel, {"verify_status": "error", "verify_detail": report.detail or report.status})

    @staticmethod
    def _store_work_metadata(root: Path, relpath: str, sha256: str, work: Mapping[str, Any] | None) -> None:
        """Keep the authors and abstract of the verified OpenAlex work in the paper's manifest row (empty fields only)."""
        if not work or "/" not in relpath:
            return
        category = relpath.split("/", 1)[0]
        path = f"{PDF_PATH_PREFIX}/{relpath}"
        fill_row_metadata(root / category / "manifest.json",
                          lambda r: r.get("pdf_path") == path or r.get("pdf_sha256") == sha256,
                          authors=openalex_authors(work), abstract=openalex_abstract(work))

    @counted("library", "verify_all")
    def verify_all(self, ctx: RequestContext, *, progress: Callable[..., None] | None = None,
                   log: Callable[..., None] | None = None, guard: Callable[[], None] | None = None) -> dict[str, Any]:
        """Verify every paper with no settled result, stopping when the quota breaker opens.

        Returns:
            ``{"verified", "not_found", "errors", "remaining": [paper ids], "quota_open": bool}``.
        """
        root = self._require_root()
        self.refresh()
        todo = [r for r in self.catalog.rows() if r["verify_status"] is None]
        counts = {"verified": 0, "not_found": 0, "errors": 0}
        remaining: list[str] = []
        quota_open = False
        for index, row in enumerate(todo):
            if guard is not None:
                guard()
            if quota_open:
                remaining.append(row["id"])
                continue
            dto = self._verify_row(root, row, ctx, force=False)
            status = dto["verify_status"]
            if status == "quota_open":
                quota_open = True
                remaining.append(row["id"])
                if log is not None:
                    log("OpenAlex quota breaker is open; stopping verification", level="warning")
            elif status in ("verified", "not_found"):
                counts[status] += 1
            else:
                counts["errors"] += 1
                if log is not None:
                    log(f"{row['relpath']}: {dto.get('verify_detail') or status}", level="warning")
            if progress is not None:
                progress(done=index + 1, total=len(todo), **counts)
        return {**counts, "remaining": remaining, "quota_open": quota_open}


def _openalex_fetch_factory(startup: Mapping[str, str], cache_path: Path,
                            breaker: QuotaBreaker) -> Callable[[], Fetch]:
    """A lazy ``() -> fetch`` over :class:`OpenAlexClient` (key and contact from the startup mapping)."""
    def factory() -> Fetch:
        settings = OpenAlexSettings.from_mapping(startup)
        if not settings.api_key:
            raise VerifyUnavailable("OPENALEX_API_KEY is not configured")
        client = OpenAlexClient(default_http_client(settings, timeout=15.0), cache=ResponseCache(cache_path),
                                breaker=breaker, settings=settings)
        return client.fetch

    return factory


__all__ = ("DEFAULT_MAX_PROBE", "INDEX_DIR_ENV", "LIBRARY_DIR_ENV", "LibraryService", "NOT_MOUNTED", "ROOT_LABEL", "VerifyUnavailable",
           "clean_upload_name", "index_status_of")
