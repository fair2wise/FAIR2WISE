"""HTTP transports for selecting the CBORG connection address family."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_global_ipv6(addr: str) -> bool:
    try:
        ip = ipaddress.IPv6Address(addr.split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_global)


def _probe_global_ipv6() -> str | None:
    """Return a globally routed IPv6 the host can use for outbound CBORG."""
    explicit_candidates: list[str] = []
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.connect(("2001:4860:4860::8888", 53))
        addr = sock.getsockname()[0]
        if _is_global_ipv6(addr):
            return addr
        if addr:
            explicit_candidates.append(addr)
    except OSError:
        pass
    finally:
        if sock is not None:
            sock.close()
    for addr in _ifconfig_global_ipv6():
        if addr not in explicit_candidates:
            return addr
    return None


def _ifconfig_global_ipv6() -> list[str]:
    import re
    import subprocess

    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    found: list[str] = []
    for match in re.finditer(r"\binet6\s+([0-9a-fA-F:]+)", out):
        addr = match.group(1)
        if _is_global_ipv6(addr) and addr not in found:
            found.append(addr)
    return found


def _ipv6_bind_assignable(addr: str) -> bool:
    """Return True if this host can bind the given IPv6 source address."""
    host = (addr or "").split("%", 1)[0].strip()
    if not host:
        return False
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind((host, 0))
    except OSError:
        return False
    finally:
        if sock is not None:
            sock.close()
    return True


def _local_address() -> str | None:
    # CBorg IP allowlists are often IPv6. Binding :: can pick a ULA that
    # cannot reach api.cborg.lbl.gov (SYN_SENT hang). Prefer a global unicast.
    # Never persist a stale CBORG_IPV6_BIND; probe a currently assigned GUA.
    want_ipv6 = _truthy(os.environ.get("CBORG_FORCE_IPV6"))
    family = os.environ.get("CBORG_IP_FAMILY", "auto").strip().lower()
    if not want_ipv6:
        if family in {"", "auto"}:
            return None
        if family in {"ipv4", "4"}:
            return "0.0.0.0"
        if family not in {"ipv6", "6"}:
            raise ValueError("CBORG_IP_FAMILY must be auto, ipv4, or ipv6")
    explicit = (os.environ.get("CBORG_IPV6_BIND") or "").strip()
    if explicit and _ipv6_bind_assignable(explicit):
        return explicit
    if explicit:
        logger.warning(
            "CBORG_IPV6_BIND is not assigned on this host; probing a current global IPv6"
        )
    probed = _probe_global_ipv6()
    if probed:
        return probed
    return "::"


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
