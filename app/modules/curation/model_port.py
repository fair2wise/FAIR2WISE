"""Model access for review through the EXISTING F2W chat clients (U-04, U-07, U-11).

No new provider, no credential-bearing transport and no default model live here: the caller
names the :class:`ModelRef`, and :class:`ChatClientAdapter` wraps whatever synchronous
``ChatClient`` F2W already builds (``extract_terms.make_chat_client``: Ollama or CBORG). The
adapter reports what the client really exposes: those clients return text only, so cost is
**unknown** unless the caller supplies a ``cost_probe``.

Admission: the resolver holds one shared-admission slot per call. ``CBORGChatClient.chat`` takes
its own slot when shared admission is on, which would double-count (and with a cap of 1,
deadlock), so the adapter calls the client's unadmitted entry point (``_chat_unadmitted``) when
it has one and leaves slot ownership to the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ModelRef:
    """Which model to call. Chosen by configuration, never defaulted here.

    Attributes:
        provider: Provider key used for shared admission (``"cborg"``, ``"ollama"``...).
        model: Model identifier as the existing client expects it.
        local: Whether inference runs locally (no provider billing); only then is cost known to be zero.
    """

    provider: str
    model: str
    local: bool = False


@dataclass(frozen=True)
class ModelReply:
    """A completed call. ``cost_usd`` is ``None`` when the provider reported none (unknown, not free)."""

    text: str
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    executed_model: str = ""


class ModelCallError(Exception):
    """A failed call, with what is known about billing.

    Attributes:
        served: ``False`` only when the request is known never to have been processed (429, refused connection).
        status: HTTP status when known.
        cost_header: Raw cost metadata the provider attached to the failure, if any.
        retry_after: Seconds the provider asked to wait.
    """

    def __init__(
        self,
        message: str,
        *,
        served: bool = True,
        status: int | None = None,
        cost_header: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.served = served
        self.status = status
        self.cost_header = cost_header
        self.retry_after = retry_after


class ModelPort(Protocol):
    """Anything that can complete a prompt."""

    def complete(self, prompt: str, *, ref: ModelRef, timeout: float) -> ModelReply:
        """Return the model's reply or raise :class:`ModelCallError`."""
        ...


class ChatClientAdapter:
    """Adapt F2W's synchronous ``ChatClient`` objects to :class:`ModelPort`.

    Args:
        client_factory: ``ModelRef -> ChatClient`` (for example a closure over ``make_chat_client``).
        cost_probe: Optional ``(client) -> Optional[float]`` returning the last call's cost when the client
            exposes it; absent, cost is unknown.
    """

    def __init__(
        self,
        client_factory: Callable[[ModelRef], Any],
        *,
        cost_probe: Callable[[Any], float | None] | None = None,
    ) -> None:
        self._factory = client_factory
        self._probe = cost_probe

    def complete(self, prompt: str, *, ref: ModelRef, timeout: float) -> ModelReply:
        """Send `prompt` to the client built for `ref` and return its reply.

        Raises:
            ModelCallError: The call failed; other exceptions are translated by ``_translate``.
        """
        client = self._factory(ref)
        call = getattr(client, "_chat_unadmitted", None)
        try:
            if call is not None:
                text = call(prompt, temperature=0.0, timeout=int(timeout))
            else:
                text = client.chat(prompt, temperature=0.0, timeout=int(timeout))
        except ModelCallError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate transport errors into billing knowledge
            raise _translate(exc) from exc
        cost = self._probe(client) if self._probe else None
        return ModelReply(text=str(text or ""), cost_usd=cost, executed_model=ref.model)


def _translate(exc: Exception) -> ModelCallError:
    """Map a client exception to what we know about billing (conservative: unknown unless clearly unserved)."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    cost = headers.get("x-litellm-response-cost") if hasattr(headers, "get") else None
    retry = None
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        retry = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        retry = None
    refused = isinstance(exc, ConnectionRefusedError) or type(exc).__name__ in {"ConnectError", "ConnectionError"}
    return ModelCallError(
        type(exc).__name__,
        served=not (status == 429 or refused),
        status=status if isinstance(status, int) else None,
        cost_header=cost,
        retry_after=retry,
    )


__all__ = ("ChatClientAdapter", "ModelCallError", "ModelPort", "ModelRef", "ModelReply")
