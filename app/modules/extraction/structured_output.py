"""Bounded extraction requests through the existing F2W inference classes (E-06).

The donor built its own vLLM/Ollama LangChain clients and read ``ALS_*``
environment variables for limits. Here every request goes through a client the
caller already built with F2W's own factories (``term_extractor.clients`` or
``extract_terms``), whose ``chat(prompt, *, temperature, timeout[, trace_label])``
signature is the sync path of docs/adr/inference-bridge.md. Nothing in this module
constructs a provider, picks a model, reads the environment or adds a fallback.

What is kept from the donor:

- one shared concurrency budget per stage, injected as a semaphore (the provider
  client still applies F2W admission itself);
- retries owned by the extraction stage, not hidden in the client;
- strict JSON-object parsing that tolerates fences and control characters.

The donor's ``vllm_extraction_options`` (vLLM ``response_format``/thinking flags) is
not ported: it is a transport option for a provider F2W does not use.

Adapted from als-computing/forge_rsoxs_analysis
src/forge/agents/literature/structured_output.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import json
import re
import threading
from typing import Any, Mapping, Protocol


class SyncChatClient(Protocol):
    """Structural type of the existing F2W sync chat clients."""

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        """Return the model's reply text."""


@dataclass(frozen=True)
class Completion:
    """One model reply and the non-secret settings that produced it."""

    text: str
    settings: dict[str, Any] = field(default_factory=dict)


class TextCompletionAdapter:
    """Thin adapter over an existing F2W sync chat client (``complete_text`` in the ADR).

    Args:
        client: An already-constructed F2W chat client (or a test fake).
        temperature: Sampling temperature passed through unchanged.
        timeout: Per-call timeout passed through unchanged.
        trace_label: Optional trace label, passed only when the client accepts it.
        limiter: Optional semaphore shared by every call of one extraction stage.
    """

    def __init__(
        self,
        client: SyncChatClient,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: str | None = None,
        limiter: threading.Semaphore | None = None,
    ) -> None:
        if not callable(getattr(client, "chat", None)):
            raise TypeError("client must expose the F2W chat(prompt, *, temperature, timeout) method")
        self.client = client
        self.temperature = temperature
        self.timeout = timeout
        self.trace_label = trace_label
        self.limiter = limiter
        try:
            params: Mapping[str, inspect.Parameter] = inspect.signature(client.chat).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtins
            params = {}
        self._accepts_trace = "trace_label" in params

    @property
    def model(self) -> str | None:
        """Model name the client reports, if any."""
        return getattr(self.client, "model", None)

    def settings(self) -> dict[str, Any]:
        """Non-secret settings recorded with every completion."""
        return {
            "client_class": f"{type(self.client).__module__}.{type(self.client).__qualname__}",
            "model": self.model, "temperature": self.temperature, "timeout": self.timeout,
        }

    def complete(self, prompt: str) -> Completion:
        """Send one prompt through the wrapped client and return its reply."""
        kwargs: dict[str, Any] = {"temperature": self.temperature, "timeout": self.timeout}
        if self._accepts_trace and self.trace_label:
            kwargs["trace_label"] = self.trace_label
        if self.limiter is None:
            text = self.client.chat(prompt, **kwargs)
        else:
            with self.limiter:
                text = self.client.chat(prompt, **kwargs)
        return Completion(text=str(text or ""), settings=self.settings())


def invoke_extraction(adapter: TextCompletionAdapter, prompt: str) -> Completion:
    """Run one extraction request under the adapter's shared budget (donor entry point)."""
    return adapter.complete(prompt)


_CONTROL_CHARS = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CONTROL_CHARS[0x7F] = None
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def strip_control_chars(text: str) -> str:
    """Remove C0 control bytes, keeping tab/newline/return."""
    return str(text).translate(_CONTROL_CHARS)


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(text, strict=False)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_json_object(reply: str) -> dict[str, Any] | None:
    """Best-effort JSON object from a model reply (fences, chatter, control chars)."""
    cleaned = (reply or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)
    parsed = _loads_object(cleaned)
    if parsed is not None:
        return parsed
    match = _JSON_BLOCK.search(cleaned)
    return _loads_object(match.group(0)) if match else None


__all__ = [
    "Completion", "SyncChatClient", "TextCompletionAdapter", "invoke_extraction", "parse_json_object",
    "strip_control_chars",
]
