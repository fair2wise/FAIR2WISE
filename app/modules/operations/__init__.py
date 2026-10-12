"""Knowledge Operations: reviewed, typed computations over authorized literature data (plan 4.10).

``operations`` composes existing domain functions into bounded, typed results
for the agent runtime, the CLI and the legacy UI. It is not a runtime, a second
registry or a general script executor. T19B adds the first operation,
:mod:`.papers` (``lit_summarize_extracted_terms``), which wraps the existing
deterministic ``summarize_extracted_terms`` unchanged.

Importing this package has no side effects: no environment or file reads, no
registry or model construction, and no import of the F2W agent modules (they
load when an operation runs). Public names resolve lazily.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

_EXPORTS: Dict[str, Tuple[str, str]] = {
    "LIT_SUMMARIZE_EXTRACTED_TERMS": ("papers", "LIT_SUMMARIZE_EXTRACTED_TERMS"),
    "SUMMARIZE_LITERATURE_SKILL": ("papers", "SUMMARIZE_LITERATURE_SKILL"),
    "TOOL_NAME": ("papers", "TOOL_NAME"),
    "PaperScope": ("papers", "PaperScope"),
    "PaperSummaryTool": ("papers", "PaperSummaryTool"),
    "PaperTermSummary": ("papers", "PaperTermSummary"),
    "SummaryOutcome": ("papers", "SummaryOutcome"),
    "pinned_paper_scope": ("papers", "pinned_paper_scope"),
    "summarize_paper_terms": ("papers", "summarize_paper_terms"),
    "LegacySummaryPath": ("summary_compat", "LegacySummaryPath"),
    "build_legacy_summary_path": ("summary_compat", "build_legacy_summary_path"),
    "session_paper_resolver": ("summary_compat", "session_paper_resolver"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f"{__name__}.{module}"), attr)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
