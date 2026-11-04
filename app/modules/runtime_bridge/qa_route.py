"""UI QA route selection for ``F2W_RUNTIME=langgraph`` (task T03B, owner decision H2 Q1).

``F2W_QA_ROUTE`` is read once, when the service starts, and only matters in
``langgraph`` mode:

- ``supervisor`` (default, also when unset or blank): the legacy orchestrator's
  ordinary-question read goes through ``F2WApplication.delegate``. The planning
  supervisor (prompt ``QA_DELEGATION_PROMPT``) makes one model call, chooses
  ``answer_from_knowledge``, and the tool runs the existing QA path. The UI still
  gets the exact legacy verdict of that tool call.
- ``direct``: the T03 behavior, ``F2WApplication.answer_from_knowledge`` with no
  planning call.

Any other value refuses startup.

Parity rules for the supervisor route:

- The tool arguments are pinned to the UI request (question verbatim, history,
  ``source_rag``, ``use_live_tiled``) by :class:`PinnedArgsService`, so the model
  decides *whether* to call the tool, not what the legacy QA path receives.
- One planning call per turn (``max_model_calls=1``): the tool's verdict is the
  answer, so the supervisor's relay call would be discarded and is not made.
- If the planner fails or does not call the tool, the turn falls back to the
  direct call (logged, and the failed planning call is in the turn trace).
- Each planning call holds the shared provider admission slot (the
  supervisor's ``admission.aslot``) and is recorded in the turn trace as
  ``llm_calls[label="supervisor-plan"]``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import contextvars
from enum import StrEnum
import json
import os
import time
from typing import TYPE_CHECKING, Any, cast

from .mode import RuntimeConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

QA_ROUTE_ENV = "F2W_QA_ROUTE"
PLANNING_LABEL = "supervisor-plan"


class QaRoute(StrEnum):
    """How a langgraph-mode UI QA turn reaches ``answer_from_knowledge``."""

    DIRECT = "direct"
    SUPERVISOR = "supervisor"


DEFAULT_QA_ROUTE = QaRoute.SUPERVISOR


def qa_route(environ: Mapping[str, str] | None = None) -> QaRoute:
    """Return the configured UI QA route.

    Raises:
        RuntimeConfigError: On any value other than ``direct``/``supervisor``/blank.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(QA_ROUTE_ENV) or "").strip().lower()
    if not raw:
        return DEFAULT_QA_ROUTE
    try:
        return QaRoute(raw)
    except ValueError:
        raise RuntimeConfigError(
            f"config_rejected: {QA_ROUTE_ENV} must be 'direct' or 'supervisor', got {raw!r}") from None


# -- argument pinning -------------------------------------------------------------

_PINNED: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("f2w_qa_pinned", default=None)


class PinnedArgsService:
    """Retrieval-service wrapper that substitutes the pinned UI arguments, if any.

    Outside a pinned turn (the direct route, CLI callers) it passes calls through
    unchanged. Every other attribute is the wrapped service's.
    """

    def __init__(self, inner: Any) -> None:  # noqa: ANN401  # Any: legacy QA service (untyped donor object)
        self.inner = inner

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401  # Any: attribute delegation to the wrapped legacy service
        return getattr(self.inner, name)

    async def query(self, question: str, *, history: Sequence[Mapping[str, str]] | None = None, source_rag: object = None,
                    use_live_tiled: object = None) -> Any:  # noqa: ANN401  # Any: legacy verdict (untyped donor structure)
        """Run the wrapped service's query, substituting the pinned turn's arguments if one is set."""
        pinned = _PINNED.get()
        if pinned is not None:
            question = pinned["question"]
            history = pinned.get("history")
            source_rag = pinned.get("source_rag")
            use_live_tiled = pinned.get("use_live_tiled")
        return await self.inner.query(question, history=history, source_rag=source_rag,
                                      use_live_tiled=use_live_tiled)


def pin_args(args: dict[str, Any]) -> contextvars.Token[dict[str, Any] | None]:
    """Pin ``args`` for tool calls made in the current context; reset with :func:`unpin_args`."""
    return _PINNED.set(dict(args))


def unpin_args(token: contextvars.Token[dict[str, Any] | None]) -> None:
    """Restore the pinned arguments saved by the :func:`pin_args` call that returned `token`."""
    _PINNED.reset(token)


# -- traced planning model --------------------------------------------------------

def _role(message: object) -> str:
    return {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}.get(
        str(getattr(message, "type", "")), str(getattr(message, "type", "")))


def _content(message: object) -> str:
    content = getattr(message, "content", "")
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    calls = getattr(message, "tool_calls", None)
    if calls:
        text += json.dumps([{"name": c.get("name"), "args": c.get("args")} for c in calls],
                           sort_keys=True, default=str)
    return text


def _record(model: object, messages: Sequence[Any], reply: object, started: float,
            error: BaseException | None) -> None:
    from ..f2w_agent.trace import record_llm_call

    record_llm_call(
        label=PLANNING_LABEL,
        model=getattr(model, "model_name", None) or getattr(model, "model", None),
        messages=[{"role": _role(m), "content": _content(m)} for m in messages],
        response=None if reply is None else _content(reply),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        error=error,
    )


class _BoundPlanner:
    def __init__(self, owner: TracedPlanningModel, tools: list[Any]) -> None:
        self.owner = owner
        self.tools = tools

    def _bound(self) -> Any:  # noqa: ANN401  # Any: model pair
        model = self.owner.current()
        return model, (model.bind_tools(self.tools) if self.tools else model)

    def invoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> Any:  # noqa: ANN401  # Any: model reply
        model, bound = self._bound()
        started = time.perf_counter()
        try:
            reply = bound.invoke(messages, *args, **kwargs)
        except Exception as exc:
            _record(model, messages, None, started, exc)
            raise
        _record(model, messages, reply, started, None)
        return reply

    async def ainvoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> Any:  # noqa: ANN401  # Any: model reply
        model, bound = self._bound()
        started = time.perf_counter()
        try:
            reply = await bound.ainvoke(messages, *args, **kwargs)
        except Exception as exc:
            _record(model, messages, None, started, exc)
            raise
        _record(model, messages, reply, started, None)
        return reply


class TracedPlanningModel:
    """Chat model handle for the supervisor that resolves the current model per call and traces it.

    Args:
        provider: Returns the LangChain chat model to use now (for example the one
            for the pipeline's current backend/model). Called on every planning call,
            never at construction, so building this handle needs no credentials.
    """

    def __init__(self, provider: Callable[[], Any]) -> None:
        self._provider = provider

    def current(self) -> Any:  # noqa: ANN401  # Any: model
        """Return the chat model the provider resolves right now."""
        return self._provider()

    def bind_tools(self, tools: Sequence[Any], **kwargs: object) -> _BoundPlanner:
        """Return a traced planner bound to `tools`; extra keyword arguments are ignored."""
        return _BoundPlanner(self, list(tools))

    def invoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> Any:  # noqa: ANN401  # Any: model reply
        """Invoke the current model on `messages` through a traced planner with no tools bound."""
        return _BoundPlanner(self, []).invoke(messages, *args, **kwargs)

    async def ainvoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> Any:  # noqa: ANN401  # Any: model reply
        """Async variant of :meth:`invoke`."""
        return await _BoundPlanner(self, []).ainvoke(messages, *args, **kwargs)


def pipeline_planning_model(pipeline: object) -> Callable[[], Any]:
    """Provider of the planning chat model for ``pipeline``'s current backend and model.

    Reuses the existing LangChain builder ``term_extractor.Orchestrator._build_llm``
    (CBORG with the fail-closed key check, or Ollama). Built lazily on the first
    planning call and rebuilt when the UI settings change backend or model.
    """
    cache: dict[Any, Any] = {}

    def current() -> Any:  # noqa: ANN401  # Any: model
        runtime = getattr(pipeline, "runtime", None)
        backend = str(getattr(runtime, "backend", None) or "cborg")
        active = getattr(pipeline, "_active_model", None)
        model = active() if callable(active) else str(getattr(runtime, "model", "") or "")
        key = (backend, model)
        if key not in cache:
            from types import SimpleNamespace

            from ..term_extractor.orchestrator import Orchestrator

            cfg = getattr(pipeline, "cfg", None)
            spec = SimpleNamespace(
                backend=backend, model=model, temperature=0.0, cborg_api_key=None,
                cborg_base=os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov"),
                ollama_url=str(getattr(cfg, "ollama_url", None) or "http://localhost:11434"),
            )
            cache.clear()
            cache[key] = Orchestrator._build_llm(cast(Orchestrator, spec))  # duck-typed self: reads only these attributes
        return cache[key]

    return current


__all__ = (
    "DEFAULT_QA_ROUTE", "PLANNING_LABEL", "PinnedArgsService", "QA_ROUTE_ENV", "QaRoute",
    "TracedPlanningModel", "pin_args", "pipeline_planning_model", "qa_route", "unpin_args",
)
