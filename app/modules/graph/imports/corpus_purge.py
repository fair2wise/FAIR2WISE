"""Guarded purge and corpus removal (T08D; editing plan 2.6, Q3, invariant I3).

**Purge** physically deletes retired F2W records of one corpus scope. It is a separate plan with its own
approval (Q3: never part of a replacement), it keeps the previous version by default (the records retired
by the live release's plan stay revivable), and it never deletes a record that has a link F2W does not own:
Tiled 0.2.18 cascades ``deleteEntity`` into every attached link of every owner (T20C P1/P2), so each
candidate's links are read immediately before the delete, its owned links are deleted first, the links are
re-read, and the entity goes only when nothing is attached. A record with a foreign link is skipped and
reported in the receipt, never deleted.

**Retire-all** removes a whole corpus from view (editing plan 2.6): every live record and link of the scope
(and of any legacy per-version graph of the corpus) is retired, the release marker is retired, F2W's release
state points at ``none`` and the publication pointer is cleared through ``pointer_to_none``. The plan can only
be computed with the corpus name typed in (``confirm_scope``), so a wrong scope cannot be approved by accident.
Open edits block it unless each carries a reviewed ``discarded`` resolution. Undo before a purge is a
replacement plan from the committed JSON (revive); after a purge, the same re-import recreates the records.

Both plans bind their detail into the plan hash and go through ``security.authorization.create_approval`` /
``verify_approval`` like every other mutation; the committed JSON is never read for writing and never touched.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
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
from .corpus_scope import (
    BoundaryLink,
    LedgerHistory,
    ReleaseState,
    ScopeOp,
    ScopeState,
    foreign_changes,
    legacy_graph_ids,
    read_scope_state,
    scope_dir,
)
from .journal import ImportJournal, Lease, operation_id
from .receipts import ReceiptArtifact, ReceiptExtras, ReceiptStore, make_artifact, receipt_valid_for
from .tiled_json import (
    Clock,
    FaultHook,
    ImportOutcome,
    ImportVerificationError,
    _canon,
    _check_client,
    _sha,
    _writer_errors,
    target_for,
)

__all__ = [
    "ADAPTER_VERSION",
    "PurgeCandidate",
    "PurgePlan",
    "RetireAllPlan",
    "apply_purge",
    "apply_retire_all",
    "plan_purge",
    "plan_retire_all",
    "retired_rows",
]

ADAPTER_VERSION = "f2w-tiled-corpus-purge/1+tiled-0.2.18"
_NO_INPUT = _sha("no snapshot: operational plan")


# --------------------------------------------------------------------------- retired records

def retired_rows(entities: Sequence[Mapping[str, Any]], scope: str) -> list[tuple[dict[str, Any], str, str, str]]:
    """Every retired F2W record of ``scope`` or of its legacy per-version graphs: ``(row, graph, node, tag)``."""
    graphs = {scope, *_legacy_any(entities, scope)}
    out = []
    for row in entities:
        props = row.get("properties")
        if not isinstance(props, Mapping) or props.get(ida.OWNER_KEY) != ida.LITERATURE_OWNER:
            continue
        gid = props.get(ida.GRAPH_KEY)
        if gid not in graphs:
            continue
        try:
            kind = lw.classify_entity(row, cast(str, gid))
        except ValueError as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc), retryable=False) from None
        if kind == lw.RETIRED:
            uri = str(row.get("uri") or "")
            out.append((dict(row), cast(str, gid), str(props.get(ida.ENTITY_KEY)), uri.rsplit("/", 1)[-1]))
    return sorted(out, key=lambda t: t[0]["id"])


def _legacy_any(entities: Sequence[Mapping[str, Any]], scope: str) -> set[str]:
    import re

    pattern = re.compile("^" + re.escape(scope) + r"_v\d+$")
    found = set()
    for row in entities:
        props = row.get("properties")
        if isinstance(props, Mapping) and props.get(ida.OWNER_KEY) == ida.LITERATURE_OWNER:
            gid = props.get(ida.GRAPH_KEY)
            if isinstance(gid, str) and pattern.match(gid):
                found.add(gid)
    return found


def _owner_graph(link: Mapping[str, Any]) -> str | None:
    key = ida.literature_link_key(link)
    return key[0] if key is not None else None


# --------------------------------------------------------------------------- purge plan

@dataclass(frozen=True)
class PurgeCandidate:
    """One retired record considered for deletion and the decision made at plan time."""

    entity_id: str
    node_id: str
    graph_id: str
    tag: str
    action: str                       # delete | skip_foreign_link
    owned_links: list[str]
    foreign_links: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form stored in the plan detail."""
        return {"entity_id": self.entity_id, "node_id": self.node_id, "graph_id": self.graph_id, "tag": self.tag,
                "action": self.action, "owned_links": list(self.owned_links),
                "foreign_links": [dict(f) for f in self.foreign_links]}


def _operational_plan(detail: Mapping[str, Any], *, kind: str, scope: str, target: str, base_revision: str,
                      supersessions: int, affected: Sequence[str]) -> ChangePlan:
    body: dict[str, Any] = {
        "snapshot_id": f"{kind}:{scope}", "input_sha256": _sha(detail), "target": target,
        "corpus_scope": [scope], "base_revision": base_revision, "creates": 0, "links": 0,
        "supersessions": supersessions, "affected_ids": [f"{kind}-detail:{_sha(detail)}", *affected],
        "validation_report_sha256": _NO_INPUT,
    }
    digest = _sha(body)
    return ChangePlan(plan_id=f"plan-{digest[:16]}", plan_sha256=digest, **body)


@dataclass(frozen=True)
class PurgePlan:
    """Immutable purge plan: its own :class:`ChangePlan` (own approval, Q3) plus the candidate list it binds."""

    change: ChangePlan
    detail: dict[str, Any]

    @property
    def scope(self) -> str:
        """Return the corpus scope."""
        return cast(str, self.detail["scope"])

    @property
    def tag(self) -> str:
        """Return the plan tag."""
        return self.change.plan_sha256[:16]

    @property
    def candidates(self) -> list[PurgeCandidate]:
        """Return the candidates with their plan-time decision."""
        return [PurgeCandidate(**c) for c in self.detail["candidates"]]

    @property
    def is_noop(self) -> bool:
        """Whether nothing would be deleted."""
        return not any(c["action"] == "delete" for c in self.detail["candidates"])

    @property
    def summary(self) -> dict[str, Any]:
        """Report for the approver: what is deleted, what is skipped and why, what is kept."""
        cands = self.candidates
        return {"plan_sha256": self.change.plan_sha256, "target": self.change.target, "scope": self.scope,
                "delete": [c.entity_id for c in cands if c.action == "delete"],
                "skip_foreign_link": [{"entity_id": c.entity_id, "node_id": c.node_id,
                                       "foreign_links": c.foreign_links} for c in cands if c.action != "delete"],
                "kept_previous_version": self.detail["kept_previous"], "live_plan_tag": self.detail["live_plan_tag"],
                "include_previous": self.detail["include_previous"], "noop": self.is_noop}

    def verify_integrity(self) -> None:
        """Recompute the plan from its detail; any edit to the candidate list is refused."""
        c = self.change
        try:
            expect = _operational_plan(self.detail, kind="purge", scope=self.scope, target=c.target,
                                       base_revision=c.base_revision, supersessions=c.supersessions,
                                       affected=[f"purge:{x['entity_id']}" for x in self.detail["candidates"]
                                                 if x["action"] == "delete"])
        except (TypeError, KeyError, ValueError):
            raise ContractError(ErrorCode.INVALID_INPUT, "purge plan detail is malformed") from None
        if expect != c:
            raise ContractError(ErrorCode.STALE_APPROVAL, "purge plan was modified after it was computed")

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable form."""
        return {"change": self.change.model_dump(mode="json"), "detail": self.detail}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PurgePlan:
        """Rebuild a plan; call :meth:`verify_integrity` before trusting it."""
        return cls(ChangePlan.model_validate(data["change"]), json.loads(_canon(data["detail"])))


def _link_brief(lk: Mapping[str, Any], entity_id: str, rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    incoming = lk.get("objectId") == entity_id
    other_id = lk.get("subjectId") if incoming else lk.get("objectId")
    other = rows.get(cast(str, other_id)) or {}
    return {"link_id": lk["id"], "predicate": lk.get("predicate"), "direction": "in" if incoming else "out",
            "other": {"id": other_id, "uri": other.get("uri"), "name": other.get("name"),
                      "entityType": other.get("entityType")}}


def plan_purge(*, client: TiledMutationClient, settings: DeploymentSettings, scope: str, state_dir: Path,
               namespace: str | None = None, include_previous: bool = False) -> PurgePlan:
    """Read-only: list the retired records of ``scope`` and decide, per record, delete or skip.

    By default the records retired by the live release's own plan (the previous version) are kept so a
    rollback stays a revive (Q3 default). ``include_previous`` adds them (needed after a retire-all).

    Raises:
        ContractError: ``UNAVAILABLE``; ``WRITER_UNAUTHORIZED``; ``CONFLICT`` (inconsistent target).
    """
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    live = ReleaseState(scope_dir(state_dir, target, scope) / "release.json").read()
    live_tag = (live.get("live_plan_sha256") or "")[:16] or None
    with _writer_errors():
        state = read_scope_state(client, scope, adopted=())
    rows = list(state.rows.values())
    graphs = {scope, *_legacy_any(rows, scope)}
    candidates: list[PurgeCandidate] = []
    kept = 0
    for row, gid, node, tag in retired_rows(rows, scope):
        if tag == live_tag and not include_previous:
            kept += 1
            continue
        links = [lk for lk in state.link_rows.values() if row["id"] in (lk.get("subjectId"), lk.get("objectId"))]
        foreign = [_link_brief(lk, row["id"], state.rows) for lk in links if _owner_graph(lk) not in graphs]
        owned = sorted(lk["id"] for lk in links if _owner_graph(lk) in graphs)
        candidates.append(PurgeCandidate(row["id"], node, gid, tag, "skip_foreign_link" if foreign else "delete",
                                         owned, sorted(foreign, key=lambda f: str(f["link_id"]))))
    detail = {"adapter_version": ADAPTER_VERSION, "kind": "purge", "scope": scope,
              "candidates": [c.as_dict() for c in candidates], "kept_previous": kept, "live_plan_tag": live_tag,
              "include_previous": include_previous, "foreign_count": len(state.foreign)}
    change = _operational_plan(detail, kind="purge", scope=scope, target=target, base_revision=state.revision,
                               supersessions=sum(1 for c in candidates if c.action == "delete"),
                               affected=[f"purge:{c.entity_id}" for c in candidates if c.action == "delete"])
    return PurgePlan(change, detail)


def _refuse(ctx: RequestContext, change: ChangePlan, settings: DeploymentSettings, client: TiledMutationClient,
            approval: ApprovalRecord | None, clock: Clock) -> ApprovalRecord:
    require(ctx, "graph.publish")
    if ctx.profile is not settings.profile or ctx.mode is not settings.mode:
        raise ContractError(ErrorCode.DENIED, "context belongs to another deployment profile")
    if ctx.corpus_scope and not set(change.corpus_scope) <= ctx.corpus_scope:
        raise ContractError(ErrorCode.DENIED, "plan scope is outside the request scope")
    _check_client(client, settings, change.target)
    if approval is None:
        raise ContractError(ErrorCode.DENIED, "an ApprovalRecord authorizing this plan is required")
    return verify_approval(approval, change, settings, at=clock())


def apply_purge(ctx: RequestContext, plan: PurgePlan, approval: ApprovalRecord | None, *,
                client: TiledMutationClient, settings: DeploymentSettings, state_dir: Path, clock: Clock,
                lease_seconds: float = 120.0, fault: FaultHook | None = None) -> ImportOutcome:
    """Apply an approved purge: per record, read links, owned links first, re-read, then delete.

    Idempotent and resumable: a candidate that is already gone counts as done. A candidate that gained a
    foreign link since planning is skipped and reported (I3 holds at the moment of deletion, not at plan
    time). Foreign records are fingerprinted before and after: a changed record is recorded (Q14); a
    **vanished** foreign record fails verification, because a purge is the one operation that could cause
    it (the cascade, T20C P1) and nothing may hide that.

    Raises:
        ContractError: ``DENIED``, ``STALE_APPROVAL``, ``CONFLICT``, ``UNAVAILABLE``, ``WRITER_UNAUTHORIZED``,
            :class:`ImportVerificationError`.
    """
    fault = fault or (lambda _stage, _i: None)
    plan.verify_integrity()
    approval = _refuse(ctx, plan.change, settings, client, approval, clock)
    change, scope = plan.change, plan.scope
    store = ReceiptStore(Path(state_dir) / "receipts")
    root = scope_dir(state_dir, change.target, scope)
    run_dir = root / "purges" / plan.tag
    journal = ImportJournal(run_dir / "journal.jsonl", clock)
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    try:
        with _writer_errors():
            outcome = _run_purge(plan, approval, client, store, journal, lease, root, run_dir, clock, fault)
    except Exception as exc:
        if journal.started():
            journal.append("run_failed", error=type(exc).__name__,
                           code=getattr(getattr(exc, "code", None), "value", None))
        lease.release()
        raise
    lease.release()
    return outcome


def _run_purge(plan: PurgePlan, approval: ApprovalRecord, client: TiledMutationClient, store: ReceiptStore,
               journal: ImportJournal, lease: Lease, root: Path, run_dir: Path, clock: Clock,
               fault: FaultHook) -> ImportOutcome:
    change, scope = plan.change, plan.scope
    resumed = journal.started()
    state = read_scope_state(client, scope, adopted=())
    for art in store.find(plan_sha256=change.plan_sha256):
        if receipt_valid_for(art, target=change.target, revision=state.revision):
            return ImportOutcome("already_applied", art, None, resumed=True)
    graphs = {scope, *_legacy_any(list(state.rows.values()), scope)}
    candidate_ids = {c.entity_id for c in plan.candidates}
    if not resumed:
        if state.revision != change.base_revision:
            raise ContractError(ErrorCode.STALE_APPROVAL, "the target revision is not the plan's base revision")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(plan.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    foreign_file = run_dir / "foreign.json"
    if foreign_file.exists():
        foreign_before = json.loads(foreign_file.read_text(encoding="utf-8"))
    else:
        # Everything that is not a candidate or one of its owned links is "foreign" to this purge.
        owned_link_ids = {lid for c in plan.candidates for lid in c.owned_links}
        foreign_before = {**{f"e:{r['id']}": _sha(r) for r in state.rows.values() if r["id"] not in candidate_ids},
                          **{f"l:{lk['id']}": _sha(lk) for lk in state.link_rows.values()
                             if lk["id"] not in owned_link_ids}}
        foreign_file.write_text(json.dumps(foreign_before, sort_keys=True), encoding="utf-8")
    journal.append("run_started" if not resumed else "run_resumed", plan_sha256=change.plan_sha256,
                   executed_as=approval.executed_as, requested_by=approval.requested_by,
                   approved_by=approval.approved_by, scope=scope)
    done = journal.done_ids()
    deleted_entities = deleted_links = 0
    skipped: list[dict[str, Any]] = []
    for i, cand in enumerate(plan.candidates):
        if cand.action != "delete":
            skipped.append({"entity_id": cand.entity_id, "node_id": cand.node_id, "reason": "foreign_link_at_plan",
                            "foreign_links": [f["link_id"] for f in cand.foreign_links]})
            continue
        oid = operation_id(change.plan_sha256, "purge", "delete_entity", cand.entity_id)
        fault("purge", i)
        lease.check()
        journal.append("intent", op_id=oid, kind="delete_entity", key=cand.entity_id, replay=oid in done)
        row = client.entity(cand.entity_id)
        if row is None:  # already purged by an earlier attempt
            journal.append("done", op_id=oid, kind="delete_entity", key=cand.entity_id, replay=True)
            continue
        if lw.classify_entity(row, cand.graph_id) != lw.RETIRED:
            raise ContractError(ErrorCode.STALE_APPROVAL,
                                f"candidate {cand.entity_id} is no longer a retired record of {cand.graph_id}")
        links = client.links_of(cand.entity_id)                      # I3: read immediately before the delete
        foreign = [lk for lk in links if _owner_graph(lk) not in graphs]
        if foreign:
            skipped.append({"entity_id": cand.entity_id, "node_id": cand.node_id, "reason": "foreign_link_at_apply",
                            "foreign_links": [lk["id"] for lk in foreign]})
            journal.append("skipped", op_id=oid, key=cand.entity_id, foreign_links=[lk["id"] for lk in foreign])
            continue
        for lk in links:                                               # owned links first
            client.delete_link(lk, cast(str, _owner_graph(lk)))
            deleted_links += 1
            fault("purge_link_effect", i)
        if client.links_of(cand.entity_id):                            # re-read before the entity goes
            skipped.append({"entity_id": cand.entity_id, "node_id": cand.node_id, "reason": "link_appeared",
                            "foreign_links": []})
            journal.append("skipped", op_id=oid, key=cand.entity_id, foreign_links=["late"])
            continue
        client.delete_entity(row, cand.graph_id)
        deleted_entities += 1
        fault("purge_effect", i)
        journal.append("done", op_id=oid, kind="delete_entity", key=cand.entity_id)
    fault("before_verify", 0)
    final = read_scope_state(client, scope, adopted=())
    skipped_ids = {s["entity_id"] for s in skipped}
    bad = [f"candidate {c.entity_id} still exists" for c in plan.candidates
           if c.action == "delete" and c.entity_id not in skipped_ids and c.entity_id in final.rows]
    after = {**{f"e:{r['id']}": _sha(r) for r in final.rows.values()},
             **{f"l:{lk['id']}": _sha(lk) for lk in final.link_rows.values()}}
    changed = [k for k in foreign_changes(foreign_before, after) if not k.startswith("appeared:")]
    vanished = [k for k in changed if k.startswith("vanished:")]
    bad.extend(f"foreign record {k[9:]} vanished during the purge" for k in vanished)
    if bad:
        journal.append("verify_failed", mismatches=bad[:10])
        raise ImportVerificationError(bad)
    fault("before_receipt", 0)
    now = clock()
    receipt = ImportReceipt(
        receipt_id=f"receipt-{_sha([change.plan_sha256, 'purge'])[:20]}", plan_id=change.plan_id,
        plan_sha256=change.plan_sha256, approval_id=approval.approval_id, snapshot_sha256=change.input_sha256,
        target=change.target, executed_as=approval.executed_as, adapter_version=ADAPTER_VERSION,
        id_mapping_sha256=IdentityLedger().sha256(),
        outcomes={"deleted_entities": deleted_entities, "deleted_links": deleted_links, "skipped": len(skipped),
                  "kept_previous": int(plan.detail["kept_previous"]), "foreign_changes": len(changed)},
        verified=True, completed_at=now)
    extras = ReceiptExtras(
        manifest_sha256=change.input_sha256, snapshot_id=change.snapshot_id, graph_id=scope,
        corpus_scope=list(change.corpus_scope), requested_by=approval.requested_by, approved_by=approval.approved_by,
        profile=approval.profile.value, authorization_mode=approval.mode.value, base_revision=change.base_revision,
        resulting_revision=final.revision, kind="purge", foreign_changes=changed[:200],
        read_probes={"skipped": skipped})
    artifact = make_artifact(receipt, extras)
    path = store.write(artifact)
    journal.append("completed", receipt_id=receipt.receipt_id, resulting_revision=final.revision,
                   deleted_entities=deleted_entities, skipped=len(skipped))
    return ImportOutcome("applied", artifact, path, resumed=resumed)


# --------------------------------------------------------------------------- retire-all (corpus removal)

@dataclass(frozen=True)
class RetireAllPlan:
    """Immutable corpus-removal plan (every live record of the scope retired, pointer to none)."""

    change: ChangePlan
    detail: dict[str, Any]

    @property
    def scope(self) -> str:
        """Return the corpus scope."""
        return cast(str, self.detail["scope"])

    @property
    def tag(self) -> str:
        """Return the plan tag that marks the retired rows."""
        return self.change.plan_sha256[:16]

    @property
    def ops(self) -> list[ScopeOp]:
        """Return the operations (retire_node, retire_link, retire_marker)."""
        return [ScopeOp(*o) for o in self.detail["ops"]]

    @property
    def is_noop(self) -> bool:
        """Whether the scope is already empty."""
        return not self.detail["ops"]

    @property
    def summary(self) -> dict[str, Any]:
        """Report for the approver: counts, boundary links, adopted legacy graphs."""
        kinds: dict[str, int] = {}
        for op in self.ops:
            kinds[op.kind] = kinds.get(op.kind, 0) + 1
        return {"plan_sha256": self.change.plan_sha256, "target": self.change.target, "scope": self.scope,
                "operations": kinds, "boundary_links": list(self.detail["boundary_links"]),
                "legacy_graph_ids": list(self.detail["legacy_graph_ids"]), "noop": self.is_noop}

    def verify_integrity(self) -> None:
        """Recompute the plan from its detail; any edit is refused."""
        c = self.change
        try:
            expect = _operational_plan(self.detail, kind="retire-all", scope=self.scope, target=c.target,
                                       base_revision=c.base_revision, supersessions=len(self.detail["ops"]),
                                       affected=[ScopeOp(*o).token for o in self.detail["ops"]])
        except (TypeError, KeyError, ValueError):
            raise ContractError(ErrorCode.INVALID_INPUT, "retire-all plan detail is malformed") from None
        if expect != c:
            raise ContractError(ErrorCode.STALE_APPROVAL, "retire-all plan was modified after it was computed")

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable form."""
        return {"change": self.change.model_dump(mode="json"), "detail": self.detail}


def plan_retire_all(*, client: TiledMutationClient, settings: DeploymentSettings, scope: str, confirm_scope: str,
                    namespace: str | None = None, access_blob: Mapping[str, Any] | None = None,
                    edit_journal: ej.EditJournal | None = None,
                    resolutions: Sequence[ej.ConflictResolution] = ()) -> RetireAllPlan:
    """Read-only: plan "retire everything in scope"; the corpus name must be typed in as ``confirm_scope``.

    Raises:
        ContractError: ``INVALID_INPUT`` when ``confirm_scope`` differs from ``scope``; ``CONFLICT`` when an
            open edit of the scope has no reviewed ``discarded`` resolution; ``UNAVAILABLE``;
            ``WRITER_UNAUTHORIZED``.
    """
    if confirm_scope != scope:
        raise ContractError(ErrorCode.INVALID_INPUT,
                            f"corpus removal needs the scope typed in: {confirm_scope!r} != {scope!r}")
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    if edit_journal is not None:
        open_edits = ej.unreconciled(edit_journal, target=target, scope=(scope,), lineage=(), include_reconciled=False)
        decided = {r.edit_id: r.decision for r in resolutions}
        blocking = sorted(r.edit_id for r in open_edits if decided.get(r.edit_id) != "discarded")
        if blocking:
            raise ContractError(ErrorCode.CONFLICT, f"{len(blocking)} open edit(s) block the removal of {scope!r} "
                                                    f"unless resolved as discarded: {blocking[:10]}",
                                                    retryable=False)
    with _writer_errors():
        entities = client.scan_entities()
        legacy = legacy_graph_ids(entities, scope)
        state = read_scope_state(client, scope, adopted=legacy)
    ops: list[ScopeOp] = []
    for node_id, row in sorted(state.final.items()):
        ops.append(ScopeOp("retire_node", node_id, row["id"], scope))
    for gid, finals in sorted(state.legacy_final.items()):
        for node_id, row in sorted(finals.items()):
            ops.append(ScopeOp("retire_node", f"legacy/{gid}/{node_id}", row["id"], gid))
    for eid, rows in sorted(state.active_links.items()):
        ops.extend(ScopeOp("retire_link", f"{eid}@{lk['id']}", lk["id"], scope) for lk in rows)
    for gid, rows in sorted(state.legacy_links.items()):
        ops.extend(ScopeOp("retire_link", f"legacy/{gid}@{lk['id']}", lk["id"], gid) for lk in rows)
    if state.release is not None:
        ops.append(ScopeOp("retire_marker", lw.RELEASE_NODE, state.release["id"], scope))
    boundary: list[dict[str, Any]] = []
    for op in ops:
        if op.kind == "retire_node" and op.old_id:
            node = op.key.split("/", 2)[2] if op.key.startswith("legacy/") else op.key
            for lk in state.foreign_links_of(op.old_id):
                brief = _link_brief(lk, op.old_id, state.rows)
                boundary.append(BoundaryLink(brief["link_id"], str(brief["predicate"]), brief["direction"], node,
                                             op.graph or scope, op.old_id, "removed", brief["other"]).as_dict())
    detail = {"adapter_version": ADAPTER_VERSION, "kind": "retire-all", "scope": scope,
              "access_blob": dict(access_blob or {}), "legacy_graph_ids": list(legacy),
              "ops": [o.as_list() for o in ops], "boundary_links": sorted(boundary, key=lambda b: (b["node_id"],
                                                                                                    b["link_id"])),
              "resolutions_sha256": ej.resolutions_sha256(resolutions), "foreign_count": len(state.foreign)}
    change = _operational_plan(detail, kind="retire-all", scope=scope, target=target, base_revision=state.revision,
                               supersessions=len(ops), affected=[o.token for o in ops])
    return RetireAllPlan(change, detail)


def apply_retire_all(ctx: RequestContext, plan: RetireAllPlan, approval: ApprovalRecord | None, *,
                     client: TiledMutationClient, settings: DeploymentSettings, state_dir: Path, clock: Clock,
                     pointer_to_none: Callable[[], Any] | None = None, lease_seconds: float = 120.0,
                     fault: FaultHook | None = None) -> ImportOutcome:
    """Retire every live record of the scope, verify, receipt, release state and pointer to ``none``.

    Resumable: already-retired rows are recognized on the re-scan. Foreign records are fingerprinted before
    and after; changes are recorded (Q14). ``pointer_to_none`` (for example ``PublishStore.clear_pointer``
    bound to the target) runs after the receipt and may run twice on a resume: it must be idempotent.
    """
    fault = fault or (lambda _stage, _i: None)
    plan.verify_integrity()
    approval = _refuse(ctx, plan.change, settings, client, approval, clock)
    change, scope, access = plan.change, plan.scope, plan.detail["access_blob"]
    store = ReceiptStore(Path(state_dir) / "receipts")
    root = scope_dir(state_dir, change.target, scope)
    run_dir = root / "plans" / plan.tag
    journal = ImportJournal(run_dir / "journal.jsonl", clock)
    release_state = ReleaseState(root / "release.json")
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    try:
        with _writer_errors():
            legacy = tuple(plan.detail["legacy_graph_ids"])
            resumed = journal.started()
            state = read_scope_state(client, scope, adopted=legacy)
            for art in store.find(plan_sha256=change.plan_sha256):
                if receipt_valid_for(art, target=change.target, revision=state.revision):
                    release_state.set_none(receipt_id=art.receipt.receipt_id, at=clock())
                    if pointer_to_none is not None:
                        pointer_to_none()
                    return ImportOutcome("already_applied", art, None, resumed=True)
            if not resumed and state.revision != change.base_revision:
                raise ContractError(ErrorCode.STALE_APPROVAL, "the target revision is not the plan's base revision")
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "plan.json").write_text(json.dumps(plan.to_json(), indent=2, sort_keys=True), encoding="utf-8")
            foreign_file = run_dir / "foreign.json"
            if foreign_file.exists():
                foreign_before = json.loads(foreign_file.read_text(encoding="utf-8"))
            else:
                foreign_before = dict(state.foreign)
                foreign_file.write_text(json.dumps(foreign_before, sort_keys=True), encoding="utf-8")
            journal.append("run_started" if not resumed else "run_resumed", plan_sha256=change.plan_sha256,
                           executed_as=approval.executed_as, requested_by=approval.requested_by,
                           approved_by=approval.approved_by, scope=scope)
            release_state.mark_switching(plan.tag)
            done = journal.done_ids()
            _retire_everything(plan, state, client, journal, lease, access, done, fault)
            fault("before_verify", 0)
            final = read_scope_state(client, scope, adopted=legacy)
            bad = [f"{len(final.final)} live record(s) remain"] if final.final else []
            bad += [f"{len(v)} live record(s) of {g} remain" for g, v in final.legacy_final.items() if v]
            bad += [f"{sum(len(v) for v in final.active_links.values())} owned link(s) remain"] \
                if final.active_links else []
            bad += [f"link(s) of {g} remain" for g, v in final.legacy_links.items() if v]
            bad += ["the release marker is still live"] if final.release is not None else []
            if bad:
                journal.append("verify_failed", mismatches=bad[:10])
                raise ImportVerificationError(bad)
            changed = foreign_changes(foreign_before, final.foreign)
            fault("before_receipt", 0)
            now = clock()
            receipt_id = f"receipt-{_sha([change.plan_sha256, 'retire-all'])[:20]}"
            receipt = ImportReceipt(
                receipt_id=receipt_id, plan_id=change.plan_id, plan_sha256=change.plan_sha256,
                approval_id=approval.approval_id, snapshot_sha256=change.input_sha256, target=change.target,
                executed_as=approval.executed_as, adapter_version=ADAPTER_VERSION,
                id_mapping_sha256=IdentityLedger().sha256(),
                outcomes={"retired_entities": sum(1 for o in plan.ops if o.kind == "retire_node"),
                          "deleted_links": sum(1 for o in plan.ops if o.kind == "retire_link"),
                          "boundary_links": len(plan.detail["boundary_links"]), "foreign_changes": len(changed)},
                verified=True, completed_at=now)
            extras = ReceiptExtras(
                manifest_sha256=change.input_sha256, snapshot_id=change.snapshot_id, graph_id=scope,
                corpus_scope=list(change.corpus_scope), requested_by=approval.requested_by,
                approved_by=approval.approved_by, profile=approval.profile.value,
                authorization_mode=approval.mode.value, base_revision=change.base_revision,
                resulting_revision=final.revision, kind="retire-all", adopted_graph_ids=list(legacy),
                boundary_links=len(plan.detail["boundary_links"]), foreign_changes=changed[:200])
            artifact: ReceiptArtifact = make_artifact(receipt, extras)
            path = store.write(artifact)
            retired = {n: r["id"] for n, r in final.retired_for(plan.tag).items()}
            LedgerHistory(root / "ledger_history.jsonl").append(receipt_id=receipt_id, snapshot_id=change.snapshot_id,
                                                                plan_sha256=change.plan_sha256,
                                                                ledger=IdentityLedger(), retired=retired, at=now)
            release_state.set_none(receipt_id=receipt_id, at=now)
            if pointer_to_none is not None:
                pointer_to_none()
            journal.append("completed", receipt_id=receipt_id, resulting_revision=final.revision)
            return ImportOutcome("applied", artifact, path, resumed=resumed)
    except Exception as exc:
        if journal.started():
            journal.append("run_failed", error=type(exc).__name__,
                           code=getattr(getattr(exc, "code", None), "value", None))
        lease.release()
        raise
    finally:
        if lease.token is not None:
            lease.release()


def _retire_everything(plan: RetireAllPlan, state: ScopeState, client: TiledMutationClient, journal: ImportJournal,
                       lease: Lease, access: Mapping[str, Any], done: set[str], fault: FaultHook) -> None:
    scope, tag = plan.scope, plan.tag
    live_ids = set(state.rows) | set(state.link_rows)
    for i, op in enumerate(plan.ops):
        oid = operation_id(plan.change.plan_sha256, "switch", op.kind, op.key)
        row = state.rows.get(cast(str, op.old_id)) if op.kind != "retire_link" else state.link_rows.get(
            cast(str, op.old_id))
        if op.old_id not in live_ids or row is None:
            continue  # already gone (deleted link) or unknown
        if op.kind != "retire_link" and lw.classify_entity(row, op.graph or scope) != "final" \
                and lw.classify_entity(row, op.graph or scope) != lw.RELEASE:
            continue  # already retired by an earlier attempt
        fault("switch", i)
        lease.check()
        journal.append("intent", op_id=oid, kind=op.kind, key=op.key, replay=oid in done)
        gid = op.graph or scope
        if op.kind == "retire_link":
            client.delete_link(row, gid)
        else:
            node = lw.RELEASE_NODE if op.kind == "retire_marker" else (
                op.key.split("/", 2)[2] if op.key.startswith("legacy/") else op.key)
            client.set_entity_state(row, gid, uri=lw.state_uri(gid, node, lw.RETIRED, tag),
                                    entity_type=lw.RETIRED_TYPE, access_blob=access)
        fault("switch_effect", i)
        journal.append("done", op_id=oid, kind=op.kind, key=op.key)
