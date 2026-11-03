"""T36: the ``library_index`` job, the shared literature index directory and the QA path (fakes only)."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import pytest

from app.modules.contracts.jobs import JobState
from app.modules.hybrid_rag.chunker import PdfChunks, _chunk_record
from app.modules.hybrid_rag.index_store import SourceIndex, index_lock, write_index
from app.modules.hybrid_rag.pipeline import HybridSourceRAG, build_literature_index
from app.modules.jobs import JobService, JobStore
from app.modules.library import LIBRARY_INDEX_KIND, library_inputs, register_library_jobs
from app.modules.library.catalog import LibraryCatalog, paper_id
from app.modules.library.handlers import library_handlers
from app.modules.library.indexer import doc_id_for, work_id_for
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.security.security_fakes import local_settings

DOI = "10.1000/lib.test"


class FakeChunker:
    """Reads a "PDF" that is plain text: ``SCAN`` means no text layer, ``BAD`` an unreadable file."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, path: Path, *, work_id: str, doc_id: str, corpus: str, extra: dict[str, Any]) -> PdfChunks:
        self.calls.append(path.name)
        raw = path.read_text(encoding="utf-8", errors="replace")
        if raw.startswith("BAD"):
            return PdfChunks()
        if raw.startswith("SCAN"):
            return PdfChunks(readable=True, pages=3)
        chunks = [_chunk_record(corpus=corpus, work_id=work_id, doc_id=doc_id, path=str(path), page=i + 1,
                                locator=f"p.{i + 1}", heading="", text=part, chunk_index=0, extra=extra)
                  for i, part in enumerate(raw.split("|"))]
        return PdfChunks(chunks=chunks, pages=len(chunks), readable=True, first_text=f"doi {DOI}" if "doi" in raw else "")


def make(tmp_path: Path, *, chunker: FakeChunker | None = None, index: bool = True) -> tuple[LibraryService, Path, Path]:
    lib = tmp_path / "papers"
    lib.mkdir(exist_ok=True)
    idx = tmp_path / "state" / "source_index" / "literature"
    service = LibraryService(lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                             probe=lambda p: ProbeResult(status="ok", pages=2, page_text="nothing"),
                             index_dir=idx if index else None, chunker=chunker or FakeChunker())
    return service, lib, idx


def put(lib: Path, name: str, text: str) -> None:
    (lib / name).parent.mkdir(parents=True, exist_ok=True)
    (lib / name).write_text(text, encoding="utf-8")


def run_index(service: LibraryService, tmp_path: Path, nonce: str = "1") -> tuple[JobService, Any, JobState]:
    settings = local_settings()
    jobs = JobService(JobStore(tmp_path / "jobs.sqlite3"), settings=settings)
    register_library_jobs(jobs, settings, handlers=library_handlers(service))
    handle = jobs.submit(service.worker_context("submit"), LIBRARY_INDEX_KIND, library_inputs(nonce=nonce))
    job_id, state = jobs.work_one("w")
    assert job_id == handle.job_id
    return jobs, handle, state


def docs_of(idx: Path) -> dict[str, Any]:
    return SourceIndex(idx).meta()["docs"]


def test_index_builds_library_chunks_with_a_docs_record(tmp_path):
    service, lib, idx = make(tmp_path)
    put(lib, "a.pdf", "alpha polymer film|second page about nexafs")
    put(lib, "sub/b.pdf", "doi mention beta")
    put(lib, "scan.pdf", "SCAN")
    put(lib, "bad.pdf", "BAD")
    _, _, state = run_index(service, tmp_path)
    assert state is JobState.SUCCEEDED
    docs = docs_of(idx)
    assert set(docs) == {"lib:a.pdf", "lib:sub/b.pdf", "lib:scan.pdf", "lib:bad.pdf"}
    a = docs["lib:a.pdf"]
    assert (a["status"], a["n_chunks"], a["pages"]) == ("ok", 2, 2) and len(a["sha256"]) == 64
    assert docs["lib:sub/b.pdf"]["doi"] == DOI  # found on the first pages
    assert (docs["lib:scan.pdf"]["status"], docs["lib:scan.pdf"]["n_chunks"]) == ("no_text", 0)
    assert docs["lib:bad.pdf"]["status"] == "no_text" and docs["lib:bad.pdf"]["reason"] == "unreadable"
    chunks = SourceIndex(idx).load()
    assert {c["corpus"] for c in chunks} == {"library"}
    assert {c["doc_id"] for c in chunks} == {"lib:a.pdf", "lib:sub/b.pdf"}
    by_doc = {c["doc_id"]: c for c in chunks}
    assert by_doc["lib:a.pdf"]["work_id"] == f"lib:{a['sha256'][:16]}"
    assert by_doc["lib:sub/b.pdf"]["work_id"] == f"doi:{DOI}"
    assert all(not c["path"].startswith("/") for c in chunks)  # a relative path, never the mount's
    meta = SourceIndex(idx).meta()
    assert "library" in meta["corpora"] and meta["n_chunks"] == 3


def test_work_and_doc_ids():
    assert doc_id_for("x/y.pdf") == "lib:x/y.pdf"
    assert work_id_for("10.1/ABC", "f" * 64) == "doi:10.1/abc"
    assert work_id_for(None, "ab" * 32) == "lib:" + "ab" * 8


def test_catalog_reports_index_status_pages_and_the_count(tmp_path):
    service, lib, idx = make(tmp_path)
    put(lib, "a.pdf", "alpha")
    put(lib, "scan.pdf", "SCAN")
    put(lib, "c.pdf", "gamma")
    assert {p["index_status"] for p in service.list_papers()["papers"]} == {"not_indexed"}
    assert service.status()["indexed_count"] == 0
    run_index(service, tmp_path)
    papers = {p["file_name"]: p for p in service.list_papers()["papers"]}
    assert {k: v["index_status"] for k, v in papers.items()} == {"a.pdf": "indexed", "scan.pdf": "no_text",
                                                                  "c.pdf": "indexed"}
    assert service.status()["indexed_count"] == 2
    assert papers["a.pdf"]["pages"] == 2
    # the bytes change: the paper is stale until the next run
    put(lib, "a.pdf", "alpha changed")
    papers = {p["file_name"]: p for p in service.list_papers()["papers"]}
    assert papers["a.pdf"]["index_status"] == "stale" and service.status()["indexed_count"] == 1


def test_pages_reach_paper_facts_from_the_probe_and_from_the_index(tmp_path):
    service, lib, _ = make(tmp_path)
    put(lib, "a.pdf", "alpha|beta|gamma")
    service.list_papers()  # probes: 2 pages
    assert service.paper_facts(paper_id("a.pdf")).pages == 2
    (lib / "b.pdf").write_text("one|two|three|four", encoding="utf-8")
    service.refresh(max_probe=0)  # hashed, not probed: no page count yet
    assert service.paper_facts(paper_id("b.pdf")).pages is None
    run_index(service, tmp_path)
    assert service.paper_facts(paper_id("b.pdf")).pages == 4  # the index run counted them


def test_catalog_migration_adds_columns_to_a_t35_database(tmp_path):
    import sqlite3

    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE papers (relpath TEXT PRIMARY KEY, id TEXT NOT NULL, sha256 TEXT NOT NULL,
      size INTEGER NOT NULL, mtime REAL NOT NULL, title TEXT, doi TEXT, doi_source TEXT,
      meta_status TEXT NOT NULL DEFAULT 'pending', index_status TEXT, first_seen REAL NOT NULL);
      INSERT INTO papers VALUES ('a.pdf','x','s',1,1.0,NULL,NULL,NULL,'ok',NULL,1.0);""")
    con.commit()
    con.close()
    catalog = LibraryCatalog(path)
    assert catalog.paper("a.pdf")["pages"] is None and catalog.indexed_count() == 0


def test_unchanged_rerun_does_nothing(tmp_path):
    chunker = FakeChunker()
    service, lib, idx = make(tmp_path, chunker=chunker)
    put(lib, "a.pdf", "alpha")
    run_index(service, tmp_path)
    before = (idx / "meta.json").stat().st_mtime_ns, (idx / "meta.json").read_text(), (idx / "chunks.jsonl").read_text()
    chunker.calls.clear()
    jobs, handle, state = run_index(service, tmp_path, nonce="2")
    assert state is JobState.SUCCEEDED and chunker.calls == []
    assert ((idx / "meta.json").stat().st_mtime_ns, (idx / "meta.json").read_text(),
            (idx / "chunks.jsonl").read_text()) == before
    assert any("unchanged" in log.message for log in jobs.store.logs(handle.job_id))


def test_replaced_pdf_is_rechunked_and_deleted_one_pruned(tmp_path):
    chunker = FakeChunker()
    service, lib, idx = make(tmp_path, chunker=chunker)
    put(lib, "a.pdf", "alpha original")
    put(lib, "b.pdf", "beta text")
    put(lib, "c.pdf", "gamma text")
    run_index(service, tmp_path)
    old_sha = docs_of(idx)["lib:a.pdf"]["sha256"]
    chunker.calls.clear()
    put(lib, "a.pdf", "alpha replaced entirely")
    (lib / "b.pdf").unlink()
    put(lib, "c.pdf", "SCAN")  # now scanned: its old chunks go
    run_index(service, tmp_path, nonce="2")
    assert sorted(chunker.calls) == ["a.pdf", "c.pdf"]
    docs = docs_of(idx)
    assert set(docs) == {"lib:a.pdf", "lib:c.pdf"} and docs["lib:a.pdf"]["sha256"] != old_sha
    assert docs["lib:c.pdf"]["status"] == "no_text"
    texts = {c["doc_id"]: c["text"] for c in SourceIndex(idx).load()}
    assert texts == {"lib:a.pdf": "alpha replaced entirely"}
    assert service.status()["indexed_count"] == 1


def test_no_index_directory_fails_closed(tmp_path):
    service, lib, _ = make(tmp_path, index=False)
    put(lib, "a.pdf", "alpha")
    _, _, state = run_index(service, tmp_path)
    assert state is JobState.FAILED


def test_cancel_keeps_finished_papers_and_a_new_run_finishes(tmp_path):
    from app.modules.library.indexer import LibraryIndexer

    chunker = FakeChunker()
    service, lib, idx = make(tmp_path, chunker=chunker)
    for n in "abcd":
        put(lib, f"{n}.pdf", f"text {n}")
    settings = local_settings()
    jobs = JobService(JobStore(tmp_path / "jobs.sqlite3"), settings=settings)
    register_library_jobs(jobs, settings, handlers=library_handlers(service))
    handle = jobs.submit(service.worker_context("s"), LIBRARY_INDEX_KIND, library_inputs(nonce="1"))

    def stopping(path, **kw):
        out = chunker(path, **kw)
        if len(chunker.calls) == 2:  # the operator cancels while the second paper is read
            jobs.store.request_stop(handle.job_id, actor="op")
        return out

    service._indexer = LibraryIndexer(service, idx, chunker=stopping, flush_every=100)
    assert jobs.work_one("w") == (handle.job_id, JobState.CANCELLED)
    assert len(docs_of(idx)) == 2  # the finished papers were flushed on the way out
    service._indexer = LibraryIndexer(service, idx, chunker=chunker)
    chunker.calls.clear()
    _, _, state = run_index(service, tmp_path, nonce="2")
    assert state is JobState.SUCCEEDED and len(chunker.calls) == 2 and len(docs_of(idx)) == 4


def test_a_checkpointed_unit_whose_chunks_never_landed_is_indexed_anyway(tmp_path):
    from app.modules.library.indexer import LibraryIndexer

    service, lib, idx = make(tmp_path)
    put(lib, "a.pdf", "alpha")
    run_index(service, tmp_path)
    (idx / "meta.json").unlink()  # the index is lost; the job's unit checkpoint still says "done"
    (idx / "chunks.jsonl").unlink()
    jobs = JobService(JobStore(tmp_path / "jobs.sqlite3"), settings=local_settings())
    register_library_jobs(jobs, local_settings(), handlers=library_handlers(service))
    handle = jobs.submit(service.worker_context("s"), LIBRARY_INDEX_KIND, library_inputs(nonce="1"))
    jobs.work_one("w")  # same identity as the first run: a finished job, nothing to do
    handle2 = jobs.submit(service.worker_context("s"), LIBRARY_INDEX_KIND, library_inputs(nonce="2"))
    assert jobs.work_one("w") == (handle2.job_id, JobState.SUCCEEDED)
    assert set(docs_of(idx)) == {"lib:a.pdf"}
    assert isinstance(service.indexer, LibraryIndexer)
    assert handle.job_id


# -- the shared index directory ---------------------------------------------------------------------------


def test_literature_builder_keeps_library_chunks_and_docs(tmp_path):
    service, lib, idx = make(tmp_path)
    put(lib, "a.pdf", "alpha polymer")
    run_index(service, tmp_path)
    docs_before = docs_of(idx)
    root = tmp_path / "repo"
    root.mkdir()  # no harvest manifest: nothing selected
    for incremental in (True, False):
        build_literature_index(dest=idx, root=root, incremental=incremental)
        assert docs_of(idx) == docs_before
        assert {c["corpus"] for c in SourceIndex(idx).load()} == {"library"}


def test_write_index_preserves_requested_meta_keys_and_is_atomic(tmp_path):
    root = tmp_path / "idx"
    write_index(root, [], index_name="x", corpus_revision="r1", extra_meta={"docs": {"k": {}}, "mode": "gold"})
    meta = write_index(root, [], index_name="x", corpus_revision="r2", preserve_meta_keys=("docs", "absent"))
    assert meta["docs"] == {"k": {}} and "mode" not in meta and "absent" not in meta
    assert sorted(p.name for p in root.iterdir()) == ["chunks.jsonl", "meta.json"]  # no temporary file left


def test_source_index_reloads_when_meta_changes(tmp_path):
    root = tmp_path / "idx"
    chunk = {"chunk_id": "c1", "corpus": "library", "doc_id": "lib:a", "text": "polymer film"}
    write_index(root, [chunk], index_name="x", corpus_revision="r1")
    reader = SourceIndex(root)
    assert [h[0]["chunk_id"] for h in reader.search("polymer")] == ["c1"]
    write_index(root, [{**chunk, "chunk_id": "c2", "text": "polymer blend"}], index_name="x", corpus_revision="r2")
    assert [h[0]["chunk_id"] for h in reader.search("polymer")] == ["c2"]
    write_index(root, [], index_name="x", corpus_revision="r3")
    assert reader.search("polymer") == []


def test_a_concurrent_reader_never_sees_a_torn_index(tmp_path):
    root = tmp_path / "idx"
    write_index(root, [], index_name="x", corpus_revision="r0")
    stop = threading.Event()
    errors: list[str] = []

    def writer() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            chunks = [{"chunk_id": f"g{n}-{i}", "corpus": "library", "doc_id": f"lib:{i}", "text": f"polymer {n} " * 50}
                      for i in range(40)]
            with index_lock(root):
                write_index(root, chunks, index_name="x", corpus_revision=f"r{n}")

    def reader() -> None:
        index = SourceIndex(root)
        for _ in range(300):
            try:
                hits = index.search("polymer", k=100)
                generations = {h[0]["chunk_id"].split("-")[0] for h in hits}
                if len(hits) not in (0, 40) or len(generations) > 1:
                    errors.append(f"torn: {len(hits)} hits from {sorted(generations)}")
                meta = index.meta()
                if meta and not isinstance(meta.get("n_chunks"), int):
                    errors.append("torn meta")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        reader()
    finally:
        stop.set()
        thread.join()
    assert errors == []


def test_index_lock_times_out_when_held(tmp_path):
    root = tmp_path / "idx"
    with index_lock(root):
        with pytest.raises(TimeoutError):
            with index_lock(root, timeout=0.2, poll=0.02):
                pass


# -- QA over the library through the existing hybrid_rag search path ---------------------------------------


def test_a_question_over_a_library_fixture_returns_a_library_citation(tmp_path):
    service, lib, idx = make(tmp_path)
    put(lib, "film.pdf", "intro text|carbon k-edge nexafs of the poly thiophene film shows a pi star peak")
    put(lib, "other.pdf", "unrelated material about batteries")
    run_index(service, tmp_path)
    rag = HybridSourceRAG(literature_root=idx, ops_root=tmp_path / "no_ops")
    out = rag.pack("What does nexafs show for the thiophene film?")
    assert out["literature_available"] is True
    cited = [e for e in out["evidence"] if e.source_kind == "paper_chunk"]
    assert cited and cited[0].corpus == "library" and cited[0].paper_id == "lib:film.pdf"
    assert cited[0].page == 2 and cited[0].locator == "p.2"
    assert not any(str(tmp_path) in json.dumps(e.provenance) for e in cited)  # no absolute path leaves
    # a harvest literature build later does not lose the library
    assert any(c["corpus"] == "library" for c in SourceIndex(idx).load())


def test_startup_reads_the_index_directory(tmp_path):
    from app.modules.security.authorization import StartupConfigError

    lib = tmp_path / "papers"
    lib.mkdir()
    ok = LibraryService.from_startup({"F2W_LIBRARY_DIR": str(lib), "HYBRID_RAG_LITERATURE_INDEX": str(tmp_path / "i")},
                                     tmp_path / "w", local_settings())
    assert ok.index_dir == tmp_path / "i"
    assert LibraryService.from_startup({}, tmp_path / "w", local_settings()).index_dir is None
    with pytest.raises(StartupConfigError):
        LibraryService.from_startup({"HYBRID_RAG_LITERATURE_INDEX": "relative/dir"}, tmp_path / "w", local_settings())
