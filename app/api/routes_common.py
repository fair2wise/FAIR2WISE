"""Shared helpers of the thin ``/v1`` route modules (``routes_*.py``, task T14).

The routes run over :class:`app.api.operations.F2WOperations`.

Each handler does HTTP validation, takes the server-built context from
:func:`app.api.auth.request_context` (or a fresh one before a conditional effect), calls
one operation and projects the result. No handler decides policy or runs an algorithm of
its own; the policies are declared in :mod:`app.api.policies`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.json_types import JsonValue
from .auth import fresh_request_context, request_context
from .operations import F2WOperations


class Prefixed:
    """Register routes directly on ``app`` under ``prefix``.

    Routes are added as plain ``APIRoute`` objects on the app (not through
    ``include_router``), so every route stays visible to the DEP-19 check
    (:func:`app.api.auth.undeclared_routes`) and to the policy lookup.
    """

    def __init__(self, app: FastAPI, prefix: str) -> None:
        """Bind the app and the path prefix prepended to every route."""
        self.app = app
        self.prefix = prefix

    def get(self, path: str, **kw: Any) -> Callable[[Any], Any]:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """``app.get(prefix + path)``."""
        return self.app.get(self.prefix + path, **kw)

    def post(self, path: str, **kw: Any) -> Callable[[Any], Any]:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """``app.post(prefix + path)``."""
        return self.app.post(self.prefix + path, **kw)

    def put(self, path: str, **kw: Any) -> Callable[[Any], Any]:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """``app.put(prefix + path)``."""
        return self.app.put(self.prefix + path, **kw)


def operations(request: Request) -> F2WOperations:
    """The app's operations service, or fail closed."""
    ops = getattr(request.app.state, "f2w_operations", None)
    if not isinstance(ops, F2WOperations):
        raise ContractError(ErrorCode.INTERNAL, "operations are not installed")
    return ops


async def context(request: Request) -> RequestContext:
    """Route dependency: the checked context (entry policy from ``app.api.policies``)."""
    ctx = await request_context(request)
    request.state.f2w_ctx = ctx
    return ctx


def fresh_context(request: Request) -> RequestContext:
    """Route dependency: re-run the ALS role check before a conditional privileged effect."""
    ctx = fresh_request_context(request)
    request.state.f2w_ctx = ctx
    return ctx


def project(request: Request, payload: JsonValue) -> JsonValue:
    """Project a ``/v1`` body with the app's one disclosure projector (D19, AUD-06).

    :meth:`~app.modules.security.disclosure.DisclosureProjector.project_api_response`:
    private keys and served-artifact values dropped, paths opaque, secrets redacted,
    strings clipped to the 600-character bound (B10). Without the guard (an app built
    without ``create_app``) the body is returned as is, as before.
    """
    guard = getattr(request.app.state, "f2w_legacy_ui", None)
    if guard is None:
        return payload
    return guard.projector.project_api_response(payload)


def project_dict(request: Request, payload: object) -> dict[str, Any]:
    """:func:`project` for a route declared to return a JSON object."""
    projected: dict[str, Any] = project(request, payload)
    return projected


def dump(model: object) -> JsonValue:
    """JSON-ready form of a Pydantic model (other values unchanged)."""
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


__all__ = ("Prefixed", "context", "dump", "fresh_context", "operations", "project", "project_dict")
