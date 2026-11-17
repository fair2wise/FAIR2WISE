"""Hardened PDF fetching, local-path containment and upload limits (L-19 extended, plan T05).

The donor checked only the *first* URL (``assert_public_http_url``) and then let
``httpx`` follow redirects and buffer the whole body. Here every hop is checked:

* scheme is ``http``/``https``; no credentials in the URL; port in the allowed set;
* the host (or IP literal) resolves only to public addresses: private, loopback,
  link-local, reserved, multicast, unspecified, shared (CGNAT) and IPv4-mapped
  private addresses are refused; an unresolvable host is refused;
* redirects are followed manually, each ``Location`` re-checked, with a hop limit;
* where the transport reports the connected peer, it is checked too (defence
  against DNS rebinding between the check and the connect). Behind an HTTP(S) proxy the peer is
  the proxy, so with the explicit opt-in ``F2W_SAFE_FETCH_TRUST_PROXY=1`` the peer of a request
  that really goes through the configured proxy may be that proxy's own address (and nothing
  else); the target host is still resolved here and refused unless every address is public.
  Without the opt-in the direct path is exactly as above;
* a declared ``Content-Length`` over the limit is refused before reading;
* the body is streamed into the store with a byte limit, a total deadline and a
  ``%PDF-`` signature check on the first bytes; nothing partial is kept.

Adapted from als-computing/forge_rsoxs_analysis src/forge/simulation/net_guard.py
(``is_public_host``, ``assert_public_http_url``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import ipaddress
import logging
import os
from pathlib import Path
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .store import AssetKind, AssetTooLarge, FileDocumentStore, StoreError

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

#: Opt-in (``1``/``true``/``yes``/``on``): trust the HTTP(S) proxy named by ``HTTP_PROXY``/``HTTPS_PROXY``.
TRUST_PROXY_ENV = "F2W_SAFE_FETCH_TRUST_PROXY"

#: ``resolve(host) -> [ip strings]``; raises ``OSError`` when the host does not resolve.
Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` with ``getaddrinfo`` (all families)."""
    return sorted({str(info[4][0]).split("%", 1)[0] for info in socket.getaddrinfo(host, None)})


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast IP (IPv4-mapped IPv6 unwrapped)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:  # not an IP address: not public
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_ADDRESS_SPACE:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified) and ip.is_global


def trust_proxy_from_env(environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``F2W_SAFE_FETCH_TRUST_PROXY`` is set to a true value (default: no)."""
    env = os.environ if environ is None else environ
    return str(env.get(TRUST_PROXY_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _env_value(environ: Mapping[str, str], name: str) -> str:
    """Proxy variable ``name`` in either case (lower case wins, as in curl and ``urllib``); empty when unset."""
    for key in (name.lower(), name.upper()):
        value = str(environ.get(key, "")).strip()
        if value:
            return value
    return ""


def no_proxy_matches(host: str, port: int | None, no_proxy: str) -> bool:
    """Standard ``NO_PROXY`` semantics: ``*``, host names and domain suffixes, IP literals, CIDR blocks.

    Entries are comma or whitespace separated, case-insensitive; ``example.com``, ``.example.com`` and
    ``*.example.com`` match the host and its subdomains; an entry may carry a ``:port``.
    """
    host = host.strip("[]").lower().rstrip(".")
    try:
        host_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        host_ip = None
    for raw in no_proxy.replace(",", " ").split():
        entry = raw.strip().lower()
        if entry == "*":
            return True
        entry_port: int | None = None
        if entry.startswith("["):  # [v6]:port or [v6]
            end = entry.find("]")
            addr_part, rest = entry[1:end], entry[end + 1:]
            entry = addr_part
            if rest.startswith(":") and rest[1:].isdigit():
                entry_port = int(rest[1:])
        elif entry.count(":") == 1:
            name, _, tail = entry.partition(":")
            if tail.isdigit():
                entry, entry_port = name, int(tail)
        if entry_port is not None and entry_port != port:
            continue
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if host_ip is not None and host_ip.version == network.version and host_ip in network:
                return True
            continue
        if host_ip is not None:
            try:
                if host_ip == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                pass
            continue
        domain = entry.removeprefix("*").lstrip(".").rstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def proxy_for_url(url: str, environ: Mapping[str, str]) -> str | None:
    """The proxy URL the environment selects for ``url`` (``None`` when direct).

    ``HTTP_PROXY`` for ``http`` and ``HTTPS_PROXY`` for ``https`` URLs, then ``ALL_PROXY``; a host matched
    by ``NO_PROXY`` goes direct. The result may carry credentials: never log or echo it.
    """
    parts = urlsplit(str(url).strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return None
    proxy = _env_value(environ, f"{scheme}_proxy") or _env_value(environ, "all_proxy")
    if not proxy:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    if no_proxy_matches(parts.hostname, port or (443 if scheme == "https" else 80), _env_value(environ, "no_proxy")):
        return None
    return proxy


def _proxy_host(proxy: str) -> str:
    """Host of a proxy URL (``host:port`` without a scheme is accepted); empty when malformed."""
    try:
        return urlsplit(proxy if "://" in proxy else f"http://{proxy}").hostname or ""
    except ValueError:
        return ""


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
        trust_proxy: Opt-in (``F2W_SAFE_FETCH_TRUST_PROXY``): a request that really goes through the proxy
            from ``HTTP_PROXY``/``HTTPS_PROXY`` (not excluded by ``NO_PROXY``) may be connected to that proxy's
            address although it is private. The target host is still resolved and must be all public.
        proxy_environ: Environment the proxy variables are read from (default: ``os.environ`` at fetch time).
        proxy_resolver: Resolver for the proxy's own host name (injectable).
    """

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_redirects: int = 5
    max_bytes: int = 100 * 1024 * 1024
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float = 120.0
    resolver: Resolver = system_resolver
    trust_proxy: bool = field(default_factory=trust_proxy_from_env)
    proxy_environ: Mapping[str, str] | None = field(default=None, compare=False)
    proxy_resolver: Resolver = system_resolver

    def proxy_addresses(self, url: str) -> frozenset[str]:
        """Addresses of the proxy used for ``url``; empty without the opt-in, a proxy or a resolvable proxy host."""
        if not self.trust_proxy:
            return frozenset()
        proxy = proxy_for_url(url, os.environ if self.proxy_environ is None else self.proxy_environ)
        host = _proxy_host(proxy) if proxy else ""
        if not host:
            return frozenset()
        if _is_ip(host):
            return frozenset({host})
        try:
            return frozenset(a.split("%", 1)[0] for a in self.proxy_resolver(host))
        except OSError:  # proxy host does not resolve: no address is trusted, the peer check stays strict
            return frozenset()

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


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


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


def _canonical(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


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

    def _peer_ok(self, response: httpx.Response, url: str = "") -> bool:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            return True  # transport does not expose the peer (pre-connect check still applied)
        try:
            peer = stream.get_extra_info("server_addr")
        except Exception:  # noqa: BLE001 - transport without a readable peer address: the pre-connect check already applied
            return True
        if not peer:
            return True
        address = str(peer[0]).split("%", 1)[0]
        if is_public_address(address):
            return True
        # Opt-in proxy hop: the peer may be the configured proxy and only it; any other private peer
        # (a request that bypassed the proxy, or a rebound target) is still refused.
        return _canonical(address) in {_canonical(a) for a in self.policy.proxy_addresses(url)}

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
                    if not self._peer_ok(response, current):
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
    def from_paths(cls, roots: Iterable[Any], **kw: Any) -> LocalIntakePolicy:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
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
            except OSError:  # unreadable path component: treat as a link (fail closed)
                return True
        return False

    def require_allowed(self, path: str | os.PathLike[str]) -> Path:
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

    def require_allowed_folder(self, path: Any) -> Path:  # noqa: ANN401  # Any: accepts str, Path or any os.PathLike from untyped callers
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
    "TRUST_PROXY_ENV", "no_proxy_matches", "proxy_for_url", "system_resolver", "trust_proxy_from_env",
)
