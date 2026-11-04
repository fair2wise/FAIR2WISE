"""Folder intake, verdict by verdict, resumable through the journal (J-05).

The skip list is the product: every file gets a :class:`FileVerdict`. The folder
must pass :class:`~.safe_fetch.LocalIntakePolicy` (trusted roots, no symlinks);
an explicit ordered file list cannot widen it. Intake is injected
(``intake(path) -> IntakeReport``) and so is ingestion (``ingest_one(report) ->
dict``), so the CLI and a service share one vetting ladder. Dry run by default.

Retryable intake outcomes (registry outage, quota pause) and failures are not
checkpointed as settled, so a resumed job retries exactly those. A quota pause
stops the run early (every later file would get the same answer) and is
reported with ``stopped_reason``.

Not ported yet: bounded multi-threaded concurrency and per-DOI locks (the
donor's ``concurrency``/``on_active``); this runs files sequentially.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/folder_ingest.py (``FileVerdict``,
``FolderReport``, ``list_pdfs``, ``ingest_folder``, ``require_allowed_folder``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
import hashlib
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from .ingested import IngestedLookup
from .intake import INGESTABLE, RETRYABLE, IntakeReport
from .safe_fetch import LocalIntakePolicy

if TYPE_CHECKING:
    from .journal import IngestJournal

logger = logging.getLogger(__name__)


@dataclass
class FileVerdict:
    """What would happen, or did happen, to one file."""

    filename: str
    status: str
    detail: str = ""
    doi: str = ""
    title: str = ""
    source_sha256: str = ""
    n_sections: int = 0
    n_figures: int = 0
    duration_s: float = 0.0
    package: str = ""
    resumed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


@dataclass
class FolderReport:
    """All verdicts of one run."""

    folder: str
    applied: bool
    verdicts: list[FileVerdict] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def counts(self) -> dict[str, int]:
        """Verdicts tallied by status."""
        tally: dict[str, int] = {}
        for verdict in self.verdicts:
            tally[verdict.status] = tally.get(verdict.status, 0) + 1
        return tally

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "folder": Path(self.folder).name,
            "applied": self.applied,
            "counts": self.counts,
            "stopped_reason": self.stopped_reason,
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def list_pdfs(folder: Path, *, glob: str = "*.pdf", limit: int = 0) -> list[Path]:
    """Regular files under ``folder`` matching ``glob``, sorted, capped by ``limit``."""
    files = sorted(p for p in Path(folder).rglob(glob) if p.is_file() and not p.is_symlink())
    return files[:limit] if limit else files


def ingest_folder(
    folder: object,
    *,
    policy: LocalIntakePolicy,
    intake: Callable[[Path], IntakeReport],
    apply: bool = False,
    ingest_one: Callable[[IntakeReport], dict[str, Any]] | None = None,
    files: Sequence[Path] | None = None,
    glob: str = "*.pdf",
    limit: int = 0,
    journal: IngestJournal | None = None,
    job_id: str = "",
    already_ingested: IngestedLookup | None = None,
    pipeline: str = "",
    force: bool = False,
) -> Iterator[FolderReport]:
    """Vet (and with ``apply``, ingest) every PDF in an allowed folder; yields after each file.

    Raises:
        ValueError: Folder outside the allowed roots, or ``apply`` without ``ingest_one``.
    """
    if apply and ingest_one is None:
        raise ValueError("ingest_folder(apply=True) needs an ingest_one callable")
    root = policy.require_allowed_folder(folder)
    report = FolderReport(folder=str(root), applied=apply)
    if files is None:
        queue = list_pdfs(root, glob=glob, limit=limit)
    else:
        queue = []
        for path in files:  # the caller orders; it does not widen the boundary
            try:
                resolved = policy.require_allowed(path)
            except ValueError:  # outside the allowed roots: skipped
                continue
            if root in resolved.parents:
                queue.append(resolved)
        queue = queue[:limit] if limit else queue
    for path in queue:
        if journal is not None and journal.stopped(job_id):
            report.stopped_reason = "stop_requested"
            break
        verdict = _process(
            path,
            policy=policy,
            intake=intake,
            apply=apply,
            ingest_one=ingest_one,
            journal=journal,
            job_id=job_id,
            already_ingested=already_ingested,
            pipeline=pipeline,
            force=force,
        )
        report.verdicts.append(verdict)
        yield report
        if verdict.status == "skipped:openalex_quota_paused":
            report.stopped_reason = "quota_paused"
            break
    yield report


def _process(
    path: Path,
    *,
    policy: LocalIntakePolicy,
    intake: Callable[[Path], IntakeReport],
    apply: bool,
    ingest_one: Callable[[IntakeReport], dict[str, Any]] | None,
    journal: IngestJournal | None,
    job_id: str,
    already_ingested: IngestedLookup | None,
    pipeline: str,
    force: bool,
) -> FileVerdict:
    started = time.monotonic()
    try:
        policy.require_allowed(path)
        with path.open("rb") as handle:
            operation = hashlib.file_digest(handle, "sha256").hexdigest()
    except (OSError, ValueError) as exc:
        return FileVerdict(filename=path.name, status="failed:read", detail=str(exc)[:200])
    if journal is not None:
        previous = journal.checkpoint(job_id, operation, "complete")
        if previous:
            return FileVerdict(**{**previous, "filename": path.name, "resumed": True})
    report = intake(path)
    verdict = FileVerdict(
        filename=path.name,
        status=report.status,
        detail=report.detail,
        doi=report.doi,
        title=report.title,
        source_sha256=report.sha256 or operation,
    )
    if report.status in INGESTABLE and already_ingested is not None and not force:
        reason = already_ingested.reason(report.doi, source_sha256=operation, pipeline=pipeline)
        if reason:
            verdict.status, verdict.detail = "skipped:already_ingested", reason
    if apply and verdict.status in INGESTABLE and ingest_one is not None:
        try:
            result = ingest_one(report) or {}
            if result.get("error"):
                raise ValueError(str(result["error"]))
            verdict.n_sections = int(result.get("n_sections") or 0)
            verdict.n_figures = int(result.get("n_figures") or 0)
            verdict.package = str(result.get("package") or "")
            if result.get("extraction_complete") is False:
                verdict.status = "staged:incomplete"
                verdict.detail = f"extraction incomplete: {len(result.get('incomplete_chunks') or [])} chunk(s)"
            else:
                verdict.status = "staged"
                verdict.detail = f"{verdict.n_sections} sections, {verdict.n_figures} figures"
            if already_ingested is not None and verdict.status == "staged":
                already_ingested.record(report.doi, source_sha256=operation, pipeline=pipeline)
        except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the run
            logger.warning("folder ingest failed for one paper: %s", type(exc).__name__, exc_info=True)
            verdict.status, verdict.detail = "failed:ingest", f"{type(exc).__name__}: {str(exc)[:200]}"
    verdict.duration_s = round(time.monotonic() - started, 3)
    if journal is not None:
        unsettled = (
            verdict.status.startswith("failed:")
            or verdict.status in RETRYABLE
            or verdict.status == "staged:incomplete"
        )
        journal.save(
            job_id,
            operation,
            "complete",
            None if unsettled else verdict.as_dict(),
            verdict.detail if unsettled else "",
        )
    return verdict


__all__ = ("FileVerdict", "FolderReport", "ingest_folder", "list_pdfs")
