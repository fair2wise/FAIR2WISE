"""T05 slice 5: ParsedDocument geometry, pluggable versioned parsing, reader and the six lit_* tools."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import httpx
import pytest

from app.modules.agent_runtime.executor import ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts.errors import ErrorCode
from app.modules.contracts.tools import ToolStatus
from app.modules.literature.legacy import LegacyIndex
from app.modules.literature.openalex import OpenAlexClient, OpenAlexSettings, QuotaBreaker, ResponseCache
from app.modules.literature.parse_service import ParseFailed, ParseService
from app.modules.literature.parsed_document import (
    BoundingBox,
    ParsedDocument,
    ParsedFigure,
    ParsedSection,
    document_from_dict,
    extract_sections,
    figure_geometry_props,
    without_embedded_images,
)
from app.modules.literature.parsers import DoclingParser, PublicationParser
from app.modules.literature.reading import PaperReader
from app.modules.literature.store import FileDocumentStore, ref_for_bytes
from app.modules.literature.tools import LIT_TOOL_SPECS, LiteratureToolset, authority_scope_provider
from app.modules.runtime_bridge.bootstrap import build_read_registry, build_read_skills
from app.modules.runtime_bridge.qa_tool import LegacyResultLedger
from app.modules.security.content_access import SourceAccessAuthority
from app.modules.tools.bootstrap import build_registry
from tests.literature.lit_fakes import (
    Router,
    als_user_ctx,
    
    local_ctx,
    make_typeset_paper_pdf,
    minimal_pdf_bytes,
    openalex_work,
)

needs_docling = pytest.mark.skipif(
    not os.environ.get("F2W_DOCLING_ARTIFACTS_PATH"), reason="Docling model weights not available"
)
DOI = "10.1021/acs.macromol.0c01782"


class FakeParser:
    """A deterministic parser (no PDF library) that writes one figure and one table file."""

    def __init__(self, name: str = "fake", version: str = "1.0", fail: Any = None, sections: int = 2) -> None:
        self.name, self._version, self.fail, self.calls, self.n = name, version, fail, 0, sections

    def version(self) -> str:
        return self._version

    def config(self) -> Dict[str, Any]:
        return {"sections": self.n}

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str) -> ParsedDocument:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        fig = work_dir / "fig.png"
        fig.write_bytes(b"\x89PNG fake " + source_ref.encode())
        csv_path = work_dir / "t.csv"
        csv_path.write_text("q,I\n0.1,10\n")
        from app.modules.literature.parsed_document import ParsedTable

        sections = tuple(
            ParsedSection(name=f"{i}. Section", text=f"text {i} " * 50, page=i + 1, level=1) for i in range(self.n)
        )
        return ParsedDocument(
            source_ref=source_ref,
            markdown="# x",
            num_pages=3,
            parser=self.name,
            parser_version=self._version,
            parser_config=self.config(),
            sections=sections,
            figures=(
                ParsedFigure(
                    caption="Figure 1.",
                    page=1,
                    index=0,
                    figure_id="fig-p001-00",
                    bbox=BoundingBox(1, 2, 3, 4, "TOPLEFT"),
                    path=fig,
                ),
            ),
            tables=(ParsedTable(page=2, n_rows=2, n_cols=2, table_id="tab-p002-00", data_path=csv_path),),
        )


@pytest.fixture
def store(tmp_path):
    return FileDocumentStore(tmp_path / "candidate-store")


def put_pdf(store, payload: bytes = b"") -> str:
    return store.put(
        local_ctx(),
        payload or minimal_pdf_bytes("paper"),
        media_type="application/pdf",
        metadata={"kind": "source_pdf"},
    )


def test_parser_protocol_and_document_roundtrip():
    assert isinstance(DoclingParser(), PublicationParser) and isinstance(FakeParser(), PublicationParser)
    doc = ParsedDocument(
        source_ref="sha256:" + "a" * 64,
        markdown="m",
        num_pages=1,
        parser="p",
        figures=(
            ParsedFigure(
                caption="c",
                page=1,
                index=0,
                bbox=BoundingBox(1, 2, 3, 4, "TOPLEFT"),
                images_scale=2.0,
                asset_ref="sha256:" + "b" * 64,
                figure_id="fig-p001-00",
            ),
        ),
        sections=(ParsedSection("Intro", "t", page=1, bbox=BoundingBox(0, 0, 1, 1)),),
    )
    again = document_from_dict(json.loads(json.dumps(doc.to_record())))
    assert again.figures[0].bbox == doc.figures[0].bbox and again.figures[0].images_scale == 2.0
    assert again.sections[0].bbox.coord_origin == "BOTTOMLEFT" and again.parser_key == doc.parser_key
    legacy = doc.as_legacy_dict()
    assert legacy["figures"][0]["path"] == "sha256:" + "b" * 64 and legacy["num_images"] == 1
    assert figure_geometry_props(legacy["figures"][0])["images_scale"] == 2.0
    with pytest.raises(ValueError):
        document_from_dict({**doc.to_record(), "schema_version": 99})
    assert without_embedded_images("x data:image/png;base64,AAAA y") == "x image-artifact y"
    assert list(extract_sections("pre\n# A\na\n```\n# not\n```\n# A\nb")) == ["preamble", "a", "a [2]"]


def test_parse_is_versioned_cached_and_separate_from_extraction(store):
    ref = put_pdf(store)
    parser = FakeParser()
    service = ParseService(store, [parser])
    record = service.parse(local_ctx(), ref)
    assert record.stage == "parsed" and not record.cache_hit and parser.calls == 1
    assert "extraction" not in json.dumps(record.as_dict())
    doc = service.load(local_ctx(), record)
    assert doc.figures[0].asset_ref and doc.figures[0].path is None and doc.tables[0].data_ref
    assert store.describe(local_ctx(), doc.figures[0].asset_ref).kind == "figure_image"
    assert store.get(local_ctx(), doc.tables[0].data_ref) == b"q,I\n0.1,10\n"
    again = service.parse(local_ctx(), ref)
    assert again.cache_hit and parser.calls == 1
    v2 = ParseService(store, [FakeParser(version="2.0")]).parse(local_ctx(), ref)
    assert v2.parser_key != record.parser_key
    assert ParseService(store, [parser]).status(local_ctx(), ref) == {
        record.parser_key: "parsed",
        v2.parser_key: "parsed",
    }
    assert str(store.root) not in json.dumps(record.as_dict())


def test_interrupted_parse_is_detected_and_resumed(store):
    ref = put_pdf(store)
    parser = FakeParser()
    service = ParseService(store, [parser])
    record_path, state_path = service._paths(
        ref,
        parser.name
        + "-1.0-"
        + __import__("app.modules.literature.parsed_document", fromlist=["config_hash"]).config_hash(
            parser.config()
        ),
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"stage": "parsing", "pid": 999999}))  # a crash mid-parse
    assert service.status(local_ctx(), ref) == {record_path.stem: "interrupted"}
    record = service.parse(local_ctx(), ref)
    assert record.recovered_from_interruption and not state_path.exists() and record_path.exists()


def test_parser_failure_fallback_and_typed_errors(store):
    ref = put_pdf(store)
    broken, good = FakeParser("broken", fail=RuntimeError("bad xref")), FakeParser("good")
    record = ParseService(store, [broken, good]).parse(local_ctx(), ref)
    assert record.parser == "good" and record.failures == ["broken: RuntimeError"]
    status = ParseService(store, [broken]).status(local_ctx(), ref)
    assert any(v == "failed:RuntimeError" for v in status.values())
    with pytest.raises(ParseFailed) as err:
        ParseService(store, [FakeParser("missing", fail=ImportError("no lib"))]).parse(local_ctx(), ref)
    assert err.value.code is ErrorCode.UNAVAILABLE
    with pytest.raises(ParseFailed) as err:
        ParseService(store, [broken]).parse(local_ctx(), ref, force=True)
    assert err.value.code is ErrorCode.INTERNAL
    html = store.put(local_ctx(), b"<html>landing</html>", media_type="text/html", metadata={})
    with pytest.raises(ParseFailed) as err:
        ParseService(store, [good]).parse(local_ctx(), html)
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_corrupt_asset_triggers_reparse_and_cache_restore(store):
    ref = put_pdf(store)
    parser = FakeParser()
    service = ParseService(store, [parser])
    record = service.parse(local_ctx(), ref)
    fig_ref = service.load(local_ctx(), record).figures[0].asset_ref
    blob = store.root / "objects" / fig_ref[7:9] / fig_ref[7:]
    os.chmod(blob, 0o644)
    blob.write_bytes(b"corrupted")
    assert not store.verify(local_ctx(), fig_ref)
    restored = service.parse(local_ctx(), ref)
    assert not restored.cache_hit and parser.calls == 2
    assert store.verify(local_ctx(), fig_ref)  # same bytes re-stored, same ref: references survive
    assert restored.asset_refs == record.asset_refs


def test_corrupt_source_is_refused(store):
    ref = put_pdf(store)
    blob = store.root / "objects" / ref[7:9] / ref[7:]
    os.chmod(blob, 0o644)
    blob.write_bytes(minimal_pdf_bytes("tampered"))
    from app.modules.literature.store import StoreIntegrityError

    with pytest.raises(StoreIntegrityError):
        ParseService(store, [FakeParser()]).parse(local_ctx(), ref)


@needs_docling
@pytest.mark.parametrize("page_size", [(612, 792), (595, 842)], ids=["letter", "a4"])
def test_docling_parser_preserves_sections_figures_tables_geometry(store, tmp_path, page_size):
    # A typeset two-column fixture (embedded serif font, captions in the layout a journal uses): the real layout
    # model labels its captions "caption", which is what links them to the figure and table. The lone Helvetica
    # lines of make_paper_pdf, or captions in non-embedded standard fonts, come back as plain text.
    paper = make_typeset_paper_pdf(tmp_path / "p.pdf", doi=DOI, page_size=page_size)
    path = paper.path
    ref = store.put_file(local_ctx(), path, media_type="application/pdf", metadata={"kind": "source_pdf"}).ref
    service = ParseService(store, [DoclingParser()])
    doc = service.load(local_ctx(), service.parse(local_ctx(), ref))
    assert (doc.pages[0].width, doc.pages[0].height) == page_size
    names = doc.section_names()
    assert "1. Introduction" in names and "2. Results" in names
    intro = doc.sections[names.index("1. Introduction")]
    assert intro.page == 1 and intro.bbox.coord_origin == "BOTTOMLEFT" and "Body line two." in intro.text
    assert any(DOI in s.text for s in doc.sections) and "Resonant soft X-ray" in " ".join((*names, doc.markdown))
    fig = doc.figures[0]
    # Docling boxes are BOTTOMLEFT; in top-left points the figure sits where the fixture drew it.
    top_left = (fig.bbox.left, page_size[1] - fig.bbox.top, fig.bbox.right, page_size[1] - fig.bbox.bottom)
    assert fig.bbox.coord_origin == "BOTTOMLEFT" and top_left == pytest.approx(paper.figure_box, abs=2.0)
    assert fig.caption.startswith("Figure 1.")
    assert fig.images_scale == pytest.approx(2.0, abs=0.05)
    assert store.get(local_ctx(), fig.asset_ref).startswith(b"\x89PNG")
    table = doc.tables[0]
    assert table.caption.startswith("Table 1.")
    assert (table.n_rows, table.n_cols) == (3, 3)
    rows = list(csv.reader(io.StringIO(store.get(local_ctx(), table.data_ref).decode())))
    assert rows[0] == ["q", "I", "dI"]
    assert len(table.cells) == 9 and all(c.bbox is not None for c in table.cells)
    assert "q I dI" not in intro.text  # table cells are not section prose
    assert doc.parser == "docling" and doc.parser_version and doc.parser_key.startswith("docling-")


# -- reader and tools ----------------------------------------------------------------------------


def _toolset(store, router, *, parser=None, scope=True, authority=None):
    index = LegacyIndex(store)
    client = OpenAlexClient(
        router.client(),
        cache=ResponseCache(":memory:"),
        breaker=QuotaBreaker(),
        settings=OpenAlexSettings(),
        sleep=lambda s: None,
    )
    reader = PaperReader(store, index, ParseService(store, [parser or FakeParser()]))
    authority = authority or SourceAccessAuthority()
    toolset = LiteratureToolset(
        client, reader, scope_provider=authority_scope_provider(authority) if scope else None
    )
    return toolset, index


def inv(ctx, **args):
    return SimpleNamespace(ctx=ctx, args=args)


def test_read_tools_use_store_and_require_trusted_scope(store, tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "10.1021_acs.macromol.0c01782.pdf").write_bytes(minimal_pdf_bytes("old cache"))
    toolset, index = _toolset(store, Router())
    index.adopt_tree(local_ctx(), "papers", papers)
    overview = toolset.read_paper(inv(als_user_ctx(), doi=f"https://doi.org/{DOI}"))
    assert overview.status is ToolStatus.OK
    assert (
        overview.data["section_names"] == ["0. Section", "1. Section"]
        and overview.data["source_status"] == "cached"
    )
    assert overview.data["source_version"] == ref_for_bytes(minimal_pdf_bytes("old cache"))[7:]
    section = toolset.read_paper_section(inv(als_user_ctx(), doi=DOI, section="1. SECTION"))
    assert section.status is ToolStatus.OK and section.data["page"] == 2 and not section.data["truncated"]
    missing = toolset.read_paper_section(inv(als_user_ctx(), doi=DOI, section="methods"))
    assert missing.status is ToolStatus.ERROR and missing.error_code is ErrorCode.NOT_FOUND
    assert missing.data["available_sections"] == ["0. Section", "1. Section"]
    blob = json.dumps([overview.data, section.data])
    assert str(store.root) not in blob and str(papers) not in blob
    denied, _ = _toolset(store, Router(), scope=False)
    result = denied.read_paper_section(inv(als_user_ctx(), doi=DOI, section="0"))
    assert result.status is ToolStatus.DENIED and result.warnings == ("source access denied",)


def test_read_paper_not_stored_without_acquirer_is_not_found(store):
    toolset, _ = _toolset(store, Router())
    result = toolset.read_paper(inv(local_ctx(), doi="10.9999/never"))
    assert result.status is ToolStatus.ERROR and result.error_code is ErrorCode.NOT_FOUND
    bad = toolset.read_paper(inv(local_ctx(), doi="nonsense"))
    assert bad.status is ToolStatus.INVALID


def test_search_tools_distinguish_empty_unavailable_and_quota(store):
    def works(request: httpx.Request) -> httpx.Response:
        search = request.url.params.get("search", "")
        if search == "nothing":
            return httpx.Response(200, json={"results": []})
        if search == "down":
            return httpx.Response(503)
        if search == "spent":
            return httpx.Response(429, headers={"Retry-After": "40000"})
        return httpx.Response(200, json={"results": [openalex_work("10.1/a", "A paper")]})

    router = Router().add("GET", "api.openalex.org", "/works", works)
    toolset, _ = _toolset(store, router)
    ok = toolset.search_papers(inv(local_ctx(), query="films", max_results=50))
    assert ok.status is ToolStatus.OK and ok.data["papers"][0]["record_kind"] == "external_search_result"
    assert ok.data["record_kind"] == "external_search_result"
    assert toolset.search_papers(inv(local_ctx(), query="nothing")).status is ToolStatus.EMPTY
    down = toolset.search_papers(inv(local_ctx(), query="down"))
    assert down.status is ToolStatus.UNAVAILABLE and down.retryable
    spent = toolset.search_papers(inv(local_ctx(), query="spent"))
    assert spent.status is ToolStatus.UNAVAILABLE and "quota paused" in spent.warnings[0]
    assert list(store.list_refs(local_ctx())) == []  # discovery never downloads


def test_doi_lookup_and_citations_tools(store):
    def by_doi(request):
        if request.url.path.endswith("10.1/ok"):
            return httpx.Response(200, json=openalex_work("10.1/ok", "Found"))
        if request.url.path.endswith("10.1/down"):
            return httpx.Response(500)
        return httpx.Response(404)

    router = Router().add("GET", "api.openalex.org", "/works/doi:", by_doi)
    router.add("GET", "api.openalex.org", "/works", (200, {"results": [openalex_work("10.1/c", "Citer")]}))
    router.add("POST", "api.semanticscholar.org", "/graph/v1/paper/", (503, {}))
    toolset, _ = _toolset(store, router)
    result = toolset.get_papers_by_doi(inv(local_ctx(), dois="10.1/ok, 10.1/gone, 10.1/down"))
    assert result.status is ToolStatus.PARTIAL and result.data["not_found"] == ["10.1/gone"]
    assert result.data["unavailable"] == ["10.1/down"] and result.data["count"] == 1
    assert toolset.find_citing_papers(inv(local_ctx(), openalex_id="W2741809807")).status is ToolStatus.OK
    assert toolset.find_related_papers(inv(local_ctx(), paper_id="10.1/c")).status is ToolStatus.UNAVAILABLE


def test_lit_tools_register_through_explicit_bootstrap_and_executor(store, tmp_path):
    toolset, index = _toolset(store, Router())
    registry = build_read_registry(object(), LegacyResultLedger(), literature=toolset)
    for spec in LIT_TOOL_SPECS:
        assert spec.name in registry
    skills = build_read_skills(registry)
    assert skills.get("research_literature").tools == tuple(spec.name for spec in LIT_TOOL_SPECS)
    default = build_read_registry(object(), LegacyResultLedger())
    assert "lit_search_papers" not in default  # unchanged unless the caller passes the toolset
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "10.1021_acs.macromol.0c01782.pdf").write_bytes(minimal_pdf_bytes("exec"))
    index.adopt_tree(local_ctx(), "papers", papers)
    sink = InMemoryEventSink()
    executor = ToolExecutor(build_registry(None, include=(), extra=toolset.registrations()), event_sink=sink)
    result = executor.execute(als_user_ctx(), "lit_read_paper_section", {"doi": DOI, "section": "0. section"})
    assert result.status is ToolStatus.OK and result.data["section"] == "0. Section"
    assert any(e.tool == "lit_read_paper_section" for e in sink.events)  # staged writes are audited
    invalid = executor.execute(als_user_ctx(), "lit_search_papers", {"query": "x", "unexpected": 1})
    assert invalid.status is ToolStatus.INVALID
    no_audit = ToolExecutor(build_registry(None, include=(), extra=toolset.registrations()))
    refused = no_audit.execute(als_user_ctx(), "lit_read_paper", {"doi": DOI})
    assert refused.status is ToolStatus.UNAVAILABLE  # no durable audit sink: the runtime refuses staged writes
