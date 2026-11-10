"""Corpus-scoped replacement import: stage, switch, retire, revive (T08C; editing plan sections 1-2, Q1-Q6).

One stable Tiled scope per corpus (owner decision Q1, revised): every F2W record of corpus ``rsoxs`` carries
``f2w_graph_id = "rsoxs"`` and the URI ``f2w:kg/rsoxs/node/<id>`` whatever version is live. The JSON side is
unchanged (manifests keep ``rsoxs_v4`` graph ids and file names); only the Tiled scope key changes. Which
version is live is named by a **release marker** entity per scope (Q5) and by F2W's publication pointer.

A version replacement is a reviewed difference, never a wipe (Q2):

* unchanged records stay exactly as they are (same Tiled UUIDs, so foreign links to them keep working);
* changed and removed records are **retired** (URI moved to ``.../retired/<node>/<tag>``, type ``f2w:Retired``),
  never deleted here (purge is T08D, with its own approval, Q3);
* new and changed records are **staged** out of sight first (``.../pending/<node>/<tag>``, type ``f2w:Staged``)
  and then switched in;
* a retired record whose content equals the incoming version is **revived** (URI and type back to final) so a
  rollback restores the same UUIDs.

Two phases (editing plan 2.0). **Stage** creates staged entities, the links that touch them and the staged
release marker; nothing visible changes. **Switch** retires old revisions, promotes staged ones, creates the
links between already-live endpoints, deletes stale owned links and swaps the marker. The switch is many
single writes (no transactions on 0.2.18), so the F2W readers pin themselves to the active receipt's ledger
while :class:`ReleaseState` says a switch is in flight (Q6, :class:`PinnedReader`); other readers may see the
short mixed window.

Invariants (editing plan 1.4): the plan lists every foreign link that touches a record it will retire (I5,
inside the plan hash, so the approval covers it); every record not owned by this plan is fingerprinted before
and after (I1/I2) and a change is **recorded in the receipt, never fatal** (Q14); the read probes of other
readers are recorded (I7); only ``f2w-literature`` records of this scope (and of the legacy per-version graph
ids being adopted) are ever written (I4/I8, via the owner-checked :class:`TiledMutationClient`).

Mutations go through this module and :mod:`.tiled_json` only; both use the same approval model
(``security.authorization.create_approval`` / ``verify_approval``) and the same lease, journal and receipt
mechanics (:mod:`.journal`, :mod:`.receipts`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, cast

from app.modules.contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from app.modules.contracts.context import RequestContext
from app.modules.security.authorization import DeploymentSettings, require, verify_approval

from .. import edit_journal as ej
from .. import identity_adapter as ida
from .. import literature_writer as lw
from ..backends.tiled_mutations import TiledMutationClient
from ..identity_adapter import IdentityLedger
from ..ports import ContractError, ErrorCode
from .journal import ImportJournal, Lease, operation_id
from .receipts import ReceiptArtifact, ReceiptExtras, ReceiptStore, make_artifact, receipt_valid_for
from .tiled_json import (
    Clock,
    FaultHook,
    ImportOutcome,
    ImportVerificationError,
    InjectedCrash,
    Source,
    SourceSnapshot,
    _canon,
    _check_client,
    _sha,
    _slug,
    _writer_errors,
    dangling_edges,
    load_source,
    target_for,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ADAPTER_VERSION",
    "BoundaryLink",
    "InjectedCrash",
    "LedgerHistory",
    "PinnedReader",
    "ReleaseState",
    "ReplacementPlan",
    "ScopeOp",
    "ScopeState",
    "abandon_plan",
    "apply_replacement",
    "compute_ops",
    "legacy_graph_ids",
    "plan_replacement",
    "read_probes",
    "read_scope_state",
    "verify_scope",
]

ADAPTER_VERSION = "f2w-tiled-corpus-import/1+tiled-0.2.18"
PHASES = ("stage", "switch")
#: Operation kinds by phase and the order they run in.
STAGE_KINDS = ("stage_node", "stage_link", "stage_marker")
SWITCH_KINDS = ("retire_node", "promote_node", "revive_node", "switch_link", "retire_link", "retire_marker",
                "promote_marker")
_SWITCH_GROUP = {"retire_node": 0, "promote_node": 0, "revive_node": 0, "switch_link": 1, "retire_link": 2,
                 "retire_marker": 3, "promote_marker": 4}
_NODE_RANK = {"retire_node": 0, "promote_node": 1, "revive_node": 1}
LEGACY_PREFIX = "legacy/"


# --------------------------------------------------------------------------- target state

def legacy_graph_ids(entities: Iterable[Mapping[str, Any]], scope: str) -> list[str]:
    """Return the per-version graph ids (``<scope>_v<N>``) that F2W literature records in ``entities`` carry.

    These are the T08B-era imports that keyed the Tiled graph by version; the one-time adoption plan retires
    them into the corpus scope (editing plan 5, T08C scope item 1).
    """
    pattern = re.compile("^" + re.escape(scope) + r"_v\d+$")
    found: set[str] = set()
    for row in entities:
        key = ida.literature_entity_key(row)  # live records only: retired legacy rows are already adopted
        if key is not None and pattern.match(key[0]):
            found.add(key[0])
    return sorted(found)


@dataclass
class ScopeState:
    """Complete scan of the target, classified for one corpus scope (plus adopted legacy graph ids)."""

    scope: str
    rows: dict[str, dict[str, Any]]
    link_rows: dict[str, dict[str, Any]]
    adopted: tuple[str, ...] = ()
    final: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)   # tag -> node -> row
    retired: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)   # tag -> node -> row
    release: dict[str, Any] | None = None
    active_links: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # edge id -> rows
    legacy_final: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # gid -> node -> row
    legacy_links: dict[str, list[dict[str, Any]]] = field(default_factory=dict)       # gid -> rows
    legacy_present: tuple[str, ...] = ()
    foreign: dict[str, str] = field(default_factory=dict)
    owned_ids: set[str] = field(default_factory=set)
    revision: str = "none"

    def pending_for(self, tag: str | None) -> dict[str, dict[str, Any]]:
        """Return the staged rows of plan ``tag`` by node id."""
        return self.pending.get(tag or "", {})

    def retired_for(self, tag: str | None) -> dict[str, dict[str, Any]]:
        """Return the rows retired by plan ``tag`` by node id."""
        return self.retired.get(tag or "", {})

    def retired_any(self, node: str) -> list[dict[str, Any]]:
        """Every retired revision of ``node`` across plan tags, oldest tag first by id (deterministic)."""
        rows = [rows[node] for rows in self.retired.values() if node in rows]
        return sorted(rows, key=lambda r: str(r["id"]))

    def owned_links_of(self, entity_id: str) -> list[dict[str, Any]]:
        """Links owned by this plan's scopes that touch ``entity_id``."""
        return [lk for lk in self.link_rows.values()
                if lk["id"] in self.owned_ids and entity_id in (lk.get("subjectId"), lk.get("objectId"))]

    def foreign_links_of(self, entity_id: str) -> list[dict[str, Any]]:
        """Links **not** owned by this plan that touch ``entity_id`` (FORGE, other scopes, other users)."""
        return sorted((lk for lk in self.link_rows.values()
                       if lk["id"] not in self.owned_ids and entity_id in (lk.get("subjectId"), lk.get("objectId"))),
                      key=lambda r: str(r["id"]))


def _tag_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def classify_scope(scope: str, entities: list[dict[str, Any]], links: list[dict[str, Any]],
                   adopted: Sequence[str] = ()) -> ScopeState:
    """Split scanned rows into this scope's records, adopted legacy records and everything else.

    Raises:
        ContractError: ``CONFLICT`` for two final entities of one node, two revisions of one node in one
            state and tag, two live release markers, or an owned record with an unrecognized URI.
    """
    st = ScopeState(scope, {e["id"]: e for e in entities}, {lk["id"]: lk for lk in links}, tuple(adopted))
    st.legacy_present = tuple(legacy_graph_ids(entities, scope))
    owned: list[Any] = []
    graphs = (scope, *adopted)
    for row in entities:
        kind, gid = None, scope
        for gid in graphs:
            try:
                kind = lw.classify_entity(row, gid)
            except ValueError as exc:
                raise ContractError(ErrorCode.CONFLICT, str(exc), retryable=False) from None
            if kind is not None:
                break
        if kind is None:
            st.foreign["e:" + row["id"]] = _sha(row)
            continue
        owned.append(("e", row))
        st.owned_ids.add(row["id"])
        node = row["properties"][ida.ENTITY_KEY]
        if gid != scope:
            if kind == "final":
                st.legacy_final.setdefault(gid, {})[node] = row
            continue  # legacy pending/retired/release rows are left exactly as they are
        if kind == lw.RELEASE:
            if st.release is not None:
                raise ContractError(ErrorCode.CONFLICT, f"two live release markers for scope {scope!r}",
                    retryable=False)
            st.release = row
        elif kind == "final":
            if node in st.final:
                raise ContractError(ErrorCode.CONFLICT,
                                    f"two Tiled entities claim ({scope}, {node}); no upsert in 0.2.18",
                                    retryable=False)
            st.final[node] = row
        else:
            slot = (st.pending if kind == lw.PENDING else st.retired).setdefault(_tag_of(row["uri"]), {})
            if node in slot:
                raise ContractError(ErrorCode.CONFLICT, f"two {kind} revisions of ({scope}, {node})",
                    retryable=False)
            slot[node] = row
    for row in links:
        key = ida.literature_link_key(row)
        if key is None or key[0] not in graphs:
            st.foreign["l:" + row["id"]] = _sha(row)
            continue
        owned.append(("l", row))
        st.owned_ids.add(row["id"])
        if key[0] == scope:
            st.active_links.setdefault(key[1], []).append(row)
        else:
            st.legacy_links.setdefault(key[0], []).append(row)
    body = sorted([k, _sha({f: r.get(f) for f in ("id", "uri", "name", "entityType", "properties", "subjectId",
                                                  "objectId", "predicate")})] for k, r in owned)
    st.revision = "none" if not body else "scan:" + _sha(body)
    return st


def read_scope_state(client: TiledMutationClient, scope: str, *, adopted: Sequence[str] = (),
                     double_read: bool = True) -> ScopeState:
    """Complete scan (twice, compared, when ``double_read``) classified for ``scope``.

    Raises:
        TruncatedGraphRead: A scan bound was reached (a partial scan never plans).
        ContractError: ``UNAVAILABLE`` (retryable), ``CONFLICT`` (changed during the read or inconsistent).
    """
    entities, links = client.scan_entities(), client.scan_links()
    if double_read:
        again_e, again_l = client.scan_entities(), client.scan_links()
        if _sha([entities, links]) != _sha([again_e, again_l]):
            raise ContractError(ErrorCode.CONFLICT, "the target changed while it was being read; retry")
    return classify_scope(scope, entities, links, adopted)


def read_probes(client: TiledMutationClient, st: ScopeState) -> dict[str, Any]:
    """I7 read probes: what other readers see, independent of this plan's records.

    * ``types``: for every entity type other readers could list (the legacy beamline lookup lists
      ``entities(entityType: "Sample")``), the ids ``entities(entityType:)`` returns minus the records this
      plan owns, hashed. Our staged/retired records must never appear there.
    * ``other_scopes``: live node count of every other F2W literature graph id on the target.
    """
    types = sorted({str(r.get("entityType") or "") for r in st.rows.values()}
                   - {lw.STAGED_TYPE, lw.RETIRED_TYPE, lw.RELEASE_TYPE, ""})
    probes: dict[str, Any] = {"types": {}, "other_scopes": {}}
    for entity_type in types:
        listed = client.scan_entities(entity_type=entity_type)
        ids = sorted(r["id"] for r in listed if r["id"] not in st.owned_ids)
        probes["types"][entity_type] = {"count": len(ids), "sha256": _sha(ids)}
    for row in st.rows.values():
        key = ida.literature_entity_key(row)
        if key is not None and key[0] != st.scope and key[0] not in st.adopted:
            probes["other_scopes"][key[0]] = probes["other_scopes"].get(key[0], 0) + 1
    return probes


# --------------------------------------------------------------------------- plan

@dataclass(frozen=True)
class ScopeOp:
    """One planned operation: ``kind``, ``key`` (node id, edge id or ``<edge>@<link>``), old row id, graph id."""

    kind: str
    key: str
    old_id: str | None = None
    graph: str | None = None

    @property
    def token(self) -> str:
        """Return ``"<kind>:<key>"``."""
        return f"{self.kind}:{self.key}"

    @property
    def phase(self) -> str:
        """Return ``stage`` or ``switch``."""
        return "stage" if self.kind in STAGE_KINDS else "switch"

    def as_list(self) -> list[str | None]:
        """Return the JSON form stored in the plan detail."""
        return [self.kind, self.key, self.old_id, self.graph]


@dataclass(frozen=True)
class BoundaryLink:
    """A foreign link that touches a record this plan retires (editing plan I5)."""

    link_id: str
    predicate: str
    direction: str          # "in" (foreign subject -> our record) | "out"
    node_id: str
    graph_id: str
    entity_id: str
    reason: str             # revised | removed | adopted
    other: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form stored in the plan detail."""
        return {"link_id": self.link_id, "predicate": self.predicate, "direction": self.direction,
                "node_id": self.node_id, "graph_id": self.graph_id, "entity_id": self.entity_id,
                "reason": self.reason, "other": dict(self.other)}


def _marker_properties(src: SourceSnapshot, scope: str, plan_sha256: str | None) -> dict[str, Any]:
    body = {"snapshot_id": src.manifest.snapshot_id, "graph_sha256": src.graph_sha256,
            "manifest_sha256": src.manifest_sha256, "data_commit": src.data_commit,
            "json_graph_id": src.graph_id, "plan_sha256": plan_sha256,
            "receipt_id": receipt_id_for(plan_sha256) if plan_sha256 else None}
    return ida.entity_properties(scope, lw.RELEASE_NODE, body)


def receipt_id_for(plan_sha256: str) -> str:
    """Return the receipt id of a plan (known before the run, so the release marker can carry it)."""
    return f"receipt-{_sha([plan_sha256, 'corpus'])[:20]}"


def _marker_is_current(row: Mapping[str, Any] | None, src: SourceSnapshot, plan_sha256: str | None) -> bool:
    if row is None:
        return False
    props = row.get("properties") or {}
    if props.get("graph_sha256") != src.graph_sha256 or props.get("snapshot_id") != src.manifest.snapshot_id:
        return False
    return plan_sha256 is None or props.get("plan_sha256") == plan_sha256


def compute_ops(src: SourceSnapshot, st: ScopeState, tag: str | None = None,
                plan_sha256: str | None = None) -> tuple[list[ScopeOp], dict[str, int], dict[str, str]]:
    """Diff the snapshot against the scanned scope.

    Returns the operations, the counts of unchanged records and the target UUID of every node whose Tiled
    identity is already known (kept, staged for ``tag``, or revivable). ``tag`` recognizes this plan's
    in-flight rows on a resume; ``plan_sha256`` recognizes this plan's promoted release marker.
    """
    ops: list[ScopeOp] = []
    counts = {"unchanged_entities": 0, "unchanged_links": 0, "revived_entities": 0}
    scope = st.scope
    target_id: dict[str, str] = {}
    kept: set[str] = set()
    pend = st.pending_for(tag)
    for node_id, node in src.nodes.items():
        spec: Any = lw.entity_spec(scope, node)  # Any: the name is reused for a LinkSpec below
        final = st.final.get(node_id)
        if final is not None and lw.entity_matches(final, spec):
            target_id[node_id] = final["id"]
            kept.add(node_id)
            counts["unchanged_entities"] += 1
            continue
        if final is not None:
            ops.append(ScopeOp("retire_node", node_id, final["id"], scope))
        staged = pend.get(node_id)
        if staged is not None:
            target_id[node_id] = staged["id"]
            ops.append(ScopeOp("promote_node", node_id, staged["id"], scope))
            continue
        revivable = [r for r in st.retired_any(node_id) if lw.entity_matches(r, spec)
                     or (r.get("entityType") == lw.RETIRED_TYPE and _same_content(r, spec))]
        if revivable:
            target_id[node_id] = revivable[0]["id"]
            counts["revived_entities"] += 1
            ops.append(ScopeOp("revive_node", node_id, revivable[0]["id"], scope))
            continue
        ops.append(ScopeOp("stage_node", node_id, final["id"] if final else None, scope))
        ops.append(ScopeOp("promote_node", node_id, None, scope))
    for node_id, row in st.final.items():
        if node_id not in src.nodes:
            ops.append(ScopeOp("retire_node", node_id, row["id"], scope))
    for gid, finals in st.legacy_final.items():
        for node_id, row in finals.items():
            ops.append(ScopeOp("retire_node", f"{LEGACY_PREFIX}{gid}/{node_id}", row["id"], gid))
    for gid, rows in st.legacy_links.items():
        ops.extend(ScopeOp("retire_link", f"{LEGACY_PREFIX}{gid}@{lk['id']}", lk["id"], gid) for lk in rows)
    expected: set[str] = set()
    for eid, edge in src.edges:
        subject, obj = edge.get("subject"), edge.get("object")
        if subject not in src.nodes or obj not in src.nodes:
            continue
        expected.add(eid)
        spec = lw.link_spec(scope, eid, edge)
        actives = st.active_links.get(eid, [])
        s_id, o_id = target_id.get(subject), target_id.get(obj)
        good = [lk for lk in actives if s_id and o_id and lw.link_matches(lk, spec, s_id, o_id)]
        if not good:
            kind = "switch_link" if subject in kept and obj in kept else "stage_link"
            ops.append(ScopeOp(kind, eid, None, scope))
        else:
            counts["unchanged_links"] += 1
        for lk in actives:
            if not good or lk is not good[0]:
                ops.append(ScopeOp("retire_link", f"{eid}@{lk['id']}", lk["id"], scope))
    for eid, rows in st.active_links.items():
        if eid not in expected:
            ops.extend(ScopeOp("retire_link", f"{eid}@{lk['id']}", lk["id"], scope) for lk in rows)
    # Release marker (Q5): swapped in the same switch; a noop plan leaves a current marker alone.
    marker_current = _marker_is_current(st.release, src, plan_sha256)
    if ops or not marker_current:
        if not marker_current:
            if st.release is not None:
                ops.append(ScopeOp("retire_marker", lw.RELEASE_NODE, st.release["id"], scope))
            if lw.RELEASE_NODE in pend:
                ops.append(ScopeOp("promote_marker", lw.RELEASE_NODE, pend[lw.RELEASE_NODE]["id"], scope))
            else:
                ops.append(ScopeOp("stage_marker", lw.RELEASE_NODE, None, scope))
                ops.append(ScopeOp("promote_marker", lw.RELEASE_NODE, None, scope))
    ops.sort(key=_op_order)
    return ops, counts, target_id


def _same_content(row: Mapping[str, Any], spec: lw.EntitySpec) -> bool:
    """A retired row (type ``f2w:Retired``) whose name and properties equal the desired record."""
    return (row.get("name") or "") == spec.name and lw.canonical(row.get("properties") or {}) == \
        lw.canonical(spec.properties)


def _op_order(op: ScopeOp) -> tuple[int, int, str, int]:
    if op.phase == "stage":
        return (0, STAGE_KINDS.index(op.kind), op.key, 0)
    return (1, _SWITCH_GROUP[op.kind], op.key, _NODE_RANK.get(op.kind, 0))


def boundary_links(src: SourceSnapshot, st: ScopeState, ops: Sequence[ScopeOp]) -> list[BoundaryLink]:
    """Every foreign link touching a record the plan retires (I5), sorted for a stable hash."""
    out: list[BoundaryLink] = []
    for op in ops:
        if op.kind != "retire_node" or op.old_id is None:
            continue
        if op.key.startswith(LEGACY_PREFIX):
            node_id, reason = op.key.split("/", 2)[2], "adopted"
        else:
            node_id, reason = op.key, ("revised" if op.key in src.nodes else "removed")
        for lk in st.foreign_links_of(op.old_id):
            incoming = lk.get("objectId") == op.old_id
            other_id = lk.get("subjectId") if incoming else lk.get("objectId")
            other = st.rows.get(cast(str, other_id)) or {}
            out.append(BoundaryLink(
                link_id=str(lk["id"]), predicate=str(lk.get("predicate") or ""),
                direction="in" if incoming else "out", node_id=node_id, graph_id=op.graph or st.scope,
                entity_id=op.old_id, reason=reason,
                other={"id": other_id, "uri": other.get("uri"), "name": other.get("name"),
                       "entityType": other.get("entityType")}))
    return sorted(out, key=lambda b: (b.node_id, b.link_id))


@dataclass(frozen=True)
class ReplacementPlan:
    """Immutable replacement plan: the contract :class:`ChangePlan` plus the detail its hash binds.

    ``detail`` carries the adapter version, scope, adopted legacy graph ids, every operation, the boundary-link
    report (I5), the dangling-stub report, counts and the I7 probe snapshot. Its hash is one of the
    ``affected_ids``, so approving ``change.plan_sha256`` approves exactly this detail, boundary links included.
    """

    change: ChangePlan
    detail: dict[str, Any]

    @property
    def ops(self) -> list[ScopeOp]:
        """Return the planned operations."""
        return [ScopeOp(*o) for o in self.detail["ops"]]

    @property
    def is_noop(self) -> bool:
        """Return whether the target already holds this version (no operations)."""
        return not self.detail["ops"]

    @property
    def tag(self) -> str:
        """Return the plan tag (first 16 hex digits of the plan hash) that marks its in-flight rows."""
        return self.change.plan_sha256[:16]

    @property
    def scope(self) -> str:
        """Return the corpus scope."""
        return cast(str, self.detail["scope"])

    @property
    def boundary(self) -> list[BoundaryLink]:
        """Return the boundary-link report."""
        return [BoundaryLink(**b) for b in self.detail["boundary_links"]]

    @property
    def is_safe(self) -> bool:
        """Whether the plan touches nothing anyone else depends on (no boundary links, no legacy adoption).

        T18E's ``:local`` auto-approval policy may apply only to a safe plan; anything else waits for review.
        """
        return not self.detail["boundary_links"] and not self.detail["adopted_graph_ids"]

    @property
    def summary(self) -> dict[str, Any]:
        """Dry-run report for the reviewer (editing plan 2.1 step 2)."""
        kinds: dict[str, int] = {}
        for op in self.ops:
            kinds[op.kind] = kinds.get(op.kind, 0) + 1
        return {"plan_sha256": self.change.plan_sha256, "target": self.change.target, "scope": self.scope,
                "snapshot_id": self.change.snapshot_id, "operations": kinds, "unchanged": self.detail["counts"],
                "boundary_links": list(self.detail["boundary_links"]),
                "adopted_graph_ids": list(self.detail["adopted_graph_ids"]), "dangling": self.detail["dangling"],
                "foreign_records_fingerprinted": self.detail["foreign_count"],
                "other_scopes": dict(self.detail["probes"]["other_scopes"]), "noop": self.is_noop,
                "safe": self.is_safe, "requires_approval": not self.is_noop}

    def verify_integrity(self) -> None:
        """Recompute the plan from its detail; any edit to detail, counts or boundary report is refused."""
        c = self.change
        try:
            expect = build_change_plan(self.detail, snapshot_id=c.snapshot_id, input_sha256=c.input_sha256,
                                       target=c.target, scope=c.corpus_scope, base_revision=c.base_revision,
                                       validation_sha256=c.validation_report_sha256)
        except (TypeError, KeyError, ValueError):
            raise ContractError(ErrorCode.INVALID_INPUT, "replacement plan detail is malformed") from None
        if expect != c:
            raise ContractError(ErrorCode.STALE_APPROVAL, "replacement plan was modified after it was computed")

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable form :meth:`from_json` reads."""
        return {"change": self.change.model_dump(mode="json"), "detail": self.detail}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ReplacementPlan:
        """Rebuild a plan; call :meth:`verify_integrity` before trusting it."""
        return cls(ChangePlan.model_validate(data["change"]), json.loads(_canon(data["detail"])))


def _plan_detail_ops(ops: Sequence[ScopeOp]) -> list[list[Any]]:
    return [[o.kind, o.key, o.old_id, o.graph] for o in ops]


def build_change_plan(detail: Mapping[str, Any], *, snapshot_id: str, input_sha256: str, target: str,
                      scope: Sequence[str], base_revision: str, validation_sha256: str) -> ChangePlan:
    """Deterministic :class:`ChangePlan` for a replacement ``detail``; ``plan_sha256`` covers every field.

    ``creates`` counts staged entities, ``links`` the links created in either phase, ``supersessions`` every
    retirement (entities, links, marker). ``affected_ids`` starts with the detail hash, which binds the
    operations, the boundary-link report and the adopted graph ids to the approval.
    """
    ops = [ScopeOp(*o) for o in detail["ops"]]
    body: dict[str, Any] = {
        "snapshot_id": snapshot_id, "input_sha256": input_sha256, "target": target,
        "corpus_scope": list(scope), "base_revision": base_revision,
        "creates": sum(1 for o in ops if o.kind == "stage_node"),
        "links": sum(1 for o in ops if o.kind in ("stage_link", "switch_link")),
        "supersessions": sum(1 for o in ops if o.kind in ("retire_node", "retire_link", "retire_marker")),
        "affected_ids": [f"plan-detail:{_sha(detail)}"] + [o.token for o in ops],
        "validation_report_sha256": validation_sha256,
    }
    digest = _sha(body)
    return ChangePlan(plan_id=f"plan-{digest[:16]}", plan_sha256=digest, **body)


def plan_replacement(source: Source, *, client: TiledMutationClient, settings: DeploymentSettings,
                     supported_schema_fingerprints: Collection[str], namespace: str | None = None,
                     access_blob: Mapping[str, Any] | None = None, adopt_legacy: bool = False,
                     edit_journal: ej.EditJournal | None = None,
                     resolutions: Sequence[ej.ConflictResolution] = (),
                     lineage: Sequence[str] = (), state_dir: Path | None = None) -> ReplacementPlan:
    """Dry-run: preflight the snapshot, read the whole target and return the exact replacement plan.

    No writes. The scope key is the snapshot's corpus (Q1). Legacy per-version imports of the corpus
    (``<corpus>_v<N>`` graph ids) are refused unless ``adopt_legacy`` is set, in which case the plan retires
    them into the scope once (their records keep existing under retired URIs; purge is T08D). With
    ``state_dir`` the scope's :class:`ReleaseState` is bound into the plan too, so re-importing the same
    snapshot after a corpus removal and purge is a new plan with its own receipt.

    Raises:
        ContractError: preflight refusal (``INVALID_INPUT``); ``UNAVAILABLE``; ``WRITER_UNAUTHORIZED``;
            ``CONFLICT`` for an unreconciled acknowledged edit, an inconsistent target, or legacy per-version
            records present without ``adopt_legacy``.
        TruncatedGraphRead: The target could not be read completely.
    """
    src = load_source(source, supported_schema_fingerprints=supported_schema_fingerprints)
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    scope = src.manifest.corpus
    ej.check_import(edit_journal, target=target, scope=(scope,), lineage=[*src.lineage, *lineage],
                    resolutions=resolutions)
    with _writer_errors():
        entities, links = client.scan_entities(), client.scan_links()
        again = (client.scan_entities(), client.scan_links())
        if _sha([entities, links]) != _sha(list(again)):
            raise ContractError(ErrorCode.CONFLICT, "the target changed while it was being read; retry")
        legacy = legacy_graph_ids(entities, scope)
        if legacy and not adopt_legacy:
            raise ContractError(ErrorCode.CONFLICT,
                                f"the target holds per-version imports of corpus {scope!r} ({legacy}); plan with "
                                "adopt_legacy=True to retire them into the corpus scope (one-time adoption)",
                                retryable=False)
        state = classify_scope(scope, entities, links, legacy if adopt_legacy else ())
        probes = read_probes(client, state)
    ops, counts, _ids = compute_ops(src, state)
    boundary = boundary_links(src, state, ops)
    release: dict[str, Any] | None = None
    if state_dir is not None:
        rec = ReleaseState(scope_dir(state_dir, target, scope) / "release.json").read()
        release = {k: rec.get(k) for k in ("pointer", "live_receipt_id", "live_plan_sha256", "retired_by_receipt_id")}
    detail = {
        "adapter_version": ADAPTER_VERSION, "manifest_sha256": src.manifest_sha256, "scope": scope,
        "release_state": release,
        "json_graph_id": src.graph_id, "owner": ida.LITERATURE_OWNER, "access_blob": dict(access_blob or {}),
        "adopted_graph_ids": list(state.adopted), "resolutions_sha256": ej.resolutions_sha256(resolutions),
        "data_commit": src.data_commit,
        "projection_policy": "all-node-fields+edge-fields/owner-marked/no-snippet-filter(O-D1)",
        "ops": _plan_detail_ops(ops), "counts": counts, "dangling": dangling_edges(src),
        "boundary_links": [b.as_dict() for b in boundary], "foreign_count": len(state.foreign),
        "probes": probes,
        "leftover_revisions": sum(len(v) for v in (*state.pending.values(), *state.retired.values())),
    }
    change = build_change_plan(detail, snapshot_id=src.manifest.snapshot_id, input_sha256=src.graph_sha256,
                               target=target, scope=(scope,), base_revision=state.revision,
                               validation_sha256=src.manifest.validation.report_sha256)
    return ReplacementPlan(change, detail)


# --------------------------------------------------------------------------- release state and ledger history

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


class ReleaseState:
    """F2W's own record of what is live for one ``(target, scope)`` and whether a switch is in flight.

    Stored as ``release.json`` under the scope's state directory. ``pointer`` is ``live`` or ``none`` (after a
    corpus removal, T08D). While ``switching`` names a plan tag, F2W readers must pin themselves to the
    ledger of ``live_receipt_id`` (Q6) and a Save is refused (Q8, T18D).
    """

    def __init__(self, path: Path) -> None:
        """Bind to ``path``; nothing is read until a method needs it."""
        self.path = Path(path)

    def read(self) -> dict[str, Any]:
        """Return the stored record (an empty ``none`` record before the first switch)."""
        try:
            return cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:  # no switch recorded yet
            return {"pointer": "none", "live_receipt_id": None, "live_snapshot_id": None,
                    "live_plan_sha256": None, "switching": None}

    def _write(self, record: Mapping[str, Any]) -> None:
        _atomic_write(self.path, json.dumps(record, indent=2, sort_keys=True))

    @property
    def switching(self) -> str | None:
        """Return the tag of the plan whose switch is in flight, or ``None``."""
        return cast(str | None, self.read().get("switching"))

    def mark_switching(self, tag: str) -> None:
        """Record that plan ``tag`` has started its switch (idempotent)."""
        rec = self.read()
        if rec.get("switching") not in (None, tag):
            raise ContractError(ErrorCode.CONFLICT, f"another plan ({rec['switching']}) is switching this scope",
                retryable=False)
        self._write({**rec, "switching": tag})

    def set_live(self, *, receipt_id: str, snapshot_id: str, plan_sha256: str, at: datetime) -> None:
        """Name the live version and clear the switching flag."""
        self._write({"pointer": "live", "live_receipt_id": receipt_id, "live_snapshot_id": snapshot_id,
                     "live_plan_sha256": plan_sha256, "switching": None, "updated_at": at.isoformat()})

    def set_none(self, *, receipt_id: str, at: datetime) -> None:
        """Point the scope at nothing (corpus removal, T08D) and clear the switching flag."""
        self._write({"pointer": "none", "live_receipt_id": None, "live_snapshot_id": None, "live_plan_sha256": None,
                     "switching": None, "retired_by_receipt_id": receipt_id, "updated_at": at.isoformat()})

    def clear_switching(self) -> None:
        """Clear the switching flag without changing the live version (after an abandon)."""
        self._write({**self.read(), "switching": None})


class LedgerHistory:
    """Append-only per-scope history of every identity ledger a receipt ever recorded (editing plan 2.1.3).

    Each record names the receipt, the snapshot, the live ``(node -> UUID)`` bindings and the UUIDs retired by
    that run, so any Tiled UUID ever issued for the scope (live, retired or later purged) maps back to its
    ``(scope, node_id)`` and the receipts that used it.
    """

    def __init__(self, path: Path) -> None:
        """Bind to the JSONL ``path``."""
        self.path = Path(path)

    def append(self, *, receipt_id: str, snapshot_id: str, plan_sha256: str, ledger: IdentityLedger,
               retired: Mapping[str, str], at: datetime) -> None:
        """Durably append one record (a repeated append for the same receipt is a no-op)."""
        if any(r.get("receipt_id") == receipt_id for r in self.records()):
            return
        record = {"at": at.isoformat(), "receipt_id": receipt_id, "snapshot_id": snapshot_id,
                  "plan_sha256": plan_sha256, "live": ledger.to_json()["entities"],
                  "retired": sorted([n, u] for n, u in retired.items())}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[dict[str, Any]]:
        """All records, oldest first (a torn last line is ignored)."""
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                logger.debug("corrupt journal line skipped: %s", type(exc).__name__)
                continue
        return out

    def ledger_for(self, receipt_id: str) -> IdentityLedger | None:
        """Rebuild the live ledger a receipt recorded."""
        for rec in self.records():
            if rec.get("receipt_id") == receipt_id:
                return IdentityLedger.from_json({"version": 1, "entities": rec["live"]})
        return None

    def lookup(self, tiled_id: str) -> list[dict[str, Any]]:
        """Every ``{node_id, receipt_id, snapshot_id, state}`` a UUID appears in (``live`` or ``retired``)."""
        hits = []
        for rec in self.records():
            for _scope, node, uuid in rec["live"]:
                if uuid == tiled_id:
                    hits.append({"node_id": node, "receipt_id": rec["receipt_id"],
                                 "snapshot_id": rec["snapshot_id"], "state": "live"})
            for node, uuid in rec["retired"]:
                if uuid == tiled_id:
                    hits.append({"node_id": node, "receipt_id": rec["receipt_id"],
                                 "snapshot_id": rec["snapshot_id"], "state": "retired"})
        return hits


def scope_dir(state_dir: Path, target: str, scope: str) -> Path:
    """Return the per-``(target, scope)`` state directory (plans, lease, release state, ledger history)."""
    return Path(state_dir) / _slug(target) / _slug(scope)


#: File name of the per-corpus edit journal under :func:`scope_dir` (Save writes it, the import-time conflict
#: block and ``f2w-kg-snapshot --reconcile-edits`` read it).
JOURNAL_FILENAME = "edits.jsonl"


def journal_path(state_dir: Path, target: str, scope: str) -> Path:
    """The per-corpus edit journal file of ``scope`` on ``target``: ``<state_dir>/<target>/<scope>/edits.jsonl``."""
    return scope_dir(state_dir, target, scope) / JOURNAL_FILENAME


# --------------------------------------------------------------------------- pinned reader (Q6)

class PinnedReader:
    """F2W node reads pinned to one receipt's ledger, so a switch in flight is never visible (Q6).

    ``entity(id)`` returns a record by UUID whatever its URI or type, so the nodes of the pinned version stay
    readable while they are being retired; the real category is taken from the stored properties. Edges of
    the pinned version come from the active snapshot JSON (the F2W reader has the bytes), not from Tiled, so
    the deletion of stale links during step 4 of the switch is never observed either.
    """

    def __init__(self, client: TiledMutationClient, scope: str, ledger: IdentityLedger) -> None:
        """Pin ``scope`` reads to ``ledger`` (the active receipt's ``(node -> UUID)`` bindings)."""
        self.client = client
        self.scope = scope
        self.ledger = ledger

    def _record(self, node_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
        props = row.get("properties") or {}
        if props.get(ida.GRAPH_KEY) != self.scope or props.get(ida.ENTITY_KEY) != node_id \
                or props.get(ida.OWNER_KEY) != ida.LITERATURE_OWNER:
            raise ContractError(ErrorCode.CONFLICT, f"Tiled entity {row.get('id')} no longer carries identity "
                                                    f"({self.scope}, {node_id})", retryable=False)
        data = ida.strip_reserved(props)
        return {"graph_id": self.scope, "id": node_id, "name": str(row.get("name") or ""),
                "category": str(data.get("category") or data.get("entityType") or ""), "backend": "tiled",
                "tiled_id": row.get("id"), "pinned": True, "data": data}

    def get_entity(self, node_id: str) -> dict[str, Any] | None:
        """Return the pinned node, or ``None`` when the pinned version does not contain it."""
        tiled_id = self.ledger.tiled_id(self.scope, node_id)
        if tiled_id is None:
            return None
        row = self.client.entity(tiled_id)
        if row is None:
            raise ContractError(ErrorCode.CONFLICT, f"pinned entity {tiled_id} vanished from Tiled",
                retryable=False)
        return self._record(node_id, row)

    def entities(self) -> list[dict[str, Any]]:
        """Return every node of the pinned version (one ``entity(id)`` read per node)."""
        out: list[dict[str, Any]] = []
        for _graph, node_id, tiled_id in sorted(g for g in self.ledger.to_json()["entities"] if g[0] == self.scope):
            row = self.client.entity(tiled_id)
            if row is None:
                raise ContractError(ErrorCode.CONFLICT, f"pinned entity {tiled_id} vanished from Tiled",
                    retryable=False)
            out.append(self._record(node_id, row))
        return out



# --------------------------------------------------------------------------- apply

def verify_scope(src: SourceSnapshot, st: ScopeState, tag: str, plan_sha256: str) -> list[str]:
    """Compare the readback of the scope to the snapshot; return mismatch descriptions (empty when exact).

    Checks every node and edge by content, that nothing of this plan is still staged, that no adopted legacy
    record is still live, and that the live release marker names this plan. Foreign records are compared
    separately (:func:`foreign_changes`, Q14).
    """
    bad: list[str] = []
    scope = st.scope
    for node_id, node in src.nodes.items():
        row = st.final.get(node_id)
        if row is None:
            bad.append(f"missing entity {node_id}")
        elif not lw.entity_matches(row, lw.entity_spec(scope, node)):
            bad.append(f"entity {node_id} differs from the snapshot")
    bad.extend(f"extra entity {n}" for n in st.final if n not in src.nodes)
    if st.pending_for(tag):
        bad.append(f"{len(st.pending_for(tag))} staged record(s) of this plan still in flight")
    for gid, finals in st.legacy_final.items():
        if finals:
            bad.append(f"{len(finals)} live record(s) of adopted graph {gid} remain")
    for gid, rows in st.legacy_links.items():
        if rows:
            bad.append(f"{len(rows)} link(s) of adopted graph {gid} remain")
    seen: set[str] = set()
    for eid, edge in src.edges:
        if edge.get("subject") not in src.nodes or edge.get("object") not in src.nodes:
            continue
        seen.add(eid)
        s, o = st.final.get(edge["subject"]), st.final.get(edge["object"])
        rows = st.active_links.get(eid, [])
        spec = lw.link_spec(scope, eid, edge)
        if len(rows) != 1:
            bad.append(f"edge {eid}: {len(rows)} active links")
        elif not (s and o and lw.link_matches(rows[0], spec, s["id"], o["id"])):
            bad.append(f"edge {eid} differs from the snapshot")
    bad.extend(f"extra link {eid}" for eid in st.active_links if eid not in seen)
    if not _marker_is_current(st.release, src, plan_sha256):
        bad.append("the live release marker does not name this plan")
    elif st.release is not None and st.release.get("entityType") != lw.RELEASE_TYPE:
        bad.append("the live release marker has the wrong type")
    return bad[:50]


def foreign_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Keys of foreign records that changed, vanished or appeared between two scans (recorded, Q14)."""
    out = [f"changed:{k}" if k in after else f"vanished:{k}" for k, d in before.items() if after.get(k) != d]
    out.extend(f"appeared:{k}" for k in after if k not in before)
    return sorted(out)


def _refusals(ctx: RequestContext, plan: ReplacementPlan, settings: DeploymentSettings,
              client: TiledMutationClient) -> None:
    require(ctx, "graph.publish")
    if ctx.profile is not settings.profile or ctx.mode is not settings.mode:
        raise ContractError(ErrorCode.DENIED, "context belongs to another deployment profile")
    if ctx.corpus_scope and not set(plan.change.corpus_scope) <= ctx.corpus_scope:
        raise ContractError(ErrorCode.DENIED, "plan scope is outside the request scope")
    plan.verify_integrity()
    _check_client(client, settings, plan.change.target)


def apply_replacement(ctx: RequestContext, plan: ReplacementPlan, approval: ApprovalRecord | None, *,
                      source: Source, client: TiledMutationClient, settings: DeploymentSettings,
                      supported_schema_fingerprints: Collection[str], state_dir: Path, clock: Clock,
                      lease_seconds: float = 120.0, fault: FaultHook | None = None) -> ImportOutcome:
    """Apply an approved replacement plan in two phases, verify by readback, write the receipt.

    Refusals come first and write nothing: capability and scope, plan integrity, client/target binding,
    approval (``verify_approval``), source bytes, then the base revision (or, on a resume, "nothing changed
    outside this plan"). A zero-operation plan whose target already holds a verified receipt for the same
    revision returns ``noop``.

    Fault hook points (tests inject crashes): ``stage`` / ``stage_effect`` per staged record, ``after_stage``,
    ``verify_staged``, ``switch`` / ``switch_effect`` per switch write, ``before_verify``, ``before_receipt``,
    ``before_history``. A crash at any of them resumes without duplicates.

    Raises:
        ContractError: ``DENIED``, ``STALE_APPROVAL``, ``CONFLICT``, ``UNAVAILABLE``, ``WRITER_UNAUTHORIZED``,
            or :class:`ImportVerificationError` (owned readback differs; no receipt).
    """
    fault = fault or (lambda _stage, _i: None)
    _refusals(ctx, plan, settings, client)
    change = plan.change
    store = ReceiptStore(Path(state_dir) / "receipts")
    scope = plan.scope
    adopted = tuple(plan.detail["adopted_graph_ids"])
    if plan.is_noop:
        with _writer_errors():
            state = read_scope_state(client, scope, adopted=adopted)
        for art in store.find(snapshot_sha256=change.input_sha256, adapter_version=ADAPTER_VERSION, graph_id=scope):
            if receipt_valid_for(art, target=change.target, revision=state.revision):
                return ImportOutcome("noop", art, None)
    if approval is None:
        raise ContractError(ErrorCode.DENIED, "an ApprovalRecord authorizing this plan is required")
    verify_approval(approval, change, settings, at=clock())
    src = load_source(source, supported_schema_fingerprints=supported_schema_fingerprints)
    if src.graph_sha256 != change.input_sha256 or src.manifest_sha256 != plan.detail["manifest_sha256"] \
            or src.manifest.corpus != scope:
        raise ContractError(ErrorCode.CONFLICT, "the source snapshot no longer matches the plan", retryable=False)
    root = scope_dir(state_dir, change.target, scope)
    run_dir = root / "plans" / plan.tag
    journal = ImportJournal(run_dir / "journal.jsonl", clock)
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    try:
        with _writer_errors():
            outcome = _run(plan, approval, src, client, store, journal, lease, root, run_dir, clock, fault)
    except Exception as exc:  # a crash (BaseException) deliberately leaves the lease to expire
        if journal.started():
            journal.append("run_failed", error=type(exc).__name__,
                           code=getattr(getattr(exc, "code", None), "value", None))
        lease.release()
        raise
    lease.release()
    return outcome


def _run(plan: ReplacementPlan, approval: ApprovalRecord, src: SourceSnapshot, client: TiledMutationClient,
         store: ReceiptStore, journal: ImportJournal, lease: Lease, root: Path, run_dir: Path, clock: Clock,
         fault: FaultHook) -> ImportOutcome:
    change, detail = plan.change, plan.detail
    scope, access, tag = plan.scope, detail["access_blob"], plan.tag
    adopted = tuple(detail["adopted_graph_ids"])
    release_state = ReleaseState(root / "release.json")
    resumed = journal.started()
    state = read_scope_state(client, scope, adopted=adopted)
    for art in store.find(plan_sha256=change.plan_sha256):
        if receipt_valid_for(art, target=change.target, revision=state.revision):
            _finish_release(art, src, state, root, run_dir, release_state, tag, clock())
            journal.append("completed", receipt_id=art.receipt.receipt_id, resulting_revision=state.revision,
                           resumed=True)
            return ImportOutcome("already_applied", art, None, resumed=True)
    ops, _counts, ids = compute_ops(src, state, tag, change.plan_sha256)
    planned = {o.token for o in plan.ops}
    if resumed:
        stray = [o.token for o in ops if o.token not in planned]
        if stray:
            raise ContractError(ErrorCode.STALE_APPROVAL,
                                f"the target changed outside this plan since it started: {stray[:3]}")
    elif state.revision != change.base_revision:
        raise ContractError(ErrorCode.STALE_APPROVAL, "the target revision is not the plan's base revision")
    elif [b.as_dict() for b in boundary_links(src, state, ops)] != detail["boundary_links"]:
        # The base revision hashes owned rows only; a foreign link that appeared on a record about to be
        # retired changes the boundary report the approval covered (I5), so the plan is stale.
        raise ContractError(ErrorCode.STALE_APPROVAL,
                            "foreign links on records to be retired changed since the plan was approved; re-plan")
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_file = run_dir / "plan.json"
    if not plan_file.exists():
        plan_file.write_text(json.dumps(plan.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    foreign_file, probes_file = run_dir / "foreign.json", run_dir / "probes_before.json"
    if foreign_file.exists():
        foreign_before = json.loads(foreign_file.read_text(encoding="utf-8"))
        probes_before = json.loads(probes_file.read_text(encoding="utf-8"))
    else:
        foreign_before, probes_before = dict(state.foreign), read_probes(client, state)
        foreign_file.write_text(json.dumps(foreign_before, sort_keys=True), encoding="utf-8")
        probes_file.write_text(json.dumps(probes_before, sort_keys=True), encoding="utf-8")
    already = len(planned) - len({o.token for o in ops}) if resumed else 0
    journal.append("run_started" if not resumed else "run_resumed", plan_sha256=change.plan_sha256,
                   base_revision=change.base_revision, already_applied=already, executed_as=approval.executed_as,
                   requested_by=approval.requested_by, approved_by=approval.approved_by, scope=scope)
    done = journal.done_ids()
    edges = dict(src.edges)

    def step(phase: str, i: int, op: ScopeOp) -> str:
        oid = operation_id(change.plan_sha256, phase, op.kind, op.key)
        fault(phase, i)
        lease.check()  # fencing: immediately before the effect
        journal.append("intent", op_id=oid, kind=op.kind, key=op.key, replay=oid in done)
        return oid

    def finish(oid: str, op: ScopeOp, **extra: object) -> None:
        journal.append("done", op_id=oid, kind=op.kind, key=op.key, **extra)

    def link_spec_for(key: str) -> lw.LinkSpec:
        return lw.link_spec(scope, key, edges[key])

    # ---- phase 1: stage (nothing visible changes) -------------------------------------------------
    stage_ops = [o for o in ops if o.phase == "stage"]
    for i, op in enumerate(stage_ops):
        oid = step("stage", i, op)
        if op.kind == "stage_node":
            spec = lw.entity_spec(scope, src.nodes[op.key])
            ids[op.key] = client.create_entity(entity_type=lw.STAGED_TYPE, name=spec.name,
                                               uri=lw.state_uri(scope, op.key, lw.PENDING, tag),
                                               properties=spec.properties, access_blob=access)
            fault("stage_effect", i)
            finish(oid, op, tiled_id=ids[op.key])
        elif op.kind == "stage_link":
            ls = link_spec_for(op.key)
            lid = client.create_link(subject_id=ids[ls.subject], predicate=ls.predicate, object_id=ids[ls.object],
                                     properties=ls.properties, access_blob=access)
            fault("stage_effect", i)
            finish(oid, op, tiled_id=lid)
        else:  # stage_marker
            mid = client.create_entity(entity_type=lw.STAGED_TYPE, name=f"{scope} release",
                                       uri=lw.state_uri(scope, lw.RELEASE_NODE, lw.PENDING, tag),
                                       properties=_marker_properties(src, scope, change.plan_sha256),
                                       access_blob=access)
            ids[lw.RELEASE_NODE] = mid
            fault("stage_effect", i)
            finish(oid, op, tiled_id=mid)
    fault("after_stage", 0)

    # ---- verify staged ------------------------------------------------------------------------------
    staged_state = read_scope_state(client, scope, adopted=adopted, double_read=False)
    fault("verify_staged", 0)
    bad = _verify_staged(src, staged_state, tag, plan, ids)
    if bad:
        journal.append("verify_staged_failed", mismatches=bad[:10])
        raise ImportVerificationError(bad)

    # ---- phase 2: switch (short run of single-record updates; F2W readers are pinned) -----------
    switch_ops = [o for o in ops if o.phase == "switch"]
    if switch_ops:
        release_state.mark_switching(tag)
        journal.append("switch_started", plan_tag=tag)
    for i, op in enumerate(switch_ops):
        oid = step("switch", i, op)
        if op.kind == "retire_node":
            row, gid = state.rows[cast(str, op.old_id)], op.graph or scope
            node = op.key.split("/", 2)[2] if op.key.startswith(LEGACY_PREFIX) else op.key
            client.set_entity_state(row, gid, uri=lw.state_uri(gid, node, lw.RETIRED, tag),
                                    entity_type=lw.RETIRED_TYPE, access_blob=access)
        elif op.kind in ("promote_node", "revive_node"):
            spec = lw.entity_spec(scope, src.nodes[op.key])
            new_id = op.old_id or ids[op.key]
            client.set_entity_state({"id": new_id, "properties": spec.properties}, scope, uri=spec.uri,
                                    entity_type=spec.entity_type, access_blob=access)
            ids[op.key] = new_id
        elif op.kind == "switch_link":
            ls = link_spec_for(op.key)
            lid = client.create_link(subject_id=ids[ls.subject], predicate=ls.predicate, object_id=ids[ls.object],
                                     properties=ls.properties, access_blob=access)
            fault("switch_effect", i)
            finish(oid, op, tiled_id=lid)
            continue
        elif op.kind == "retire_link":
            client.delete_link(state.link_rows[cast(str, op.old_id)], op.graph or scope)
        elif op.kind == "retire_marker":
            row = state.rows[cast(str, op.old_id)]
            client.set_entity_state(row, scope, uri=lw.state_uri(scope, lw.RELEASE_NODE, lw.RETIRED, tag),
                                    entity_type=lw.RETIRED_TYPE, access_blob=access)
        else:  # promote_marker
            mid = op.old_id or ids[lw.RELEASE_NODE]
            client.set_entity_state({"id": mid, "properties": _marker_properties(src, scope, change.plan_sha256)},
                                    scope, uri=lw.release_uri(scope), entity_type=lw.RELEASE_TYPE,
                                    access_blob=access)
            ids[lw.RELEASE_NODE] = mid
        fault("switch_effect", i)
        finish(oid, op)
    fault("before_verify", 0)

    # ---- verify final, receipt, ledger history, release state --------------------------------------
    final = read_scope_state(client, scope, adopted=adopted)
    mismatches = verify_scope(src, final, tag, change.plan_sha256)
    if mismatches:
        journal.append("verify_failed", mismatches=mismatches[:10])
        raise ImportVerificationError(mismatches)
    changed_foreign = foreign_changes(foreign_before, final.foreign)
    probes_after = read_probes(client, final)
    probe_report = _probe_report(probes_before, probes_after)
    if changed_foreign:
        journal.append("foreign_changed", keys=changed_foreign[:20])
    ledger = IdentityLedger()
    for node_id, row in final.final.items():
        ledger.bind(scope, node_id, row["id"])
    fault("before_receipt", 0)
    receipt_id = receipt_id_for(change.plan_sha256)
    counts = {
        "staged_entities": sum(1 for o in plan.ops if o.kind == "stage_node"),
        "promoted_entities": sum(1 for o in plan.ops if o.kind == "promote_node"),
        "revived_entities": sum(1 for o in plan.ops if o.kind == "revive_node"),
        "retired_entities": sum(1 for o in plan.ops if o.kind == "retire_node"),
        "created_links": sum(1 for o in plan.ops if o.kind in ("stage_link", "switch_link")),
        "deleted_links": sum(1 for o in plan.ops if o.kind == "retire_link"),
        "unchanged_entities": detail["counts"]["unchanged_entities"],
        "unchanged_links": detail["counts"]["unchanged_links"],
        "boundary_links": len(detail["boundary_links"]),
        "foreign_changes": len(changed_foreign),
        "dangling_unimported": len(detail["dangling"]),
        "reconciled_on_replay": max([r.get("already_applied", 0) for r in journal.records()
                                     if r.get("event") in ("run_resumed", "run_started")] or [0]),
    }
    now = clock()
    receipt = ImportReceipt(
        receipt_id=receipt_id, plan_id=change.plan_id, plan_sha256=change.plan_sha256,
        approval_id=approval.approval_id, snapshot_sha256=src.graph_sha256, target=change.target,
        executed_as=approval.executed_as, adapter_version=ADAPTER_VERSION, id_mapping_sha256=ledger.sha256(),
        outcomes=counts, verified=True, completed_at=now)
    extras = ReceiptExtras(
        manifest_sha256=src.manifest_sha256, snapshot_id=src.manifest.snapshot_id, graph_id=scope,
        corpus_scope=list(change.corpus_scope), requested_by=approval.requested_by, approved_by=approval.approved_by,
        profile=approval.profile.value, authorization_mode=approval.mode.value, base_revision=change.base_revision,
        resulting_revision=final.revision, data_commit=src.data_commit,
        operation_counts={"entities": change.creates, "links": change.links, "supersessions": change.supersessions},
        dangling_unimported=len(detail["dangling"]), kind="replacement", adopted_graph_ids=list(adopted),
        boundary_links=len(detail["boundary_links"]), foreign_changes=changed_foreign[:200],
        read_probes=probe_report, release_marker_id=final.release["id"] if final.release else None)
    artifact = make_artifact(receipt, extras)
    path = store.write(artifact)
    fault("before_history", 0)
    _finish_release(artifact, src, final, root, run_dir, release_state, tag, now)
    journal.append("completed", receipt_id=receipt_id, resulting_revision=final.revision)
    return ImportOutcome("applied", artifact, path, resumed=resumed)


def _retired_by(st: ScopeState, tag: str) -> dict[str, str]:
    """``node -> UUID`` of every row (scope and adopted legacy graphs) retired by plan ``tag``."""
    out = {n: r["id"] for n, r in st.retired_for(tag).items()}
    for gid in st.adopted:
        for row in st.rows.values():
            props = row.get("properties") or {}
            uri = str(row.get("uri") or "")
            if props.get(ida.GRAPH_KEY) == gid and uri.endswith("/" + tag) and f"/{lw.RETIRED}/" in uri:
                out[f"{gid}/{props.get(ida.ENTITY_KEY)}"] = row["id"]
    return out


def _finish_release(artifact: ReceiptArtifact, src: SourceSnapshot, st: ScopeState, root: Path, run_dir: Path,
                    release_state: ReleaseState, tag: str, at: datetime) -> None:
    """Idempotent post-receipt steps: ledger file, ledger history record, release state (also on resume)."""
    ledger = IdentityLedger()
    for node_id, row in st.final.items():
        ledger.bind(st.scope, node_id, row["id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ledger.json").write_text(json.dumps(ledger.to_json(), sort_keys=True), encoding="utf-8")
    receipt = artifact.receipt
    LedgerHistory(root / "ledger_history.jsonl").append(receipt_id=receipt.receipt_id,
                                                        snapshot_id=src.manifest.snapshot_id,
                                                        plan_sha256=receipt.plan_sha256, ledger=ledger,
                                                        retired=_retired_by(st, tag), at=at)
    release_state.set_live(receipt_id=receipt.receipt_id, snapshot_id=src.manifest.snapshot_id,
                           plan_sha256=receipt.plan_sha256, at=at)


def _verify_staged(src: SourceSnapshot, st: ScopeState, tag: str, plan: ReplacementPlan,
                   ids: Mapping[str, str]) -> list[str]:
    """Read back every record the plan staged (type ``f2w:Staged``, content equal to the snapshot)."""
    bad: list[str] = []
    pend = st.pending_for(tag)
    for op in plan.ops:
        if op.kind == "stage_node":
            row = pend.get(op.key)
            if row is None:
                if op.key in st.final and lw.entity_matches(st.final[op.key], lw.entity_spec(st.scope,
                                                                                          src.nodes[op.key])):
                    continue  # already promoted by an earlier attempt
                bad.append(f"staged entity {op.key} is missing")
            elif row.get("entityType") != lw.STAGED_TYPE or \
                    lw.canonical(row.get("properties") or {}) != lw.canonical(
                        lw.entity_spec(st.scope, src.nodes[op.key]).properties):
                bad.append(f"staged entity {op.key} differs from the snapshot")
        elif op.kind == "stage_marker":
            row = pend.get(lw.RELEASE_NODE)
            if row is None and not _marker_is_current(st.release, src, plan.change.plan_sha256):
                bad.append("the staged release marker is missing")
    return bad[:50]


def _probe_report(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    empty = {"count": 0, "sha256": _sha([])}
    types: dict[str, Any] = {}
    for name in sorted(set(before.get("types", {})) | set(after.get("types", {}))):
        b, a = before.get("types", {}).get(name, empty), after.get("types", {}).get(name, empty)
        types[name] = {"before": b, "after": a, "equal": b == a}
    scopes: dict[str, Any] = {}
    for name in sorted(set(before.get("other_scopes", {})) | set(after.get("other_scopes", {}))):
        b, a = before.get("other_scopes", {}).get(name), after.get("other_scopes", {}).get(name)
        scopes[name] = {"before": b, "after": a, "equal": b == a}
    return {"types": types, "other_scopes": scopes,
            "equal": all(v["equal"] for v in (*types.values(), *scopes.values()))}


# --------------------------------------------------------------------------- abandon

def abandon_plan(ctx: RequestContext, plan: ReplacementPlan, *, client: TiledMutationClient,
                 settings: DeploymentSettings, state_dir: Path, clock: Clock,
                 lease_seconds: float = 120.0) -> dict[str, Any]:
    """Delete this plan's own staged records (never anything else) before its switch has started.

    A plan whose switch has begun cannot be abandoned: it is resumed with :func:`apply_replacement`. Each
    staged entity's links are read immediately before the delete (I3, T20C P1); an entity with a foreign link
    is left in place (type ``f2w:Staged``, invisible) and reported.

    Raises:
        ContractError: ``DENIED``; ``CONFLICT`` when the switch has started or the lease is held.
    """
    _refusals(ctx, plan, settings, client)
    scope, tag = plan.scope, plan.tag
    root = scope_dir(state_dir, plan.change.target, scope)
    journal = ImportJournal(root / "plans" / tag / "journal.jsonl", clock)
    if any(r.get("event") == "switch_started" for r in journal.records()):
        raise ContractError(ErrorCode.CONFLICT, "the switch of this plan has started; resume it instead of abandoning",
            retryable=False)
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    report: dict[str, Any] = {"deleted_entities": 0, "deleted_links": 0, "skipped": []}
    try:
        with _writer_errors():
            state = read_scope_state(client, scope, adopted=tuple(plan.detail["adopted_graph_ids"]))
            journal.append("abandon_started", plan_tag=tag)
            for node, row in sorted(state.pending_for(tag).items()):
                lease.check()
                links = client.links_of(row["id"])
                foreign = [lk for lk in links if (ida.literature_link_key(lk) or ("", ""))[0] != scope]
                if foreign:
                    report["skipped"].append({"node_id": node, "entity_id": row["id"],
                                              "foreign_links": [lk["id"] for lk in foreign]})
                    journal.append("abandon_skipped", key=node, foreign_links=[lk["id"] for lk in foreign])
                    continue
                for lk in links:
                    client.delete_link(lk, scope)
                    report["deleted_links"] += 1
                if client.links_of(row["id"]):
                    report["skipped"].append({"node_id": node, "entity_id": row["id"], "foreign_links": ["late"]})
                    continue
                client.delete_entity(row, scope)
                report["deleted_entities"] += 1
                journal.append("abandon_deleted", key=node, tiled_id=row["id"])
            journal.append("abandoned", **{k: v for k, v in report.items() if k != "skipped"},
                           skipped=len(report["skipped"]))
    finally:
        lease.release()
    return report
