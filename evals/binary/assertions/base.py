"""Outcome of executing one case oracle once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VERDICTS = ("PASS", "FAIL")


@dataclass
class Outcome:
    """Execution state plus, only for a completed run, a PASS/FAIL verdict.

    ``verdict`` must be None unless ``execution_state`` is ``completed``; the runner
    enforces this so a timeout or skip can never look like a result.
    """

    execution_state: str
    verdict: Optional[str] = None
    actual: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)
