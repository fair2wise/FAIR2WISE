"""Typed report assembly for graph and corpus Knowledge Operations (task T19C).

Every T19C operation returns one :class:`OperationReport`. It carries what
KNOWLEDGE_OPERATIONS.md section 6 asks of a computed result:

* the operation id/version and a **method version** (``kg-statistics/1``...): a change
  to a metric definition is a new method version, never a silent edit;
* validated parameters and the scope (graph ids or a corpus name, never a path);
* source revisions (a snapshot ``sha256:`` for JSON, ``tiled-live:`` for Tiled);
* the population definition and the **counting unit of every metric**;
* :class:`Coverage`: whether iteration reached the end of the population, how many
  records were scanned, and whether the read was snapshot-isolated. **Input coverage
  (``completeness``) is recorded separately from display truncation
  (``display_truncated``)**: a capped scan is ``partial``; a complete scan whose preview
  was shortened stays ``complete`` with ``display_truncated=True``;
* bounded previews, explicit exclusions (with the reason) and warnings.

Reports never contain source bodies: :func:`check_no_source_material` rejects any
payload that carries definitions, context snippets, abstracts, quotes, file paths or
page text (D19, CONTENT_ACCESS_POLICY.md). The full (unbounded) report can be kept as a
private, content-addressed artifact through the injected ``DocumentStore``
(:func:`store_report_artifact`); the bounded copy goes to the model.

Long scans run as jobs: :func:`build_operation_pipeline` wraps any registered
operation in a one-stage :class:`~app.modules.jobs.pipeline.Pipeline` with cancellation
checkpoints, progress counters and an artifact reference (kind
:data:`OPERATION_JOB_KIND`, capability ``ops.run``).

No model is called by any T19C operation, so no provider admission slot is taken; an
operation that later adds model use must take ``admission.slot(provider)`` from the
injected shared admission (``llm_admission``), never a private limiter.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
import json
import re
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import ContractError, ErrorCode, RequestContext, ToolResult, ToolStatus

if TYPE_CHECKING:
    from ..jobs.service import JobService, RegisteredKind

REPORT_SCHEMA: Final = "f2w-operation-report/1"
#: Bound on ``len(json.dumps(ToolResult.data))``; below the executor's 8000-character inline bound.
MAX_RESULT_CHARS = 7000
#: Upper bound of any preview list returned inline.
MAX_PREVIEW_ITEMS = 50
#: Job kind under which trusted wiring registers long-running operations.
OPERATION_JOB_KIND = "knowledge_operation"
#: Capability that submits an operation job (admin / local operator).
OPERATION_JOB_CAPABILITY = "ops.run"

Completeness = Literal["complete", "partial"]
Exactness = Literal["snapshot", "live_unisolated"]

#: Keys that would carry source bodies, private locations or quotations. Never in a report.
FORBIDDEN_KEYS = frozenset({
    "definition", "definitions", "context_snippets", "snippet", "snippets", "text", "page_text",
    "abstract", "abstract_text", "quote", "quotes", "body", "content", "pdf_path", "path", "paths",
    "file_path", "terms_path", "manifest_path", "image", "image_b64", "crop", "crops",
})
#: An absolute POSIX path with at least two segments, or a Windows drive path.
_ABS_PATH = re.compile(r"^(?:/[^/\s]+/[^\s]*|[A-Za-z]:\\)")


def utcnow() -> datetime:
    """Current UTC time (patched in tests)."""
    return datetime.now(UTC)


class Coverage(BaseModel):
    """Input coverage of one scan, independent of how much is displayed.

    Attributes:
        scan_complete: Iteration reached the end of the population listing.
        records_scanned: Records read (entities + edges, or term entries).
        records_expected: Total when the source states one; ``None`` = unknown (never 0).
        exactness: ``snapshot`` (frozen JSON file, hash recorded) or ``live_unisolated``
            (Tiled 0.2.18 offers no revision; concurrent writes may be missed or seen twice).
        completeness: ``complete`` only when ``scan_complete`` and nothing failed.
        reasons: Why the result is partial, or caveats of a complete live scan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scan_complete: bool
    records_scanned: int = Field(ge=0)
    records_expected: int | None = None
    exactness: Exactness = "snapshot"
    completeness: Completeness
    reasons: tuple[str, ...] = ()

    @classmethod
    def of(cls, *, scan_complete: bool, records_scanned: int, exactness: Exactness = "snapshot",
           records_expected: int | None = None, reasons: Sequence[str] = ()) -> Coverage:
        """Build coverage; ``completeness`` follows ``scan_complete`` (never set independently)."""
        return cls(scan_complete=scan_complete, records_scanned=records_scanned, records_expected=records_expected,
                   exactness=exactness, completeness="complete" if scan_complete else "partial",
                   reasons=tuple(reasons))


class OperationReport(BaseModel):
    """Typed, bounded result of one graph/corpus operation.

    ``metrics`` hold the complete computed values; ``preview`` holds bounded example
    lists only. ``counting_units`` names the unit of every metric in ``metrics``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["f2w-operation-report/1"] = REPORT_SCHEMA
    operation: str
    operation_version: str
    method: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    source_revisions: dict[str, str] = Field(default_factory=dict)
    population: str
    counting_units: dict[str, str] = Field(default_factory=dict)
    coverage: Coverage
    metrics: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    display_truncated: bool = False
    excluded: dict[str, str] = Field(default_factory=dict)
    effects: tuple[str, ...] = ("read_corpus",)
    model_calls: int = 0
    warnings: tuple[str, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def completeness(self) -> Completeness:
        """Shortcut for ``coverage.completeness``."""
        return self.coverage.completeness

    def to_data(self) -> dict[str, Any]:
        """JSON-ready payload for ``ToolResult.data`` / job data / artifacts."""
        return self.model_dump(mode="json")


# -- D19 guard -----------------------------------------------------------------------

def check_no_source_material(payload: object, *, where: str = "report") -> None:
    """Raise ``INTERNAL`` if ``payload`` carries a source-body or path-like key, or an absolute path.

    Reports carry computed counts, ids, labels and bibliographic keys only. The check runs
    on every report before it leaves the operation, so a future metric cannot leak
    ``context_snippets``/definitions/paths by accident.
    """
    stack: list[tuple[str, Any]] = [(where, payload)]
    while stack:
        loc, value = stack.pop()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in FORBIDDEN_KEYS:
                    raise ContractError(ErrorCode.INTERNAL, f"{loc}.{key}: source material never enters a report")
                stack.append((f"{loc}.{key}", item))
        elif isinstance(value, (list, tuple)):
            stack.extend((f"{loc}[{i}]", item) for i, item in enumerate(value))
        elif isinstance(value, str) and _ABS_PATH.match(value):
            raise ContractError(ErrorCode.INTERNAL, f"{loc}: a filesystem path never enters a report")


# -- bounding ------------------------------------------------------------------------

def _size(report: OperationReport) -> int:
    return len(json.dumps(report.to_data(), sort_keys=True, default=str))


def fit_report(report: OperationReport, limit: int = MAX_RESULT_CHARS) -> OperationReport:
    """Shorten preview lists (never metrics) until the report serialises within ``limit``.

    Every preview list is first capped at :data:`MAX_PREVIEW_ITEMS`, then all are halved
    together until the report fits; as a last resort the previews are dropped. Any
    shortening sets ``display_truncated`` and adds one warning. ``coverage`` and
    ``metrics`` are never changed, so totals stay the same under truncation (OPS-04).

    Raises:
        ContractError: ``INTERNAL`` if the metrics alone exceed ``limit`` (a bug: metrics
            must be scalars or bounded histograms).
    """
    check_no_source_material(report.to_data())

    def shorten(preview: Mapping[str, Any], cap: int) -> tuple[dict[str, Any], bool]:
        out: dict[str, Any] = {}
        cut = False
        for key, value in preview.items():
            if isinstance(value, list) and len(value) > cap:
                out[key] = value[:cap]
                cut = True
            elif isinstance(value, Mapping):
                out[key], inner = shorten(value, cap)
                cut = cut or inner
            else:
                out[key] = value
        return out, cut

    preview, cut = shorten(report.preview, MAX_PREVIEW_ITEMS)
    candidate = report.model_copy(update={"preview": preview})
    cap = MAX_PREVIEW_ITEMS
    while _size(candidate) > limit and cap > 1:
        cap //= 2
        preview, inner = shorten(report.preview, cap)
        cut = cut or inner
        candidate = report.model_copy(update={"preview": preview})
    if _size(candidate) > limit:
        candidate = report.model_copy(update={"preview": {}})
        cut = cut or bool(report.preview)
    if _size(candidate) > limit:
        raise ContractError(ErrorCode.INTERNAL, f"{report.operation} metrics exceed the {limit}-character bound")
    if cut:
        note = ("display truncated: preview lists were shortened to stay within the result bound; metrics and "
                "coverage are computed over the full scanned population")
        candidate = candidate.model_copy(update={"display_truncated": True,
                                                 "warnings": (*candidate.warnings, note)})
        if _size(candidate) > limit:  # the note itself must fit
            candidate = candidate.model_copy(update={"preview": {}})
    return candidate


def top_items(counter: Mapping[str, int], n: int) -> list[list[Any]]:
    """``[[key, count], ...]`` sorted by count desc, then key; at most ``n``."""
    return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]]


# -- ToolResult mapping ----------------------------------------------------------------

def report_to_result(tool: str, report: OperationReport, *, provenance_id: str | None = None,
                     artifact_refs: Sequence[str] = (), limit: int = MAX_RESULT_CHARS) -> ToolResult:
    """Map a report to a ``ToolResult``: ``OK`` when complete, ``PARTIAL`` otherwise.

    A partial report is never ``OK``: an executor, planner or UI that reads only the
    status still sees that the scan did not cover the population (OPS-03).
    """
    bounded = fit_report(report, limit)
    warnings = list(bounded.warnings)
    if bounded.completeness == "partial":
        warnings.insert(0, "partial: the scan did not cover the whole population; counts are lower bounds over "
                           f"{bounded.coverage.records_scanned} scanned records ({'; '.join(bounded.coverage.reasons)})")
    elif bounded.coverage.exactness == "live_unisolated":
        warnings.insert(0, "live read: the listing reached its end but the backend has no snapshot isolation; "
                           "concurrent writes during the scan may be missed")
    status = ToolStatus.OK if bounded.completeness == "complete" else ToolStatus.PARTIAL
    return ToolResult(tool=tool, status=status, data=bounded.to_data(), artifact_refs=tuple(artifact_refs),
                      warnings=tuple(warnings), provenance_id=provenance_id)


def error_result(tool: str, exc: ContractError, *, provenance_id: str | None = None) -> ToolResult:
    """A typed failure (status follows the error code; the message names no server path)."""
    from ..contracts.tools import status_for_code

    return ToolResult(tool=tool, status=status_for_code(exc.code), error_code=exc.code, retryable=exc.retryable,
                      warnings=(str(exc),), provenance_id=provenance_id)


# -- artifacts -----------------------------------------------------------------------

def store_report_artifact(store: Any, ctx: RequestContext, report: OperationReport) -> str | None:
    """Store the full report as a private JSON artifact and return its ``sha256:`` ref.

    ``store`` is the injected ``DocumentStore`` (``FileDocumentStore`` or the executor's
    artifact store); ``None`` stores nothing. The artifact is internal (D19): it is
    addressed by content hash, never a public URL, and holds computed results only.
    """
    if store is None:
        return None
    payload = report.to_data()
    check_no_source_material(payload)
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return cast(str, store.put(ctx, body, media_type="application/json",  # Any: duck-typed store
                               metadata={"kind": "other", "content": "operation_report", "operation": report.operation,
                                         "method": report.method, "disclosure": "internal"}))


# -- scan control --------------------------------------------------------------------

class ScanControl:
    """Bounds and cancellation for one scan.

    Args:
        max_records: Records read before the scan stops and reports ``partial``.
        checkpoint: Called every ``every`` records with the count so far; a job passes a
            function that publishes progress and raises when the job is stopped
            (``StageContext.guard``), so cancellation lands between pages.
        deadline: Absolute deadline; reaching it stops the scan as ``partial``.
        every: Checkpoint interval.
        job: The scan runs inside a ``knowledge_operation`` job, so inline-only size limits
            (for example the corpus audit's near-duplicate title check above
            ``NEAR_DUPLICATE_INLINE_LIMIT`` keys, owner decision D1.10) do not apply.
    """

    def __init__(self, max_records: int = 200_000, *, checkpoint: Callable[[int], None] | None = None,
                 deadline: datetime | None = None, every: int = 1000,
                 clock: Callable[[], datetime] = utcnow, job: bool = False) -> None:
        if max_records < 1 or every < 1:
            raise ContractError(ErrorCode.INVALID_INPUT, "max_records and every must be >= 1")
        self.max_records = max_records
        self.checkpoint = checkpoint
        self.deadline = deadline
        self.job = job
        self.every = every
        self.clock = clock
        self.count = 0
        self.stopped_reason: str | None = None

    def tick(self, n: int = 1) -> bool:
        """Count ``n`` records; return ``False`` (and record why) when the scan must stop."""
        before = self.count
        self.count += n
        if self.checkpoint is not None and self.count // self.every != before // self.every:
            self.checkpoint(self.count)
        if self.count > self.max_records:
            self.stopped_reason = f"scan bound of {self.max_records} records reached"
            return False
        if self.deadline is not None and self.clock() >= self.deadline:
            self.stopped_reason = "request deadline reached"
            return False
        return True


# -- jobs ----------------------------------------------------------------------------

#: ``(ctx, operation, args, control) -> OperationReport``; :class:`OperationRunner` implementations.
OperationFn = Callable[[RequestContext, str, Mapping[str, Any], ScanControl | None], OperationReport]


def operation_dispatch(*suites: Any) -> OperationFn:
    """One :data:`OperationFn` over operation suites (``GraphOperationsTool``, ``CorpusAuditTool``).

    Each suite provides ``operation_names()`` and ``compute_operation(ctx, name, args, control)``.
    An unknown or duplicated operation name is rejected.
    """
    table: dict[str, Any] = {}
    for suite in suites:
        for name in suite.operation_names():
            if name in table:
                raise ValueError(f"operation {name!r} is provided twice")
            table[name] = suite

    def run(ctx: RequestContext, operation: str, args: Mapping[str, Any],
            control: ScanControl | None) -> OperationReport:
        suite = table.get(operation)
        if suite is None:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown operation {operation!r}")
        return cast(OperationReport, suite.compute_operation(ctx, operation, args, control))  # Any: duck-typed suite

    return run


def build_operation_pipeline(run: OperationFn, context_for_job: Callable[[Any], RequestContext], *,
                             artifact_store: Any = None, max_records: int = 5_000_000) -> Any:
    """One-stage pipeline that runs a registered operation as a durable, cancellable job.

    The job's ``inputs.params`` must be ``{"operation": <tool name>, "args": {...}}``.
    ``context_for_job(job)`` is trusted wiring that rebuilds the initiating subject's
    request context (scope = the job's ``corpus_scope``). The stage publishes
    ``records_scanned`` progress, stops between pages when the job is cancelled, stores
    the full report as a private artifact, and records the bounded report as stage data.
    A partial report completes the job but keeps ``coverage.completeness == "partial"``.
    """
    from ..jobs.pipeline import Pipeline, StageResult, StageSpec

    def compute(stage_ctx: Any) -> Any:
        params = dict(stage_ctx.inputs.params or {})
        operation = str(params.get("operation") or "")
        args = dict(params.get("args") or {})
        req_ctx = context_for_job(stage_ctx.job)

        def checkpoint(count: int) -> None:
            stage_ctx.progress(records_scanned=count)

        control = ScanControl(max_records, checkpoint=checkpoint, deadline=req_ctx.deadline, job=True)
        report = run(req_ctx, operation, args, control)
        ref = stage_ctx.run_effect(f"artifact:{operation}",
                                   lambda: {"ref": store_report_artifact(artifact_store, req_ctx, report)})["ref"]
        bounded = fit_report(report)
        return StageResult(artifact_refs=(ref,) if ref else (),
                           progress={"records_scanned": report.coverage.records_scanned,
                                     "complete": int(report.completeness == "complete")},
                           data=bounded.to_data())

    return Pipeline([StageSpec("compute", compute, phase="other", relevant=("corpus_scope", "params"),
                               required_capability=OPERATION_JOB_CAPABILITY)])


def register_operation_jobs(job_service: JobService, run: OperationFn, context_for_job: Callable[[Any], RequestContext],
                            *, artifact_store: object = None,
                            **kwargs: Any) -> RegisteredKind:  # noqa: ANN401  # Any: forwarded verbatim to JobService.register
    """Register :data:`OPERATION_JOB_KIND` on ``job_service`` (trusted wiring only)."""
    pipeline = build_operation_pipeline(run, context_for_job, artifact_store=artifact_store)
    return job_service.register(OPERATION_JOB_KIND, pipeline, capability=OPERATION_JOB_CAPABILITY, **kwargs)


def stamp(report: OperationReport, started: datetime) -> OperationReport:
    """Set ``started_at``/``finished_at`` (ISO 8601 UTC)."""
    return report.model_copy(update={"started_at": started.isoformat(), "finished_at": utcnow().isoformat()})


def merge_warnings(*groups: Iterable[str]) -> tuple[str, ...]:
    """Concatenate warning groups, dropping repeats, preserving order."""
    out: list[str] = []
    for group in groups:
        for item in group:
            if item and item not in out:
                out.append(item)
    return tuple(out)


__all__: tuple[str, ...] = (
    "Coverage", "FORBIDDEN_KEYS", "MAX_PREVIEW_ITEMS", "MAX_RESULT_CHARS", "OPERATION_JOB_CAPABILITY",
    "OPERATION_JOB_KIND", "OperationFn", "OperationReport", "REPORT_SCHEMA", "ScanControl",
    "build_operation_pipeline", "check_no_source_material", "error_result", "fit_report", "merge_warnings",
    "operation_dispatch",
    "register_operation_jobs", "report_to_result", "stamp", "store_report_artifact", "top_items", "utcnow",
)
