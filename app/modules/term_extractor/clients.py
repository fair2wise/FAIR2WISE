import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import openai
import requests

from app.modules.cborg_limiter import sync_slot
from app.modules.cborg_http import openai_http_kwargs
from app.modules.f2w_agent.trace import record_llm_call

logger = logging.getLogger(__name__)


def _traced_chat(model: str, prompt: str, trace_label: Optional[str], timeout: int, call):
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
    @abstractmethod
    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: Optional[str] = None,
    ) -> str: ...


class OllamaChatClient(ChatClient):
    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base = base_url.rstrip("/")

    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: Optional[str] = None,
    ) -> str:
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
    """OpenAI-compatible CBORG client (https://api.cborg.lbl.gov).
    Env: CBORG_API_KEY, CBORG_BASE_URL
    """
    def __init__(self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None):
        import os
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key or os.environ.get("CBORG_API_KEY"),
            base_url=(base_url or os.environ.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov").rstrip("/"),
            **openai_http_kwargs(asynchronous=False),
        )

    def chat(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        timeout: int = 240,
        trace_label: Optional[str] = None,
    ) -> str:
        logger.debug("CBorgClient.chat: model=%s prompt_len=%d", self.model, len(prompt))

        def call() -> str:
            with sync_slot():
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    timeout=timeout,
                )
            result = resp.choices[-1].message.content or ""
            logger.debug("CBorgClient.chat: response_len=%d", len(result))
            return result

        return _traced_chat(self.model, prompt, trace_label, timeout, call)


def make_chat_client(
    backend: str,
    model: str,
    *,
    ollama_url: str = "http://localhost:11434",
    cborg_base: Optional[str] = None,
    cborg_api_key: Optional[str] = None,
) -> ChatClient:
    b = (backend or "ollama").lower()
    logger.debug("make_chat_client: backend=%s model=%s", b, model)
    if b == "ollama":
        return OllamaChatClient(model=model, base_url=ollama_url)
    if b in ("cborg", "cborg-openai"):
        return CBorgChatClient(model=model, api_key=cborg_api_key, base_url=cborg_base)
    raise ValueError(f"Unknown LLM backend: {backend!r}")
