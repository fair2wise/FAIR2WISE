"""Create immutable, versioned JSON KG snapshots (T23B).

``json2kg`` stays the KG creation logic (D21): this module wraps ``convert_terms_to_graph`` /
``build_graph`` and never edits them. Building needs no Tiled client, server, network or credentials; nothing
here writes to Tiled (the JSON-to-Tiled importer is T08B) and nothing calls a shell or subprocess.

Layout written under ``<storage_kg>`` (default ``storage/kg``; json_kg_contract.md section 4, release_contract.md
section 2)::

    <corpus>/<snapshot-id>/graph.json       exact json2kg bytes
    <corpus>/<snapshot-id>/manifest.json    sidecar (snapshot_manifest.KGManifest)
    <corpus>/<snapshot-id>/validation.json  schema validation report (hash bound in the manifest)
    matkg_<corpus>_<snapshot-id>.json       byte-identical flat compatibility copy (never a symlink)

Guarantees: an existing snapshot (directory or flat name) is never overwritten, a repair or rebuild gets a new
``v<N+1>`` with ``parent``; the caller's terms file is only read; a failed build leaves no partial snapshot
(work happens in a private staging directory that is renamed into place atomically); two jobs never share
staging space. A mutable working file (``workdir/kg.json``, ``matkg_<model>_<ts>.json``) is never an approved
artifact: only the frozen layout passes ``releases.verify_*``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Literal, cast
import uuid

from app.modules import json2kg
from app.modules.contracts.changes import SnapshotManifest
from app.modules.resources import resource_root
from app.modules.schema.loading import closure_fingerprint, load_helper
from app.modules.schema.registry import DEFAULT_SCHEMA_ID, SchemaEntry, SchemaRegistry
from app.modules.schema.validation import validate_graph

from .releases import (
    SNAPSHOT_FILES,
    DisclosureReport,
    ReleaseVerificationError,
    check_disclosure,
    sha256_bytes,
    verify_snapshot_dir,
)
from .snapshot_manifest import (
    CORPUS_RE,
    PARENT_RELATIONS,
    SNAPSHOT_ID_RE,
    CompatPath,
    CorpusRevision,
    Created,
    DisclosureBlock,
    ExtractionBlock,
    ExtractionRevision,
    GraphBlock,
    InputRef,
    KGManifest,
    Parent,
    SchemaBlock,
    ValidationBlock,
    sources_checksum,
    to_contract_manifest,
    utc_iso,
)
from .snapshots import graph_id_for_snapshot

logger = logging.getLogger(__name__)

_HELPERS: dict[str, Any] = {}
_HELPERS_LOCK = threading.Lock()

TOOL = "app.modules.json2kg.convert_terms_to_graph"
VALIDATION_SUITE = "schema.validation.validate_graph/1"
_MAX_ID_ATTEMPTS = 8


class SnapshotError(ValueError):
    """A snapshot could not be created; nothing was left behind."""


class SnapshotExistsError(SnapshotError):
    """The snapshot id (directory or flat compatibility name) is already taken. Snapshots are immutable."""


class SnapshotBuildError(SnapshotError):
    """json2kg could not convert the terms input (the input is not a valid extracted-terms record)."""


def build_graph_isolated(
    raw_terms: Iterable[dict[str, Any]],
    code_snippets: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    """``json2kg.build_graph`` on deep copies of the inputs.

    ``build_graph`` aliases the caller's list fields into the graph and its merge appends to the first
    term's ``source_papers`` (json_kg_contract.md 2.7.1), so re-running it on the same in-memory objects
    changes the result. In-process callers use this wrapper instead; the output for fresh input is identical.
    """
    return json2kg.build_graph(copy.deepcopy(list(raw_terms)), copy.deepcopy(code_snippets), **kwargs)


@dataclass(frozen=True)
class SnapshotRequest:
    """What to build. Paths are inputs only; nothing outside ``storage_kg`` is written.

    Attributes:
        corpus: Corpus name (``^[a-z0-9][a-z0-9_-]*$``).
        terms_path: Extracted-terms JSON (json2kg input). Read, never modified.
        storage_kg: The ``storage/kg`` directory to write into (default: resource root ``storage/kg``).
        schema_id: Registered LinkML schema to bind; default is chosen from the flat file name
            (``multi_kg`` patterns) and falls back to ``matkg_schema``.
        snapshot_id: ``v<N>``; default is the next free number for the corpus.
        parent_snapshot_id / parent_relation / parent_data_commit: Lineage; the parent must exist and verify.
        extraction_revisions: Contributing extraction runs; default is one ``unknown`` revision (never an
            invented model).
        source_sha256: Per-source hashes of the corpus.
        corpus_revision_id: Label of the source set; default ``<corpus>-<checksum[:12]>``.
        terms_ref: Repo-relative path or ``private:`` reference recorded for the terms input (required when
            ``terms_path`` is outside the repository).
        code_commit: Full Git object id of the building code (default: read from ``.git`` without a shell).
        run_id / profile: Recorded in the manifest.
        now: Creation time (tests); default UTC now.
        owner_waiver_ref / disclosure_allowlist: Disclosure exceptions (reviewed, by key).
        registry: Schema registry to use (default: discover the packaged one).
        allow_pending_review_schema: Bind a schema whose set is still pending owner review (T22C
            ``rsoxs_schema@0.3.0``...). Off by default: no build uses an unreviewed schema unless asked twice,
            by ``schema_id`` and by this flag.
    """

    corpus: str
    terms_path: Path
    storage_kg: Path | None = None
    schema_id: str | None = None
    snapshot_id: str | None = None
    parent_snapshot_id: str | None = None
    parent_relation: str = "rebuild"
    parent_data_commit: str | None = None
    extraction_revisions: Sequence[Mapping[str, Any]] = ()
    source_sha256: Sequence[str] = ()
    corpus_revision_id: str | None = None
    terms_ref: str | None = None
    code_commit: str | None = None
    run_id: str | None = None
    profile: str | None = "local"
    now: datetime | None = None
    owner_waiver_ref: str | None = None
    disclosure_allowlist: frozenset[str] = frozenset()
    registry: SchemaRegistry | None = None
    allow_pending_review_schema: bool = False


@dataclass(frozen=True)
class SnapshotResult:
    """A created snapshot."""

    snapshot_id: str
    corpus: str
    directory: Path
    graph_path: Path
    manifest_path: Path
    validation_path: Path
    flat_path: Path
    manifest: KGManifest
    contract_manifest: SnapshotManifest
    graph_sha256: str
    validation_status: str
    disclosure: DisclosureReport
    warnings: list[str] = field(default_factory=list)

    @property
    def commit_ready(self) -> bool:
        """True when the disclosure check passed or carries an owner waiver."""
        return self.disclosure.commit_ready


def _git_head(repo: Path) -> str | None:
    """Current commit of ``repo`` read from ``.git`` files (no Git process)."""
    dot_git = repo / ".git"
    try:
        if dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8").strip()
            gitdir = Path(text.split(":", 1)[1].strip()) if text.startswith("gitdir:") else None
            if gitdir is None:
                return None
            gitdir = gitdir if gitdir.is_absolute() else (repo / gitdir)
        else:
            gitdir = dot_git
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            if (base / ref).is_file():
                return (base / ref).read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref) and not line.startswith("#"):
                    return line.split(" ", 1)[0]
    except (OSError, ValueError, IndexError):
        return None
    return None


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _existing_numbers(corpus_dir: Path, storage_kg: Path, corpus: str) -> list[int]:
    numbers: list[int] = []
    if corpus_dir.is_dir():
        numbers += [int(p.name[1:]) for p in corpus_dir.iterdir() if SNAPSHOT_ID_RE.match(p.name)]
    prefix = f"matkg_{corpus}_v"
    for flat in storage_kg.glob(f"{prefix}*.json"):
        tail = flat.name[len(prefix):-len(".json")]
        if tail.isdigit():
            numbers.append(int(tail))
    return numbers


def _choose_schema(request: SnapshotRequest, registry: SchemaRegistry) -> tuple[SchemaEntry, str]:
    if request.schema_id:
        entry, source = registry.get(request.schema_id), "caller"
    else:
        entry = registry.for_graph(f"matkg_{request.corpus}_v1.json")
        source = "filename_pattern"
        if entry is None:
            entry, source = registry.get(DEFAULT_SCHEMA_ID), "default"
    if not entry.is_linkml or entry.known_broken:
        raise SnapshotError(f"{entry.path} cannot be bound: not a selectable LinkML schema")
    if entry.pending_review and not request.allow_pending_review_schema:
        raise SnapshotError(f"{entry.path} is pending owner review (schema set {entry.schema_set}); pass "
                            "allow_pending_review_schema=True to build with it")
    return entry, source


def _extraction(request: SnapshotRequest, warnings: list[str]) -> ExtractionBlock:
    if request.extraction_revisions:
        return ExtractionBlock(revisions=[ExtractionRevision(**dict(r)) for r in request.extraction_revisions])
    warnings.append("no extraction revisions supplied: recorded as one unknown revision")
    return ExtractionBlock(revisions=[ExtractionRevision(extractor="unknown", version="unknown", unknown=True)])


def _terms_ref(request: SnapshotRequest) -> str:
    if request.terms_ref:
        return request.terms_ref
    root = resource_root()
    if root is not None:
        try:
            return Path(request.terms_path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    raise SnapshotError("terms_ref is required (a repo-relative path or private: reference) when the terms file "
                        "is outside the repository; absolute local paths are not recorded")


def _helper_for(entry: Any, fingerprint: str) -> Any:
    """Return the existing ``SchemaHelper`` for ``entry``, reused across builds.

    The closure fingerprint is the cache key, so any change to any imported file
    builds a fresh one.
    """
    with _HELPERS_LOCK:
        helper = _HELPERS.get(fingerprint)
        if helper is None:
            if len(_HELPERS) >= 8:
                _HELPERS.clear()
            helper = _HELPERS[fingerprint] = load_helper(entry)
        return helper


def _graph_stats(blob: bytes) -> dict[str, int]:
    data = json.loads(blob.decode("utf-8"))
    nodes = data.get("things") or []
    return {
        "nodes": len(nodes), "edges": len(data.get("associations") or []),
        "snippets": sum(
            1 for n in nodes if isinstance(n, dict) and str(n.get("type", "")).endswith("CodeSnippet")),
    }


def _verified_parent(corpus_dir: Path, request: SnapshotRequest) -> Parent | None:
    if not request.parent_snapshot_id:
        return None
    if request.parent_relation not in PARENT_RELATIONS:
        raise SnapshotError(f"parent_relation must be one of {PARENT_RELATIONS}")
    if not SNAPSHOT_ID_RE.match(request.parent_snapshot_id):
        raise SnapshotError("parent_snapshot_id must be v<N>")
    try:
        parent = verify_snapshot_dir(corpus_dir / request.parent_snapshot_id)
    except ReleaseVerificationError as exc:
        raise SnapshotError(f"parent snapshot {request.parent_snapshot_id} does not verify: {exc}") from None
    return Parent(snapshot_id=request.parent_snapshot_id, graph_sha256=parent.graph_sha256,
                  data_commit=request.parent_data_commit, relation=cast(
                      Literal["rebuild", "edit_reconciliation", "repair", "disclosure_split", "legacy_adoption"],
                      request.parent_relation))


def create_snapshot(request: SnapshotRequest) -> SnapshotResult:
    """Build a candidate graph from a terms file and freeze it as an immutable snapshot.

    Raises:
        SnapshotBuildError: json2kg rejected the input (nothing is written).
        SnapshotExistsError: the requested id, or its flat name, already exists.
        SnapshotError: invalid request, unresolvable code commit, unverifiable parent, or I/O failure.
        SchemaBindingError / RegistryError: schema selection problems.
    """
    if not CORPUS_RE.match(request.corpus or ""):
        raise SnapshotError("corpus must match ^[a-z0-9][a-z0-9_-]*$")
    if request.snapshot_id is not None and not SNAPSHOT_ID_RE.match(request.snapshot_id):
        raise SnapshotError("snapshot_id must be v<N>")
    terms_path = Path(request.terms_path)
    if not terms_path.is_file():
        raise SnapshotError("terms_path is not a file")
    root = resource_root()
    storage_kg = Path(request.storage_kg) if request.storage_kg is not None else (
        (root / "storage" / "kg") if root else None)
    if storage_kg is None:
        raise SnapshotError("storage_kg is required (no resource root is configured)")
    code_commit = request.code_commit or (_git_head(root) if root else None)
    if not code_commit:
        raise SnapshotError("code_commit could not be determined; pass code_commit")
    registry = request.registry or SchemaRegistry.discover()
    entry, selection_source = _choose_schema(request, registry)
    warnings: list[str] = []
    extraction = _extraction(request, warnings)
    terms_ref = _terms_ref(request)
    now = request.now or datetime.now(UTC)

    storage_kg = storage_kg.resolve()
    corpus_dir = storage_kg / request.corpus
    parent = _verified_parent(corpus_dir, request)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    staging = corpus_dir / f".staging-{uuid.uuid4().hex}"
    flat_tmp = storage_kg / f".flat-{uuid.uuid4().hex}.tmp"
    claimed_flat: Path | None = None
    try:
        staging.mkdir()
        terms_bytes = terms_path.read_bytes()  # the only access to the caller's file
        private_terms = staging / "terms.input.json"
        private_terms.write_bytes(terms_bytes)
        graph_file = staging / "graph.json"
        try:
            json2kg.convert_terms_to_graph(private_terms, graph_file)
        except Exception as exc:  # json2kg raises many types; none of them leaves a snapshot
            raise SnapshotBuildError(f"json2kg could not convert the terms input: {exc}") from None
        private_terms.unlink()
        graph_bytes = graph_file.read_bytes()
        graph = json.loads(graph_bytes.decode("utf-8"))
        stats = _graph_stats(graph_bytes)

        fingerprint = closure_fingerprint(entry)
        report = validate_graph(graph, entry, registry=registry, selection_source=selection_source,
                                helper=_helper_for(entry, fingerprint), fingerprint=fingerprint)
        report_bytes = report.canonical_bytes()
        _write_synced(staging / "validation.json", report_bytes)

        checksum = sources_checksum(list(request.source_sha256))
        corpus_revision = CorpusRevision(
            id=request.corpus_revision_id or f"{request.corpus}-{checksum[:12]}",
            source_count=len(request.source_sha256), sources_checksum=checksum)
        schema_block = SchemaBlock(name=entry.schema_id, path=entry.path, sha256=entry.sha256,
                                   version=entry.version, import_fingerprint=fingerprint)
        graph_sha = sha256_bytes(graph_bytes)

        numbers = _existing_numbers(corpus_dir, storage_kg, request.corpus)
        candidate = int(request.snapshot_id[1:]) if request.snapshot_id else (max(numbers, default=0) + 1)
        for attempt in range(_MAX_ID_ATTEMPTS):
            snapshot_id = f"v{candidate}"
            final = corpus_dir / snapshot_id
            flat = storage_kg / f"matkg_{request.corpus}_{snapshot_id}.json"
            rel_base = f"storage/kg/{request.corpus}/{snapshot_id}"
            rel_flat = f"storage/kg/{flat.name}"
            disclosure = check_disclosure(
                graph, paths=[f"{rel_base}/{name}" for name in SNAPSHOT_FILES] + [rel_flat],
                allowlist=request.disclosure_allowlist, waiver_ref=request.owner_waiver_ref)
            manifest = KGManifest(
                corpus=request.corpus, snapshot_id=snapshot_id,
                graph_id=graph_id_for_snapshot(final / "graph.json"),
                graph=GraphBlock(sha256=graph_sha, size_bytes=len(graph_bytes), node_count=stats["nodes"],
                                 edge_count=stats["edges"], codesnippet_count=stats["snippets"]),
                compat_paths=[CompatPath(path=rel_flat, sha256=graph_sha)],
                parent=parent,
                created=Created(at=utc_iso(now), tool=TOOL, code_commit=code_commit,
                                json2kg_sha256=sha256_bytes(Path(json2kg.__file__).read_bytes()),
                                run_id=request.run_id,
                                profile=cast(Literal["local", "als-dev", "als-prod", "ci"] | None, request.profile)),
                schema=schema_block, extraction=extraction, corpus_revision=corpus_revision,
                inputs=[InputRef(role="terms_snapshot", sha256=sha256_bytes(terms_bytes), ref=terms_ref)],
                validation=ValidationBlock(status=cast(Literal["pass", "fail"], report.status), report_sha256=report.sha256,
                                           report_ref=f"{rel_base}/validation.json", suite=VALIDATION_SUITE),
                disclosure=DisclosureBlock(check_version=disclosure.check_version, status=cast(Literal["pass", "fail", "owner_waiver"], disclosure.status),
                                           report_sha256=disclosure.sha256, waiver_ref=disclosure.waiver_ref),
            )
            contract = to_contract_manifest(manifest)
            manifest_file = staging / "manifest.json"
            if manifest_file.exists():
                manifest_file.unlink()
            _write_synced(manifest_file, manifest.to_bytes())
            try:
                if final.exists() or os.path.lexists(flat):
                    raise FileExistsError(str(final))
                if not flat_tmp.exists():
                    _write_synced(flat_tmp, graph_bytes)
                os.link(flat_tmp, flat)  # claims the flat name atomically; never overwrites
                claimed_flat = flat
                staging.rename(final)  # fails if another job created `final` first
            except FileExistsError:
                pass
            except OSError as exc:
                if claimed_flat is not None:
                    claimed_flat.unlink(missing_ok=True)
                    claimed_flat = None
                if final.exists():
                    pass
                else:
                    raise SnapshotError(f"could not freeze snapshot: {exc.strerror}") from None
            else:
                claimed_flat = None  # published: keep it
                return SnapshotResult(
                    snapshot_id=snapshot_id, corpus=request.corpus, directory=final,
                    graph_path=final / "graph.json", manifest_path=final / "manifest.json",
                    validation_path=final / "validation.json", flat_path=flat, manifest=manifest,
                    contract_manifest=contract, graph_sha256=graph_sha,
                    validation_status=report.status, disclosure=disclosure, warnings=warnings)
            if claimed_flat is not None:
                claimed_flat.unlink(missing_ok=True)
                claimed_flat = None
            if request.snapshot_id is not None:
                raise SnapshotExistsError(
                    f"snapshot {request.corpus}/{snapshot_id} already exists; snapshots are immutable "
                    "(create a new version instead)")
            numbers = _existing_numbers(corpus_dir, storage_kg, request.corpus)
            candidate = max(numbers + [candidate]) + 1
        raise SnapshotExistsError("could not allocate a free snapshot id")
    finally:
        flat_tmp.unlink(missing_ok=True)
        if claimed_flat is not None:  # an exception between claiming and publishing
            claimed_flat.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
