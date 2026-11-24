"""P3b: authors and abstracts are stored in the manifest at harvest, upload and verify time (fakes only, no network)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from app.modules.library import harvest as harvest_mod
from app.modules.library.bibmeta import (
    MAX_ABSTRACT_STORED,
    MAX_AUTHORS_STORED,
    cap_abstract,
    clean_author_list,
    openalex_abstract,
    openalex_authors,
)
from app.modules.library.harvest import Candidate, HarvestStore, arxiv_searcher, merge_candidates, openalex_searcher
from app.modules.library.manifest import fill_missing, fill_row_metadata, record_upload
from app.modules.literature import legacy_download
from app.modules.literature.legacy_download import RateLimiter, parse_arxiv_feed
from tests.library.test_library_harvest import GOOD, PAPERS, SERVED, World, cand, fixed, Net
from tests.library.test_library_service import DOI, TEXT, TITLE, FakeProbe, Registry, make, pdf
from tests.literature.lit_fakes import local_ctx
from app.modules.library.probe import ProbeResult

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <updated>2021-01-02T00:00:00Z</updated><published>2021-01-01T00:00:00Z</published>
    <title>Resonant soft X-ray scattering</title>
    <summary>  We   measure
      domains.</summary>
    <author><name>Ada  Lovelace</name></author>
    <author><name>Grace Hopper</name><arxiv:affiliation>Navy</arxiv:affiliation></author>
    <author><name>  </name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2101.00001v2"/>
  </entry>
</feed>"""

WORK = {
    "id": "https://openalex.org/W1", "doi": f"https://doi.org/{DOI}", "title": TITLE, "publication_year": 2020,
    "authorships": [{"author": {"display_name": "A. One"}}, {"author": {"display_name": "B. Two <b@x.org>"}},
                    {"raw_author_name": "C. Three"}, {"author": None}],
    "abstract_inverted_index": {"Lamellar": [0], "domains": [1], "form.": [2]},
}


# -- helpers -----------------------------------------------------------------------------------------------


def test_clean_authors_strip_emails_dedupe_and_cap():
    names = clean_author_list(["  A.  One ", "A. One", "B. Two b@x.org", "", 5, "x" * 500] + [f"N{i}" for i in range(300)])
    assert names[:3] == ["A. One", "B. Two", "x" * 200] and len(names) == MAX_AUTHORS_STORED
    assert not any("@" in n for n in names)


def test_cap_abstract_collapses_and_cuts():
    assert cap_abstract("  a \n b ") == "a b" and cap_abstract("   ") is None and cap_abstract(None) is None
    assert len(cap_abstract("w " * 5000) or "") <= MAX_ABSTRACT_STORED


def test_openalex_record_helpers():
    assert openalex_authors(WORK) == ["A. One", "B. Two", "C. Three"]
    assert openalex_abstract(WORK) == "Lamellar domains form."
    assert openalex_authors({}) == [] and openalex_abstract({"abstract_inverted_index": None}) is None


# -- sources -----------------------------------------------------------------------------------------------


def test_arxiv_atom_authors_are_parsed():
    (entry,), _ = parse_arxiv_feed(ATOM)
    assert entry["authors"] == ["Ada Lovelace", "Grace Hopper"]


def test_arxiv_searcher_carries_authors_and_abstract(monkeypatch):
    monkeypatch.setattr(legacy_download, "ARXIV_LIMITER", RateLimiter(3.0, clock=lambda: 0.0, sleep=lambda s: None))
    found = arxiv_searcher(get_bytes=lambda url, **kw: ATOM)("resonant soft x-ray scattering", 1)
    assert found[0].authors == ["Ada Lovelace", "Grace Hopper"] and found[0].abstract == "We measure domains."


def test_openalex_searcher_selects_authorships_and_sends_the_key_only_as_a_header(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k-test-123")
    seen: list[tuple[str, dict[str, str]]] = []

    def get_json(url: str, *, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        seen.append((url, dict(headers or {})))
        return {"results": [WORK], "meta": {}}

    (found,) = openalex_searcher(get_json=get_json, sleep=lambda s: None)("lamellar domains", 5)
    url, headers = seen[0]
    assert "authorships" in url and "abstract_inverted_index" in url and "k-test-123" not in url
    assert headers["Authorization"] == "Bearer k-test-123"
    assert found.authors == ["A. One", "B. Two", "C. Three"] and found.abstract == "Lamellar domains form."


def test_merge_keeps_the_first_non_empty_authors():
    a = Candidate("A", "arxiv", doi="10.1/x")
    b = Candidate("A", "openalex", doi="10.1/x", authors=["Z. Last"])
    assert merge_candidates([[a], [b]])[0].authors == ["Z. Last"]


# -- manifest rows -----------------------------------------------------------------------------------------


def _manifest(lib: Path, category: str = "rsoxs") -> dict[str, Any]:
    return json.loads((lib / category / "manifest.json").read_text())


def test_record_upload_stores_authors_and_abstract_and_older_calls_stay_valid(tmp_path):
    (tmp_path / "rsoxs").mkdir()
    kw = {"category": "rsoxs", "year": 2020, "now": "t", "doi": "10.1/a"}
    record_upload(tmp_path, relpath="rsoxs/2020/a.pdf", sha256="a", title="A", authors=["A. One", "e@x.org"],
                  abstract="  Text  here ", **kw)  # type: ignore[arg-type]
    record_upload(tmp_path, relpath="rsoxs/2020/b.pdf", sha256="b", title="B", **{**kw, "doi": "10.1/b"})  # type: ignore[arg-type]
    rows = {r["pdf_sha256"]: r for r in _manifest(tmp_path)["papers"]}
    assert rows["a"]["authors"] == ["A. One"] and rows["a"]["abstract"] == "Text here"
    assert rows["b"]["authors"] is None and rows["b"]["abstract"] is None  # unknown stays explicit null


def test_fill_missing_never_overwrites_a_stored_value():
    row = {"authors": ["Kept"], "abstract": None}
    assert fill_missing(row, authors=["New"], abstract="New abstract") == ["abstract"]
    assert row == {"authors": ["Kept"], "abstract": "New abstract"}
    row = {"abstract": "Kept", "authors": []}
    assert fill_missing(row, authors=["New"], abstract="Other") == ["authors"] and row["abstract"] == "Kept"
    assert fill_missing({}, authors=None, abstract="") == []


def test_fill_row_metadata_rewrites_atomically_and_only_when_something_changed(tmp_path):
    (tmp_path / "rsoxs").mkdir()
    path = tmp_path / "rsoxs" / "manifest.json"
    path.write_text(json.dumps({"corpus": "rsoxs", "papers": [{"pdf_path": "papers/rsoxs/a.pdf", "abstract": "Kept"}]}))
    match = lambda r: r.get("pdf_path") == "papers/rsoxs/a.pdf"  # noqa: E731
    assert fill_row_metadata(path, match, authors=["A"], abstract="X") == ["authors"]
    stamp = path.stat().st_mtime_ns
    assert fill_row_metadata(path, match, authors=["B"], abstract="Y") == [] and path.stat().st_mtime_ns == stamp
    assert not (tmp_path / "rsoxs" / "manifest.json.tmp").exists()
    assert fill_row_metadata(tmp_path / "none" / "manifest.json", match, authors=["A"], abstract=None) == []
    path.write_text("not json")
    assert fill_row_metadata(path, match, authors=["A"], abstract="X") == [] and path.read_text() == "not json"


# -- the harvest -------------------------------------------------------------------------------------------


def _with_meta(c: Candidate, **kw: Any) -> Candidate:
    return Candidate(**{**c.__dict__, **kw})


def test_harvest_writes_authors_and_abstract_into_the_manifest_row(tmp_path):
    papers = (_with_meta(PAPERS[0], authors=["A. One", "B. Two"], abstract="Blends of  polymers."), *PAPERS[1:])
    world = World(tmp_path, {"arxiv": fixed(*papers)}, Net(SERVED))
    world.run(sources=["arxiv"])
    rows = {r["doi"]: r for r in _manifest(world.lib)["papers"]}
    assert rows["https://doi.org/10.1/a"]["authors"] == ["A. One", "B. Two"]
    assert rows["https://doi.org/10.1/a"]["abstract"] == "Blends of polymers."
    assert rows["https://doi.org/10.1/b"]["authors"] is None
    listed = {p["relpath"]: p for p in world.service.list_papers()["papers"]}["rsoxs/2013/10.1_a.pdf"]
    assert listed["authors"] == ["A. One", "B. Two"] and listed["abstract"] == "Blends of polymers."
    assert listed["metadata_missing"] == []


def test_a_hand_supplied_paywalled_pdf_keeps_the_candidates_authors_and_abstract(tmp_path):
    paywalled = _with_meta(PAPERS[2], authors=["P. Wall"], abstract="Behind a paywall.")
    world = World(tmp_path, {"arxiv": fixed(PAPERS[0], paywalled)}, Net(SERVED))
    handle, _ = world.run(sources=["arxiv"])
    row = next(c for c in world.store.candidates(handle.job_id) if c["status"] == "paywalled")
    assert "authors" not in row and "abstract" not in row  # the list API never carries them
    out = world.harvester.supply_pdf(row["id"], "x.pdf", pdf("by-hand"), confirm=True)
    assert "authors" not in out["candidate"] and "abstract" not in out["candidate"]
    stored = next(r for r in _manifest(world.lib)["papers"] if r["doi"] == "https://doi.org/10.1/p")
    assert stored["authors"] == ["P. Wall"] and stored["abstract"] == "Behind a paywall."


def test_an_older_harvest_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "harvest.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, topic TEXT NOT NULL, category TEXT NOT NULL, schema_label TEXT,"
        " sources TEXT NOT NULL, max_downloads INTEGER NOT NULL, created_at TEXT NOT NULL);"
        "CREATE TABLE candidates (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL,"
        " year INTEGER, doi TEXT, arxiv_id TEXT, source TEXT NOT NULL, tier TEXT NOT NULL, score REAL NOT NULL,"
        " status TEXT NOT NULL, landing_url TEXT, pdf_url TEXT, relpath TEXT, detail TEXT, UNIQUE (run_id, key));"
        "INSERT INTO candidates VALUES ('c1','r','k','T',2020,'10.1/o',NULL,'arxiv','A',1.0,'paywalled','','',NULL,'');")
    con.commit()
    con.close()
    store = HarvestStore(path)
    old = store.candidate("c1", with_metadata=True)
    assert old is not None and old["authors"] == [] and old["abstract"] is None
    store.save_candidate("r", _with_meta(cand("T", doi="10.1/o"), authors=["N. New"], abstract="Now stored."),
                         status="found")
    HarvestStore(path)  # opening again does not fail on the existing columns


def test_logs_and_job_messages_never_hold_authors_or_abstract(tmp_path):
    papers = (_with_meta(PAPERS[0], authors=["Secretname Person"], abstract="Confidential abstract words."),)
    world = World(tmp_path, {"arxiv": fixed(*papers)}, Net(SERVED))
    handle, _ = world.run(sources=["arxiv"])
    text = " ".join(entry.message for entry in world.jobs.store.logs(handle.job_id))
    assert "Secretname" not in text and "Confidential" not in text


# -- verify ------------------------------------------------------------------------------------------------


def test_verify_stores_the_works_authors_and_abstract_in_the_manifest_row_once(tmp_path):
    registry = Registry({DOI: WORK})
    service, lib = make(tmp_path, probe=FakeProbe({"a.pdf": ProbeResult(status="ok", title="t", page_text=TEXT)}),
                        registry=registry)
    target = lib / "rsoxs" / "2020" / "a.pdf"
    target.parent.mkdir(parents=True)
    data = pdf("a")
    target.write_bytes(data)
    import hashlib
    (lib / "rsoxs" / "manifest.json").write_text(json.dumps({"corpus": "rsoxs", "papers": [
        {"pdf_path": "papers/rsoxs/2020/a.pdf", "pdf_sha256": hashlib.sha256(data).hexdigest(), "abstract": "Kept",
         "authors": None}]}))
    service.list_papers()
    paper_id = next(p["id"] for p in service.list_papers()["papers"])
    assert service.verify_paper(paper_id, local_ctx())["paper"]["verify_status"] == "verified"
    row = _manifest(lib)["papers"][0]
    assert row["authors"] == ["A. One", "B. Two", "C. Three"] and row["abstract"] == "Kept"
    shown = service.list_papers()["papers"][0]  # the stat-keyed cache sees the rewritten file
    assert shown["authors"] == ["A. One", "B. Two", "C. Three"] and shown["abstract"] == "Kept"


def test_the_harvest_module_selects_authorships():
    assert "authorships" in harvest_mod._OPENALEX_FIELDS.split(",")
