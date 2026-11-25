"""``/v1/library/schemas/*`` authoring routes (policies V89-V94 and V96-V98, ``:local`` only; ALS answers 404).

Validate, insert the required core, save, list, read, diff and delete user-written LinkML schemas (design:
``mkdocs/docs/design-schema-generation.md``). Every body that carries schema text is capped at 256 KiB *before* it
is parsed. The text is returned verbatim (it is the user's own, not a disclosure risk), so these routes do not run it
through the 600-character body projector that clips other ``/v1`` strings.

Writing and checking need the separate capability ``library.author_schema``; reading the stored schemas needs
``library.read`` like the other library lists. Nothing here starts a model call (see ``routes_library_schema_ai``).
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.library.kg_service import KgLibrary
from ..modules.library.schema_generate import SchemaGenerator
from ..modules.library.schema_preview import SchemaPreview
from ..modules.library.schema_prompt import MAX_DESCRIPTION
from ..modules.library.user_schema_validation import MAX_BYTES, insert_required_core, validate_schema_text
from ..modules.library.user_schemas import SchemaInvalid, UserSchemaStore, validate_schema_name
from .routes_common import Prefixed, context
from .routes_library_kgs import kg_library

#: Request-body cap of the routes that carry schema text (the validator's own size limit plus JSON overhead).
MAX_BODY_BYTES = MAX_BYTES + 4096


async def read_json_body(request: Request, *, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
    """The JSON object of the request body, refused above ``limit`` bytes *before* it is parsed.

    The ``Content-Length`` is checked first, then the stream is read with a running count, so a client that lies or
    streams cannot make the server hold more than ``limit`` bytes.
    """
    too_big = ContractError(ErrorCode.INVALID_INPUT, f"the request body is larger than {limit // 1024} KiB",
                            retryable=False, reason="body_too_large")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_big
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise too_big
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except (ValueError, UnicodeDecodeError):
        raise ContractError(ErrorCode.INVALID_INPUT, "the request body is not valid JSON", retryable=False) from None
    if not isinstance(body, dict):
        raise ContractError(ErrorCode.INVALID_INPUT, "the request body must be a JSON object", retryable=False)
    return body


def text_field(body: dict[str, Any], key: str, *, limit: int, required: bool = True) -> str | None:
    """A string field of ``body`` (``INVALID_INPUT`` when missing, not text, or longer than ``limit`` characters)."""
    value = body.get(key)
    if value is None:
        if required:
            raise ContractError(ErrorCode.INVALID_INPUT, f"{key} is required", retryable=False)
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_INPUT, f"{key} must be a string", retryable=False)
    if len(value) > limit:
        raise ContractError(ErrorCode.INVALID_INPUT, f"{key} is longer than {limit} characters", retryable=False)
    return value


def user_store(request: Request) -> UserSchemaStore:
    """The user schema store of the app's KG library."""
    service: KgLibrary = kg_library(request)
    return service.user_schemas


#: Body cap of the generate route (a description of at most 2,000 characters plus a backend and a model name).
MAX_GENERATE_BODY_BYTES = 16 * 1024


def schema_generator(request: Request) -> SchemaGenerator:
    """The app's schema generator, or fail closed."""
    generator = getattr(request.app.state, "f2w_schema_generator", None)
    if not isinstance(generator, SchemaGenerator):
        raise ContractError(ErrorCode.INTERNAL, "the schema generator is not installed")
    return generator


def install(app: FastAPI, env: Mapping[str, str] | None = None) -> FastAPI:
    """Mount the routes on ``app`` (``:local`` only); ``env`` is the startup environment of the generate route."""
    router = Prefixed(app, "/v1/library")
    app.state.f2w_schema_generator = SchemaGenerator(env)
    app.state.f2w_schema_preview = SchemaPreview(lambda: app.state.f2w_kg_library, lambda: app.state.f2w_library,
                                                 lambda: app.state.f2w_schema_generator)

    @router.post("/schemas/validate")
    async def validate(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Check schema text; answers the report (problems with lines, what was checked). Nothing is stored."""
        body = await read_json_body(request)
        text = text_field(body, "text", limit=MAX_BYTES) or ""
        return validate_schema_text(text).as_dict()

    @router.post("/schemas/insert-core")
    async def insert_core(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, str]:
        """The text with the missing parts of the required core added (it is checked for size first)."""
        body = await read_json_body(request)
        text = text_field(body, "text", limit=MAX_BYTES) or ""
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ContractError(ErrorCode.INVALID_INPUT, f"the schema is larger than {MAX_BYTES // 1024} KiB", retryable=False)
        return {"text": insert_required_core(text)}

    @router.post("/schemas")
    async def save(request: Request, ctx: RequestContext = Depends(context)) -> Any:  # noqa: ANN401  # Any: JSON body or a typed 422 response
        """Validate and store ``text`` as the next immutable version of schema ``name``; invalid text is a 422 with the report."""
        body = await read_json_body(request)
        name = validate_schema_name(text_field(body, "name", limit=80))
        text = text_field(body, "text", limit=MAX_BYTES) or ""
        try:
            saved = user_store(request).save(name, text)
        except SchemaInvalid as exc:
            return JSONResponse({"code": exc.code.value, "message": str(exc), "retryable": False, "reason": exc.reason,
                                 "report": exc.report.as_dict()}, status_code=422)
        return saved.as_dict(with_text=False)

    @router.get("/schemas/user")
    def list_user(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Every user schema: its latest version (no text) and all version numbers."""
        return {"schemas": user_store(request).list()}

    @router.get("/schemas/user/{ref}")
    def read_user(ref: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """One stored version (``<name>@vN``): text, sha256, version, creation time."""
        return user_store(request).get(f"user/{ref}").as_dict()

    @router.get("/schemas/user/{name}/diff")
    def diff_user(name: str, request: Request, from_: int = Query(alias="from", ge=1, le=999),
                  to: int = Query(ge=1, le=999), ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Unified diff text between two versions of ``name``."""
        return {"name": name, "from": from_, "to": to, "diff": user_store(request).diff(validate_schema_name(name), from_, to)}

    @router.delete("/schemas/user/{ref}")
    def delete_user(ref: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Delete one version, only when no KG bundle's manifest references it (409 otherwise)."""
        user_store(request).delete(f"user/{ref}")
        return {"deleted": f"user/{ref}"}

    @router.post("/schemas/generate")
    async def generate(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Ask the chosen model (free CBORG or Ollama) to draft a schema; validated, repaired at most twice, every attempt returned."""
        body = await read_json_body(request, limit=MAX_GENERATE_BODY_BYTES)
        description = text_field(body, "description", limit=MAX_DESCRIPTION) or ""
        backend = text_field(body, "backend", limit=20, required=False)
        if backend is not None and backend not in ("cborg", "ollama"):
            raise ContractError(ErrorCode.INVALID_INPUT, "backend must be cborg or ollama", retryable=False)
        model = text_field(body, "model", limit=200, required=False)
        generator = schema_generator(request)
        return await run_in_threadpool(lambda: generator.generate(description, backend=backend, model=model))

    @router.post("/schemas/preview")
    async def preview(request: Request, ctx: RequestContext = Depends(context)) -> Any:  # noqa: ANN401  # Any: JSON body or a typed 422 response
        """Try a schema on one paper (<= 3 pages, one model call per page, nothing stored). Without ``confirm`` it only estimates."""
        body = await read_json_body(request)
        schema = text_field(body, "schema", limit=MAX_BYTES) or ""
        paper_id = text_field(body, "paper_id", limit=200) or ""
        backend = text_field(body, "backend", limit=20, required=False)
        if backend is not None and backend not in ("cborg", "ollama"):
            raise ContractError(ErrorCode.INVALID_INPUT, "backend must be cborg or ollama", retryable=False)
        model = text_field(body, "model", limit=200, required=False)
        confirmation = text_field(body, "confirmation", limit=200, required=False)
        confirm = body.get("confirm", False)
        if not isinstance(confirm, bool):
            raise ContractError(ErrorCode.INVALID_INPUT, "confirm must be true or false", retryable=False)
        service: SchemaPreview = request.app.state.f2w_schema_preview
        try:
            return await run_in_threadpool(lambda: service.preview(schema, paper_id, model=model, backend=backend,
                                                                   confirm=confirm, confirmation=confirmation))
        except SchemaInvalid as exc:
            return JSONResponse({"code": exc.code.value, "message": str(exc), "retryable": False, "reason": exc.reason,
                                 "report": exc.report.as_dict()}, status_code=422)

    return app


__all__ = ("MAX_BODY_BYTES", "install", "read_json_body", "schema_generator", "text_field", "user_store")
