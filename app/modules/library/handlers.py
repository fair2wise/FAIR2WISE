"""Real stage bodies for the library job kinds (A8): ``library_verify`` (T35).

``register_library_jobs(job_service, settings, handlers=library_handlers(service))`` replaces the
fail-closed stubs of T34. The API process registers the same handlers (submission validation) and the
worker process runs them (``app.modules.workers.factories:library_service``).
"""

from __future__ import annotations

from collections.abc import Mapping

from ..jobs.pipeline import StageContext, StageResult
from .jobs import LIBRARY_KIND_CAPABILITIES, LIBRARY_VERIFY_KIND, StageHandler
from .service import LibraryService

#: Most unverified paper ids a verify run reports as incomplete (the rest are counted only).
MAX_REPORTED_REMAINING = 200


def verify_handler(service: LibraryService) -> StageHandler:
    """The ``library_verify`` stage: verify every paper without a settled result.

    A run stopped by the open OpenAlex quota breaker fails retryably (``incomplete`` names the papers
    left), so the operator can resubmit with a new nonce once the quota resets; settled results stay cached.
    """
    def run(ctx: StageContext) -> StageResult:
        ctx.require(LIBRARY_KIND_CAPABILITIES[LIBRARY_VERIFY_KIND])
        summary = service.verify_all(service.worker_context(ctx.job.job_id), progress=ctx.progress, log=ctx.log,
                                     guard=ctx.guard)
        ctx.log(f"verified {summary['verified']}, not found {summary['not_found']}, errors {summary['errors']}, "
                f"left {len(summary['remaining'])}")
        remaining = summary["remaining"]
        return StageResult(
            progress={"verified": summary["verified"], "not_found": summary["not_found"],
                      "errors": summary["errors"], "remaining": len(remaining)},
            data={"quota_open": summary["quota_open"], "remaining": len(remaining)},
            incomplete=tuple(remaining[:MAX_REPORTED_REMAINING]))

    return run


def library_handlers(service: LibraryService) -> Mapping[str, StageHandler]:
    """``kind -> stage body`` for the kinds implemented so far (T35: ``library_verify``)."""
    return {LIBRARY_VERIFY_KIND: verify_handler(service)}


__all__ = ("MAX_REPORTED_REMAINING", "library_handlers", "verify_handler")
