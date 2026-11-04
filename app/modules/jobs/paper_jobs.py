"""Job kinds for the graph-enrichment agent: enrichment, topic sweep and PDF parse (B5/J-11, RT-27..RT-32).

The donor enrichment agent exposed three start/status/stop route triples (``/api/graph/enrich``,
``/api/graph/topic_sweep`` and the PDF-parse sweep). F2W gives them one lifecycle, the ``/v1/jobs`` jobs:

====================  ====================================  ==========================================
Donor route           Job lifecycle                         Capability
====================  ====================================  ==========================================
``POST .../enrich``   submit a job of kind ``enrichment``   ``jobs.admin`` (admin-only on ALS)
``GET  .../status``   ``GET /v1/jobs/{job_id}`` (+ events)  creator or ``jobs.admin``, corpus scope
``POST .../stop``     ``POST /v1/jobs/{job_id}/cancel``     creator or ``jobs.admin``
====================  ====================================  ==========================================

This module owns the *kinds*: parameter validation, the durable one-stage pipeline (cancellable between
units, counters published as progress, stable-ID checkpoints so a resumed job skips settled units) and the
re-authorization of the initiating subject before the effect. The work itself is a **handler port**
(:class:`PaperJobHandler`) bound by trusted wiring, so no model, parser or acquisition code runs here and tests
use a fake. What the handler writes into the graph is a proposal for the existing curation review
(``curation.approve``); nothing here changes a graph.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .keys import OperationInputs
from .pipeline import Pipeline, StageContext, StageResult, StageSpec

if TYPE_CHECKING:
    from .service import JobService

ENRICHMENT_KIND = "enrichment"
TOPIC_SWEEP_KIND = "topic_sweep"
PDF_PARSE_KIND = "pdf_parse"
#: Capability that submits these kinds; on ALS the entry dependency re-runs the admin role check.
PAPER_JOB_CAPABILITY = "jobs.admin"
PAPER_JOB_KINDS = (ENRICHMENT_KIND, TOPIC_SWEEP_KIND, PDF_PARSE_KIND)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")
    limit: int | None = Field(default=None, ge=1, le=1_000_000)


class EnrichmentParams(_Params):
    """Parameters of an ``enrichment`` job: enrich the entities of one corpus."""

    entity_ids: list[str] | None = Field(default=None, max_length=100_000)


class TopicSweepParams(_Params):
    """Parameters of a ``topic_sweep`` job: sweep a corpus for topics."""

    topics: list[str] = Field(default_factory=list, max_length=1000)
    year_from: int | None = Field(default=None, ge=1500, le=2200)


class PdfParseParams(_Params):
    """Parameters of a ``pdf_parse`` job: parse the PDFs of one corpus that are not parsed yet."""

    paper_ids: list[str] | None = Field(default=None, max_length=100_000)
    force: bool = False


PARAMS: Mapping[str, type[_Params]] = {
    ENRICHMENT_KIND: EnrichmentParams,
    TOPIC_SWEEP_KIND: TopicSweepParams,
    PDF_PARSE_KIND: PdfParseParams,
}


def validate_params(kind: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Validated, normalized parameters of a paper job.

    Raises:
        ContractError: ``INVALID_INPUT`` for an unknown kind or bad parameters.
    """
    model = PARAMS.get(kind)
    if model is None:
        raise ContractError(ErrorCode.INVALID_INPUT, f"{kind!r} is not a paper job kind")
    try:
        return model.model_validate(dict(params)).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ContractError(ErrorCode.INVALID_INPUT, f"invalid {kind} parameters: {exc.error_count()} error(s)") from None


def job_inputs(kind: str, params: Mapping[str, Any]) -> OperationInputs:
    """:class:`OperationInputs` of a paper job: scope = the corpus, parameters validated."""
    clean = validate_params(kind, params)
    return OperationInputs(corpus_scope=(str(clean["corpus"]),), params=clean)


@dataclass(frozen=True)
class JobControl:
    """What a handler may do while it runs: publish counters, stop cleanly, settle units once."""

    stage: StageContext

    def progress(self, **counters: int) -> None:
        """Publish counters and renew the lease; raises when the job was cancelled or the worker is stopping."""
        self.stage.progress(**counters)

    def guard(self) -> None:
        """Raise at a safe boundary when a stop was requested (``cancel``) or ownership was lost."""
        self.stage.guard()

    def settled(self, unit_id: str, unit_key: str) -> dict[str, Any] | None:
        """Result of a unit an earlier run settled with the same ``unit_key``, if any (resume skips it)."""
        return self.stage.unit(unit_id, unit_key)

    def settle(self, unit_id: str, unit_key: str, result: Mapping[str, Any]) -> None:
        """Checkpoint one finished unit (a paper, an entity, a topic)."""
        self.stage.save_unit(unit_id, unit_key, result)

    def fail(self, unit_id: str, unit_key: str, error: str) -> None:
        """Record a unit that failed; it is retried on resume and the stage reports it incomplete."""
        self.stage.fail_unit(unit_id, unit_key, error)


class PaperJobHandler(Protocol):
    """The work of one kind, bound by trusted wiring. Returns a small JSON-able result (counters, refs)."""

    def __call__(self, ctx: RequestContext, params: Mapping[str, Any], control: JobControl) -> Mapping[str, Any]:
        """Run the job; use ``control`` for progress, checkpoints and stop checks."""
        ...


def build_pipeline(kind: str, handler: PaperJobHandler, context_for_job: Callable[[Any], RequestContext]) -> Pipeline:
    """One-stage pipeline of a paper job kind (``run``), re-authorizing ``jobs.admin`` before the effect."""

    def run(stage: StageContext) -> StageResult:
        stage.require(PAPER_JOB_CAPABILITY)
        params = validate_params(kind, dict(stage.inputs.params or {}))
        result = dict(handler(context_for_job(stage.job), params, JobControl(stage)))
        counters = {k: int(v) for k, v in result.items() if isinstance(v, int) and not isinstance(v, bool)}
        incomplete = tuple(str(u) for u in result.get("incomplete") or ())
        data = {k: v for k, v in result.items() if k != "incomplete"}
        return StageResult(progress=counters, data=data, incomplete=incomplete)

    return Pipeline([StageSpec("run", run, phase="other", relevant=("corpus_scope", "params"),
                               required_capability=PAPER_JOB_CAPABILITY)])


def register_paper_jobs(job_service: JobService, handlers: Mapping[str, PaperJobHandler],
                        context_for_job: Callable[[Any], RequestContext], *,
                        kinds: Iterable[str] = PAPER_JOB_KINDS,
                        **kwargs: Any) -> list[Any]:  # noqa: ANN401  # Any: forwarded verbatim to JobService.register
    """Register the paper job kinds that have a handler on ``job_service`` (trusted wiring only).

    A kind without a handler is not registered, so submitting it is refused (``UNSUPPORTED_CAPABILITY``
    through ``F2WOperations.submit_paper_job``) rather than accepted and never run.
    """
    out = []
    for kind in kinds:
        handler = handlers.get(kind)
        if handler is None:
            continue
        out.append(job_service.register(kind, build_pipeline(kind, handler, context_for_job),
                                        capability=PAPER_JOB_CAPABILITY, **kwargs))
    return out


__all__ = ("ENRICHMENT_KIND", "PAPER_JOB_CAPABILITY", "PAPER_JOB_KINDS", "PDF_PARSE_KIND", "PARAMS",
           "TOPIC_SWEEP_KIND", "EnrichmentParams", "JobControl", "PaperJobHandler", "PdfParseParams",
           "TopicSweepParams", "build_pipeline", "job_inputs", "register_paper_jobs", "validate_params")
