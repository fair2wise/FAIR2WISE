"""``/v1/validation/judge-model`` (policies V23, V24; owner O-U2).

The settings panel's judge-model dropdown reads and saves the deployment-wide judge here.
These are new routes beside the 19 frozen UI routes: ``GET``/``PUT /settings`` and their
bodies are unchanged, so the frozen UI contract holds. Both routes need
``settings.global`` (ALS admin, re-checked for the change; every ``:local`` caller).

The handlers only validate HTTP input and call
:class:`app.modules.validation_settings.JudgeModelService`. No route reads or waits on a
binary verdict (H3 B).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.validation_settings import JudgeModelService
from .routes_common import Prefixed, context, fresh_context, project


class JudgeModelUpdate(BaseModel):
    """Body of ``PUT /v1/validation/judge-model``; ``null`` or ``""`` clears the judge."""

    model_config = ConfigDict(extra="forbid")

    judge_model: str | None = Field(default=None, max_length=200)


def judge_service(request: Request) -> JudgeModelService:
    """The app's judge setting service, or fail closed."""
    service = getattr(request.app.state, "f2w_judge_settings", None)
    if not isinstance(service, JudgeModelService):
        raise ContractError(ErrorCode.INTERNAL, "judge settings are not installed")
    return service


def install(app: FastAPI) -> FastAPI:
    """Mount the validation settings routes on ``app``."""
    router = Prefixed(app, "/v1/validation")

    @router.get("/judge-model")
    def get_judge_model(request: Request, refresh: bool = Query(default=False),
                        ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The deployment's judge model and the models the provider lists right now."""
        return project(request, judge_service(request).view(ctx, refresh=refresh))

    @router.put("/judge-model")
    def put_judge_model(request: Request, body: JudgeModelUpdate,
                        ctx: RequestContext = Depends(fresh_context)) -> dict[str, Any]:
        """Set or clear the judge; it must be in the provider's current list."""
        return project(request, judge_service(request).update(ctx, body.judge_model))

    return app


__all__ = ("JudgeModelUpdate", "install", "judge_service")
