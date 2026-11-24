"""In-memory fakes for contract and parity tests.

The fake chat clients match the *existing* F2W client signatures structurally
(``term_extractor.clients.ChatClient.chat`` and ``kg_rag_api.ChatClient.chat``)
without importing them, so tests can stand in for CBORG/Ollama without a model
or network. Replies are deterministic: a scripted queue, then a fixed default.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
import hashlib
from typing import Any

from .changes import ApprovalRecord, ChangePlan, ImportReceipt
from .context import RequestContext
from .errors import CapabilityError, ContractError, ErrorCode
from .ports import GraphCapabilities


class FakeSyncChatClient:
    """Deterministic stand-in for ``term_extractor.clients.ChatClient``.

    Args:
        replies: Replies returned in order; ``default`` once exhausted.
        default: Reply used after ``replies`` run out.
        model: Reported model name.
    """

    def __init__(self, replies: Sequence[str] = (), *, default: str = "", model: str = "fake-model") -> None:
        self.model = model
        self._replies = list(replies)
        self._default = default
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: str | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        num_ctx: int | None = None,
    ) -> str:
        """Record the call and return the next scripted reply."""
        self.calls.append(
            {"prompt": prompt, "temperature": temperature, "timeout": timeout, "trace_label": trace_label}
        )
        if system is not None or max_tokens is not None or num_ctx is not None:  # recorded only when given
            self.calls[-1].update({"system": system, "max_tokens": max_tokens, "num_ctx": num_ctx})
        return self._replies.pop(0) if self._replies else self._default


class FakeAsyncChatClient:
    """Deterministic stand-in for the async ``kg_rag_api.ChatClient`` protocol.

    Args:
        replies: Replies returned in order; ``default`` once exhausted.
        default: Reply used after ``replies`` run out.
        model: Reported model name.
    """

    def __init__(self, replies: Sequence[str] = (), *, default: str = "", model: str = "fake-model") -> None:
        self.model = model
        self._replies = list(replies)
        self._default = default
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: Sequence[dict[str, str]]) -> str:
        """Record the messages and return the next scripted reply."""
        self.calls.append([dict(m) for m in messages])
        return self._replies.pop(0) if self._replies else self._default


class InMemoryGraphPort:
    """Graph-scoped in-memory backend implementing :class:`~.ports.GraphPort`.

    Args:
        graphs: Mapping of ``graph_id`` to ``{entity_id: entity_dict}``.
        capabilities: Capabilities to advertise; writes require ``batch_writes``.
    """

    def __init__(
        self,
        graphs: Mapping[str, Mapping[str, dict[str, Any]]] | None = None,
        *,
        capabilities: GraphCapabilities | None = None,
    ) -> None:
        self._graphs: dict[str, dict[str, dict[str, Any]]] = {g: dict(e) for g, e in (graphs or {}).items()}
        self._caps = capabilities or GraphCapabilities(
            backend="memory", complete_listing=True, bounded_traversal=True
        )
        self.receipts: list[ImportReceipt] = []

    def capabilities(self) -> GraphCapabilities:
        """Return the advertised capabilities."""
        return self._caps

    def _scoped(self, ctx: RequestContext, graph_id: str) -> dict[str, dict[str, Any]]:
        if ctx.corpus_scope and graph_id not in ctx.corpus_scope:
            raise ContractError(ErrorCode.DENIED, f"graph {graph_id!r} is outside the request scope")
        return self._graphs.get(graph_id, {})

    def get_entity(self, ctx: RequestContext, graph_id: str, entity_id: str) -> dict[str, Any] | None:
        """Return one entity or ``None``."""
        entity = self._scoped(ctx, graph_id).get(entity_id)
        return dict(entity, graph_id=graph_id) if entity is not None else None

    def find_entities(
        self, ctx: RequestContext, graph_id: str, query: str, *, limit: int
    ) -> tuple[Sequence[dict[str, Any]], bool]:
        """Return label matches and whether the listing is complete."""
        needle = query.lower()
        hits = [
            dict(e, id=i, graph_id=graph_id)
            for i, e in sorted(self._scoped(ctx, graph_id).items())
            if needle in str(e.get("label", "")).lower()
        ]
        return hits[:limit], len(hits) <= limit

    def neighborhood(
        self, ctx: RequestContext, graph_id: str, entity_id: str, *, hops: int, max_nodes: int
    ) -> dict[str, Any]:
        """Return a breadth-first neighborhood over ``links`` lists."""
        if not self._caps.bounded_traversal:
            raise CapabilityError("neighborhood", self._caps.backend)
        graph = self._scoped(ctx, graph_id)
        seen, frontier = [entity_id], [entity_id]
        for _ in range(hops):
            nxt = []
            for node in frontier:
                for other in graph.get(node, {}).get("links", []):
                    if other not in seen:
                        seen.append(other)
                        nxt.append(other)
            frontier = nxt
        truncated = len(seen) > max_nodes
        return {"graph_id": graph_id, "nodes": seen[:max_nodes], "truncated": truncated}

    def apply_plan(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> ImportReceipt:
        """Record a receipt for an approved plan; raises if unsupported or not approved."""
        if not self._caps.batch_writes:
            raise CapabilityError("apply_plan", self._caps.backend)
        now = datetime.now(UTC)
        if not approval.authorizes(plan, now):
            raise ContractError(ErrorCode.STALE_APPROVAL, "approval does not cover this plan")
        mapping = hashlib.sha256("\n".join(plan.affected_ids).encode()).hexdigest()
        receipt = ImportReceipt(
            receipt_id=f"receipt-{len(self.receipts) + 1}",
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            approval_id=approval.approval_id,
            snapshot_sha256=plan.input_sha256,
            target=plan.target,
            executed_as=approval.executed_as,
            adapter_version="memory-0",
            id_mapping_sha256=mapping,
            outcomes={"created": plan.creates, "linked": plan.links},
            verified=True,
            completed_at=now,
        )
        self.receipts.append(receipt)
        return receipt


class InMemoryDocumentStore:
    """Content-addressed in-memory store implementing :class:`~.ports.DocumentStore`."""

    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str, dict[str, str]]] = {}

    def put(self, ctx: RequestContext, data: bytes, *, media_type: str, metadata: Mapping[str, str]) -> str:
        """Store ``data`` under its SHA-256 address."""
        ref = "sha256:" + hashlib.sha256(data).hexdigest()
        self._blobs.setdefault(ref, (bytes(data), media_type, dict(metadata)))
        return ref

    def get(self, ctx: RequestContext, ref: str) -> bytes:
        """Return bytes after verifying the checksum."""
        if ref not in self._blobs:
            raise ContractError(ErrorCode.NOT_FOUND, f"no artifact {ref}")
        data = self._blobs[ref][0]
        if "sha256:" + hashlib.sha256(data).hexdigest() != ref:
            raise ContractError(ErrorCode.INTERNAL, f"checksum mismatch for {ref}")
        return data

    def exists(self, ctx: RequestContext, ref: str) -> bool:
        """Return whether ``ref`` is stored."""
        return ref in self._blobs

    def list_refs(self, ctx: RequestContext, *, prefix: str = "") -> Iterable[str]:
        """Yield stored refs in sorted order."""
        return iter(sorted(r for r in self._blobs if r.startswith(prefix)))
