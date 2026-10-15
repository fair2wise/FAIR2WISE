"""Reusable stage handlers that compose the merged T05/T06 pieces without duplicating them.

* :func:`acquire_stage`: asks the T05 acquirer (which checks the content-addressed store first),
  so a crash after a PDF landed in the store but before the stage completion record was written
  is recovered on resume with *no second download*; a stored asset that fails its checksum is
  quarantined instead of trusted.
* :func:`chunked_stage`: per-unit checkpoints (parse pages, extraction chunks). Successful
  compatible units survive a crash; failed units are listed as ``incomplete`` so coverage gaps
  block publication; provider/budget exhaustion pauses the job instead of failing the paper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .pipeline import PauseBudget, PauseProvider, QuarantineStage, StageContext, StageResult, StageRun


def acquire_stage(
    acquirer: Any,
    store: Any,
    *,
    doi_of: Callable[[StageContext], str],
    ctx_for: Callable[[StageContext], RequestContext],
) -> StageRun:
    """Build the acquisition stage over a ``PaperAcquirer``-shaped object and a ``FileDocumentStore``."""

    def run(sc: StageContext) -> StageResult:
        ctx = ctx_for(sc)
        sc.require("ingest.acquire")
        result = acquirer.acquire(ctx, doi_of(sc))
        if not result.ok:
            raise ContractError(
                ErrorCode.UNAVAILABLE if result.status == "no_oa_pdf" else ErrorCode.INVALID_INPUT,
                f"acquisition: {result.status}",
                retryable=result.status == "no_oa_pdf",
            )
        if not store.verify(ctx, result.ref):
            raise QuarantineStage("stored source failed checksum verification", {"ref": result.ref[:19]})
        return StageResult(
            effects=(f"asset:{result.ref}",),
            artifact_refs=(result.ref,),
            data={"ref": result.ref, "status": result.status},
        )

    return run


@dataclass(frozen=True)
class WorkUnit:
    """One independently checkpointed unit of work (a page batch, an extraction chunk)."""

    unit_id: str
    unit_key: str
    payload: Any = None


class BudgetExhausted(Exception):
    """Raised by a unit processor when a configured budget refuses the next call."""


class ProviderUnavailable(Exception):
    """Raised by a unit processor when the provider is down or rate limited."""


def chunked_stage(
    units: Callable[[StageContext], Sequence[WorkUnit]],
    process: Callable[[StageContext, WorkUnit], Mapping[str, Any]],
    *,
    counters: Tuple[str, str] = ("units_done", "units_total"),
) -> StageRun:
    """Build a stage that checkpoints per unit and reports incomplete coverage."""

    def run(sc: StageContext) -> StageResult:
        work = list(units(sc))
        done = 0
        reused = 0
        missing = []
        results: Dict[str, Any] = {}
        sc.progress(**{counters[0]: 0, counters[1]: len(work)})
        for unit in work:
            sc.guard()
            cached = sc.unit(unit.unit_id, unit.unit_key)
            if cached is not None:
                done += 1
                reused += 1
                results[unit.unit_id] = cached
                continue
            try:
                outcome = dict(process(sc, unit))
            except BudgetExhausted as exc:
                raise PauseBudget(str(exc) or "budget exhausted") from exc
            except ProviderUnavailable as exc:
                raise PauseProvider(str(exc) or "provider unavailable") from exc
            except ContractError as exc:
                if exc.code is ErrorCode.BUDGET_EXHAUSTED:
                    raise PauseBudget(str(exc)) from exc
                if exc.code is ErrorCode.DENIED:
                    raise
                sc.fail_unit(unit.unit_id, unit.unit_key, f"{exc.code.value}: {exc}")
                missing.append(unit.unit_id)
                continue
            except Exception as exc:  # noqa: BLE001 - one bad chunk must not lose the others
                sc.fail_unit(unit.unit_id, unit.unit_key, type(exc).__name__)
                missing.append(unit.unit_id)
                continue
            sc.save_unit(unit.unit_id, unit.unit_key, outcome)
            results[unit.unit_id] = outcome
            done += 1
            sc.progress(**{counters[0]: done})
        return StageResult(
            progress={
                counters[0]: done,
                counters[1]: len(work),
                "units_reused": reused,
                "units_failed": len(missing),
            },
            data={"unit_ids": [u.unit_id for u in work]},
            incomplete=tuple(missing),
        )

    return run


__all__ = ("BudgetExhausted", "ProviderUnavailable", "WorkUnit", "acquire_stage", "chunked_stage")
