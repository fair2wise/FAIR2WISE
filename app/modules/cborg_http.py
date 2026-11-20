"""HTTP transports for selecting the CBORG connection address family."""

from __future__ import annotations

import contextvars
import functools
import ipaddress
import logging
import os
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx

from . import tracing

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Callable, Iterable

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_global_ipv6(addr: str) -> bool:
    try:
        ip = ipaddress.IPv6Address(addr.split("%", 1)[0])
    except ValueError:  # not an IPv6 address
        return False
    return bool(ip.is_global)


def _probe_global_ipv6() -> str | None:
    """Return a globally routed IPv6 the host can use for outbound CBORG."""
    explicit_candidates: list[str] = []
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.connect(("2001:4860:4860::8888", 53))
        addr: str = sock.getsockname()[0]
        if _is_global_ipv6(addr):
            return addr
        if addr:
            explicit_candidates.append(addr)
    except OSError as exc:
        logger.debug("IPv6 route probe failed: %s", exc)
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
    except (OSError, subprocess.CalledProcessError):  # ifconfig unavailable
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
    except ValueError:  # not an IPv6 address
        return False
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind((host, 0))
    except OSError:  # address not assignable on this host
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


def _stream_piece(value: object) -> str:
    return value if isinstance(value, str) and value else ""


def stream_chunk_text(chunk: object) -> str:
    """Text from one streamed chat chunk. Uses the last choice, then its delta."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    choice = choices[-1]
    delta = getattr(choice, "delta", None)
    if delta is not None:
        piece = _stream_piece(getattr(delta, "content", None))
        if piece:
            return piece
    message = getattr(choice, "message", None)
    if message is None:
        return ""
    return _stream_piece(getattr(message, "content", None))


#: Seconds to open a connection / wait for the next bytes (httpx limits are per phase, not total).
LLM_CONNECT_TIMEOUT = 10.0
LLM_READ_TIMEOUT = 120.0
#: Seconds one LLM call may take in total, retries and a trickling stream included.
LLM_TOTAL_DEADLINE = 300.0
#: SDK-level retries per request (each already bounded by the timeouts above).
LLM_MAX_RETRIES = 2


def llm_timeout() -> httpx.Timeout:
    """Per-phase HTTP limits for an LLM client (no phase may wait for ever)."""
    return httpx.Timeout(LLM_READ_TIMEOUT, connect=LLM_CONNECT_TIMEOUT)


#: Most calls that outlived their deadline and still run (a transport hung past its socket timeout) at once.
#: A new call is refused beyond it, so a dead provider cannot pile up unbounded threads.
LLM_MAX_ABANDONED = 32
_abandoned = 0
_abandoned_lock = threading.Lock()


def abandoned_calls() -> int:
    """Calls past their deadline whose thread is still running (diagnostics and tests)."""
    with _abandoned_lock:
        return _abandoned


def call_with_deadline[T](call: Callable[[], T], seconds: float = LLM_TOTAL_DEADLINE) -> T:
    """Run ``call`` and return its result, or raise ``TimeoutError`` once ``seconds`` have passed.

    The call runs on a daemon thread so a hung transport cannot block the caller (a worker that must keep
    renewing its lease).

    Residual risk: a blocking call cannot be cancelled from outside. After the deadline the thread is
    abandoned and ends only when its own socket timeout fires (``LLM_READ_TIMEOUT`` per phase, so normally
    within minutes); it holds its socket and memory until then, and its result is discarded. The number of
    such abandoned threads is capped at :data:`LLM_MAX_ABANDONED`: beyond it a new call is refused at once
    with ``TimeoutError`` instead of spawning another thread, and every thread that finally returns frees
    its slot.
    """
    global _abandoned  # noqa: PLW0603 - module-level count of threads that outlived their deadline
    with _abandoned_lock:
        if _abandoned >= LLM_MAX_ABANDONED:
            raise TimeoutError(
                f"LLM call refused: {_abandoned} earlier calls are abandoned and still hung (provider unreachable?)"
            )
    box: dict[str, Any] = {}  # Any: the result or exception of an arbitrary callable
    finished = threading.Event()
    state = {"abandoned": False}

    def runner() -> None:
        global _abandoned  # noqa: PLW0603
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc
        finally:
            with _abandoned_lock:
                finished.set()
                if state["abandoned"]:
                    _abandoned -= 1  # the hung call finally returned: its slot is free

    # With tracing on, the call keeps the caller's span context so its HTTP spans nest under the caller's span;
    # with tracing off the thread starts with an empty context, exactly as before.
    context = contextvars.copy_context() if tracing.is_enabled() else None
    target = runner if context is None else functools.partial(context.run, runner)
    threading.Thread(target=target, name="llm-call", daemon=True).start()
    if not finished.wait(seconds):
        with _abandoned_lock:
            if not finished.is_set():  # decided under the lock, so the runner and this count cannot disagree
                state["abandoned"] = True
                _abandoned += 1
        if state["abandoned"]:
            raise TimeoutError(f"LLM call exceeded its {seconds:g}s deadline")
    if "error" in box:
        raise box["error"]
    value: T = box["value"]
    return value


def collect_stream_text(chunks: Iterable[object], *, deadline: float | None = None) -> str:
    """Join a synchronous ``stream=True`` completion into one string.

    Args:
        chunks: The streamed chunks.
        deadline: ``time.monotonic()`` value after which the stream is abandoned with ``TimeoutError``
            (checked between chunks, so a server that trickles bytes cannot hold the call open for ever).
    """
    parts: list[str] = []
    for chunk in chunks:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("LLM stream exceeded its total deadline")
        parts.append(stream_chunk_text(chunk))
    return "".join(parts)


async def collect_stream_text_async(chunks: AsyncIterable[object]) -> str:
    """Join an async ``stream=True`` completion into one string."""
    parts: list[str] = []
    async for chunk in chunks:
        piece = stream_chunk_text(chunk)
        if piece:
            parts.append(piece)
    return "".join(parts)


def require_cborg_api_key(explicit: str | None = None) -> str:
    """Return the CBORG API key or raise if none is configured (H1-4).

    The openai SDK falls back to ``OPENAI_API_KEY`` when ``api_key`` is empty,
    which would send an OpenAI credential to CBORG. Fail closed instead.
    """
    key = (explicit or os.environ.get("CBORG_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "CBORG_API_KEY is not set; refusing to create a CBORG client "
            "(the OpenAI SDK would otherwise fall back to OPENAI_API_KEY)."
        )
    return key


def openai_http_kwargs(*, asynchronous: bool) -> dict[str, Any]:
    """Return OpenAI client kwargs that pin CBORG to IPv4 or IPv6."""
    local_address = _local_address()
    if local_address is None:
        return {}
    if asynchronous:
        transport: Any = httpx.AsyncHTTPTransport(local_address=local_address)  # Any: reused for the sync transport below
        return {"http_client": httpx.AsyncClient(transport=transport)}
    transport = httpx.HTTPTransport(local_address=local_address)
    return {"http_client": httpx.Client(transport=transport)}
