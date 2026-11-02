"""The legacy UI Save as a journaled multi-write on Tiled, with no Splash call (T18D; owner decisions Q7-Q14).

The frozen UI's ``PATCH /graph/node/{id}`` used to write a node, its snippets and its relationships to
Splash. With a :class:`SaveTarget` configured, :class:`TiledSaveBackend` does the same job on Tiled:

1. **Authorization first** (``graph.edit``): a denied caller leaves no journal entry and no Tiled access.
2. **Prepare** (:meth:`TiledSaveBackend.prepare`): refuse while a switch is in flight (Q8, the route's 502),
   finish whatever an earlier Save left half-done (the recovery job below), and return the **effective
   record**: the active snapshot's graph plus every in-flight journal entry. ``/graph``, node search, node
   detail and retrieval all read that one file, so a journaled-but-unfinished edit is visible everywhere.
3. **Diff** the legacy handler's before/after documents into journal edits (:func:`build_edits`):
   ``node_patch`` (a node or an existing snippet), ``relationship_add``/``relationship_remove``,
   ``snippet_upsert`` (new snippet node plus ``rel:has_code_snippet`` link, or a link to an existing one)
   and ``snippet_unlink`` (the implicit unlink the frozen UI relies on: the link goes, the snippet node
   stays, Q10).
4. **Validate against Tiled before anything is journaled**: a ``relationship_remove`` naming a link F2W does
   not own is refused with the legacy 400 (Q9); a ``node_patch`` or ``snippet_unlink`` from an out-of-date
   copy is a :class:`StaleSave` (409, Q10). Nothing is journaled for a refused Save.
5. For each edit, **journal first** (``EditJournal.record`` with the content-derived id, fsynced), **then
   write Tiled** (a ``node_patch`` through :func:`~.imports.save_recovery.execute_save`'s five-step
   sequence; link and snippet edits are single idempotent creates/deletes), **then advance** the entry to
   ``applied``. The request returns only after the Tiled effect (Q12).
6. A failure after the first Tiled write marks the entry ``failed`` and re-raises (the route's 502); the
   :meth:`recover` job finishes it (full effect) or, for a ``node_patch``, reverts it to the pre-Save state.
   A retry of the same request first runs that recovery, so it is answered with the same readback.

Edit ids are content derived (``derive_edit_id``): a retry after a lost response finds its own entry. When the
same content recurs after the graph moved on (A to B, B to A, A to B again) the id is salted with the
base snapshot and a counter, so a legitimate repeat is a new edit and a retry is not.

Everything works without Tiled too: with no :class:`SaveTarget` the service keeps its previous behaviour and
this module is not imported by that path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any

from app.modules.contracts.context import RequestContext
from app.modules.security.authorization import DeploymentSettings, require

from . import edit_journal as ej
from . import identity_adapter as ida
from . import literature_writer as lw
from .backends.tiled_mutations import TiledMutationClient
from .edit_replay import IN_FLIGHT_STATES, replay_journal
from .imports import save_recovery as sr
from .imports.corpus_scope import ReleaseState, ScopeState, read_scope_state, scope_dir
from .imports.journal import Lease
from .imports.save_recovery import StaleRevision
from .imports.tiled_json import Clock, FaultHook, _check_client, _writer_errors, target_for
from .ports import ContractError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["SaveOutcome", "SaveTarget", "StaleSave", "TiledSaveBackend", "build_edits"]

SNIPPET_PREDICATE = "rel:has_code_snippet"
_EDGE_OPS = ("relationship_add", "relationship_remove", "snippet_upsert", "snippet_unlink")


class StaleSave(ContractError):
    """The Save was made from an out-of-date copy (Q10): the legacy route answers 409."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CONFLICT, message, retryable=False)


@dataclass
class SaveTarget:
    """Everything the Save sequence needs about one corpus scope on one Tiled target.

    Attributes:
        client: Writer client (the runtime writer credential only; never a user token).
        settings: Deployment settings the target must belong to.
        state_dir: Importer state directory (release marker, leases, per-edit journals).
        clock: Time source.
        scope: Corpus scope (Q1: one stable Tiled graph per corpus).
        journal: Durable scoped edit journal.
        base_graph_path: The active snapshot's ``graph.json`` (read only).
        base_snapshot_id / base_graph_sha256: The active snapshot; the journal records edits against it and the
            base file is hash-checked on every read.
        namespace / access_blob: Passed to the writes (``access_blob`` is required on every 0.2.18 update).
        fault: Test hook, called as ``fault(stage, index)`` between writes.
    """

    client: TiledMutationClient
    settings: DeploymentSettings
    state_dir: Path
    clock: Clock
    scope: str
    journal: ej.EditJournal
    base_graph_path: Path
    base_snapshot_id: str
    base_graph_sha256: str
    namespace: str | None = None
    access_blob: Mapping[str, Any] | None = None
    fault: FaultHook | None = None

    @property
    def target(self) -> str:
        """Normalized Tiled target of the journal records."""
        return target_for(self.client, self.namespace)


@dataclass
class SaveOutcome:
    """What one Save did: the journal edit ids applied (empty for a no-op Save) and the recovery reports."""

    edit_ids: list[str] = field(default_factory=list)
    recovered: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _Edit:
    operation: str
    ids: tuple[str, ...]
    before: dict[str, object]
    after: dict[str, object]


# --------------------------------------------------------------------------- diff

def _triple(edge: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(edge.get("subject") or edge.get("source") or ""), str(edge.get("predicate") or "rel:related_to"),
            str(edge.get("object") or edge.get("target") or ""))


def _normal_edge(edge: Mapping[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in edge.items() if k not in ("source", "target")}
    s, p, o = _triple(edge)
    return {**body, "subject": s, "predicate": p, "object": o}


def _nodes(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(n["id"]): n for n in (data.get("things") or []) if isinstance(n, dict) and n.get("id")}


def _edges(data: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in data.get("associations") or []:
        if isinstance(edge, dict):
            out.setdefault(_triple(edge), _normal_edge(edge))
    return out


def build_edits(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[_Edit]:
    """Journal edits that turn the ``before`` session graph into ``after`` (deterministic order).

    Order: node patches, snippet upserts, relationship adds, relationship removes, snippet unlinks. A node
    present only in ``after`` is a new snippet and must come with its ``rel:has_code_snippet`` edge.
    """
    b_nodes, a_nodes = _nodes(before), _nodes(after)
    b_edges, a_edges = _edges(before), _edges(after)
    edits: list[_Edit] = []
    for node_id in sorted(set(b_nodes) & set(a_nodes)):
        if b_nodes[node_id] != a_nodes[node_id]:
            edits.append(_Edit("node_patch", (node_id,), {"node": b_nodes[node_id]}, {"node": a_nodes[node_id]}))
    added = [t for t in a_edges if t not in b_edges]
    removed = [t for t in b_edges if t not in a_edges]
    new_nodes = set(a_nodes) - set(b_nodes)
    used: set[str] = set()
    for t in sorted(t for t in added if t[1] == SNIPPET_PREDICATE):
        snippet = t[2] if t[2] in a_nodes else t[0]
        used.add(snippet)
        edits.append(_Edit("snippet_upsert", (t[0], t[2]), {}, {"node": a_nodes[snippet], "edge": a_edges[t]}))
    stray = new_nodes - used
    if stray:
        raise ValueError(f"new node(s) without a snippet link: {sorted(stray)}")
    edits += [_Edit("relationship_add", t_ids(t), {}, {"edge": a_edges[t]}) for t in sorted(added)
              if t[1] != SNIPPET_PREDICATE]
    edits += [_Edit("relationship_remove", t_ids(t), {"edge": b_edges[t]}, {}) for t in sorted(removed)
              if t[1] != SNIPPET_PREDICATE]
    edits += [_Edit("snippet_unlink", t_ids(t), {"edge": b_edges[t]}, {}) for t in sorted(removed)
              if t[1] == SNIPPET_PREDICATE]
    return edits


def t_ids(t: tuple[str, str, str]) -> tuple[str, ...]:
    """Touched node ids of an edge edit: its two endpoints."""
    return (t[0], t[2])


# --------------------------------------------------------------------------- backend

class TiledSaveBackend:
    """Save, recovery and the effective record for one :class:`SaveTarget`."""

    def __init__(self, target: SaveTarget) -> None:
        """Bind the target; the scope's journal and state live where the target says."""
        self.t = target
        self._lock = threading.RLock()

    # -- authorization and refusals ---------------------------------------------------------
    def _root(self) -> Path:
        return scope_dir(self.t.state_dir, self.t.target, self.t.scope)

    def _refuse_during_switch(self) -> None:
        if ReleaseState(self._root() / "release.json").switching:
            raise ContractError(ErrorCode.CONFLICT, "the graph is being republished; try again", retryable=True)

    def _check(self) -> None:
        _check_client(self.t.client, self.t.settings, self.t.target)

    # -- effective record ----------------------------------------------------------------------
    def base_graph(self) -> dict[str, Any]:
        """The active snapshot's graph, hash-checked against the configured sha256."""
        try:
            blob = self.t.base_graph_path.read_bytes()
        except OSError:
            raise ContractError(ErrorCode.UNAVAILABLE, "the active snapshot graph is unavailable") from None
        if hashlib.sha256(blob).hexdigest() != self.t.base_graph_sha256:
            raise ContractError(ErrorCode.CONFLICT, "the active snapshot graph does not match its recorded hash")
        data = json.loads(blob.decode("utf-8"))
        if not isinstance(data, dict):
            raise ContractError(ErrorCode.INVALID_INPUT, "the active snapshot graph is not a KG document")
        data.setdefault("things", [])
        data.setdefault("associations", [])
        return data

    def effective_graph(self) -> dict[str, Any]:
        """Active snapshot plus every in-flight journal entry of the scope (what every F2W reader sees)."""
        result = replay_journal(self.base_graph(), self.t.journal, target=self.t.target, scope=(self.t.scope,),
                                states=IN_FLIGHT_STATES)
        for conflict in result.conflicts:
            logger.warning("journal edit %s is not shown in the effective graph: %s", conflict.edit_id,
                           conflict.reason)
        return result.graph

    # -- one Save -------------------------------------------------------------------------------
    def prepare(self, ctx: RequestContext) -> SaveOutcome:
        """Authorize, refuse during a switch, finish earlier half-done Saves. Call before reading the graph.

        Raises:
            ContractError: ``DENIED`` (nothing is touched); ``CONFLICT`` retryable during a switch (Q8).
        """
        require(ctx, "graph.edit")
        self._check()
        self._refuse_during_switch()
        with self._lock:
            return SaveOutcome(recovered=self.recover(ctx))

    def save(self, ctx: RequestContext, before: Mapping[str, Any], after: Mapping[str, Any]) -> SaveOutcome:
        """Journal and apply the difference between two session documents; return only after Tiled has it.

        Raises:
            ContractError: ``DENIED``; ``CONFLICT`` (switch in flight, lease); :class:`StaleSave`;
                ``UNAVAILABLE``/``WRITER_UNAUTHORIZED`` (a partial Save is marked ``failed``).
            ValueError: A relationship removal that names a link F2W does not own (Q9; the route's 400), or a
                malformed difference.
        """
        require(ctx, "graph.edit")
        self._check()
        self._refuse_during_switch()
        edits = build_edits(before, after)
        outcome = SaveOutcome()
        if not edits:
            return outcome
        with self._lock, _writer_errors():
            st = read_scope_state(self.t.client, self.t.scope, double_read=False)
            edits = [e for e in edits if not self._completed_retry(st, e)]
            for edit in edits:
                self._validate(st, edit)
            for edit in edits:
                record = self._record(ctx, edit)
                if record is None:
                    continue
                outcome.edit_ids.append(record.edit_id)
                self._apply(ctx, record)
        return outcome

    # -- validation against Tiled (nothing is journaled yet) ----------------------------------------------
    def _owned(self, st: ScopeState, edge: Mapping[str, Any]) -> list[dict[str, Any]]:
        s, p, o = _triple(edge)
        sid, oid = st.final.get(s, {}).get("id"), st.final.get(o, {}).get("id")
        return [lk for lk in st.link_rows.values()
                if lk.get("subjectId") == sid and lk.get("objectId") == oid and lk.get("predicate") == p
                and (ida.literature_link_key(lk) or ("",))[0] == self.t.scope]

    def _foreign(self, st: ScopeState, edge: Mapping[str, Any]) -> list[dict[str, Any]]:
        s, p, o = _triple(edge)
        sid, oid = st.final.get(s, {}).get("id"), st.final.get(o, {}).get("id")
        return [lk for lk in st.link_rows.values()
                if lk.get("subjectId") == sid and lk.get("objectId") == oid and lk.get("predicate") == p
                and lk["id"] not in st.owned_ids]

    def _validate(self, st: ScopeState, edit: _Edit) -> None:
        scope = self.t.scope
        if edit.operation == "node_patch":
            row = st.final.get(edit.ids[0])
            if row is None:
                raise FileNotFoundError(f"Node not found: {edit.ids[0]}")
            if not lw.entity_matches(row, lw.entity_spec(scope, edit.before["node"])):  # type: ignore[arg-type]
                raise StaleSave(f"node {edit.ids[0]!r} changed since this copy was read; reload and edit again")
        elif edit.operation == "relationship_remove":
            edge = edit.before["edge"]
            if not self._owned(st, edge) and self._foreign(st, edge):  # type: ignore[arg-type]
                raise ValueError("Only relationships created by F2W can be removed")
        elif edit.operation == "snippet_unlink":
            if not self._owned(st, edit.before["edge"]):  # type: ignore[arg-type]
                raise StaleSave("the snippet link was already removed; reload and edit again")
        else:
            for node_id in edit.ids:
                if node_id not in st.final and not (edit.operation == "snippet_upsert"
                                                    and node_id == edit.after["node"].get("id")):  # type: ignore[attr-defined]
                    raise FileNotFoundError(f"Node not found: {node_id}")

    # -- journaling ----------------------------------------------------------------------------------------------
    def _new_record(self, ctx: RequestContext, edit: _Edit, base: str) -> ej.EditRecord:
        eid = ej.derive_edit_id(target=self.t.target, scope=(self.t.scope,), base_snapshot_id=base,
                                operation=edit.operation, ids=edit.ids, before=edit.before, after=edit.after)
        return ej.EditRecord(edit_id=eid, seq=1, state="recorded", target=self.t.target, scope=(self.t.scope,),
                             base_snapshot_id=self.t.base_snapshot_id, base_graph_sha256=self.t.base_graph_sha256,
                             profile=ctx.profile.value, mode=ctx.mode.value, actor=ctx.principal.subject,
                             operation=edit.operation, ids=edit.ids,  # type: ignore[arg-type]
                             before=edit.before, after=edit.after, recorded_at=self.t.clock())

    def _record(self, ctx: RequestContext, edit: _Edit) -> ej.EditRecord | None:
        """Durably record the edit (fsync).

        Returns the record to apply, or ``None`` when it is a retry of a completed Save.
        """
        for n in range(1000):
            base = self.t.base_snapshot_id if n == 0 else f"{self.t.base_snapshot_id}~{n}"
            fresh = self._new_record(ctx, edit, base)
            current, _created = self.t.journal.record(fresh)
            if current.state in ("recorded", "failed"):
                return current
            if current.state == "reverted":     # a retry of a Save the recovery job reverted: run it again
                return self.t.journal.advance(current.edit_id, "recorded", self.t.clock(),
                                              note="retried after recovery reverted it")
            if current.state in ("applied", "acknowledged", "reconciled", "resolved"):
                if self._effect_present(read_scope_state(self.t.client, self.t.scope, double_read=False), edit):
                    return None     # a retry of a completed Save
                continue            # the graph moved on since: a legitimate repeat gets a salted id
            if current.state == "conflict":
                raise StaleSave(f"edit {current.edit_id} is in conflict; an admin must resolve it")
        raise ContractError(ErrorCode.INTERNAL, "could not allocate an edit id")

    def _completed_retry(self, st: ScopeState, edit: _Edit) -> bool:
        """The same content was already saved and its effect is on Tiled: a retry of a completed Save."""
        eid = ej.derive_edit_id(target=self.t.target, scope=(self.t.scope,), base_snapshot_id=self.t.base_snapshot_id,
                                operation=edit.operation, ids=edit.ids, before=edit.before, after=edit.after)
        current = self.t.journal.latest().get(eid)
        return (current is not None and current.state in ("applied", "acknowledged", "reconciled", "resolved")
                and self._effect_present(st, edit))

    def _effect_present(self, st: ScopeState, edit: _Edit) -> bool:
        if edit.operation == "node_patch":
            row = st.final.get(edit.ids[0])
            return row is not None and lw.entity_matches(row, lw.entity_spec(self.t.scope, edit.after["node"]))  # type: ignore[arg-type]
        if edit.operation in ("relationship_add", "snippet_upsert"):
            return bool(self._owned(st, edit.after["edge"]))  # type: ignore[arg-type]
        return not self._owned(st, edit.before["edge"])  # type: ignore[arg-type]

    # -- applying -------------------------------------------------------------------------------------------------------
    def _apply(self, ctx: RequestContext, record: ej.EditRecord) -> None:
        if record.operation == "node_patch":
            try:
                sr.execute_save(ctx, record, journal=self.t.journal, client=self.t.client, settings=self.t.settings,
                                state_dir=self.t.state_dir, clock=self.t.clock, access_blob=self.t.access_blob,
                                namespace=self.t.namespace, fault=self.t.fault)
            except (StaleRevision, ContractError) as exc:
                current = self.t.journal.latest()[record.edit_id]
                if current.state == "recorded":     # refused before any Tiled write: the edit never happened
                    self.t.journal.advance(record.edit_id, "reverted", self.t.clock(),
                                           note=f"not applied: {str(exc)[:160]}")
                if isinstance(exc, StaleRevision):
                    raise StaleSave(str(exc)) from None
                raise
            return
        self._apply_link_edit(record)

    def _apply_link_edit(self, record: ej.EditRecord) -> None:
        """Single idempotent create/delete; roll-forward is the recovery for a partial one."""
        scope, fault = self.t.scope, self.t.fault or (lambda _s, _i: None)
        access = dict(self.t.access_blob or {})
        wrote = False
        lease = Lease(self._root() / "lease.json", self.t.clock, 60.0).acquire()
        try:
            st = read_scope_state(self.t.client, scope, double_read=False)
            if record.operation in ("relationship_add", "snippet_upsert"):
                edge = _normal_edge(record.after["edge"])  # type: ignore[arg-type]
                s, p, o = _triple(edge)
                if record.operation == "snippet_upsert":
                    node = record.after["node"]
                    node_id = node["id"]  # type: ignore[index]
                    if node_id not in st.final:
                        spec = lw.entity_spec(scope, node)  # type: ignore[arg-type]
                        wrote = True
                        lease.check()
                        tid = self.t.client.create_entity(entity_type=spec.entity_type, name=spec.name, uri=spec.uri,
                                                          properties=spec.properties, access_blob=access)
                        fault("snippet_entity_effect", 0)
                        st.final[node_id] = {"id": tid}
                ids = {n: st.final[n]["id"] for n in (s, o) if n in st.final}
                if s not in ids or o not in ids:
                    raise FileNotFoundError(f"Relationship endpoint not found: {s if s not in ids else o}")
                if not self._owned(st, edge):
                    link = lw.link_spec(scope, ida.edge_id(s, p, o), edge)
                    wrote = True
                    lease.check()
                    self.t.client.create_link(subject_id=ids[s], predicate=p, object_id=ids[o],
                                              properties=link.properties, access_blob=access)
                    fault("link_effect", 0)
            else:
                edge = _normal_edge(record.before["edge"])  # type: ignore[arg-type]
                for i, lk in enumerate(sorted(self._owned(st, edge), key=lambda r: str(r["id"]))):
                    wrote = True
                    lease.check()
                    self.t.client.delete_link(lk, scope)
                    fault("unlink_effect", i)
        except BaseException as exc:
            if isinstance(exc, Exception):
                state: ej.State = "failed" if wrote or (
                    isinstance(exc, ContractError) and exc.code == ErrorCode.UNAVAILABLE) else "reverted"
                self.t.journal.advance(record.edit_id, state, self.t.clock(),
                                       note=f"link edit stopped: {type(exc).__name__}: {str(exc)[:160]}")
            lease.release()
            raise
        self.t.journal.advance(record.edit_id, "applied", self.t.clock(), note="link edit applied")
        lease.release()

    # -- recovery ---------------------------------------------------------------------------------------------------------
    def recover(self, ctx: RequestContext) -> list[dict[str, Any]]:
        """Reconcile every half-done Save of the scope; idempotent (a second run returns ``[]``).

        * ``node_patch``: :func:`~.imports.save_recovery.recover_failed_saves` (full effect or pre-Save state);
          a ``recorded`` entry that never reached Tiled is applied now (its journal write was the durable intent).
        * link and snippet edits: rolled forward (every step is idempotent); an edit that can no longer be
          applied (an endpoint vanished) is ``reverted``.
        """
        self._check()
        self._refuse_during_switch()
        reports: list[dict[str, Any]] = []
        with self._lock, _writer_errors():
            reports += sr.recover_failed_saves(
                ctx, self.t.journal, client=self.t.client, settings=self.t.settings, state_dir=self.t.state_dir,
                clock=self.t.clock, scope=self.t.scope, access_blob=self.t.access_blob, namespace=self.t.namespace,
                fault=self.t.fault)
            root = self._root()
            for rec in sorted(self.t.journal.latest().values(), key=lambda r: r.edit_id):
                if rec.target != self.t.target or self.t.scope not in rec.scope or rec.state not in ("recorded", "failed"):
                    continue
                if rec.operation == "node_patch":
                    if rec.state == "recorded" and not sr._needs_recovery(rec, root):
                        reports.append(self._roll_forward(ctx, rec))
                elif rec.operation in _EDGE_OPS:
                    reports.append(self._roll_forward(ctx, rec))
        return reports

    def _roll_forward(self, ctx: RequestContext, rec: ej.EditRecord) -> dict[str, Any]:
        try:
            self._apply(ctx, rec)
        except (ContractError, FileNotFoundError, StaleSave) as exc:
            if isinstance(exc, ContractError) and exc.code in (ErrorCode.UNAVAILABLE, ErrorCode.WRITER_UNAUTHORIZED):
                raise
            current = self.t.journal.latest()[rec.edit_id]
            if current.state in ("recorded", "failed"):
                self.t.journal.advance(rec.edit_id, "reverted", self.t.clock(),
                                       note=f"recovery could not apply it: {str(exc)[:160]}")
            return {"edit_id": rec.edit_id, "outcome": "reverted", "detail": {"error": str(exc)[:160]}}
        return {"edit_id": rec.edit_id, "outcome": "applied", "detail": {"operation": rec.operation}}


def is_switch_refusal(exc: BaseException) -> bool:
    """True for the retryable ``CONFLICT`` that a switch in flight raises (Q8)."""
    return isinstance(exc, ContractError) and exc.code == ErrorCode.CONFLICT and exc.retryable
