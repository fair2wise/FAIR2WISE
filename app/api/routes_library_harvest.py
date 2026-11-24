"""Harvester routes (A8, T45), ``:local`` only: harvest runs, their candidates and the paywalled queue.

Starting a harvest is a job (``POST /v1/jobs`` with kind ``library_harvest``); these routes read what it found and
complete the papers a person has to fetch by hand (paywalled, blocked by network policy, or failed to fetch). The
page is opened by the person in their own browser: no route here requests a publisher; the only thing that reaches
the server is the PDF the person chooses to upload, one file per request.
"""

from __future__ import annotations

import re
from typing import Any, cast

from fastapi import Depends, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.library.harvest import Harvester, openalex_keyed
from .multipart_upload import PartFile, read_single_file
from .routes_common import Prefixed, context, project
from .upload_limits import read_upload_limited

_ID_RE = re.compile(r"^[0-9a-f]{16,64}$")


def harvester(request: Request) -> Harvester:
    """The app's harvester, or fail closed."""
    found = getattr(request.app.state, "f2w_harvest", None)
    if not isinstance(found, Harvester):
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "the harvester is not installed")
    return found


def _id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ContractError(ErrorCode.NOT_FOUND, "not found")
    return value


def install(app: FastAPI) -> FastAPI:
    """Mount the harvester routes on ``app`` (the factory calls this on ``:local`` only)."""
    router = Prefixed(app, "/v1/library/harvest")

    @router.get("/runs")
    def runs(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Every harvest run, newest first, with counts per candidate status.

        ``openalex_keyed`` says whether OpenAlex is searched with a key (``false``: unauthenticated, reduced rate);
        it is the boolean only, never the key.
        """
        return cast(dict[str, Any], project(request, {"runs": harvester(request).store.runs(),
                                                      "openalex_keyed": openalex_keyed()}))

    @router.get("/runs/{run_id}/candidates")
    def candidates(run_id: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The candidates of one run: tier, status, landing URL and where a downloaded PDF was filed."""
        found = harvester(request)
        if found.store.run(_id(run_id)) is None:
            raise ContractError(ErrorCode.NOT_FOUND, "run not found")
        return cast(dict[str, Any], project(request, {"candidates": found.store.candidates(run_id)}))

    @router.post("/candidates/{candidate_id}/pdf")
    async def supply_pdf(candidate_id: str, request: Request, confirm: bool = False, to: str | None = None,
                         ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """File a PDF the person saved (multipart field ``file``) for a paper that needs one by hand.

        The PDF is matched to the candidate (DOI in the PDF, else title). A strong match is filed at once
        (``filed: true``); otherwise nothing is stored and the answer is ``filed: false, needs_confirmation: true``
        with the verdict, what the PDF carries and, when its DOI belongs to another open candidate of the run, that
        candidate. ``confirm=true`` files it anyway (the verdict is recorded as provenance); ``to=<candidate id>``
        files it against that other candidate of the same run. Each file is its own request.
        """
        found = harvester(request)
        _id(candidate_id)
        if to is not None:
            _id(to)
        service = found.service
        filename, raw = await read_single_file(request, "file", service.max_upload_bytes)
        data = await read_upload_limited(PartFile(raw), service.max_upload_bytes)
        who = ctx.principal.subject
        return cast(dict[str, Any], project(request, await run_in_threadpool(
            lambda: found.supply_pdf(candidate_id, filename, data, confirm=confirm, to=to, supplied_by=who))))

    @router.post("/runs/{run_id}/pdf")
    async def supply_run_pdf(run_id: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The batch drop: file a PDF against whichever open candidate of the run it belongs to (one file per request)."""
        found = harvester(request)
        _id(run_id)
        service = found.service
        filename, raw = await read_single_file(request, "file", service.max_upload_bytes)
        data = await read_upload_limited(PartFile(raw), service.max_upload_bytes)
        who = ctx.principal.subject
        return cast(dict[str, Any], project(request, await run_in_threadpool(
            lambda: found.supply_pdf_to_run(run_id, filename, data, supplied_by=who))))

    @router.post("/candidates/{candidate_id}/undo")
    def undo_pdf(candidate_id: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Reverse a filing made from a supplied PDF: the file, its manifest and catalog rows, and the candidate's status."""
        return cast(dict[str, Any], project(request, harvester(request).undo_supplied(_id(candidate_id))))

    @router.post("/candidates/{candidate_id}/dismiss")
    def dismiss(candidate_id: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Dismiss a paywalled candidate (it stays listed as dismissed)."""
        return cast(dict[str, Any], project(request, harvester(request).dismiss(_id(candidate_id))))

    return app


__all__ = ("harvester", "install")
