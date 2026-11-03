"""Real stage bodies for the library job kinds (A8): ``library_verify`` (T35), ``library_index`` (T36).

``register_library_jobs(job_service, settings, handlers=library_handlers(service))`` replaces the
fail-closed stubs of T34. The API process registers the same handlers (submission validation) and the
worker process runs them (``app.modules.workers.factories:library_service``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..jobs.pipeline import StageContext, StageResult
from .jobs import LIBRARY_EXTRACT_KIND, LIBRARY_INDEX_KIND, LIBRARY_KIND_CAPABILITIES, LIBRARY_VERIFY_KIND, StageHandler
from .service import LibraryService

if TYPE_CHECKING:  # pragma: no cover
    from .extraction import LibraryExtractor

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


def index_handler(service: LibraryService) -> StageHandler:
    """The ``library_index`` stage: chunk the library into the literature index (see :mod:`.indexer`).

    Incremental by sha256 (an unchanged library writes nothing), one checkpointed unit per paper, so progress
    and cancel work per PDF. A scanned PDF is reported ``no_text``, not a failure.
    """
    def run(ctx: StageContext) -> StageResult:
        ctx.require(LIBRARY_KIND_CAPABILITIES[LIBRARY_INDEX_KIND])
        return service.indexer.run(ctx)

    return run


def extract_handler(extractor: LibraryExtractor) -> StageHandler:
    """The ``library_extract`` stage: term extraction per PDF into a KG bundle (see :mod:`.extraction`)."""
    def run(ctx: StageContext) -> StageResult:
        ctx.require(LIBRARY_KIND_CAPABILITIES[LIBRARY_EXTRACT_KIND])
        return extractor.run(ctx)

    return run


def library_handlers(service: LibraryService, *, extractor: LibraryExtractor | None = None) -> Mapping[str, StageHandler]:
    """``kind -> stage body`` for the kinds implemented so far (T35 verify, T36 index, T39 extract).

    ``library_extract`` is included only when an ``extractor`` is given; otherwise it stays the fail-closed stub.
    """
    out = {LIBRARY_VERIFY_KIND: verify_handler(service), LIBRARY_INDEX_KIND: index_handler(service)}
    if extractor is not None:
        out[LIBRARY_EXTRACT_KIND] = extract_handler(extractor)
    return out


__all__ = ("MAX_REPORTED_REMAINING", "extract_handler", "index_handler", "library_handlers", "verify_handler")
