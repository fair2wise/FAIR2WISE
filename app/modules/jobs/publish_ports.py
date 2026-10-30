"""Ports of the controlled publication job (T08B and T12/T13 fulfil them; T10 only defines and drives them).

The publication job is ``plan -> review -> revalidate -> import -> verify -> receipt -> activate``.
T10 owns the *job*: leases, audit, stale-approval handling, quarantine, the active pointer and
rollback. T10 does **not** implement the JSON-to-Tiled import: :class:`PublishStep` is the port
T08B fulfils (plan computation, an idempotent stable-ID ``apply``, read-back ``verify`` and the
target-side ``receipt``). Tests use a fake; no Tiled write happens here.

Contract for a :class:`PublishStep` implementation:

* ``apply`` must be idempotent under the stable ``operation_id`` and the plan's stable IDs: calling
  it again after a crash (entities written, links or receipt not) completes the scoped plan and
  creates no duplicate record. It must call ``guard()`` before each batch of effects so a worker
  that lost its lease stops writing.
* ``reconcile`` answers "did an earlier apply already land completely?" by reading the target; it
  returns the outcome or ``None`` and never writes.
* ``verify`` reads back from the target; a receipt is only valid for the target and revision it names.
* ``plan`` and ``apply`` perform no model, parser or acquisition work: the snapshot is already frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt, SnapshotManifest
from ..contracts.context import RequestContext


@dataclass(frozen=True)
class FrozenSnapshot:
    """A complete, immutable versioned JSON snapshot, already verified against its manifest.

    Attributes:
        ref: Stable reference the job uses (``"<corpus>/<snapshot id>"``).
        corpus: Corpus the snapshot covers.
        sha256: SHA-256 of the graph JSON bytes.
        manifest: The contract-level sidecar manifest.
        package_keys: Staged package keys this snapshot was built from (checked for completeness).
    """

    ref: str
    corpus: str
    sha256: str
    manifest: SnapshotManifest
    package_keys: tuple[str, ...] = ()


class SnapshotSource(Protocol):
    """Reads frozen snapshots. ``load`` raises when bytes no longer match the manifest (tampering)."""

    def load(self, ref: str) -> FrozenSnapshot:
        """Return the verified snapshot or raise."""
        ...


class RevisionProbe(Protocol):
    """Current schema/vocabulary revisions, read at review and import time."""

    def schema_fingerprint(self) -> str:
        """Fingerprint of the schema set in force now."""
        ...

    def vocabulary_revision(self) -> str:
        """Content revision of the vocabulary in force now."""
        ...


@dataclass(frozen=True)
class ApplyOutcome:
    """What ``apply`` did. ``complete`` is false for a partial application that must be resumed."""

    created: int = 0
    reconciled: int = 0
    id_mapping_sha256: str = ""
    effects: tuple[str, ...] = ()
    complete: bool = True

    def as_dict(self) -> dict[str, Any]:
        """Return the outcome as a plain dict (effects as a list)."""
        return {
            "created": self.created,
            "reconciled": self.reconciled,
            "id_mapping_sha256": self.id_mapping_sha256,
            "effects": list(self.effects),
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplyOutcome:
        """Rebuild an outcome from :meth:`as_dict` output; missing fields take their defaults."""
        return cls(
            int(data.get("created", 0)),
            int(data.get("reconciled", 0)),
            str(data.get("id_mapping_sha256", "")),
            tuple(data.get("effects") or ()),
            bool(data.get("complete", True)),
        )


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of reading the applied plan back from the target."""

    ok: bool
    detail: str = ""
    checked: int = 0
    target_revision: str = ""


class PublishStep(Protocol):
    """The importer port (fulfilled by T08B)."""

    adapter_version: str

    def plan(self, ctx: RequestContext, snapshot: FrozenSnapshot, target: str, *, operation_id: str) -> ChangePlan:
        """Compute the scoped plan of ``snapshot`` against ``target``'s current revision."""
        ...

    def target_revision(self, ctx: RequestContext, target: str) -> str:
        """Current revision of the target (compared with ``plan.base_revision`` before import)."""
        ...

    def apply(
        self,
        ctx: RequestContext,
        plan: ChangePlan,
        approval: ApprovalRecord,
        *,
        operation_id: str,
        guard: Callable[[], None],
    ) -> ApplyOutcome:
        """Apply the approved plan idempotently; may raise partway (a crash) and be called again."""
        ...

    def reconcile(self, ctx: RequestContext, plan: ChangePlan, *, operation_id: str) -> ApplyOutcome | None:
        """Return the outcome if an earlier apply landed completely, else ``None``. Read-only."""
        ...

    def verify(self, ctx: RequestContext, plan: ChangePlan, outcome: ApplyOutcome) -> VerifyOutcome:
        """Read back and check the target against the plan."""
        ...

    def receipt(
        self,
        ctx: RequestContext,
        plan: ChangePlan,
        approval: ApprovalRecord,
        outcome: ApplyOutcome,
        verification: VerifyOutcome,
    ) -> ImportReceipt:
        """Build (and persist target-side, if the adapter does) the receipt for this exact target and revision."""
        ...


@dataclass(frozen=True)
class IndexRef:
    """A built retrieval index paired with the graph state it serves."""

    ref: str
    snapshot_sha256: str
    receipt_id: str


class IndexBuilder(Protocol):
    """Builds and verifies the retrieval index for a verified (snapshot, receipt) pair."""

    def build(self, ctx: RequestContext, snapshot: FrozenSnapshot, receipt: ImportReceipt) -> IndexRef:
        """Build idempotently for the pair (a repeated call returns the same ref)."""
        ...

    def verify(self, ctx: RequestContext, index: IndexRef) -> bool:
        """Whether the index is complete and readable."""
        ...


@dataclass(frozen=True)
class PackageProblem:
    """An incomplete or failed staged package, with a reason a person can act on."""

    key: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


__all__ = (
    "ApplyOutcome",
    "FrozenSnapshot",
    "IndexBuilder",
    "IndexRef",
    "PackageProblem",
    "PublishStep",
    "RevisionProbe",
    "SnapshotSource",
    "VerifyOutcome",
)
