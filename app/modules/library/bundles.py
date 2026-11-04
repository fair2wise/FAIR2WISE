"""KG bundles on the state volume (A8, T38).

A bundle is a directory ``<F2W_WORKDIR>/kgs/<name>/`` holding the five files one extraction needs to
continue later::

    terms.json  kg.json  manifest.json  processed_pdfs.json  extraction_manifest.json

``manifest.json`` records the topic, the schema (locked for the life of the bundle), the model, the
backend, the papers with their sha256, the parent bundle and the ``reconstructed`` flag.

This module owns the store, the name rules, the schema lock, the continuation of a KG that has no
bundle yet (:func:`continue_from_kg`, over :mod:`app.modules.term_extractor.kg_to_terms`), the export to
a release directory, and the additive hooks the legacy Settings graph list uses
(:func:`registered_graph_paths`, :func:`resolve_registered_graph`, :func:`schema_path_for_registered_graph`).
Nothing here imports the legacy agent; ``f2w_agent.api`` imports these three hooks lazily.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, TYPE_CHECKING

from ..contracts.errors import ContractError, ErrorCode
from ..json_types import JsonValue

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Environment variable naming the state volume (the profile sets it).
WORKDIR_ENV = "F2W_WORKDIR"
#: Environment variable naming the release directory; default ``storage/kg`` of the application.
RELEASE_DIR_ENV = "F2W_KG_RELEASE_DIR"
#: Sub-directory of the work directory holding the bundles.
BUNDLES_DIRNAME = "kgs"

TERMS_FILE = "terms.json"
KG_FILE = "kg.json"
MANIFEST_FILE = "manifest.json"
PROCESSED_FILE = "processed_pdfs.json"
EXTRACTION_MANIFEST_FILE = "extraction_manifest.json"
BUNDLE_FILES = (TERMS_FILE, KG_FILE, MANIFEST_FILE, PROCESSED_FILE, EXTRACTION_MANIFEST_FILE)

MANIFEST_VERSION = 1
#: Letters, digits, ``_`` and ``-``; starts alphanumeric; at most 64 characters. No dots, slashes or spaces.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
#: Directory prefix of the virtual graph paths shown in the Settings graph list.
VIRTUAL_PREFIX = BUNDLES_DIRNAME + "/"
#: Cap on a source KG read for "continue" (bytes).
MAX_KG_BYTES = 256 * 1024 * 1024


def validate_name(name: object) -> str:
    """Return ``name`` when it is a valid bundle name, else raise ``INVALID_INPUT``.

    Valid: 1 to 64 characters from ``A-Za-z0-9_-``, starting with a letter or digit. This rules out
    path separators, dots, whitespace and the empty string, so a name is always one safe path segment.
    """
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ContractError(ErrorCode.INVALID_INPUT,
                            "KG name must be 1-64 characters of letters, digits, '_' or '-', starting with a "
                            "letter or digit")
    return name


def now_iso() -> str:
    """UTC time, ISO 8601 with ``Z``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: object) -> None:
    """Atomic JSON write (temp file in the same directory, then ``os.replace``)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=False)
            handle.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> JsonValue:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class PaperRef:
    """One paper of a bundle: file name and content hash (``sha256`` is ``None`` for reconstructed papers)."""

    name: str
    sha256: str | None = None
    pages: int | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON form recorded in ``manifest.json``."""
        return {"name": self.name, "sha256": self.sha256, "pages": self.pages}


@dataclass(frozen=True)
class Bundle:
    """A bundle's manifest plus where it lives (``directory`` is never sent to a client)."""

    name: str
    directory: Path
    manifest: Mapping[str, Any]

    @property
    def schema(self) -> str:
        """The locked schema id."""
        return str(self.manifest["schema"]["id"])

    def file(self, filename: str) -> Path:
        """Path of one of :data:`BUNDLE_FILES`."""
        if filename not in BUNDLE_FILES:
            raise ValueError(f"not a bundle file: {filename}")
        return self.directory / filename

    def summary(self) -> dict[str, Any]:
        """The ``KgBundleSummary`` shape of ``extractionApi.ts`` (no paths)."""
        m = self.manifest
        return {
            "name": self.name,
            "topic": m.get("topic"),
            "schema": str(m["schema"]["id"]),
            "model": m.get("model"),
            "backend": m.get("backend"),
            "paper_count": len(m.get("papers") or []),
            "parent": m.get("parent"),
            "reconstructed": bool(m.get("reconstructed")),
            "updated_at": m.get("updated_at"),
        }


def manifest_schema_block(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The ``schema`` block of a manifest from a schema choice (``id`` required)."""
    return {k: schema.get(k) for k in ("id", "version", "sha256", "path") if schema.get(k) is not None}


class BundleStore:
    """Bundles under ``root`` (``<F2W_WORKDIR>/kgs``).

    Args:
        root: The bundle directory (created on first write; reading a missing root lists nothing).
        clock: Returns the ISO timestamp stored in manifests (injectable for tests).
    """

    def __init__(self, root: Path, *, clock: Callable[[], str] = now_iso) -> None:
        self.root = Path(root)
        self._clock = clock

    # ------------------------------------------------------------------ paths
    def directory(self, name: str) -> Path:
        """The directory of ``name`` (validated; never follows a symlink out of the root)."""
        validate_name(name)
        path = self.root / name
        if path.is_symlink():
            raise ContractError(ErrorCode.INVALID_INPUT, "bundle directory is a link")
        return path

    def exists(self, name: str) -> bool:
        """True when ``name`` has a manifest."""
        return (self.directory(name) / MANIFEST_FILE).is_file()

    # ------------------------------------------------------------------ read
    def get(self, name: str) -> Bundle:
        """The bundle ``name``; ``NOT_FOUND`` when absent, ``INTERNAL`` for an unreadable manifest."""
        directory = self.directory(name)
        manifest_path = directory / MANIFEST_FILE
        if not manifest_path.is_file():
            raise ContractError(ErrorCode.NOT_FOUND, "no such KG bundle")
        try:
            manifest = _read_json(manifest_path)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("schema"), dict) \
                    or not manifest["schema"].get("id"):
                raise ValueError("manifest has no schema")
        except (OSError, ValueError) as exc:
            raise ContractError(ErrorCode.INTERNAL, "KG bundle manifest is unreadable") from exc
        return Bundle(name=name, directory=directory, manifest=manifest)

    def list(self) -> list[Bundle]:
        """Every readable bundle, sorted by name; unreadable ones are skipped."""
        if not self.root.is_dir():
            return []
        out: list[Bundle] = []
        for child in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not NAME_RE.fullmatch(child.name) or child.is_symlink() or not child.is_dir():
                continue
            try:
                out.append(self.get(child.name))
            except ContractError as exc:
                logger.debug("invalid bundle skipped in listing: %s", type(exc).__name__)
                continue
        return out

    # ------------------------------------------------------------------ write
    def create(self, name: str, *, schema: Mapping[str, Any], topic: str | None = None, model: str | None = None,
               backend: str | None = None, papers: Iterable[PaperRef] = (), parent: str | None = None,
               reconstructed: bool = False, terms: Mapping[str, Any] | None = None,
               kg: Mapping[str, Any] | None = None, processed_pdfs: Sequence[str] = (),
               extraction_manifest: Mapping[str, Any] | None = None) -> Bundle:
        """Create a new bundle; refuse a name that already exists (``CONFLICT``).

        The directory is created atomically (``mkdir`` without ``exist_ok``), so two concurrent creations
        of one name cannot both succeed. A failed write removes the half-built directory.
        """
        validate_name(name)
        if not schema.get("id"):
            raise ContractError(ErrorCode.INVALID_INPUT, "a bundle needs a schema")
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.directory(name)
        try:
            directory.mkdir()
        except FileExistsError:
            raise ContractError(ErrorCode.CONFLICT, "a KG bundle with this name already exists") from None
        now = self._clock()
        manifest = {
            "version": MANIFEST_VERSION, "name": name, "topic": topic or None, "schema": manifest_schema_block(schema),
            "model": model, "backend": backend, "papers": [p.to_json() for p in papers], "parent": parent,
            "reconstructed": bool(reconstructed), "created_at": now, "updated_at": now,
        }
        try:
            _write_json(directory / TERMS_FILE, terms if terms is not None else
                        {"metadata": {}, "terms": [], "code_snippets": []})
            _write_json(directory / KG_FILE, kg if kg is not None else {"things": [], "associations": []})
            _write_json(directory / PROCESSED_FILE, list(processed_pdfs))
            _write_json(directory / EXTRACTION_MANIFEST_FILE,
                        extraction_manifest if extraction_manifest is not None else {"papers": {}})
            _write_json(directory / MANIFEST_FILE, manifest)  # last: a bundle exists once its manifest does
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return Bundle(name=name, directory=directory, manifest=manifest)

    def fork(self, parent: str, name: str, *, schema_id: str | None = None, topic: str | None = None) -> Bundle:
        """Copy ``parent`` into a new child bundle (continuation); the schema is inherited and cannot change.

        Args:
            parent: Existing bundle.
            name: New bundle name.
            schema_id: The schema the caller asked for, if any; must equal the parent's (see :func:`ensure_schema_locked`).
            topic: Overrides the parent's topic when given.
        """
        source = self.get(parent)
        ensure_schema_locked(source.manifest, schema_id)
        validate_name(name)
        if name == parent:
            raise ContractError(ErrorCode.CONFLICT, "a KG bundle with this name already exists")
        return self.create(
            name, schema=source.manifest["schema"], topic=topic or source.manifest.get("topic"),
            model=source.manifest.get("model"), backend=source.manifest.get("backend"),
            papers=[PaperRef(p.get("name", ""), p.get("sha256"), p.get("pages")) for p in source.manifest.get("papers") or []],
            parent=parent, reconstructed=bool(source.manifest.get("reconstructed")),
            terms=_read_json(source.file(TERMS_FILE)), kg=_read_json(source.file(KG_FILE)),
            processed_pdfs=_read_json(source.file(PROCESSED_FILE)),
            extraction_manifest=_read_json(source.file(EXTRACTION_MANIFEST_FILE)))

    def record_extraction(self, name: str, *, schema_id: str, model: str | None, backend: str | None,
                          papers: Iterable[PaperRef]) -> Bundle:
        """Update the manifest after an extraction (T39 calls this); the schema must be the locked one."""
        bundle = self.get(name)
        ensure_schema_locked(bundle.manifest, schema_id)
        known = {p.get("name"): p for p in bundle.manifest.get("papers") or []}
        for paper in papers:
            known[paper.name] = paper.to_json()
        manifest = {**bundle.manifest, "model": model, "backend": backend, "papers": list(known.values()),
                    "updated_at": self._clock()}
        _write_json(bundle.directory / MANIFEST_FILE, manifest)
        return Bundle(name=name, directory=bundle.directory, manifest=manifest)


def ensure_schema_locked(manifest: Mapping[str, Any], requested: str | None) -> None:
    """Refuse a schema that differs from the bundle's (``INVALID_INPUT``); ``None`` means "keep"."""
    locked = str(manifest["schema"]["id"])
    if requested is not None and requested != locked:
        raise ContractError(ErrorCode.INVALID_INPUT,
                            f"the schema of this KG is locked to {locked}; it cannot change on continuation")


def continue_from_kg(store: BundleStore, kg_path: Path, name: str, *, schema: Mapping[str, Any],
                     topic: str | None = None, source_label: str = "") -> tuple[Bundle, list[str]]:
    """Reverse a KG file that has no bundle into a child bundle (``kg_to_terms``, T37).

    The child's ``kg.json`` is a copy of the source KG, its ``terms.json`` the reconstruction, and its
    ``processed_pdfs.json``/``extraction_manifest.json`` mark every paper ``reconstructed``. The manifest
    carries ``reconstructed=true`` and ``parent=None`` (the source is a file, not a bundle); the schema
    the caller chose is locked from now on.

    Returns:
        ``(bundle, warnings)``. ``KgToTermsError`` surfaces as ``INVALID_INPUT``.
    """
    from ..term_extractor.kg_to_terms import KgToTermsError, kg_to_terms, reconstructed_extraction_manifest

    validate_name(name)
    if store.exists(name):
        raise ContractError(ErrorCode.CONFLICT, "a KG bundle with this name already exists")
    try:
        if kg_path.stat().st_size > MAX_KG_BYTES:
            raise ContractError(ErrorCode.INVALID_INPUT, "KG file is too large to continue")
        kg = _read_json(kg_path)
        result = kg_to_terms(kg, source=source_label or kg_path.name)
    except KgToTermsError as exc:
        raise ContractError(ErrorCode.INVALID_INPUT, f"this KG cannot be continued: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_INPUT, "the KG file cannot be read") from exc
    papers = [PaperRef(p) for p in result.processed_pdfs]
    bundle = store.create(
        name, schema=schema, topic=topic, papers=papers, reconstructed=True, terms=result.terms, kg=kg,
        processed_pdfs=result.processed_pdfs,
        extraction_manifest=reconstructed_extraction_manifest(result.terms, terms_json=TERMS_FILE))
    return bundle, list(result.warnings)


# ---------------------------------------------------------------------- export


def default_release_dir() -> Path:
    """The configured release directory: ``F2W_KG_RELEASE_DIR`` or the application's ``storage/kg``."""
    configured = (os.environ.get(RELEASE_DIR_ENV) or "").strip()
    if configured:
        return Path(configured)
    from ..resources import resolve_resource

    return Path(resolve_resource("storage/kg"))


def export_file_name(name: str) -> str:
    """``matkg_<name>.json``."""
    return f"matkg_{validate_name(name)}.json"


def export_bundle(bundle: Bundle, release_dir: Path, schema: Mapping[str, Any] | None = None) -> str:
    """Write ``matkg_<name>.json`` into ``release_dir`` with the schema name in its metadata; return the file name.

    The target is always ``release_dir / export_file_name(name)`` (the name is validated, so it cannot
    leave the directory), the directory must already exist, and a link or a file that is not this
    bundle's earlier export is never overwritten (``CONFLICT``). The bundle's ``kg.json`` is untouched.
    The file is published by an atomic rename.
    """
    release_dir = Path(release_dir)
    if not release_dir.is_dir():
        raise ContractError(ErrorCode.UNAVAILABLE, "the release directory is not available")
    target = release_dir / export_file_name(bundle.name)
    if target.resolve().parent != release_dir.resolve():
        raise ContractError(ErrorCode.INVALID_INPUT, "export target is outside the release directory")
    if target.is_symlink():
        raise ContractError(ErrorCode.CONFLICT, "the export target is a link")
    if target.exists():
        try:
            existing = _read_json(target)
            owned = isinstance(existing, dict) and (existing.get("metadata") or {}).get("f2w_bundle") == bundle.name
        except (OSError, ValueError):
            owned = False
        if not owned:
            raise ContractError(ErrorCode.CONFLICT, "a different KG file with this name exists in the release directory")
    kg = _read_json(bundle.file(KG_FILE))
    if not isinstance(kg, dict):
        raise ContractError(ErrorCode.INTERNAL, "bundle KG is not an object")
    schema_block = dict(bundle.manifest["schema"])
    if schema:
        schema_block.update({k: v for k, v in schema.items() if k in ("id", "version", "sha256", "path", "label")})
    previous = kg.get("metadata")
    metadata = {**(previous if isinstance(previous, dict) else {}),
                "schema": schema_block["id"], "schema_info": schema_block, "f2w_bundle": bundle.name,
                "topic": bundle.manifest.get("topic"), "model": bundle.manifest.get("model"),
                "reconstructed": bool(bundle.manifest.get("reconstructed")), "exported_at": now_iso()}
    _write_json(target, {**kg, "metadata": metadata})
    return target.name


# ---------------------------------------------------------------------- legacy Settings hooks


def bundle_root(workdir: Path | str | None = None) -> Path | None:
    """``<workdir>/kgs`` for ``workdir`` or ``F2W_WORKDIR``; ``None`` when neither is set."""
    base = workdir if workdir is not None else (os.environ.get(WORKDIR_ENV) or "").strip() or None
    return Path(base) / BUNDLES_DIRNAME if base else None


def virtual_graph_path(name: str) -> str:
    """The path a bundle's KG has in the Settings graph list: ``kgs/<name>/matkg_<name>.json``.

    The file name is unique per bundle (the legacy graph id comes from it) and equals the export name.
    """
    return f"{VIRTUAL_PREFIX}{name}/{export_file_name(name)}"


def registered_graph_paths(workdir: Path | str | None = None) -> list[str]:
    """Virtual paths of every bundle whose ``kg.json`` is non-empty, sorted by name. Never raises."""
    root = bundle_root(workdir)
    if root is None:
        return []
    try:
        return [virtual_graph_path(b.name) for b in BundleStore(root).list()
                if (b.directory / KG_FILE).is_file() and (b.directory / KG_FILE).stat().st_size > 0]
    except OSError as exc:
        logger.debug("bundle listing failed: %s", type(exc).__name__)
        return []


_VIRTUAL_RE = re.compile(r"^kgs/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/matkg_([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.json$")


def _parse_virtual(path: str) -> str | None:
    text = str(path).replace("\\", "/")
    match = _VIRTUAL_RE.fullmatch(text)
    return match.group(1) if match and match.group(1) == match.group(2) else None


def resolve_registered_graph(path: str, workdir: Path | str | None = None) -> Path | None:
    """The real ``kg.json`` behind a virtual bundle path, or ``None`` when ``path`` is not one.

    Returns ``None`` (so legacy resolution proceeds and refuses the path) for anything that is not exactly
    ``kgs/<name>/matkg_<name>.json`` of an existing, non-linked bundle.
    """
    name = _parse_virtual(path)
    root = bundle_root(workdir)
    if name is None or root is None:
        return None
    store = BundleStore(root)
    try:
        if not store.exists(name):
            return None
    except ContractError:  # an invalid name is no bundle
        return None
    target = store.directory(name) / KG_FILE
    if target.is_symlink() or not target.is_file():
        return None
    return target.resolve()


def schema_path_for_registered_graph(path: str, workdir: Path | str | None = None) -> str | None:
    """Manifest-based schema lookup: the schema file path locked in the bundle behind ``path``.

    Returns the repository-relative schema path (e.g. ``storage/schema/v0.3.1/matkg_schema.yaml``) or ``None``
    when ``path`` is not a registered bundle graph. Legacy ``schema_path_for_graph`` (file-name based) is
    not changed; a caller that wants bundle schemas asks this first.
    """
    name = _parse_virtual(path)
    root = bundle_root(workdir)
    if name is None or root is None:
        return None
    try:
        bundle = BundleStore(root).get(name)
    except ContractError:  # an invalid name is no bundle
        return None
    value = bundle.manifest["schema"].get("path")
    return str(value) if value else None


__all__ = (
    "BUNDLES_DIRNAME", "BUNDLE_FILES", "Bundle", "BundleStore", "PaperRef", "RELEASE_DIR_ENV", "WORKDIR_ENV",
    "bundle_root", "continue_from_kg", "default_release_dir", "ensure_schema_locked", "export_bundle",
    "export_file_name", "registered_graph_paths", "resolve_registered_graph",
    "schema_path_for_registered_graph", "validate_name", "virtual_graph_path",
)
