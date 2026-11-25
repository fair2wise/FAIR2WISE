"""Service factories a worker process may load (``--factory app.modules.workers.factories:<name>``).

Only callables under ``app.modules.`` are loadable (see ``jobs.process_support.worker_command``).
``idle_service`` lets a worker start, observe the store and exit (the ALS profiles keep it until the
ALS worker has an identity wiring, T44). ``library_service`` (T34, ``:local`` profile only) registers
the library job kinds so a worker claims and runs them.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path

from ..jobs.service import JobService
from ..jobs.store import JobStore
from ..library import register_library_jobs

logger = logging.getLogger(__name__)


def idle_service(db_path: str) -> JobService:
    """A service over the job store at ``db_path`` with no kinds registered."""
    return JobService(JobStore(db_path), heartbeat_interval=30.0)


def library_service(db_path: str, environ: Mapping[str, str] | None = None) -> JobService:
    """A service over the job store at ``db_path`` with the library kinds registered (``:local`` only).

    The deployment settings come from the worker's startup environment, as for the API process. On an
    ALS profile this refuses to start (fail closed) rather than run a worker with no job authority.

    Args:
        db_path: The job store the API submits to.
        environ: Startup environment (``os.environ`` when ``None``).

    Raises:
        StartupConfigError: The environment is not a valid ``:local`` deployment.
    """
    from ...api.legacy_ui import deployment_settings_from
    from ..security.authorization import StartupConfigError

    env = os.environ if environ is None else environ
    settings = deployment_settings_from(env)
    if not settings.is_local:
        raise StartupConfigError(f"config_rejected: library_service runs on the local profile only, not {settings.profile}")
    service = JobService(JobStore(db_path), settings=settings, heartbeat_interval=30.0)
    # T35: the real library_verify handler over the same mount and sidecar catalog as the API process.
    from ..library.handlers import library_handlers
    from ..library.service import LibraryService

    workdir = Path(env.get("F2W_WORKDIR") or Path(db_path).parent)
    from ..library.bundles import BUNDLES_DIRNAME, BundleStore
    from ..library.extraction import LibraryExtractor

    library = LibraryService.from_startup(env, workdir, settings)
    # T39: library_extract runs here; the LLM backend comes from the worker's startup environment only.
    extractor = LibraryExtractor(library, BundleStore(workdir / BUNDLES_DIRNAME), env=env)
    from ..library.harvest import Harvester, HarvestStore

    harvester = Harvester(library, HarvestStore(workdir / "library" / "harvest.sqlite"))
    # Owner decision 2026-11-04: ``:local`` publishes into the image's own Tiled, so this worker must run the
    # publication kind the API process registers (same stores, same gate; a no-op without a matching managed Tiled).
    # Built before the library kinds: the library_publish job (owner decision 2026-11-10) stages through it.
    from ...api.local_publication import SNAPSHOTS_DIRNAME, build_local_publication, managed_tiled_from

    wired = None
    managed = managed_tiled_from(env)
    if managed is not None:
        wired = build_local_publication(service, settings, managed, workdir=workdir, ops_root=Path(db_path).parent)
        if wired is None:
            logger.warning("local publication not registered on the worker: the managed Tiled is not this deployment's target")
    pipelines = {}
    if wired is not None:
        from ...api.operations import F2WOperations
        from ..library.jobs import LIBRARY_PUBLISH_KIND
        from ..library.kg_service import KgLibrary
        from ..library.publish import LibraryPublisher

        operations = F2WOperations(settings, service, publication=wired.publication, publication_gate=wired.gate)
        pipelines[LIBRARY_PUBLISH_KIND] = LibraryPublisher(
            KgLibrary(BundleStore(workdir / BUNDLES_DIRNAME)), operations, storage_kg=workdir / SNAPSHOTS_DIRNAME,
            environ=env).pipeline()
    from ..library.passages_job import LibraryPassages

    passages = LibraryPassages(library, BundleStore(workdir / BUNDLES_DIRNAME))  # no model: it only reads the library PDFs
    register_library_jobs(service, settings,
                          handlers=library_handlers(library, extractor=extractor, harvester=harvester, passages=passages),
                          pipelines=pipelines)
    return service


__all__ = ("idle_service", "library_service")
