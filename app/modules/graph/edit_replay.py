"""Replay the edit journal onto a rebuilt graph (T23C; editing plan 2.1.4, json_kg_contract.md 6.2.3).

A rebuild from new extraction starts from fresh content, so edits carried by the previous version would
vanish. :func:`replay_edits` re-applies every edit the journal still owes the scope and guarantees that
each one is either *kept* (applied, or already present in the rebuilt graph) or recorded as a
:class:`ReplayConflict`; none is silently dropped. :func:`record_conflicts` writes the conflicts back to the
journal (state ``conflict``) so a later import is blocked until a reviewed ``carried``/``discarded``
resolution exists.

Edits replayed: ``acknowledged``/``applied``/``failed``/``conflict``/``reconciled`` records of the scope (a
``reconciled`` edit is replayed so a rebuild keeps it even though its parent snapshot carried it). Never
replayed: ``recorded`` (never acknowledged), ``reverted``, ``resolved``.

Supported payloads: ``node_patch`` with ``before.node``/``after.node`` or flat field maps;
``relationship_add``/``relationship_remove`` with ``edge`` payloads; ``snippet_upsert`` (``after.node`` is
created when absent, ``after.edge`` is added; T18D) and ``snippet_unlink`` (``before.edge`` is removed, the
snippet node stays). Any other operation is a conflict (``unsupported_operation``), not a skip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .edit_journal import EditJournal, EditRecord

REPLAYED_STATES = frozenset({"acknowledged", "applied", "failed", "conflict", "reconciled"})
#: T18D: edits whose effect the effective record must show although no snapshot carries them yet: a Save
#: journaled but not finished (``recorded``, ``failed``) and one finished (``applied``, ``acknowledged``).
#: ``reverted`` and ``resolved`` are never shown; ``reconciled`` edits are already in the active snapshot.
IN_FLIGHT_STATES = frozenset({"recorded", "applied", "acknowledged", "failed"})
_MISSING = object()


@dataclass(frozen=True)
class ReplayConflict:
    """One edit that could not be replayed onto the rebuilt graph."""

    edit_id: str
    reason: str
    detail: str


@dataclass
class ReplayResult:
    """Outcome of one replay: the new graph plus a complete account of every edit considered."""

    graph: dict[str, Any]
    kept: list[str] = field(default_factory=list)       # applied now
    already: list[str] = field(default_factory=list)    # rebuilt graph already had the effect
    conflicts: list[ReplayConflict] = field(default_factory=list)

    @property
    def accounted(self) -> list[str]:
        """Every edit id with an outcome (kept, already present or conflict)."""
        return sorted({*self.kept, *self.already, *(c.edit_id for c in self.conflicts)})


def _fields(before: Mapping[str, object], after: Mapping[str, object]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    b, a = before.get("node", before), after.get("node", after)
    if not isinstance(b, Mapping) or not isinstance(a, Mapping):
        return None
    keys = sorted(k for k in {*b, *a} if b.get(k, _MISSING) != a.get(k, _MISSING))
    return ({k: b.get(k, _MISSING) for k in keys}, {k: a.get(k, _MISSING) for k in keys})


def _node_key(graph: dict[str, Any]) -> str:
    return "things" if "things" in graph or "nodes" not in graph else "nodes"


def _edge_key(graph: dict[str, Any]) -> str:
    return "associations" if "associations" in graph or "edges" not in graph else "edges"


def _apply_node_patch(graph: dict[str, Any], rec: EditRecord) -> tuple[str, ReplayConflict | None]:
    changed = _fields(rec.before, rec.after)
    if changed is None or len(rec.ids) != 1:
        return "conflict", ReplayConflict(rec.edit_id, "invalid_payload", "node_patch needs one id and field maps")
    node = next((n for n in graph.get(_node_key(graph), []) if isinstance(n, dict) and n.get("id") == rec.ids[0]),
                None)
    if node is None:
        return "conflict", ReplayConflict(rec.edit_id, "node_missing",
                                          f"node {rec.ids[0]!r} no longer exists in the rebuilt graph")
    before, after = changed
    if all(node.get(k, _MISSING) == v for k, v in after.items()):
        return "already", None
    clashes = sorted(k for k, v in before.items() if node.get(k, _MISSING) not in (v, after[k]))
    if clashes:
        return "conflict", ReplayConflict(rec.edit_id, "field_changed",
                                          f"the rebuild changed {clashes} of {rec.ids[0]!r} independently")
    for key, value in after.items():
        if value is _MISSING:
            node.pop(key, None)
        else:
            node[key] = copy.deepcopy(value)
    return "kept", None


def _edge_payload(rec: EditRecord, side: str) -> dict[str, Any] | None:
    payload = (rec.after if side == "after" else rec.before).get("edge")
    if isinstance(payload, Mapping) and all(k in payload for k in ("subject", "predicate", "object")):
        return dict(payload)
    return None


def _apply_edge(graph: dict[str, Any], rec: EditRecord) -> tuple[str, ReplayConflict | None]:
    add = rec.operation in ("relationship_add", "snippet_upsert")
    edge = _edge_payload(rec, "after" if add else "before")
    if edge is None:
        return "conflict", ReplayConflict(rec.edit_id, "invalid_payload", f"{rec.operation} needs an edge payload")
    nodes = {n.get("id") for n in graph.get(_node_key(graph), []) if isinstance(n, dict)}
    triple = (edge["subject"], edge["predicate"], edge["object"])
    edges = graph.setdefault(_edge_key(graph), [])
    present = [e for e in edges if isinstance(e, dict)
               and (e.get("subject"), e.get("predicate"), e.get("object")) == triple]
    if add:
        if present:
            return "already", None
        if edge["subject"] not in nodes or edge["object"] not in nodes:
            return "conflict", ReplayConflict(rec.edit_id, "endpoint_missing",
                                              f"an endpoint of {triple} no longer exists in the rebuilt graph")
        edges.append(copy.deepcopy(edge))
        return "kept", None
    if not present:
        return "already", None
    edges[:] = [e for e in edges if e not in present]
    return "kept", None


def _apply_snippet_upsert(graph: dict[str, Any], rec: EditRecord) -> tuple[str, ReplayConflict | None]:
    node = rec.after.get("node")
    if not isinstance(node, Mapping) or not node.get("id") or _edge_payload(rec, "after") is None:
        return "conflict", ReplayConflict(rec.edit_id, "invalid_payload", "snippet_upsert needs after.node and after.edge")
    nodes = graph.setdefault(_node_key(graph), [])
    created = not any(isinstance(n, dict) and n.get("id") == node["id"] for n in nodes)
    if created:
        nodes.append(copy.deepcopy(dict(node)))
    # The edge endpoint check of _apply_edge would reject a snippet whose node was just added only if the
    # subject is gone; that is a genuine conflict.
    outcome, conflict = _apply_edge(graph, rec)
    if conflict is not None:
        if created:
            nodes[:] = [n for n in nodes if not (isinstance(n, dict) and n.get("id") == node["id"])]
        return outcome, conflict
    return ("kept" if created or outcome == "kept" else "already"), None


def replay_edits(graph: Mapping[str, Any], records: Sequence[EditRecord], *,
                 states: frozenset[str] = REPLAYED_STATES, ordered: bool = False) -> ReplayResult:
    """Apply ``records`` (oldest first by ``recorded_at``/``seq``) to a deep copy of ``graph``.

    Every record in ``states`` (default :data:`REPLAYED_STATES`) ends in exactly one of ``kept``, ``already``
    or ``conflicts``. ``states=IN_FLIGHT_STATES`` builds the effective record (T18D). ``ordered=True`` keeps the
    order of ``records`` (the journal's creation order) instead of sorting by the time of each edit's latest
    state: a recovery that finishes an old edit late must not reorder it behind a newer one.
    """
    out = copy.deepcopy(dict(graph))
    result = ReplayResult(graph=out)
    chosen = [r for r in records if r.state in states]
    for rec in chosen if ordered else sorted(chosen, key=lambda r: (r.recorded_at, r.edit_id)):
        if rec.operation == "node_patch":
            outcome, conflict = _apply_node_patch(out, rec)
        elif rec.operation == "snippet_upsert":
            outcome, conflict = _apply_snippet_upsert(out, rec)
        elif rec.operation in ("relationship_add", "relationship_remove", "snippet_unlink"):
            outcome, conflict = _apply_edge(out, rec)
        else:
            outcome, conflict = "conflict", ReplayConflict(rec.edit_id, "unsupported_operation",
                                                           f"{rec.operation} cannot be replayed automatically")
        if conflict is not None:
            result.conflicts.append(conflict)
        elif outcome == "kept":
            result.kept.append(rec.edit_id)
        else:
            result.already.append(rec.edit_id)
    return result


def replay_journal(graph: Mapping[str, Any], journal: EditJournal, *, target: str,
                   scope: Sequence[str], states: frozenset[str] = REPLAYED_STATES) -> ReplayResult:
    """:func:`replay_edits` over the journal's current records for ``target`` and ``scope``."""
    wanted = set(scope)
    records = [r for r in journal.latest().values() if r.target == target and set(r.scope) & wanted]
    return replay_edits(graph, records, states=states, ordered=True)


def record_conflicts(journal: EditJournal, result: ReplayResult, at: datetime) -> list[str]:
    """Append state ``conflict`` (with the reason as note) for each conflict not already in that state."""
    latest = journal.latest()
    done = []
    for c in result.conflicts:
        cur = latest.get(c.edit_id)
        if cur is not None and cur.state != "conflict":
            journal.advance(c.edit_id, "conflict", at, note=f"{c.reason}: {c.detail}")
            done.append(c.edit_id)
    return done
