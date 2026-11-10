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

Edit reconciliation (json_kg_contract.md 6.2.2; ``f2w-kg-snapshot --reconcile-edits``): :func:`reconcile_snapshot`
freezes ``v<N+1>`` from the live snapshot's own ``graph.json`` bytes plus the edit journal (a JSON patch, no
json2kg rebuild), with ``parent.relation == "edit_reconciliation"`` and an ``edit_reconciliation`` manifest block.
In both builders the journal entries are advanced to ``reconciled`` only after the frozen directory has been
re-read and hash-checked (:func:`~app.modules.graph.releases.verify_snapshot_dir`); the commit that later holds
the version is recorded on the same entries by :func:`record_reconciliation_commit`, never assumed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
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

from .edit_journal import EditJournal, EditRecord, unreconciled
from .edit_replay import ReplayResult, record_conflicts, replay_journal
from .releases import (
    SNAPSHOT_FILES,
    DisclosureReport,
    ReleaseVerificationError,
    VerifiedSnapshot,
    check_disclosure,
    lookup_snapshot,
    sha256_bytes,
    verify_snapshot_dir,
    walk_ancestry,
)
from .snapshot_manifest import (
    CORPUS_RE,
    PARENT_RELATIONS,
    SNAPSHOT_ID_RE,
    CompatPath,
    CorpusRevision,
    Created,
    DisclosureBlock,
    EditReconciliation,
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
from .snapshots import graph_id_for_snapshot, serialize_like_json2kg

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
    **kwargs: Any,  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
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
        edit_journal / journal_target / journal_scope: T18D (T23C left replay unwired). When a journal is
            given, every edit it still owes the scope is replayed onto the rebuilt graph
            (:func:`~app.modules.graph.edit_replay.replay_journal`) before the graph is frozen. Each edit
            ends *kept*, *already present* or recorded as a ``conflict`` (the build still succeeds; the
            conflicts block the next import until reviewed). Kept and already-present edits are advanced to
            ``reconciled`` with this snapshot's id once the snapshot exists. ``journal_scope`` defaults to
            ``(corpus,)``; ``journal_target`` is the Tiled target the edits were journaled against.
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
    edit_journal: EditJournal | None = None
    journal_target: str | None = None
    journal_scope: Sequence[str] | None = None


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
    reconciled_edits: tuple[str, ...] = ()
    edit_conflicts: tuple[str, ...] = ()

    @property
    def commit_ready(self) -> bool:
        """True when the disclosure check passed or carries an owner waiver."""
        return self.disclosure.commit_ready


@dataclass(frozen=True)
class ReconcileRequest:
    """Freeze the next version of a corpus from its live snapshot plus the edit journal (no terms, no json2kg).

    Attributes:
        corpus: Corpus name.
        storage_kg: The ``storage/kg`` directory holding ``<corpus>/<parent_snapshot_id>/``.
        parent_snapshot_id: The live snapshot the edits were made against; it must verify. The new version's
            ``parent.relation`` is always ``edit_reconciliation``.
        edit_journal / journal_target / journal_scope: The per-corpus journal, the Tiled target its entries were
            journaled against and the scope (default ``(corpus,)``).
        journal_ref: Repo-relative path or ``private:`` reference recorded for the journal (the file itself is
            operator state, never committed).
        snapshot_id: ``v<N>``; default the next free number.
        parent_data_commit: The commit that holds the parent, when known (recorded in ``parent.data_commit``).
        code_commit / run_id / profile / now / owner_waiver_ref / disclosure_allowlist / registry /
            allow_pending_review_schema: As :class:`SnapshotRequest`. The schema is the parent's.
    """

    corpus: str
    storage_kg: Path
    parent_snapshot_id: str
    edit_journal: EditJournal
    journal_target: str
    journal_ref: str
    journal_scope: Sequence[str] | None = None
    snapshot_id: str | None = None
    parent_data_commit: str | None = None
    code_commit: str | None = None
    run_id: str | None = None
    profile: str | None = "local"
    now: datetime | None = None
    owner_waiver_ref: str | None = None
    disclosure_allowlist: frozenset[str] = frozenset()
    registry: SchemaRegistry | None = None
    allow_pending_review_schema: bool = False


@dataclass(frozen=True)
class ReconciliationCommit:
    """Outcome of :func:`record_reconciliation_commit`."""

    corpus: str
    snapshot_id: str
    data_commit: str
    graph_sha256: str
    recorded: tuple[str, ...]
    already_recorded: tuple[str, ...]


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
    except (OSError, ValueError, IndexError):  # no readable packed-refs: ref not found
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


def _choose_schema(schema_id: str | None, corpus: str, registry: SchemaRegistry, *,
                   allow_pending_review: bool, source_when_named: str = "caller") -> tuple[SchemaEntry, str]:
    entry: SchemaEntry | None
    if schema_id:
        entry, source = registry.get(schema_id), source_when_named
    else:
        entry = registry.for_graph(f"matkg_{corpus}_v1.json")
        source = "filename_pattern"
        if entry is None:
            entry, source = registry.get(DEFAULT_SCHEMA_ID), "default"
    if entry is None:
        raise SnapshotError("no schema can be bound: the default schema is not registered")
    if not entry.is_linkml or entry.known_broken:
        raise SnapshotError(f"{entry.path} cannot be bound: not a selectable LinkML schema")
    if entry.pending_review and not allow_pending_review:
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
            logger.debug("terms path %s is outside the resource root %s", request.terms_path, root)
    raise SnapshotError("terms_ref is required (a repo-relative path or private: reference) when the terms file "
                        "is outside the repository; absolute local paths are not recorded")


def _helper_for(entry: Any, fingerprint: str) -> Any:  # noqa: ANN401  # Any: schema entry (duck-typed: donor object or typed record)
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


def _reconcile_edits(journal: EditJournal | None, replay: ReplayResult | None, corpus: str, snapshot_id: str,
                     now: datetime) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """After the snapshot is frozen *and verified*: carried edits become ``reconciled``, conflicts are journaled."""
    if journal is None or replay is None:
        return (), ()
    latest = journal.latest()
    carried = [e for e in sorted({*replay.kept, *replay.already}) if latest[e].state != "reconciled"]
    for edit_id in carried:
        journal.advance(edit_id, "reconciled", now, reconciled_in=snapshot_id,
                        note=f"carried into {corpus}/{snapshot_id}")
    conflicts = record_conflicts(journal, replay, now)
    return tuple(carried), tuple(conflicts)


def _verify_frozen(result: SnapshotResult) -> VerifiedSnapshot:
    """Re-read the frozen directory and flat copy from disk and hash-check them against the manifest.

    The journal is advanced only on the strength of this re-read, never on the bytes still held in memory.
    """
    try:
        verified = verify_snapshot_dir(result.directory)
    except ReleaseVerificationError as exc:
        raise SnapshotError(f"frozen snapshot {result.corpus}/{result.snapshot_id} does not read back: {exc}") from None
    if verified.graph_sha256 != result.graph_sha256 or sha256_bytes(result.flat_path.read_bytes()) != result.graph_sha256:
        raise SnapshotError(f"frozen snapshot {result.corpus}/{result.snapshot_id} read back with other bytes")
    return verified


def _resolve_storage(storage_kg: Path | None, root: Path | None) -> Path:
    resolved = Path(storage_kg) if storage_kg is not None else ((root / "storage" / "kg") if root else None)
    if resolved is None:
        raise SnapshotError("storage_kg is required (no resource root is configured)")
    return resolved.resolve()


def _resolve_code_commit(code_commit: str | None, root: Path | None) -> str:
    found = code_commit or (_git_head(root) if root else None)
    if not found:
        raise SnapshotError("code_commit could not be determined; pass code_commit")
    return found


@dataclass(frozen=True)
class _FreezeSpec:
    """Everything the freeze step needs that does not depend on the graph bytes."""

    corpus: str
    storage_kg: Path
    snapshot_id: str | None
    parent: Parent | None
    entry: SchemaEntry
    selection_source: str
    registry: SchemaRegistry
    extraction: ExtractionBlock
    corpus_revision: CorpusRevision
    inputs: list[InputRef]
    code_commit: str
    run_id: str | None
    profile: str | None
    now: datetime
    disclosure_allowlist: frozenset[str]
    owner_waiver_ref: str | None
    edit_reconciliation: EditReconciliation | None = None


def _freeze(spec: _FreezeSpec, build: Callable[[Path], tuple[dict[str, Any], bytes]],
            warnings: list[str]) -> SnapshotResult:
    """Build the graph in a private staging directory, validate it and rename it into place atomically.

    ``build(staging)`` returns the graph and its exact bytes; it may use ``staging`` as scratch space (anything it
    leaves there other than ``graph.json`` must be removed by it). Nothing outside ``storage_kg`` is written.
    """
    corpus_dir = spec.storage_kg / spec.corpus
    corpus_dir.mkdir(parents=True, exist_ok=True)
    staging = corpus_dir / f".staging-{uuid.uuid4().hex}"
    flat_tmp = spec.storage_kg / f".flat-{uuid.uuid4().hex}.tmp"
    claimed_flat: Path | None = None
    try:
        staging.mkdir()
        graph, graph_bytes = build(staging)
        graph_file = staging / "graph.json"
        if graph_file.exists():
            graph_file.unlink()
        _write_synced(graph_file, graph_bytes)
        stats = _graph_stats(graph_bytes)

        fingerprint = closure_fingerprint(spec.entry)
        report = validate_graph(graph, spec.entry, registry=spec.registry, selection_source=spec.selection_source,
                                helper=_helper_for(spec.entry, fingerprint), fingerprint=fingerprint)
        report_bytes = report.canonical_bytes()
        _write_synced(staging / "validation.json", report_bytes)
        schema_block = SchemaBlock(name=spec.entry.schema_id, path=spec.entry.path, sha256=spec.entry.sha256,
                                   version=spec.entry.version, import_fingerprint=fingerprint)
        graph_sha = sha256_bytes(graph_bytes)

        numbers = _existing_numbers(corpus_dir, spec.storage_kg, spec.corpus)
        candidate = int(spec.snapshot_id[1:]) if spec.snapshot_id else (max(numbers, default=0) + 1)
        for _attempt in range(_MAX_ID_ATTEMPTS):
            snapshot_id = f"v{candidate}"
            final = corpus_dir / snapshot_id
            flat = spec.storage_kg / f"matkg_{spec.corpus}_{snapshot_id}.json"
            rel_base = f"storage/kg/{spec.corpus}/{snapshot_id}"
            rel_flat = f"storage/kg/{flat.name}"
            disclosure = check_disclosure(
                graph, paths=[f"{rel_base}/{name}" for name in SNAPSHOT_FILES] + [rel_flat],
                allowlist=spec.disclosure_allowlist, waiver_ref=spec.owner_waiver_ref)
            manifest = KGManifest(
                corpus=spec.corpus, snapshot_id=snapshot_id,
                graph_id=graph_id_for_snapshot(final / "graph.json"),
                graph=GraphBlock(sha256=graph_sha, size_bytes=len(graph_bytes), node_count=stats["nodes"],
                                 edge_count=stats["edges"], codesnippet_count=stats["snippets"]),
                compat_paths=[CompatPath(path=rel_flat, sha256=graph_sha)],
                parent=spec.parent,
                created=Created(at=utc_iso(spec.now), tool=TOOL, code_commit=spec.code_commit,
                                json2kg_sha256=sha256_bytes(Path(json2kg.__file__).read_bytes()),
                                run_id=spec.run_id,
                                profile=cast(Literal["local", "als-dev", "als-prod", "ci"] | None, spec.profile)),
                schema=schema_block, extraction=spec.extraction, corpus_revision=spec.corpus_revision,
                inputs=list(spec.inputs),
                validation=ValidationBlock(status=cast(Literal["pass", "fail"], report.status), report_sha256=report.sha256,
                                           report_ref=f"{rel_base}/validation.json", suite=VALIDATION_SUITE),
                disclosure=DisclosureBlock(check_version=disclosure.check_version, status=cast(Literal["pass", "fail", "owner_waiver"], disclosure.status),
                                           report_sha256=disclosure.sha256, waiver_ref=disclosure.waiver_ref),
                edit_reconciliation=spec.edit_reconciliation,
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
                logger.debug("snapshot %s already claimed by another job", final)
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
                    snapshot_id=snapshot_id, corpus=spec.corpus, directory=final,
                    graph_path=final / "graph.json", manifest_path=final / "manifest.json",
                    validation_path=final / "validation.json", flat_path=flat, manifest=manifest,
                    contract_manifest=contract, graph_sha256=graph_sha,
                    validation_status=report.status, disclosure=disclosure, warnings=warnings)
            if claimed_flat is not None:
                claimed_flat.unlink(missing_ok=True)
                claimed_flat = None
            if spec.snapshot_id is not None:
                raise SnapshotExistsError(
                    f"snapshot {spec.corpus}/{snapshot_id} already exists; snapshots are immutable "
                    "(create a new version instead)")
            numbers = _existing_numbers(corpus_dir, spec.storage_kg, spec.corpus)
            candidate = max(numbers + [candidate]) + 1
        raise SnapshotExistsError("could not allocate a free snapshot id")
    finally:
        flat_tmp.unlink(missing_ok=True)
        if claimed_flat is not None:  # an exception between claiming and publishing
            claimed_flat.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _with_edits(result: SnapshotResult, journal: EditJournal | None, replay: ReplayResult | None,
                now: datetime) -> SnapshotResult:
    """Verify the frozen bytes on disk, then (and only then) advance the journal."""
    _verify_frozen(result)
    reconciled, conflicts = _reconcile_edits(journal, replay, result.corpus, result.snapshot_id, now)
    return SnapshotResult(**{**result.__dict__, "reconciled_edits": reconciled, "edit_conflicts": conflicts})


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
    storage_kg = _resolve_storage(request.storage_kg, root)
    code_commit = _resolve_code_commit(request.code_commit, root)
    registry = request.registry or SchemaRegistry.discover()
    entry, selection_source = _choose_schema(request.schema_id, request.corpus, registry,
                                             allow_pending_review=request.allow_pending_review_schema)
    warnings: list[str] = []
    extraction = _extraction(request, warnings)
    terms_ref = _terms_ref(request)
    now = request.now or datetime.now(UTC)
    parent = _verified_parent(storage_kg / request.corpus, request)
    checksum = sources_checksum(list(request.source_sha256))
    corpus_revision = CorpusRevision(
        id=request.corpus_revision_id or f"{request.corpus}-{checksum[:12]}",
        source_count=len(request.source_sha256), sources_checksum=checksum)
    terms_bytes = terms_path.read_bytes()  # the only access to the caller's file
    replay: ReplayResult | None = None

    def build(staging: Path) -> tuple[dict[str, Any], bytes]:
        nonlocal replay
        private_terms = staging / "terms.input.json"
        private_terms.write_bytes(terms_bytes)
        graph_file = staging / "graph.json"
        try:
            json2kg.convert_terms_to_graph(private_terms, graph_file)
        except Exception as exc:  # json2kg raises many types; none of them leaves a snapshot
            raise SnapshotBuildError(f"json2kg could not convert the terms input: {exc}") from None
        private_terms.unlink()
        graph_bytes = graph_file.read_bytes()
        graph: dict[str, Any] = json.loads(graph_bytes.decode("utf-8"))
        if request.edit_journal is not None:
            replay = replay_journal(graph, request.edit_journal, target=request.journal_target or "",
                                    scope=tuple(request.journal_scope or (request.corpus,)))
            if replay.kept:
                graph = replay.graph
                graph_bytes = serialize_like_json2kg(graph)
            for conflict in replay.conflicts:
                warnings.append(f"edit {conflict.edit_id} not carried: {conflict.reason}")
        return graph, graph_bytes

    spec = _FreezeSpec(
        corpus=request.corpus, storage_kg=storage_kg, snapshot_id=request.snapshot_id, parent=parent, entry=entry,
        selection_source=selection_source, registry=registry, extraction=extraction, corpus_revision=corpus_revision,
        inputs=[InputRef(role="terms_snapshot", sha256=sha256_bytes(terms_bytes), ref=terms_ref)],
        code_commit=code_commit, run_id=request.run_id, profile=request.profile, now=now,
        disclosure_allowlist=request.disclosure_allowlist, owner_waiver_ref=request.owner_waiver_ref)
    result = _freeze(spec, build, warnings)
    return _with_edits(result, request.edit_journal, replay, now)


def reconcile_snapshot(request: ReconcileRequest) -> SnapshotResult:
    """Freeze ``v<N+1>`` of a corpus from its live snapshot's bytes plus the edits the journal still owes it.

    The parent's ``graph.json`` is re-verified and used as the base (no json2kg, no terms file). Every journal
    entry of the target/scope is replayed; the version is created only when at least one edit changes the
    graph. Edits that are *kept* or *already present* are advanced to ``reconciled`` (``reconciled_in`` = the
    new id) after the frozen directory has been re-read and hash-checked; conflicts are journaled as such and
    keep blocking the next import until reviewed. The schema, extraction revisions and corpus revision are the
    parent's; the manifest's ``inputs`` name the parent graph (``seed_graph``) and the journal (``edit_journal``).

    Raises:
        SnapshotError: nothing to reconcile (no open edit for the target/scope outside the parent's lineage, or
            no edit could be applied), an unverifiable parent, an unresolvable code commit, or I/O failure.
        SnapshotExistsError: the requested id, or its flat name, already exists.
    """
    if not CORPUS_RE.match(request.corpus or ""):
        raise SnapshotError("corpus must match ^[a-z0-9][a-z0-9_-]*$")
    if request.snapshot_id is not None and not SNAPSHOT_ID_RE.match(request.snapshot_id):
        raise SnapshotError("snapshot_id must be v<N>")
    if not SNAPSHOT_ID_RE.match(request.parent_snapshot_id or ""):
        raise SnapshotError("parent_snapshot_id must be v<N>")
    if not request.journal_target:
        raise SnapshotError("journal_target is required (the Tiled target the edits were journaled against)")
    root = resource_root()
    storage_kg = _resolve_storage(request.storage_kg, root)
    code_commit = _resolve_code_commit(request.code_commit, root)
    corpus_dir = storage_kg / request.corpus
    parent_dir = corpus_dir / request.parent_snapshot_id
    try:
        live = verify_snapshot_dir(parent_dir)
    except ReleaseVerificationError as exc:
        raise SnapshotError(f"live snapshot {request.parent_snapshot_id} does not verify: {exc}") from None
    registry = request.registry or SchemaRegistry.discover()
    entry, selection_source = _choose_schema(live.manifest.schema_block.name, request.corpus, registry,
                                             allow_pending_review=request.allow_pending_review_schema,
                                             source_when_named="parent")
    scope = tuple(request.journal_scope or (request.corpus,))
    lineage = (request.parent_snapshot_id, *walk_ancestry(parent_dir, live.manifest))
    owed = unreconciled(request.edit_journal, target=request.journal_target, scope=scope, lineage=lineage)
    if not owed:
        raise SnapshotError(f"nothing to reconcile: the journal owes {request.corpus}/{request.parent_snapshot_id} "
                            f"no edit for target {request.journal_target!r} and scope {list(scope)}")
    journal_sha = request.edit_journal.sha256()
    graph: dict[str, Any] = json.loads(live.graph_bytes.decode("utf-8"))
    replay = replay_journal(graph, request.edit_journal, target=request.journal_target, scope=scope)
    warnings = [f"edit {c.edit_id} not carried: {c.reason}" for c in replay.conflicts]
    if not replay.kept:
        detail = "; ".join(f"{c.edit_id}: {c.reason} ({c.detail})" for c in replay.conflicts) or (
            f"edits {sorted(replay.already)} are already present in the live graph")
        raise SnapshotError(f"no edit changes {request.corpus}/{request.parent_snapshot_id}; no version is "
                            f"created: {detail}")
    graph_bytes = serialize_like_json2kg(replay.graph)
    now = request.now or datetime.now(UTC)
    parent = Parent(snapshot_id=request.parent_snapshot_id, graph_sha256=live.graph_sha256,
                    data_commit=request.parent_data_commit, relation="edit_reconciliation")
    spec = _FreezeSpec(
        corpus=request.corpus, storage_kg=storage_kg, snapshot_id=request.snapshot_id, parent=parent, entry=entry,
        selection_source=selection_source, registry=registry, extraction=live.manifest.extraction,
        corpus_revision=live.manifest.corpus_revision,
        inputs=[InputRef(role="seed_graph", sha256=live.graph_sha256,
                         ref=f"storage/kg/{request.corpus}/{request.parent_snapshot_id}/graph.json"),
                InputRef(role="edit_journal", sha256=journal_sha, ref=request.journal_ref)],
        code_commit=code_commit, run_id=request.run_id, profile=request.profile, now=now,
        disclosure_allowlist=request.disclosure_allowlist, owner_waiver_ref=request.owner_waiver_ref,
        edit_reconciliation=EditReconciliation(journal_sha256=journal_sha, base_snapshot_id=request.parent_snapshot_id,
                                               applied_edit_count=len(replay.kept), journal_ref=request.journal_ref))
    result = _freeze(spec, lambda _staging: (replay.graph, graph_bytes), warnings)
    return _with_edits(result, request.edit_journal, replay, now)


def record_reconciliation_commit(*, journal: EditJournal, storage_kg: Path, corpus: str, snapshot_id: str,
                                 repo: Path, data_commit: str, now: datetime | None = None) -> ReconciliationCommit:
    """Record on the journal entries reconciled into ``snapshot_id`` the commit that verifiably holds it.

    The local frozen directory is re-verified, the committed bytes are selected from ``repo`` at ``data_commit``
    (``releases.lookup_snapshot``: full commit id only, read-only Git) and the two must agree on graph and manifest
    hashes. Each ``reconciled`` entry with ``reconciled_in == snapshot_id`` then gets one more ``reconciled``
    record carrying ``data_commit``; an entry already recorded against this commit is left alone; one recorded
    against another commit is refused. Nothing is written to Git.

    Raises:
        SnapshotError: the local or committed snapshot does not verify, their bytes differ, no entry is reconciled
            into ``snapshot_id``, or an entry already names a different commit.
    """
    if not SNAPSHOT_ID_RE.match(snapshot_id or ""):
        raise SnapshotError("snapshot_id must be v<N>")
    try:
        local = verify_snapshot_dir(Path(storage_kg).resolve() / corpus / snapshot_id)
    except ReleaseVerificationError as exc:
        raise SnapshotError(f"local snapshot {corpus}/{snapshot_id} does not verify: {exc}") from None
    try:
        committed = lookup_snapshot(Path(repo), data_commit, corpus, snapshot_id)
    except ReleaseVerificationError as exc:
        raise SnapshotError(f"commit does not hold {corpus}/{snapshot_id} ({exc.code}): {exc}") from None
    if committed.graph.sha256 != local.graph_sha256 or committed.manifest_artifact.sha256 != local.manifest_sha256:
        raise SnapshotError(f"the bytes committed at {data_commit} differ from the local {corpus}/{snapshot_id}")
    entries: list[EditRecord] = [r for r in journal.latest().values()
                                 if r.state == "reconciled" and r.reconciled_in == snapshot_id]
    if not entries:
        raise SnapshotError(f"no journal entry is reconciled into {corpus}/{snapshot_id}")
    other = sorted(r.edit_id for r in entries if r.data_commit not in (None, committed.data_commit))
    if other:
        raise SnapshotError(f"edits {other} are already recorded against another commit")
    at = now or datetime.now(UTC)
    recorded, already = [], []
    for rec in sorted(entries, key=lambda r: r.edit_id):
        if rec.data_commit == committed.data_commit:
            already.append(rec.edit_id)
            continue
        journal.advance(rec.edit_id, "reconciled", at, reconciled_in=snapshot_id, data_commit=committed.data_commit,
                        note=f"{corpus}/{snapshot_id} committed as {committed.data_commit}")
        recorded.append(rec.edit_id)
    return ReconciliationCommit(corpus=corpus, snapshot_id=snapshot_id, data_commit=committed.data_commit,
                                graph_sha256=local.graph_sha256, recorded=tuple(recorded),
                                already_recorded=tuple(already))
