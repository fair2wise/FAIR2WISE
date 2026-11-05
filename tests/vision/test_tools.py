"""T09 tools: registration, access, capability errors, result safety and the science helpers."""

import json
import time
from datetime import timedelta

import pytest
from vision_fakes import CAPTION, FIG, bl_bbox, multi_panel_pdf, parsed, raster_figure_pdf

from app.modules.agent_runtime.executor import ToolExecutor
from app.modules.contracts.context import (
    AuthorizationMode, DeploymentProfile, Principal, RequestContext,
)
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.tools import Effect, ToolStatus
from app.modules.literature.parsed_document import ParsedSection
from app.modules.runtime_bridge.bootstrap import build_read_registry, build_read_skills
from app.modules.runtime_bridge.qa_tool import LegacyResultLedger
from app.modules.science.tools import SCIENCE_TOOL_SPECS, science_registrations
from app.modules.security.content_access import SourceAccessAuthority
from app.modules.tools.bootstrap import build_registry
from app.modules.vision.classification import ChatAdapter
from app.modules.vision.skill import PAPER_FIGURES_SKILL, SCIENCE_TOOL_NAMES, VISION_TOOL_NAMES
from app.modules.vision.tools import (
    LoadedPaper, StoredPaperSource, VISION_TOOL_SPECS, VisionToolset, build_specs, figure_scope_provider,
)

DOI = "10.1000/test"


def ctx():
    return RequestContext(
        profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
        principal=Principal(subject="local-operator"), request_id="req-vis",
        capabilities=frozenset({"chat.ask"}),
    )


class Source:
    def __init__(self, document, pdf):
        self.paper = LoadedPaper(document, pdf, DOI)
        self.calls = 0

    def load(self, c, doi):
        self.calls += 1
        if doi != DOI:
            raise ContractError(ErrorCode.NOT_FOUND, "no stored PDF", retryable=False)
        return self.paper


def toolset(document, pdf, *, scope=True, **kw):
    authority = SourceAccessAuthority()
    provider = figure_scope_provider(authority) if scope else None
    return VisionToolset(Source(document, pdf), scope_provider=provider, **kw)


def executor(ts):
    return ToolExecutor(build_registry(None, include=(), extra=ts.registrations()))


def good():
    return toolset(parsed(CAPTION, bbox=bl_bbox(FIG)), multi_panel_pdf(CAPTION))


def test_specs_effects_and_names():
    assert tuple(s.name for s in VISION_TOOL_SPECS) == VISION_TOOL_NAMES
    for spec in VISION_TOOL_SPECS:
        assert Effect.READ_PRIVATE_SOURCE in spec.effects and Effect.MODEL_INFERENCE not in spec.effects
        assert spec.required_capabilities == {"chat.ask"}
    with_model = {s.name: s for s in build_specs(model=True)}
    assert all(Effect.MODEL_INFERENCE in s.effects for n, s in with_model.items() if n != "vision_extract_tables")
    assert Effect.MODEL_INFERENCE not in with_model["vision_extract_tables"].effects
    assert tuple(s.name for s in SCIENCE_TOOL_SPECS) == SCIENCE_TOOL_NAMES


def test_extract_figures_result_locates_figure_and_panels_without_image_material():
    result = executor(good()).execute(ctx(), "vision_extract_figures", {"doi": DOI})
    assert result.status in (ToolStatus.OK, ToolStatus.PARTIAL)
    (fig,) = result.data["figures"]
    assert fig["locator"]["page"] == 1 and fig["locator"]["figure"] == fig["figure_id"]
    assert fig["locator"]["bbox_units"] == "pt" and fig["locator"]["bbox_origin"] == "top-left"
    assert [p["locator"]["panel"] for p in fig["panels"]] == ["a", "b"]
    assert all(p["locator"]["bbox"] for p in fig["panels"])
    assert result.evidence and all(e.locator.figure == fig["figure_id"] for e in result.evidence)
    blob = json.dumps(result.data)
    for needle in ("data:image", "base64", "http", ".png", "image_url", "crop", "/papers/"):
        assert needle not in blob


def test_each_reading_tool_returns_panel_locators():
    ex = executor(good())
    for tool, key in (("vision_read_scale", "scale"), ("vision_read_qrange", "q_range")):
        result = ex.execute(ctx(), tool, {"doi": DOI, "panel": "a"})
        (fig,) = result.data["figures"]
        (pan,) = fig["panels"]
        assert pan["label"] == "a" and pan["locator"]["panel"] == "a" and pan[key]["reported"] is True
    cls = ex.execute(ctx(), "vision_classify_figure", {"doi": DOI})
    (fig,) = cls.data["figures"]
    assert fig["classification"]["class"] == "experimental_scattering" and len(fig["classification_units"]) == 2


def test_missing_text_layer_is_unavailable_retryable_for_figure_required_and_partial_when_waived(monkeypatch):
    monkeypatch.setenv("F2W_FIGURE_OCR_ENGINE", "none")   # no engine, whatever weights the environment has
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    ts = toolset(parsed(cap), raster_figure_pdf(cap))
    ex = executor(ts)
    refused = ex.execute(ctx(), "vision_read_qrange", {"doi": DOI})
    assert refused.status is ToolStatus.UNAVAILABLE and refused.retryable is True
    assert refused.error_code is ErrorCode.UNAVAILABLE and refused.data["capability_errors"]
    assert refused.data["figures"][0]["locator"]["page"] == 1     # still located
    waived = ex.execute(ctx(), "vision_read_qrange", {"doi": DOI, "figure_required": False})
    assert waived.status is ToolStatus.PARTIAL
    ocr = ex.execute(ctx(), "vision_read_qrange", {"doi": DOI, "use_ocr": True})     # no engine installed here
    assert ocr.status is ToolStatus.UNAVAILABLE and ocr.retryable is True


def test_access_denied_without_scope_or_authority_and_unknown_inputs():
    denied = executor(toolset(parsed(CAPTION, bbox=bl_bbox(FIG)), multi_panel_pdf(CAPTION), scope=False)).execute(
        ctx(), "vision_extract_figures", {"doi": DOI})
    assert denied.status is ToolStatus.DENIED
    ex = executor(good())
    assert ex.execute(ctx(), "vision_extract_figures", {"doi": DOI, "bogus": 1}).status is ToolStatus.INVALID
    assert ex.execute(ctx(), "vision_extract_figures", {"doi": DOI, "figure_id": "nope"}).error_code \
        is ErrorCode.NOT_FOUND
    assert ex.execute(ctx(), "vision_extract_figures", {"doi": "10.9/none"}).error_code is ErrorCode.NOT_FOUND


def test_expired_scope_is_denied():
    ts = good()
    inner = figure_scope_provider(SourceAccessAuthority(), ttl=timedelta(microseconds=1))

    def slow(c):
        scope = inner(c)
        time.sleep(0.01)
        return scope

    ts.scope_provider = slow
    assert executor(ts).execute(ctx(), "vision_extract_figures", {"doi": DOI}).status is ToolStatus.DENIED


def test_orientation_tool_separates_source_statements_from_inference():
    sections = (ParsedSection("Results", "As shown in Figure 1, the lamellae are edge-on with respect to the "
                                         "substrate. Annealing was done in air.", page=2),)
    cap = "Figure 1. (a) 2D GIWAXS pattern of the film. (b) Line cut."

    class Chat:
        def chat(self, prompt, *, temperature=0.0, timeout=240, trace_label=None):
            return "OUT_OF_PLANE"

    doc = parsed(cap, bbox=bl_bbox(FIG), sections=sections)
    ts = toolset(doc, multi_panel_pdf(cap), model=ChatAdapter(Chat()))
    assert Effect.MODEL_INFERENCE in ts.specs[4].effects
    result = executor(ts).execute(ctx(), "vision_read_orientation", {"doi": DOI})
    (fig,) = result.data["figures"]
    assert fig["reported"] == "edge-on"
    (stmt,) = fig["source_statements"]
    assert stmt["origin"] == "section" and stmt["locator"]["section"] == "Results" and stmt["locator"]["page"] == 2
    (cand,) = fig["inference_candidates"]
    assert cand["orientation"] == "parallel" and cand["reportable"] is False
    assert cand["kind"] == "inference_candidate"
    none = toolset(parsed(cap, bbox=bl_bbox(FIG)), multi_panel_pdf(cap))
    res = executor(none).execute(ctx(), "vision_read_orientation", {"doi": DOI})
    assert res.status is ToolStatus.PARTIAL and res.data["figures"][0]["reported"] is None
    assert res.data["figures"][0]["withheld_reason"] == "no_source_statement"


def test_stored_paper_source_requires_a_stored_verified_pdf():
    class Index:
        def find_by_doi(self, doi):
            return "sha256:" + "a" * 64 if doi == DOI else None

    class Store:
        def verify(self, c, ref):
            return True

        def get(self, c, ref):
            return b"%PDF"

    class Parse:
        def parse(self, c, ref):
            return "record"

        def load(self, c, record):
            return parsed("")

    src = StoredPaperSource(Store(), Index(), Parse())
    assert src.load(ctx(), DOI).work_id == DOI
    with pytest.raises(ContractError) as err:
        src.load(ctx(), "10.9/missing")
    assert err.value.code is ErrorCode.NOT_FOUND


def test_science_tools_through_executor():
    ex = ToolExecutor(build_registry(None, include=(), extra=science_registrations()))
    ok = ex.execute(ctx(), "science_q_to_spacing", {"q": 0.1, "unit": "A^-1"})
    assert ok.status is ToolStatus.OK and ok.data["d_nm"] == pytest.approx(6.283185, rel=1e-5)
    bad = ex.execute(ctx(), "science_normalize_quantity", {"value": 3, "unit": "furlong"})
    assert bad.status is ToolStatus.INVALID and bad.data["reason"] == "unknown_unit"


def test_bootstrap_registration_is_opt_in_and_skill_covers_tools():
    ledger = LegacyResultLedger()
    plain = build_read_registry(object(), ledger)
    assert not any(n in plain for n in VISION_TOOL_NAMES + SCIENCE_TOOL_NAMES)
    reg = build_read_registry(object(), ledger, vision=good())
    assert all(n in reg for n in VISION_TOOL_NAMES + SCIENCE_TOOL_NAMES)
    skills = build_read_skills(reg)
    assert PAPER_FIGURES_SKILL.name in [sk.name for sk in skills.list_skills()]
