from abc import ABC, abstractmethod
from typing import Optional

import openai
import requests


class ChatClient(ABC):
    @abstractmethod
    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str: ...


class OllamaChatClient(ChatClient):
    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base = base_url.rstrip("/")

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature},
        }
        r = requests.post(f"{self.base}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "") or ""


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
        )

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            timeout=timeout,
        )
        return resp.choices[-1].message.content or ""


def make_chat_client(
    backend: str,
    model: str,
    *,
    ollama_url: str = "http://localhost:11434",
    cborg_base: Optional[str] = None,
    cborg_api_key: Optional[str] = None,
) -> ChatClient:
    b = (backend or "ollama").lower()
    if b == "ollama":
        return OllamaChatClient(model=model, base_url=ollama_url)
    if b in ("cborg", "cborg-openai"):
        return CBorgChatClient(model=model, api_key=cborg_api_key, base_url=cborg_base)
    raise ValueError(f"Unknown LLM backend: {backend!r}")
