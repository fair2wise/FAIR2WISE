"""Legacy/new orchestration selection flag (task T03).

``F2W_RUNTIME`` selects how the existing UI/CLI service answers ordinary
questions. It is read once, when the service starts, never per request:

- ``legacy`` (default, also when unset or blank): the existing
  ``AgentPipelineService`` path, byte-for-byte unchanged.
- ``langgraph``: the ordinary-question read/QA call goes through the
  application service and the runtime's authorizing tool executor
  (``answer_from_knowledge``), and is translated back to the exact legacy
  verdict, so ``ChatResponse``/SSE shapes do not change.

Any other value refuses startup instead of silently picking a mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import os

RUNTIME_ENV = "F2W_RUNTIME"


class RuntimeConfigError(ValueError):
    """The runtime selection is invalid; the service must not start."""


class RuntimeMode(StrEnum):
    """Orchestration used for ordinary read/QA turns."""

    LEGACY = "legacy"
    LANGGRAPH = "langgraph"


def runtime_mode(environ: Mapping[str, str] | None = None) -> RuntimeMode:
    """Return the configured runtime mode.

    Args:
        environ: Startup configuration; ``os.environ`` when ``None``. Called once
            at service construction.

    Returns:
        :attr:`RuntimeMode.LEGACY` unless ``F2W_RUNTIME`` is ``langgraph``.

    Raises:
        RuntimeConfigError: On any value other than ``legacy``/``langgraph``/blank.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(RUNTIME_ENV) or "").strip().lower()
    if not raw:
        return RuntimeMode.LEGACY
    try:
        return RuntimeMode(raw)
    except ValueError:
        raise RuntimeConfigError(
            f"config_rejected: {RUNTIME_ENV} must be 'legacy' or 'langgraph', got {raw!r}") from None


__all__ = ("RUNTIME_ENV", "RuntimeConfigError", "RuntimeMode", "runtime_mode")
