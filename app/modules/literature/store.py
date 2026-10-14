"""Content-addressed store for private literature sources, parser artifacts and assets (T05).

One durable store replaces the donor's two paper caches (FORGE's temp-dir
``forge_paper_cache`` and the Knowledge Agent's ``data/paper_cache``). Every
object is addressed by the SHA-256 of its bytes (``sha256:<hex>``), so the same
PDF downloaded under two DOIs is stored once, and a truncated or edited file is
detected on read instead of being trusted forever.

Layout under ``root`` (all paths are internal; none is ever returned to a user)::

    objects/ab/<hex>        immutable bytes, written once via temp file + rename
    meta/ab/<hex>.json      immutable metadata: media type, kind, size, created_at
    tmp/                    in-flight writes; safe to delete when no writer runs

An object may instead be *referenced* in place (``mode="reference"``): its bytes
stay in an existing durable root (for example ``papers/``) and the metadata
records where. Reads still verify the checksum. This is how legacy roots survive
the migration without a copy until an approved move (plan T05).

Access: the store implements :class:`~app.modules.contracts.ports.DocumentStore`.
It requires a server-built :class:`RequestContext` on every call and refuses a
context whose corpus scope excludes the store's corpus. Reading a source *for an
agent* additionally goes through ``security.content_access.SourceAccessScope``
in the calling service; a ref is never a public URL or a bearer token (D19).

Partly adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``sha256_of``,
manifest/duplicate handling) (BSD-3-Clause).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Iterable, Iterator, Mapping, Optional, Tuple

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode

#: Version of the on-disk layout and metadata record.
STORE_SCHEMA_VERSION = 1

#: Default per-object byte limit. A paper PDF is rarely above 100 MB.
DEFAULT_MAX_OBJECT_BYTES = 256 * 1024 * 1024

_CHUNK = 1 << 20
_REF_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
_META_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_META_VALUE = 2048


class AssetKind(str, Enum):
    """What a stored object is. Recorded at first write and never changed."""

    SOURCE_PDF = "source_pdf"
    PARSER_DOCUMENT = "parser_document"
    FIGURE_IMAGE = "figure_image"
    FIGURE_SPANS = "figure_spans"
    TABLE_DATA = "table_data"
    PACKAGE = "package"
    OTHER = "other"


class StoreError(ContractError):
    """Base error for the document store; carries a stable error code."""


class StoreIntegrityError(StoreError):
    """Stored bytes no longer hash to their address (corrupt or edited asset)."""

    def __init__(self, ref: str, detail: str = "checksum mismatch") -> None:
        super().__init__(ErrorCode.INTERNAL, f"{detail} for {ref}", retryable=False)
        self.ref = ref


class AssetTooLarge(StoreError):
    """An object exceeded the configured byte limit; nothing was stored."""

    def __init__(self, limit: int) -> None:
        super().__init__(ErrorCode.INVALID_INPUT, f"object exceeds {limit} bytes", retryable=False)
        self.limit = limit


@dataclass(frozen=True)
class StoredAsset:
    """Metadata of one stored object. Contains no filesystem path.

    Attributes:
        ref: ``sha256:<hex>`` content address.
        size: Byte length.
        media_type: MIME type given at first write.
        kind: :class:`AssetKind` value.
        metadata: String metadata given at first write (immutable).
        created_at: Epoch seconds of the first write.
        mode: ``"copy"`` (bytes inside the store) or ``"reference"`` (bytes in a
            legacy durable root, verified on read).
    """

    ref: str
    size: int
    media_type: str
    kind: str
    metadata: Dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    mode: str = "copy"

    @property
    def sha256(self) -> str:
        """Hex digest part of :attr:`ref`."""
        return self.ref.split(":", 1)[1]


def sha256_of(path: Path) -> str:
    """Hex SHA-256 of a file's bytes, read in 1 MiB blocks; ``""`` if unreadable.

    Adapted from donor ``paper_reading.sha256_of``: a hash is metadata, so a read
    failure returns ``""`` rather than failing the caller.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def parse_ref(ref: str) -> str:
    """Return the hex digest of a valid ref, else raise ``INVALID_INPUT``.

    Refs are validated before touching the filesystem, so a crafted ref cannot
    traverse out of the store.
    """
    match = _REF_RE.match(str(ref or ""))
    if not match:
        raise StoreError(ErrorCode.INVALID_INPUT, "not a sha256 content address", retryable=False)
    return match.group(1)


def ref_for_bytes(data: bytes) -> str:
    """Content address of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to ``path`` through a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _check_metadata(metadata: Mapping[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in (metadata or {}).items():
        if not isinstance(key, str) or not _META_KEY_RE.match(key):
            raise StoreError(ErrorCode.INVALID_INPUT, f"invalid metadata key {key!r}", retryable=False)
        if not isinstance(value, str) or len(value) > _MAX_META_VALUE:
            raise StoreError(ErrorCode.INVALID_INPUT, f"metadata {key!r} must be a short string", retryable=False)
        out[key] = value
    return out


class FileDocumentStore:
    """Filesystem implementation of :class:`~app.modules.contracts.ports.DocumentStore`.

    Args:
        root: Store directory. Created if missing. Choose a private, gitignored
            location; tests pass a temp directory (an isolated candidate store).
        corpus: Corpus this store serves. When set, a context whose non-empty
            ``corpus_scope`` excludes it is denied.
        max_object_bytes: Per-object byte limit for streamed writes.
        clock: Returns epoch seconds; injectable for tests.
    """

    backend = "file"

    def __init__(self, root: Path, *, corpus: str = "", max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.corpus = corpus
        self.max_object_bytes = int(max_object_bytes)
        self._clock = clock
        self._lock = threading.Lock()
        for sub in ("objects", "meta", "tmp"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- access ---------------------------------------------------------------------------------

    def _check(self, ctx: RequestContext) -> None:
        if not isinstance(ctx, RequestContext):
            raise StoreError(ErrorCode.DENIED, "document store requires a server-built RequestContext",
                             retryable=False)
        if self.corpus and ctx.corpus_scope and self.corpus not in ctx.corpus_scope:
            raise StoreError(ErrorCode.DENIED, "corpus outside request scope", retryable=False)

    def _object_path(self, hexdigest: str) -> Path:
        return self.root / "objects" / hexdigest[:2] / hexdigest

    def _meta_path(self, hexdigest: str) -> Path:
        return self.root / "meta" / hexdigest[:2] / f"{hexdigest}.json"

    # -- writes ---------------------------------------------------------------------------------

    def put(self, ctx: RequestContext, data: bytes, *, media_type: str, metadata: Mapping[str, str]) -> str:
        """Store ``data`` and return its ``sha256:<hex>`` address (DocumentStore port)."""
        return self.put_stream(ctx, [bytes(data)], media_type=media_type, metadata=metadata).ref

    def put_file(self, ctx: RequestContext, path: Path, *, media_type: str,
                 metadata: Mapping[str, str] = (), max_bytes: Optional[int] = None) -> StoredAsset:
        """Copy a local file into the store by streaming it."""
        with Path(path).open("rb") as handle:
            return self.put_stream(ctx, iter(lambda: handle.read(_CHUNK), b""), media_type=media_type,
                                   metadata=dict(metadata or {}), max_bytes=max_bytes)

    def put_stream(self, ctx: RequestContext, chunks: Iterable[bytes], *, media_type: str,
                   metadata: Mapping[str, str] = (), max_bytes: Optional[int] = None,
                   prefix_check: Optional[Callable[[bytes], bool]] = None) -> StoredAsset:
        """Stream ``chunks`` into the store, hashing as they arrive.

        Args:
            ctx: Request context.
            chunks: Byte chunks; consumed once.
            media_type: MIME type recorded on first write.
            metadata: String metadata recorded on first write (``kind`` selects
                the :class:`AssetKind`).
            max_bytes: Byte limit; defaults to ``max_object_bytes``. Exceeding it
                stops reading and stores nothing.
            prefix_check: Called once with the first bytes (up to 1 KiB); a false
                answer stops the write with ``INVALID_INPUT``.

        Returns:
            The :class:`StoredAsset` (the existing one when the bytes were already stored).
        """
        self._check(ctx)
        meta = _check_metadata(dict(metadata or {}))
        kind = meta.get("kind", AssetKind.OTHER.value)
        AssetKind(kind)  # validate
        limit = self.max_object_bytes if max_bytes is None else int(max_bytes)
        digest = hashlib.sha256()
        size = 0
        head = b""
        checked = prefix_check is None
        fd, tmp_name = tempfile.mkstemp(dir=str(self.root / "tmp"), prefix="put-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise AssetTooLarge(limit)
                    if not checked:
                        head += chunk[: 1024 - len(head)]
                        if len(head) >= 1024 or size >= 1024:
                            if not prefix_check(head):
                                raise StoreError(ErrorCode.INVALID_INPUT, "content signature check failed",
                                                 retryable=False)
                            checked = True
                    digest.update(chunk)
                    out.write(chunk)
                if not checked and not prefix_check(head):
                    raise StoreError(ErrorCode.INVALID_INPUT, "content signature check failed", retryable=False)
                out.flush()
                os.fsync(out.fileno())
            hexdigest = digest.hexdigest()
            return self._commit(tmp, hexdigest, size, media_type, kind, meta)
        finally:
            tmp.unlink(missing_ok=True)

    def _commit(self, tmp: Path, hexdigest: str, size: int, media_type: str, kind: str,
                meta: Dict[str, str], *, external: Optional[Path] = None) -> StoredAsset:
        target = self._object_path(hexdigest)
        meta_path = self._meta_path(hexdigest)
        with self._lock:
            existing = self._read_meta(hexdigest)
            if existing is not None:
                # Metadata is immutable. Bytes that arrive for a referenced or lost object are
                # kept inside the store (this is also how a deleted blob is repaired).
                if external is None and (not target.exists() or sha256_of(target) != hexdigest):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp, target)
                    os.chmod(target, 0o444)
                return existing
            if external is None:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
                os.chmod(target, 0o444)
            record = {
                "schema_version": STORE_SCHEMA_VERSION, "sha256": hexdigest, "size": size,
                "media_type": media_type, "kind": kind, "metadata": meta, "created_at": self._clock(),
                "mode": "reference" if external is not None else "copy",
            }
            if external is not None:
                record["external_path"] = str(external)
            atomic_write_json(meta_path, record)
            return self._asset_from(record)

    def reference_file(self, ctx: RequestContext, path: Path, *, media_type: str,
                       metadata: Mapping[str, str] = ()) -> StoredAsset:
        """Register a file that stays in its durable legacy root (no copy).

        The checksum is computed now and verified on every read; a later edit of
        the legacy file therefore surfaces as :class:`StoreIntegrityError`.
        """
        self._check(ctx)
        meta = _check_metadata(dict(metadata or {}))
        kind = meta.get("kind", AssetKind.OTHER.value)
        AssetKind(kind)
        source = Path(path).resolve()
        hexdigest = sha256_of(source)
        if not hexdigest:
            raise StoreError(ErrorCode.NOT_FOUND, "legacy file is not readable", retryable=False)
        existing = self._read_meta(hexdigest)
        if existing is not None:
            return existing
        return self._commit(Path(os.devnull), hexdigest, source.stat().st_size, media_type, kind, meta,
                            external=source)

    # -- reads ----------------------------------------------------------------------------------

    def _read_meta(self, hexdigest: str) -> Optional[StoredAsset]:
        record = self._read_meta_record(hexdigest)
        return self._asset_from(record) if record is not None else None

    def _read_meta_record(self, hexdigest: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self._meta_path(hexdigest).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _asset_from(record: Mapping[str, Any]) -> StoredAsset:
        return StoredAsset(
            ref="sha256:" + str(record["sha256"]), size=int(record["size"]), media_type=str(record["media_type"]),
            kind=str(record["kind"]), metadata=dict(record.get("metadata") or {}),
            created_at=float(record.get("created_at") or 0.0), mode=str(record.get("mode") or "copy"),
        )

    def _bytes_path(self, hexdigest: str) -> Optional[Path]:
        target = self._object_path(hexdigest)
        if target.exists():
            return target
        record = self._read_meta_record(hexdigest)
        if record and record.get("mode") == "reference" and record.get("external_path"):
            external = Path(str(record["external_path"]))
            return external if external.exists() else None
        return None

    def describe(self, ctx: RequestContext, ref: str) -> StoredAsset:
        """Return metadata for ``ref`` or raise ``NOT_FOUND``."""
        self._check(ctx)
        asset = self._read_meta(parse_ref(ref))
        if asset is None:
            raise StoreError(ErrorCode.NOT_FOUND, f"no artifact {ref}", retryable=False)
        return asset

    def exists(self, ctx: RequestContext, ref: str) -> bool:
        """Whether ``ref`` has metadata and readable bytes (no checksum pass)."""
        self._check(ctx)
        try:
            hexdigest = parse_ref(ref)
        except StoreError:
            return False
        return self._read_meta(hexdigest) is not None and self._bytes_path(hexdigest) is not None

    def open_verified(self, ctx: RequestContext, ref: str) -> Iterator[bytes]:
        """Yield the object's bytes in chunks; raise on checksum mismatch at the end.

        Callers that act on bytes before the end must use :meth:`get` or
        :meth:`local_path` instead, which verify first.
        """
        self._check(ctx)
        hexdigest = parse_ref(ref)
        path = self._bytes_path(hexdigest)
        if path is None or self._read_meta(hexdigest) is None:
            raise StoreError(ErrorCode.NOT_FOUND, f"no artifact {ref}", retryable=False)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
                yield chunk
        if digest.hexdigest() != hexdigest:
            raise StoreIntegrityError(ref)

    def get(self, ctx: RequestContext, ref: str) -> bytes:
        """Return the bytes for ``ref`` after checking access and checksum (DocumentStore port)."""
        return b"".join(self.open_verified(ctx, ref))

    def verify(self, ctx: RequestContext, ref: str) -> bool:
        """Whether ``ref`` is present and its bytes still hash to it."""
        try:
            for _ in self.open_verified(ctx, ref):
                pass
        except StoreError:
            return False
        return True

    def local_path(self, ctx: RequestContext, ref: str, *, verify: bool = True) -> Path:
        """Internal filesystem path for trusted parser/worker code. Never expose it.

        Raises:
            StoreIntegrityError: When ``verify`` and the bytes do not match.
        """
        self._check(ctx)
        hexdigest = parse_ref(ref)
        path = self._bytes_path(hexdigest)
        if path is None:
            raise StoreError(ErrorCode.NOT_FOUND, f"no artifact {ref}", retryable=False)
        if verify and sha256_of(path) != hexdigest:
            raise StoreIntegrityError(ref)
        return path

    def list_refs(self, ctx: RequestContext, *, prefix: str = "") -> Iterable[str]:
        """Yield stored refs in sorted order, optionally filtered by ``prefix``."""
        self._check(ctx)
        refs = []
        for meta in sorted((self.root / "meta").glob("*/*.json")):
            ref = "sha256:" + meta.stem
            if ref.startswith(prefix):
                refs.append(ref)
        return iter(refs)

    def list_assets(self, ctx: RequestContext, *, kind: Optional[AssetKind] = None) -> Tuple[StoredAsset, ...]:
        """All stored assets, optionally of one kind."""
        out = []
        for ref in self.list_refs(ctx):
            asset = self._read_meta(parse_ref(ref))
            if asset is not None and (kind is None or asset.kind == kind.value):
                out.append(asset)
        return tuple(out)

    def copy_out(self, ctx: RequestContext, ref: str, dest: Path, *, overwrite: bool = False) -> Path:
        """Write the verified bytes of ``ref`` to ``dest`` atomically (cache restore)."""
        dest = Path(dest)
        if dest.exists() and not overwrite:
            raise StoreError(ErrorCode.CONFLICT, "destination exists", retryable=False)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".restore-")
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in self.open_verified(ctx, ref):
                    out.write(chunk)
            os.replace(tmp_name, dest)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return dest

    def read_stream(self, ctx: RequestContext, ref: str) -> BinaryIO:  # pragma: no cover - thin helper
        """Open the object's bytes for reading (unverified; prefer :meth:`get`)."""
        self._check(ctx)
        path = self._bytes_path(parse_ref(ref))
        if path is None:
            raise StoreError(ErrorCode.NOT_FOUND, f"no artifact {ref}", retryable=False)
        return path.open("rb")


__all__ = (
    "AssetKind", "AssetTooLarge", "DEFAULT_MAX_OBJECT_BYTES", "FileDocumentStore", "STORE_SCHEMA_VERSION",
    "StoreError", "StoreIntegrityError", "StoredAsset", "atomic_write_json", "parse_ref", "ref_for_bytes",
    "sha256_of",
)
