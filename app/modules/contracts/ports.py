"""Ports: small structural protocols implemented by graph and document backends.

Backends advertise what they can do through :class:`GraphCapabilities`. An
operation a backend cannot perform raises :class:`~.errors.CapabilityError`; it
never returns a fake success. No cross-store transaction is implied (plan
section 3, ADR 0004).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .changes import ApprovalRecord, ChangePlan, ImportReceipt
from .context import RequestContext


class GraphCapabilities(BaseModel):
    """What a graph backend supports.

    Attributes:
        backend: Backend identifier (``"json"``, ``"tiled"``, ``"memory"``).
        complete_listing: Can enumerate every entity in scope (required before
            repair or deduplication planning).
        bounded_traversal: Supports neighborhood queries with hop/size limits.
        mutable_entity_properties: Entity properties can change after creation
            (Tiled Graph entities are create-only; edits project via new records/links).
        append_only_relations: Relations are added, never edited in place.
        batch_writes: Supports applying many creates/links in one request.
        compare_and_swap: Supports conditional writes against a revision.
        owned_deletion: Can delete records it created, scoped to its owner.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str
    complete_listing: bool = False
    bounded_traversal: bool = False
    mutable_entity_properties: bool = False
    append_only_relations: bool = True
    batch_writes: bool = False
    compare_and_swap: bool = False
    owned_deletion: bool = False


@runtime_checkable
class GraphPort(Protocol):
    """Scoped literature-graph access.

    Reads return graph-scoped records keyed by ``graph_id``; identities are never
    joined across graphs by display name (B27).
    """

    def capabilities(self) -> GraphCapabilities:
        """Return the backend's supported operations."""
        ...

    def get_entity(self, ctx: RequestContext, graph_id: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Return one entity in ``graph_id`` or ``None`` if absent in scope."""
        ...

    def find_entities(
        self, ctx: RequestContext, graph_id: str, query: str, *, limit: int
    ) -> Tuple[Sequence[Dict[str, Any]], bool]:
        """Return up to ``limit`` matches and whether the result is complete."""
        ...

    def neighborhood(
        self, ctx: RequestContext, graph_id: str, entity_id: str, *, hops: int, max_nodes: int
    ) -> Dict[str, Any]:
        """Return a bounded neighborhood with ``truncated`` set when capped."""
        ...

    def apply_plan(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> ImportReceipt:
        """Apply an approved plan and return a verified receipt, or raise."""
        ...


@runtime_checkable
class DocumentStore(Protocol):
    """Content-addressed store for private source artifacts.

    Artifacts are internal to authorized agent workflows; references are not
    public bearer URLs (D19).
    """

    def put(self, ctx: RequestContext, data: bytes, *, media_type: str, metadata: Mapping[str, str]) -> str:
        """Store bytes and return their content address (``sha256:<hex>``)."""
        ...

    def get(self, ctx: RequestContext, ref: str) -> bytes:
        """Return the bytes for ``ref`` after checking access and checksum."""
        ...

    def exists(self, ctx: RequestContext, ref: str) -> bool:
        """Return whether ``ref`` is stored and readable in scope."""
        ...

    def list_refs(self, ctx: RequestContext, *, prefix: str = "") -> Iterable[str]:
        """Yield stored refs, optionally filtered by ``prefix``."""
        ...
