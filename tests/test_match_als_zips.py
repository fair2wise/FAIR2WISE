"""Unit tests for ALS zip ↔ harvest DOI matching (no network, no zip extract)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from match_als_zips import (
    bare_doi,
    doi_compact_key,
    harvest_pdf_stem,
    keys_match,
    zip_member_compact_key,
)


def test_bare_doi_strips_url_keeps_slash():
    assert bare_doi("https://doi.org/10.1103/PhysRevB.69.115425") == "10.1103/PhysRevB.69.115425"
    assert bare_doi("http://dx.doi.org/10.1002/adma.202107316") == "10.1002/adma.202107316"
    assert bare_doi("10.1021/acs.macromol.8b02198") == "10.1021/acs.macromol.8b02198"


def test_doi_compact_key_drops_slashes_and_case():
    assert doi_compact_key("https://doi.org/10.1103/PhysRevLett.119.167801") == (
        "10.1103physrevlett.119.167801"
    )
    assert doi_compact_key("10.1103/physrevlett.119.167801") == "10.1103physrevlett.119.167801"
    assert doi_compact_key("https://doi.org/10.1080/08940886.2020.1784698") == (
        "10.108008940886.2020.1784698"
    )
    assert doi_compact_key("10.48550/arXiv.2202.11587") == "10.48550arxiv.2202.11587"
    assert doi_compact_key(None) == ""
    assert doi_compact_key("") == ""


def test_zip_member_compact_key_uses_basename():
    assert zip_member_compact_key("ALS/10.1103PhysRevB.69.115425.pdf") == (
        "10.1103physrevb.69.115425"
    )
    assert zip_member_compact_key("ALS/10.108008940886.2020.1784698.PDF") == (
        "10.108008940886.2020.1784698"
    )


def test_keys_match_zip_stem_to_harvest_doi():
    member = "ALS/10.1103PhysRevLett.119.167801.pdf"
    assert keys_match("https://doi.org/10.1103/physrevlett.119.167801", member)
    assert keys_match("10.1103/PhysRevLett.119.167801", member)
    assert not keys_match("https://doi.org/10.1103/PhysRevLett.119.167802", member)
    assert not keys_match(None, member)


def test_harvest_pdf_stem_uses_underscores_not_zip_style():
    doi = "https://doi.org/10.1002/adma.202107316"
    assert harvest_pdf_stem(doi) == "10.1002_adma.202107316"
    assert doi_compact_key(doi) == "10.1002adma.202107316"
    assert harvest_pdf_stem(doi) != zip_member_compact_key("ALS/10.1002adma.202107316.pdf")
    assert keys_match(doi, "ALS/10.1002adma.202107316.pdf")


def test_apply_match_to_paper_sets_path_and_als_provenance(tmp_path, monkeypatch):
    import match_als_zips as m

    monkeypatch.setattr(m, "REPO_ROOT", tmp_path)
    dest = tmp_path / "papers" / "rsoxs" / "2021" / "10.1002_adma.202107316.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"%PDF-1.4\n")
    paper = {
        "doi": "https://doi.org/10.1002/adma.202107316",
        "pdf_path": None,
        "ingestion": {"status": "skipped", "error": "no reliable OA PDF URL"},
        "provenance": [{"source": "als_pubtemp"}],
    }
    assert m.apply_match_to_paper(
        paper, dest, "ALS-test.zip", "ALS/10.1002adma.202107316.pdf"
    )
    assert paper["pdf_path"] == "papers/rsoxs/2021/10.1002_adma.202107316.pdf"
    assert paper["ingestion"]["status"] == "pending"
    assert paper["ingestion"]["error"] is None
    assert any(item.get("source") == "als_zip" for item in paper["provenance"])
    assert (
        m.apply_match_to_paper(
            paper, dest, "ALS-test.zip", "ALS/10.1002adma.202107316.pdf"
        )
        is False
    )
