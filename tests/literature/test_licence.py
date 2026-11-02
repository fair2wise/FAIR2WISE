"""T29 B2: licence and open-access metadata on every intake record (offline)."""

from __future__ import annotations

from pathlib import Path

from app.modules.literature.intake import prepare_local_pdf
from app.modules.literature.licence import (
    LicenceInfo, collect, crossref_licenses, from_openalex, licence_status, pdf_notice,
)
from app.modules.literature.openalex import paper_record
from app.modules.literature.store import FileDocumentStore
from tests.literature.lit_fakes import local_ctx, minimal_pdf_bytes, openalex_work
from tests.literature.test_intake import BODY, DOI, TITLE, Registry, texts


def test_openalex_fields_and_status():
    work = openalex_work(DOI, TITLE)
    work["primary_location"]["license"] = "cc-by"
    work["best_oa_location"] = {"license": "cc-by-nc", "source": {"display_name": "Open Venue"}}
    work["open_access"] = {"is_oa": True, "oa_status": "gold"}
    info = from_openalex(work)
    assert (info.license, info.is_oa, info.oa_status) == ("cc-by", True, "gold")
    assert (info.host_venue, info.host_venue_license) == ("Open Venue", "cc-by-nc")
    assert info.licence_status == "licensed" and info.sources == ["openalex"]


def test_nothing_known_is_explicit_unknown():
    info = from_openalex({"id": "x"})
    assert info.license == info.oa_status == info.host_venue_license == info.pdf_notice == "unknown"
    assert info.is_oa is None and info.licence_status == "unknown"
    assert licence_status(None) == "unknown" and licence_status({}) == "unknown"
    assert LicenceInfo().as_dict()["licence_status"] == "unknown"


def test_open_access_without_licence_is_not_licensed():
    info = from_openalex({"open_access": {"is_oa": True, "oa_status": "bronze"}})
    assert info.is_oa is True and info.licence_status == "unknown"


def test_crossref_and_pdf_notice():
    msg = {"license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}, {"URL": "https://x/y"},
                       {"URL": "https://x/y"}]}
    assert crossref_licenses(msg) == ["https://creativecommons.org/licenses/by/4.0/", "https://x/y"]
    assert collect(None, crossref=msg).licence_status == "licensed"
    assert "CC BY 4.0" in pdf_notice("Published under CC BY 4.0 by the authors")
    assert pdf_notice("(c) 2020 ACS. All rights reserved.").lower().endswith("all rights reserved")
    only = collect(None, pdf_text="Licensed under a Creative Commons Attribution license")
    assert only.licence_status == "notice_only" and only.sources == ["pdf"]
    assert collect(None, pdf_text="nothing here").pdf_notice == "unknown"


def test_paper_record_carries_licence():
    rec = paper_record(openalex_work(DOI, TITLE))
    assert rec["licence"]["licence_status"] == "unknown"


def _run(tmp_path: Path, work, text: str, **kw):
    store = FileDocumentStore(tmp_path / "s")
    path = tmp_path / "paper.pdf"
    path.write_bytes(minimal_pdf_bytes("p"))
    return prepare_local_pdf(local_ctx(), path, store=store, fetch=Registry({DOI: work}) if work else Registry({}),
                             text_extractor=texts({"paper.pdf": text}), apply=True, **kw)


def test_intake_record_carries_licence_fields(tmp_path):
    work = openalex_work(DOI, TITLE)
    work["primary_location"]["license"] = "cc-by"
    work["open_access"] = {"is_oa": True, "oa_status": "hybrid"}
    report = _run(tmp_path, work, BODY + " Creative Commons Attribution 4.0", crossref={
        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}]})
    assert report.status == "ready" and report.licence_status == "licensed"
    data = report.as_dict()["licence"]
    assert data["license"] == "cc-by" and data["oa_status"] == "hybrid"
    assert data["sources"] == ["openalex", "crossref", "pdf"]
    asset = next(iter(FileDocumentStore(tmp_path / "s").list_assets(local_ctx())))
    assert asset.metadata["licence_status"] == "licensed"


def test_intake_without_any_licence_is_unknown_and_failed_intake_still_has_field(tmp_path):
    report = _run(tmp_path, openalex_work(DOI, TITLE), BODY)
    assert report.status == "ready" and report.licence_status == "unknown"
    assert report.as_dict()["licence"]["license"] == "unknown"
    refused = _run(tmp_path / "r", None, BODY)
    assert refused.status != "ready" and refused.as_dict()["licence"]["licence_status"] == "unknown"
