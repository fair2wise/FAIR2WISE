"""Stored year, authors and abstract in the library list (owner bug list, task 10): fake PDFs and manifests only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.library.metadata import (
    MAX_ABSTRACT_CHARS,
    MAX_AUTHORS,
    ManifestMetadata,
    clean_abstract,
    clean_authors,
    folder_year,
)
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.library.test_library_service import pdf
from tests.security.security_fakes import local_settings


def service(tmp_path: Path) -> tuple[LibraryService, Path]:
    lib = tmp_path / "papers"
    lib.mkdir()
    svc = LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                         probe=lambda p: ProbeResult(status="ok", pages=2, page_text="x"))
    return svc, lib


def put(lib: Path, relpath: str, tag: str) -> bytes:
    target = lib / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    data = pdf(tag)
    target.write_bytes(data)
    return data


def manifest(lib: Path, category: str, rows: list[dict[str, Any]]) -> None:
    (lib / category).mkdir(exist_ok=True)
    (lib / category / "manifest.json").write_text(json.dumps({"corpus": category, "papers": rows}), encoding="utf-8")


def listed(svc: LibraryService) -> dict[str, dict[str, Any]]:
    return {p["relpath"]: p for p in svc.list_papers()["papers"]}


def test_stored_fields_come_from_the_manifest_row_and_missing_ones_are_named(tmp_path):
    svc, lib = service(tmp_path)
    put(lib, "rsoxs/2013/a.pdf", "a")
    manifest(lib, "rsoxs", [{"pdf_path": "papers/rsoxs/2013/a.pdf", "publication_year": 2014,
                              "abstract": "  We  study\nblends. ", "authors": ["A. One", {"name": "B. Two"},
                                                                              {"author": {"display_name": "C. Three"}}]}])
    paper = listed(svc)["rsoxs/2013/a.pdf"]
    assert paper["year"] == 2014 and paper["year_source"] == "manifest"
    assert paper["authors"] == ["A. One", "B. Two", "C. Three"] and paper["authors_total"] == 3
    assert paper["abstract"] == "We study blends." and paper["abstract_truncated"] is False
    assert paper["metadata_missing"] == []


def test_the_harvest_scripts_manifest_shape_has_no_authors_so_they_are_reported_missing(tmp_path):
    svc, lib = service(tmp_path)
    put(lib, "rsoxs/2019/b.pdf", "b")
    manifest(lib, "rsoxs", [{"pdf_path": "papers/rsoxs/2019/b.pdf", "publication_year": 2019, "abstract": "Text.",
                              "title": "B"}])
    paper = listed(svc)["rsoxs/2019/b.pdf"]
    assert paper["authors"] is None and paper["metadata_missing"] == ["authors"]


def test_the_year_falls_back_to_the_year_folder_and_everything_else_is_missing(tmp_path):
    svc, lib = service(tmp_path)
    put(lib, "tomo/2021/c.pdf", "c")
    put(lib, "tomo/unknown/d.pdf", "d")
    put(lib, "e.pdf", "e")
    papers = listed(svc)
    c = papers["tomo/2021/c.pdf"]
    assert (c["year"], c["year_source"], c["abstract"], c["authors"]) == (2021, "folder", None, None)
    assert c["metadata_missing"] == ["authors", "abstract"]
    for rel in ("tomo/unknown/d.pdf", "e.pdf"):
        assert papers[rel]["year"] is None and papers[rel]["year_source"] is None
        assert papers[rel]["metadata_missing"] == ["year", "authors", "abstract"]


def test_a_row_is_found_by_hash_or_doi_when_the_path_differs(tmp_path):
    svc, lib = service(tmp_path)
    data = put(lib, "moved/2020/f.pdf", "f")
    import hashlib

    manifest(lib, "rsoxs", [{"pdf_path": "papers/rsoxs/2020/old.pdf", "pdf_sha256": hashlib.sha256(data).hexdigest(),
                              "publication_year": 2018, "abstract": "By hash."}])
    paper = listed(svc)["moved/2020/f.pdf"]
    assert paper["year"] == 2018 and paper["abstract"] == "By hash."


def test_a_broken_manifest_means_not_stored_not_an_error(tmp_path):
    svc, lib = service(tmp_path)
    put(lib, "rsoxs/2013/a.pdf", "a")
    (lib / "rsoxs" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert listed(svc)["rsoxs/2013/a.pdf"]["metadata_missing"] == ["authors", "abstract"]


def test_a_changed_manifest_is_reread(tmp_path):
    svc, lib = service(tmp_path)
    put(lib, "rsoxs/2013/a.pdf", "a")
    manifest(lib, "rsoxs", [{"pdf_path": "papers/rsoxs/2013/a.pdf", "abstract": "One."}])
    assert listed(svc)["rsoxs/2013/a.pdf"]["abstract"] == "One."
    manifest(lib, "rsoxs", [{"pdf_path": "papers/rsoxs/2013/a.pdf", "abstract": "Two, longer."}])
    assert listed(svc)["rsoxs/2013/a.pdf"]["abstract"] == "Two, longer."


def test_long_abstracts_and_author_lists_are_capped_with_the_true_count(tmp_path):
    text, flag = clean_abstract("w " * 3000)
    assert flag is True and text is not None and len(text) <= MAX_ABSTRACT_CHARS + 1 and text.endswith("…")
    names, total = clean_authors([f"Author {i}" for i in range(MAX_AUTHORS + 15)])
    assert len(names) == MAX_AUTHORS and total == MAX_AUTHORS + 15
    assert clean_authors("A. One; B. Two") == (["A. One", "B. Two"], 2)
    assert clean_authors(None) == ([], 0) and clean_abstract("   ") == (None, False) and clean_abstract(5) == (None, False)


def test_folder_year_reads_only_four_digit_year_folders():
    assert folder_year("rsoxs/2013/x.pdf") == 2013
    assert folder_year("rsoxs/unknown/x.pdf") is None and folder_year("x.pdf") is None and folder_year("rsoxs/x.pdf") is None
    assert folder_year("rsoxs/12345/x.pdf") is None


def test_the_lookup_ignores_links_and_invalid_category_folders(tmp_path):
    svc, lib = service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text(json.dumps({"papers": [{"pdf_path": "papers/x.pdf", "abstract": "leak"}]}))
    (lib / "link").symlink_to(outside)
    assert ManifestMetadata().index(lib).describe("x.pdf", "0", None)["abstract"] is None
