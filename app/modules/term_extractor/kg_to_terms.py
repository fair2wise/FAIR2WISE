"""Reconstruct an extracted-terms JSON from an existing knowledge-graph JSON.

The forward direction is ``json2kg.build_graph``: terms become nodes, relations become ``rel:X`` edges,
relation targets that are not terms become *stubs* (nodes with no source papers and an empty description),
``code_snippets`` become ``CodeSnippet`` nodes and ``has_code_snippet`` edges are synthesised. This module
walks that mapping backwards so the extraction pipeline can continue onto an existing KG: add papers to the
reconstructed terms, then rebuild with ``json2kg.convert_terms_to_graph``.

Field mapping (flat ``{things, associations, metadata?}`` shape)
----------------------------------------------------------------

Term nodes (every node that is not a ``CodeSnippet`` and not a stub):

* ``name`` -> ``term``; ``description`` -> ``definition`` (``"N/A"``, the forward placeholder, becomes ``""``).
* ``category``, ``raw_category``, ``formula``, ``formula_validation``, ``pages``, ``source_papers``,
  ``context_snippets``, ``properties``, ``source_metadata``, ``publications`` and the chemistry fields
  (``chebi``, ``smiles``, ``charge``, ``inchi``, ``inchikey``, ``mass``) are copied.
* ``importance`` is recomputed with ``TermStore.importance_tier`` (the stored value is not trusted).
* Publication scalars (``publication_year``, ``paper_title``, ``authors``, ``institutions``, ``doi``,
  ``journal``, ``volume``, ``issue``, ``pages_range``, ``abstract_text``, ``keywords``) become top-level term
  keys when non-empty; ``TermRecord.extra`` carries them through ``TermStore``.
* Every other node key (``type``, ``pv``, ``ophyd_name``, ``aliases`` ...) becomes a top-level term key
  and travels in ``TermRecord.extra``; ``json2kg.copy_passthrough_slots`` puts it back on the node.
* ``id`` / ``id_prefix`` are written only where ``make_id(name) != id`` (beamline ``beamline:`` nodes,
  ``rsoxs:`` nodes); an id that no ``make_id`` call can reproduce is reported in the warnings.

Stubs: a node with no ``source_papers``, an empty ``description``, no outgoing relation edge and at least
one incoming edge is a stub. It is not promoted to a term (it is regenerated from the relation); the number
dropped is ``KgToTermsResult.dropped_stubs``. A node with no papers and no description that has outgoing
relations, or that nothing points at, cannot be a stub and is kept as a term.

Edges: ``rel:X`` -> relation ``X``; ``related_term`` is the object node's ``name`` (plus ``related_id``
when ``make_id(name)`` would not land on the object id); ``raw_predicate`` is kept; ``has_evidence`` is split
on ``"; "`` into ``evidence`` unless the edge carries a structured ``evidence`` list (mappings ``{text, source_paper, page,
kind, verified}``), which is read first and kept as it is. Synthesised ``rel:has_code_snippet`` edges (no evidence, no
``raw_predicate``) are not reversed: ``build_graph`` rewires them from papers and pages. One that carries
evidence is a real relation (beamline KGs) and is kept, with ``related_id`` pointing at the snippet node.
Edges whose subject is a snippet are skipped.

``CodeSnippet`` nodes become ``code_snippets`` entries with ``curated=True``, ``source_paper`` =
``source_papers[0]``, ``page`` = ``pages[0]`` or ``0`` and the repository and domain fields; ``id`` is
only written if the forward id would differ.

Metadata: the KG ``metadata`` plus ``kg_to_terms`` bookkeeping (``reconstructed: True``,
``extractor: "unknown"``), ``processed_files`` = distinct source papers, page counters ``None`` (unknown;
``TermStore`` treats ``None`` as absent), ``version`` ``"2.1"``.

The ``nodes`` / ``edges`` shape (``matkg_rsoxs_v3.json``)
---------------------------------------------------------

Detected by its keys and converted to the flat shape first: ``label`` -> ``name``, ``entityType`` ->
``category``, ``papers`` -> ``source_papers``, ``description`` and ``pages`` as is; ``aliases`` and every
non-null ``slots`` entry become top-level passthrough keys (``TermRecord.extra``); null slots, ``graph_id``
and unknown node keys are not placed and are reported. ``prov:*`` nodes and ``prov:*`` edges describe the
conversion run itself, not content: they are dropped and counted. Top-level graph keys (``graph_id``,
``corpus_revision``, ``source_terms``, ``prov_metadata`` ...) are copied into ``metadata``. This shape has
no publications, ``source_metadata`` or evidence-bearing snippets, and its ``source_papers`` are DOIs, not
PDF file names.

Failure policy: two terms whose ``TermStore.normalize`` keys collide would be merged by the store, so the
conversion raises ``KgToTermsError`` naming them unless ``lossy=True`` (first kept, rest dropped with a warning).
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass, field
import datetime
import json
import logging
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import json2kg
from ..cli_output import output
from .models import TermRecord
from .store import TermStore

logger = logging.getLogger(__name__)

app = typer.Typer(help="Reconstruct an extracted-terms JSON from a knowledge-graph JSON.")

#: Version string written to reconstructed terms metadata (same as ``TermStore``).
TERMS_VERSION = "2.1"
_DEFAULT_PREFIX = "matkg"
_STUB_REL = "rel:has_code_snippet"
_CHEMISTRY_FIELDS = ("chebi", "smiles", "charge", "inchi", "inchikey", "mass")
#: Node keys that the forward mapping always sets and that this module maps explicitly.
_HANDLED_NODE_KEYS = frozenset(
    {
        "id",
        "name",
        "category",
        "raw_category",
        "description",
        "pages",
        "source_papers",
        "context_snippets",
        "formula",
        "formula_validation",
        "properties",
        "source_metadata",
        "publications",
        "importance",
        *json2kg._PUB_FIELDS,
        *_CHEMISTRY_FIELDS,
    }
)
_SNIPPET_COPY_KEYS = (
    "function_name",
    "code_snippet",
    "code_language",
    "code_description",
    "code_domain",
    "domain_features",
    "source_type",
    "repo_url",
    "repo_owner",
    "repo_name",
    "repo_default_branch",
    "repo_commit_sha",
    "source_file_path",
    "source_file_url",
    "source_start_line",
    "source_end_line",
    "repository_license",
    "license_warning",
    "source_score",
    "publication_year",
    "paper_title",
    "authors",
    "paper_authors",
    "doi",
    "source_metadata",
    "publications",
)
_SNIPPET_NODE_KEYS = frozenset(json2kg.make_code_snippet_node({"code_snippet": "x"}))


class KgToTermsError(ValueError):
    """Raised when a KG cannot be reversed safely (unreadable shape, key collisions, duplicate ids)."""


@dataclass
class KgToTermsResult:
    """Outcome of ``kg_to_terms``.

    Attributes:
        terms: The reconstructed extracted-terms document: ``metadata``, ``terms``, ``code_snippets``.
        warnings: Human-readable notes on everything that could not be placed or was changed.
        processed_pdfs: Sorted union of the source papers of the terms and snippets.
        dropped_stubs: Number of stub nodes not promoted to terms.
        dropped_terms: Node ids dropped because a ``TermStore`` key collision (``lossy=True`` only).
    """

    terms: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    processed_pdfs: list[str] = field(default_factory=list)
    dropped_stubs: int = 0
    dropped_terms: list[str] = field(default_factory=list)


def _is_empty(value: object) -> bool:
    """Return True for the values ``copy_passthrough_slots`` treats as absent."""
    return value in (None, "", [], {})


def _local_part(node_id: str) -> str:
    """Return the part of an id after its first ``:`` (the whole id if it has none)."""
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def _id_hint(node_id: str, prefix: str, *, curie_first: bool = False) -> tuple[str, str | None] | None:
    """Find ``(id, id_prefix)`` input for which ``make_id`` reproduces ``node_id``.

    The id is split at its first ``:`` so the term keeps its own prefix (which also decides the prefix of the
    stubs its relations create); the whole CURIE is used when the split does not reproduce it, or first when
    ``curie_first`` is set (relation targets: ``build_graph`` names a stub after its ``related_id`` and later
    replaces a CURIE name by the real term's name, which a bare local id would not allow).

    Args:
        node_id: The node id to reproduce.
        prefix: Prefix ``make_id`` will be called with when ``id_prefix`` is not set.
        curie_first: Try the whole id as a known CURIE before splitting off the prefix.

    Returns:
        ``(id_value, id_prefix_or_None)`` (``None`` when ``prefix`` already applies) or ``None`` when no
        input reproduces the id.
    """
    if curie_first and json2kg.split_curie(node_id) and json2kg.make_id(node_id, prefix=prefix) == node_id:
        return node_id, None
    if ":" in node_id:
        node_prefix, local = node_id.split(":", 1)
        if json2kg.make_id(local, prefix=node_prefix) == node_id:
            return local, (None if node_prefix == prefix else node_prefix)
    if json2kg.split_curie(node_id) and json2kg.make_id(node_id, prefix=prefix) == node_id:
        return node_id, None
    return None


def detect_shape(kg: Mapping[str, Any]) -> str:
    """Identify the KG JSON shape from its keys.

    Args:
        kg: Parsed KG JSON.

    Returns:
        ``"flat"`` for ``{things, associations}`` or ``"nodes_edges"`` for ``{nodes, edges}``.

    Raises:
        KgToTermsError: If the document is neither shape (or is both).
    """
    if not isinstance(kg, Mapping):
        raise KgToTermsError(f"KG JSON must be an object, got {type(kg).__name__}")
    flat = "things" in kg and "associations" in kg
    nodes_edges = "nodes" in kg and "edges" in kg
    if flat and nodes_edges:
        raise KgToTermsError("ambiguous KG: has both things/associations and nodes/edges")
    if flat:
        return "flat"
    if nodes_edges:
        return "nodes_edges"
    raise KgToTermsError(
        "unrecognised KG shape: expected top-level keys things+associations or nodes+edges, "
        f"found {sorted(str(k) for k in kg)[:12]}"
    )


def _adapt_nodes_edges(
    kg: Mapping[str, Any], warnings: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Convert the ``nodes``/``edges`` shape to flat-shaped nodes, edges and metadata.

    Args:
        kg: Parsed ``{nodes, edges, ...}`` document.
        warnings: List that receives one note per field that cannot be placed.

    Returns:
        ``(things, associations, metadata)`` in the flat shape.
    """
    raw_nodes, raw_edges = kg["nodes"], kg["edges"]
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise KgToTermsError("nodes and edges must be lists")
    things: list[dict[str, Any]] = []
    prov_nodes = prov_edges = 0
    aliases = flattened_slots = null_slots = 0
    unplaced: dict[str, int] = {}
    graph_ids: set[str] = set()
    for node in raw_nodes:
        if not isinstance(node, dict) or "id" not in node:
            raise KgToTermsError("every node needs an object with an 'id'")
        if str(node.get("entityType", "")).startswith("prov:") or str(node.get("type", "")).startswith("prov:"):
            prov_nodes += 1
            continue
        node_id = str(node["id"])
        flat: dict[str, Any] = {
            "id": node_id,
            "name": node.get("label") if node.get("label") is not None else _local_part(node_id),
            "category": node.get("entityType"),
            "description": node.get("description") or "",
            "pages": list(node.get("pages") or []),
            "source_papers": list(node.get("papers") or []),
        }
        if node.get("aliases"):
            flat["aliases"] = list(node["aliases"])
            aliases += 1
        slots = node.get("slots")
        if isinstance(slots, dict):
            for slot, value in slots.items():
                if _is_empty(value):
                    null_slots += 1
                elif slot in flat or slot in _HANDLED_NODE_KEYS:
                    unplaced[f"slots.{slot}"] = unplaced.get(f"slots.{slot}", 0) + 1
                else:
                    flat[slot] = value
                    flattened_slots += 1
        for key, value in node.items():
            if key in {"id", "label", "entityType", "description", "pages", "papers", "aliases", "slots"}:
                continue
            if key == "graph_id":
                graph_ids.add(str(value))
            elif key == "type":
                flat.setdefault("type", value)
            elif key not in flat:
                flat[key] = value
                unplaced[key] = unplaced.get(key, 0) + 1
        things.append(flat)
    associations: list[dict[str, Any]] = []
    for edge in raw_edges:
        if not isinstance(edge, dict) or not {"subject", "predicate", "object"} <= set(edge):
            raise KgToTermsError("every edge needs an object with subject, predicate and object")
        if str(edge.get("type", "")).startswith("prov:") or str(edge["predicate"]).startswith(
            "http://www.w3.org/ns/prov#"
        ):
            prov_edges += 1
            continue
        associations.append(dict(edge))
    metadata = {k: copy.deepcopy(v) for k, v in kg.items() if k not in {"nodes", "edges"}}
    warnings.append(
        "nodes/edges adapter: label->name, entityType->category, papers->source_papers, "
        "description/pages unchanged; source_papers are DOIs (not PDF file names), so processed_pdfs "
        "will not match PDFs on disk"
    )
    if prov_nodes or prov_edges:
        warnings.append(
            f"dropped {prov_nodes} prov:* nodes and {prov_edges} prov:* edges (provenance of the earlier "
            "conversion run, not extracted content; graph-level provenance is kept in metadata)"
        )
    if aliases:
        warnings.append(f"{aliases} nodes carry aliases: kept as passthrough key 'aliases' (TermRecord.extra only)")
    if flattened_slots or null_slots:
        warnings.append(
            f"slots: {flattened_slots} non-null values flattened to top-level passthrough keys; "
            f"{null_slots} null/empty slot values dropped"
        )
    if graph_ids:
        warnings.append(f"node key 'graph_id' ({sorted(graph_ids)}) not placed on terms (kept in metadata only)")
    for key, count in sorted(unplaced.items()):
        warnings.append(f"node key '{key}' on {count} nodes has no TermRecord field: passthrough only")
    warnings.append(
        "no publications, source_metadata, context_snippets, formula or properties exist in this shape: "
        "terms reconstructed without them"
    )
    return things, associations, metadata


def _snippet_entry(node: Mapping[str, Any], prefix: str, warnings: list[str]) -> dict[str, Any] | None:
    """Reverse one ``CodeSnippet`` node into a ``code_snippets`` entry, or None if it cannot be rebuilt."""
    body = str(node.get("code_snippet") or "").strip()
    if not body:
        warnings.append(f"CodeSnippet node {node.get('id')!r} has no code_snippet body: dropped")
        return None
    entry: dict[str, Any] = {key: copy.deepcopy(node[key]) for key in _SNIPPET_COPY_KEYS if node.get(key) is not None}
    if "code_description" not in entry and node.get("description"):
        entry["code_description"] = node["description"]
    papers = list(node.get("source_papers") or [])
    pages = list(node.get("pages") or [])
    if len(papers) > 1 or len(pages) > 1:
        warnings.append(f"CodeSnippet node {node.get('id')!r} has several papers/pages: only the first is kept")
    entry["source_paper"] = papers[0] if papers else ""
    entry["page"] = pages[0] if pages else 0
    entry["curated"] = True
    node_id = str(node["id"])
    if json2kg.make_code_snippet_node(entry, prefix=prefix)["id"] != node_id:
        hint = _id_hint(node_id, prefix)
        if hint is None:
            warnings.append(f"CodeSnippet id {node_id!r} cannot be reproduced by make_id: the rebuilt id differs")
        else:
            entry["id"] = hint[0]
            if hint[1]:
                entry["id_prefix"] = hint[1]
    return entry


def _term_from_node(
    node: Mapping[str, Any], prefix: str, warnings: list[str]
) -> tuple[dict[str, Any], str]:
    """Reverse one term node into a term dict (without relations); also return its effective id prefix."""
    node_id = str(node["id"])
    name = node.get("name")
    if not isinstance(name, str) or not name:
        warnings.append(f"node {node_id!r} has no usable name: using the id")
        name = _local_part(node_id)
    description = node.get("description")
    pages = copy.deepcopy(list(node.get("pages") or []))
    papers = copy.deepcopy(list(node.get("source_papers") or []))
    term: dict[str, Any] = {
        "term": name,
        "definition": "" if description in (None, "N/A") else str(description),
        "category": node["category"] if node.get("category") is not None else "Unknown",
    }
    if node.get("raw_category") is not None:
        term["raw_category"] = node["raw_category"]
    if node.get("formula"):
        term["formula"] = node["formula"]
    if node.get("formula_validation"):
        term["formula_validation"] = copy.deepcopy(node["formula_validation"])
    term["relations"] = []
    term["pages"] = pages
    term["source_papers"] = papers
    term["context_snippets"] = copy.deepcopy(list(node.get("context_snippets") or []))
    term["properties"] = copy.deepcopy(list(node.get("properties") or []))
    if node.get("aliases"):
        term["aliases"] = [str(a) for a in node.get("aliases") or []]
    term["source_metadata"] = copy.deepcopy(node.get("source_metadata") or {})
    term["publications"] = copy.deepcopy(list(node.get("publications") or []))
    for key in _CHEMISTRY_FIELDS:
        if not _is_empty(node.get(key)):
            term[key] = copy.deepcopy(node[key])
    term["importance"] = TermStore.importance_tier(pages, papers)
    for key in json2kg._PUB_FIELDS:
        if not _is_empty(node.get(key)):
            term[key] = copy.deepcopy(node[key])
    for key, value in node.items():
        if key not in _HANDLED_NODE_KEYS and not _is_empty(value):
            term[key] = copy.deepcopy(value)
    effective_prefix = prefix
    if json2kg.make_id(name, prefix=prefix) != node_id:
        hint = _id_hint(node_id, prefix)
        if hint is None:
            warnings.append(f"node id {node_id!r} cannot be reproduced by make_id: the rebuilt id will differ")
        else:
            term["id"] = hint[0]
            if hint[1]:
                term["id_prefix"] = hint[1]
                effective_prefix = hint[1]
    return term, effective_prefix


def _split_evidence(value: object) -> list[str]:
    """Split a ``has_evidence`` edge value back into the evidence list."""
    if _is_empty(value):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return str(value).split("; ")


def kg_to_terms(kg: Mapping[str, Any], *, source: str = "", lossy: bool = False) -> KgToTermsResult:
    """Reconstruct an extracted-terms document from a KG JSON.

    The input is never modified. See the module docstring for the full field mapping.

    Args:
        kg: Parsed KG JSON, either ``{things, associations}`` or ``{nodes, edges}``.
        source: Label of the KG (usually its path), stored in ``metadata["kg_to_terms"]["source_kg"]``.
        lossy: When True, terms whose ``TermStore.normalize`` keys collide (and duplicate node ids) keep the
            first one and produce a warning instead of an error.

    Returns:
        The reconstructed terms, warnings, processed PDF names and the number of stubs dropped.

    Raises:
        KgToTermsError: On an unreadable shape, duplicate node ids, or key collisions when ``lossy`` is False.
    """
    shape = detect_shape(kg)
    warnings: list[str] = []
    if shape == "nodes_edges":
        things, associations, kg_meta = _adapt_nodes_edges(kg, warnings)
    else:
        things, associations = kg["things"], kg["associations"]
        if not isinstance(things, list) or not isinstance(associations, list):
            raise KgToTermsError("things and associations must be lists")
        kg_meta = copy.deepcopy(kg["metadata"]) if isinstance(kg.get("metadata"), dict) else {}
        extra_keys = sorted(str(k) for k in kg if k not in {"things", "associations", "metadata"})
        if extra_keys:
            warnings.append(f"ignored top-level KG keys: {extra_keys}")
    prefix = str(kg_meta.get("id_prefix") or _DEFAULT_PREFIX)

    nodes: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for node in things:
        if not isinstance(node, dict) or "id" not in node:
            raise KgToTermsError("every node needs an object with an 'id'")
        node_id = str(node["id"])
        if node_id in nodes:
            duplicates.append(node_id)
            continue
        nodes[node_id] = node
    if duplicates:
        message = f"{len(duplicates)} duplicate node ids, e.g. {duplicates[:5]}"
        if not lossy:
            raise KgToTermsError(message + " (use lossy=True to keep the first of each)")
        warnings.append(message + ": kept the first of each")

    snippet_ids = {
        nid
        for nid, n in nodes.items()
        if n.get("category") == "CodeSnippet" and str(n.get("code_snippet") or "").strip()
    }
    demoted = [nid for nid, n in nodes.items() if n.get("category") == "CodeSnippet" and nid not in snippet_ids]
    if demoted:
        warnings.append(
            f"{len(demoted)} nodes in category CodeSnippet have no code body (not from code_snippets): kept as "
            "terms; build_graph demotes their category to Unknown (raw_category CodeSnippet)"
        )
    outgoing: set[str] = set()
    incoming: set[str] = set()
    for edge in associations:
        if not isinstance(edge, dict) or not {"subject", "predicate", "object"} <= set(edge):
            raise KgToTermsError("every edge needs an object with subject, predicate and object")
        if edge["predicate"] != _STUB_REL:
            outgoing.add(str(edge["subject"]))
            incoming.add(str(edge["object"]))
    stub_ids = {
        nid
        for nid, n in nodes.items()
        if nid not in snippet_ids
        and not n.get("source_papers")
        and n.get("description") in (None, "")
        and nid not in outgoing
        and nid in incoming
    }

    code_snippets: list[dict[str, Any]] = []
    terms: dict[str, dict[str, Any]] = {}
    prefixes: dict[str, str] = {}
    seen_keys: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    dropped_by_collision: set[str] = set()
    for nid, node in nodes.items():
        if nid in snippet_ids:
            entry = _snippet_entry(node, prefix, warnings)
            if entry is not None:
                code_snippets.append(entry)
            extra = sorted(k for k in node if k not in _SNIPPET_NODE_KEYS and not _is_empty(node[k]))
            if extra:
                warnings.append(f"CodeSnippet node {nid!r} keys not representable in code_snippets: {extra}")
            continue
        if nid in stub_ids:
            continue
        term, term_prefix = _term_from_node(node, prefix, warnings)
        key = TermStore.normalize(term["term"])
        if key in seen_keys:
            collisions.setdefault(key, [seen_keys[key]]).append(nid)
            dropped_by_collision.add(nid)
            continue
        seen_keys[key] = nid
        terms[nid] = term
        prefixes[nid] = term_prefix
    if collisions:
        listing = "; ".join(f"{k!r}: {ids}" for k, ids in sorted(collisions.items()))
        message = f"{len(collisions)} TermStore key collision(s) between node ids: {listing}"
        if not lossy:
            raise KgToTermsError(message + " (use lossy=True to keep the first of each)")
        warnings.append(message + ": kept the first of each, dropped the others")

    targets = _attach_relations(nodes, associations, terms, prefixes, snippet_ids, dropped_by_collision, prefix, warnings, lossy)

    light = {nid for nid in terms if _is_light(nodes[nid])}
    order, lost = _processing_order([n for n in nodes if n not in snippet_ids], terms, targets, light, snippet_ids)
    term_list = [terms[nid] for nid in order]
    if lost:
        warnings.append(
            f"{len(lost)} terms that carry pages/snippets/properties are in relation cycles: rebuilding creates "
            f"them from a relation first, so build_graph keeps only their category, description and papers "
            f"(e.g. {lost[:3]})"
        )
    result_terms = _finish(term_list, code_snippets, kg_meta, source, warnings)
    pdfs = processed_pdfs_for(result_terms)
    result_terms["metadata"]["processed_files"] = len(pdfs)
    return KgToTermsResult(
        terms=result_terms,
        warnings=warnings,
        processed_pdfs=pdfs,
        dropped_stubs=len(stub_ids),
        dropped_terms=sorted(dropped_by_collision),
    )


def _attach_relations(
    nodes: Mapping[str, Mapping[str, Any]],
    associations: list[dict[str, Any]],
    terms: dict[str, dict[str, Any]],
    prefixes: Mapping[str, str],
    snippet_ids: set[str],
    dropped: set[str],
    default_prefix: str,
    warnings: list[str],
    lossy: bool,
) -> dict[str, list[str]]:
    """Reverse the relation edges into the ``relations`` lists of ``terms`` (in place).

    Returns:
        For each subject id, the ids its relations point at (in edge order, without repeats).
    """
    targets: dict[str, list[str]] = {}
    seen: set[tuple[str, str, str]] = set()
    skipped_subject = dangling = unhinted = bare_predicates = extra_edge_keys = 0
    odd_predicates: set[str] = set()
    for edge in associations:
        subject, predicate, obj = str(edge["subject"]), str(edge["predicate"]), str(edge["object"])
        synthesised = predicate == _STUB_REL and _is_empty(edge.get("has_evidence")) and not edge.get("raw_predicate")
        if synthesised or subject in snippet_ids:
            continue
        if subject in dropped:
            continue
        if subject not in terms:
            skipped_subject += 1
            continue
        if (subject, predicate, obj) in seen:
            continue
        seen.add((subject, predicate, obj))
        target = nodes.get(obj)
        if target is None:
            dangling += 1
            related_term = _local_part(obj)
        else:
            related_term = str(target.get("name") or _local_part(obj))
        relation: dict[str, Any] = {
            "relation": predicate.removeprefix("rel:"),
            "related_term": related_term,
        }
        if not predicate.startswith("rel:"):
            odd_predicates.add(predicate)
            bare_predicates += 1
        if edge.get("raw_predicate"):
            relation["raw_predicate"] = edge["raw_predicate"]
        structured = edge.get("evidence")
        if isinstance(structured, list) and any(isinstance(item, dict) for item in structured):
            relation["evidence"] = [dict(item) if isinstance(item, dict) else str(item) for item in structured]   # read first
        else:
            evidence = _split_evidence(edge.get("has_evidence"))
            if evidence:
                relation["evidence"] = evidence
        subject_prefix = prefixes.get(subject, default_prefix)
        if json2kg.make_id(related_term, prefix=subject_prefix) != obj:
            hint = _id_hint(obj, subject_prefix, curie_first=True)
            if hint is None:
                unhinted += 1
            else:
                relation["related_id"] = hint[0]
                if hint[1]:
                    relation["related_id_prefix"] = hint[1]
        extras = set(edge) - {"subject", "predicate", "object", "has_evidence", "evidence", "raw_predicate"}
        if extras:
            extra_edge_keys += 1
        terms[subject]["relations"].append(relation)
        if obj not in targets.setdefault(subject, []):
            targets[subject].append(obj)
    if skipped_subject:
        warnings.append(f"{skipped_subject} edges skipped: subject is not a term node (missing or stub)")
    if dangling:
        warnings.append(f"{dangling} edges point at ids with no node: target name taken from the id")
    if unhinted:
        warnings.append(f"{unhinted} edge targets cannot be reproduced by make_id: rebuilt target ids will differ")
    if bare_predicates:
        message = (
            f"{bare_predicates} edges use predicates without the 'rel:' prefix {sorted(odd_predicates)[:5]}: "
            "build_graph would rewrite them to rel:<predicate>"
        )
        if not lossy:
            raise KgToTermsError(message + " (use lossy=True to convert them anyway)")
        warnings.append(message)
    if extra_edge_keys:
        warnings.append(f"{extra_edge_keys} edges carry keys other than subject/predicate/object/has_evidence/evidence/raw_predicate: not kept")
    return targets


def _is_light(node: Mapping[str, Any]) -> bool:
    """Return True if the node carries nothing that ``build_graph`` loses when a stub is filled in later.

    ``build_graph`` merges only category, name, description, source papers and passthrough keys into a node
    that a relation already created, so a node without pages, snippets, properties, formula, source metadata,
    publications or publication scalars is the same whether it was created by its own term or as a stub.
    The empty ``publications`` matters: a node created by its own single-paper term always has one entry.
    """
    if node.get("publications"):
        return False
    if any(node.get(key) for key in ("pages", "context_snippets", "properties", "formula", "formula_validation")):
        return False
    if node.get("source_metadata"):
        return False
    return all(_is_empty(node.get(key)) for key in json2kg._PUB_FIELDS)


def _processing_order(
    node_order: list[str],
    terms: Mapping[str, Any],
    targets: Mapping[str, list[str]],
    light: set[str],
    existing: set[str],
) -> tuple[list[str], list[str]]:
    """Order the terms so that rebuilding creates the nodes in KG order with no data lost.

    ``build_graph`` creates a node when a term or a relation first touches it, and later merges only a few
    fields into it. Two things depend on that order: a term touched by an earlier relation keeps only its
    light fields, and the paper-level ``has_code_snippet`` fallback wires each snippet to the first node
    (in creation order) of its paper. This replays the build: walking the KG nodes in order, a node is
    emitted as its own term unless it is ``light`` and a term already created earlier is waiting whose new
    relation targets start with that node; then that waiting term is emitted instead (it creates the node as its
    stub, as originally) provided the nodes it would create are exactly the ones that follow in KG order. Terms that carry data are never emitted after a relation created them.
    """
    created: set[str] = set(existing)
    emitted: set[str] = set()
    order: list[str] = []
    lost: list[str] = []
    referrers: dict[str, list[str]] = {}
    for subject in node_order:
        for target in targets.get(subject, ()):
            referrers.setdefault(target, []).append(subject)

    def emit(first: str) -> None:
        """Emit ``first`` after any data-carrying term it would stub-create (iterative, cycle-safe)."""
        stack = [first]
        visiting = {first}
        while stack:
            tid = stack[-1]
            pending = next(
                (
                    t
                    for t in targets.get(tid, ())
                    if t in terms and t not in created and t not in emitted and t not in light and t not in visiting
                ),
                None,
            )
            if pending is not None:
                stack.append(pending)
                visiting.add(pending)
                continue
            stack.pop()
            visiting.discard(tid)
            if tid in emitted:
                continue
            emitted.add(tid)
            order.append(tid)
            created.add(tid)
            for target in targets.get(tid, ()):
                if target in terms and target not in light and target not in created:
                    lost.append(target)
                created.add(target)

    position = {nid: index for index, nid in enumerate(node_order)}

    def creates_next(tid: str, nid: str) -> bool:
        """Return True if emitting ``tid`` now would create ``nid`` and then the KG nodes that follow it."""
        new = [t for t in targets.get(tid, ()) if t not in created and t in position]
        start = position[nid]
        return (
            bool(new)
            and new[0] == nid
            and node_order[start : start + len(new)] == new
            and all(t not in terms or t in light for t in new)
        )

    for nid in node_order:
        if nid in created or nid not in terms and nid not in referrers:
            continue
        if nid in terms and nid not in light:
            emit(nid)
            continue
        for ref in referrers.get(nid, ()):
            if ref in created and ref not in emitted and ref in terms and creates_next(ref, nid):
                emit(ref)
                break
        else:
            if nid in terms:
                emit(nid)
    for nid in node_order:
        if nid in terms and nid not in emitted:
            emit(nid)
    return order, lost


def _finish(
    term_list: list[dict[str, Any]],
    code_snippets: list[dict[str, Any]],
    kg_meta: dict[str, Any],
    source: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Assemble the terms document and check each term is readable by ``TermStore``."""
    metadata = dict(kg_meta)
    metadata.update(
        {
            "processed_files": 0,
            "processed_pages_total": None,
            "processed_pages_with_terms": None,
            "version": TERMS_VERSION,
            "kg_to_terms": {"source_kg": source, "reconstructed": True, "extractor": "unknown"},
        }
    )
    unreadable = 0
    for term in term_list:
        try:
            TermRecord.from_dict(term)
        except (KeyError, TypeError, ValueError, AttributeError):
            unreadable += 1
    if unreadable:
        warnings.append(
            f"{unreadable} terms have context_snippets/properties/relations that TermStore cannot read "
            "(missing required keys): it will skip them with a warning"
        )
    return {"metadata": metadata, "terms": term_list, "code_snippets": code_snippets}


def processed_pdfs_for(terms: Mapping[str, Any]) -> list[str]:
    """Return the sorted union of the source papers of a terms document.

    Args:
        terms: ``{"terms": [...], "code_snippets": [...]}`` as produced by ``kg_to_terms``.

    Returns:
        Sorted, de-duplicated paper identifiers from terms (``source_papers``) and snippets (``source_paper``).
    """
    papers: set[str] = set()
    for term in terms.get("terms") or []:
        papers.update(str(p) for p in term.get("source_papers") or [] if p)
    for snippet in terms.get("code_snippets") or []:
        if snippet.get("source_paper"):
            papers.add(str(snippet["source_paper"]))
    return sorted(papers)


def reconstructed_extraction_manifest(
    terms: Mapping[str, Any],
    *,
    terms_json: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build an extraction manifest marking every source paper as ``reconstructed``.

    Same shape as ``Coordinator._record_full_extraction`` writes (``papers`` keyed by file name with
    ``filename``, ``pdf_sha256``, ``extraction_state``, ``timestamp``, ``terms_json`` and the page counters),
    but ``extraction_state`` is ``"reconstructed"`` (never ``"full"``), the hash is ``None`` and no ``full``
    block is written, so nothing treats the paper as fully extracted.

    Args:
        terms: The reconstructed terms document.
        terms_json: Path of the terms file to record in each entry, if known.
        timestamp: ISO timestamp; defaults to now (UTC).

    Returns:
        ``{"papers": {name: entry}}``.
    """
    now = timestamp or datetime.datetime.now(datetime.UTC).isoformat()
    papers = {
        name: {
            "filename": name,
            "pdf_sha256": None,
            "extraction_state": "reconstructed",
            "timestamp": now,
            "terms_json": terms_json,
            "processed_pages_total": None,
            "processed_pages_with_terms": None,
        }
        for name in processed_pdfs_for(terms)
    }
    return {"papers": papers}


@app.callback()
def main() -> None:
    """Reverse knowledge-graph JSON files into extracted-terms JSON files."""


@app.command()
def convert(
    kg_json: Annotated[Path, typer.Argument(help="Input KG JSON (things/associations or nodes/edges).")],
    out_terms_json: Annotated[Path, typer.Argument(help="Where to write the reconstructed terms JSON.")],
    lossy: Annotated[bool, typer.Option("--lossy", help="Keep the first of colliding terms instead of failing.")] = False,
    manifest_dir: Annotated[
        Path | None,
        typer.Option(
            "--manifest-dir",
            help="Also write processed_pdfs.json and extraction_manifest.json (state 'reconstructed') here.",
        ),
    ] = None,
) -> None:
    """Reconstruct an extracted-terms JSON from a KG JSON so extraction can continue onto it."""
    try:
        with kg_json.open(encoding="utf-8") as handle:
            kg = json.load(handle)
        result = kg_to_terms(kg, source=str(kg_json), lossy=lossy)
    except (OSError, json.JSONDecodeError) as exc:
        output.error("Cannot read %s: %s", kg_json, exc)
        raise typer.Exit(code=1) from exc
    except KgToTermsError as exc:
        output.error("Cannot convert %s: %s", kg_json, exc)
        raise typer.Exit(code=2) from exc
    out_terms_json.parent.mkdir(parents=True, exist_ok=True)
    out_terms_json.write_text(json.dumps(result.terms, indent=2, ensure_ascii=False), encoding="utf-8")
    for message in result.warnings:
        output.warning("warning: %s", message)
    output.info(
        "Wrote %d terms and %d code snippets to %s (%d stubs not promoted, %d source papers)",
        len(result.terms["terms"]),
        len(result.terms["code_snippets"]),
        out_terms_json,
        result.dropped_stubs,
        len(result.processed_pdfs),
    )
    if manifest_dir is not None:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "processed_pdfs.json").write_text(json.dumps(result.processed_pdfs, indent=2), encoding="utf-8")
        manifest = reconstructed_extraction_manifest(result.terms, terms_json=str(out_terms_json))
        (manifest_dir / "extraction_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        output.info("Wrote manifests to %s", manifest_dir)


if __name__ == "__main__":
    app()
