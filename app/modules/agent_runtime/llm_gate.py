"""Admitted, budgeted, deadline-bounded chat calls for the routing and debate agents (T17B P2).

Owner decision 2026-11-09 (fourteenth set): the orchestrator and debate agents run on LangGraph and
make every LLM call through ``agent_runtime.admission``, counted against the curation budgets.

:class:`AdmittedChat` is the only object those agents hold: it has no public path to the chat client.
One call does, in order:

1. reserve budget (remote models only; a refused reservation raises :class:`LlmBudgetRefused` and
   nothing else happens: no slot, no client, no call);
2. take an admission slot (bounded concurrency; a refusal releases the reservation);
3. run the client under the injected deadline helper (``cborg_http.call_with_deadline``);
4. settle the reservation exactly once: unknown cost unless the client reported one, released only
   when the request is known never to have been served.

Everything that touches ``app.modules`` outside this package (the deadline helper, the budget ledger,
the provider admission, the client factory, trace context binding) is injected, so the runtime keeps its
import boundary.
"""

from __future__ import annotations

from collections.abc import Callable
import contextvars
from typing import Any, Protocol

from .admission import Admission


class LlmCallRefused(RuntimeError):
    """Base of the typed refusals raised before a model call is made."""


class LlmBudgetRefused(LlmCallRefused):
    """The curation budget would be exceeded; the call was not made."""


class ChatClientPort(Protocol):
    """The synchronous single-prompt client the F2W chat clients implement."""

    def chat(self, prompt: str, *, temperature: float, timeout: int, trace_label: str | None) -> str:
        """Return the reply text."""
        ...


class BudgetPort(Protocol):
    """Reserve and settle spend for one call (the curation ``BudgetLedger`` adapter implements it)."""

    def reserve(self, *, model: str, label: str) -> str | None:
        """Return a reservation id, or ``None`` when a cap would be exceeded."""
        ...

    def settle(self, reservation: str, *, cost_usd: float | None, served: bool) -> None:
        """Settle once: a known cost, unknown (``cost_usd is None`` and ``served``), or released (not served)."""
        ...


#: ``(call, seconds) -> result`` raising ``TimeoutError`` past the deadline (``cborg_http.call_with_deadline``).
DeadlineRunner = Callable[[Callable[[], str], float], str]

#: Provider key of the shared admission controller for these agents (cap: ``F2W_AGENT_LLM_MAX_CONCURRENCY``).
AGENT_ADMISSION_KEY = "agent_llm"


def _never_served(exc: BaseException) -> bool:
    """True only when the request is known not to have reached the provider (429, refused connection)."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429 or isinstance(exc, ConnectionRefusedError) or type(exc).__name__ in {
        "ConnectError", "ConnectionError"}


class AdmittedChat:
    """Chat access whose every call is budgeted, admitted and bounded by a deadline.

    Args:
        client_factory: Builds the chat client lazily, inside the admission slot (so a missing key is an
            ordinary call failure, as before the port).
        model: Model name, used for the budget reservation.
        admission: Required; ``None`` is rejected (no call without admission).
        run_with_deadline: Deadline helper; required.
        budget: Optional budget port. Without one the call is admitted but not cost-counted.
        local: A local model is never reserved (it is not billed).
        admission_key: Provider key for the admission slot.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], ChatClientPort],
        model: str,
        admission: Admission,
        run_with_deadline: DeadlineRunner,
        budget: BudgetPort | None = None,
        local: bool = False,
        admission_key: str = AGENT_ADMISSION_KEY,
    ) -> None:
        if admission is None or not (callable(getattr(admission, "slot", None))):
            raise TypeError("an admission with slot(provider) is required; LLM calls are never made without one")
        self._factory = client_factory
        self._model = model
        self._admission = admission
        self._deadline = run_with_deadline
        self._budget = budget
        self._local = local
        self._key = admission_key

    def complete(self, prompt: str, *, timeout: int, label: str, temperature: float = 0.0) -> str:
        """Return the model's reply.

        Raises:
            LlmBudgetRefused: A cap would be exceeded (no call was made).
            TimeoutError: The deadline passed (the reservation stays charged as unknown).
            Exception: Whatever the client raised, after settlement.
        """
        reservation: str | None = None
        if self._budget is not None and not self._local:
            reservation = self._budget.reserve(model=self._model, label=label)
            if reservation is None:
                raise LlmBudgetRefused(f"LLM call '{label}' refused: curation budget would be exceeded")
        ctx = contextvars.copy_context()  # the worker thread sees the caller's trace context
        sent = False

        def call() -> str:
            client = self._factory()
            return client.chat(prompt, temperature=temperature, timeout=timeout, trace_label=label)

        def run() -> str:
            nonlocal sent
            sent = True
            return self._deadline(lambda: ctx.run(call), float(timeout) * 3)

        try:
            with self._admission.slot(self._key):
                text = run()
        except BaseException as exc:
            self._settle(reservation, served=sent and not _never_served(exc))
            raise
        self._settle(reservation, served=True)
        return text

    def _settle(self, reservation: str | None, *, served: bool) -> None:
        if reservation is not None and self._budget is not None:
            self._budget.settle(reservation, cost_usd=None, served=served)


__all__ = ("AGENT_ADMISSION_KEY", "AdmittedChat", "BudgetPort", "ChatClientPort", "DeadlineRunner",
           "LlmBudgetRefused", "LlmCallRefused")
