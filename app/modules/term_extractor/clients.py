"""Chat-completion clients (Ollama, CBORG) with LLM-call tracing."""

from abc import ABC, abstractmethod
import logging
import time

import openai
import requests

from ..cborg_http import collect_stream_text, openai_http_kwargs, require_cborg_api_key
from ..cborg_limiter import sync_slot
from ..f2w_agent.trace import record_llm_call

logger = logging.getLogger(__name__)


def _traced_chat(model: str, prompt: str, trace_label: str | None, timeout: int, call):
    """Run ``call`` and record the rendered prompt when a turn trace is active."""
    label = trace_label or "chat"
    started = time.perf_counter()
    messages = [{"role": "user", "content": prompt}]
    try:
        result = call()
    except Exception as exc:
        record_llm_call(
            label=label,
            model=model,
            messages=messages,
            response=None,
            latency_ms=(time.perf_counter() - started) * 1000,
            timeout=timeout,
            error=exc,
        )
        raise
    record_llm_call(
        label=label,
        model=model,
        messages=messages,
        response=result,
        latency_ms=(time.perf_counter() - started) * 1000,
        timeout=timeout,
        error=None,
    )
    return result


class ChatClient(ABC):
    """Backend-agnostic interface for single-prompt chat completions."""

    @abstractmethod
    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: str | None = None,
    ) -> str:
        """Send ``prompt`` as one user message and return the reply text."""
        ...


class OllamaChatClient(ChatClient):
    """Chat client for a local Ollama server (``/api/chat``, non-streaming)."""

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base = base_url.rstrip("/")

    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: str | None = None,
    ) -> str:
        """Send ``prompt`` as a single user message and return the reply text.

        Args:
            prompt: User message content.
            temperature: Sampling temperature.
            timeout: HTTP timeout in seconds.
            trace_label: Label recorded for the LLM-call trace; defaults to "chat".

        Raises:
            requests.HTTPError: If the server returns an error status.
        """
        logger.debug("OllamaClient.chat: model=%s prompt_len=%d", self.model, len(prompt))

        def call() -> str:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": temperature},
            }
            response = requests.post(f"{self.base}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            result = response.json().get("message", {}).get("content", "") or ""
            logger.debug("OllamaClient.chat: response_len=%d", len(result))
            return result

        return _traced_chat(self.model, prompt, trace_label, timeout, call)


class CBorgChatClient(ChatClient):
    """OpenAI-compatible CBORG client for https://api.cborg.lbl.gov.

    Env: CBORG_API_KEY, CBORG_BASE_URL
    """

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        import os
        self.model = model
        self.client = openai.OpenAI(
            api_key=require_cborg_api_key(api_key),
            base_url=(base_url or os.environ.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov").rstrip("/"),
            **openai_http_kwargs(asynchronous=False),
        )

    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: str | None = None,
    ) -> str:
        """Send ``prompt`` as a single user message and return the streamed reply text.

        Args:
            prompt: User message content.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.
            trace_label: Label recorded for the LLM-call trace; defaults to "chat".
        """
        logger.debug("CBorgClient.chat: model=%s prompt_len=%d", self.model, len(prompt))

        def call() -> str:
            with sync_slot():
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    timeout=timeout,
                    stream=True,
                )
            result = collect_stream_text(resp)
            logger.debug("CBorgClient.chat: response_len=%d", len(result))
            return result

        return _traced_chat(self.model, prompt, trace_label, timeout, call)


def make_chat_client(
    backend: str,
    model: str,
    *,
    ollama_url: str = "http://localhost:11434",
    cborg_base: str | None = None,
    cborg_api_key: str | None = None,
) -> ChatClient:
    """Create a chat client for the named backend.

    Args:
        backend: ``"ollama"`` (default when empty) or ``"cborg"`` / ``"cborg-openai"``.
        model: Model name passed to the client.
        ollama_url: Ollama server base URL (ollama backend only).
        cborg_base: Override for the CBORG base URL (cborg backends only).
        cborg_api_key: Override for the CBORG API key (cborg backends only).

    Raises:
        ValueError: If ``backend`` is not recognized.
    """
    b = (backend or "ollama").lower()
    logger.debug("make_chat_client: backend=%s model=%s", b, model)
    if b == "ollama":
        return OllamaChatClient(model=model, base_url=ollama_url)
    if b in ("cborg", "cborg-openai"):
        return CBorgChatClient(model=model, api_key=cborg_api_key, base_url=cborg_base)
    raise ValueError(f"Unknown LLM backend: {backend!r}")
