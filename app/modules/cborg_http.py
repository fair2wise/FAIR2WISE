"""HTTP transports for selecting the CBORG connection address family."""

from __future__ import annotations

import os
from typing import Any

import httpx


def _local_address() -> str | None:
    family = os.environ.get("CBORG_IP_FAMILY", "auto").strip().lower()
    if family in {"", "auto"}:
        return None
    if family in {"ipv6", "6"}:
        return "::"
    if family in {"ipv4", "4"}:
        return "0.0.0.0"
    raise ValueError("CBORG_IP_FAMILY must be auto, ipv4, or ipv6")


def openai_http_kwargs(*, asynchronous: bool) -> dict[str, Any]:
    """Return OpenAI client kwargs that pin CBORG to IPv4 or IPv6."""
    local_address = _local_address()
    if local_address is None:
        return {}
    if asynchronous:
        transport = httpx.AsyncHTTPTransport(local_address=local_address)
        return {"http_client": httpx.AsyncClient(transport=transport)}
    transport = httpx.HTTPTransport(local_address=local_address)
    return {"http_client": httpx.Client(transport=transport)}
