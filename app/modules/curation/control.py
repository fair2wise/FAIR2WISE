"""Operator controls and immutable per-job resolver settings (U-08).

The control file keeps the donor schema (``enabled`` switch and ``budget_cap_usd``) and its
fail-closed reading: an unreadable or invalid file means *disabled*. The donor's
``apply_control_environment`` (mutating ``os.environ`` for later constructors) is replaced by
:class:`ResolverSettings`, a frozen value built once per job and passed explicitly, so no
request or job can change another's limits.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/cborg_control.py (``read_control``,
``write_control``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from ..jobs.process_support import write_text_atomic


@dataclass(frozen=True)
class ResolverSettings:
    """Per-job, immutable resolver settings (no secrets; no model default).

    Attributes:
        remote_enabled: Operator switch for paid/remote model resolution.
        budget_cap_usd: Configured cost limit; ``None`` means no cost limit is configured.
        local_model / remote_model: ``(provider, model)`` pairs chosen by the caller; ``None`` disables that route.
        reserve_usd_per_call: Upper-bound estimate reserved before each remote call.
        timeout_seconds / max_attempts / batch_size: Bounds.
    """

    remote_enabled: bool = False
    budget_cap_usd: float | None = None
    local_model: tuple[str, str] | None = None
    remote_model: tuple[str, str] | None = None
    reserve_usd_per_call: float = 0.05
    timeout_seconds: float = 180.0
    max_attempts: int = 3
    batch_size: int = 8

    def __post_init__(self) -> None:
        if self.budget_cap_usd is not None and (
            not math.isfinite(self.budget_cap_usd) or self.budget_cap_usd <= 0
        ):
            raise ValueError("budget_cap_usd must be a positive finite number or None")
        if not math.isfinite(self.reserve_usd_per_call) or self.reserve_usd_per_call < 0:
            raise ValueError("reserve_usd_per_call must be finite and non-negative")

    @classmethod
    def from_control(cls, control: dict[str, Any], **overrides: Any) -> ResolverSettings:
        """Build from a control mapping (see :func:`read_control`)."""
        return cls(
            remote_enabled=bool(control.get("enabled")), budget_cap_usd=control.get("budget_cap_usd"), **overrides
        )


def read_control(path: Path, *, default_cap: float | None = None) -> dict[str, Any]:
    """Read saved controls, failing closed (disabled) when the file is missing-valued or invalid."""
    defaults: dict[str, Any] = {"enabled": False, "budget_cap_usd": default_cap}
    target = Path(path)
    if not target.exists():
        return defaults
    try:
        saved = json.loads(target.read_text(encoding="utf-8"))
        cap = saved["budget_cap_usd"]
        enabled = saved["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        if cap is not None:
            cap = float(cap)
            if not math.isfinite(cap) or cap <= 0:
                raise ValueError("invalid cap")
        return {"enabled": enabled, "budget_cap_usd": cap}
    except (OSError, ValueError, KeyError, TypeError):
        return defaults


def write_control(path: Path, *, enabled: bool, budget_cap_usd: float | None) -> None:
    """Atomically save a switch or cap change (validate first; the API layer authorizes the caller)."""
    if budget_cap_usd is not None and (not math.isfinite(budget_cap_usd) or budget_cap_usd <= 0):
        raise ValueError("budget_cap_usd must be a positive finite number or None")
    write_text_atomic(Path(path), json.dumps({"enabled": bool(enabled), "budget_cap_usd": budget_cap_usd}))


__all__ = ("ResolverSettings", "read_control", "write_control")
