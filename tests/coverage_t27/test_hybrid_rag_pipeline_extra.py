"""Hybrid source RAG: chunking windows, source discovery edge cases and incremental index builds."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.hybrid_rag import chunker, pipeline, sources
from app.modules.hybrid_rag.index_store import SourceIndex
from app.modules.literature import pdf_reader


# ------------------------------------------------------------------ chunker
def test_windows_split_with_overlap_and_normalise_whitespace():
    assert chunker._windows("") == [] and chunker._windows("  \n ") == []
    assert chunker._windows("a  b\n c") == ["a b c"]
    text = "x" * 25
    parts = chunker._windows(text, size=10, overlap=4)
    assert [len(x) for x in parts] == [10, 10, 10, 7, 1]  # stride 6; the tail window is fully overlapped
    assert chunker._windows("abcdef", size=3, overlap=3)[:2] == ["abc", "bcd"]  # overlap >= size still advances


def test_strip_html_drops_tags_entities_and_blank_runs():
    out = chunker._strip_html("<p>A&amp;B</p>  \n\n\n\n<b>C</b>")
    assert "A&B" in out and "<" not in out and "\n\n\n" not in out
    assert chunker._strip_html(None) == ""


class _FakePage:
    def __init__(self, text, boom=False):
        self.text, self.boom = text, boom

    def get_text(self, kind="text"):
        if self.boom:
            raise RuntimeError("bad page")
        return self.text


class _FakeDoc:
    def __init__(self, pages):
        self.pages, self.closed = pages, False

    def __iter__(self):
        return iter(self.pages)

    def close(self):
        self.closed = True


def test_chunk_pdf_is_page_aware_and_always_closes_document(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    doc = _FakeDoc([_FakePage("hello world"), _FakePage("", boom=True), _FakePage("y" * 1500)])
    monkeypatch.setattr(pdf_reader, "open_pdf", lambda p: doc)
    chunks = chunker.chunk_pdf(pdf, work_id="doi:1", doc_id="a.pdf", extra={"title": "T", "sha256": "s"})
    assert [c["locator"] for c in chunks] == ["p.1", "p.3", "p.3"]
    assert chunks[0]["chunk_id"] == "literature:a.pdf:p1:body:c0" and chunks[0]["title"] == "T"
    assert chunks[2]["chunk_id"].endswith(":p3:body:c1") and chunks[0]["work_id"] == "doi:1"
    assert doc.closed is True


def test_chunk_pdf_failures_yield_empty(tmp_path, monkeypatch):
    assert chunker.chunk_pdf(tmp_path / "missing.pdf", work_id="w", doc_id="d") == []
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    def bad_open(p):
        raise OSError("corrupt")

    monkeypatch.setattr(pdf_reader, "open_pdf", bad_open)
    assert chunker.chunk_pdf(pdf, work_id="w", doc_id="d") == []


def test_chunk_text_document_markdown_headings_line_locators_and_url(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("Preamble text\n\n# Setup\nline one\nline two\n## Detail\nmore\n", encoding="utf-8")
    chunks = chunker.chunk_text_document(md, doc_id="d", url="https://x/doc", extra={"entity_ids": ["e1"]})
    assert [c["heading"] for c in chunks] == ["", "Setup", "Detail"]
    assert all(c["url"] == "https://x/doc" and c["entity_ids"] == ["e1"] and c["page"] is None for c in chunks)
    assert chunks[1]["locator"].endswith(":2-3 §Setup")
    assert chunks[0]["chunk_id"] == "bl1101:d:doc:body:c0"


def test_chunk_text_document_html_and_plain_text_and_unreadable(tmp_path):
    html = tmp_path / "p.html"
    html.write_text("<html><body><script>var x=1</script><h1>Energy</h1><p>165 to 1500 eV</p>"
                    "<h2>Flux</h2><p>high</p></body></html>", encoding="utf-8")
    chunks = chunker.chunk_text_document(html, doc_id="h")
    assert [(c["heading"], c["text"]) for c in chunks] == [("Energy", "165 to 1500 eV"), ("Flux", "high")]
    txt = tmp_path / "n.txt"
    txt.write_text("just text", encoding="utf-8")
    assert chunker.chunk_text_document(txt, doc_id="t")[0]["text"] == "just text"
    assert chunker.chunk_text_document(tmp_path / "gone.md", doc_id="g") == []
    heading_only = tmp_path / "h.md"
    heading_only.write_text("# Only heading\n", encoding="utf-8")
    assert chunker.chunk_text_document(heading_only, doc_id="x")[0]["text"] == "Only heading"


def test_html_sections_fall_back_to_tag_stripping_without_lxml_or_on_parse_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_lxml(name, *a, **k):
        if name == "lxml":
            raise ImportError
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_lxml)
    assert chunker._html_sections("<p>plain</p>") == [("", "plain")]
    monkeypatch.undo()
    assert chunker._html_sections("") == [("", "")] or chunker._html_sections("") == []


def test_markdown_sections_without_headings_and_iter_nonempty():
    assert chunker._markdown_sections("no headings") == [("", "no headings")]
    assert chunker._markdown_sections("") == [("", "")]
    assert chunker.iter_nonempty([{"text": "a"}, {"text": "  "}, {}]) == [{"text": "a"}]


# ------------------------------------------------------------------ sources
def test_sha256_file_and_repo_root_respects_resource_root(tmp_path, monkeypatch):
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    assert sources.sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    monkeypatch.setenv("F2W_RESOURCE_ROOT", str(tmp_path))
    assert sources.repo_root() == tmp_path.resolve()


def test_work_id_prefers_doi_then_version_doi_then_arxiv_then_title():
    wid = sources.work_id_for_paper
    assert wid({"doi": " 10.1/ABC "}) == "doi:10.1/abc"
    assert wid({"versions": ["junk", {"doi": ""}, {"doi": "10.2/X"}]}) == "doi:10.2/x"
    assert wid({"arxiv_id": "2101.0001v3"}) == "arxiv:2101.0001"
    assert wid({"title": "A Study: of P3HT!"}) == "title:a-study-of-p3ht"
    assert wid({}) == "title:unknown"


def test_resolve_pdf_path_relative_absolute_and_missing(tmp_path):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"x")
    assert sources.resolve_pdf_path({"pdf_path": "p.pdf"}, tmp_path) == pdf
    assert sources.resolve_pdf_path({"pdf_path": str(pdf)}, Path("/nowhere")) == pdf
    assert sources.resolve_pdf_path({"pdf_path": "nope.pdf"}, tmp_path) is None
    assert sources.resolve_pdf_path({}, tmp_path) is None


def test_load_harvest_papers_shapes_and_errors(tmp_path):
    p = tmp_path / "m.json"
    assert sources.load_harvest_papers(p) == []
    p.write_text("{not json")
    assert sources.load_harvest_papers(p) == []
    p.write_text(json.dumps({"papers": {"a": {"title": "A"}, "b": "junk"}}))
    assert sources.load_harvest_papers(p) == [{"title": "A"}]
    p.write_text(json.dumps([{"title": "B"}, 3]))
    assert sources.load_harvest_papers(p) == [{"title": "B"}]
    p.write_text(json.dumps({"papers": 5}))
    assert sources.load_harvest_papers(p) == []


def _manifest(tmp_path, papers):
    d = tmp_path / "papers/rsoxs"
    d.mkdir(parents=True, exist_ok=True)
    for paper in papers:
        if paper.get("pdf_path") and "missing" not in paper["pdf_path"]:
            (tmp_path / paper["pdf_path"]).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / paper["pdf_path"]).write_bytes(b"%PDF-1")
    (d / "manifest.json").write_text(json.dumps({"papers": papers}))


def test_select_literature_docs_gold_ranking_dedup_and_all_mode(tmp_path):
    _manifest(tmp_path, [
        {"title": "Unrelated", "doi": "10.1/u", "pdf_path": "p/u.pdf", "kg_seed_rank": "bad"},
        {"title": "P3HT blends", "doi": "10.1/p", "pdf_path": "p/p.pdf"},
        {"title": "Primary other", "doi": "10.1/o", "pdf_path": "p/o.pdf", "kg_primary": True, "kg_seed_rank": 1},
        {"title": "Duplicate P3HT", "doi": "10.1/p", "pdf_path": "p/dup.pdf"},
        {"title": "No file", "doi": "10.1/n", "pdf_path": "p/missing.pdf"},
    ])
    gold = sources.select_literature_docs(root=tmp_path, mode="gold", limit=2)
    assert [d["doc_id"] for d in gold] == ["p.pdf", "o.pdf"]  # needle hit first, then primary
    everything = sources.select_literature_docs(root=tmp_path, mode="all", limit=0)
    assert [d["title"] for d in everything] == ["P3HT blends", "Primary other", "Unrelated"]
    assert sources.select_literature_docs(root=tmp_path, mode="ALL", limit=1)[0]["title"] == "P3HT blends"


def test_collect_ops_docs_gathers_pages_readmes_and_repo_markdown_but_not_secrets(tmp_path, monkeypatch):
    cache = tmp_path / ".cache/bl1101"
    (cache / "pages").mkdir(parents=True)
    (cache / "pages/als_11-0-1-2.html").write_text("<h1>x</h1>")
    bp = tmp_path / "papers/rsoxs/beamline_blueprint"
    bp.mkdir(parents=True)
    (bp / "blueprint_bl11012_version09092026.html").write_text("<h1>bp</h1>")
    repo = cache / "repos/myrepo"
    (repo / "info").mkdir(parents=True)
    (repo / "README.md").write_text("# r")
    (repo / "info/notes.md").write_text("n")
    (repo / "info/.env").write_text("SECRET=1")
    (cache / "readmes/other").mkdir(parents=True)
    (cache / "readmes/other/README.md").write_text("# other")
    (cache / "readmes/other/CHANGES.md").write_text("ignored")
    specs = (
        SimpleNamespace(name="myrepo", url="https://git/myrepo", md_globs=("README.md", "info/*"), readme_only=False),
        SimpleNamespace(name="ronly", url="u", md_globs=(), readme_only=True),
        SimpleNamespace(name="absent", url="u", md_globs=(), readme_only=False),
    )
    import app.modules.bl1101_ingest as ingest

    monkeypatch.setattr(ingest, "REPO_SPECS", specs)
    ids = sorted(d["doc_id"] for d in sources.collect_ops_docs(root=tmp_path))
    assert ids == ["als_11-0-1-2", "blueprint_bl11012_version09092026", "myrepo:README.md",
                   "myrepo:info/notes.md", "readme:other/README.md"]
    docs = {d["doc_id"]: d for d in sources.collect_ops_docs(root=tmp_path)}
    assert docs["myrepo:README.md"]["url"] == "https://git/myrepo/blob/HEAD/README.md"
    assert docs["blueprint_bl11012_version09092026"]["access"] == "gated"


# ------------------------------------------------------------------ pipeline
def test_index_roots_prefer_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_RAG_LITERATURE_INDEX", str(tmp_path / "lit"))
    monkeypatch.setenv("HYBRID_RAG_OPS_INDEX", str(tmp_path / "ops"))
    assert pipeline.literature_index_root(tmp_path / "root") == tmp_path / "lit"
    assert pipeline.ops_index_root() == tmp_path / "ops"
    monkeypatch.delenv("HYBRID_RAG_LITERATURE_INDEX")
    monkeypatch.delenv("HYBRID_RAG_OPS_INDEX")
    assert pipeline.literature_index_root(tmp_path / "root") == tmp_path / "root" / pipeline.DEFAULT_LIT_INDEX
    assert pipeline.ops_index_root(tmp_path / "root") == tmp_path / "root" / pipeline.DEFAULT_OPS_INDEX


def test_enabled_flag_precedence(monkeypatch):
    monkeypatch.delenv("F2W_SOURCE_RAG", raising=False)
    monkeypatch.delenv("HYBRID_RAG_ENABLED", raising=False)
    assert pipeline.hybrid_rag_enabled() is False
    monkeypatch.setenv("HYBRID_RAG_ENABLED", "yes")
    assert pipeline.hybrid_rag_enabled() is True
    monkeypatch.setenv("F2W_SOURCE_RAG", "0")  # the F2W flag wins when set
    assert pipeline.hybrid_rag_enabled() is False
    monkeypatch.setenv("F2W_SOURCE_RAG", "  ")  # blank counts as unset
    assert pipeline.hybrid_rag_enabled() is True


@pytest.fixture
def lit_root(tmp_path, monkeypatch):
    _manifest(tmp_path, [{"title": "P3HT films", "doi": "10.1/p", "pdf_path": "p/a.pdf"}])
    state = {"text": "P3HT crystallinity measured by RSoXS"}
    monkeypatch.setattr(pdf_reader, "open_pdf", lambda p: _FakeDoc([_FakePage(state["text"])]))
    return tmp_path, state


def test_build_literature_index_incremental_skips_unchanged_then_reindexes_changes(lit_root):
    root, state = lit_root
    dest = root / "idx"
    first = pipeline.build_literature_index(dest=dest, root=root, limit=5)
    assert first["indexed_docs"] == ["a.pdf"] and first["n_chunks"] == 1 and first["skipped_unchanged"] == 0
    again = pipeline.build_literature_index(dest=dest, root=root, limit=5)
    assert again["skipped_unchanged"] == 1 and again["n_chunks"] == 1  # unchanged: metadata returned as-is
    (root / "p/a.pdf").write_bytes(b"%PDF-changed")
    state["text"] = "revised text about P3HT"
    third = pipeline.build_literature_index(dest=dest, root=root, limit=5)
    assert third["indexed_docs"] == ["a.pdf"] and third["n_chunks"] == 1
    assert "revised" in SourceIndex(dest).load()[0]["text"]
    full = pipeline.build_literature_index(dest=dest, root=root, limit=5, incremental=False)
    assert full["skipped_unchanged"] == 0


def test_build_literature_index_without_pdfs_records_empty_status(tmp_path):
    meta = pipeline.build_literature_index(dest=tmp_path / "i", root=tmp_path)
    assert meta["status"] == "no_pdfs" and meta["n_chunks"] == 0


def test_build_ops_index_incremental_cycle_and_empty(tmp_path):
    assert pipeline.build_ops_index(dest=tmp_path / "e", root=tmp_path)["status"] == "no_ops_docs"
    page = tmp_path / ".cache/bl1101/pages/als_11-0-1-2.html"
    page.parent.mkdir(parents=True)
    page.write_text("<h2>Energy</h2><p>165 to 1500 eV</p>")
    dest = tmp_path / "ops"
    first = pipeline.build_ops_index(dest=dest, root=tmp_path)
    assert first["indexed_docs"] == ["als_11-0-1-2"]
    assert pipeline.build_ops_index(dest=dest, root=tmp_path)["skipped_unchanged"] == 1
    page.write_text("<h2>Energy</h2><p>200 to 1200 eV</p>")
    assert pipeline.build_ops_index(dest=dest, root=tmp_path)["indexed_docs"] == ["als_11-0-1-2"]
    assert SourceIndex(dest).search("1200 eV")


def test_hybrid_source_rag_retrieve_failure_and_pack_layout_forces_ops_only(tmp_path):
    rag = pipeline.HybridSourceRAG(literature_root=tmp_path / "l", ops_root=tmp_path / "o")
    calls = {}

    def retrieve(question, **kw):
        calls.update(kw)
        raise RuntimeError("index corrupt")

    rag.retriever = SimpleNamespace(retrieve=retrieve, literature=None, ops=None)
    assert rag.retrieve_chunks("q") == []
    out = rag.pack("where is the detector?", layout=True, include_literature=True)
    assert calls["include_literature"] is False and calls["include_ops"] is True
    assert out["chunk_count"] == 0 and out["literature_available"] is False and out["ops_available"] is False
    assert isinstance(pipeline.HybridSourceRAG.from_env(), pipeline.HybridSourceRAG)
