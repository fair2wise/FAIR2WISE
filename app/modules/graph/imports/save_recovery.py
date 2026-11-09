"""The multi-write Save sequence and its recovery job (T08D; owner decisions Q7, Q12, Q14).

Q7 (STOP-1, owner): every UI Save performs its recreate-links sequence on Tiled **immediately**, journaled
and resumable, instead of waiting for the next publication. Q12: the Save is acknowledged only after the
journal write *and* the Tiled effect; a partial failure answers the existing 502, marks the edit journal
entry ``failed`` and leaves reconciliation to the recovery job here.

On 0.2.18 properties are create-only and link endpoints are fixed (T20C P4), so a node edit is a new
revision plus moved links. The sequence for one ``node_patch`` edit of node ``N`` in scope ``S`` is:

1. ``create_revision``: create the new revision under the pending URI with type ``f2w:Staged``;
2. ``recreate_links``: for every F2W-owned link of the old revision, create the same link on the new one
   (still invisible: one endpoint is staged);
3. ``retire_old``: move the old revision to its retired URI, type ``f2w:Retired`` (foreign links stay on it,
   T20C P3, and are reported);
4. ``promote_new``: final URI and real type (the edit is now visible);
5. ``delete_old_links``: delete the old revision's owned links.

Every step is journaled ``intent``/``done`` under a stable operation id in a per-edit import journal, so the
recovery job can tell how far a Save got. The **recovery job** reconciles each ``failed`` entry (and each
``recorded`` entry whose Save journal started but never completed, i.e. a hard crash) to exactly one of:

* the **full effect** (``applied``): the new revision was promoted, so steps 2 and 5 are finished;
* the **pre-Save state** (``reverted``): the new revision was never promoted, so its links and the staged
  record are removed (unless a foreign link already points at it: then it is retired and reported) and the
  old revision, if it was retired, is restored to final.

The journal entry's final state says which; the job is idempotent (a second run finds nothing to do) and
resumable (its own intent journal). Foreign records changing under the job are recorded, never fatal (Q14).
T18D wires the UI route to :func:`execute_save`; nothing here parses, calls a model or reads a snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, cast

from app.modules.contracts.context import RequestContext
from app.modules.security.authorization import DeploymentSettings, require_any

from .. import edit_journal as ej
from .. import identity_adapter as ida
from .. import literature_writer as lw
from ..backends.tiled_mutations import TiledMutationClient
from ..ports import ContractError, ErrorCode
from .corpus_scope import ReleaseState, ScopeState, read_scope_state, scope_dir
from .journal import ImportJournal, Lease, operation_id
from .tiled_json import Clock, FaultHook, _check_client, _writer_errors, target_for

logger = logging.getLogger(__name__)

__all__ = ["SAVE_STEPS", "SaveResult", "StaleRevision", "execute_save", "recover_failed_saves", "save_tag"]

SAVE_STEPS = ("create_revision", "recreate_links", "retire_old", "promote_new", "delete_old_links")
SAVE_CAPABILITY = "graph.edit"


class StaleRevision(ContractError):
    """The record changed since the edit was made (Q10: the legacy route answers 409, not the 502)."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CONFLICT, message, retryable=False)


def save_tag(edit_id: str) -> str:
    """Plan tag of a Save: marks the edit's pending and retired rows (``.../pending/<node>/<tag>``)."""
    return hashlib.sha256(("save:" + edit_id).encode()).hexdigest()[:16]


class SaveResult(dict[str, Any]):
    """Readback of a completed Save: ``node_id``, ``tiled_id``, ``old_tiled_id``, ``links``, ``state``."""


def _node_spec(scope: str, payload: Mapping[str, Any]) -> lw.EntitySpec:
    return lw.entity_spec(scope, payload)


def _edit_payloads(record: ej.EditRecord) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    if record.operation != "node_patch" or len(record.ids) != 1 or len(record.scope) != 1:
        raise ContractError(ErrorCode.INVALID_INPUT, "the Save sequence handles one node_patch of one scope")
    before, after = record.before.get("node"), record.after.get("node")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or after.get("id") != record.ids[0]:
        raise ContractError(ErrorCode.INVALID_INPUT, "node_patch needs before.node and after.node payloads")
    return record.scope[0], record.ids[0], dict(before), dict(after)


def _owned_links_of(st: ScopeState, entity_id: str, scope: str) -> list[dict[str, Any]]:
    return [lk for lk in st.link_rows.values() if (ida.literature_link_key(lk) or ("",))[0] == scope
            and entity_id in (lk.get("subjectId"), lk.get("objectId"))]


def _moved(lk: Mapping[str, Any], old_id: str, new_id: str) -> tuple[str, str, str, str]:
    s = new_id if lk.get("subjectId") == old_id else cast(str, lk.get("subjectId"))
    o = new_id if lk.get("objectId") == old_id else cast(str, lk.get("objectId"))
    return s, str(lk.get("predicate")), o, lw.canonical(lk.get("properties") or {})


def _link_key(lk: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (cast(str, lk.get("subjectId")), str(lk.get("predicate")), cast(str, lk.get("objectId")),
            lw.canonical(lk.get("properties") or {}))


def execute_save(ctx: RequestContext, record: ej.EditRecord, *, journal: ej.EditJournal, client: TiledMutationClient,
                 settings: DeploymentSettings, state_dir: Path, clock: Clock,
                 access_blob: Mapping[str, Any] | None = None, namespace: str | None = None,
                 fault: FaultHook | None = None, lease_seconds: float = 60.0) -> SaveResult:
    """Run the five-step Save sequence for a ``recorded`` ``node_patch`` edit and advance it to ``applied``.

    Authorization comes first (``graph.edit``; a denied caller leaves no trace). The edit must already be
    in the edit journal as ``recorded`` (the durable intent). A switch in flight for the scope refuses the
    Save (Q8; the route maps this ``CONFLICT`` to the legacy 502). Any failure after the first Tiled write
    marks the entry ``failed`` and re-raises; the recovery job finishes or reverts it.

    Raises:
        ContractError: ``DENIED``; ``CONFLICT`` (switch in flight, stale node, lease); ``UNAVAILABLE``;
            ``WRITER_UNAUTHORIZED``; ``NOT_FOUND`` (the node is not live in the scope).
    """
    fault = fault or (lambda _stage, _i: None)
    require_any(ctx, SAVE_CAPABILITY, "graph.publish")
    scope, node_id, before, after = _edit_payloads(record)
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    current = journal.latest().get(record.edit_id)
    if current is None or current.state != "recorded":
        raise ContractError(ErrorCode.CONFLICT, f"edit {record.edit_id} is not a recorded, unapplied edit",
            retryable=False)
    root = scope_dir(state_dir, target, scope)
    if ReleaseState(root / "release.json").switching:
        raise ContractError(ErrorCode.CONFLICT, "the graph is being republished; try again", retryable=True)
    tag = save_tag(record.edit_id)
    access = dict(access_blob or {})
    op_journal = ImportJournal(root / "saves" / record.edit_id / "journal.jsonl", clock)
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    written = False
    try:
        with _writer_errors():
            st = read_scope_state(client, scope, double_read=False)
            old = st.final.get(node_id)
            new_spec = _node_spec(scope, after)
            if old is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"node {node_id!r} is not live in scope {scope!r}")
            if not lw.entity_matches(old, _node_spec(scope, before)):
                raise StaleRevision(f"node {node_id!r} changed since the edit was made (409)")
            op_journal.append("save_started", edit_id=record.edit_id, tag=tag, old_tiled_id=old["id"])
            written = True
            result = _run_sequence(client, st, scope, node_id, old, new_spec, tag, access, op_journal, lease, fault)
    except BaseException as exc:
        if written and isinstance(exc, Exception):
            journal.advance(record.edit_id, "failed", clock(),
                            note=f"save sequence stopped: {type(exc).__name__}: {str(exc)[:160]}")
            op_journal.append("save_failed", error=type(exc).__name__)
        lease.release()
        raise
    journal.advance(record.edit_id, "applied", clock(), note="save sequence completed")
    op_journal.append("save_completed", tiled_id=result["tiled_id"])
    lease.release()
    return result


def _run_sequence(client: TiledMutationClient, st: ScopeState, scope: str, node_id: str, old: Mapping[str, Any],
                  new_spec: lw.EntitySpec, tag: str, access: Mapping[str, Any], op_journal: ImportJournal,
                  lease: Lease, fault: FaultHook) -> SaveResult:
    done = op_journal.done_ids()
    pending = st.pending_for(tag).get(node_id)

    def step(name: str, i: int) -> str:
        oid = operation_id(tag, "save", name, node_id)
        fault(name, i)
        lease.check()
        op_journal.append("intent", op_id=oid, kind=name, key=node_id, replay=oid in done)
        return oid

    oid = step("create_revision", 0)
    if pending is None:
        new_id = client.create_entity(entity_type=lw.STAGED_TYPE, name=new_spec.name,
                                      uri=lw.state_uri(scope, node_id, lw.PENDING, tag),
                                      properties=new_spec.properties, access_blob=access)
    else:
        new_id = pending["id"]
    fault("create_revision_effect", 0)
    op_journal.append("done", op_id=oid, kind="create_revision", key=node_id, tiled_id=new_id)
    old_links = _owned_links_of(st, old["id"], scope)
    existing = {_link_key(lk) for lk in client.links_of(new_id)}
    for i, lk in enumerate(sorted(old_links, key=lambda r: str(r["id"]))):
        s, p, o, _props = _moved(lk, old["id"], new_id)
        if (s, p, o, _props) in existing:
            continue
        oid = step("recreate_links", i)
        client.create_link(subject_id=s, predicate=p, object_id=o, properties=lk.get("properties") or {},
                           access_blob=access)
        fault("recreate_links_effect", i)
        op_journal.append("done", op_id=oid, kind="recreate_links", key=f"{node_id}@{lk['id']}")
    oid = step("retire_old", 0)
    client.set_entity_state(old, scope, uri=lw.state_uri(scope, node_id, lw.RETIRED, tag),
                            entity_type=lw.RETIRED_TYPE, access_blob=access)
    fault("retire_old_effect", 0)
    op_journal.append("done", op_id=oid, kind="retire_old", key=node_id)
    oid = step("promote_new", 0)
    client.set_entity_state({"id": new_id, "properties": new_spec.properties}, scope, uri=new_spec.uri,
                            entity_type=new_spec.entity_type, access_blob=access)
    fault("promote_new_effect", 0)
    op_journal.append("done", op_id=oid, kind="promote_new", key=node_id)
    for i, lk in enumerate(sorted(old_links, key=lambda r: str(r["id"]))):
        oid = step("delete_old_links", i)
        client.delete_link(lk, scope)
        fault("delete_old_links_effect", i)
        op_journal.append("done", op_id=oid, kind="delete_old_links", key=f"{node_id}@{lk['id']}")
    return SaveResult(node_id=node_id, tiled_id=new_id, old_tiled_id=old["id"], state="applied",
                      links=len(old_links))


# --------------------------------------------------------------------------- recovery job

def _journal_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            logger.debug("corrupt recovery line skipped: %s", type(exc).__name__)
            continue
    return out


def _needs_recovery(record: ej.EditRecord, root: Path) -> bool:
    if record.state == "failed":
        return True
    if record.state != "recorded":
        return False
    rows = _journal_rows(root / "saves" / record.edit_id / "journal.jsonl")
    return any(r.get("event") == "save_started" for r in rows) and \
        not any(r.get("event") in ("save_completed", "recovered") for r in rows)


def recover_failed_saves(ctx: RequestContext, journal: ej.EditJournal, *, client: TiledMutationClient,
                         settings: DeploymentSettings, state_dir: Path, clock: Clock, scope: str,
                         access_blob: Mapping[str, Any] | None = None, namespace: str | None = None,
                         fault: FaultHook | None = None, lease_seconds: float = 60.0) -> list[dict[str, Any]]:
    """Reconcile every half-done Save of ``scope`` to its full effect or to the pre-Save state.

    Admin work (``jobs.admin`` or ``graph.publish``). Returns one report per edit handled:
    ``{"edit_id", "outcome": "applied" | "reverted", "detail"}``. Idempotent: entries already reconciled are
    not touched; a second call returns ``[]``.

    Raises:
        ContractError: ``DENIED``; ``CONFLICT`` (lease, switch in flight); ``UNAVAILABLE``;
            ``WRITER_UNAUTHORIZED``.
    """
    fault = fault or (lambda _stage, _i: None)
    require_any(ctx, "jobs.admin", "graph.publish")
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    root = scope_dir(state_dir, target, scope)
    if ReleaseState(root / "release.json").switching:
        raise ContractError(ErrorCode.CONFLICT, "the graph is being republished; recover afterwards", retryable=True)
    access = dict(access_blob or {})
    todo = [r for r in journal.latest().values()
            if r.target == target and scope in r.scope and r.operation == "node_patch" and _needs_recovery(r, root)]
    if not todo:
        return []
    reports: list[dict[str, Any]] = []
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    try:
        with _writer_errors():
            for record in sorted(todo, key=lambda r: r.edit_id):
                reports.append(_recover_one(record, journal, client, scope, root, access, lease, clock, fault))
    finally:
        lease.release()
    return reports


def _recover_one(record: ej.EditRecord, journal: ej.EditJournal, client: TiledMutationClient, scope: str,
                 root: Path, access: Mapping[str, Any], lease: Lease, clock: Clock, fault: FaultHook) -> dict[str, Any]:
    _scope, node_id, before, after = _edit_payloads(record)
    tag = save_tag(record.edit_id)
    op_journal = ImportJournal(root / "saves" / record.edit_id / "journal.jsonl", clock)
    st = read_scope_state(client, scope, double_read=False)
    new_spec, old_spec = _node_spec(scope, after), _node_spec(scope, before)
    final = st.final.get(node_id)
    pending = st.pending_for(tag).get(node_id)
    retired_old = st.retired_for(tag).get(node_id)
    started = [r for r in op_journal.records() if r.get("event") == "save_started"]
    old_id = started[0].get("old_tiled_id") if started else (retired_old or {}).get("id")
    op_journal.append("recovery_started", edit_id=record.edit_id)
    lease.check()
    detail: dict[str, Any]
    if final is not None and lw.entity_matches(final, new_spec):
        # The new revision was promoted: roll forward (finish moving the links, drop the old ones).
        new_id = final["id"]
        old_row = st.rows.get(cast(str, old_id)) if old_id else None
        moved = 0
        if old_row is not None:
            existing = {_link_key(lk) for lk in client.links_of(new_id)}
            for i, lk in enumerate(sorted(_owned_links_of(st, old_row["id"], scope), key=lambda r: str(r["id"]))):
                s, p, o, props = _moved(lk, old_row["id"], new_id)
                if (s, p, o, props) not in existing:
                    fault("recover_link", i)
                    client.create_link(subject_id=s, predicate=p, object_id=o, properties=lk.get("properties") or {},
                                       access_blob=access)
                    moved += 1
                client.delete_link(lk, scope)
                fault("recover_delete", i)
        outcome, detail = "applied", {"tiled_id": new_id, "links_recreated": moved}
        journal.advance(record.edit_id, "applied", clock(), note="recovered: full Save effect completed")
    else:
        # Never promoted: revert. Remove the staged revision and its links; restore the old revision.
        removed_links = 0
        kept_staged: str | None = None
        if pending is not None:
            for lk in client.links_of(pending["id"]):
                if (ida.literature_link_key(lk) or ("",))[0] == scope:
                    client.delete_link(lk, scope)
                    removed_links += 1
            fault("recover_revert_links", 0)
            if client.links_of(pending["id"]):      # a foreign link already points at the staged row (I3)
                client.set_entity_state(pending, scope, uri=lw.state_uri(scope, node_id, lw.RETIRED, tag),
                                        entity_type=lw.RETIRED_TYPE, access_blob=access)
                kept_staged = pending["id"]
            else:
                client.delete_entity(pending, scope)
        fault("recover_revert_entity", 0)
        restored = None
        if final is None and retired_old is not None and lw.canonical(retired_old.get("properties") or {}) == \
                lw.canonical(old_spec.properties):
            client.set_entity_state(retired_old, scope, uri=old_spec.uri, entity_type=old_spec.entity_type,
                                    access_blob=access)
            restored = retired_old["id"]
        elif final is None:
            raise ContractError(ErrorCode.CONFLICT,
                                f"cannot revert Save {record.edit_id}: no revision of {node_id!r} to restore",
                                retryable=False)
        outcome = "reverted"
        detail = {"removed_links": removed_links, "restored_tiled_id": restored or (final or {}).get("id"),
                  "staged_kept_as_retired": kept_staged}
        journal.advance(record.edit_id, "reverted", clock(),
                        note="recovered: reverted to the pre-Save state" + (" (staged row retired, foreign link)"
                                                                           if kept_staged else ""))
    op_journal.append("recovered", outcome=outcome, **{k: v for k, v in detail.items() if v is not None})
    return {"edit_id": record.edit_id, "outcome": outcome, "detail": detail}


def save_journal_records(state_dir: Path, target: str, scope: str, edit_id: str) -> list[dict[str, Any]]:
    """Return the per-edit Save journal (for tests and the admin report)."""
    return _journal_rows(scope_dir(state_dir, target, scope) / "saves" / edit_id / "journal.jsonl")
