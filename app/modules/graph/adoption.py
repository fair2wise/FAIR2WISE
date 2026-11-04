"""Adopt legacy flat KG files as promotable nested releases, and build data-release descriptors (T26A, O-R1 Q2).

Owner decision O-R1 (2026-10-30, T26A Q2): the existing flat ``storage/kg/matkg_*.json`` files are adopted. A flat
file ``F`` becomes ``storage/kg/<corpus>/v<N>/{graph.json,manifest.json,validation.json}`` where:

* ``graph.json`` is a byte copy of ``F`` (same sha256, never re-serialized, never a symlink); ``F`` stays untouched;
* ``manifest.json`` has ``parent.relation = legacy_adoption`` and honest provenance: one ``unknown`` extraction
  revision (no invented model), no inputs, an empty source set, the schema bound by the file-name patterns of
  ``multi_kg`` (default ``matkg_schema``), ``created.code_commit`` = the adopting revision (an existing commit, as
  ``create_snapshot`` does), and the hashes of the validation and disclosure reports that were actually computed;
* a file is adopted only if it passes the commit-ready checks of release_contract.md section 4 (R1 paths, R2 format
  and schema validation, R3 secrets, R4 local paths, R5 bearer URLs, R7 raw bodies; R6 is informational per O-D1).
  Nothing is allowlisted here: an R3/R4 exception is an owner-reviewed decision, passed in by the caller.

Naming: ``matkg_<corpus>_v<N>.json`` maps to ``<corpus>/v<N>``. Any other legacy name becomes corpus ``<slug of
the stem>``, snapshot ``v1`` (D3.24, owner 2026-11-03: the R1 flat allowlist is extended to legacy names). In both
cases the flat file is listed in ``compat_paths`` so the paths readers use keep working; the nested ``graph.json``
copy is never shipped in images (``.dockerignore``).

The module never runs Git for writes and never overwrites a frozen directory.

Descriptors (release_contract.md 9.2) are built after a commit exists with :func:`build_adoption_descriptor` from
the objects at that commit, and checked with :func:`validate_descriptor` against the normative JSON Schema.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Literal, cast
import uuid

from app.modules import json2kg
from app.modules.f2w_agent.multi_kg import graph_id_for_path
from app.modules.resources import resource_root
from app.modules.schema.loading import closure_fingerprint
from app.modules.schema.registry import DEFAULT_SCHEMA_ID, SchemaEntry, SchemaRegistry
from app.modules.schema.validation import validate_graph

from .releases import (
    SNAPSHOT_FILES,
    ReleaseVerificationError,
    check_disclosure,
    lookup_snapshot,
    sha256_bytes,
    verify_git_artifact,
    verify_snapshot_dir,
)
from .snapshot import (
    VALIDATION_SUITE,
    SnapshotError,
    SnapshotExistsError,
    _git_head,
    _helper_for,
    _write_synced,
)
from .snapshot_manifest import (
    CORPUS_RE,
    SNAPSHOT_ID_RE,
    CompatPath,
    CorpusRevision,
    Created,
    DisclosureBlock,
    ExtractionBlock,
    ExtractionRevision,
    GraphBlock,
    KGManifest,
    Parent,
    SchemaBlock,
    ValidationBlock,
    sources_checksum,
    utc_iso,
)

ADOPTION_TOOL = ("app.modules.graph.adoption.adopt_legacy_flat_file "
                 "(legacy graph adopted byte for byte, not rebuilt; json2kg_sha256 is the adopting revision's)")
LEGACY_CORPUS_REVISION_ID = "legacy-unknown"
ADOPTION_CODE_COMMIT_RULE = "code_commit is the adopting revision (HEAD at adoption), an existing commit"

_FLAT_NAME = re.compile(r"^matkg_([a-z0-9][a-z0-9_-]*)_(v[1-9][0-9]*)\.json$")
_LEGACY_NAME = re.compile(r"^matkg_.+\.json$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)+$")

# Skip rule labels (stable; the contract's "Adoption results" subsection uses them).
RULE_R1 = "R1"
RULE_PLACEHOLDER = "placeholder"
RULE_R2_FORMAT = "R2-format"
RULE_R2_SCHEMA = "R2-schema"
RULE_R2_VALIDATION = "R2-validation"
RULE_TARGET_EXISTS = "target-exists"


class AdoptionRefused(SnapshotError):
    """A flat file was not adopted; nothing was written. ``rule`` names the failing check."""

    def __init__(self, rule: str, message: str, findings: Mapping[str, int] | None = None) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.findings = dict(findings or {})


@dataclass(frozen=True)
class AdoptionOutcome:
    """Result for one top-level ``storage/kg`` entry."""

    flat_name: str
    status: str  # "adopted" | "would_adopt" | "skipped"
    corpus: str | None = None
    snapshot_id: str | None = None
    graph_sha256: str | None = None
    rule: str | None = None
    detail: str = ""
    findings: Mapping[str, int] = field(default_factory=dict)
    directory: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready, path-free summary."""
        return {"flat_name": self.flat_name, "status": self.status, "corpus": self.corpus,
                "snapshot_id": self.snapshot_id, "graph_sha256": self.graph_sha256, "rule": self.rule,
                "detail": self.detail, "findings": dict(self.findings)}


def derive_identity(flat_name: str) -> tuple[str, str]:
    """``(corpus, snapshot_id)`` for a legacy flat file name (see the module docstring)."""
    match = _FLAT_NAME.match(flat_name)
    if match:
        return match.group(1), match.group(2)
    stem = flat_name[:-len(".json")] if flat_name.endswith(".json") else flat_name
    if stem.startswith("matkg_"):
        stem = stem[len("matkg_"):]
    corpus = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-_")
    if not CORPUS_RE.match(corpus or ""):
        raise AdoptionRefused(RULE_R1, "the file name does not yield a valid corpus name")
    return corpus, "v1"


def _choose_schema(registry: SchemaRegistry, flat_name: str, allow_pending_review_schema: bool) -> tuple[SchemaEntry, str]:
    entry = registry.for_graph(flat_name)
    source = "filename_pattern"
    if entry is None:
        entry, source = registry.get(DEFAULT_SCHEMA_ID), "default"
    if not entry.is_linkml or entry.known_broken:
        raise AdoptionRefused(RULE_R2_SCHEMA, f"{entry.schema_id} is not a selectable LinkML schema")
    if entry.pending_review and not allow_pending_review_schema:
        raise AdoptionRefused(RULE_R2_SCHEMA, f"{entry.schema_id} is pending owner review")
    return entry, source


def _check_flat_source(flat: Path) -> bytes:
    """R1 for the source entry, then its bytes. A symlink, a non-``matkg_*.json`` name or a non-file is refused."""
    if flat.is_symlink():
        raise AdoptionRefused(RULE_R1, "the flat file is a symlink; adoption needs a regular file")
    if not _LEGACY_NAME.match(flat.name) or not flat.is_file():
        raise AdoptionRefused(RULE_R1, "outside the path allowlist (not a matkg_*.json graph file)")
    return flat.read_bytes()


def _parse_legacy_graph(blob: bytes) -> Mapping[str, Any]:
    """R2 format: json2kg things/associations, serialized exactly as the manifest's ``serialization`` states."""
    try:
        graph = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AdoptionRefused(RULE_R2_FORMAT, "not UTF-8 JSON") from None
    if not isinstance(graph, dict) or not isinstance(graph.get("things"), list) \
            or not isinstance(graph.get("associations"), list):
        raise AdoptionRefused(RULE_R2_FORMAT, "not the f2w.json2kg.things_associations.v1 family "
                              f"(top-level keys: {sorted(graph) if isinstance(graph, dict) else type(graph).__name__})")
    if json.dumps(graph, indent=2, ensure_ascii=False).encode("utf-8") != blob:
        raise AdoptionRefused(RULE_R2_FORMAT, "bytes differ from json.dumps(indent=2, ensure_ascii=False) "
                              "without trailing newline, which the manifest's serialization field asserts")
    if not graph["things"] and not graph["associations"]:
        raise AdoptionRefused(RULE_PLACEHOLDER, "empty placeholder graph (0 nodes, 0 edges)")
    return graph


def _stats(graph: Mapping[str, Any]) -> dict[str, int]:
    nodes = graph.get("things") or []
    return {"nodes": len(nodes), "edges": len(graph.get("associations") or []),
            "snippets": sum(1 for n in nodes if isinstance(n, dict) and str(n.get("type", "")).endswith("CodeSnippet"))}


def adopt_legacy_flat_file(
    flat_path: Path,
    *,
    storage_kg: Path | None = None,
    code_commit: str | None = None,
    now: datetime | None = None,
    registry: SchemaRegistry | None = None,
    disclosure_allowlist: frozenset[str] = frozenset(),
    allow_pending_review_schema: bool = False,
    run_id: str | None = None,
    profile: str | None = "local",
    dry_run: bool = False,
) -> AdoptionOutcome:
    """Adopt one flat legacy KG file as ``storage/kg/<corpus>/v<N>/``.

    Args:
        flat_path: ``storage/kg/matkg_*.json``. Read only; never modified, moved or replaced.
        storage_kg: The ``storage/kg`` directory to write into (default: the flat file's directory).
        code_commit: Full Git object id of the adopting revision (default: read from ``.git``, no process).
        now: ``created.at`` (default: UTC now). Pass it for reproducible manifests.
        registry: Schema registry (default: discovered).
        disclosure_allowlist: Owner-reviewed R3/R4 exceptions (never created by this module).
        allow_pending_review_schema: Permit a schema that is still pending owner review.
        run_id: Recorded in the manifest.
        profile: Recorded in the manifest.
        dry_run: Run every check but write nothing.

    Raises:
        AdoptionRefused: a check failed (``rule`` says which); nothing was written.
        SnapshotExistsError: the target directory exists (frozen directories are never overwritten).
        SnapshotError: other invalid requests or I/O failures.
    """
    flat = Path(flat_path)
    kg_dir = (Path(storage_kg) if storage_kg is not None else flat.parent).resolve()
    blob = _check_flat_source(flat)
    graph = _parse_legacy_graph(blob)
    corpus, snapshot_id = derive_identity(flat.name)
    if not SNAPSHOT_ID_RE.match(snapshot_id):
        raise SnapshotError("snapshot_id must be v<N>")
    root = resource_root()
    code_commit = code_commit or (_git_head(root) if root else None)
    if not code_commit or not re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", code_commit):
        raise SnapshotError("code_commit could not be determined; pass the adopting commit's full id")
    registry = registry or SchemaRegistry.discover()
    entry, selection_source = _choose_schema(registry, flat.name, allow_pending_review_schema)

    corpus_dir = kg_dir / corpus
    final = corpus_dir / snapshot_id
    if corpus_dir.is_symlink() or os.path.lexists(final):
        raise SnapshotExistsError(f"{corpus}/{snapshot_id} already exists; frozen directories are never rewritten")

    rel_base = f"storage/kg/{corpus}/{snapshot_id}"
    nested_paths = [f"{rel_base}/{name}" for name in SNAPSHOT_FILES]
    disclosure = check_disclosure(graph, paths=[*nested_paths, f"storage/kg/{flat.name}"],
                                  allowlist=disclosure_allowlist)
    if not disclosure.commit_ready:
        counts = Counter(f.rule for f in disclosure.findings)
        raise AdoptionRefused(
            ",".join(sorted(counts)),
            "commit-ready disclosure check failed: "
            + ", ".join(f"{rule} x{n}" for rule, n in sorted(counts.items())), counts)

    fingerprint = closure_fingerprint(entry)
    report = validate_graph(graph, entry, registry=registry, selection_source=selection_source,
                            helper=_helper_for(entry, fingerprint), fingerprint=fingerprint)
    if report.status != "pass":
        raise AdoptionRefused(RULE_R2_VALIDATION, f"schema validation against {entry.schema_id} is "
                              f"{report.status!r}; T26B refuses a candidate whose validation is not a pass")
    graph_sha = sha256_bytes(blob)
    outcome = AdoptionOutcome(flat.name, "would_adopt" if dry_run else "adopted", corpus, snapshot_id, graph_sha,
                              detail=f"schema {entry.schema_id}", directory=final)
    if dry_run:
        return outcome

    checksum = sources_checksum([])
    stats = _stats(graph)
    manifest = KGManifest(
        corpus=corpus, snapshot_id=snapshot_id, graph_id=graph_id_for_path(flat.name),
        graph=GraphBlock(sha256=graph_sha, size_bytes=len(blob), node_count=stats["nodes"],
                         edge_count=stats["edges"], codesnippet_count=stats["snippets"]),
        compat_paths=[CompatPath(path=f"storage/kg/{flat.name}", sha256=graph_sha)],
        parent=Parent(snapshot_id=flat.name[:-len(".json")], graph_sha256=graph_sha, relation="legacy_adoption"),
        created=Created(at=utc_iso(now or datetime.now(UTC)), tool=ADOPTION_TOOL, code_commit=code_commit,
                        json2kg_sha256=sha256_bytes(Path(json2kg.__file__).read_bytes()), run_id=run_id,
                        profile=cast(Literal["local", "als-dev", "als-prod", "ci"] | None, profile)),  # checked by the model
        schema=SchemaBlock(name=entry.schema_id, path=entry.path, sha256=entry.sha256, version=entry.version,
                           import_fingerprint=fingerprint),
        extraction=ExtractionBlock(revisions=[ExtractionRevision(extractor="unknown", version="unknown",
                                                                 unknown=True)]),
        corpus_revision=CorpusRevision(id=LEGACY_CORPUS_REVISION_ID, source_count=0, sources_checksum=checksum),
        inputs=[],
        validation=ValidationBlock(status=cast(Literal["pass", "fail"], report.status), report_sha256=report.sha256,
                                   report_ref=f"{rel_base}/validation.json", suite=VALIDATION_SUITE),
        disclosure=DisclosureBlock(check_version=disclosure.check_version, status=cast(Literal["pass", "fail", "owner_waiver"], disclosure.status),
                                   report_sha256=disclosure.sha256),
    )
    corpus_dir.mkdir(parents=True, exist_ok=True)
    staging = corpus_dir / f".staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        _write_synced(staging / "graph.json", blob)  # byte copy of the flat file, never re-serialized
        _write_synced(staging / "validation.json", report.canonical_bytes())
        _write_synced(staging / "manifest.json", manifest.to_bytes())
        if (staging / "graph.json").read_bytes() != blob:
            raise SnapshotError("graph.json does not read back identically to the flat file")
        try:
            staging.rename(final)
        except OSError:
            if os.path.lexists(final):
                raise SnapshotExistsError(f"{corpus}/{snapshot_id} appeared during adoption") from None
            raise
        verify_snapshot_dir(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return outcome


def adopt_legacy_directory(storage_kg: Path, *, only: Sequence[str] | None = None, **kwargs: Any) -> list[AdoptionOutcome]:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
    """Adopt every top-level entry of ``storage_kg`` that passes; list the rest with the exact rule.

    Directories (already nested snapshots) are not entries. Order is by file name, so a run is deterministic.
    ``kwargs`` go to :func:`adopt_legacy_flat_file`.
    """
    kg_dir = Path(storage_kg)
    outcomes: list[AdoptionOutcome] = []
    for entry in sorted(kg_dir.iterdir(), key=lambda p: p.name):
        if entry.is_dir() and not entry.is_symlink():
            continue
        if entry.name.startswith(".") or (only is not None and entry.name not in only):
            continue
        try:
            outcomes.append(adopt_legacy_flat_file(entry, storage_kg=kg_dir, **kwargs))
        except AdoptionRefused as exc:
            outcomes.append(AdoptionOutcome(entry.name, "skipped", rule=exc.rule, detail=str(exc).split(": ", 1)[1],
                                            findings=exc.findings))
        except SnapshotExistsError as exc:
            outcomes.append(AdoptionOutcome(entry.name, "skipped", rule=RULE_TARGET_EXISTS, detail=str(exc)))
    return outcomes


# --------------------------------------------------------------------------- descriptors

_SCHEMA_FILE = "docs/migration/data_releases/schemas/data_release_descriptor.schema.json"


def descriptor_schema(repo: Path | None = None) -> dict[str, Any]:
    """The normative descriptor JSON Schema of ``repo`` (default: the resource root)."""
    base = Path(repo) if repo is not None else resource_root()
    if base is None:
        raise SnapshotError("no repository root: pass repo")
    return cast(dict[str, Any], json.loads((base / _SCHEMA_FILE).read_text(encoding="utf-8")))  # Any: JSON schema


def validate_descriptor(descriptor: Mapping[str, Any], repo: Path | None = None) -> list[str]:
    """Schema errors of ``descriptor`` (empty list when valid), against ``data_release_descriptor.schema.json``."""
    import jsonschema  # lazy: only descriptor tooling needs it

    schema = descriptor_schema(repo)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                  for e in validator.iter_errors(descriptor))


def build_adoption_descriptor(repo: Path, data_commit: str, corpus: str, snapshot_id: str, *, repository: str,
                              importer_name: str, importer_version: str,
                              legacy_flat_name: str | None = None) -> dict[str, Any]:
    """State-``candidate`` descriptor for an adopted snapshot, from the objects at ``data_commit``.

    Created only after the commit exists (release_contract.md section 3): the manifest, graph and validation
    report are read from Git and hash-checked (:func:`lookup_snapshot`), the flat legacy file is checked to be the
    same bytes. No binary report is bound, so there is no ``staging`` block: an adopted release is not
    staging-validated until the staging import records one. Nothing is written; the caller decides where the
    output goes (the owner's Q6 answer).

    Raises:
        ReleaseVerificationError: any hash, blob, manifest or layout mismatch at the commit.
        ValueError: the repository identity is malformed, or the result does not validate against the schema.
    """
    if not _REPOSITORY.match(repository or ""):
        raise ValueError("repository must be a canonical identity such as github.com/org/repo")
    repo = Path(repo)
    release = lookup_snapshot(repo, data_commit, corpus, snapshot_id)
    manifest = release.manifest
    base = f"storage/kg/{corpus}/{snapshot_id}"

    def entry(role: str, path: str, art: Any) -> dict[str, Any]:
        return {"role": role, "path": path, "sha256": art.sha256, "git_blob": art.git_blob,
                "size_bytes": len(art.data)}

    artifacts = [entry("graph", f"{base}/graph.json", release.graph),
                 entry("manifest", f"{base}/manifest.json", release.manifest_artifact)]
    if release.validation is not None:
        artifacts.append(entry("validation", f"{base}/validation.json", release.validation))
    listed = set()
    for art in release.compat:
        artifacts.append(entry("compat_copy", art.path, art))
        listed.add(art.path)
    flat_name = legacy_flat_name
    if flat_name is None and manifest.parent is not None and manifest.parent.relation == "legacy_adoption":
        flat_name = manifest.parent.snapshot_id + ".json"  # the adopted flat file (parent.snapshot_id is its stem)
    flat_path = f"storage/kg/{flat_name}" if flat_name else None
    if flat_path and flat_path not in listed:
        flat = verify_git_artifact(repo, data_commit, flat_path, manifest.graph.sha256)
        if flat.data != release.graph.data:
            raise ReleaseVerificationError("manifest_mismatch", f"{flat_path} is not the graph bytes")
        artifacts.append(entry("legacy_flat_graph", flat_path, flat))
    descriptor: dict[str, Any] = {
        "descriptor_version": "1",
        "release_id": f"{corpus}-{snapshot_id}-{manifest.graph.sha256[:12]}",
        "repository": repository, "data_commit": data_commit, "corpus": corpus, "snapshot_id": snapshot_id,
        "graph_id": manifest.graph_id, "artifacts": artifacts,
        "compatibility": {
            "schema_sha256": manifest.schema_block.sha256,
            "import_fingerprint": manifest.schema_block.import_fingerprint,
            "importer": {"name": importer_name, "version": importer_version},
            "code_commits": [manifest.created.code_commit], "image_digests": []},
        "disclosure": {"status": manifest.disclosure.status, "report_sha256": manifest.disclosure.report_sha256,
                       "check_version": manifest.disclosure.check_version},
        "state": "candidate", "supersedes": None, "rollback_to": None,
    }
    errors = validate_descriptor(descriptor, repo if (repo / _SCHEMA_FILE).is_file() else None)
    if errors:
        raise ValueError("descriptor does not validate: " + "; ".join(errors))
    return descriptor


def descriptor_bytes(descriptor: Mapping[str, Any]) -> bytes:
    """Stable bytes: sorted keys, two-space indent, trailing newline."""
    return (json.dumps(descriptor, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
