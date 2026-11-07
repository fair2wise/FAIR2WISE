"""Apply adapters (T07B, H4): from an ACCEPTED curation case to the gated canonicalization apply routes.

The production path this module wires::

    proposal (campaign Decision / online Resolution / reviewer verdict)
        -> ``graph_proposal`` or ``unresolved_term`` case in the curation queue (``curation.approve`` decides)
        -> ACCEPTED case (``resolved``, ``needs_validation = 1``)
        -> :func:`accepted_merges`: one :class:`AcceptedMerge` per case, bound to the case revision
        -> :func:`build_plan`: a :class:`~.migration.ScopedMergePlan` over the live graph rows (source revision)
           and the approved vocabulary (vocabulary revision); unsafe/unknown targets are HELD, visibly
        -> :meth:`MergeApplyService.apply_plan`: one of the three apply routes (``apply_migration``,
           ``apply_cleanup``, ``apply_campaign``), every one of which goes through
           :func:`~.migration.apply_scoped_plan` and so through the single gate of :mod:`.gates`
        -> :class:`EditPortMergeTarget`: the merge is a ``node_patch`` per candidate node, written through the
           existing journaled Save port (:class:`~app.modules.graph.legacy_save.TiledSaveBackend`), never a new
           graph writer.

What this module never does: it constructs no ``ApprovalRecord`` (the API layer records a human's approval
through ``security.authorization.create_approval``), it never flips a gate (the gates stay closed until the
owner's C4 real-model rerun; a real apply answers the typed ``gate_closed`` refusal), it never calls a
model (no judge, no resolver: the proposals were adjudicated and reviewed before they got here), and it
never activates or promotes a vocabulary (H5). A dry run is the default and touches nothing.

The report separates **false merges** (a planned fold that a named negative control or the safety check
says must never happen; always refused, counted so a regression is visible) from **false splits** (an
accepted proposal whose surface is an approved alias of its target and still did not make it into the
plan), as the T07 acceptance asks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any, Protocol

from . import gates as _gates
from ..contracts.changes import ApprovalRecord
from ..contracts.context import ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .campaign.evidence import GraphRows, fold_key
from .campaign.models import Decision, TermEvidence, term_key
from .gates import ApplyRefused
from .measurement_fixture import NAMED_NEGATIVE_CONTROLS
from .merge_safety import check_merge, fold
from .migration import (
    ApplyResult,
    MergeOp,
    PlanDraft,
    apply_campaign,
    apply_cleanup,
    apply_migration,
    plan_migration,
    sha256_json,
)
from .vocabulary import CATEGORIES, Vocabulary

if TYPE_CHECKING:
    from ..contracts.json_types import JsonObject

ADAPTER_VERSION = "t07b-apply-adapters-1"

#: ``Proposal.kind`` / ``payload["proposal_kind"]`` of a canonicalization merge proposal in the review queue.
MERGE_PROPOSAL_KIND = "canonical_merge"
#: Case types an accepted merge may come from.
MERGE_CASE_TYPES = frozenset({"unresolved_term", "graph_proposal"})

#: The three apply routes by name; every one of them goes through ``apply_scoped_plan`` and its gate.
APPLY_ROUTES: Mapping[str, Callable[..., ApplyResult]] = {
    "apply_migration": apply_migration,
    "apply_cleanup": apply_cleanup,
    "apply_campaign": apply_campaign,
}
#: Gate name each route checks (``apply_campaign`` checks ``apply_merge``).
ROUTE_GATES: Mapping[str, str] = {
    "apply_migration": "apply_migration",
    "apply_cleanup": "apply_cleanup",
    "apply_campaign": "apply_merge",
}

#: Fields a merge writes on each candidate node (a ``node_patch`` through the Save port). The node keeps its
#: id, name and category: a merge records the canonical identity, it does not delete or rename the mention.
CANONICAL_FIELDS = ("canonical_slug", "canonical_name", "canonical_category", "canonical_vocabulary_revision",
                    "canonical_plan_id", "canonical_method")

#: F2W graph categories (schema classes, casefolded) -> vocabulary categories. RSoXSMeasurement is_a
#: ExperimentalTechnique in the approved schema (owner, H3 F5), so it is a technique term here.
DEFAULT_CATEGORY_MAP: Mapping[str, str] = {
    "material": "material",
    "technique": "technique",
    "experimentaltechnique": "technique",
    "rsoxsmeasurement": "technique",
    "instrument": "instrument",
    "analysis": "analysis",
    "analysismethod": "analysis",
    "analysistechnique": "analysis",
}

#: Upper bound on accepted cases one apply considers (the plan cap is ``MAX_PLAN_OPS``; this bounds the read).
MAX_ACCEPTED_CASES = 1000

_NEGATIVE_PAIRS = frozenset(
    frozenset((fold(a).replace(" ", ""), fold(b).replace(" ", ""))) for a, b in NAMED_NEGATIVE_CONTROLS
)


# ---------------------------------------------------------------------------------------------------------------
# Graph rows from a KG document
# ---------------------------------------------------------------------------------------------------------------


def _node_key(graph: Mapping[str, Any]) -> str:
    return "things" if "things" in graph or "nodes" not in graph else "nodes"


def _edge_key(graph: Mapping[str, Any]) -> str:
    return "associations" if "associations" in graph or "edges" not in graph else "edges"


def _publication_of(node: Mapping[str, Any]) -> str:
    for key in ("publication_id", "publications", "source_papers"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, Mapping):
                return str(first.get("id") or first.get("doi") or "")
            return str(first)
    return ""


def rows_from_graph(graph: Mapping[str, Any], *, category_map: Mapping[str, str] | None = None) -> GraphRows:
    """Candidate-term rows of a KG document (``things``/``associations`` or ``nodes``/``edges``).

    Only nodes whose category maps onto a vocabulary category are candidate terms; the rest of the graph
    (publications, samples, code snippets) is not a merge candidate and is not read. Every row carries the node
    id, its surface (``name``/``label``), the vocabulary category, the first publication reference and the
    description (or the name) as its context text. ``revision()`` of the result is the plan's source revision.
    """
    cmap = {k.casefold(): v for k, v in (category_map or DEFAULT_CATEGORY_MAP).items()}
    nodes: list[dict[str, Any]] = []
    for node in graph.get(_node_key(graph)) or []:
        if not isinstance(node, Mapping) or not node.get("id"):
            continue
        raw_category = str(node.get("category") or node.get("entityType") or node.get("type") or "")
        category = cmap.get(raw_category.casefold())
        if category not in CATEGORIES:
            continue
        surface = node.get("name") if node.get("name") is not None else node.get("label")
        nodes.append({
            "node_id": str(node["id"]),
            "surface": surface,
            "category": category,
            "graph_category": raw_category,
            "publication_id": _publication_of(node),
            "section_id": str(node.get("section_id") or ""),
            "text": str(node.get("description") or surface or ""),
        })
    degree: dict[str, int] = {}
    for edge in graph.get(_edge_key(graph)) or []:
        if not isinstance(edge, Mapping):
            continue
        for side in ("subject", "source", "object", "target"):
            end = edge.get(side)
            if isinstance(end, str) and end:
                degree[end] = degree.get(end, 0) + 1
    ids = {n["node_id"] for n in nodes}
    return GraphRows(nodes, {k: v for k, v in degree.items() if k in ids})


# ---------------------------------------------------------------------------------------------------------------
# Accepted cases -> merges
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptedMerge:
    """One accepted proposal, as a merge of candidate nodes into one vocabulary entry.

    Attributes:
        case_id / case_revision: The curation case and the revision the reviewer's decision bound to.
        term_key: Campaign term identity (``sha256(category|folded surface)``).
        category: Vocabulary category.
        surface: The mention as proposed.
        variants: Every raw spelling folded into the term.
        node_ids: Candidate nodes the merge touches (from the proposal, else the live graph rows).
        target_slug: Vocabulary entry the nodes fold into.
        method: How the proposal came about (``reviewer_accepted:<source>``).
        confidence: Proposal confidence (reviewer-accepted verdicts carry their own confidence).
        votes: Retained signals (the review decision, the proposal kind).
    """

    case_id: str
    case_revision: str
    term_key: str
    category: str
    surface: str
    variants: tuple[str, ...]
    node_ids: tuple[str, ...]
    target_slug: str
    method: str
    confidence: float
    votes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the merge as a plain dict (tuples as lists)."""
        return {
            "case_id": self.case_id, "case_revision": self.case_revision, "term_key": self.term_key,
            "category": self.category, "surface": self.surface, "variants": list(self.variants),
            "node_ids": list(self.node_ids), "target_slug": self.target_slug, "method": self.method,
            "confidence": round(self.confidence, 4), "votes": list(self.votes),
        }


@dataclass(frozen=True)
class HeldProposal:
    """An accepted case that could not become a merge, kept visible with the reason (and the target it named)."""

    case_id: str
    surface: str
    reason: str
    category: str = ""
    target_slug: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the held proposal as a plain dict."""
        return {"case_id": self.case_id, "surface": self.surface, "reason": self.reason, "category": self.category,
                "target_slug": self.target_slug}


def _nodes_for(rows: GraphRows, category: str, surfaces: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Node ids and raw spellings of the rows whose folded surface equals any of ``surfaces`` in ``category``."""
    keys = {fold_key(s) for s in surfaces if isinstance(s, str) and fold_key(s)}
    ids: list[str] = []
    variants: set[str] = set()
    for node in rows.nodes:
        surface = node.get("surface")
        if not isinstance(surface, str) or str(node.get("category")) != category:
            continue
        if fold_key(surface) in keys:
            ids.append(str(node["node_id"]))
            variants.add(surface.strip())
    return tuple(sorted(set(ids))), tuple(sorted(variants))


def _evidence_json(case: Mapping[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(str(case.get("evidence") or ""))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def accepted_merge(case: Mapping[str, Any], vocabulary: Vocabulary, rows: GraphRows) -> AcceptedMerge | HeldProposal | None:
    """Read one ACCEPTED case as a merge, a held proposal (with reason), or ``None`` (not a merge proposal).

    ``unresolved_term`` cases carry the reviewer's ``resolved`` verdict (``canonical_label``, ``category``,
    ``aliases``): the label must be an exact slug/alias of the vocabulary in that category. ``graph_proposal``
    cases are merge proposals only when their ``proposal_kind`` is :data:`MERGE_PROPOSAL_KIND`; their evidence
    names ``surface``, ``category``, ``target_slug`` and optionally ``node_ids``/``variants``. Any other case
    type or proposal kind is not this adapter's business and is skipped, not held.
    """
    case_id = str(case.get("id") or "")
    revision = str(case.get("revision") or "")
    if case.get("state") != "resolved" or case.get("case_type") not in MERGE_CASE_TYPES:
        return None
    payload = case.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    if case.get("case_type") == "graph_proposal":
        if payload.get("proposal_kind") != MERGE_PROPOSAL_KIND:
            return None
        body = _evidence_json(case)
        surface = str(body.get("surface") or "")
        category = str(body.get("category") or payload.get("category") or "")
        slug = str(body.get("target_slug") or "")
        proposed_nodes = tuple(str(n) for n in body.get("node_ids") or ())
        proposed_variants = tuple(str(v) for v in body.get("variants") or ())
        confidence = float(body.get("confidence") or 1.0)
        source = str(body.get("method") or "campaign")
    else:
        resolution = case.get("resolution") or {}
        if not isinstance(resolution, Mapping) or resolution.get("disposition") != "resolved":
            return HeldProposal(case_id, str(payload.get("surface") or ""), "accepted without a resolved verdict")
        surface = str(payload.get("surface") or "")
        category = str(resolution.get("category") or payload.get("category") or "")
        label = str(resolution.get("canonical_label") or "")
        entry = vocabulary.match(label, category or None) if label else None
        if entry is None:
            return HeldProposal(case_id, surface, f"canonical label {label!r} is not an approved vocabulary "
                                                  f"entry of category {category!r}")
        slug = entry.slug
        proposed_nodes = tuple(str(n) for n in payload.get("node_ids") or ())
        proposed_variants = tuple(str(a) for a in resolution.get("aliases") or ())
        confidence = float(resolution.get("confidence") or 0.0)
        source = "reviewer_verdict"
    if not surface or not category or not slug:
        return HeldProposal(case_id, surface, "merge proposal lacks surface, category or target slug", category, slug)
    if category not in CATEGORIES:
        return HeldProposal(case_id, surface, f"unknown vocabulary category {category!r}", category, slug)
    live_nodes, live_variants = _nodes_for(rows, category, (surface, *proposed_variants))
    node_ids = proposed_nodes or live_nodes
    if not node_ids:
        return HeldProposal(case_id, surface, "no candidate node of this term exists in the live graph", category, slug)
    known = {str(n["node_id"]) for n in rows.nodes}
    missing = sorted(set(node_ids) - known)
    if missing:
        return HeldProposal(case_id, surface, f"proposed nodes are not in the live graph: {missing[:5]}", category,
                            slug)
    variants = tuple(sorted({surface.strip(), *live_variants, *(v.strip() for v in proposed_variants if v.strip())}))
    return AcceptedMerge(
        case_id, revision, term_key(category, fold_key(surface)), category, surface, variants, tuple(sorted(node_ids)),
        slug, f"reviewer_accepted:{source}", confidence,
        (f"review:{case.get('resolved_by') or 'reviewer'}", f"proposal:{source}"),
    )


def accepted_merges(cases: Sequence[Mapping[str, Any]], vocabulary: Vocabulary, rows: GraphRows,
                    ) -> tuple[list[AcceptedMerge], list[HeldProposal], int]:
    """Merges and held proposals of the accepted cases, plus the count of cases that were not merge proposals.

    Two accepted cases for the same term and target become one merge (their node ids and variants are joined);
    the same term with two different targets is held: a reviewer must decide, the adapter never picks.
    """
    merges: dict[str, AcceptedMerge] = {}
    held: list[HeldProposal] = []
    skipped = 0
    for case in cases[:MAX_ACCEPTED_CASES]:
        got = accepted_merge(case, vocabulary, rows)
        if got is None:
            skipped += 1
        elif isinstance(got, HeldProposal):
            held.append(got)
        elif got.term_key in merges and merges[got.term_key].target_slug != got.target_slug:
            other = merges.pop(got.term_key)
            why = f"two accepted targets for one term: {other.target_slug!r} and {got.target_slug!r}"
            held.append(HeldProposal(other.case_id, other.surface, why, other.category, other.target_slug))
            held.append(HeldProposal(got.case_id, got.surface, why, got.category, got.target_slug))
        elif got.term_key in merges:
            first = merges[got.term_key]
            merges[got.term_key] = AcceptedMerge(
                first.case_id, first.case_revision, first.term_key, first.category, first.surface,
                tuple(sorted({*first.variants, *got.variants})), tuple(sorted({*first.node_ids, *got.node_ids})),
                first.target_slug, first.method, max(first.confidence, got.confidence),
                tuple(dict.fromkeys((*first.votes, *got.votes, f"also:{got.case_id}"))),
            )
        else:
            merges[got.term_key] = got
    return [merges[k] for k in sorted(merges)], held, skipped


def merge_proposal_content(*, surface: str, category: str, target_slug: str, node_ids: Sequence[str],
                           variants: Sequence[str] = (), confidence: float = 1.0, method: str = "campaign",
                           ) -> dict[str, Any]:
    """The ``content`` of a canonicalization merge ``Proposal`` (what :func:`accepted_merge` reads back).

    Campaign and online outcomes use this to enter the review queue through the existing
    ``CurationProposalSink`` with ``kind = MERGE_PROPOSAL_KIND``; the sink never writes a graph.
    """
    return {
        "surface": surface, "category": category, "target_slug": target_slug,
        "node_ids": sorted(str(n) for n in node_ids), "variants": sorted(str(v) for v in variants),
        "confidence": round(float(confidence), 4), "method": method,
    }


# ---------------------------------------------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------------------------------------------


def build_plan(merges: Sequence[AcceptedMerge], rows: GraphRows, vocabulary: Vocabulary, *, scope: Sequence[str],
               target: str, created_at: datetime) -> PlanDraft:
    """A scoped plan over the live rows and vocabulary; unsafe or unknown targets are held, never dropped.

    The plan binds ``rows.revision()`` (source) and ``vocabulary.revision``; a changed graph or vocabulary makes
    the plan stale and every apply route refuses it (``stale_source`` / ``stale_vocabulary``).
    """
    decisions = [
        Decision(m.term_key, m.category, m.node_ids, m.target_slug, m.method, m.confidence, m.votes)
        for m in merges
    ]
    evidence = {
        m.term_key: TermEvidence(m.term_key, m.category, m.surface, fold_key(m.surface), m.variants, m.node_ids, (),
                                 0, 0, sum(rows.edges.get(n, 0) for n in m.node_ids))
        for m in merges
    }
    report_sha256 = sha256_json([m.as_dict() for m in merges])
    return plan_migration(decisions, evidence, vocabulary, scope=scope, target=target,
                          source_revision=rows.revision(), report_sha256=report_sha256, created_at=created_at)


def gate_is_open(gate: str) -> bool:
    """Whether :func:`~.gates.require_gate` would let ``gate`` through now (reported, never acted on here)."""
    try:
        _gates.require_gate(gate)
    except ApplyRefused:
        return False
    return True


def is_negative_control(surface: str, entry_names: Sequence[str]) -> bool:
    """Whether folding ``surface`` into an entry named by any of ``entry_names`` is a named negative control."""
    left = fold(surface).replace(" ", "")
    return any(frozenset((left, fold(n).replace(" ", ""))) in _NEGATIVE_PAIRS for n in entry_names)


# ---------------------------------------------------------------------------------------------------------------
# The target: merges as node patches through the existing Save port
# ---------------------------------------------------------------------------------------------------------------


class GraphEditPort(Protocol):
    """The journaled Save port (``TiledSaveBackend``): the effective graph and a before/after Save."""

    def effective_graph(self) -> dict[str, Any]:
        """Active snapshot plus in-flight journal entries: what every F2W reader sees."""
        ...

    def save(self, ctx: RequestContext, before: Mapping[str, Any], after: Mapping[str, Any]) -> object:
        """Journal and apply the difference between two graph documents (``graph.edit``; returns after the effect)."""
        ...


class EditPortMergeTarget:
    """``MergeTarget`` over a :class:`GraphEditPort`: one Save (``node_patch`` per node) per merge operation.

    ``source_revision`` is the revision of the candidate rows read from the port's effective graph, so a graph
    that moved since the plan was made refuses the apply (``stale_source``). ``merge`` only ever adds the
    :data:`CANONICAL_FIELDS` to the operation's nodes: nothing is deleted, renamed or re-linked.
    """

    def __init__(self, port: GraphEditPort, ctx: RequestContext, *, plan_id: str, vocabulary: Vocabulary,
                 category_map: Mapping[str, str] | None = None) -> None:
        """Bind the port, the acting context and the plan whose operations will be written."""
        self.port = port
        self.ctx = ctx
        self.plan_id = plan_id
        self.vocabulary = vocabulary
        self.category_map = category_map
        self.saved: list[object] = []

    def rows(self) -> GraphRows:
        """Candidate rows of the effective graph now."""
        return rows_from_graph(self.port.effective_graph(), category_map=self.category_map)

    def source_revision(self) -> str:
        """Revision of the live candidate rows."""
        return self.rows().revision()

    def merge(self, op: MergeOp) -> None:
        """Write the canonical identity onto every node of ``op`` through the Save port (one journaled Save)."""
        entry = self.vocabulary.get(op.target_slug)
        if entry is None:
            raise ApplyRefused("unknown_slug", f"vocabulary entry {op.target_slug!r} disappeared")
        before = self.port.effective_graph()
        after = copy.deepcopy(before)
        wanted = set(op.node_ids)
        patched: set[str] = set()
        for node in after.get(_node_key(after)) or []:
            if isinstance(node, Mapping) and str(node.get("id")) in wanted and isinstance(node, dict):
                node["canonical_slug"] = entry.slug
                node["canonical_name"] = entry.display_name
                node["canonical_category"] = entry.category
                node["canonical_vocabulary_revision"] = self.vocabulary.revision
                node["canonical_plan_id"] = self.plan_id
                node["canonical_method"] = op.method
                patched.add(str(node["id"]))
        missing = sorted(wanted - patched)
        if missing:
            raise ApplyRefused("node_missing", f"nodes of term {op.surface!r} are no longer in the graph: {missing[:5]}")
        self.saved.append(self.port.save(self.ctx, before, after))


# ---------------------------------------------------------------------------------------------------------------
# Report and service
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplyReport:
    """What one apply (dry or real) found, planned, held, applied and refused. ``model_calls`` is always 0.

    Attributes:
        dry_run: Whether anything was written (``False`` only after a real, gated apply).
        route: Apply route used (``apply_migration`` | ``apply_cleanup`` | ``apply_campaign``).
        gate / gate_open: The gate that route checks and whether it is open on this build.
        corpus / target: Scope and graph target.
        source_revision / vocabulary_revision: Revisions the plan binds to.
        vocabulary_approved: Whether the vocabulary used is an approved revision.
        plan_id / plan_sha256: The plan (``None`` when nothing could be planned).
        accepted_cases: Accepted cases read; ``not_merge_proposals`` of them were other case kinds.
        merges: Merges read from the accepted cases.
        held: Accepted proposals held before planning (reason each).
        planned: Term keys in the plan; ``plan_held`` the terms the planner held (safety / unknown target).
        applied / refused: Per the route's :class:`~.migration.ApplyResult`.
        unresolved_visible: Terms still unresolved after this apply (held + plan-held), kept visible.
        false_merges / false_merge_items: Planned or applied folds that are named negative controls or fail the
            safety check (reported separately; never applied).
        false_splits / false_split_items: Accepted proposals whose surface is an approved alias of the proposed
            target and that still did not get planned (a merge that should have happened).
        candidates_considered: Nodes read as candidate terms (the bounded read).
        model_calls: Always 0; the adapter never calls a model.
        validated_cases: Cases marked validated after a real apply.
        refusal: The typed refusal of a real apply (``code``, ``message``), or ``None``.
    """

    dry_run: bool
    route: str
    gate: str
    gate_open: bool
    corpus: str
    target: str
    source_revision: str
    vocabulary_revision: str
    vocabulary_approved: bool
    plan_id: str | None
    plan_sha256: str | None
    accepted_cases: int
    not_merge_proposals: int
    merges: tuple[AcceptedMerge, ...]
    held: tuple[HeldProposal, ...]
    planned: tuple[str, ...]
    plan_held: tuple[tuple[str, str], ...]
    applied: tuple[str, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()
    unresolved_visible: tuple[str, ...] = ()
    false_merges: int = 0
    false_merge_items: tuple[str, ...] = ()
    false_splits: int = 0
    false_split_items: tuple[str, ...] = ()
    candidates_considered: int = 0
    model_calls: int = 0
    validated_cases: tuple[str, ...] = ()
    refusal: tuple[str, str] | None = None
    version: str = ADAPTER_VERSION

    def as_dict(self) -> JsonObject:
        """Return the report as JSON-able data."""
        return {
            "version": self.version, "dry_run": self.dry_run, "route": self.route, "gate": self.gate,
            "gate_open": self.gate_open, "corpus": self.corpus, "target": self.target,
            "source_revision": self.source_revision, "vocabulary_revision": self.vocabulary_revision,
            "vocabulary_approved": self.vocabulary_approved, "plan_id": self.plan_id, "plan_sha256": self.plan_sha256,
            "accepted_cases": self.accepted_cases, "not_merge_proposals": self.not_merge_proposals,
            "merges": [m.as_dict() for m in self.merges], "held": [h.as_dict() for h in self.held],
            "planned": list(self.planned), "plan_held": [list(h) for h in self.plan_held],
            "applied": list(self.applied), "refused": [list(r) for r in self.refused],
            "unresolved_visible": list(self.unresolved_visible),
            "false_merges": self.false_merges, "false_merge_items": list(self.false_merge_items),
            "false_splits": self.false_splits, "false_split_items": list(self.false_split_items),
            "candidates_considered": self.candidates_considered, "model_calls": self.model_calls,
            "validated_cases": list(self.validated_cases),
            "refusal": None if self.refusal is None else {"code": self.refusal[0], "message": self.refusal[1]},
        }


class RefusedApply(ApplyRefused):
    """A real apply was refused; ``report`` is the dry-run report with ``refusal`` filled in."""

    def __init__(self, cause: ApplyRefused, report: ApplyReport) -> None:
        super().__init__(cause.code, str(cause).split(": ", 1)[-1])
        self.report = ApplyReport(**{**report.__dict__, "refusal": (cause.code, str(cause))})


class CaseSource(Protocol):
    """The part of ``CurationStore`` the adapter uses: accepted cases awaiting validation, and marking them."""

    def awaiting_validation(self, corpora: Sequence[str]) -> list[dict[str, Any]]:
        """Resolved cases whose candidate must go back through validation."""
        ...

    def mark_validated(self, case_id: str) -> None:
        """Clear the revalidation flag of one case."""
        ...


@dataclass
class MergeApplyService:
    """Accepted proposals of one corpus -> scoped plan -> gated apply route -> Save port.

    Args:
        cases: The curation store (or its case-source part).
        vocabulary: Returns the vocabulary to plan against; ``None`` when no governed vocabulary is configured
            (every call then raises ``UNSUPPORTED_CAPABILITY``). An approved revision is required for a real apply.
        port: Returns the graph edit port (``TiledSaveBackend`` on ``:local``); may raise a typed
            ``ContractError`` when no publication is active yet.
        target: The normalized graph target the plan binds to.
        clock: Current UTC time.
        category_map: Graph class -> vocabulary category (default :data:`DEFAULT_CATEGORY_MAP`).
    """

    cases: CaseSource
    vocabulary: Callable[[], Vocabulary] | None
    port: Callable[[], GraphEditPort]
    target: str
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    category_map: Mapping[str, str] | None = None
    #: Routes this service may use; a name outside it is ``INVALID_INPUT``.
    routes: Mapping[str, Callable[..., ApplyResult]] = field(default_factory=lambda: dict(APPLY_ROUTES))

    def _vocabulary(self) -> Vocabulary:
        if self.vocabulary is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "no governed vocabulary is configured on this deployment; nothing to plan against (H5)")
        return self.vocabulary()

    @staticmethod
    def _route(name: str) -> str:
        if name not in APPLY_ROUTES:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown apply route {name!r}; one of {sorted(APPLY_ROUTES)}")
        return name

    def plan(self, ctx: RequestContext, *, corpus: str, route: str = "apply_migration",
             ) -> tuple[PlanDraft, ApplyReport, GraphRows, Vocabulary]:
        """Read the accepted cases of ``corpus`` and plan them over the live graph; writes nothing.

        Raises:
            ContractError: ``DENIED`` (not a human, or the corpus is outside the caller's scope);
                ``UNSUPPORTED_CAPABILITY`` (no vocabulary or no active graph); ``INVALID_INPUT`` (route).
        """
        route = self._route(route)
        _human(ctx)
        _in_scope(ctx, corpus)
        vocabulary = self._vocabulary()
        port = self.port()
        rows = rows_from_graph(port.effective_graph(), category_map=self.category_map)
        accepted = self.cases.awaiting_validation([corpus])
        merges, held, skipped = accepted_merges(accepted, vocabulary, rows)
        draft = build_plan(merges, rows, vocabulary, scope=[corpus], target=self.target, created_at=self.clock()) \
            if merges else PlanDraft(None, ())
        report = self._report(route, corpus, rows, vocabulary, accepted, skipped, merges, held, draft)
        return draft, report, rows, vocabulary

    def apply_plan(self, ctx: RequestContext, draft: PlanDraft, report: ApplyReport, *, approval: ApprovalRecord | None,
                   vocabulary: Vocabulary, dry_run: bool = True, now: datetime | None = None) -> ApplyReport:
        """Run the report's route on the drafted plan. Dry run by default; a real apply is gated.

        A real apply first re-reads the live revisions and refuses a stale plan with the same typed codes the
        gate uses (``stale_source`` / ``stale_vocabulary``), then hands the plan, the approval and an
        :class:`EditPortMergeTarget` to the route, which runs :func:`~.migration.apply_scoped_plan` (gate,
        hash, approval, revisions, approved vocabulary, per-operation safety). Cases whose terms were applied
        are marked validated. The typed refusal of a real apply is returned in ``report.refusal`` AND raised.

        Raises:
            ApplyRefused: The typed refusal (``gate_closed`` on every build until the C4 rerun opens the gates).
            ContractError: ``DENIED``; ``UNSUPPORTED_CAPABILITY`` when no graph target is reachable.
        """
        _human(ctx)
        route = self._route(report.route)
        plan = draft.plan
        if plan is None:
            return report
        run = self.routes[route]
        if dry_run:
            result = run(plan, None, None, vocabulary, dry_run=True, now=now)
            return self._with_result(report, result, validated=())
        target = EditPortMergeTarget(self.port(), ctx, plan_id=plan.plan_id, vocabulary=vocabulary,
                                     category_map=self.category_map)
        try:
            live_source = target.source_revision()
            if plan.source_revision != live_source:
                raise ApplyRefused("stale_source", "the graph changed since the plan was made; plan again")
            if plan.vocabulary_revision != vocabulary.revision:
                raise ApplyRefused("stale_vocabulary", "the vocabulary changed since the plan was made; plan again")
            result = run(plan, approval, target, vocabulary, dry_run=False, now=now)
        except ApplyRefused as exc:
            raise RefusedApply(exc, report) from exc
        validated = self._validate_cases(report, result)
        return self._with_result(report, result, validated=validated)

    def apply_accepted(self, ctx: RequestContext, *, corpus: str, route: str = "apply_migration",
                       approval: ApprovalRecord | None = None, dry_run: bool = True, now: datetime | None = None,
                       ) -> ApplyReport:
        """Plan the accepted cases of ``corpus`` and run the route on the plan (see :meth:`plan`, :meth:`apply_plan`)."""
        draft, report, _rows, vocabulary = self.plan(ctx, corpus=corpus, route=route)
        return self.apply_plan(ctx, draft, report, approval=approval, vocabulary=vocabulary, dry_run=dry_run, now=now)

    # -- internals ------------------------------------------------------------------------------------------------

    def _validate_cases(self, report: ApplyReport, result: ApplyResult) -> tuple[str, ...]:
        done = set(result.applied)
        validated: list[str] = []
        for merge in report.merges:
            if merge.term_key in done:
                for case_id in (merge.case_id, *(v[5:] for v in merge.votes if v.startswith("also:"))):
                    self.cases.mark_validated(case_id)
                    validated.append(case_id)
        return tuple(validated)

    def _report(self, route: str, corpus: str, rows: GraphRows, vocabulary: Vocabulary,
                accepted: Sequence[Mapping[str, Any]], skipped: int, merges: Sequence[AcceptedMerge],
                held: Sequence[HeldProposal], draft: PlanDraft) -> ApplyReport:
        plan = draft.plan
        planned = tuple(o.term_key for o in plan.ops) if plan else ()
        false_merge_items, false_split_items = _false_counts(merges, held, draft, vocabulary)
        unresolved = tuple(sorted({h.surface for h in held} | {_surface_of(merges, k) for k, _ in draft.held}))
        gate = ROUTE_GATES[route]
        return ApplyReport(
            dry_run=True, route=route, gate=gate, gate_open=gate_is_open(gate),
            corpus=corpus, target=self.target, source_revision=rows.revision(),
            vocabulary_revision=vocabulary.revision, vocabulary_approved=vocabulary.approved,
            plan_id=plan.plan_id if plan else None, plan_sha256=plan.plan_sha256 if plan else None,
            accepted_cases=len(accepted), not_merge_proposals=skipped, merges=tuple(merges), held=tuple(held),
            planned=planned, plan_held=tuple(draft.held), unresolved_visible=unresolved,
            false_merges=len(false_merge_items), false_merge_items=false_merge_items,
            false_splits=len(false_split_items), false_split_items=false_split_items,
            candidates_considered=len(rows.nodes),
        )

    @staticmethod
    def _with_result(report: ApplyReport, result: ApplyResult, *, validated: tuple[str, ...]) -> ApplyReport:
        refused_terms = {k for k, _ in result.refused}
        unresolved = tuple(sorted({*report.unresolved_visible, *(_surface_of(report.merges, k) for k in refused_terms)}))
        return ApplyReport(
            **{**report.__dict__, "dry_run": result.dry_run, "applied": tuple(result.applied),
               "refused": tuple(result.refused), "unresolved_visible": unresolved, "validated_cases": validated},
        )


def _surface_of(merges: Sequence[AcceptedMerge], key: str) -> str:
    return next((m.surface for m in merges if m.term_key == key), key)


def _false_counts(merges: Sequence[AcceptedMerge], held: Sequence[HeldProposal], draft: PlanDraft,
                  vocabulary: Vocabulary) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(false merge items, false split items)``: negative-control or unsafe folds vs approved aliases left out.

    A false split is an accepted proposal, held before or during planning, whose surface the approved vocabulary
    itself maps to the proposed target: the merge should have happened and did not.
    """
    planned = {o.term_key for o in draft.plan.ops} if draft.plan else set()
    false_merges: list[str] = []
    false_splits: list[str] = []
    for h in held:
        if h.target_slug and (matched := vocabulary.match(h.surface, h.category or None)) is not None \
                and matched.slug == h.target_slug:
            false_splits.append(f"{h.surface} -> {h.target_slug}")
    for m in merges:
        entry = vocabulary.get(m.target_slug)
        names: tuple[str, ...] = (entry.display_name, *entry.aliases) if entry else ()
        unsafe = any(
            is_negative_control(v, names) or not any(check_merge(n, v, canonical_class=m.category).allowed for n in names)
            for v in m.variants
        )
        if unsafe:
            false_merges.append(f"{m.surface} -> {m.target_slug}")
        elif m.term_key not in planned:
            matched = vocabulary.match(m.surface, m.category)
            if matched is not None and matched.slug == m.target_slug:
                false_splits.append(f"{m.surface} -> {m.target_slug}")
    return tuple(false_merges), tuple(false_splits)


def _human(ctx: RequestContext) -> None:
    if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
        raise ContractError(ErrorCode.DENIED, "applying accepted proposals is done by a human, not a worker, delegate or model")


def _in_scope(ctx: RequestContext, corpus: str) -> None:
    if not corpus.strip():
        raise ContractError(ErrorCode.INVALID_INPUT, "a corpus is required (plans are scoped; there is no whole-graph apply)")
    if ctx.corpus_scope and corpus not in ctx.corpus_scope:
        raise ContractError(ErrorCode.DENIED, f"corpus {corpus!r} is outside the caller's scope")


def contract_error(exc: ApplyRefused) -> ContractError:
    """Map a typed apply refusal onto the API's ``ContractError`` (the HTTP body carries ``reason = exc.code``)."""
    code = {
        "gate_closed": ErrorCode.UNSUPPORTED_CAPABILITY,
        "vocabulary_not_approved": ErrorCode.UNSUPPORTED_CAPABILITY,
        "no_target": ErrorCode.UNSUPPORTED_CAPABILITY,
        "approval_missing": ErrorCode.STALE_APPROVAL,
        "approval_mismatch": ErrorCode.STALE_APPROVAL,
        "stale_source": ErrorCode.CONFLICT,
        "stale_vocabulary": ErrorCode.CONFLICT,
        "plan_tampered": ErrorCode.CONFLICT,
        "node_missing": ErrorCode.CONFLICT,
    }.get(exc.code, ErrorCode.INVALID_INPUT)
    return ContractError(code, str(exc), reason=exc.code)


__all__ = (
    "ADAPTER_VERSION", "APPLY_ROUTES", "CANONICAL_FIELDS", "DEFAULT_CATEGORY_MAP", "MAX_ACCEPTED_CASES",
    "MERGE_CASE_TYPES", "MERGE_PROPOSAL_KIND", "ROUTE_GATES", "AcceptedMerge", "ApplyReport", "CaseSource",
    "EditPortMergeTarget", "GraphEditPort", "HeldProposal", "MergeApplyService", "RefusedApply", "accepted_merge",
    "accepted_merges", "build_plan", "contract_error", "gate_is_open", "is_negative_control",
    "merge_proposal_content", "rows_from_graph",
)
