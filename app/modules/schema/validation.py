"""Validation adapters over the existing SchemaHelper, and fingerprint binding checks (T22B).

Nothing here re-implements normalization. :func:`normalize_term` and :func:`relation_is_valid` call the
existing ``SchemaHelper.validate_and_fix_term`` / ``check_relation_validity`` unchanged (on a copy, because
the former mutates its argument). :func:`validate_graph` is a report-mode check of a json2kg payload against a
registered schema's classes and slots (taken from the helper's own tables). It never reclassifies, retypes
or edits a graph (ADR 2.5): values outside the closure are ``legacy_warning`` counts, with their unit.

Diagnostic categories are kept separate: ``contract_error``, ``legacy_warning``, ``incomplete_extraction``,
``unknown_source_data``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
import copy
from dataclasses import dataclass, field
import hashlib
import json
from typing import TYPE_CHECKING, Any, cast

from .loading import closure_fingerprint, load_helper, pre_s4_fingerprint
from .registry import (
    NORMALIZER_TERM_EXTRACTOR,
    REGISTRY_VERSION,
    SchemaEntry,
    SchemaRegistry,
)

if TYPE_CHECKING:
    from ..json_types import JsonValue

CONTRACT_ERROR = "contract_error"
LEGACY_WARNING = "legacy_warning"
INCOMPLETE_EXTRACTION = "incomplete_extraction"
UNKNOWN_SOURCE_DATA = "unknown_source_data"
CATEGORIES = (CONTRACT_ERROR, LEGACY_WARNING, INCOMPLETE_EXTRACTION, UNKNOWN_SOURCE_DATA)

REPORT_VERSION = "1"

#: Binding statuses. ``verified_pre_s4_licence``: the artifact carries the fingerprint this schema had before the
#: H3 S4 licence-line change; the bytes differ only in that line, proven by reconstruction (T22C).
BINDING_VERIFIED = "verified"
BINDING_VERIFIED_PRE_S4 = "verified_pre_s4_licence"
BINDING_LEGACY = "legacy_unverified"
_PREDICATE_PREFIX = "rel:"
_TOP_N = 20


class SchemaBindingError(ValueError):
    """An artifact claims a schema fingerprint that is not registered (never reinterpreted, ADR 2.5.3)."""


def registered_fingerprints(registry: SchemaRegistry) -> dict[str, str]:
    """``closure fingerprint -> schema id`` for every selectable LinkML asset (all schema sets)."""
    out: dict[str, str] = {}
    for entry in registry:
        if entry.is_linkml and not entry.known_broken:
            out[closure_fingerprint(entry)] = entry.schema_id
    return out


def pre_s4_fingerprints(registry: SchemaRegistry) -> dict[str, str]:
    """``pre-S4 closure fingerprint -> schema id`` for every selectable base LinkML asset (T22C)."""
    out: dict[str, str] = {}
    for entry in registry:
        if entry.is_linkml and not entry.known_broken:
            fp = pre_s4_fingerprint(entry)
            if fp:
                out[fp] = entry.schema_id
    return out


def check_binding(
    registry: SchemaRegistry | None, entry: SchemaEntry, claimed: str | None, *, actual: str | None = None
) -> str:
    """Compare an artifact's claimed schema fingerprint with ``entry``.

    Returns:
        ``"verified"`` when equal, ``"verified_pre_s4_licence"`` when the claim is ``entry``'s fingerprint
        from before the S4 licence-line change (same classes and slots), ``"legacy_unverified"`` when the
        artifact carries no fingerprint (warning only; the artifact is neither invalid nor reclassified).

    Raises:
        SchemaBindingError: the artifact claims a fingerprint that differs from ``entry`` (stale) or that no
            registered schema has (unknown). The message says "schema <id>@<fp> not registered".
    """
    if not claimed:
        return BINDING_LEGACY
    actual = actual or closure_fingerprint(entry)
    if claimed == actual:
        return BINDING_VERIFIED
    if claimed == pre_s4_fingerprint(entry):
        return BINDING_VERIFIED_PRE_S4
    known = registered_fingerprints(registry) if registry is not None else {}
    if registry is not None:
        known.update({fp: sid for fp, sid in pre_s4_fingerprints(registry).items() if fp not in known})
    if claimed in known:
        raise SchemaBindingError(
            f"artifact is bound to registered schema {known[claimed]!r}, not {entry.schema_id!r} "
            f"(schema {entry.schema_id}@{claimed[:12]} not registered for this use)"
        )
    raise SchemaBindingError(f"schema {entry.schema_id}@{claimed[:12]} not registered (stale or unknown)")


def normalize_term(helper: Any, term: Mapping[str, Any]) -> dict[str, Any]:  # noqa: ANN401  # Any: SchemaHelper (two implementations, duck-typed)
    """Run the existing ``validate_and_fix_term`` on a deep copy of ``term`` and return the result."""
    return cast(dict[str, Any], helper.validate_and_fix_term(copy.deepcopy(dict(term))))  # Any: untyped helper


def relation_is_valid(helper: Any, subject_class: str, predicate: str, object_class: str) -> bool:  # noqa: ANN401  # Any: SchemaHelper (two implementations, duck-typed)
    """The existing domain/range check (``SchemaHelper.check_relation_validity``) unchanged."""
    return bool(helper.check_relation_validity(subject_class, predicate, object_class))


@dataclass
class GraphValidationReport:
    """Deterministic, path-free result of :func:`validate_graph` (no timestamps, so its hash is stable)."""

    schema: dict[str, Any]
    fingerprint_status: str
    normalizer: str
    selection_source: str
    counts: dict[str, int]
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """``fail`` only for contract errors; legacy warnings never fail a graph."""
        return "fail" if self.categories[CONTRACT_ERROR]["count"] else "pass"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready body (the content of ``validation.json``)."""
        return {
            "report_version": REPORT_VERSION,
            "registry_version": REGISTRY_VERSION,
            "status": self.status,
            "schema": self.schema,
            "fingerprint_status": self.fingerprint_status,
            "normalizer": self.normalizer,
            "selection_source": self.selection_source,
            "counts": self.counts,
            "categories": self.categories,
        }

    def canonical_bytes(self) -> bytes:
        """Stored bytes: sorted keys, two-space indent, UTF-8, trailing newline."""
        return (json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    @property
    def sha256(self) -> str:
        """Hash of :meth:`canonical_bytes` (goes into the manifest ``validation.report_sha256``)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _top(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N])


def _lists(payload: Mapping[str, Any]) -> tuple[Any, Any]:  # Any: node/edge lists are unvalidated payload
    nodes = payload.get("things") if "things" in payload else payload.get("nodes")
    edges = payload.get("associations") if "associations" in payload else payload.get("edges")
    return nodes, edges


def validate_graph(
    payload: JsonValue,
    entry: SchemaEntry,
    *,
    registry: SchemaRegistry | None = None,
    claimed_fingerprint: str | None = None,
    selection_source: str = "caller",
    helper: Any = None,  # noqa: ANN401  # Any: SchemaHelper (two implementations, duck-typed)
    implementation: str = NORMALIZER_TERM_EXTRACTOR,
    fingerprint: str | None = None,
) -> GraphValidationReport:
    """Report-mode check of one graph payload against ``entry``. The payload is read, never modified.

    Args:
        payload: Parsed graph JSON (``things``/``associations`` or ``nodes``/``edges``).
        entry: Registered schema to compare with.
        registry: Optional; lets a mismatch name the other registered schema it belongs to.
        claimed_fingerprint: Fingerprint the artifact carries. A mismatch raises
            :class:`SchemaBindingError`; no claim gives ``legacy_unverified`` (a warning).
        selection_source: How ``entry`` was chosen (``caller``, ``default``, ``filename_pattern``...).
        helper: Existing ``SchemaHelper`` to reuse; built from ``entry`` when omitted.
        implementation: Normalizer implementation used to build the helper when ``helper`` is omitted.
        fingerprint: Precomputed ``closure_fingerprint(entry)`` (saves one schema load).
    """
    fingerprint = fingerprint or closure_fingerprint(entry)
    status = check_binding(registry, entry, claimed_fingerprint, actual=fingerprint)
    helper = helper or load_helper(entry, implementation=implementation)
    schema_block = {
        "name": entry.schema_id, "path": entry.path, "sha256": entry.sha256, "version": entry.version,
        "import_fingerprint": fingerprint,
    }
    errors: list[str] = []
    nodes: list[Any] = []
    edges: list[Any] = []
    if not isinstance(payload, Mapping):
        errors.append("payload is not a JSON object")
    else:
        raw_nodes, raw_edges = _lists(payload)
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            errors.append("things/associations (or nodes/edges) must both be lists")
        else:
            nodes, edges = raw_nodes, raw_edges

    ids = set()
    bad_categories: Counter[str] = Counter()
    unknown_nodes = 0
    incomplete = 0
    no_source = 0
    snippets = 0
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping) or not node.get("id"):
            errors.append(f"node #{index} is not an object with an id")
            continue
        ids.add(node["id"])
        category = node.get("category")
        if category == "CodeSnippet" or str(node.get("type", "")).endswith("CodeSnippet"):
            snippets += 1
        elif not node.get("source_papers"):
            no_source += 1
        if category not in helper.classes:
            unknown_nodes += 1
            bad_categories[str(category)] += 1
        if str(node.get("description") or "N/A") == "N/A" and category != "CodeSnippet":
            incomplete += 1

    bad_predicates: Counter[str] = Counter()
    unknown_edges = 0
    domain_range = 0
    dangling = 0
    by_id = {n["id"]: n for n in nodes if isinstance(n, Mapping) and n.get("id")}
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or not (edge.get("subject") and edge.get("predicate")
                                                 and edge.get("object")):
            errors.append(f"edge #{index} lacks subject/predicate/object")
            continue
        predicate = str(edge["predicate"])
        slot = predicate[len(_PREDICATE_PREFIX):] if predicate.startswith(_PREDICATE_PREFIX) else predicate
        subject, obj = by_id.get(edge["subject"]), by_id.get(edge["object"])
        if subject is None or obj is None:
            dangling += 1
        if slot not in helper.slots:
            unknown_edges += 1
            bad_predicates[predicate] += 1
        elif subject is not None and obj is not None and not relation_is_valid(
                helper, str(subject.get("category")), slot, str(obj.get("category"))):
            domain_range += 1

    categories: dict[str, dict[str, Any]] = {
        CONTRACT_ERROR: {"count": len(errors), "unit": "items", "examples": sorted(errors)[:_TOP_N]},
        LEGACY_WARNING: {
            "count": unknown_nodes + unknown_edges + domain_range + dangling + (
                1 if status == BINDING_LEGACY else 0),
            "unit": "mixed; see the per-kind counts",
            "nodes_with_category_outside_closure": unknown_nodes,
            "edges_with_predicate_outside_closure": unknown_edges,
            "edges_failing_domain_range": domain_range,
            "edges_with_missing_endpoint_node": dangling,
            "artifact_without_schema_fingerprint": status == BINDING_LEGACY,
            "categories_outside_closure": _top(bad_categories),
            "predicates_outside_closure": _top(bad_predicates),
        },
        INCOMPLETE_EXTRACTION: {"count": incomplete, "unit": "nodes", "rule": "description is N/A or empty"},
        UNKNOWN_SOURCE_DATA: {
            "count": no_source, "unit": "nodes", "rule": "non-snippet node without source_papers"},
    }
    counts = {"nodes": len(nodes), "edges": len(edges), "codesnippet_nodes": snippets}
    return GraphValidationReport(
        schema=schema_block, fingerprint_status=status, normalizer=implementation,
        selection_source=selection_source, counts=counts, categories=categories,
    )


def categories_of(nodes: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Category histogram of a node list (used to prove validation retypes nothing)."""
    return dict(Counter(str(n.get("category")) for n in nodes if isinstance(n, Mapping)))
