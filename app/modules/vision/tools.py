"""The T09 paper-figure tools as F2W ToolSpecs over :class:`VisionToolset` (opt-in registration).

``vision_extract_figures``, ``vision_classify_figure``, ``vision_read_scale``,
``vision_read_qrange``, ``vision_read_orientation`` and ``vision_extract_tables`` read a paper's
parsed document and PDF through a :class:`PaperSource` and a trusted
:class:`SourceAccessScope` (``FIGURE_CROP`` + ``PDF_BYTES`` + ``PARSED_TEXT``; D19). They are internal
agent functionality: results hold derived findings, locators and evidence references, never image bytes,
store paths or asset URLs, and still go through the disclosure projection before any user surface.

Effects are ``READ_PRIVATE_SOURCE`` (plus ``MODEL_INFERENCE`` only when the toolset was built with a model).
Nothing registers at import: pass the toolset to ``build_read_registry(vision=...)`` or
``build_registry(extra=toolset.registrations())``.

A figure-required call (the default) never succeeds silently on a figure whose text layer is
insufficient: it returns ``UNAVAILABLE`` with ``retryable=True`` (retry with ``use_ocr`` when an OCR engine is
installed, or set ``figure_required=false`` for an explicit text-only/partial job).

Rewritten for F2W; tool names/stages follow PORT_CATALOG V-02..V-08 (BSD-3-Clause donor
als-computing/forge_rsoxs_analysis als_knowledge_agent graph/figure_*.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
import logging
import re
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol
import uuid

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.tools import CostClass, Effect, ToolResult, ToolSpec, ToolStatus
from ..literature.legacy import doi_key
from ..literature.parsed_document import ParsedDocument
from ..security.content_access import (
    ReadPurpose,
    SourceAccessAuthority,
    SourceAccessDenied,
    SourceAccessScope,
    SourceKind,
)
from .classification import ChatAdapter
from .figure_extraction import ExtractionOutcome, FigureAnalysis, extract_figures, figure_id_of
from .ocr import OcrEngine
from .orientation import OrientationStatement, find_statements, read_orientation
from .results import VisionUnavailable, locator
from .skill import VISION_TOOL_NAMES as _SKILL_NAMES
from .tables import extract_tables

if TYPE_CHECKING:
    from ..agent_runtime.executor import ToolInvocation

logger = logging.getLogger(__name__)

TOOL_VERSION = "1.0.0"
_READ = frozenset({"chat.ask"})
_FIG_KINDS = (SourceKind.PDF_BYTES, SourceKind.PARSED_TEXT, SourceKind.FIGURE_CROP)


class LoadedPaper(NamedTuple):
    """A parsed paper and its PDF bytes (both internal)."""

    document: ParsedDocument
    pdf_bytes: bytes
    work_id: str


class PaperSource(Protocol):
    """Resolves a DOI to a parsed document and PDF bytes (host wiring)."""

    def load(self, ctx: RequestContext, doi: str) -> LoadedPaper:  # pragma: no cover - protocol
        """Load or raise a :class:`ContractError` (``NOT_FOUND``, ``UNAVAILABLE``, ...)."""


class StoredPaperSource:
    """:class:`PaperSource` over the T05 store, legacy index and parse service (stored papers only)."""

    def __init__(self, store: Any, index: Any, parse_service: Any) -> None:
        self.store, self.index, self.parse_service = store, index, parse_service

    def load(self, ctx: RequestContext, doi: str) -> LoadedPaper:
        """The stored, verified PDF and its versioned parse; no acquisition happens here."""
        key = doi_key(doi)
        if not key:
            raise ContractError(ErrorCode.INVALID_INPUT, "not a DOI", retryable=False)
        ref = self.index.find_by_doi(doi)
        if not ref or not self.store.verify(ctx, ref):
            raise ContractError(ErrorCode.NOT_FOUND, f"no stored PDF for {key}", retryable=False)
        record = self.parse_service.parse(ctx, ref)
        return LoadedPaper(self.parse_service.load(ctx, record), self.store.get(ctx, ref), key)


ScopeProvider = Callable[[RequestContext], SourceAccessScope]


def figure_scope_provider(
    authority: SourceAccessAuthority, *, ttl: timedelta = timedelta(minutes=2)
) -> ScopeProvider:
    """A scope provider issuing a short-lived figure-analysis grant per call from ``authority``."""

    def provide(ctx: RequestContext) -> SourceAccessScope:
        kinds = (*_FIG_KINDS, SourceKind.SECTION)
        grant = authority.issue(ctx, purpose=ReadPurpose.FIGURE_ANALYSIS, kinds=kinds, ttl=ttl)
        return authority.scope(ctx, grant)

    return provide


_DOI = {"type": "string", "minLength": 4, "maxLength": 300}
_FIG = {"type": "string", "minLength": 1, "maxLength": 80}
_PANEL = {"type": "string", "minLength": 1, "maxLength": 16}
_OUT = {"type": "object", "properties": {"doi": {"type": "string"}, "source_version": {"type": "string"},
                                         "figures": {"type": "array"}}, "required": ["doi", "figures"]}
_COMMON = {
    "doi": _DOI, "figure_id": _FIG, "page": {"type": "integer", "minimum": 1, "maximum": 5000},
    "use_ocr": {"type": "boolean", "description": "Opt in to the OCR fallback if the text layer is insufficient."},
    "figure_required": {"type": "boolean", "description": "Default true: no silent text-only success."},
}


def _spec(name: str, description: str, when: str, when_not: str, props: dict[str, Any], required: Sequence[str],
          output: dict[str, Any], *, model: bool, evidence: str, cost: CostClass = CostClass.MODERATE) -> ToolSpec:
    effects = {Effect.READ_PRIVATE_SOURCE} | ({Effect.MODEL_INFERENCE} if model else set())
    return ToolSpec(
        name=name, version=TOOL_VERSION, domain="vision", description=description, when_to_use=when,
        when_not_to_use=when_not,
        input_schema={"type": "object", "properties": props, "required": list(required),
                      "additionalProperties": False},
        output_schema=output, effects=frozenset(effects), cost_class=cost, required_capabilities=_READ,
        evidence_contract=evidence, idempotent=True,
    )


def build_specs(*, model: bool = False) -> tuple[ToolSpec, ...]:
    """The vision ToolSpecs; ``MODEL_INFERENCE`` is declared only when a model is configured."""
    private = "Results are agent-private derived findings with locators; never publish image bytes or URLs."
    ev = "figure/panel locator (page, figure, panel, bbox in pt, top-left origin) with source_version"
    return (
        _spec("vision_extract_figures",
              "Locate a paper's figures and panels from the PDF text layer: geometry, panels, readings, "
              "uncertainty.",
              "First step for any figure question on an ingested paper.", private, _COMMON, ["doi"], _OUT,
              model=model, evidence=ev),
        _spec("vision_classify_figure",
              "Classify a paper figure or its panels from caption context (text only, no image).",
              "After vision_extract_figures, to learn what kind of figure or panel it is.",
              "Pixel-level interpretation; this reads caption text.",
              {**_COMMON, "panel": _PANEL}, ["doi"], _OUT, model=model,
              evidence=ev),
        _spec("vision_read_scale",
              "Read per-panel scale bars: measured bar length with the caption/text-layer length; "
              "ambiguous ones are withheld.",
              "A length scale for a micrograph panel is needed.",
              "Inferring a scale that the paper does not state.",
              {**_COMMON, "panel": _PANEL}, ["doi"], _OUT, model=model, evidence=ev),
        _spec("vision_read_qrange",
              "Read per-panel q-axis ranges in nm^-1 with the unit source; unidentified or "
              "unit-ambiguous axes are withheld.",
              "The q range of a scattering panel is needed.", "Axes whose unit is only visible in OCR output.",
              {**_COMMON, "panel": _PANEL}, ["doi"], _OUT, model=model, evidence=ev),
        _spec("vision_read_orientation",
              "Record orientation statements the paper makes about a figure, apart from any inference candidates.",
              "Asking how domains or lamellae are oriented in a paper figure.",
              "Treating a visual guess as an observation; candidates are never reportable.", _COMMON, ["doi"],
              _OUT, model=model, evidence=ev),
        _spec("vision_extract_tables",
              "List a paper's tables with cell text and per-cell geometry from the PDF.",
              "Numeric values that a paper reports in tables.", "Table CSV downloads; none are served.",
              {"doi": _DOI, "table_id": _FIG, "page": _COMMON["page"]}, ["doi"],
              {"type": "object", "properties": {"doi": {"type": "string"}, "tables": {"type": "array"}},
               "required": ["doi", "tables"]}, model=False, evidence=ev),
    )


VISION_TOOL_SPECS = build_specs()
VISION_TOOL_NAMES = tuple(s.name for s in VISION_TOOL_SPECS)
assert VISION_TOOL_NAMES == _SKILL_NAMES

_FIGNUM = re.compile(r"^\s*(?:fig(?:ure)?\.?)\s*(\d+)", re.IGNORECASE)


def _failure(tool: str, exc: Exception) -> ToolResult:
    provenance = f"vision:{uuid.uuid4().hex}"
    if isinstance(exc, VisionUnavailable):
        return ToolResult(tool=tool, status=ToolStatus.UNAVAILABLE, error_code=ErrorCode.UNAVAILABLE,
                          retryable=True, provenance_id=provenance,
                          warnings=(f"capability unavailable: {exc.capability}",))
    if isinstance(exc, SourceAccessDenied):
        return ToolResult(tool=tool, status=ToolStatus.DENIED, error_code=ErrorCode.DENIED,
                          provenance_id=provenance, warnings=("source access denied",))
    if isinstance(exc, ContractError):
        status = {ErrorCode.DENIED: ToolStatus.DENIED, ErrorCode.INVALID_INPUT: ToolStatus.INVALID,
                  ErrorCode.UNAVAILABLE: ToolStatus.UNAVAILABLE}.get(exc.code, ToolStatus.ERROR)
        message = "source access denied" if exc.code is ErrorCode.DENIED else str(exc)
        return ToolResult(tool=tool, status=status, error_code=exc.code, retryable=exc.retryable,
                          provenance_id=provenance, warnings=(message,))
    logger.warning("vision tool %s failed: %s", tool, type(exc).__name__)
    return ToolResult(tool=tool, status=ToolStatus.ERROR, error_code=ErrorCode.INTERNAL, provenance_id=provenance,
                      warnings=(f"{tool} failed ({type(exc).__name__})",))


class VisionToolset:
    """The six vision tools bound to a paper source, a trusted scope provider and optional adapters.

    Args:
        source: Resolves DOIs to parsed documents and PDF bytes.
        scope_provider: Trusted source-access scope per request (see :func:`figure_scope_provider`);
            ``None`` makes every tool ``DENIED``.
        model: Optional text-only :class:`ChatAdapter` over an existing F2W chat client.
        ocr_engine: Optional OCR engine for ``use_ocr`` (default: lazy RapidOCR, else retryable unavailable).
    """

    def __init__(self, source: PaperSource, *, scope_provider: ScopeProvider | None = None,
                 model: ChatAdapter | None = None, ocr_engine: OcrEngine | None = None) -> None:
        self.source, self.scope_provider, self.model, self.ocr_engine = source, scope_provider, model, ocr_engine
        self.specs = build_specs(model=model is not None)

    def registrations(self) -> tuple[tuple[ToolSpec, Callable[[Any], ToolResult]], ...]:
        """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
        fns = (self.extract_figures, self.classify_figure, self.read_scale, self.read_qrange,
               self.read_orientation, self.extract_tables)
        return tuple(zip(self.specs, fns))

    # -- shared -------------------------------------------------------------------------------------------
    def _load(self, inv: ToolInvocation, kinds: Sequence[SourceKind]) -> LoadedPaper:
        if self.scope_provider is None:
            raise ContractError(ErrorCode.DENIED, "no trusted source-access scope is configured", retryable=False)
        scope = self.scope_provider(inv.ctx)
        key = doi_key(inv.args["doi"])
        for kind in kinds:
            scope.require(kind, work_id=key)
        return self.source.load(inv.ctx, inv.args["doi"])

    def _outcome(self, inv: ToolInvocation, paper: LoadedPaper) -> ExtractionOutcome:
        a = inv.args
        figure_ids = (a["figure_id"],) if a.get("figure_id") else ()
        return extract_figures(paper.document, paper.pdf_bytes, work_id=paper.work_id, figure_ids=figure_ids,
                               page=a.get("page"), use_ocr=bool(a.get("use_ocr", False)),
                               ocr_engine=self.ocr_engine, model=self.model)

    def _figures_result(self, tool: str, inv: ToolInvocation, paper: LoadedPaper, outcome: ExtractionOutcome,
                        render: Callable[[FigureAnalysis], dict[str, Any]]) -> ToolResult:
        a = inv.args
        provenance = f"vision:{uuid.uuid4().hex}"
        if not outcome.figures:
            if a.get("figure_id"):
                available = [figure_id_of(f) for f in paper.document.figures][:100]
                return ToolResult(tool=tool, status=ToolStatus.ERROR, error_code=ErrorCode.NOT_FOUND,
                                  provenance_id=provenance, data={"available_figures": available},
                                  warnings=(f"no figure {a['figure_id']!r} in this paper",))
            return ToolResult(tool=tool, status=ToolStatus.EMPTY, provenance_id=provenance,
                              warnings=("no figures matched",))
        rows = [render(f) for f in outcome.figures]
        data = {"doi": paper.work_id, "source_version": paper.document.file_sha256,
                "parser": paper.document.parser_key, "figures": rows,
                "capability_errors": outcome.capability_errors}
        warnings: list[str] = []
        for f in outcome.figures:
            warnings += [f"{f.figure_id}: {u.code}" for u in f.uncertainties if u.blocking]
        if outcome.capability_errors and a.get("figure_required", True):
            data["hint"] = ("Retry with use_ocr=true (needs an OCR engine) or figure_required=false "
                            "for a partial job.")
            return ToolResult(tool=tool, status=ToolStatus.UNAVAILABLE, error_code=ErrorCode.UNAVAILABLE,
                              retryable=True, data=data, evidence=outcome.evidence, provenance_id=provenance,
                              warnings=tuple(warnings + [f"{e['figure_id']}: {e['detail']}"
                                                         for e in outcome.capability_errors]))
        warnings += [f"{e['figure_id']}: {e['detail']}" for e in outcome.capability_errors]
        any_unc = any(f.uncertainties for f in outcome.figures)
        return ToolResult(tool=tool, status=ToolStatus.PARTIAL if (warnings or any_unc) else ToolStatus.OK,
                          data=data, evidence=outcome.evidence, warnings=tuple(warnings), provenance_id=provenance)

    def _run(self, tool: str, inv: ToolInvocation, render: Callable[[FigureAnalysis], dict[str, Any]],
             kinds: Sequence[SourceKind] = _FIG_KINDS) -> ToolResult:
        try:
            paper = self._load(inv, kinds)
            return self._figures_result(tool, inv, paper, self._outcome(inv, paper), render)
        except Exception as exc:  # noqa: BLE001 - typed result
            return _failure(tool, exc)

    @staticmethod
    def _head(f: FigureAnalysis) -> dict[str, Any]:
        return {"figure_id": f.figure_id, "page": f.page, "locator": f.locator.model_dump(exclude_none=True),
                "geometry": f.geometry, "text_layer": {"status": f.text_status, "n_spans": f.n_text_spans},
                "uncertainties": [u.as_dict() for u in f.uncertainties]}

    def _panels(self, f: FigureAnalysis, inv: ToolInvocation, pick: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        want = inv.args.get("panel")
        rows = [{"panel_id": p.panel_id, "label": p.label or None, "label_source": p.label_source,
                 "locator": p.locator.model_dump(exclude_none=True), **pick(p)}
                for p in f.panels if not want or p.label == want.lower()]
        return {**self._head(f), "panels": rows}

    # -- tools --------------------------------------------------------------------------------------------
    def extract_figures(self, inv: ToolInvocation) -> ToolResult:
        """``vision_extract_figures``."""
        return self._run(VISION_TOOL_NAMES[0], inv, lambda f: f.as_dict())

    def classify_figure(self, inv: ToolInvocation) -> ToolResult:
        """``vision_classify_figure``: figure verdict, panel verdicts and the units a consumer should act on."""
        def render(f: FigureAnalysis) -> dict[str, Any]:
            row = self._panels(f, inv, lambda p: {"classification": p.classification})
            return {**row, "classification": f.classification,
                    "classification_units": list(f.classification_units)}
        return self._run(VISION_TOOL_NAMES[1], inv, render)

    def read_scale(self, inv: ToolInvocation) -> ToolResult:
        """``vision_read_scale``."""
        def pick(p: Any) -> dict[str, Any]:
            return {"scale": p.scale.as_dict() if p.scale else None}
        return self._run(VISION_TOOL_NAMES[2], inv, lambda f: self._panels(f, inv, pick))

    def read_qrange(self, inv: ToolInvocation) -> ToolResult:
        """``vision_read_qrange``."""
        def pick(p: Any) -> dict[str, Any]:
            return {"q_range": p.qrange.as_dict() if p.qrange else None}
        return self._run(VISION_TOOL_NAMES[3], inv, lambda f: self._panels(f, inv, pick))

    def read_orientation(self, inv: ToolInvocation) -> ToolResult:
        """``vision_read_orientation``: source statements (caption + citing prose) and inference candidates."""
        tool = VISION_TOOL_NAMES[4]
        try:
            paper = self._load(inv, (*_FIG_KINDS, SourceKind.SECTION))
            doc = paper.document
            wanted = inv.args.get("figure_id")
            figs = [f for f in doc.figures if not wanted or figure_id_of(f) == wanted]
            if inv.args.get("page"):
                figs = [f for f in figs if f.page == inv.args["page"]]
            if not figs:
                return ToolResult(tool=tool, status=ToolStatus.EMPTY, warnings=("no figures matched",))
            analysed = {a.figure_id: a for a in self._outcome(inv, paper).figures}
            rows = []
            for fig in figs:
                fid = figure_id_of(fig)
                loc = locator(fig.page, fid)
                stmts: list[OrientationStatement] = find_statements(fig.caption, loc, "caption")
                m = _FIGNUM.match(fig.caption or "")
                if m:
                    cite = re.compile(rf"\bfig(?:ure)?s?\.?\s*{m.group(1)}\b", re.IGNORECASE)
                    for sec in doc.sections:
                        sloc = locator(sec.page, None, None, None, section=sec.name)
                        stmts += [s for s in find_statements(sec.text, sloc, "section") if cite.search(s.sentence)]
                a = analysed.get(fid)
                found = read_orientation(fid, stmts, caption=fig.caption, verdict=a.classification if a else None,
                                         model=self.model, locator=loc)
                rows.append({**found.as_dict(), "locator": loc.model_dump(exclude_none=True)})
            return ToolResult(tool=tool, status=ToolStatus.PARTIAL if any(r["reported"] is None for r in rows)
                              else ToolStatus.OK,
                              data={"doi": paper.work_id, "source_version": doc.file_sha256, "figures": rows},
                              provenance_id=f"vision:{uuid.uuid4().hex}")
        except Exception as exc:  # noqa: BLE001 - any extractor failure is returned as a failed ToolResult, never raised into the agent loop
            return _failure(tool, exc)

    def extract_tables(self, inv: ToolInvocation) -> ToolResult:
        """``vision_extract_tables``."""
        tool = VISION_TOOL_NAMES[5]
        try:
            paper = self._load(inv, (SourceKind.PDF_BYTES, SourceKind.PARSED_TEXT))
            ids = (inv.args["table_id"],) if inv.args.get("table_id") else ()
            tables = extract_tables(paper.document, paper.pdf_bytes, work_id=paper.work_id, table_ids=ids,
                                    page=inv.args.get("page"))
            if not tables:
                return ToolResult(tool=tool, status=ToolStatus.EMPTY, warnings=("no tables matched",))
            warnings = tuple(f"{t.table_id}: {u.code}" for t in tables for u in t.uncertainties)
            return ToolResult(
                tool=tool, status=ToolStatus.PARTIAL if warnings else ToolStatus.OK, warnings=warnings,
                data={"doi": paper.work_id, "source_version": paper.document.file_sha256,
                      "tables": [t.as_dict() for t in tables]},
                evidence=tuple(e for t in tables for e in t.evidence), provenance_id=f"vision:{uuid.uuid4().hex}")
        except Exception as exc:  # noqa: BLE001 - any extractor failure is returned as a failed ToolResult, never raised into the agent loop
            return _failure(tool, exc)


__all__ = ("LoadedPaper", "PaperSource", "ScopeProvider", "StoredPaperSource", "TOOL_VERSION", "VISION_TOOL_NAMES",
           "VISION_TOOL_SPECS", "VisionToolset", "build_specs", "figure_scope_provider")
