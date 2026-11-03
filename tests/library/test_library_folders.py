"""Uploads into a category: year sub-folder, new categories, and the category manifest (A8)."""

from __future__ import annotations

import json
import os

from fastapi import HTTPException
import pytest

from app.modules.library.arrange import guess_year, is_valid_folder_name, year_folder
from app.modules.library.probe import ProbeResult
from tests.library.test_library_service import FakeProbe, make, pdf

DOI = "10.1021/acs.macromol.0c01782"


def probe_with(text: str = "", meta: str = "", title: str = "") -> FakeProbe:
    return FakeProbe(default=ProbeResult(status="ok", pages=4, title=title, page_text=text, meta_text=meta))


@pytest.mark.parametrize(("name", "text", "meta", "expected"), [
    ("Advanced Materials - 2016 - Nian - Efficient.pdf", "Published online 2018", "", 2016),
    ("paper.pdf", "Received: 3 May 2015; Accepted: 1 June 2015; Published: 20 July 2016", "", 2016),
    ("paper.pdf", "(c) 2014 Elsevier", "", 2014),
    ("paper.pdf", "nothing", "CreationDate\nD:20120105", 2012),
    ("paper.pdf", "nothing", "", None),
    ("1234567.pdf", "Published 1850", "", None),
])
def test_guess_year(name, text, meta, expected):
    assert guess_year(name, text, meta) == expected


def test_helpers():
    assert year_folder(2013) == "2013" and year_folder(None) == "unknown"
    assert is_valid_folder_name("rsoxs") and is_valid_folder_name("tomo-2_x")
    assert not any(is_valid_folder_name(n) for n in ("", ".hidden", "a/b", "..", "a b", "x" * 65))


def test_upload_into_a_category_files_it_by_year_and_creates_the_manifest(tmp_path):
    service, lib = make(tmp_path, probe=probe_with(f"Published 2013\nhttps://doi.org/{DOI}", title="A title"))
    out = service.upload("a.pdf", pdf("a"), "rsoxs")
    assert out["paper"]["relpath"] == "rsoxs/2013/a.pdf" and out["paper"]["folder"] == "rsoxs"
    assert (lib / "rsoxs" / "2013" / "a.pdf").is_file()
    assert out["manifest"] == {"updated": True, "path": "rsoxs/manifest.json", "detail": "added to the manifest"}
    manifest = json.loads((lib / "rsoxs" / "manifest.json").read_text())
    (row,) = manifest["papers"]
    assert manifest["corpus"] == "rsoxs"
    assert row["pdf_path"] == "papers/rsoxs/2013/a.pdf" and row["publication_year"] == 2013
    assert row["doi"] == f"https://doi.org/{DOI}" and row["title"] == "A title" and row["source"] == "upload"
    assert row["ingestion"]["status"] == "pending" and len(row["pdf_sha256"]) == 64
    assert sorted(p.name for p in lib.rglob("*") if p.is_file()) == [".manifest.lock", "a.pdf", "manifest.json"]


def test_unknown_year_goes_to_the_unknown_folder(tmp_path):
    service, lib = make(tmp_path, probe=probe_with("no year anywhere"))
    out = service.upload("b.pdf", pdf("b"), "tomo")
    assert out["paper"]["relpath"] == "tomo/unknown/b.pdf" and (lib / "tomo" / "unknown" / "b.pdf").is_file()
    assert json.loads((lib / "tomo" / "manifest.json").read_text())["papers"][0]["publication_year"] is None


def test_an_existing_manifest_is_appended_to_not_rewritten(tmp_path):
    service, lib = make(tmp_path, probe=probe_with("Published 2020"))
    (lib / "rsoxs").mkdir()
    old = {"corpus": "rsoxs", "kg_version": "v1", "beamline": "11.0.1.2", "papers": [{"doi": None, "title": "old"}]}
    (lib / "rsoxs" / "manifest.json").write_text(json.dumps(old))
    service.upload("c.pdf", pdf("c"), "rsoxs")
    data = json.loads((lib / "rsoxs" / "manifest.json").read_text())
    assert data["beamline"] == "11.0.1.2" and data["papers"][0] == {"doi": None, "title": "old"}
    assert [p["pdf_path"] for p in data["papers"][1:]] == ["papers/rsoxs/2020/c.pdf"]


def test_an_unreadable_manifest_is_left_alone_and_the_upload_still_succeeds(tmp_path):
    service, lib = make(tmp_path, probe=probe_with("Published 2020"))
    (lib / "rsoxs").mkdir()
    (lib / "rsoxs" / "manifest.json").write_text("{not json")
    out = service.upload("d.pdf", pdf("d"), "rsoxs")
    assert out["paper"]["relpath"] == "rsoxs/2020/d.pdf" and out["manifest"]["updated"] is False
    assert (lib / "rsoxs" / "manifest.json").read_text() == "{not json"


def test_a_paper_already_listed_by_doi_with_its_pdf_on_disk_is_not_listed_twice(tmp_path):
    service, lib = make(tmp_path, probe=probe_with(f"Published 2019 https://doi.org/{DOI}"))
    (lib / "rsoxs" / "2019").mkdir(parents=True)
    (lib / "rsoxs" / "2019" / "orig.pdf").write_bytes(pdf("orig"))
    listed = {"papers": [{"doi": f"https://doi.org/{DOI}", "pdf_path": "papers/rsoxs/2019/orig.pdf"}]}
    (lib / "rsoxs" / "manifest.json").write_text(json.dumps(listed))
    out = service.upload("again.pdf", pdf("again"), "rsoxs")
    assert out["manifest"]["updated"] is False and len(json.loads((lib / "rsoxs" / "manifest.json").read_text())["papers"]) == 1


def test_an_existing_category_matches_case_insensitively(tmp_path):
    service, lib = make(tmp_path, probe=probe_with("Published 2011"))
    (lib / "rsoxs").mkdir()
    out = service.upload("e.pdf", pdf("e"), "RSoXS")
    assert out["paper"]["relpath"] == "rsoxs/2011/e.pdf" and sorted(p.name for p in lib.iterdir()) == ["rsoxs"]


def test_same_name_in_the_same_year_folder_gets_a_suffix(tmp_path):
    service, lib = make(tmp_path, probe=probe_with("Published 2011"))
    service.upload("f.pdf", pdf("one"), "tomo")
    out = service.upload("f.pdf", pdf("two"), "tomo")
    assert out["paper"]["relpath"] == "tomo/2011/f (1).pdf"


@pytest.mark.parametrize("folder", ["../x", "a/b", ".hidden", "a b", "x" * 65])
def test_unsafe_folder_names_are_422_and_write_nothing(tmp_path, folder):
    service, lib = make(tmp_path)
    with pytest.raises(HTTPException) as err:
        service.upload("g.pdf", pdf("g"), folder)
    assert err.value.status_code == 422 and list(lib.iterdir()) == []


def test_a_linked_category_is_refused(tmp_path):
    service, lib = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, lib / "linked")
    with pytest.raises(HTTPException) as err:
        service.upload("h.pdf", pdf("h"), "linked")
    assert err.value.status_code == 422 and list(outside.iterdir()) == []


def test_without_a_folder_the_paper_goes_to_the_root_with_no_manifest(tmp_path):
    service, lib = make(tmp_path)
    out = service.upload("i.pdf", pdf("i"))
    assert out["paper"]["relpath"] == "i.pdf" and "manifest" not in out
    assert [p.name for p in lib.iterdir()] == ["i.pdf"]
