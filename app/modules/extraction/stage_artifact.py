"""Reported-evidence persistence for one validated extraction run (T06B deliverable).

:func:`stage_artifact` gathers, for one strategy run over one source version:

- the extraction contract fields: strategy/version, work, source version, parser
  revision, completeness, chunk coverage and inference settings;
- the strategy's ``side_outputs`` and ``unmapped`` **verbatim** (for the default
  strategy: the terms JSON entries, code snippets and metadata exactly as the
  extractor produced them), so no baseline output is lost to the stage;
- the candidate evidence and quantity records the stage wrote, each quantity with
  its decided evidence-source tag, the proposed tag, reasons and any derivation;
- every withheld value with its reasons and tag (F2: captured, not discarded);
- the F4 peak-slot fill, structure claims, the gate snapshot and, only when a
  schema set was selected explicitly, the 0.3.0 candidate payload.

:func:`write_stage_artifact` writes it atomically as one JSON file. The file is a
candidate for review and for the graph build; it is never an import source by
itself, and nothing here publishes (``publish`` stays closed until H5).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..evidence.persistence import InMemoryEvidenceSink
from .contract import ExtractionResult
from .record_validation import StageReport

#: Format version of the artifact written here.
STAGE_ARTIFACT_VERSION = 1


def _coverage(result: ExtractionResult) -> list:
    return [{"chunk_id": c.chunk_id, "section": c.section, "start": c.start, "end": c.end, "ok": c.ok,
             "extracted_by": c.extracted_by, "error": c.error, "reason": c.reason, "settings": dict(c.settings)}
            for c in result.coverage]


def stage_artifact(result: ExtractionResult, report: StageReport, sink: InMemoryEvidenceSink) -> dict[str, Any]:
    """JSON-ready candidate record of one validated run (see module docstring)."""
    if report.strategy != result.strategy or report.work_id != result.work_id:
        raise ValueError("report does not belong to this extraction result")
    snapshot = sink.snapshot()
    return {
        "artifact_version": STAGE_ARTIFACT_VERSION,
        "status": "candidate",
        "extraction": {
            "strategy": result.strategy, "strategy_version": result.strategy_version,
            "contract_version": result.contract_version, "work_id": result.work_id,
            "source_version": result.source_version, "parser_revision": result.parser_revision,
            "complete": result.complete, "incomplete_chunks": list(result.incomplete_chunks),
            "summary": result.summary(), "coverage": _coverage(result), "inference": dict(result.inference),
            "side_outputs": copy.deepcopy(dict(result.side_outputs)),
            "unmapped": copy.deepcopy(dict(result.unmapped)),
        },
        "validation": report.to_dict(),
        "evidence": snapshot["evidence"],
        "quantities": snapshot["quantities"],
    }


def write_stage_artifact(path: Path, result: ExtractionResult, report: StageReport,
                         sink: InMemoryEvidenceSink) -> Path:
    """Write :func:`stage_artifact` atomically to ``path`` and return it."""
    from ..literature.store import atomic_write_json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, stage_artifact(result, report, sink))
    return path


__all__ = ["STAGE_ARTIFACT_VERSION", "stage_artifact", "write_stage_artifact"]
