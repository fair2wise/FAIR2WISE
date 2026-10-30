"""Audit event model for graph writes and approvals (K-13).

An event is small, redacted and bounded: credentials never appear, large payloads are replaced
by a digest and a truncation flag. ``critical`` events record mutations (graph writes, approvals,
publication steps): if one cannot be made durable the mutation does not run (see
:mod:`.recorder`).

Adapted from als-computing/forge_rsoxs_analysis src/forge/kg/recorder.py (``GraphRecorder``
event surface) (BSD-3-Clause); the donor's HTTP writes to the graph service are replaced by a
durable local sink with the graph projection as an optional, lagging consumer.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any, Mapping
import uuid

from pydantic import BaseModel, ConfigDict, Field

EVENT_VERSION = 1
MAX_STRING = 500
MAX_ITEMS = 40
MAX_DEPTH = 4
_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|passw(or)?d|credential|authorization|cookie)", re.I)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9]{12,}|Bearer\s+\S+|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+)")


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Copy of ``value`` with secrets removed and size bounded."""
    if _depth > MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (name, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out["_truncated_items"] = len(value) - MAX_ITEMS
                break
            out[str(name)] = "[redacted]" if _SECRET_KEY.search(str(name)) else redact(item, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:MAX_ITEMS]
        out_list = [redact(i, _depth=_depth + 1) for i in items]
        if len(value) > MAX_ITEMS:
            out_list.append(f"[+{len(value) - MAX_ITEMS} more]")
        return out_list
    if isinstance(value, str):
        text = _SECRET_VALUE.sub("[redacted]", value)
        return text if len(text) <= MAX_STRING else text[:MAX_STRING] + "[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value), _depth=_depth + 1)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class GraphWriteEvent(BaseModel):
    """One audit event.

    Attributes:
        event_id: Unique ID (retries of the same write reuse the operation id, not the event id).
        kind: ``graph_write``, ``approval``, ``import_step``, ``activation``, ``provenance``...
        phase: ``intent`` before the mutation, ``outcome`` after it, or ``note``.
        operation_id: Stable ID of the operation this event belongs to.
        actor: Subject that caused it (never a credential).
        run_id: Job/run it belongs to.
        target: Graph or target the write affects.
        critical: Whether a failure to persist this event must block the mutation.
        status: ``ok``, ``failed`` or ``started``.
        payload: Redacted, bounded detail.
        payload_sha256: Digest of the *unredacted-but-secret-free* payload before bounding.
        at: Event time (UTC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = EVENT_VERSION
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: str
    phase: str = "note"
    operation_id: str
    actor: str
    run_id: str | None = None
    target: str = ""
    critical: bool = False
    status: str = "ok"
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_sha256: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def build(cls, *, payload: Mapping[str, Any] | None = None, **fields: Any) -> GraphWriteEvent:
        """Construct with the payload redacted and digested."""
        body = dict(payload or {})
        safe = redact(body)
        return cls(payload=safe, payload_sha256=_digest(safe), **fields)


__all__ = ("EVENT_VERSION", "GraphWriteEvent", "redact")
