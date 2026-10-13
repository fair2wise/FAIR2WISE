"""F2W application service: one instance owns its registry, runtime and hooks (task T03).

An :class:`F2WApplication` is what the legacy UI service (in ``F2W_RUNTIME=langgraph``
mode), the CLI, workers and, later, ``f2w.public.F2WClient`` call. It holds no
FastAPI code. Each instance owns:

- one frozen ``ToolRegistry`` and ``SkillRegistry``, built by an explicit bootstrap
  (``runtime_bridge.bootstrap``) at construction;
- one authorizing ``ToolExecutor`` (capability, schema and budget checks before
  every call), used by the direct read call;
- optionally one LangGraph literature supervisor over the same registry, with an
  injected chat model, checkpointer and the F2W delegation prompt;
- admission hooks (default ``ProviderAdmission()``, the shared per-provider
  controller of H1-3) and request-context hooks
  (``security.resolve_context`` / ``local_operator_context``).

Two instances in one process share no registry, executor, ledger, checkpointer
or supervisor. The only process-wide state they can share is what is meant to
be shared: the per-provider admission controllers and the retrieval service, if
the caller passes the same one to both.

Two call styles reach the same :class:`~app.modules.runtime_bridge.retrieval.LegacyRetrievalService`:

- :meth:`F2WApplication.answer_from_knowledge`: a direct read call that bypasses
  planning (no supervisor model call);
- :meth:`F2WApplication.delegate`: a task run by the supervisor, which calls the
  same ``answer_from_knowledge`` tool;
- :meth:`F2WApplication.answer_via_supervisor` (T03B): one read routed through the
  supervisor with the tool arguments pinned to the caller's, returning the same
  ``ReadOutcome`` as the direct call (used by the legacy UI QA route).

Importing this module has no side effects: no environment or file reads, no
registry, model or agent construction, and no LangGraph, provider or retrieval
imports (they load when an instance is built or a delegated task runs).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Tuple

from .contracts import ActorKind, ContractError, ErrorCode, RequestContext, ToolResult
from .security.authorization import DeploymentSettings, local_operator_context, resolve_context


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DelegatedOutcome:
    """Result of one delegated supervisor turn.

    Attributes:
        thread_id: The scoped LangGraph thread id the turn ran on.
        final_text: Content of the last AI message (the supervisor's reply).
        reads: One ``ReadOutcome`` per ``answer_from_knowledge`` call made this turn, in order.
        tool_results: Every tool result of this turn, in order.
        interrupted: Whether the graph returned ``__interrupt__`` (some approval is open).
        stopped: Runtime stop code (budget/deadline), if the runtime stopped the turn.
        open_approvals: Tool-call ids of approval requests still open on the thread (T02B).
        blocked: The turn itself is waiting: it requested an approval this turn and has no
            answer yet. ``interrupted and not blocked`` means "answered, an earlier approval
            is still open": ``final_text`` and ``reads`` are this turn's answer.
    """

    thread_id: str
    final_text: str
    reads: Tuple[Any, ...]
    tool_results: Tuple[ToolResult, ...]
    interrupted: bool = False
    stopped: Optional[str] = None
    open_approvals: Tuple[str, ...] = ()
    blocked: bool = False

    @property
    def answered(self) -> bool:
        """The turn produced a final reply (an older approval may still be open)."""
        return not self.blocked and self.stopped is None

    @property
    def evidence_ids(self) -> List[str]:
        """Evidence IDs across this turn's read calls, in order, without repeats."""
        out: List[str] = []
        for read in self.reads:
            for eid in read.evidence_ids:
                if eid not in out:
                    out.append(eid)
        return out


@dataclass(frozen=True)
class SupervisedRead:
    """Result of :meth:`F2WApplication.answer_via_supervisor`.

    Attributes:
        read: The ``ReadOutcome`` of the tool call (``.to_legacy()`` gives the legacy verdict).
        delegated: The supervisor turn, or ``None`` if the planner raised.
        fallback: ``None`` when the supervisor's own tool call produced ``read``; otherwise why
            the direct call was used instead (``"planner_error:<Type>"`` or ``"no_tool_call"``).
    """

    read: Any
    delegated: Optional[DelegatedOutcome]
    fallback: Optional[str] = None


class F2WApplication:
    """Application service for one F2W deployment instance.

    Args:
        settings: Validated startup ``DeploymentSettings`` (profile, Tiled target, scope).
        retrieval: The retrieval service both call styles use.
        chat_model: Injected LangChain chat model for delegated tasks; ``None`` disables
            :meth:`delegate`. Never constructed here.
        checkpointer: A ``ManagedCheckpointer`` (or raw LangGraph saver) for delegated
            threads; default a fresh in-memory one owned by this instance.
        event_sink: Audit sink; default an in-memory sink owned by this instance.
        admission: Object with ``slot``/``aslot``; default ``ProviderAdmission()``.
        limits: ``RuntimeLimits``; default ``RuntimeLimits(max_retries=0)`` so a failed
            QA call is not silently re-run (the legacy path makes one attempt).
        provider: Admission key for supervisor model calls.
        system_prompt: Supervisor prompt; default ``QA_DELEGATION_PROMPT``.
        identity_port: ALS identity port for :meth:`context` (unused on ``:local``).
        clock: Aware-UTC clock.
        name: Registry label.
        ledger_size: Bound on retained legacy payloads.
    """

    def __init__(
        self,
        *,
        settings: DeploymentSettings,
        retrieval: Any,
        chat_model: Any = None,
        checkpointer: Any = None,
        event_sink: Any = None,
        admission: Any = None,
        limits: Any = None,
        provider: str = "cborg",
        system_prompt: Optional[str] = None,
        identity_port: Any = None,
        clock: Callable[[], datetime] = _utcnow,
        name: str = "f2w",
        ledger_size: int = 256,
    ) -> None:
        from .agent_runtime.executor import RuntimeLimits, ToolExecutor
        from .agent_runtime.policies import InMemoryEventSink
        from .llm_admission import ProviderAdmission
        from .runtime_bridge.bootstrap import (
            QA_DELEGATION_PROMPT,
            build_read_registry,
            build_read_skills,
        )
        from .runtime_bridge.qa_route import PinnedArgsService
        from .runtime_bridge.qa_tool import LegacyResultLedger

        if not isinstance(settings, DeploymentSettings):
            raise TypeError("settings must be a validated DeploymentSettings")
        self.settings = settings
        self.retrieval = retrieval
        self.identity_port = identity_port
        self.clock = clock
        self.provider = provider
        self.limits = limits if limits is not None else RuntimeLimits(max_retries=0)
        self.admission = admission if admission is not None else ProviderAdmission()
        self.event_sink = event_sink if event_sink is not None else InMemoryEventSink()
        self.ledger = LegacyResultLedger(ledger_size)
        # Pass-through except inside answer_via_supervisor, which pins the caller's arguments.
        self.registry = build_read_registry(PinnedArgsService(retrieval), self.ledger, name=name)
        self.skills = build_read_skills(self.registry)
        self.executor = ToolExecutor(self.registry, event_sink=self.event_sink, admission=self.admission,
                                     limits=self.limits, clock=clock)
        self._owned_checkpointer = None
        self._managed = None
        self._saver = None
        self.chat_model = chat_model
        self.supervisor = None
        if chat_model is not None:
            self._saver = self._open_checkpointer(checkpointer)
            from .agent_runtime.supervisor import build_literature_supervisor

            self.supervisor = build_literature_supervisor(
                registry=self.registry, chat_model=chat_model, checkpointer=self._saver,
                event_sink=self.event_sink, limits=self.limits,
                system_prompt=system_prompt or QA_DELEGATION_PROMPT, skills=self.skills,
                admission=self.admission, provider=provider, clock=clock,
            )

    # -- lifecycle ----------------------------------------------------------------

    def _open_checkpointer(self, checkpointer: Any) -> Any:
        from .agent_runtime.checkpointer import ManagedCheckpointer

        if checkpointer is None:
            self._owned_checkpointer = self._managed = ManagedCheckpointer()
            return self._managed.open()
        if isinstance(checkpointer, ManagedCheckpointer):
            self._managed = checkpointer
            return checkpointer.saver
        return checkpointer

    @property
    def checkpointer(self) -> Any:
        """The LangGraph saver used by delegated tasks (``None`` without a chat model)."""
        return self._saver

    def close(self) -> None:
        """Close the checkpointer this instance created (an injected one stays open)."""
        if self._owned_checkpointer is not None:
            self._owned_checkpointer.close()
            self._owned_checkpointer = None

    def __enter__(self) -> "F2WApplication":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- request context -------------------------------------------------------------

    def context(
        self, credential: Any = None, *, request_id: Optional[str] = None, session_id: Optional[str] = None,
        actor_kind: ActorKind = ActorKind.INTERACTIVE, run_id: Optional[str] = None, require_fresh: bool = False,
    ) -> RequestContext:
        """Resolve the acting context through ``security.resolve_context``.

        On ``:local`` this is the local-operator (no credential read). On ALS the
        credential is verified against the profile's Tiled; there is no fallback.
        """
        return resolve_context(self.settings, credential, request_id=request_id or uuid.uuid4().hex,
                               session_id=session_id, actor_kind=actor_kind, identity_port=self.identity_port,
                               clock=self.clock, require_fresh=require_fresh, run_id=run_id)

    def local_context(
        self, *, request_id: Optional[str] = None, session_id: Optional[str] = None,
        actor_kind: ActorKind = ActorKind.INTERACTIVE, run_id: Optional[str] = None,
    ) -> RequestContext:
        """The trusted local-operator context (``:local`` only; DENIED elsewhere)."""
        return local_operator_context(self.settings, request_id=request_id or uuid.uuid4().hex,
                                      session_id=session_id, actor_kind=actor_kind, run_id=run_id)

    # -- direct read call (no planning) ----------------------------------------------

    @staticmethod
    def _read_args(question: str, history: Optional[Sequence[Mapping[str, str]]], source_rag: Optional[bool],
                   use_live_tiled: Optional[bool]) -> Dict[str, Any]:
        args: Dict[str, Any] = {"question": question}
        if history is not None:
            args["history"] = [dict(m) for m in history]
        if source_rag is not None:
            args["source_rag"] = bool(source_rag)
        if use_live_tiled is not None:
            args["use_live_tiled"] = bool(use_live_tiled)
        return args

    async def answer_from_knowledge(
        self,
        ctx: RequestContext,
        question: str,
        *,
        history: Optional[Sequence[Mapping[str, str]]] = None,
        source_rag: Optional[bool] = None,
        use_live_tiled: Optional[bool] = None,
    ) -> Any:
        """Answer ``question`` with the existing F2W QA path, bypassing planning.

        Runs the ``answer_from_knowledge`` tool through this instance's executor
        (capability, schema and deadline checks first) and returns a
        ``ReadOutcome``: ``.result`` is the typed ``ToolResult``; ``.to_legacy()``
        returns the unchanged legacy verdict (or re-raises the original error).
        """
        from .runtime_bridge.qa_tool import TOOL_NAME, outcome_from_ledger

        outcome = await self.executor.aexecute_call(
            ctx, TOOL_NAME, self._read_args(question, history, source_rag, use_live_tiled))
        return outcome_from_ledger(outcome.result, self.ledger, pop=True)

    def answer_from_knowledge_sync(self, ctx: RequestContext, question: str, **kw: Any) -> Any:
        """Synchronous :meth:`answer_from_knowledge` for CLI/worker callers without a running loop.

        Raises:
            RuntimeError: When called from inside a running event loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.answer_from_knowledge(ctx, question, **kw))
        raise RuntimeError("answer_from_knowledge_sync called inside a running event loop; await "
                           "answer_from_knowledge instead")

    # -- delegated task (LangGraph supervisor) -----------------------------------------

    def _require_supervisor(self) -> Any:
        if self.supervisor is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "delegated tasks need a chat model; this application was built without one")
        return self.supervisor

    def _config(self, ctx: RequestContext, thread_id: str) -> Dict[str, Any]:
        from .agent_runtime.checkpointer import thread_config

        return thread_config(ctx, thread_id, recursion_limit=self.limits.recursion_limit)

    @contextmanager
    def _run_scope(self, scoped_thread: str) -> Iterator[None]:
        if self._managed is None:
            yield
            return
        with self._managed.run_scope(scoped_thread):
            yield

    async def delegate(
        self, ctx: RequestContext, task: str, *, thread_id: str, scope: Optional[FrozenSet[str]] = None,
    ) -> DelegatedOutcome:
        """Run one delegated literature task through the supervisor on ``thread_id``.

        The supervisor sees only tools ``ctx`` may use and calls the same
        ``answer_from_knowledge`` tool (and so the same retrieval service) as the
        direct call. Threads are namespaced by principal/session, persist in the
        checkpointer and survive a new application instance built over the same
        saver. Cancelling the awaiting task cancels the run.
        """
        from langchain_core.messages import HumanMessage

        from .agent_runtime.state import RunContext

        graph = self._require_supervisor()
        config = self._config(ctx, thread_id)
        scoped = config["configurable"]["thread_id"]
        with self._run_scope(scoped):
            state = await graph.ainvoke({"messages": [HumanMessage(content=task)]}, config,
                                        context=RunContext(request=ctx, scope=scope))
        return self._delegated_outcome(scoped, state)

    def _delegated_outcome(self, scoped: str, state: Mapping[str, Any]) -> DelegatedOutcome:
        from langchain_core.messages import AIMessage

        from .agent_runtime.message_trimming import current_turn_start
        from .agent_runtime.supervisor import RUNTIME_STOP_KEY
        from .runtime_bridge.qa_tool import TOOL_NAME, outcome_from_ledger

        messages = list(state.get("messages") or [])
        turn = messages[current_turn_start(messages):]
        results: List[ToolResult] = []
        reads: List[Any] = []
        for message in turn:
            art = getattr(message, "artifact", None)
            if getattr(message, "type", None) != "tool" or not isinstance(art, dict) or not art.get("result"):
                continue
            result = ToolResult.model_validate(art["result"])
            results.append(result)
            if result.tool == TOOL_NAME:
                reads.append(outcome_from_ledger(result, self.ledger, pop=False))
        last_ai = next((m for m in reversed(turn) if isinstance(m, AIMessage)), None)
        stopped = (last_ai.response_metadata or {}).get(RUNTIME_STOP_KEY) if last_ai is not None else None
        open_ids = tuple(str(k) for k in (state.get("open_approvals") or {}))
        turn_calls = {str(c.get("id")) for m in turn if isinstance(m, AIMessage)
                      for c in (getattr(m, "tool_calls", None) or [])}
        # Blocked: this turn's last model reply asked for tools and one of them awaits approval.
        blocked = bool(state.get("__interrupt__")) and last_ai is not None and bool(last_ai.tool_calls) and bool(
            turn_calls & set(open_ids))
        text = "" if blocked or last_ai is None or not isinstance(last_ai.content, str) else last_ai.content
        return DelegatedOutcome(thread_id=scoped, final_text=text, reads=tuple(reads), tool_results=tuple(results),
                                interrupted=bool(state.get("__interrupt__")), stopped=stopped,
                                open_approvals=open_ids, blocked=blocked)

    async def answer_via_supervisor(
        self,
        ctx: RequestContext,
        question: str,
        *,
        history: Optional[Sequence[Mapping[str, str]]] = None,
        source_rag: Optional[bool] = None,
        use_live_tiled: Optional[bool] = None,
        thread_id: Optional[str] = None,
        keep_thread: bool = False,
    ) -> SupervisedRead:
        """Answer ``question`` through the planning supervisor (T03B).

        The supervisor plans the turn and calls ``answer_from_knowledge``; the tool
        arguments are pinned to this call's (question verbatim, history, flags), so the
        existing QA path receives exactly what :meth:`answer_from_knowledge` would pass.
        The first read of the turn is returned. If the planner raises or calls no tool,
        the direct call answers instead (``fallback`` says why). The one-turn thread is
        deleted afterwards unless ``keep_thread``.
        """
        from .runtime_bridge.qa_route import pin_args, unpin_args

        self._require_supervisor()
        args = self._read_args(question, history, source_rag, use_live_tiled)
        tid = thread_id or f"qa-{uuid.uuid4().hex}"
        token = pin_args(args)
        delegated: Optional[DelegatedOutcome] = None
        fallback: Optional[str] = None
        try:
            delegated = await self.delegate(ctx, question, thread_id=tid)
        except ContractError:
            raise
        except Exception as exc:  # noqa: BLE001 - planner/provider failure: the direct call answers
            fallback = f"planner_error:{type(exc).__name__}"
            logger.warning("supervisor QA route failed (%s); using the direct call", type(exc).__name__)
        finally:
            unpin_args(token)
            if not keep_thread:
                self._drop_thread(ctx, tid)
        read = None
        if delegated is not None:
            for extra in delegated.reads[1:]:
                self.ledger.get(extra.result.provenance_id, pop=True)
            if delegated.reads:
                read = delegated.reads[0]
                self.ledger.get(read.result.provenance_id, pop=True)
            else:
                fallback = "no_tool_call"
                logger.warning("supervisor QA route made no tool call; using the direct call")
        if read is None:
            read = await self.answer_from_knowledge(ctx, question, history=history, source_rag=source_rag,
                                                    use_live_tiled=use_live_tiled)
        return SupervisedRead(read=read, delegated=delegated, fallback=fallback)

    def _drop_thread(self, ctx: RequestContext, thread_id: str) -> None:
        scoped = self._config(ctx, thread_id)["configurable"]["thread_id"]
        try:
            if self._managed is not None:
                self._managed.delete_thread(scoped)
            elif self._saver is not None and hasattr(self._saver, "delete_thread"):
                self._saver.delete_thread(scoped)
        except Exception:  # noqa: BLE001 - best-effort cleanup of a finished one-turn thread
            logger.debug("could not delete QA thread %s", scoped, exc_info=True)

    async def thread_messages(self, ctx: RequestContext, thread_id: str) -> List[Any]:
        """Checkpointed messages of ``thread_id`` for ``ctx``'s principal/session."""
        graph = self._require_supervisor()
        snapshot = await graph.aget_state(self._config(ctx, thread_id))
        return list((snapshot.values or {}).get("messages") or [])


__all__: Tuple[str, ...] = ("DelegatedOutcome", "F2WApplication", "SupervisedRead")
