"""Live list of the models a provider currently offers (owner O-U2: "not hardcoded").

The CBORG backend is OpenAI-compatible, so its listing endpoint is ``GET {base}/models``
(``openai.OpenAI.models.list``). :func:`list_cborg_models_live` asks it, under the shared
CBORG admission slot (:func:`app.modules.cborg_limiter.sync_slot`), and returns a
:class:`ModelListing`. Nothing here is a catalogue: when the endpoint cannot be asked (no
key, network or provider error) the listing is ``unavailable`` with an empty model list and
a short reason. It never falls back to a configured or built-in list, because a stale list
would present models that may no longer exist as "currently available".

The API key is read only through :func:`app.modules.cborg_http.require_cborg_api_key`
(``CBORG_API_KEY`` in the process environment, or an explicit value) and is never placed in
a listing, an error message or a log line.

Used by the validation judge-model picker (``/v1/validation/judge-model``) and the pilot
labelling tool of the binary evaluation suite (``--list-models``). Importing this module has
no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import os
import re
import threading
import time
from typing import Any

DEFAULT_CBORG_BASE = "https://api.cborg.lbl.gov"
#: Seconds a successful listing is reused before the endpoint is asked again.
DEFAULT_TTL_SECONDS = 300.0
#: Seconds an unavailable listing is reused (short, so a fixed key or network shows up quickly).
DEFAULT_FAILURE_TTL_SECONDS = 30.0

_SECRETISH = re.compile(r"(?i)(bearer\s+\S+|sk-[A-Za-z0-9_\-]{6,}|api[_-]?key\S*)")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ModelListing:
    """What one provider listing returned.

    Attributes:
        backend: Provider name (``cborg``).
        source: ``live`` when the listing endpoint answered, else ``unavailable``.
        models: Model ids the endpoint returned, sorted and de-duplicated (empty when unavailable).
        listed_at: UTC time of the request (ISO 8601, ``Z``).
        error: Short reason when ``unavailable`` (exception type and a scrubbed message, never a key).
    """

    backend: str
    source: str
    models: tuple[str, ...] = ()
    listed_at: str = field(default_factory=_utc_now)
    error: str | None = None

    @property
    def available(self) -> bool:
        """True when the provider answered (an empty answer is still a live answer)."""
        return self.source == "live"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {"backend": self.backend, "source": self.source, "models": list(self.models),
                "listed_at": self.listed_at, "error": self.error}


def _scrub(text: str, secret: str | None = None, limit: int = 200) -> str:
    out = str(text or "")
    if secret:
        out = out.replace(secret, "<redacted>")
    out = _SECRETISH.sub("<redacted>", out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 3] + "..."


def _model_ids(page: Any) -> Iterable[str]:
    data = getattr(page, "data", None)
    if data is None and isinstance(page, dict):
        data = page.get("data")
    if data is None:
        data = page
    for item in data or ():
        ident = getattr(item, "id", None)
        if ident is None and isinstance(item, dict):
            ident = item.get("id")
        if isinstance(ident, str) and ident.strip():
            yield ident.strip()


def list_cborg_models_live(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client: Any = None,
    timeout: float = 15.0,
    slot: Callable[[], Any] | None = None,
) -> ModelListing:
    """Ask the CBORG listing endpoint which models it offers right now.

    Args:
        api_key: Explicit key; default ``CBORG_API_KEY`` from the environment.
        base_url: Explicit base URL; default ``CBORG_BASE_URL`` or :data:`DEFAULT_CBORG_BASE`.
        client: An OpenAI-compatible client (tests pass a fake); built from the key otherwise.
        timeout: Request timeout in seconds.
        slot: Context-manager factory holding one admission slot; default the shared CBORG
            ``sync_slot``.

    Returns:
        A ``live`` listing, or ``unavailable`` with the reason. Never raises for a provider fault.
    """
    from .cborg_http import openai_http_kwargs, require_cborg_api_key
    from .cborg_limiter import sync_slot

    key: str | None = None
    if client is None:
        try:
            key = require_cborg_api_key(api_key)
        except RuntimeError:
            return ModelListing("cborg", "unavailable", error="no CBORG API key configured (CBORG_API_KEY)")
        try:
            import openai

            client = openai.OpenAI(
                api_key=key,
                base_url=(base_url or os.environ.get("CBORG_BASE_URL") or DEFAULT_CBORG_BASE).rstrip("/"),
                **openai_http_kwargs(asynchronous=False),
            )
        except Exception as exc:  # noqa: BLE001 - client construction fault is "unavailable"
            return ModelListing("cborg", "unavailable", error=_scrub(f"{type(exc).__name__}: {exc}", key))
    hold = slot or sync_slot
    try:
        with hold():
            page = client.models.list(timeout=timeout)
            models = tuple(sorted(set(_model_ids(page))))
    except Exception as exc:  # noqa: BLE001 - provider/network fault is "unavailable", never a list
        return ModelListing("cborg", "unavailable", error=_scrub(f"{type(exc).__name__}: {exc}", key))
    return ModelListing("cborg", "live", models)


class CachedModelCatalog:
    """Thread-safe cache over a lister, so a settings panel does not ask the provider on every open.

    Args:
        lister: Zero-argument callable returning a :class:`ModelListing`.
        ttl: Seconds a live listing is reused.
        failure_ttl: Seconds an unavailable listing is reused.
        clock: Monotonic clock (injectable for tests).
    """

    def __init__(
        self,
        lister: Callable[[], ModelListing] | None = None,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        failure_ttl: float = DEFAULT_FAILURE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lister = lister or list_cborg_models_live
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: ModelListing | None = None
        self._expires = 0.0

    def get(self, *, refresh: bool = False) -> ModelListing:
        """The cached listing, asking the provider again when it expired or ``refresh`` is set."""
        with self._lock:
            if not refresh and self._cached is not None and self._clock() < self._expires:
                return self._cached
        listing = self.lister()
        with self._lock:
            self._cached = listing
            self._expires = self._clock() + (self.ttl if listing.available else self.failure_ttl)
        return listing


__all__ = ("CachedModelCatalog", "DEFAULT_CBORG_BASE", "ModelListing", "list_cborg_models_live")
