"""Sidecar manifest of a frozen versioned JSON KG snapshot (T23B).

``KGManifest`` mirrors ``docs/migration/data_releases/schemas/kg_snapshot_manifest.schema.json`` (normative;
equivalence is tested against the JSON Schema with ``jsonschema``). It is what ``manifest.json`` contains and
what T08A's ``load_snapshot`` already reads (``graph.sha256``). The contract model
``app.modules.contracts.changes.SnapshotManifest`` is the compact projection used in plans and receipts:
:func:`to_contract_manifest` builds it from the full manifest (contracts are not changed here).

Rules from json_kg_contract.md section 4.2: the payload stays unchanged; neither file contains its own hash or
the commit that contains it (``data_commit`` is rejected, ``extra="forbid"``); references are repo-relative or
``private:``; hashes are over stored bytes.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.contracts.changes import SnapshotManifest

MANIFEST_VERSION = "1"
ARTIFACT_TYPE = "f2w.kg_snapshot"
GRAPH_FORMAT = "f2w.json2kg.things_associations.v1"
SERIALIZATION = "json.dumps(indent=2, ensure_ascii=False); utf-8; no trailing newline"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_REPO_PATH = re.compile(r"^(?!/)(?!.*\.\.)(?!.*\\)[A-Za-z0-9._/-]+$")
_PRIVATE_REF = re.compile(r"^private:[A-Za-z0-9._:/-]+$")
CORPUS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SNAPSHOT_ID_RE = re.compile(r"^v[1-9][0-9]*$")

PARENT_RELATIONS = ("rebuild", "edit_reconciliation", "repair", "disclosure_split", "legacy_adoption")


def _sha(value: str) -> str:
    if not _SHA256.match(value):
        raise ValueError("must be 64 lowercase hex characters")
    return value


def _repo_path(value: str) -> str:
    if not _REPO_PATH.match(value):
        raise ValueError("must be a repository-relative POSIX path (no absolute path, '..' or backslash)")
    return value


def _ref(value: str) -> str:
    if not (_REPO_PATH.match(value) or _PRIVATE_REF.match(value)):
        raise ValueError("must be a repository-relative path or a private: reference")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GraphBlock(_Strict):
    """The frozen graph bytes: identity, size, format and counts."""

    path: Literal["graph.json"] = "graph.json"
    sha256: str
    size_bytes: int = Field(ge=1)
    format: Literal["f2w.json2kg.things_associations.v1"] = GRAPH_FORMAT
    serialization: Literal[
        "json.dumps(indent=2, ensure_ascii=False); utf-8; no trailing newline"] = SERIALIZATION
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    codesnippet_count: int | None = Field(default=None, ge=0)

    _v = field_validator("sha256")(_sha)


class CompatPath(_Strict):
    """A flat legacy-reader copy that must be byte-identical to ``graph.json``."""

    path: str
    sha256: str

    _p = field_validator("path")(_repo_path)
    _s = field_validator("sha256")(_sha)


class Parent(_Strict):
    """The snapshot this one revises."""

    snapshot_id: str
    graph_sha256: str
    data_commit: str | None = None
    relation: Literal["rebuild", "edit_reconciliation", "repair", "disclosure_split", "legacy_adoption"]

    _s = field_validator("graph_sha256")(_sha)

    @field_validator("data_commit")
    @classmethod
    def _commit(cls, value: str | None) -> str | None:
        if value is not None and not _GIT_SHA.match(value):
            raise ValueError("must be a full Git object id")
        return value


class Created(_Strict):
    """How and when the graph was built."""

    at: str
    tool: str
    code_commit: str
    json2kg_sha256: str
    run_id: str | None = None
    profile: Literal["local", "als-dev", "als-prod", "ci"] | None = None

    _s = field_validator("json2kg_sha256")(_sha)

    @field_validator("code_commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _GIT_SHA.match(value):
            raise ValueError("must be a full Git object id")
        return value

    @field_validator("at")
    @classmethod
    def _at(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class SchemaBlock(_Strict):
    """Registered schema the graph was validated against (T22B)."""

    name: str
    path: str
    sha256: str
    version: str | None = None
    import_fingerprint: str | None = None

    _p = field_validator("path")(_repo_path)
    _s = field_validator("sha256")(_sha)

    @field_validator("import_fingerprint")
    @classmethod
    def _fp(cls, value: str | None) -> str | None:
        return value if value is None else _sha(value)


class ExtractionRevision(_Strict):
    """One contributing extraction run or settings revision."""

    extractor: str
    version: str
    settings_revision: str | None = None
    model: str | None = None
    run_ids: list[str] | None = None
    unknown: bool | None = None


class ExtractionBlock(_Strict):
    """Every contributing extraction revision (never one invented model)."""

    revisions: list[ExtractionRevision] = Field(min_length=1)


class CorpusRevision(_Strict):
    """The source set."""

    id: str
    source_count: int = Field(ge=0)
    sources_checksum: str
    source_list_ref: str | None = None

    _s = field_validator("sources_checksum")(_sha)

    @field_validator("source_list_ref")
    @classmethod
    def _r(cls, value: str | None) -> str | None:
        return value if value is None else _ref(value)


class InputRef(_Strict):
    """An input by role, hash and reference."""

    role: Literal["terms_snapshot", "code_snippets", "edit_journal", "seed_graph", "mapping_file"]
    sha256: str
    ref: str

    _s = field_validator("sha256")(_sha)
    _r = field_validator("ref")(_ref)


class ValidationBlock(_Strict):
    """Result and hash of the schema validation report."""

    status: Literal["pass", "fail"]
    report_sha256: str
    report_ref: str | None = None
    suite: str | None = None

    _s = field_validator("report_sha256")(_sha)

    @field_validator("report_ref")
    @classmethod
    def _r(cls, value: str | None) -> str | None:
        return value if value is None else _ref(value)


class DisclosureBlock(_Strict):
    """Result and hash of the commit-ready disclosure check."""

    check_version: str
    status: Literal["pass", "fail", "owner_waiver"]
    report_sha256: str
    waiver_ref: str | None = None

    _s = field_validator("report_sha256")(_sha)


class KGManifest(_Strict):
    """``manifest.json`` of one frozen snapshot."""

    manifest_version: Literal["1"] = MANIFEST_VERSION
    artifact_type: Literal["f2w.kg_snapshot"] = ARTIFACT_TYPE
    corpus: str
    snapshot_id: str
    graph_id: str = Field(min_length=1)
    graph: GraphBlock
    compat_paths: list[CompatPath] | None = None
    parent: Parent | None = None
    created: Created
    schema_block: SchemaBlock = Field(alias="schema")
    extraction: ExtractionBlock
    corpus_revision: CorpusRevision
    inputs: list[InputRef]
    validation: ValidationBlock
    disclosure: DisclosureBlock

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @field_validator("corpus")
    @classmethod
    def _corpus(cls, value: str) -> str:
        if not CORPUS_RE.match(value):
            raise ValueError("corpus must match ^[a-z0-9][a-z0-9_-]*$")
        return value

    @field_validator("snapshot_id")
    @classmethod
    def _snapshot(cls, value: str) -> str:
        if not SNAPSHOT_ID_RE.match(value):
            raise ValueError("snapshot_id must be v<N>")
        return value

    def to_json_dict(self) -> dict:
        """Schema-shaped dict (``schema`` key, absent optionals dropped)."""
        body = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        body.setdefault("parent", None)  # required by the schema; null for a first snapshot
        return body

    def to_bytes(self) -> bytes:
        """Stored bytes: sorted keys, two-space indent, UTF-8, trailing newline."""
        text = json.dumps(self.to_json_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        return (text + "\n").encode("utf-8")


def parse_manifest(data: bytes) -> KGManifest:
    """Parse and validate ``manifest.json`` bytes (raises ``ValueError``/``ValidationError``)."""
    return KGManifest.model_validate(json.loads(data.decode("utf-8")))


def utc_iso(moment: datetime) -> str:
    """UTC ISO-8601 with a ``Z`` suffix, whole seconds."""
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def extraction_fingerprint(revisions: Sequence[ExtractionRevision]) -> str:
    """SHA-256 over the canonical JSON of the revisions, sorted, so order does not matter."""
    items = sorted(json.dumps(r.model_dump(mode="json", exclude_none=True), sort_keys=True) for r in revisions)
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()


def sources_checksum(source_sha256s: Sequence[str]) -> str:
    """SHA-256 over the sorted newline-joined per-source hashes (schema: ``sources_checksum``)."""
    for value in source_sha256s:
        _sha(value)
    return hashlib.sha256("\n".join(sorted(source_sha256s)).encode("utf-8")).hexdigest()


def to_contract_manifest(manifest: KGManifest) -> SnapshotManifest:
    """Project the full manifest onto the shared contract model (plans and receipts use this form)."""
    base = f"storage/kg/{manifest.corpus}/{manifest.snapshot_id}"
    return SnapshotManifest(
        snapshot_id=manifest.snapshot_id,
        corpus=manifest.corpus,
        graph_path=f"{base}/{manifest.graph.path}",
        graph_sha256=manifest.graph.sha256,
        parent_snapshot_id=manifest.parent.snapshot_id if manifest.parent else None,
        schema_fingerprint=manifest.schema_block.import_fingerprint or manifest.schema_block.sha256,
        extraction_fingerprint=extraction_fingerprint(manifest.extraction.revisions),
        corpus_fingerprint=manifest.corpus_revision.sources_checksum,
        validation_report_sha256=manifest.validation.report_sha256,
        created_at=datetime.fromisoformat(manifest.created.at.replace("Z", "+00:00")),
    )
