"""Staged paper packages, import receipts and staged-import status (J-01..J-03).

A package is one paper's intake + parse + (optional) extraction result, saved
atomically under ``<staging root>/<package key>/paper.json``. Its assets (source
PDF, parser document, figures, tables) are refs into the content-addressed
store with their checksums; nothing is copied twice.

Stage state is explicit and per stage::

    "stages": {"intake": "complete", "parse": "complete",
               "extraction": "complete" | "incomplete" | "not_run"}
    "incomplete_chunks": [...]     # extraction chunks that failed and need a retry

``complete`` means every *requested* stage completed. It is never scientific
approval: a complete package is a candidate for validation/review/publication,
which happen elsewhere (H5). An incomplete package is never importable.

The package key hashes schema, DOI, source sha256, parser key, pipeline
fingerprint and whether extraction was requested, so a new pipeline revision is
a new package, not an overwrite.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_staging.py (``save_package``,
``load_package``, ``import_package``), import_staged.py (``pending_packages``) and
staged_import_status.py (``read_staged_import_status``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, cast

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .store import FileDocumentStore, atomic_write_json

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

PACKAGE_SCHEMA_VERSION = 1
STAGES = ("intake", "parse", "extraction")
_COMPLETE_MARKER = re.compile(rb'"complete"\s*:\s*(true|false)')


class PackageInvalid(ContractError):
    """A package failed validation (schema, completeness, missing or corrupt asset)."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_INPUT, message, retryable=False)


def pipeline_fingerprint(pipeline: Mapping[str, Any]) -> str:
    """Stable short hash of a pipeline configuration."""
    return hashlib.sha256(json.dumps(dict(pipeline), sort_keys=True, default=str).encode()).hexdigest()[:16]


def package_key(
    *, doi: str, source_sha256: str, parser_key: str, pipeline: Mapping[str, Any], extraction_requested: bool
) -> str:
    """Content identity of a package."""
    identity = [
        PACKAGE_SCHEMA_VERSION,
        doi.lower(),
        source_sha256,
        parser_key,
        pipeline_fingerprint(pipeline),
        bool(extraction_requested),
    ]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def save_package(
    ctx: RequestContext,
    store: FileDocumentStore,
    root: Path,
    *,
    work: Mapping[str, Any],
    source_ref: str,
    parse_record: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    extraction: Mapping[str, Any] | None = None,
    extraction_requested: bool = False,
    incomplete_chunks: Sequence[Any] = (),
    timings: Mapping[str, float] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Path:
    """Write one package atomically after checking every asset it names is in the store.

    Args:
        ctx: Request context under which the assets are verified and described.
        store: Document store that must hold every asset the package names.
        root: Directory under which the package directory (named by its key) is created.
        work: Work metadata; its ``doi`` takes part in the package key.
        source_ref: Store reference of the source document; it must be among the verified assets.
        parse_record: Parse result with ``document_ref``, ``parser_key`` and optional ``asset_refs``.
        pipeline: Pipeline description recorded with the package and fingerprinted into its key.
        extraction: Extraction result (opaque to this module), when one ran.
        extraction_requested: Whether this pipeline includes extraction.
        incomplete_chunks: Chunks whose extraction failed; makes extraction ``incomplete``.
        timings: Optional stage timings recorded in the package.
        identity: Optional identity record stored in the package.

    Returns:
        Path of the written ``paper.json``.

    Raises:
        PackageInvalid: An asset is missing or corrupt in the store; nothing is written.
    """
    refs = [source_ref, str(parse_record["document_ref"]), *[str(r) for r in parse_record.get("asset_refs", [])]]
    assets: dict[str, int] = {}
    for ref in refs:
        if not store.verify(ctx, ref):
            raise PackageInvalid(f"asset {ref[:19]} is missing or corrupt; package not written")
        assets[ref] = store.describe(ctx, ref).size
    if not extraction_requested:
        extraction_stage = "not_run"
    elif extraction is not None and not incomplete_chunks:
        extraction_stage = "complete"
    else:
        extraction_stage = "incomplete"
    stages = {"intake": "complete", "parse": "complete", "extraction": extraction_stage}
    complete = extraction_stage in {"complete", "not_run"}
    key = package_key(
        doi=str(work.get("doi") or ""),
        source_sha256=source_ref.split(":", 1)[-1],
        parser_key=str(parse_record["parser_key"]),
        pipeline=pipeline,
        extraction_requested=extraction_requested,
    )
    directory = Path(root) / key
    directory.mkdir(parents=True, exist_ok=True)
    payload = {  # small header first: status readers scan only the head
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "complete": complete,
        "stages": stages,
        "incomplete_chunks": list(incomplete_chunks),
        "created_at": time.time(),
        "work": dict(work),
        "identity": dict(identity or {}),
        "source_ref": source_ref,
        "parse": dict(parse_record),
        "pipeline": dict(pipeline),
        "pipeline_fingerprint": pipeline_fingerprint(pipeline),
        "timings": dict(timings or {}),
        "assets": assets,
        "extraction": dict(extraction) if extraction is not None else None,
    }
    path = directory / "paper.json"
    _write_ordered(path, payload)
    return path


def _write_ordered(path: Path, payload: dict[str, Any]) -> None:
    # atomic_write_json sorts keys; packages keep insertion order so "complete" leads.
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def load_package(ctx: RequestContext, store: FileDocumentStore, path: Path) -> dict[str, Any]:
    """Validate a complete package and every asset checksum; return its payload."""
    try:
        payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageInvalid("package unreadable") from exc
    if payload.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageInvalid("unsupported package schema")
    if payload.get("complete") is not True:
        raise PackageInvalid("package is incomplete")
    for ref in payload.get("assets") or {}:
        if not store.verify(ctx, ref):
            raise PackageInvalid(f"asset checksum mismatch: {ref[:19]}")
    if payload.get("source_ref") not in (payload.get("assets") or {}):
        raise PackageInvalid("source is not listed among the package assets")
    return payload


def _file_sha(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextlib.contextmanager
def _exclusive(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ContractError(ErrorCode.CONFLICT, "package import already running") from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)


def import_package(
    ctx: RequestContext,
    store: FileDocumentStore,
    path: Path,
    *,
    publish: Callable[[dict[str, Any]], Mapping[str, Any]],
    target: str = "candidate",
) -> dict[str, Any]:
    """Import once per package checksum and target; write a receipt only on confirmed success.

    ``publish`` must be idempotent (a crash can land after its write and before
    the receipt). It receives the validated payload and must return a mapping
    with ``record_id``; the receipt records target, package sha256 and that id.
    """
    path = Path(path)
    with _exclusive(path.with_name("import.lock")):
        digest = _file_sha(path)
        receipt_path = path.with_name("imported.json")
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("package_sha256") == digest and receipt.get("target") == target:
                return {**receipt, "status": "already_imported"}
        payload = load_package(ctx, store, path)
        result = dict(publish(payload) or {})
        if not result.get("record_id") or result.get("error"):
            raise ContractError(ErrorCode.INTERNAL, "publisher did not confirm the import")
        if _file_sha(path) != digest:
            raise ContractError(ErrorCode.CONFLICT, "package changed during import; no receipt written")
        receipt = {
            "status": "imported",
            "package_sha256": digest,
            "target": target,
            "record_id": str(result["record_id"]),
            "imported_at": time.time(),
        }
        atomic_write_json(receipt_path, receipt)
        return receipt


def _header_complete(path: Path) -> bool:
    with Path(path).open("rb") as stream:
        marker = _COMPLETE_MARKER.search(stream.read(2048))
    if marker:
        return marker.group(1) == b"true"
    return json.loads(Path(path).read_text(encoding="utf-8")).get("complete") is True


def _receipt_matches(path: Path) -> bool:
    receipt = path.with_name("imported.json")
    if not receipt.is_file():
        return False
    try:
        return cast(bool, json.loads(receipt.read_text(encoding="utf-8")).get("package_sha256") == _file_sha(path))  # Any: JSON payload
    except (OSError, ValueError):
        return False


def pending_packages(
    root: Path, seen: dict[Path, tuple[int, int]], priorities: Mapping[str, tuple[int, ...]] | None = None
) -> list[Path]:
    """Complete, not-yet-imported packages changed since last seen, highest priority first.

    Incomplete or unreadable packages are remembered in ``seen`` so they are not
    retried until they change (an explicit retry re-stages them).
    """
    queue = []
    for path in Path(root).glob("*/paper.json"):
        stat = path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        if seen.get(path) == fingerprint:
            continue
        try:
            if not _header_complete(path) or _receipt_matches(path):
                seen[path] = fingerprint
                continue
            doi = str(json.loads(path.read_text(encoding="utf-8"))["work"].get("doi") or "").lower()
        except (OSError, ValueError, KeyError):
            seen[path] = fingerprint
            continue
        queue.append(((priorities or {}).get(doi, (3, 0, 0)), str(path), path))
    queue.sort(key=lambda item: (item[0], item[1]))
    return [path for _, _, path in queue]


def read_staged_import_status(root: Path) -> dict[str, Any]:
    """Counts of complete, imported, waiting and incomplete packages, and incomplete stages.

    Import status is distinct from extraction success and parse completion.
    """
    counts = {"complete_packages": 0, "imported_packages": 0, "incomplete_packages": 0}
    incomplete_stages: dict[str, int] = {}
    for path in Path(root).glob("*/paper.json"):
        try:
            if _header_complete(path):
                counts["complete_packages"] += 1
                counts["imported_packages"] += int(_receipt_matches(path))
                continue
            stages = json.loads(path.read_text(encoding="utf-8")).get("stages") or {}
            for stage, state in stages.items():
                if state not in {"complete", "not_run"}:
                    incomplete_stages[stage] = incomplete_stages.get(stage, 0) + 1
        except (OSError, ValueError):
            pass
        counts["incomplete_packages"] += 1
    return {
        **counts,
        "waiting_packages": counts["complete_packages"] - counts["imported_packages"],
        "incomplete_stages": incomplete_stages,
    }


__all__ = (
    "PACKAGE_SCHEMA_VERSION",
    "PackageInvalid",
    "STAGES",
    "import_package",
    "load_package",
    "package_key",
    "pending_packages",
    "pipeline_fingerprint",
    "read_staged_import_status",
    "save_package",
)
