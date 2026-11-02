"""Admission seam for model and provider calls (task T02).

The runtime never owns provider concurrency. It takes an injected admission
object with the interface of the shared CBORG controller
(``app.modules.llm_admission.ProviderAdmission``, H1-3):

- ``admission.slot(provider)``: synchronous context manager.
- ``admission.aslot(provider)``: asynchronous context manager.

This module only defines that structural interface and a no-op default, so the
runtime can be built and tested without importing the real controller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Admission(Protocol):
    """Anything with ``slot``/``aslot`` context managers keyed by provider name."""

    def slot(self, provider: str) -> AbstractContextManager[Any]:
        """Hold one synchronous admission slot for ``provider``."""
        ...

    def aslot(self, provider: str) -> AbstractAsyncContextManager[Any]:
        """Hold one asynchronous admission slot for ``provider``."""
        ...


class NoOpAdmission:
    """Admission that admits everything immediately. The default when none is injected."""

    @contextmanager
    def slot(self, provider: str) -> Iterator[None]:
        """Yield immediately."""
        yield

    @asynccontextmanager
    async def aslot(self, provider: str) -> AsyncIterator[None]:
        """Yield immediately."""
        yield


def ensure_admission(admission: Any) -> Any:
    """Return ``admission`` or a :class:`NoOpAdmission`, rejecting objects without both slots.

    Raises:
        TypeError: If ``admission`` lacks ``slot`` or ``aslot``.
    """
    if admission is None:
        return NoOpAdmission()
    if not (callable(getattr(admission, "slot", None)) and callable(getattr(admission, "aslot", None))):
        raise TypeError("admission must provide slot(provider) and aslot(provider) context managers")
    return admission
