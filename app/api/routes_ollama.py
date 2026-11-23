"""``GET /v1/ollama/models`` (policy V86): the models of the deployment's Ollama server, for the settings dropdown.

The browser never calls Ollama. This route asks ``{OLLAMA_URL}/api/tags`` (read-only, bounded by a short timeout and a
byte cap, see :mod:`app.modules.ollama_catalog`) each time it is called, so opening the dropdown refreshes the list.
It answers 200 with a state (``ok``, ``empty``, ``unset``, ``unreachable``, ``error``) rather than an HTTP error, and
never carries the Ollama URL, its host or a transport error text. Entry capability ``settings.personal``: whoever can
use the settings panel the dropdown lives in.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request

from ..modules.contracts.context import RequestContext
from ..modules.ollama_catalog import list_ollama_models
from .routes_common import Prefixed, context, project_dict


def install(app: FastAPI) -> FastAPI:
    """Mount the Ollama model list on ``app``."""
    router = Prefixed(app, "/v1/ollama")

    @router.get("/models")
    def ollama_models(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Models the Ollama server holds right now (``state``, ``models``, ``error``)."""
        return project_dict(request, list_ollama_models().to_dict())

    return app


__all__ = ("install",)
