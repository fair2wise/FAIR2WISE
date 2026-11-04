"""Trusted wiring of the ``:local`` image's own publication target (owner decision 2026-11-04, ninth set).

"I want both :local-only publish into container's own tiled, and import exported json through the
existing json-to-tiled import path". The ``:local`` image runs one Tiled on loopback under its
supervisor (``F2W_TILED_PORT``, default 8765). This module is the only place that adds a target to the
H5 publication allowlist, and it adds exactly one: that loopback Tiled, when

* the deployment profile is ``:local`` (never ``test``, never an ALS profile), and
* the managed Tiled the image runs resolves to the very base URL the deployment's settings name
  (the same normalized host and port ``target_in_deployment`` compares against).

Nothing here is reachable from a request, a setting or an environment value that names a *target*:
the environment only chooses the port the image's own managed Tiled listens on, and the result must
equal ``DeploymentSettings.tiled_target`` or nothing is wired and the gate stays empty. Central and ALS
Tiled targets are never in the allowlist (an ALS profile gets an empty gate, whatever the environment
says), and the gate still refuses every other target with 403 (not on this deployment) or 501 (H5).

The writer credential is read the way the managed lifecycle already reads it
(:func:`~app.modules.deployment.tiled_lifecycle.writer_credential`, lazily, per request, from the
private secret file under the Tiled data directory). No new secret path exists and nothing is logged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
import time

from ..modules.contracts.context import DeploymentProfile, RequestContext
from ..modules.deployment import tiled_lifecycle
from ..modules.deployment.tiled_config import ManagedTiledConfig, TiledConfigError, load_tiled_settings
from ..modules.graph.backends.tiled_mutations import TiledMutationClient
from ..modules.graph.imports import tiled_json as tj
from ..modules.graph.snapshot_manifest import SNAPSHOT_ID_RE
from ..modules.jobs.publication import Publication, PublicationDeps
from ..modules.jobs.publish_ports import FrozenSnapshot, IndexRef
from ..modules.jobs.publish_store import PublishStore
from ..modules.jobs.replacement_step import ReplacementPublishStep
from ..modules.jobs.service import JobService
from ..modules.jobs.snapshot_source import DirectorySnapshotSource
from ..modules.provenance.recorder import AuditLog, AuditRecorder
from ..modules.schema.registry import SchemaRegistry
from ..modules.security.tiled_identity import normalize_tiled_target
from ..modules.security.authorization import (
    DeploymentSettings,
    local_operator_context,
    verify_approval,
    worker_context,
)
from .operations import F2WOperations, PublicationGate
from .post_extraction import PostExtractionPublisher, snapshot_freezer

logger = logging.getLogger(__name__)

#: Directory (under the session workdir) of the frozen snapshots this deployment publishes from.
SNAPSHOTS_DIRNAME = "storage_kg"
#: Directory (under the operations root) of importer receipts, leases, release state and persisted plans.
STATE_DIRNAME = "publication-state"
VOCABULARY_REVISION = "local-unversioned"


def local_tiled_target(settings: DeploymentSettings, managed: ManagedTiledConfig | None) -> str | None:
    """The one target the ``:local`` gate may allow, or ``None`` (nothing is allowed).

    ``None`` unless the profile is exactly ``:local``, a managed Tiled is configured, and its loopback
    base URL equals ``settings.tiled_target``.
    """
    if settings.profile is not DeploymentProfile.LOCAL or managed is None or managed.profile != "local":
        return None
    own = f"http://{managed.host}:{managed.port}"
    try:
        own = normalize_tiled_target(own)
    except ValueError:
        return None
    return own if own == settings.tiled_target else None


def local_publication_gate(settings: DeploymentSettings, managed: ManagedTiledConfig | None) -> PublicationGate:
    """The H5 gate for this deployment: the image's own Tiled on ``:local``, otherwise empty."""
    target = local_tiled_target(settings, managed)
    return PublicationGate(frozenset({target})) if target else PublicationGate()


class LocalRevisionProbe:
    """Schema/vocabulary revisions in force on ``:local``.

    The schema revision is a digest of the import fingerprints of the registered schemas; the vocabulary
    has no separate revision on the image yet, so it is a fixed label (an approval still binds to it).
    """

    def __init__(self, fingerprints: frozenset[str]) -> None:
        self._schema = hashlib.sha256("\n".join(sorted(fingerprints)).encode()).hexdigest()

    def schema_fingerprint(self) -> str:
        """Digest of the supported schema fingerprints."""
        return self._schema

    def vocabulary_revision(self) -> str:
        """Fixed label: no separately versioned vocabulary on ``:local``."""
        return VOCABULARY_REVISION


class LocalIndexBuilder:
    """On ``:local`` the frozen JSON graph is the retrieval source: the "index" is a verified pointer to it."""

    def __init__(self, storage_kg: Path) -> None:
        self.root = Path(storage_kg)

    def build(self, ctx: RequestContext, snapshot: FrozenSnapshot, receipt: object) -> IndexRef:
        """Return the stable reference for the (snapshot, receipt) pair."""
        receipt_id = str(getattr(receipt, "receipt_id", ""))
        return IndexRef(ref=f"local-json:{snapshot.ref}@{snapshot.sha256[:16]}", snapshot_sha256=snapshot.sha256,
                        receipt_id=receipt_id)

    def verify(self, ctx: RequestContext, index: IndexRef) -> bool:
        """Whether the snapshot graph the reference names still has the recorded bytes."""
        ref = index.ref.removeprefix("local-json:").split("@", 1)[0]
        corpus, _, snap = ref.partition("/")
        if not corpus or not SNAPSHOT_ID_RE.match(snap):
            return False
        try:
            data = (self.root / corpus / snap / "graph.json").read_bytes()
        except OSError:
            return False
        return hashlib.sha256(data).hexdigest() == index.snapshot_sha256


@dataclass(frozen=True)
class LocalPublication:
    """What :func:`build_local_publication` wired."""

    publication: Publication
    gate: PublicationGate
    step: ReplacementPublishStep
    target: str
    storage_kg: Path


def build_local_publication(jobs: JobService, settings: DeploymentSettings, managed: ManagedTiledConfig, *,
                            workdir: Path, ops_root: Path, audit: AuditRecorder | None = None,
                            clock: Callable[[], datetime] | None = None,
                            epoch: Callable[[], float] = time.time,
                            registry: SchemaRegistry | None = None) -> LocalPublication | None:
    """Register the real publication job on ``jobs`` for the image's own Tiled; ``None`` if not applicable.

    Used by the API process (``create_api_app``) and by the worker that runs the jobs
    (``workers.factories.library_service``): both build the same step, stores and gate.

    Args:
        jobs: The job service of this process (its job database is the one the API and worker share).
        settings: Startup deployment settings.
        managed: The managed Tiled configuration of the image.
        workdir: Session workdir; frozen snapshots live under ``<workdir>/storage_kg``.
        ops_root: Operations directory (job, audit and curation stores); importer state goes beside it.
        audit: Audit recorder; default one over ``<ops_root>/audit.sqlite3``.
        clock: Timezone-aware time source of the importer.
        epoch: Epoch-seconds source of the publication stores.
        registry: Schema registry (default: discovered).
    """
    target = local_tiled_target(settings, managed)
    if target is None:
        return None
    registry = registry or SchemaRegistry.discover()
    fingerprints = tj.default_supported_schema_fingerprints(registry)
    storage_kg = Path(workdir) / SNAPSHOTS_DIRNAME
    storage_kg.mkdir(parents=True, exist_ok=True)
    state_dir = Path(ops_root) / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    now = clock or (lambda: datetime.now(UTC))
    # The writer credential: the managed lifecycle's own reader, called lazily per request (never cached or logged).
    client = TiledMutationClient(target, profile="local", credential=lambda: tiled_lifecycle.writer_credential(managed))
    step = ReplacementPublishStep(client=client, settings=settings, supported_schema_fingerprints=fingerprints,
                                  storage_kg=storage_kg, state_dir=state_dir, clock=now)
    recorder = audit or AuditRecorder(AuditLog(Path(ops_root) / "audit.sqlite3", clock=epoch))
    operator = local_operator_context(settings, request_id="local-publication")
    deps = PublicationDeps(
        snapshots=DirectorySnapshotSource(storage_kg), step=step, index=LocalIndexBuilder(storage_kg),
        probe=LocalRevisionProbe(fingerprints), store=PublishStore(jobs.store.db, clock=epoch), audit=recorder,
        ctx_for=lambda job: worker_context(operator, run_id=job.job_id), clock=epoch,
        verify_approval=lambda approval, plan, at: verify_approval(approval, plan, settings, at=at))
    return LocalPublication(Publication(jobs, deps), PublicationGate(frozenset({target})), step, target, storage_kg)


def managed_tiled_from(environ: Mapping[str, str]) -> ManagedTiledConfig | None:
    """The managed Tiled configuration in the startup environment, or ``None`` (logged, never raised)."""
    try:
        return load_tiled_settings(environ).managed
    except TiledConfigError as exc:
        logger.warning("local publication not wired: %s", exc)
        return None


def install_local_publication(operations: F2WOperations, *, environ: Mapping[str, str], workdir: Path,
                              ops_root: Path, clock: Callable[[], datetime] | None = None,
                              registry: SchemaRegistry | None = None) -> LocalPublication | None:
    """Wire publication and the H5 gate of ``operations`` for the ``:local`` image's own Tiled.

    A no-op (returns ``None``, the gate stays as it was) on every other profile, when the operations
    already carry a publication, or when no managed Tiled matching the settings is configured.
    """
    settings = operations.settings
    if settings.profile is not DeploymentProfile.LOCAL or operations.publication is not None:
        return None
    managed = managed_tiled_from(environ)
    if managed is None:
        return None
    wired = build_local_publication(operations.jobs, settings, managed, workdir=workdir, ops_root=ops_root,
                                    audit=operations.audit, clock=clock, registry=registry)
    if wired is None:
        logger.warning("local publication not wired: the managed Tiled is not this deployment's Tiled target")
        return None
    operations.publication = wired.publication
    operations.gate = wired.gate
    return wired


def post_extraction_publisher(operations: F2WOperations, wired: LocalPublication,
                              *, registry: SchemaRegistry | None = None) -> PostExtractionPublisher | None:
    """The post-extraction publisher for the one corpus the deployment is scoped to, or ``None``.

    The corpus comes from the trusted startup setting ``F2W_CORPUS_SCOPE`` and must name exactly one
    corpus; otherwise the legacy phases keep reporting ``skipped`` (JSON available, Tiled unavailable).
    """
    scope = sorted(operations.settings.corpus_scope)
    if len(scope) != 1:
        return None
    return PostExtractionPublisher(
        operations, freeze=snapshot_freezer(storage_kg=wired.storage_kg, corpus=scope[0], registry=registry),
        is_safe=wired.step.plan_is_safe, target=wired.target)


__all__ = ("LocalIndexBuilder", "LocalPublication", "LocalRevisionProbe", "SNAPSHOTS_DIRNAME", "STATE_DIRNAME",
           "build_local_publication", "install_local_publication", "local_publication_gate",
           "local_tiled_target", "managed_tiled_from", "post_extraction_publisher")
