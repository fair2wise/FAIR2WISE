"""T05 slice 3: local PDF intake, identity facts, already-ingested lookup and bibliography records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.modules.literature import bibliography as bib
from app.modules.literature.ingested import IngestedPapers, graph_ingested_keys, staged_keys
from app.modules.literature.intake import (
    extract_doi_candidates, filename_doi_candidates, prepare_local_pdf, pdf_first_pages_text,
    resolve_verified, title_matches_pdf, version_pair_candidate,
)
from app.modules.literature.legacy import LegacyIndex
from app.modules.literature.openalex import QUOTA_PAUSED, UNREACHABLE, OpenAlexNoData
from app.modules.literature.resolver_batch import Resolved
from app.modules.literature.store import FileDocumentStore
from tests.literature.lit_fakes import have_pdf_stack, local_ctx, make_paper_pdf, minimal_pdf_bytes, openalex_work

TITLE = "Resonant soft X-ray scattering of polymer blend morphology"
DOI = "10.1021/acs.macromol.0c01782"
BODY = f"{TITLE}\nA. Author, B. Author\nhttps://doi.org/{DOI}\nAbstract: we measured lamellar domains " * 2


class Registry:
    """Offline registry: ``fetch("works", {"filter": "doi:X"})``."""

    def __init__(self, works: Dict[str, Any]) -> None:
        self.works = {k.lower(): v for k, v in works.items()}
        self.asked = []

    def __call__(self, path: str, params: Dict[str, Any] = None, **_kw: Any) -> Dict[str, Any]:
        doi = (params or {}).get("filter", "").split("doi:", 1)[-1].lower()
        self.asked.append(doi)
        value = self.works.get(doi, "missing")
        if value == "missing":
            return {"results": []}
        if isinstance(value, OpenAlexNoData):
            return value
        return {"results": [value]}


def texts(mapping: Dict[str, str]):
    def extract(path: Path, pages: int) -> str:
        return mapping.get(Path(path).name, "")
    return extract


@pytest.fixture
def env(tmp_path):
    store = FileDocumentStore(tmp_path / "candidate-store")
    return store, LegacyIndex(store), tmp_path


def pdf(tmp_path: Path, name: str, payload: bytes = b"") -> Path:
    path = tmp_path / name
    path.write_bytes(payload or minimal_pdf_bytes(name))
    return path


def test_verified_doi_is_stored_with_identity_facts(env):
    store, index, tmp = env
    registry = Registry({DOI: openalex_work(DOI.upper(), TITLE, wid="W42")})
    path = pdf(tmp, "download (3).pdf")
    report = prepare_local_pdf(local_ctx(), path, store=store, index=index, fetch=registry,
                               text_extractor=texts({path.name: BODY}), apply=True)
    assert report.ok, report.detail
    ident = report.identity
    assert ident.verified_doi == DOI.upper() and ident.verification == "title_match"
    assert ident.canonical_work_id == "W42" and ident.source_version == "journal"
    assert ident.source_ref == f"sha256:{report.sha256}" and store.exists(local_ctx(), ident.source_ref)
    assert index.find_by_doi(DOI) == ident.source_ref
    assert "candidate-store" not in json.dumps(report.as_dict())  # no store path in the report


def test_dry_run_writes_nothing(env):
    store, index, tmp = env
    path = pdf(tmp, "a.pdf")
    report = prepare_local_pdf(local_ctx(), path, store=store, index=index,
                               fetch=Registry({DOI: openalex_work(DOI, TITLE)}),
                               text_extractor=texts({"a.pdf": BODY}))
    assert report.ok and "would store" in report.detail
    assert list(store.list_refs(local_ctx())) == [] and index.find_by_doi(DOI) is None


def test_doi_of_cited_work_is_a_title_mismatch(env):
    store, index, tmp = env
    cited = "10.1103/physrevb.99.1"
    text = f"{TITLE}\nSee also https://doi.org/{cited} for the theory. " * 2
    registry = Registry({cited: openalex_work(cited, "Completely different electronic structure theory")})
    path = pdf(tmp, "x.pdf")
    report = prepare_local_pdf(local_ctx(), path, store=store, index=index, fetch=registry,
                               text_extractor=texts({"x.pdf": text}), apply=True)
    assert report.status == "skipped:title_mismatch" and list(store.list_refs(local_ctx())) == []


def test_asserted_doi_mismatch_is_recorded_not_hidden(env):
    store, index, tmp = env
    asserted = "10.1021/nl2020526"
    registry = Registry({asserted: openalex_work(asserted, "Defining the nanostructured morphology")})
    path = pdf(tmp, "si.pdf")
    report = prepare_local_pdf(local_ctx(), path, store=store, index=index, fetch=registry, doi=asserted,
                               text_extractor=texts({"si.pdf": BODY}), apply=True)
    assert report.ok and report.identity_asserted
    assert report.identity.asserted_doi == asserted and report.identity.verified_doi == ""
    assert report.identity.verification == "asserted_registry_only"
    assert report.identity.doi_mismatch and report.warnings


def test_missing_doi(env):
    store, index, tmp = env
    path = pdf(tmp, "scan-0001.pdf")
    report = prepare_local_pdf(local_ctx(), path, store=store, fetch=Registry({}),
                               text_extractor=texts({path.name: "No identifiers in this text at all. " * 5}))
    assert report.status == "skipped:no_doi"
    nothing = prepare_local_pdf(local_ctx(), path, store=store, fetch=Registry({}), text_extractor=texts({}))
    assert nothing.status == "skipped:no_text"


def test_old_f2w_filename_recovers_doi_and_verifies_offline(env):
    store, index, tmp = env
    path = pdf(tmp, "10.1021_acs.macromol.0c01782.pdf")
    assert filename_doi_candidates(path.name)[0] == DOI
    text = f"{TITLE} " * 4 + f" doi {DOI} "
    report = prepare_local_pdf(local_ctx(), path, store=store, index=index, verify="filename",
                               text_extractor=texts({path.name: text}), apply=True)
    assert report.ok and report.identity.verification == "filename_match"
    assert report.work == {"doi": DOI, "metadata_source": "pdf_only"}
    # Offline, a DOI that only the filename names (not the text) is not enough.
    other = pdf(tmp, "10.1021_acs.other.1.pdf", minimal_pdf_bytes("other"))
    rep2 = prepare_local_pdf(local_ctx(), other, store=store, verify="filename",
                             text_extractor=texts({other.name: TITLE * 3}))
    assert rep2.status == "skipped:filename_mismatch"


def test_duplicate_pdf_is_refused_unless_forced(env):
    store, index, tmp = env
    registry = Registry({DOI: openalex_work(DOI, TITLE)})
    a = pdf(tmp, "a.pdf", minimal_pdf_bytes("same"))
    b = pdf(tmp, "b-copy.pdf", minimal_pdf_bytes("same"))
    extract = texts({"a.pdf": BODY, "b-copy.pdf": BODY})
    assert prepare_local_pdf(local_ctx(), a, store=store, index=index, fetch=registry, text_extractor=extract,
                             apply=True).ok
    dup = prepare_local_pdf(local_ctx(), b, store=store, index=index, fetch=registry, text_extractor=extract,
                            apply=True)
    assert dup.status == "skipped:duplicate" and dup.duplicate_of
    forced = prepare_local_pdf(local_ctx(), b, store=store, index=index, fetch=registry, text_extractor=extract,
                               apply=True, force=True)
    assert forced.ok and len(list(store.list_refs(local_ctx()))) == 1


def test_second_source_version_of_same_doi_is_kept(env):
    store, index, tmp = env
    registry = Registry({DOI: openalex_work(DOI, TITLE)})
    a, b = pdf(tmp, "am.pdf", minimal_pdf_bytes("accepted")), pdf(tmp, "vor.pdf", minimal_pdf_bytes("vor"))
    extract = texts({"am.pdf": BODY, "vor.pdf": BODY})
    first = prepare_local_pdf(local_ctx(), a, store=store, index=index, fetch=registry, text_extractor=extract,
                              apply=True)
    second = prepare_local_pdf(local_ctx(), b, store=store, index=index, fetch=registry, text_extractor=extract,
                               apply=True)
    assert second.ok and second.identity.prior_source_refs == [first.identity.source_ref]
    assert index.refs_for_doi(DOI) == [first.identity.source_ref, second.identity.source_ref]


def test_unavailable_provider_and_quota_pause_are_retryable(env):
    store, index, tmp = env
    path = pdf(tmp, "p.pdf")
    down = Registry({DOI: OpenAlexNoData(UNREACHABLE)})
    report = prepare_local_pdf(local_ctx(), path, store=store, fetch=down, text_extractor=texts({"p.pdf": BODY}))
    assert report.status == "skipped:openalex_unreachable" and report.retryable
    paused = Registry({DOI: OpenAlexNoData(QUOTA_PAUSED, resume_at=1.0)})
    report = prepare_local_pdf(local_ctx(), path, store=store, fetch=paused, text_extractor=texts({"p.pdf": BODY}))
    assert report.status == "skipped:openalex_quota_paused" and report.retryable
    # A quota pause stops asking about further candidates.
    two = f"{BODY} https://doi.org/10.1/second"
    work, status, tried = resolve_verified(extract_doi_candidates(two), two,
                                           fetch=Registry({DOI: OpenAlexNoData(QUOTA_PAUSED)}))
    assert status == "skipped:openalex_quota_paused" and len(tried) == 1


def test_giant_upload_and_disallowed_paths_are_refused_before_hashing(env):
    store, index, tmp = env
    big = pdf(tmp, "big.pdf", minimal_pdf_bytes("big", pad=50_000))
    report = prepare_local_pdf(local_ctx(), big, store=store, fetch=Registry({}), text_extractor=texts({}),
                               max_bytes=10_000)
    assert report.status == "skipped:too_large" and report.sha256 == ""

    def deny(path: Path) -> Path:
        raise ValueError("outside allowed roots")

    assert prepare_local_pdf(local_ctx(), big, store=store, allowed=deny, text_extractor=texts({})).status == \
        "skipped:not_allowed"
    html = pdf(tmp, "landing.pdf", b"<html>" + b"x" * 3000)
    assert prepare_local_pdf(local_ctx(), html, store=store, text_extractor=texts({})).status == "skipped:not_pdf"
    tiny = pdf(tmp, "tiny.pdf", b"%PDF-1.4\n%%EOF")
    tiny_report = prepare_local_pdf(local_ctx(), tiny, store=store, text_extractor=texts({}))
    assert tiny_report.status == "skipped:too_small"


def test_journal_preprint_candidate_pair_requires_review():
    journal = {"doi": "https://doi.org/10.1103/PhysRevLett.1",
               "title": "Soft X-ray scattering of block copolymers"}
    preprint = {"doi": "10.48550/arXiv.2101.00001", "title": "Soft x-ray scattering of block-copolymers"}
    pair = version_pair_candidate(journal, preprint)
    assert pair == {"relation": "candidate_version_of", "preprint_doi": "10.48550/arxiv.2101.00001",
                    "journal_doi": "10.1103/physrevlett.1", "title_similarity": pair["title_similarity"],
                    "requires_review": True}
    unrelated = {"doi": "10.48550/arXiv.2", "title": "Unrelated topic entirely"}
    assert version_pair_candidate(journal, unrelated) is None
    assert version_pair_candidate(journal, {"doi": "10.1/other", "title": journal["title"]}) is None


def test_doi_extraction_and_title_check():
    text = "doi:10.1039/C5TA01004G. Also (see 10.1002/(SICI)1097-0258(19980815)17:15)."
    assert extract_doi_candidates(text)[:2] == ["10.1039/C5TA01004G", "10.1039/C5TA01004G."]
    assert title_matches_pdf(TITLE, BODY) and not title_matches_pdf("A B C", BODY)
    assert filename_doi_candidates("ao-49-31-6102.pdf")[0] == "10.1364/AO.49.006102"
    assert "10.48550/arXiv.2209.13121" in filename_doi_candidates("CyRSoXS2209.13121.pdf")


@pytest.mark.skipif(not have_pdf_stack(), reason="PDF reader stack not installed")
def test_pdf_text_extractor_reads_first_pages(tmp_path):
    path = make_paper_pdf(tmp_path / "real.pdf", title=TITLE, doi=DOI,
                          sections=[("1. Introduction", "Lamellar domains were measured.")])
    text = pdf_first_pages_text(path)
    assert DOI in text and "Introduction" in text


# -- already ingested ----------------------------------------------------------------------------

def test_ingested_lookup_by_content_and_by_source_pipeline(tmp_path):
    sections = [{"publication_id": "https://doi.org/10.1/A"}, {"publication_id": "W77"}]
    publications = [{"id": "W77", "doi": "10.1/b", "openalex_id": "W77"}, {"id": "W88", "doi": "10.1/c"}]
    in_graph = graph_ingested_keys(sections, publications)
    assert in_graph == {"101a", "w77", "101b"}
    staging = tmp_path / "staged" / "pkg1"
    staging.mkdir(parents=True)
    (staging / "paper.json").write_text(json.dumps({"complete": True, "work": {"doi": "10.1/d"}}))
    (tmp_path / "staged" / "pkg2").mkdir()
    (tmp_path / "staged" / "pkg2" / "paper.json").write_text(json.dumps({"complete": False,
                                                                         "work": {"doi": "10.1/e"}}))
    staged = staged_keys(tmp_path / "staged")
    assert staged == {"101d"}
    papers = IngestedPapers(in_graph, staged, completed=[("abc", "parser:v1")])
    assert "graph" in papers.reason("10.1/b") and papers.reason("10.1/c") is None  # metadata-only node
    assert "staged" in papers.reason("doi:10.1/D")
    papers.record("10.1/c")
    assert "this run" in papers.reason("https://doi.org/10.1/c")
    by_pipeline = IngestedPapers(in_graph, doi_only=False, completed=[("abc", "parser:v1")])
    assert by_pipeline.reason("10.1/b", source_sha256="abc", pipeline="parser:v2") is None  # new revision
    assert by_pipeline.reason("10.1/b", source_sha256="abc", pipeline="parser:v1")


# -- bibliography --------------------------------------------------------------------------------

def test_bibliography_proposal_and_effective_publication():
    work = openalex_work("10.1/A", "Real Title", wid="W5")
    proposal = bib.propose_backfill("pub-1", Resolved("doi:10.1/a", "openalex", work, "2026-10-14T00:00:00+00:00"))
    assert proposal.status == "proposed" and proposal.record["title"] == "Real Title"
    assert proposal.record["metadata_source"] == "openalex" and proposal.link["predicate"] == bib.HAS_BIBLIOGRAPHY
    unchanged = bib.propose_backfill("pub-1", Resolved("k", "openalex", work, "t"),
                                     newest={"record_digest": proposal.digest})
    assert unchanged.status == "unchanged"
    assert bib.propose_backfill("pub-1", Resolved("k", "none", None, "t")).status == "no_record"
    cr = bib.normalise_record("crossref", {"title": ["T"], "issued": {"date-parts": [[2019]]},
                                           "author": [{"given": "A", "family": "B"}], "DOI": "10.1/X"})
    assert cr["year"] == 2019 and cr["authors"] == [{"name": "A B"}] and cr["doi"] == "10.1/x"
    with pytest.raises(ValueError):
        bib.normalise_record("mystery", {})
    entity = {"id": "pub-1", "name": "10.1/a", "properties": {"doi": "10.1/a"}}
    assert bib.needs_backfill(entity)
    assert bib.select_backfill_candidates([entity], {}) == [entity]
    assert bib.select_backfill_candidates([entity], {"pub-1": {}}) == []
    name, props, used = bib.effective_publication("10.1/a", {"doi": "10.1/a", "year": 2001}, proposal.record)
    assert used and name == "Real Title" and props["year"] == 2001  # own value wins
    assert props["bibliography_source"] == "openalex"
    rows = [{"predicate": bib.HAS_BIBLIOGRAPHY, "subjectId": "pub-1", "id": "e1",
             "properties": {"recorded_at": "2026-01-01"}},
            {"predicate": bib.HAS_BIBLIOGRAPHY, "subjectId": "pub-1", "id": "e2",
             "properties": json.dumps({"recorded_at": "2026-05-01"})}]
    assert bib.newest_bibliography_link(rows, "pub-1")["id"] == "e2"
    assert bib.author_names([{"name": "A"}, "B", {"display_name": "C"}, ""]) == ["A", "B", "C"]
