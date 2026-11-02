"""``download_pdf(url, dest) -> bool`` on top of the hardened T05 fetcher (addendum A7 T29, B29).

The legacy :class:`~app.modules.f2w_agent.download_agent.DownloadAgent` saves PDFs to
``<dir>/<name>.pdf`` with the signature ``download_pdf(url, dest) -> bool``. This keeps that
contract and replaces the transport with :class:`.safe_fetch.SafeFetcher`: scheme, port,
credential and private-address checks on every redirect hop, a byte limit, a wall-clock
deadline and PDF magic-byte verification before anything is stored.

Visible behaviour is preserved: ``True`` only when a valid PDF is at ``dest``; otherwise
``False`` and nothing (no partial file) at ``dest``; a non-PDF answer is not retried; HTTP and
network failures are retried up to ``retries`` times with a pause between attempts. Bytes are
fetched into a private temporary content-addressed store and copied out atomically, so
``dest`` never holds a partial file.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import tempfile
import time

import httpx

from ..contracts.context import AuthorizationMode, DeploymentProfile, Principal, RequestContext
from .safe_fetch import NetworkPolicy, SafeFetcher
from .store import FileDocumentStore, StoreError

logger = logging.getLogger(__name__)

#: Same request headers as the legacy downloader (some repositories reject anonymous clients).
DOWNLOAD_HEADERS = {
    "User-Agent": "FAIRtoWISE-harvest/1.0 (legal OA PDF fetch)",
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
}
#: Statuses that may succeed on a retry (the legacy code retried every exception).
RETRYABLE_PREFIXES = ("failed:",)


def _internal_context() -> RequestContext:
    """Context for the private temporary store: it grants nothing and authorises nothing."""
    return RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                          principal=Principal(subject="local-operator"), request_id="legacy-download",
                          capabilities=frozenset())


def default_fetcher(policy: NetworkPolicy | None = None) -> SafeFetcher:
    """A :class:`SafeFetcher` on a fresh ``httpx.Client`` carrying the legacy headers."""
    return SafeFetcher(httpx.Client(headers=DOWNLOAD_HEADERS), policy)


def hardened_download_pdf(url: str, dest: str, retries: int = 3, *, fetcher: SafeFetcher | None = None,
                          sleep: Callable[[float], None] = time.sleep, retry_pause: float = 5.0) -> bool:
    """Download ``url`` to ``dest`` through the hardened fetcher; ``True`` on success.

    Args:
        url: PDF URL (already filtered to reliable hosts by the caller).
        dest: Destination file path.
        retries: Attempts for retryable failures (HTTP errors, network, timeout).
        fetcher: Injected fetcher (tests); a default one is built and closed otherwise.
        sleep: Pause function between attempts.
        retry_pause: Seconds between attempts.
    """
    dest_path = Path(dest)
    ctx = _internal_context()
    own = fetcher is None
    active = fetcher or default_fetcher()
    try:
        for attempt in range(1, max(1, retries) + 1):
            with tempfile.TemporaryDirectory(prefix="f2w-dl-") as scratch:
                store = FileDocumentStore(Path(scratch))
                result = active.fetch_pdf(ctx, url, store)
                if result.ok:
                    try:
                        store.copy_out(ctx, result.ref, dest_path, overwrite=True)
                    except (OSError, StoreError) as exc:
                        logger.warning("Could not save %s (%s)", dest_path.name, type(exc).__name__)
                        return False
                    logger.info("Saved PDF to %s", dest)
                    return True
            logger.warning("Download of %s did not succeed (%s)", url, result.status)
            if not result.status.startswith(RETRYABLE_PREFIXES) or attempt >= retries:
                break
            sleep(retry_pause)
        return False
    finally:
        if own:
            active.http.close()


__all__ = ("DOWNLOAD_HEADERS", "default_fetcher", "hardened_download_pdf")
