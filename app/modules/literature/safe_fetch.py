"""Hardened PDF fetching, local-path containment and upload limits (L-19 extended, plan T05).

The donor checked only the *first* URL (``assert_public_http_url``) and then let
``httpx`` follow redirects and buffer the whole body. Here every hop is checked:

* scheme is ``http``/``https``; no credentials in the URL; port in the allowed set;
* the host (or IP literal) resolves only to public addresses: private, loopback,
  link-local, reserved, multicast, unspecified, shared (CGNAT) and IPv4-mapped
  private addresses are refused; an unresolvable host is refused;
* redirects are followed manually, each ``Location`` re-checked, with a hop limit;
* where the transport reports the connected peer, it is checked too (defence
  against DNS rebinding between the check and the connect);
* a declared ``Content-Length`` over the limit is refused before reading;
* the body is streamed into the store with a byte limit, a total deadline and a
  ``%PDF-`` signature check on the first bytes; nothing partial is kept.

Adapted from als-computing/forge_rsoxs_analysis src/forge/simulation/net_guard.py
(``is_public_host``, ``assert_public_http_url``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ipaddress
import logging
from pathlib import Path
import socket
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .store import AssetKind, AssetTooLarge, FileDocumentStore, StoreError

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

#: ``resolve(host) -> [ip strings]``; raises ``OSError`` when the host does not resolve.
Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` with ``getaddrinfo`` (all families)."""
    return sorted({str(info[4][0]).split("%", 1)[0] for info in socket.getaddrinfo(host, None)})


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast IP (IPv4-mapped IPv6 unwrapped)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_ADDRESS_SPACE:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified) and ip.is_global


class BlockedURL(ContractError):
    """A URL (or redirect target) failed the network policy; nothing was fetched from it."""

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(ErrorCode.DENIED, f"blocked URL ({reason})", retryable=False)
        self.reason = reason
        self.url = url


@dataclass(frozen=True)
class NetworkPolicy:
    """Egress rules for third-party PDF URLs.

    Attributes:
        allowed_schemes: URL schemes that may be fetched.
        allowed_ports: Explicit ports allowed (the scheme default is always allowed).
        max_redirects: Redirect hops followed, each re-validated.
        max_bytes: Body byte limit.
        connect_timeout / read_timeout: Per-request ``httpx`` timeouts (seconds).
        total_timeout: Wall-clock limit for one fetch including redirects.
        resolver: DNS resolver (injectable; tests never use the network).
    """

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_redirects: int = 5
    max_bytes: int = 100 * 1024 * 1024
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float = 120.0
    resolver: Resolver = system_resolver

    def check_url(self, url: str) -> str:
        """Return the URL when it passes the policy, else raise :class:`BlockedURL`."""
        try:
            parts = urlsplit(str(url).strip())
            port = parts.port
        except ValueError as exc:
            raise BlockedURL("malformed", str(url)) from exc
        scheme = (parts.scheme or "").lower()
        if scheme not in self.allowed_schemes:
            raise BlockedURL("scheme", url)
        if parts.username or parts.password or "@" in parts.netloc:
            raise BlockedURL("credentials_in_url", url)
        host = parts.hostname or ""
        if not host:
            raise BlockedURL("no_host", url)
        if port is not None and port not in self.allowed_ports:
            raise BlockedURL("port", url)
        try:
            ipaddress.ip_address(host)
            addresses: Sequence[str] = [host]
        except ValueError:
            try:
                addresses = list(self.resolver(host))
            except OSError as exc:
                raise BlockedURL("unresolvable", url) from exc
        if not addresses:
            raise BlockedURL("unresolvable", url)
        if not all(is_public_address(a) for a in addresses):
            raise BlockedURL("private_address", url)
        return str(url).strip()


def assert_public_http_url(url: str, policy: NetworkPolicy | None = None) -> None:
    """Donor-compatible guard: raise ``ValueError`` unless ``url`` passes :class:`NetworkPolicy`."""
    try:
        (policy or NetworkPolicy()).check_url(url)
    except BlockedURL as exc:
        raise ValueError(f"refusing {url!r}: {exc.reason}") from exc


@dataclass
class FetchResult:
    """Outcome of one guarded fetch.

    ``status`` is one of:

    ``stored`` | ``blocked:<reason>`` | ``refused:too_large`` | ``refused:not_pdf`` |
    ``failed:http_<code>`` | ``failed:timeout`` | ``failed:network``.
    """

    url: str
    status: str
    ref: str = ""
    final_url: str = ""
    redirects: list[str] = field(default_factory=list)
    bytes: int = 0

    @property
    def ok(self) -> bool:
        """Whether bytes were stored."""
        return self.status == "stored"

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form (URLs are third-party locations, not store paths)."""
        return asdict(self)


class _Deadline(Exception):
    pass


class SafeFetcher:
    """Fetch third-party PDFs into a :class:`FileDocumentStore` under a :class:`NetworkPolicy`.

    Args:
        http: An ``httpx.Client``; redirects are followed by this class, not by the client.
        policy: Network policy.
        clock: Monotonic clock for the total deadline.
    """

    def __init__(self, http: httpx.Client, policy: NetworkPolicy | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.policy = policy or NetworkPolicy()
        self._clock = clock

    def _peer_ok(self, response: httpx.Response) -> bool:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            return True  # transport does not expose the peer (pre-connect check still applied)
        try:
            peer = stream.get_extra_info("server_addr")
        except Exception:  # noqa: BLE001
            return True
        if not peer:
            return True
        return is_public_address(str(peer[0]))

    def fetch_pdf(self, ctx: RequestContext, url: str, store: FileDocumentStore, *,
                  metadata: Mapping[str, str] | None = None) -> FetchResult:
        """Fetch ``url`` (following validated redirects) and store it if it is a PDF within limits."""
        result = FetchResult(url=url, status="failed:network")
        policy = self.policy
        deadline = self._clock() + policy.total_timeout
        timeout = httpx.Timeout(policy.read_timeout, connect=policy.connect_timeout)
        current = url
        for _hop in range(policy.max_redirects + 1):
            try:
                policy.check_url(current)
            except BlockedURL as exc:
                result.status = f"blocked:{exc.reason}" if current == url else "blocked:redirect_target"
                logger.warning("refusing PDF URL hop (%s)", exc.reason)
                return result
            if self._clock() > deadline:
                result.status = "failed:timeout"
                return result
            try:
                with self.http.stream("GET", current, follow_redirects=False, timeout=timeout,
                                      headers={"Accept": "application/pdf,*/*;q=0.1"}) as response:
                    if not self._peer_ok(response):
                        result.status = "blocked:private_address"
                        return result
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location:
                            result.status = f"failed:http_{response.status_code}"
                            return result
                        current = urljoin(current, location)
                        result.redirects.append(current)
                        continue
                    if response.status_code != 200:
                        result.status = f"failed:http_{response.status_code}"
                        return result
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > policy.max_bytes:
                        result.status = "refused:too_large"
                        return result
                    meta = {"kind": AssetKind.SOURCE_PDF.value, **dict(metadata or {})}
                    asset = store.put_stream(ctx, self._body(response, deadline), media_type="application/pdf",
                                             metadata=meta, max_bytes=policy.max_bytes,
                                             prefix_check=lambda head: head.startswith(PDF_MAGIC))
                    result.status, result.ref, result.final_url, result.bytes = "stored", asset.ref, current, \
                        asset.size
                    return result
            except AssetTooLarge:
                result.status = "refused:too_large"
                return result
            except StoreError as exc:
                result.status = "refused:not_pdf" if exc.code is ErrorCode.INVALID_INPUT else "failed:network"
                return result
            except (_Deadline, httpx.TimeoutException):
                result.status = "failed:timeout"
                return result
            except httpx.HTTPError:
                result.status = "failed:network"
                return result
        result.status = "blocked:too_many_redirects"
        return result

    def _body(self, response: httpx.Response, deadline: float) -> Iterator[bytes]:
        for chunk in response.iter_bytes(64 * 1024):
            if self._clock() > deadline:
                raise _Deadline()
            yield chunk


# ---------------------------------------------------------------------------------------------
# Local paths and uploads
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalIntakePolicy:
    """Where local intake may read, and how large a file may be.

    Roots come from trusted configuration, never from a request. A path is
    resolved (collapsing ``..`` and symlinks) before the containment check, so a
    link cannot walk out of a root; with ``allow_symlinks=False`` (default) a link
    is refused even when it points inside a root.
    """

    allowed_roots: tuple[Path, ...]
    max_bytes: int = 256 * 1024 * 1024
    allow_symlinks: bool = False

    @classmethod
    def from_paths(cls, roots: Iterable[Any], **kw: Any) -> LocalIntakePolicy:
        """Build from configured root paths (resolved once)."""
        resolved: list[Path] = []
        for root in roots:
            path = Path(root).expanduser().resolve()
            if path not in resolved:
                resolved.append(path)
        return cls(tuple(resolved), **kw)

    def _contained(self, resolved: Path) -> bool:
        return any(resolved == root or root in resolved.parents for root in self.allowed_roots)

    def _symlink_inside_root(self, lexical: Path) -> bool:
        """Whether a link sits at or below an allowed root on the way to ``lexical``.

        Links above the roots (``/tmp`` -> ``/private/tmp`` on macOS) are host
        aliases, not intake content, and are allowed.
        """
        for part in [lexical, *lexical.parents]:
            try:
                if part.is_symlink() and self._contained(part.parent.resolve()):
                    return True
            except OSError:
                return True
        return False

    def require_allowed(self, path: Any) -> Path:
        """Return the resolved file path, or raise ``ValueError`` (outside roots, symlink, too large)."""
        candidate = Path(path).expanduser()
        lexical = Path(candidate.absolute())
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"{candidate.name}: not readable") from exc
        if not self._contained(resolved):
            raise ValueError(f"{candidate.name} is not inside an allowed intake directory")
        if not self.allow_symlinks and self._symlink_inside_root(lexical):
            raise ValueError(f"{candidate.name}: symlinks are not followed for intake")
        if resolved.is_file() and resolved.stat().st_size > self.max_bytes:
            raise ValueError(f"{candidate.name} exceeds the {self.max_bytes}-byte intake limit")
        return resolved

    def require_allowed_folder(self, path: Any) -> Path:
        """Return the resolved directory, or raise ``ValueError`` (donor ``require_allowed_folder``)."""
        resolved = self.require_allowed(path)
        if not resolved.is_dir():
            raise ValueError(f"{Path(path).name} is not a directory")
        return resolved


class UploadTooLarge(ContractError):
    """An upload exceeded its byte limit (declared or while streaming)."""

    def __init__(self, limit: int) -> None:
        super().__init__(ErrorCode.INVALID_INPUT, f"upload exceeds {limit} bytes", retryable=False)
        self.limit = limit


def check_declared_length(headers: Mapping[str, str], max_bytes: int) -> None:
    """Refuse an upload by its declared ``Content-Length`` *before* multipart parsing.

    A missing or malformed header is allowed through to :func:`limited_stream`,
    which enforces the limit again while the body is consumed.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    declared = str(lowered.get("content-length", "")).strip()
    if declared.isdigit() and int(declared) > max_bytes:
        raise UploadTooLarge(max_bytes)


def limited_stream(chunks: Iterable[bytes], max_bytes: int) -> Iterator[bytes]:
    """Yield ``chunks`` and raise :class:`UploadTooLarge` as soon as the total exceeds ``max_bytes``."""
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(max_bytes)
        yield chunk


__all__ = (
    "BlockedURL", "FetchResult", "LocalIntakePolicy", "NetworkPolicy", "PDF_MAGIC", "SafeFetcher",
    "UploadTooLarge", "assert_public_http_url", "check_declared_length", "is_public_address", "limited_stream",
    "system_resolver",
)
