"""Live list of the models the deployment's Ollama server holds (``GET {OLLAMA_URL}/api/tags``).

The browser never calls Ollama: the API asks it, read-only and bounded, and returns an
:class:`OllamaListing`. The listing is a status, never a stale or built-in list: ``unset`` (no
``OLLAMA_URL``), ``unreachable`` (no answer), ``error`` (an answer that is not a model list),
``empty`` (answered, zero models) or ``ok``. A listing never contains the Ollama URL, its host, or
the text of a transport error (those can name the cluster network): the ``error`` is a short fixed
sentence.

Egress: the request goes the way the extraction and chat clients reach the same server (``httpx`` and
``requests`` with the process environment), so ``HTTP(S)_PROXY`` and ``NO_PROXY`` apply identically and a
successful listing predicts a reachable server for a run. An Ollama host on the cluster network must be
named in ``NO_PROXY`` where an egress proxy is set; this module does not change that. Redirects are not
followed, the body is read through a byte cap, and only ``http``/``https`` URLs without credentials are used.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL_ENV = "OLLAMA_URL"
#: Seconds the whole listing may take (connect and read each).
DEFAULT_TIMEOUT = 3.0
#: Most response bytes read; a larger body is an ``error``, never parsed.
MAX_BODY_BYTES = 1_000_000
#: Most model names returned.
MAX_MODELS = 500
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")

#: ``fetch(url, timeout, max_bytes) -> (status, body bytes)``; raises ``httpx.HTTPError`` or ``OSError`` on transport faults.
Fetcher = Callable[[str, float, int], tuple[int, bytes]]


@dataclass(frozen=True)
class OllamaListing:
    """What one ``/api/tags`` request returned.

    Attributes:
        state: ``ok``, ``empty``, ``unset``, ``unreachable`` or ``error``.
        models: Model names, sorted and de-duplicated (empty unless ``ok``).
        error: Short fixed reason for ``unset``/``unreachable``/``error``; ``None`` otherwise.
    """

    state: str
    models: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (no URL, no host)."""
        return {"state": self.state, "models": list(self.models), "error": self.error}


def ollama_base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The configured server base URL (``OLLAMA_URL``), or ``None`` when unset or unusable."""
    env = os.environ if environ is None else environ
    raw = str(env.get(OLLAMA_URL_ENV, "") or "").strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    return raw


def _httpx_fetch(url: str, timeout: float, max_bytes: int) -> tuple[int, bytes]:
    with httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False) as client, \
            client.stream("GET", url, headers={"Accept": "application/json"}) as response:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                return response.status_code, b""  # over the cap: reported as an error by the caller
            chunks.append(chunk)
        return response.status_code, b"".join(chunks)


def _names(payload: object) -> tuple[str, ...] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None
    found: set[str] = set()
    for item in payload["models"]:
        name = item.get("name") or item.get("model") if isinstance(item, dict) else None
        if isinstance(name, str) and _NAME.match(name.strip()):
            found.add(name.strip())
    return tuple(sorted(found)[:MAX_MODELS])


def list_ollama_models(environ: Mapping[str, str] | None = None, *, fetch: Fetcher | None = None,
                       timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_BODY_BYTES) -> OllamaListing:
    """Ask the Ollama server which models it holds. Never raises for a server or network fault."""
    base = ollama_base_url(environ)
    if base is None:
        return OllamaListing("unset", error="OLLAMA_URL is not set on the server")
    try:
        status, body = (fetch or _httpx_fetch)(base + "/api/tags", timeout, max_bytes)
    except Exception as exc:  # noqa: BLE001 - any transport fault is "unreachable"; its text can name the network
        logger.warning("ollama model listing unreachable: %s", type(exc).__name__)
        return OllamaListing("unreachable", error="Ollama did not answer")
    if status != 200:
        return OllamaListing("error", error=f"Ollama answered HTTP {status}")
    if not body:
        return OllamaListing("error", error="Ollama sent no usable model list")
    try:
        names = _names(json.loads(body))
    except ValueError:
        names = None
    if names is None:
        return OllamaListing("error", error="Ollama sent no usable model list")
    return OllamaListing("ok", names) if names else OllamaListing("empty")


__all__ = ("OllamaListing", "list_ollama_models", "ollama_base_url")
