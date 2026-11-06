"""The publication job's real importer: corpus-scoped replacement, corpus removal and purge (T10B).

:class:`ReplacementPublishStep` fulfils the :class:`~.publish_ports.PublishStep` port over
:mod:`app.modules.graph.imports.corpus_scope` (stage, switch, retire, revive) and adds the operational
plans of :mod:`...corpus_purge` (retire-all, purge) through :meth:`plan_operation` /
:meth:`apply_operation`. It owns no policy: approvals, leases, audit and the active pointer stay in
:mod:`.publication`.

* **Rollback is a replacement.** A rollback to an earlier version is the same replacement plan with the
  older snapshot as source; the importer revives retired records, so the Tiled UUIDs come back.
* **Marker agreement (TIL-14).** :meth:`release_marker` reads the live release marker entity of a scope
  from Tiled; the job refuses to move F2W's pointer to a version the marker does not name.
* **Q14.** A foreign record that changes under a run is recorded in the receipt, never fatal;
  :meth:`report` surfaces it for the job's receipt stage.
* **Wiring decisions.** A plan is *safe* only without boundary links and without legacy adoption
  (:attr:`ReplacementPlan.is_safe`); legacy adoption is an explicit option of a submitted job and always
  needs an approval; a full purge after a retire-all needs ``include_previous`` as a second explicit choice.

Plans are persisted under ``<state_dir>/job-plans`` by plan hash so a restarted worker can resume.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from ..contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..graph import edit_journal as ej
from ..graph.backends.tiled_mutations import TiledMutationClient
from ..graph.imports import corpus_purge as cp
from ..graph.imports import corpus_scope as cs
from ..graph.imports.receipts import ReceiptArtifact, ReceiptStore, receipt_valid_for
from ..graph.imports.tiled_json import _canon, target_for
from ..security.authorization import DeploymentSettings
from .publish_ports import ApplyOutcome, FrozenSnapshot, VerifyOutcome

OPERATION_KINDS = ("retire", "purge")


class ReplacementPublishStep:
    """``PublishStep`` over the corpus-scoped importer, plus corpus removal and purge plans.

    Args:
        client: Owner-checked Tiled mutation client of the target.
        settings: Deployment settings (profile, writer identity, target binding).
        supported_schema_fingerprints: Schema import fingerprints a snapshot may carry.
        storage_kg: Root of the frozen snapshots (``<corpus>/<vN>/``).
        state_dir: Where receipts, leases, release state and persisted plans live.
        clock: Timezone-aware ``datetime`` source.
        namespace: Optional target namespace (``#namespace``).
        access_blob: Access blob written on records.
        edit_journal: Scoped edit journal checked for lost edits (T23C), or ``None``.
        edit_journals: Per-corpus journals (``scope -> journal``) used when ``edit_journal`` is ``None``; the
            ``:local`` image passes the Save journals so a publication never papers over an acknowledged edit.
    """

    adapter_version = cs.ADAPTER_VERSION
    #: The job must keep F2W's pointer and the Tiled release marker in step (TIL-14).
    has_release_marker = True
    #: ``plan`` accepts ``options`` (``adopt_legacy``, ``resolutions``).
    plan_options = True

    def __init__(self, *, client: TiledMutationClient, settings: DeploymentSettings,
                 supported_schema_fingerprints: Collection[str], storage_kg: Path, state_dir: Path,
                 clock: Callable[[], datetime], namespace: str | None = None,
                 access_blob: Mapping[str, Any] | None = None, edit_journal: ej.EditJournal | None = None,
                 edit_journals: Callable[[str], ej.EditJournal] | None = None) -> None:
        self.client, self.settings, self.fingerprints = client, settings, supported_schema_fingerprints
        self.storage_kg, self.state_dir, self.clock = Path(storage_kg), Path(state_dir), clock
        self.namespace, self.access_blob, self.edit_journal = namespace, dict(access_blob or {}), edit_journal
        self.edit_journals = edit_journals
        self.target = target_for(client, namespace)
        self.receipts = ReceiptStore(self.state_dir / "receipts")
        self._plans: dict[str, Any] = {}

    def _journal_for(self, scope: str) -> ej.EditJournal | None:
        """The edit journal the import of ``scope`` must not lose edits from, or ``None``."""
        if self.edit_journal is not None:
            return self.edit_journal
        return self.edit_journals(scope) if self.edit_journals is not None else None

    # --- plan persistence -------------------------------------------------------------------------

    def _plan_path(self, plan_sha256: str) -> Path:
        return self.state_dir / "job-plans" / f"{plan_sha256}.json"

    def _remember(self, kind: str, plan: Any) -> ChangePlan:  # noqa: ANN401  # Any: ReplacementPlan, RetireAllPlan or PurgePlan (no shared base class)
        self._plans[plan.change.plan_sha256] = (kind, plan)
        path = self._plan_path(plan.change.plan_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": kind, "plan": plan.to_json()}, sort_keys=True), encoding="utf-8")
        change: ChangePlan = plan.change
        return change

    def _recall(self, plan: ChangePlan) -> tuple[str, Any]:
        found = self._plans.get(plan.plan_sha256)
        if found is None:
            try:
                raw = json.loads(self._plan_path(plan.plan_sha256).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ContractError(ErrorCode.NOT_FOUND, "the detailed plan is not on record; re-plan") from None
            kind, data = raw["kind"], raw["plan"]
            if kind == "replacement":
                obj: Any = cs.ReplacementPlan.from_json(data)
            elif kind == "purge":
                obj = cp.PurgePlan.from_json(data)
            else:
                obj = cp.RetireAllPlan(ChangePlan.model_validate(data["change"]), json.loads(_canon(data["detail"])))
            found = (kind, obj)
            self._plans[plan.plan_sha256] = found
        kind, obj = found
        obj.verify_integrity()
        if obj.change != plan:
            raise ContractError(ErrorCode.STALE_APPROVAL, "the plan does not match the detailed plan on record")
        return kind, obj

    # --- PublishStep ------------------------------------------------------------------------------

    def source_dir(self, corpus: str, snapshot_id: str) -> Path:
        """Directory of a frozen snapshot."""
        return self.storage_kg / corpus / snapshot_id

    def plan(self, ctx: RequestContext, snapshot: FrozenSnapshot, target: str, *, operation_id: str,
             options: Mapping[str, Any] | None = None) -> ChangePlan:
        """Dry-run replacement plan of ``snapshot`` (a rollback is the same call with an older snapshot)."""
        opts = dict(options or {})
        if target != self.target:
            raise ContractError(ErrorCode.INVALID_INPUT, "the job's target is not this importer's target")
        resolutions = [ej.ConflictResolution.model_validate(r) for r in opts.get("resolutions") or ()]
        rp = cs.plan_replacement(
            self.source_dir(snapshot.corpus, snapshot.manifest.snapshot_id), client=self.client,
            settings=self.settings, supported_schema_fingerprints=self.fingerprints, namespace=self.namespace,
            access_blob=self.access_blob, adopt_legacy=bool(opts.get("adopt_legacy")),
            edit_journal=self._journal_for(snapshot.corpus), resolutions=resolutions, state_dir=self.state_dir)
        return self._remember("replacement", rp)

    def plan_is_safe(self, plan: ChangePlan) -> bool:
        """Whether ``plan`` is a *safe* replacement (no boundary links, no legacy adoption).

        Re-read from the persisted detail with its integrity checked. Anything else, including a retire or purge
        plan or a plan that is not on record, is not safe: T18E's auto-approval policy uses this and nothing else.
        """
        try:
            kind, obj = self._recall(plan)
        except ContractError:
            return False
        return bool(kind == "replacement" and obj.is_safe)

    def _scope_revision(self, scope: str, adopted: Sequence[str]) -> str:
        state = cs.read_scope_state(self.client, scope, adopted=tuple(adopted))
        return state.revision

    def plan_revision(self, ctx: RequestContext, plan: ChangePlan) -> str:
        """Current revision of the scope ``plan`` was computed against (compared with ``base_revision``)."""
        _kind, obj = self._recall(plan)
        adopted = obj.detail.get("adopted_graph_ids") or obj.detail.get("legacy_graph_ids") or ()
        return self._scope_revision(obj.scope, adopted)

    def target_revision(self, ctx: RequestContext, target: str) -> str:
        """Not meaningful without a scope; the job uses :meth:`plan_revision`."""
        raise ContractError(ErrorCode.INVALID_INPUT, "use plan_revision: revisions are per corpus scope")

    def apply(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord, *, operation_id: str,
              guard: Callable[[], None]) -> ApplyOutcome:
        """Apply the approved replacement; ``guard`` runs before every stage and switch write."""
        kind, rp = self._recall(plan)
        if kind != "replacement":
            raise ContractError(ErrorCode.INVALID_INPUT, f"{kind} plans are applied with apply_operation")
        out = cs.apply_replacement(
            ctx, rp, approval, source=self.source_dir(rp.scope, plan.snapshot_id), client=self.client,
            settings=self.settings, supported_schema_fingerprints=self.fingerprints, state_dir=self.state_dir,
            clock=self.clock, fault=lambda _stage, _i: guard())
        return self._outcome(out.receipt)

    @staticmethod
    def _outcome(art: ReceiptArtifact) -> ApplyOutcome:
        o = art.receipt.outcomes
        return ApplyOutcome(created=int(o.get("staged_entities", 0)) + int(o.get("created_links", 0)),
                            reconciled=int(o.get("reconciled_on_replay", 0)),
                            id_mapping_sha256=art.receipt.id_mapping_sha256,
                            effects=(f"receipt:{art.receipt.receipt_id}",), complete=True)

    def _valid_receipt(self, plan: ChangePlan, adopted: Sequence[str], scope: str) -> ReceiptArtifact | None:
        state = cs.read_scope_state(self.client, scope, adopted=tuple(adopted))
        for art in self.receipts.find(plan_sha256=plan.plan_sha256):
            if receipt_valid_for(art, target=plan.target, revision=state.revision):
                return art
        return None

    def reconcile(self, ctx: RequestContext, plan: ChangePlan, *, operation_id: str) -> ApplyOutcome | None:
        """The outcome when an earlier apply landed completely (a valid receipt exists), else ``None``."""
        _kind, obj = self._recall(plan)
        art = self._valid_receipt(plan, obj.detail.get("adopted_graph_ids") or (), obj.scope)
        return self._outcome(art) if art is not None else None

    def verify(self, ctx: RequestContext, plan: ChangePlan, outcome: ApplyOutcome) -> VerifyOutcome:
        """Independent read-back: the scope still has exactly the state the receipt verified."""
        _kind, obj = self._recall(plan)
        adopted = obj.detail.get("adopted_graph_ids") or ()
        state = cs.read_scope_state(self.client, obj.scope, adopted=tuple(adopted))
        for art in self.receipts.find(plan_sha256=plan.plan_sha256):
            if receipt_valid_for(art, target=plan.target, revision=state.revision):
                return VerifyOutcome(True, "", len(state.final), state.revision)
        return VerifyOutcome(False, "no receipt matches the target as read back now", 0, state.revision)

    def _artifact(self, plan: ChangePlan) -> ReceiptArtifact:
        found = self.receipts.find(plan_sha256=plan.plan_sha256)
        if not found:
            raise ContractError(ErrorCode.NOT_FOUND, "no receipt for this plan")
        return found[0]

    def receipt(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord, outcome: ApplyOutcome,
                verification: VerifyOutcome) -> ImportReceipt:
        """The importer's own receipt (already written to the receipt store with its extras)."""
        art = self._artifact(plan)
        return art.receipt if verification.ok else art.receipt.model_copy(update={"verified": False})

    def report(self, ctx: RequestContext, plan: ChangePlan, receipt: ImportReceipt) -> dict[str, Any]:
        """Receipt extras for the job's receipt stage: Q14 foreign changes, boundary links, revived records."""
        art = self._artifact(plan)
        return {"kind": art.extras.kind, "snapshot_id": art.extras.snapshot_id,
                "foreign_changes": list(art.extras.foreign_changes), "boundary_links": art.extras.boundary_links,
                "adopted_graph_ids": list(art.extras.adopted_graph_ids),
                "revived_entities": int(art.receipt.outcomes.get("revived_entities", 0)),
                "read_probes_equal": bool((art.extras.read_probes or {}).get("equal", True)),
                "release_marker_id": art.extras.release_marker_id}

    # --- release marker (TIL-14) -------------------------------------------------------------------

    def release_marker(self, ctx: RequestContext, scope: str) -> dict[str, Any] | None:
        """The live release marker of ``scope`` as Tiled holds it, or ``None`` (no live version)."""
        state = cs.read_scope_state(self.client, scope, adopted=())
        if state.release is None:
            return None
        props: Mapping[str, Any] = state.release.get("properties") or {}
        return {"snapshot_id": props.get("snapshot_id"), "graph_sha256": props.get("graph_sha256"),
                "plan_sha256": props.get("plan_sha256"), "receipt_id": props.get("receipt_id")}

    # --- corpus removal and purge -------------------------------------------------------------------

    def plan_operation(self, ctx: RequestContext, operation: str, params: Mapping[str, Any]) -> ChangePlan:
        """Plan ``retire`` (corpus removal; the corpus name typed in) or ``purge`` (separate approval, Q3)."""
        scope = str(params["scope"])
        if operation == "retire":
            resolutions = [ej.ConflictResolution.model_validate(r) for r in params.get("resolutions") or ()]
            rplan = cp.plan_retire_all(client=self.client, settings=self.settings, scope=scope,
                                       confirm_scope=str(params.get("confirm_scope", "")), namespace=self.namespace,
                                       access_blob=self.access_blob, edit_journal=self._journal_for(scope),
                                       resolutions=resolutions)
            return self._remember("retire", rplan)
        if operation == "purge":
            pplan = cp.plan_purge(client=self.client, settings=self.settings, scope=scope, state_dir=self.state_dir,
                                  namespace=self.namespace, include_previous=bool(params.get("include_previous")))
            return self._remember("purge", pplan)
        raise ContractError(ErrorCode.INVALID_INPUT, f"unknown scope operation {operation!r}")

    def apply_operation(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord, *,
                        guard: Callable[[], None],
                        pointer_to_none: Callable[[], Any] | None = None) -> tuple[ApplyOutcome, ImportReceipt]:
        """Apply an approved retire-all or purge plan."""
        kind, obj = self._recall(plan)
        fault = (lambda _stage, _i: guard())
        if kind == "retire":
            out = cp.apply_retire_all(ctx, obj, approval, client=self.client, settings=self.settings,
                                      state_dir=self.state_dir, clock=self.clock, pointer_to_none=pointer_to_none,
                                      fault=fault)
        elif kind == "purge":
            out = cp.apply_purge(ctx, obj, approval, client=self.client, settings=self.settings,
                                 state_dir=self.state_dir, clock=self.clock, fault=fault)
        else:
            raise ContractError(ErrorCode.INVALID_INPUT, "replacement plans are applied with apply")
        return self._outcome(out.receipt), out.receipt.receipt

    def operation_revision(self, ctx: RequestContext, plan: ChangePlan) -> str:
        """Current revision for a retire or purge plan (``base_revision`` comparison)."""
        return self.plan_revision(ctx, plan)

    def operation_report(self, plan: ChangePlan) -> dict[str, Any]:
        """Extras of the receipt of a retire or purge plan."""
        art = self._artifact(plan)
        return {"kind": art.extras.kind, "foreign_changes": list(art.extras.foreign_changes),
                "boundary_links": art.extras.boundary_links,
                "outcomes": dict(art.receipt.outcomes)}


__all__ = ("OPERATION_KINDS", "ReplacementPublishStep")
