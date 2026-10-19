"""Registry of the existing schema assets (T22B).

Assets are discovered where they are (``storage/schema``); none is moved, copied or edited. The registry key
is ``(schema_id, version, content_fingerprint)``, never a bare class name or the LinkML ``id`` alone:
``rsoxs_schema.yaml`` and ``rsoxs_schema_extract.yaml`` both declare ``name: rsoxs`` and ``version:
rsoxs_v1`` and are told apart only by their bytes (T22A F1).

Fingerprint definition (:func:`closure_fingerprint` in :mod:`.loading`): SHA-256 over the canonical JSON of
``{registry_version, linkml_runtime, files: [[filename, sha256(bytes)], ...sorted]}`` for the resolved
import closure of the asset. Paths, the working directory and timestamps are not part of it.

Schema sets (T22C). The files directly in ``storage/schema`` are the base set: the version every default
build, extraction and publish path uses. A later version of some of those files lives in its own
subdirectory (``storage/schema/v0.3.0/``) with the same file names, so ``imports: [matkg_schema]`` resolves
to the sibling of the same version. Its entries are registered as ``<stem>@<set version>`` and carry the
set's review status; a set pending owner review is never chosen by default and is selectable only on
explicit request (:meth:`SchemaRegistry.select` with ``allow_pending_review=True``).

Licence line (H3 S4). Every base LinkML asset declared ``license: MIT`` until T22C replaced that one line
with :data:`LICENCE_BLOCK`. :func:`pre_s4_bytes` reconstructs the earlier bytes exactly, so an artifact
bound to a pre-S4 fingerprint is still recognised (``closure_fingerprint(entry, pre_s4=True)``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import yaml

from ..resources import resolve_resource

REGISTRY_VERSION = "1"
SCHEMA_REL_DIR = "storage/schema"

CLASS_BASE = "linkml_base"
CLASS_OVERLAY = "linkml_overlay"
CLASS_OVERLAY_EXTRACT = "linkml_overlay_extract"
CLASS_VOCAB = "reference_vocabulary"
CLASS_MAPPING = "ingest_mapping"
CLASS_SEED = "seed"
CLASS_EXAMPLE = "example"
CLASS_UNCLASSIFIED = "unclassified"

LINKML_CLASSES = frozenset({CLASS_BASE, CLASS_OVERLAY, CLASS_OVERLAY_EXTRACT})

# Non-LinkML YAML next to the schemas (T22A inventory). Never loaded by the LinkML loader.
_NON_LINKML = {
    "prov_vocabulary": CLASS_VOCAB,
    "bl1101_motor_stage_map": CLASS_MAPPING,
    "tiled_sim_seed": CLASS_SEED,
    "xray_seed_instances": CLASS_EXAMPLE,
}

# selectable_for values: "extraction", "graph-view" (see ADR 2.2). x-ray graphs bind xray_schema by file name
# (owner decision H3 S5, ``multi_kg.schema_path_for_graph``).
_SELECTABLE = {
    "matkg_schema": ("extraction",),
    "rsoxs_schema": ("extraction", "graph-view"),
    "bl1101_schema": ("graph-view",),
    "xray_schema": ("extraction", "graph-view"),
    "xray_schema_extract": ("extraction",),
}

KNOWN_ISSUES: Dict[str, Tuple[str, ...]] = {
    "rsoxs_schema_extract": (
        "F1: redeclares RSoXSMeasurement with slots photon_energy_eV/absorption_edge/polarization that are "
        "undefined in its closure; SchemaView.class_induced_slots('RSoXSMeasurement') raises ValueError "
        "(owner question Q1; kept byte-identical)",
        "F1: shares name/id 'rsoxs' and version 'rsoxs_v1' with rsoxs_schema.yaml",
    ),
}
# Registered but never selectable: selecting it errors clearly instead of failing deep in SchemaView.
KNOWN_BROKEN = frozenset({"rsoxs_schema_extract"})

# Default schema when nothing selects one: matkg_schema.yaml is the extraction default, not an
# interpretation fallback for artifacts that carry a fingerprint (ADR 2.5.3).
DEFAULT_SCHEMA_ID = "matkg_schema"

NORMALIZER_TERM_EXTRACTOR = "term_extractor.schema.SchemaHelper"
NORMALIZER_LEGACY = "extract_terms.SchemaHelper"

# Review status of a schema set. The base set is what every default path uses today.
REVIEW_IN_USE = "in_use"
REVIEW_PENDING = "pending_owner_review"

#: The one line every base LinkML asset carried before H3 S4 (T22C).
PRE_S4_LICENCE_LINE = b"license: MIT\n"
#: What replaced it (LICENSE.txt: LBNL BSD-style, SPDX ``BSD-3-Clause-LBNL``).
LICENCE_ID = "BSD-3-Clause-LBNL"
LICENCE_BLOCK = (
    b"# LBNL BSD-style licence: FAIR to WISE (F2W) Copyright (c) 2025, The Regents of the\n"
    b"# University of California, through Lawrence Berkeley National Laboratory. See LICENSE.txt.\n"
    b"license: BSD-3-Clause-LBNL\n"
)


def pre_s4_bytes(data: bytes) -> Optional[bytes]:
    """The bytes ``data`` had before the S4 licence change, or ``None`` if it never had that line.

    Exactly one :data:`LICENCE_BLOCK` must be present; it is replaced by :data:`PRE_S4_LICENCE_LINE`.
    """
    if data.count(LICENCE_BLOCK) != 1 or PRE_S4_LICENCE_LINE in data:
        return None
    return data.replace(LICENCE_BLOCK, PRE_S4_LICENCE_LINE)


@dataclass(frozen=True)
class SchemaSet:
    """A later version of some base assets, kept in its own subdirectory (T22C).

    Attributes:
        version: Set version label (``0.3.0``); entry ids are ``<stem>@<version>``.
        directory: Subdirectory of the schema directory holding the set's files.
        members: File stems in the set; each imports its siblings by the base name.
        review_status: :data:`REVIEW_PENDING` until the owner approves the diff in ``review_doc``.
        review_doc: Plain-language diff the owner reviews.
    """

    version: str
    directory: str
    members: Tuple[str, ...]
    review_status: str
    review_doc: str

    def entry_id(self, stem: str) -> str:
        """Registry id of ``stem`` in this set."""
        return f"{stem}@{self.version}"


#: T22C (H3 F2/F3/F5/F6/F7): Sample, MeasurementRun, Instrument and the evidence slots. Additive; pending the
#: owner's review of the schema diff. bl1101 and xray stay on the base set (see the review doc).
SCHEMA_SETS: Tuple[SchemaSet, ...] = (
    SchemaSet(version="0.3.0", directory="v0.3.0", members=("matkg_schema", "rsoxs_schema"),
              review_status=REVIEW_PENDING, review_doc="docs/migration/reviews/H3/schema_diff_T22C.md"),
)


@dataclass(frozen=True)
class EntrypointDefault:
    """Which schema and normalizer one entrypoint uses when nothing else is configured (T22A F3/F4).

    Attributes:
        name: Entrypoint label.
        source: Repository-relative file that holds the default.
        needle: Text that must appear in ``source`` (a test keeps this table honest).
        schema_id: Registered schema the default resolves to.
        selection_source: ``default``, ``env`` or ``hard_coded``.
        normalizer: Dotted name of the SchemaHelper implementation that path uses.
    """

    name: str
    source: str
    needle: str
    schema_id: str
    selection_source: str
    normalizer: str


ENTRYPOINT_DEFAULTS: Tuple[EntrypointDefault, ...] = (
    EntrypointDefault("compose-stack", "compose.yaml", "F2W_SCHEMA: storage/schema/rsoxs_schema.yaml",
                      "rsoxs_schema", "env", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("rsoxs-stack-script", "scripts/start_rsoxs_stack.sh",
                      'F2W_SCHEMA="${F2W_SCHEMA:-storage/schema/rsoxs_schema.yaml}"',
                      "rsoxs_schema", "env", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("agent-backend-script", "scripts/start_agent_backend.sh",
                      'SCHEMA="${F2W_SCHEMA:-storage/schema/matkg_schema.yaml}"',
                      "matkg_schema", "env", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("scripts-run", "scripts/run.py", '"--schema", default="storage/schema/matkg_schema.yaml"',
                      "matkg_schema", "default", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("f2w-agent-cli", "app/modules/f2w_agent/cli.py",
                      'p.add_argument("--schema", default="storage/schema/matkg_schema.yaml")',
                      "matkg_schema", "default", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("coordinator-config", "app/modules/f2w_agent/coordinator.py",
                      'schema_path: str = "storage/schema/matkg_schema.yaml"',
                      "matkg_schema", "default", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("orchestrator", "app/modules/term_extractor/orchestrator.py",
                      'schema_path: str = "storage/schema/matkg_schema.yaml"',
                      "matkg_schema", "default", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("academy-extractor", "app/modules/launchers/academy_extractor.py",
                      'default="storage/schema/matkg_schema.yaml"',
                      "matkg_schema", "default", NORMALIZER_TERM_EXTRACTOR),
    EntrypointDefault("extract-terms-cli", "app/modules/extract_terms.py",
                      'default=Path("storage/schema/matkg_schema.yaml")',
                      "matkg_schema", "default", NORMALIZER_LEGACY),
    EntrypointDefault("run-pipeline-cborg", "app/run_pipeline_cborg.py",
                      'schema_path="storage/schema/matkg_schema.yaml"',
                      "matkg_schema", "hard_coded", NORMALIZER_LEGACY),
)


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RegistryKey:
    """Identity of one registered asset version."""

    schema_id: str
    version: Optional[str]
    content_fingerprint: str


@dataclass(frozen=True)
class SchemaEntry:
    """One discovered asset.

    Attributes:
        schema_id: File stem (unique per directory; not the LinkML ``id``).
        file: File name inside the schema directory.
        path: Repository-relative POSIX path (``storage/schema/<file>``), the form recorded in manifests.
        abs_path: Where the bytes were read from.
        classification: One of the ``CLASS_*`` constants.
        version: Free-form ``version`` label from the file (not a content identity).
        linkml_name / linkml_id: Declared LinkML ``name``/``id`` (collide across assets, see F1).
        declared_imports: ``imports`` as written.
        sha256: Hash of the file bytes (the ``content_fingerprint``).
        size_bytes: File size.
        selectable_for: ``extraction`` / ``graph-view``; empty means not selectable.
        known_issues: Recorded defects, never repaired here.
        schema_set: ``None`` for the base set, else the :class:`SchemaSet` version.
        review_status: :data:`REVIEW_IN_USE` or :data:`REVIEW_PENDING`.
        base_id: Base entry this one is a later version of (``None`` for base entries).
        pre_s4_sha256: Hash of the bytes before the S4 licence change; ``None`` if the file never had it.
    """

    schema_id: str
    file: str
    path: str
    abs_path: Path
    classification: str
    version: Optional[str]
    linkml_name: Optional[str]
    linkml_id: Optional[str]
    declared_imports: Tuple[str, ...]
    sha256: str
    size_bytes: int
    selectable_for: Tuple[str, ...] = ()
    known_issues: Tuple[str, ...] = ()
    schema_set: Optional[str] = None
    review_status: str = REVIEW_IN_USE
    base_id: Optional[str] = None
    pre_s4_sha256: Optional[str] = None

    @property
    def pending_review(self) -> bool:
        """True while the owner has not approved this entry's schema set."""
        return self.review_status == REVIEW_PENDING

    @property
    def is_linkml(self) -> bool:
        """True for scientific LinkML assets (base, overlay, extract overlay)."""
        return self.classification in LINKML_CLASSES

    @property
    def known_broken(self) -> bool:
        """True when selecting the asset must fail clearly (F1)."""
        return self.schema_id in KNOWN_BROKEN

    @property
    def key(self) -> RegistryKey:
        """Registry key (schema id, version label, content fingerprint)."""
        return RegistryKey(self.schema_id, self.version, self.sha256)


class RegistryError(LookupError):
    """An asset is unknown, unregistered or not selectable."""


def _classify(stem: str, doc: Mapping) -> str:
    if stem in _NON_LINKML:
        return _NON_LINKML[stem]
    if "id" in doc and "name" in doc and ("classes" in doc or "imports" in doc or "slots" in doc):
        imports = [str(i) for i in (doc.get("imports") or [])]
        if not any(not i.startswith("linkml:") for i in imports):
            return CLASS_BASE
        return CLASS_OVERLAY_EXTRACT if stem.endswith("_extract") else CLASS_OVERLAY
    return CLASS_UNCLASSIFIED


def _entry_from_file(path: Path, schema_set: Optional[SchemaSet] = None) -> SchemaEntry:
    data = path.read_bytes()
    doc = yaml.safe_load(data.decode("utf-8"))
    doc = doc if isinstance(doc, dict) else {}
    stem = path.stem
    classification = _classify(stem, doc)
    linkml = classification in LINKML_CLASSES
    selectable: Tuple[str, ...] = () if stem in KNOWN_BROKEN else _SELECTABLE.get(stem, ())
    version = doc.get("version") if linkml else None
    before = pre_s4_bytes(data)
    rel = f"{SCHEMA_REL_DIR}/{schema_set.directory}/{path.name}" if schema_set else f"{SCHEMA_REL_DIR}/{path.name}"
    return SchemaEntry(
        schema_id=schema_set.entry_id(stem) if schema_set else stem, file=path.name, path=rel, abs_path=path,
        classification=classification,
        version=str(version) if version is not None else None,
        linkml_name=str(doc["name"]) if linkml and "name" in doc else None,
        linkml_id=str(doc["id"]) if linkml and "id" in doc else None,
        declared_imports=tuple(str(i) for i in (doc.get("imports") or [])) if linkml else (),
        sha256=sha256_bytes(data), size_bytes=len(data), selectable_for=selectable,
        known_issues=KNOWN_ISSUES.get(stem, ()),
        schema_set=schema_set.version if schema_set else None,
        review_status=schema_set.review_status if schema_set else REVIEW_IN_USE,
        base_id=stem if schema_set else None,
        pre_s4_sha256=sha256_bytes(before) if before is not None and schema_set is None else None,
    )


def _set_entries(base: Path, sets: Sequence[SchemaSet]) -> Dict[str, SchemaEntry]:
    out: Dict[str, SchemaEntry] = {}
    for schema_set in sets:
        directory = base / schema_set.directory
        if not directory.is_dir():
            continue
        for stem in schema_set.members:
            path = directory / f"{stem}.yaml"
            if not path.is_file():
                raise RegistryError(f"schema set {schema_set.version} lacks {schema_set.directory}/{path.name}")
            entry = _entry_from_file(path, schema_set)
            out[entry.schema_id] = entry
    return out


@dataclass
class SchemaRegistry:
    """The assets of one schema directory, read once.

    Entries are a snapshot of the bytes at discovery time; build a new registry to see later changes.
    """

    schema_dir: Path
    entries: Dict[str, SchemaEntry] = field(default_factory=dict)
    version: str = REGISTRY_VERSION

    @classmethod
    def discover(cls, schema_dir: Optional[Path] = None) -> "SchemaRegistry":
        """Register every ``*.yaml`` in ``schema_dir`` (default: the packaged ``storage/schema``).

        The directory is resolved like every other schema path (CWD-independent, ``F2W_RESOURCE_ROOT``).
        """
        base = Path(schema_dir) if schema_dir is not None else resolve_resource(SCHEMA_REL_DIR)
        base = base.resolve()
        entries = {p.stem: _entry_from_file(p) for p in sorted(base.glob("*.yaml"))}
        entries.update(_set_entries(base, SCHEMA_SETS))
        return cls(schema_dir=base, entries=entries)

    def base_entries(self) -> List[SchemaEntry]:
        """Entries of the base set (what every default path uses)."""
        return [e for e in self if e.schema_set is None]

    def set_entries(self, version: str) -> List[SchemaEntry]:
        """Entries of one schema set, in member order."""
        return [e for e in self if e.schema_set == version]

    def __iter__(self) -> Iterator[SchemaEntry]:
        return iter(self.entries.values())

    def get(self, schema_id: str) -> SchemaEntry:
        """Entry by id or by file name; raises :class:`RegistryError` when unregistered."""
        key = schema_id[:-5] if schema_id.endswith(".yaml") else schema_id
        try:
            return self.entries[key]
        except KeyError:
            raise RegistryError(f"schema {schema_id!r} is not registered") from None

    def resolve(self, path_or_id: object) -> Optional[SchemaEntry]:
        """Entry for an id, file name or a ``.../storage/schema/<file>`` path; ``None`` if unregistered.

        Unknown paths keep working for callers as "unregistered" (ADR 2.2); the registry never guesses.
        """
        text = str(path_or_id or "").replace("\\", "/").strip()
        if not text:
            return None
        if "/" in text:
            for versioned in (e for e in self if e.schema_set is not None):
                candidate = Path(text)
                if text.endswith(versioned.path) or candidate.resolve() == versioned.abs_path.resolve():
                    if candidate.is_file() and sha256_bytes(candidate.read_bytes()) != versioned.sha256:
                        return None
                    return versioned
        elif "@" in text:
            return self.entries.get(text[:-5] if text.endswith(".yaml") else text)
        name = text.rsplit("/", 1)[-1]
        entry = self.entries.get(name[:-5] if name.endswith(".yaml") else name)
        if entry is None:
            return None
        if "/" in text:
            candidate = Path(text)
            same_place = text.endswith(entry.path) or candidate.resolve() == entry.abs_path.resolve()
            if not same_place:
                return None
            if candidate.is_file() and sha256_bytes(candidate.read_bytes()) != entry.sha256:
                return None  # same name, different bytes: a different, unregistered asset
        return entry

    def for_graph(self, graph_path: str, schema_set: Optional[str] = None) -> Optional[SchemaEntry]:
        """Overlay for a KG file name, reusing ``multi_kg.schema_path_for_graph`` (filename patterns).

        With ``schema_set`` (an explicit selection, never a default), the set's version of that overlay is
        returned when the set has one; an overlay the set does not contain stays on the base entry.
        """
        from ..f2w_agent.multi_kg import schema_path_for_graph

        path = schema_path_for_graph(graph_path)
        entry = self.resolve(path) if path else None
        if entry is None or schema_set is None:
            return entry
        if not any(s.version == schema_set for s in SCHEMA_SETS):
            raise RegistryError(f"schema set {schema_set!r} is not registered")
        return self.entries.get(f"{entry.schema_id}@{schema_set}", entry)

    def select(self, schema_id: str, purpose: str = "extraction", *, allow_pending_review: bool = False) -> SchemaEntry:
        """Entry that may be used for ``purpose``; known-broken or unsuitable assets raise a clear error.

        An entry whose schema set is pending owner review (T22C) is refused unless the caller passes
        ``allow_pending_review=True``: no default path selects it.
        """
        entry = self.get(schema_id)
        if entry.pending_review and not allow_pending_review:
            raise RegistryError(
                f"{entry.path} belongs to schema set {entry.schema_set}, which is pending owner review "
                f"({SCHEMA_SET_DOCS.get(entry.schema_set, 'review doc')}); select it explicitly with "
                "allow_pending_review=True"
            )
        if entry.known_broken:
            raise RegistryError(
                f"{entry.path} is registered as known-broken and not selectable: {entry.known_issues[0]}"
            )
        if not entry.is_linkml:
            raise RegistryError(f"{entry.path} is {entry.classification}, not a LinkML schema")
        if purpose not in entry.selectable_for:
            raise RegistryError(f"{entry.path} is not selectable for {purpose!r}")
        return entry

    def by_fingerprint(self, content_fingerprint: str) -> List[SchemaEntry]:
        """Entries whose file bytes hash to ``content_fingerprint``."""
        return [e for e in self if e.sha256 == content_fingerprint]

    def id_collisions(self, schema_set: Optional[str] = None) -> Dict[str, List[str]]:
        """LinkML ``name`` values declared by more than one asset of one schema set (F1).

        A later version of a schema in another set shares its name on purpose and is not a collision.
        """
        seen: Dict[str, List[str]] = {}
        for entry in self:
            if entry.linkml_name and entry.schema_set == schema_set:
                seen.setdefault(entry.linkml_name, []).append(entry.schema_id)
        return {k: v for k, v in seen.items() if len(v) > 1}


SCHEMA_SET_DOCS: Dict[str, str] = {s.version: s.review_doc for s in SCHEMA_SETS}
