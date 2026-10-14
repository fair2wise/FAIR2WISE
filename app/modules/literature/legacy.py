"""Reversible legacy manifest: old filenames and caches mapped onto the content-addressed store.

F2W stores acquired PDFs today under filename conventions, not content hashes:

* ``DownloadAgent`` and ``download_pdfs``: ``<target>/<doi with "/" -> "_">.pdf``, with
  a sibling ``downloads.jsonl`` attempt log (B22);
* ``scripts/harvest_rsoxs.py``: ``papers/rsoxs/<year>/<doi_stem>.pdf`` plus
  ``papers/rsoxs/manifest.json`` rows carrying ``doi``, ``pdf_path`` and
  ``pdf_sha256`` (B23);
* the FORGE donor caches: ``_safe_stem`` (every non-word character -> ``_``) and
  ``_legacy_stem`` (``/`` and ``.`` -> ``_``), case varying by publisher.

This index records, for every legacy file it has seen, ``root/relative path -> ref``
and ``doi -> refs``. Nothing is moved: files are either *referenced* in place
(their durable root is kept until an approved move) or *copied* into the store.
The mapping is exported whole (:meth:`LegacyIndex.export`) and every entry can be
written back to its original path (:meth:`LegacyIndex.restore`), so a figure or
manifest reference that names an old path keeps resolving.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``read_manifest``,
``record_in_manifest``, ``duplicate_stems``, ``_cached_pdf_for``, ``_safe_stem``,
``_legacy_stem``, ``_doi_only``) (BSD-3-Clause).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from ..contracts.context import RequestContext
from ..contracts.errors import ErrorCode
from .store import AssetKind, FileDocumentStore, StoredAsset, StoreError, atomic_write_json, sha256_of

try:  # POSIX advisory lock for cross-process index writers; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

LEGACY_SCHEMA_VERSION = 1
PDF_MAGIC = b"%PDF-"
_ROOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def doi_only(value: object) -> str:
    """A DOI reduced to its identifying part (``doi.org`` URL / ``doi:`` prefix removed).

    Adapted from donor ``paper_reading._doi_only``; validates nothing.
    """
    text = str(value or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/",
                   "doi.org/", "doi:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    return text.strip("/ ")


def doi_key(value: object) -> str:
    """Lowercased bare DOI, or ``""`` when ``value`` is not a DOI."""
    bare = doi_only(value).lower()
    return bare if bare.startswith("10.") and "/" in bare else ""


def f2w_stem(doi: str) -> str:
    """F2W's filename stem (``DownloadAgent._safe_name``, ``harvest_rsoxs.doi_stem``)."""
    return doi_only(doi).replace("/", "_")


def safe_stem(doi: str) -> str:
    """Donor ``_safe_stem``: every non-word character becomes ``_``."""
    return re.sub(r"[^\w]", "_", doi_only(doi))


def legacy_stem(doi: str) -> str:
    """Donor ``_legacy_stem``: ``/`` and ``.`` become ``_``."""
    return doi_only(doi).replace("/", "_").replace(".", "_")


def legacy_filenames(doi: str) -> List[str]:
    """Every PDF filename a legacy cache may have used for ``doi``, lowercased, best first."""
    if not doi_key(doi):
        return []
    names: List[str] = []
    for stem in (f2w_stem(doi), safe_stem(doi), legacy_stem(doi)):
        name = f"{stem}.pdf".lower()
        if name not in names:
            names.append(name)
    return names


def contained_regular_file(root: Path, path: Path) -> bool:
    """Whether ``path`` is a regular file inside ``root`` reached without any symlink.

    Resolving collapses ``..`` and links; comparing the resolved path with the
    lexical one detects a link anywhere on the way (file or directory).
    """
    root = Path(root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    lexical = Path(os.path.abspath(candidate))
    if resolved != lexical:
        return False
    return (resolved == root or root in resolved.parents) and resolved.is_file()


def looks_like_pdf(path: Path) -> bool:
    """Magic-byte check (donor ``is_pdf``): a landing page saved as ``.pdf`` is refused."""
    try:
        with Path(path).open("rb") as handle:
            return handle.read(5) == PDF_MAGIC
    except OSError:
        return False


@dataclass
class AdoptReport:
    """Outcome of adopting a legacy directory or manifest into the store.

    Every list holds legacy keys (``root/relative/path``); nothing is dropped silently.
    """

    root_id: str
    mode: str
    adopted: List[str] = field(default_factory=list)
    already_indexed: List[str] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)
    not_pdf: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    skipped_symlinks: List[str] = field(default_factory=list)
    checksum_mismatch: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    dois_recorded: int = 0

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


class LegacyIndex:
    """The reversible ``legacy path -> ref`` and ``doi -> refs`` index of one store.

    Args:
        store: The content-addressed store the refs live in.
        path: Index file; defaults to ``<store root>/legacy/index.json``.
    """

    def __init__(self, store: FileDocumentStore, *, path: Optional[Path] = None) -> None:
        self.store = store
        self.path = Path(path) if path is not None else store.root / "legacy" / "index.json"
        self._lock = threading.Lock()

    # -- persistence ----------------------------------------------------------------------------

    def _empty(self) -> Dict[str, Any]:
        return {"schema_version": LEGACY_SCHEMA_VERSION, "roots": {}, "files": {}, "dois": {}}

    def read(self) -> Dict[str, Any]:
        """The index, or an empty one when absent. A corrupt index raises; it is never guessed."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except ValueError as exc:
            raise StoreError(ErrorCode.INTERNAL, f"legacy index unreadable: {exc}", retryable=False) from exc
        if data.get("schema_version") != LEGACY_SCHEMA_VERSION:
            raise StoreError(ErrorCode.INTERNAL, "unsupported legacy index schema", retryable=False)
        for key in ("roots", "files", "dois"):
            data.setdefault(key, {})
        return data

    @contextlib.contextmanager
    def _editing(self) -> Iterator[Dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path.with_suffix(".lock"), "a") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                data = self.read()
                yield data
                atomic_write_json(self.path, data)
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    # -- recording ------------------------------------------------------------------------------

    @staticmethod
    def key(root_id: str, relpath: str) -> str:
        """Index key for a file: ``<root_id>/<posix relative path>``."""
        if not _ROOT_ID_RE.match(root_id):
            raise StoreError(ErrorCode.INVALID_INPUT, f"invalid legacy root id {root_id!r}", retryable=False)
        rel = Path(relpath).as_posix().lstrip("/")
        if rel.startswith("../") or "/../" in f"/{rel}/":
            raise StoreError(ErrorCode.INVALID_INPUT, "legacy path escapes its root", retryable=False)
        return f"{root_id}/{rel}"

    def register_root(self, root_id: str, root_path: Path) -> None:
        """Remember where ``root_id`` lives on this host (used by :meth:`restore`)."""
        self.key(root_id, "x")
        with self._editing() as data:
            data["roots"][root_id] = str(Path(root_path).resolve())

    def record(self, root_id: str, relpath: str, asset: StoredAsset, *, doi: str = "",
               source_url: str = "") -> List[str]:
        """Record one legacy file. Returns other legacy names already holding the same bytes.

        Duplicate bytes under another name are recorded, not refused: the same paper
        under two DOIs is real (donor ``record_in_manifest``). Duplicate bytes are not
        Work identity; they are reported for a deliberate merge.
        """
        key = self.key(root_id, relpath)
        with self._editing() as data:
            return self._record_into(data, key, asset, doi=doi, source_url=source_url)

    @staticmethod
    def _record_into(data: Dict[str, Any], key: str, asset: StoredAsset, *, doi: str = "",
                     source_url: str = "") -> List[str]:
        duplicates = sorted(k for k, row in data["files"].items() if row.get("ref") == asset.ref and k != key)
        row: Dict[str, Any] = {"ref": asset.ref, "bytes": asset.size, "mode": asset.mode,
                               "recorded_at": time.time()}
        if source_url:
            row["source_url"] = source_url
        if duplicates:
            row["duplicate_of"] = duplicates
        data["files"][key] = row
        dkey = doi_key(doi)
        if dkey:
            refs = data["dois"].setdefault(dkey, [])
            if asset.ref not in refs:
                refs.append(asset.ref)
        if duplicates:
            logger.warning("%s has the same content as %s; duplicate bytes are not Work identity",
                           key, ", ".join(duplicates))
        return duplicates

    def add_doi_alias(self, doi: str, ref: str) -> bool:
        """Associate ``doi`` with ``ref``; returns whether a new alias was added."""
        dkey = doi_key(doi)
        if not dkey:
            return False
        with self._editing() as data:
            refs = data["dois"].setdefault(dkey, [])
            if ref in refs:
                return False
            refs.append(ref)
            return True

    # -- lookup ---------------------------------------------------------------------------------

    def lookup_path(self, root_id: str, relpath: str) -> Optional[str]:
        """Ref recorded for a legacy path (exact, then case-insensitive), else ``None``."""
        key = self.key(root_id, relpath)
        files = self.read()["files"]
        if key in files:
            return files[key]["ref"]
        lowered = key.lower()
        for name, row in files.items():
            if name.lower() == lowered:
                return row["ref"]
        return None

    def refs_for_doi(self, doi: str) -> List[str]:
        """Refs recorded for ``doi`` as an alias, in recording order."""
        return list(self.read()["dois"].get(doi_key(doi), []))

    def find_by_doi(self, doi: str) -> Optional[str]:
        """A stored source for ``doi``: a recorded alias, else a legacy filename match.

        Tolerates the three historical stem conventions and case (donor
        ``_cached_pdf_for``) so nothing already on disk is stranded.
        """
        refs = self.refs_for_doi(doi)
        if refs:
            return refs[0]
        wanted = set(legacy_filenames(doi))
        if not wanted:
            return None
        for name, row in self.read()["files"].items():
            if name.rsplit("/", 1)[-1].lower() in wanted:
                return row["ref"]
        return None

    def names_for(self, ref: str) -> List[str]:
        """Every legacy key holding ``ref`` (donor ``duplicate_stems``)."""
        return sorted(k for k, row in self.read()["files"].items() if row.get("ref") == ref)

    def export(self) -> Dict[str, Any]:
        """The whole reversible mapping, for review or a later approved move."""
        return self.read()

    # -- adoption -------------------------------------------------------------------------------

    def adopt_tree(self, ctx: RequestContext, root_id: str, root_path: Path, *, mode: str = "reference",
                   pattern: str = "*.pdf") -> AdoptReport:
        """Index every PDF under ``root_path`` (recursively) without moving anything.

        Symlinks are not followed: a link could pull a file from outside the root
        into a private store. Non-PDFs by magic bytes are reported, not stored.

        Args:
            ctx: Request context.
            root_id: Stable label for the root (for example ``"papers"``).
            root_path: Directory to scan.
            mode: ``"reference"`` keeps bytes in place; ``"copy"`` copies them in.
            pattern: Filename glob applied recursively.
        """
        if mode not in {"reference", "copy"}:
            raise StoreError(ErrorCode.INVALID_INPUT, "mode must be 'reference' or 'copy'", retryable=False)
        root = Path(root_path).resolve()
        self.register_root(root_id, root)
        report = AdoptReport(root_id=root_id, mode=mode)
        indexed = self.read()["files"]
        pending: List[tuple] = []
        for path in sorted(root.rglob(pattern)):
            rel = path.relative_to(root).as_posix()
            key = self.key(root_id, rel)
            if not contained_regular_file(root, path):
                if path.is_symlink() or not path.is_dir():
                    report.skipped_symlinks.append(key)
                continue
            if not looks_like_pdf(path):
                report.not_pdf.append(key)
                continue
            try:
                asset = self._ingest_file(ctx, path, mode, {"legacy_name": path.name})
            except (OSError, StoreError) as exc:
                logger.warning("could not adopt %s: %s", key, exc)
                report.unreadable.append(key)
                continue
            if key in indexed and indexed[key].get("ref") == asset.ref:
                report.already_indexed.append(key)
                continue
            pending.append((key, asset))
        with self._editing() as data:  # one index write for the whole tree
            for key, asset in pending:
                duplicates = self._record_into(data, key, asset)
                report.adopted.append(key)
                if duplicates:
                    report.duplicates[key] = duplicates
        return report

    def _ingest_file(self, ctx: RequestContext, path: Path, mode: str, meta: Mapping[str, str]) -> StoredAsset:
        metadata = {"kind": AssetKind.SOURCE_PDF.value, **meta}
        if mode == "copy":
            return self.store.put_file(ctx, path, media_type="application/pdf", metadata=metadata)
        return self.store.reference_file(ctx, path, media_type="application/pdf", metadata=metadata)

    def import_harvest_manifest(self, ctx: RequestContext, manifest_path: Path, *, repo_root: Path,
                                root_id: str = "repo", mode: str = "reference") -> AdoptReport:
        """Index the PDFs a ``harvest_rsoxs`` manifest names, with their DOIs.

        Rows (and their ``versions``) carry ``pdf_path`` relative to ``repo_root``.
        A path outside the root is refused; a recorded ``pdf_sha256`` that no longer
        matches the file is reported and the file is not indexed.
        """
        root = Path(repo_root).resolve()
        self.register_root(root_id, root)
        report = AdoptReport(root_id=root_id, mode=mode)
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        rows: List[Mapping[str, Any]] = []
        for paper in manifest.get("papers") or []:
            rows.append(paper)
            rows.extend(v for v in (paper.get("versions") or []) if isinstance(v, Mapping))
        seen: set = set()
        for row in rows:
            rel = str(row.get("pdf_path") or "").strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            target = (root / rel).resolve()
            if root not in target.parents:
                report.unreadable.append(f"{root_id}/<outside root>/{Path(rel).name}")
                continue
            key = self.key(root_id, rel)
            if not target.is_file():
                report.missing.append(key)
                continue
            expected = str(row.get("pdf_sha256") or "")
            if expected and sha256_of(target) != expected:
                report.checksum_mismatch.append(key)
                continue
            if not looks_like_pdf(target):
                report.not_pdf.append(key)
                continue
            asset = self._ingest_file(ctx, target, mode, {"legacy_name": target.name})
            duplicates = self.record(root_id, rel, asset, doi=str(row.get("doi") or ""))
            report.adopted.append(key)
            if duplicates:
                report.duplicates[key] = duplicates
            if doi_key(row.get("doi")):
                report.dois_recorded += 1
        return report

    def adopt_download(self, ctx: RequestContext, root_id: str, root_path: Path, path: Path, *,
                       doi: str = "", source_url: str = "", mode: str = "copy") -> StoredAsset:
        """Index one freshly downloaded file at its legacy path (DownloadAgent forwarding)."""
        root = Path(root_path).resolve()
        target = Path(path).resolve()
        if target != root and root not in target.parents:
            raise StoreError(ErrorCode.INVALID_INPUT, "download outside its legacy root", retryable=False)
        self.register_root(root_id, root)
        asset = self._ingest_file(ctx, target, mode, {"legacy_name": target.name})
        self.record(root_id, target.relative_to(root).as_posix(), asset, doi=doi, source_url=source_url)
        return asset

    # -- reversal -------------------------------------------------------------------------------

    def restore(self, ctx: RequestContext, key: str, *, dest_root: Optional[Path] = None,
                overwrite_mismatch: bool = False) -> Path:
        """Write the stored bytes back to the legacy path ``key`` names.

        Args:
            ctx: Request context.
            key: Index key (``root_id/relative/path``).
            dest_root: Root to restore under; defaults to the registered root path.
            overwrite_mismatch: Replace an existing file whose bytes differ. Off by
                default: different bytes at a legacy name may be another paper.

        Returns:
            The restored path (unchanged when it already held the right bytes).
        """
        data = self.read()
        row = data["files"].get(key)
        if row is None:
            raise StoreError(ErrorCode.NOT_FOUND, f"no legacy entry {key}", retryable=False)
        root_id, _, rel = key.partition("/")
        base = Path(dest_root) if dest_root is not None else Path(data["roots"].get(root_id, ""))
        if not str(base):
            raise StoreError(ErrorCode.NOT_FOUND, f"legacy root {root_id} is not registered", retryable=False)
        target = (base / rel)
        if target.resolve() != base.resolve() and base.resolve() not in target.resolve().parents:
            raise StoreError(ErrorCode.INVALID_INPUT, "legacy path escapes its root", retryable=False)
        if target.exists():
            if sha256_of(target) == row["ref"].split(":", 1)[1]:
                return target
            if not overwrite_mismatch:
                raise StoreError(ErrorCode.CONFLICT, f"{key} holds different bytes", retryable=False)
        return self.store.copy_out(ctx, row["ref"], target, overwrite=True)

    def restore_all(self, ctx: RequestContext, *, dest_root: Optional[Path] = None,
                    root_id: Optional[str] = None) -> Dict[str, str]:
        """Restore every entry (optionally of one root); returns ``key -> outcome``."""
        outcomes: Dict[str, str] = {}
        for key in sorted(self.read()["files"]):
            if root_id is not None and not key.startswith(f"{root_id}/"):
                continue
            try:
                self.restore(ctx, key, dest_root=(Path(dest_root) / key.partition("/")[0]) if dest_root else None)
                outcomes[key] = "restored"
            except StoreError as exc:
                outcomes[key] = exc.code.value
        return outcomes


def unlink_quietly(path: Path) -> None:  # pragma: no cover - tiny helper kept for callers
    """Remove ``path`` if present."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


__all__ = (
    "AdoptReport", "LEGACY_SCHEMA_VERSION", "LegacyIndex", "doi_key", "doi_only", "f2w_stem", "legacy_filenames",
    "legacy_stem", "looks_like_pdf", "safe_stem",
)
