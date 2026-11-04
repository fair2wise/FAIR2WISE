"""harvest_rsoxs (T27f), part 2: retry, harvest, rescore, add-work, PubTemp merge, pending OA downloads and ``main``.

Searches, lookups and PDF downloads are replaced by fakes; files live under ``tmp_path`` (``REPO_ROOT`` is redirected).
"""
from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
import sys
from typing import Any

import pytest

from app.modules import dotenv_entry
from app.modules.commands import harvest_rsoxs as hv

from .harvest_fakes import GOOD_ABSTRACT, GOOD_TITLE, Downloads, OpenAlexWorld

PDF = b"%PDF-1.4 fake"
TITLES = ["RSoXS study of block copolymer morphology", "Polarized RSoXS of conjugated polymer P3HT films",
          "Resonant soft x-ray scattering from liquid crystal films", "RSoXS of organic solar cell blends and domains",
          "Soft X-ray scattering of vapor-deposited glass using RSoXS"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(hv.dl, "set_delay_floor", lambda seconds: None)
    sleeps: list[float] = []
    monkeypatch.setattr(hv.time, "sleep", sleeps.append)
    dest = tmp_path / "papers" / "rsoxs"
    dest.mkdir(parents=True)
    return types_ns(root=tmp_path, dest=dest, sleeps=sleeps)


def types_ns(**kw: Any):
    import types

    return types.SimpleNamespace(**kw)


def downloads(monkeypatch, table=None, default=None) -> Downloads:
    fake = Downloads(table, default)
    monkeypatch.setattr(hv, "download_pdf", fake)
    return fake


def row(title: str, *, doi: str | None = None, status: str = "skipped", pdf_path: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"doi": doi, "title": title, "abstract": GOOD_ABSTRACT, "publication_year": 2020, "pdf_path": pdf_path,
            "ingestion": {"status": status, "error": None}, **extra}


def save(dest: Path, papers: list[dict[str, Any]], **extra: Any) -> None:
    hv._save_manifest(dest / "manifest.json", {"corpus": "rsoxs", "papers": papers, **extra})


def load(dest: Path) -> dict[str, dict[str, Any]]:
    return {p["title"]: p for p in json.loads((dest / "manifest.json").read_text())["papers"]}


# -- retry --------------------------------------------------------------------------------------------------------


def test_retry_urls_gathers_listed_error_arxiv_and_europepmc_urls_once():
    paper = {"pdf_urls": ["https://arxiv.org/pdf/2101.00001", " "], "arxiv_id": "2101.00001",
             "doi": "https://doi.org/10.48550/arXiv.2102.00002",
             "ingestion": {"error": "no pdf at https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/x.pdf)."}}
    urls = hv._retry_urls(paper)
    assert urls.count("https://arxiv.org/pdf/2101.00001") == 1 and "https://arxiv.org/pdf/2102.00002" in urls
    assert any("PMC1234567" in u for u in urls) and any("europepmc" in u.lower() for u in urls)
    assert hv._retry_urls({"title": "nothing"}) == []


def test_retry_failed_pdfs_recovers_what_it_can_and_records_the_rest(repo, monkeypatch):
    fake = downloads(monkeypatch, {"https://arxiv.org/pdf/2101.00001": PDF})
    on_disk = repo.root / "papers" / "rsoxs" / "2019" / "have.pdf"
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(PDF)
    papers = [
        row("Recovered", doi="https://doi.org/10.1/r", arxiv_id="2101.00001", pdf_urls=["https://evil-first.example/x.pdf"]),
        row("Already here", doi="https://doi.org/10.1/h", pdf_path="papers/rsoxs/2019/have.pdf", pdf_urls=["https://arxiv.org/pdf/9"]),
        row("Pending is not retried", status="pending", arxiv_id="2101.9"),
        row("No urls", doi="https://doi.org/10.1/n"),
        row("Hopeless", doi="https://doi.org/10.1/x", arxiv_id="2101.55555", status="failed"),
    ]
    manifest = {"papers": papers}
    assert hv.retry_failed_pdfs(manifest, repo.dest, delay=0.25) == 1
    by = {p["title"]: p for p in papers}
    assert by["Recovered"]["pdf_path"] == "papers/rsoxs/2020/10.1_r.pdf" and by["Recovered"]["pdf_sha256"]
    assert by["Recovered"]["ingestion"] == {"status": "pending", "error": None} and (repo.root / by["Recovered"]["pdf_path"]).read_bytes() == PDF
    assert by["Hopeless"]["pdf_path"] is None and by["Hopeless"]["ingestion"]["error"].startswith("download failed: https://arxiv.org/pdf/2101.55555")
    assert by["No urls"]["ingestion"]["status"] == "skipped" and by["Pending is not retried"]["ingestion"]["status"] == "pending"
    assert "https://arxiv.org/pdf/9" not in fake.calls and repo.sleeps == [0.25]


# -- harvest ------------------------------------------------------------------------------------------------------


def work(i: int, *, urls: list[str] | None = None, doi: str | None = "auto") -> dict[str, Any]:
    return {"title": TITLES[i], "abstract": GOOD_ABSTRACT, "publication_year": 2020 + i % 2,
            "doi": f"https://doi.org/10.1/w{i}" if doi == "auto" else doi, "source": "openalex",
            "pdf_urls": [f"https://arxiv.org/pdf/2101.0000{i}"] if urls is None else urls}


@pytest.fixture
def searches(monkeypatch):
    found: dict[str, list[dict[str, Any]]] = {"authors": [], "arxiv": [], "oa": [], "arxiv2": [], "oa2": []}
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hv, "search_openalex_authors", lambda names, n, mailto: calls.append(("authors", mailto)) or found["authors"])
    arxiv = itertools.cycle(["arxiv", "arxiv2"])
    monkeypatch.setattr(hv, "search_arxiv", lambda queries, n, classifier=None: calls.append(("arxiv", n)) or found[next(arxiv)])
    oa = itertools.cycle(["oa", "oa2"])
    monkeypatch.setattr(hv, "search_openalex", lambda queries, n, mailto, **kw: calls.append(("openalex", kw)) or found[next(oa)])
    return found, calls


def test_harvest_downloads_skips_existing_and_records_failures(repo, monkeypatch, searches):
    found, calls = searches
    found["authors"] = [work(0)]
    found["arxiv"] = [work(1)]
    found["oa"] = [work(2, urls=[]), work(3)]
    found["oa2"] = [work(4)]
    existing = repo.dest / "2021" / "10.1_w1.pdf"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(PDF)
    fake = downloads(monkeypatch, {"https://arxiv.org/pdf/2101.00000": PDF, "https://arxiv.org/pdf/2101.00004": PDF})
    summary = hv.harvest(dest_root=repo.dest, max_pdfs=50, per_query=7, mailto="me@example.org", delay=0.5)
    assert summary["candidates"] == 5 and summary["already_on_disk"] == 1 and summary["failed"] == 1
    assert summary["downloaded_or_existing"] == 3 and summary["papers_in_manifest"] == 5
    papers = load(repo.dest)
    assert papers[TITLES[0]]["ingestion"]["status"] == "pending" and papers[TITLES[0]]["pdf_sha256"]
    assert papers[TITLES[1]]["pdf_path"] == "papers/rsoxs/2021/10.1_w1.pdf" and papers[TITLES[1]]["downloaded_at"]
    assert papers[TITLES[2]]["ingestion"]["error"] == "no reliable OA PDF URL" and papers[TITLES[2]]["pdf_path"] is None
    assert papers[TITLES[3]]["ingestion"]["error"].startswith("download failed: https://arxiv.org/pdf/2101.00003")
    assert papers[TITLES[3]]["arxiv_id"] == "2101.00003" and papers[TITLES[0]]["rsoxs_tier"] in {"A", "B"}
    assert sorted(fake.calls) == ["https://arxiv.org/pdf/2101.00000", "https://arxiv.org/pdf/2101.00003", "https://arxiv.org/pdf/2101.00004"]
    assert ("authors", "me@example.org") in calls and ("arxiv", 7) in calls and repo.sleeps  # delay between downloads
    assert any(c[0] == "openalex" and c[1] == {"oa_only": False, "require_pdf": False} for c in calls)


def test_harvest_stops_downloading_at_the_cap_but_still_records_the_rest(repo, monkeypatch, searches):
    found, _ = searches
    found["oa"] = [work(0), work(1)]
    fake = downloads(monkeypatch, default=PDF)
    summary = hv.harvest(dest_root=repo.dest, max_pdfs=1, per_query=5, mailto=None, delay=0)
    assert len(fake.calls) == 1 and summary["downloaded_or_existing"] == 1
    assert len(json.loads((repo.dest / "manifest.json").read_text())["papers"]) == 1  # the capped one is not recorded
    assert summary["roles"] and "als" in summary
    fake2 = downloads(monkeypatch, default=PDF)
    hv.harvest(dest_root=repo.dest, max_pdfs=0, per_query=5, mailto=None, delay=0)
    assert fake2.calls == []


def test_harvest_retries_failed_rows_first_and_saves_them(repo, monkeypatch, searches):
    save(repo.dest, [row("Old failure", doi="https://doi.org/10.1/old", status="failed", arxiv_id="2101.77777")])
    downloads(monkeypatch, {"https://arxiv.org/pdf/2101.77777": PDF})
    hv.harvest(dest_root=repo.dest, max_pdfs=5, per_query=5, mailto=None, delay=0)
    old = load(repo.dest)["Old failure"]
    assert old["pdf_path"].endswith("10.1_old.pdf") and old["ingestion"]["status"] == "pending"


def test_harvest_ignores_tier_c_candidates(repo, monkeypatch, searches):
    found, _ = searches
    found["oa"] = [{"title": "Cooking pasta", "abstract": "no science", "doi": "https://doi.org/10.1/c",
                    "pdf_urls": ["https://arxiv.org/pdf/2101.1"]}]
    monkeypatch.setattr(hv, "merge_candidates", lambda *groups: [g for grp in groups for g in grp])
    fake = downloads(monkeypatch, default=PDF)
    assert hv.harvest(dest_root=repo.dest, max_pdfs=5, per_query=5, mailto=None, delay=0)["papers_in_manifest"] == 0
    assert fake.calls == []


def test_harvest_names_a_doi_less_work_after_its_id(repo, monkeypatch, searches):
    found, _ = searches
    found["oa"] = [{**work(0, doi=None), "id": "https://openalex.org/W77"}]
    downloads(monkeypatch, default=PDF)
    hv.harvest(dest_root=repo.dest, max_pdfs=5, per_query=5, mailto=None, delay=0)
    assert (repo.dest / "2020" / "https:__openalex.org_W77.pdf").read_bytes() == PDF


# -- rescore ------------------------------------------------------------------------------------------------------


def test_rescore_updates_scores_collapses_duplicates_and_counts(repo, monkeypatch):
    pdf = repo.dest / "2020" / "a.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(PDF)
    monkeypatch.setattr(hv, "pdf_preview_text", lambda path: "Measured at the Advanced Light Source beamline 11.0.1.2")
    save(repo.dest, [
        row(GOOD_TITLE, doi="https://doi.org/10.1/a", pdf_path="papers/rsoxs/2020/a.pdf", rsoxs_score=1, rsoxs_emphasis="x"),
        row(GOOD_TITLE, doi="https://doi.org/10.48550/arXiv.2101.00001", versions=[{"arxiv_id": "2101.00001", "doi": "x"}]),
        row("Cooking pasta", doi="https://doi.org/10.1/c", abstract="nothing"),
    ])
    summary = hv.rescore_manifest(repo.dest)
    assert summary["papers"] == 2 and summary["als"] == 1 and summary["A"] + summary["B"] + summary["C"] == 2
    papers = load(repo.dest)
    kept = papers[GOOD_TITLE]
    assert kept["facility"] == "ALS" and kept["beamline"] == "11.0.1.2" and "rsoxs_score" not in kept
    assert kept["arxiv_id"] == "2101.00001" and len(kept["versions"]) >= 2
    assert sum(summary["roles"].values()) == 2


# -- Crossref / OpenAlex lookups and add-work -------------------------------------------------------------------


def crossref(monkeypatch, payload: Any):
    seen: list[tuple[str, dict[str, str]]] = []

    def fake(url: str, headers: dict[str, str] | None = None) -> Any:
        seen.append((url, headers or {}))
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(hv, "hardened_get_json", fake)
    return seen


def test_crossref_doi_lookup_maps_the_work(monkeypatch):
    seen = crossref(monkeypatch, {"message": {
        "DOI": "10.1/ABC", "title": ["Resonant soft X-ray", "scattering"], "abstract": "<jats:p>Probes <i>domains</i></jats:p>",
        "published-print": {"date-parts": [[2021, 3, 9]]},
        "link": [{"intended-application": "text-mining", "content-type": "application/pdf", "URL": "https://arxiv.org/pdf/2101.00001"}]}})
    work = hv._crossref_by_doi_or_title("10.1/ABC")
    assert seen[0][0] == "https://api.crossref.org/works/10.1/ABC" and "mailto" in seen[0][1]["User-Agent"]
    assert work["title"] == "Resonant soft X-ray scattering" and work["abstract"] == "Probes domains"
    assert work["doi"] == "https://doi.org/10.1/ABC" and work["publication_year"] == 2021 and work["publication_date"] == "2021-03-09"
    assert work["pdf_urls"] and work["source"] == "crossref"


def test_crossref_year_only_and_missing_dates(monkeypatch):
    crossref(monkeypatch, {"message": {"DOI": "10.1/y", "title": ["T"], "published-online": {"date-parts": [[2019]]}}})
    year_only = hv._crossref_by_doi_or_title("10.1/y")
    assert year_only["publication_date"] == "2019-01-01" and year_only["publication_year"] == 2019
    crossref(monkeypatch, {"message": {"DOI": "10.1/n", "title": ["T"]}})
    undated = hv._crossref_by_doi_or_title("10.1/n")
    assert undated["publication_year"] is None and undated["publication_date"] is None


def test_crossref_title_search_picks_the_best_item_or_gives_up(monkeypatch):
    items = [{"DOI": "10.1/other", "title": ["Unrelated battery paper"]}, {"DOI": "10.1/hit", "title": [GOOD_TITLE]}]
    seen = crossref(monkeypatch, {"message": {"items": items}})
    assert hv._crossref_by_doi_or_title(GOOD_TITLE)["doi"] == "https://doi.org/10.1/hit"
    assert seen[0][0].startswith("https://api.crossref.org/works?query.bibliographic=") and seen[0][0].endswith("&rows=5")
    crossref(monkeypatch, {"message": {"items": [{"DOI": "10.1/rs", "title": ["RSoXS of something else"]}]}})
    assert hv._crossref_by_doi_or_title("zzz qqq")["doi"] == "https://doi.org/10.1/rs"  # first item, RSoXS in title
    crossref(monkeypatch, {"message": {"items": [{"DOI": "10.1/c", "title": ["Cooking pasta"]}]}})
    assert hv._crossref_by_doi_or_title("zzz qqq") is None  # not RSoXS and not an explicit DOI
    crossref(monkeypatch, {"message": {}})
    assert hv._crossref_by_doi_or_title("zzz qqq") is None
    crossref(monkeypatch, {"message": {"DOI": "10.1/c", "title": ["Cooking pasta"]}})
    assert hv._crossref_by_doi_or_title("10.1/c")["title"] == "Cooking pasta"  # explicit DOI is kept


def test_crossref_failure_is_a_miss(monkeypatch, caplog):
    crossref(monkeypatch, RuntimeError("timeout"))
    with caplog.at_level(logging.WARNING, logger="harvest_rsoxs"):
        assert hv._crossref_by_doi_or_title("10.1/x") is None
    assert "Crossref lookup failed" in caplog.text
    assert hv._strip_jats(None) == "" and hv._strip_jats("<a>x</a>  <b>y</b>") == "x y"  # type: ignore[arg-type]


def test_openalex_lookup_prefers_crossref_then_doi_then_title(monkeypatch):
    world = OpenAlexWorld().install(monkeypatch)
    monkeypatch.setattr(hv, "_crossref_by_doi_or_title", lambda q: {"title": "from crossref"} if q == "cr" else None)
    assert hv._openalex_by_doi_or_title("cr", None) == {"title": "from crossref"}
    world.by_doi["https://doi.org/10.1/d"] = {"display_name": GOOD_TITLE, "doi": "https://doi.org/10.1/d"}
    assert hv._openalex_by_doi_or_title("10.1/d", "me@example.org")["title"] == GOOD_TITLE and world.config.email == "me@example.org"
    world.works["near"] = [{"display_name": "Other thing"}, {"display_name": "near match RSoXS", "abstract_inverted_index": {"rsoxs": [0]}}]
    assert hv._openalex_by_doi_or_title("near", None)["title"] == "near match RSoXS"
    world.works["first thing"] = [{"display_name": "Resonant soft x-ray scattering of A"}]
    assert hv._openalex_by_doi_or_title("first thing", None)["title"] == "Resonant soft x-ray scattering of A"
    world.works["nothing"] = []
    assert hv._openalex_by_doi_or_title("nothing", None) is None
    world.by_doi["https://doi.org/10.1/boom"] = RuntimeError("down")
    assert hv._openalex_by_doi_or_title("10.1/boom", None) is None
    world.by_doi["https://doi.org/10.1/missing"] = None
    world.works["10.1/missing"] = []
    assert hv._openalex_by_doi_or_title("10.1/missing", None) is None


def _known(**over: Any) -> dict[str, Any]:
    base = {"title": TITLES[0], "abstract": GOOD_ABSTRACT, "doi": "https://doi.org/10.1/k0", "publication_year": 2022,
            "source": "crossref", "pdf_urls": ["https://arxiv.org/pdf/2101.00010"]}
    return {**base, **over}


def test_ingest_known_works_downloads_records_and_skips(repo, monkeypatch, caplog):
    lookups = {
        "ok": _known(),
        "miss": None,
        "doi-title": _known(title="10.1/looks-like-a-doi", doi="https://doi.org/10.1/k1"),
        "nourl": _known(title=TITLES[1], doi="https://doi.org/10.1/k2", pdf_urls=[]),
        "bad": _known(title=TITLES[2], doi="https://doi.org/10.1/k3", pdf_urls=["https://arxiv.org/pdf/2101.99999"]),
        "have": _known(title=TITLES[3], doi="https://doi.org/10.1/k4"),
        "nodoi": _known(title=TITLES[4], doi=None, id="https://openalex.org/W5", pdf_urls=[]),
    }
    monkeypatch.setattr(hv, "_openalex_by_doi_or_title", lambda q, mailto: lookups[q])
    monkeypatch.setattr(hv, "_arxiv_pdf_urls_for_title", lambda title: ["https://arxiv.org/pdf/2101.00011"] if title == TITLES[0] else [])
    have = repo.dest / "2022" / "10.1_k4.pdf"
    have.parent.mkdir(parents=True)
    have.write_bytes(PDF)
    fake = downloads(monkeypatch, {"https://arxiv.org/pdf/2101.00010": PDF})
    with caplog.at_level(logging.WARNING, logger="harvest_rsoxs"):
        added = hv.ingest_known_works(repo.dest, list(lookups), mailto="m@example.org", delay=0.1)
    assert [r["title"] for r in added] == [TITLES[0], TITLES[1], TITLES[2], TITLES[3], TITLES[4]]
    by = {r["title"]: r for r in added}
    assert by[TITLES[0]]["pdf_sha256"] and by[TITLES[0]]["pdf_urls"][-1] == "https://arxiv.org/pdf/2101.00011"
    assert by[TITLES[1]]["ingestion"]["error"] == "no reliable OA PDF URL" and by[TITLES[1]]["pdf_path"] is None
    assert by[TITLES[2]]["ingestion"]["status"] == "failed" and by[TITLES[3]]["pdf_sha256"] and by[TITLES[4]]["pdf_path"] is None
    assert "No Crossref/OpenAlex hit for 'miss'" in caplog.text and "DOI as title" in caplog.text
    assert have.read_bytes() == PDF and fake.calls.count("https://arxiv.org/pdf/2101.00010") == 1 and repo.sleeps
    assert len(load(repo.dest)) == 5 and "facility" in load(repo.dest)[TITLES[0]]  # rescored after the merge


def test_arxiv_title_lookup_keeps_near_duplicate_titles_on_the_allowlist(monkeypatch):
    from .harvest_fakes import ArxivResult, ArxivWorld

    world = ArxivWorld({'ti:"Resonant soft X-ray scattering of polymer blends"': [
        ArxivResult("1", title=GOOD_TITLE), ArxivResult("2", title="Unrelated"), ArxivResult("3", title=GOOD_TITLE, pdf_url="https://evil.example/x.pdf"),
        ArxivResult("4", title=GOOD_TITLE, pdf_url=None)]}).install(monkeypatch)
    assert hv._arxiv_pdf_urls_for_title(GOOD_TITLE + ": a subtitle") == ["https://arxiv.org/pdf/1"]
    assert hv._arxiv_pdf_urls_for_title("short") == [] and hv._arxiv_pdf_urls_for_title("") == []
    world.table['ti:"Another long enough title"'] = RuntimeError("HTTP 500")
    assert hv._arxiv_pdf_urls_for_title("Another long enough title") == []


# -- PubTemp merge ------------------------------------------------------------------------------------------------


def record(text: str, section: str = "Refereed Journal Articles") -> dict[str, Any]:
    parsed = hv.parse_pubtemp_plain(text, section)
    assert parsed is not None
    return parsed


def test_import_pubtemp_merges_identity_doi_arxiv_year_and_versions(repo, monkeypatch, tmp_path):
    have = repo.dest / "2021" / "have.pdf"
    have.parent.mkdir()
    have.write_bytes(PDF)
    save(repo.dest, [
        row(GOOD_TITLE, doi="https://doi.org/10.48550/arXiv.2101.00001", authors=["Doe J"], versions=[]),
        row(TITLES[1], doi=None, publication_year=None, publication_date=None),
        row(TITLES[2], doi="https://doi.org/10.1/have", pdf_path="papers/rsoxs/2021/have.pdf"),
    ])
    records = [
        record(f'Doe J, "{GOOD_TITLE}", J. Chem. (2021) (doi: 10.1/journal-version) PMID: 123 arXiv:2101.00001 https://arxiv.org/pdf/2101.00001'),
        record(f'Roe R, "{TITLES[1]}", Nature (2019) (doi: 10.1/second)'),
        record(f'Poe P, "{TITLES[2]}", Science (2020) (doi: 10.1/have)'),
        record('Moe M, "A brand new RSoXS paper about nothing else", Nature (2018)', "Theses"),
    ]
    monkeypatch.setattr(hv, "parse_pubtemp_rtf", lambda path: records)
    outside = tmp_path.parent / "outside.rtf"  # not under REPO_ROOT, so the manifest keeps the full path
    summary = hv.import_pubtemp(repo.dest, outside, export_date="2026-01-02")
    assert (summary["parsed_count"], summary["matched_existing"], summary["new_works"]) == (4, 3, 1)
    assert summary["included"] == 4 and summary["already_had_pdf"] == 1 and summary["newly_queued"] == 3
    assert summary["kind_counts"]["journal"] == 3 and summary["kind_counts"]["thesis"] == 1
    manifest = json.loads((repo.dest / "manifest.json").read_text())
    assert manifest["pubtemp_import"]["file"] == str(outside) and manifest["pubtemp_import"]["export_date"] == "2026-01-02"
    papers = {p["title"]: p for p in manifest["papers"]}
    first = papers[GOOD_TITLE]
    assert first["doi"] == "https://doi.org/10.1/journal-version" and first["arxiv_id"] == "2101.00001" and first["pmid"] == "123"
    assert any(v.get("doi") == "https://doi.org/10.1/journal-version" for v in first["versions"]) and first["selection"] == {"included": True}
    assert first["authors"] == ["Doe J"] and first["provenance"][0]["source"] == "als_pubtemp"
    second = papers[TITLES[1]]
    assert second["doi"] == "https://doi.org/10.1/second" and second["publication_year"] == 2019 and second["publication_date"] == "2019-01-01"
    assert papers["A brand new RSoXS paper about nothing else"]["source"] == "pubtemp"


def test_works_same_identity_uses_doi_arxiv_versions_and_title_with_authors():
    base = {"doi": "https://doi.org/10.1/a", "title": GOOD_TITLE, "authors": ["Doe, J"]}
    assert hv.works_same_identity(base, {**base, "doi": "10.1/A".join(["https://doi.org/", ""])}) is True
    assert hv.works_same_identity(base, {**base, "doi": "https://doi.org/10.1/b"}) is False
    pre = {"doi": "https://doi.org/10.48550/arXiv.2101.1", "arxiv_id": "2101.1v2", "title": "other", "authors": []}
    assert hv.works_same_identity(pre, {"doi": "https://doi.org/10.1/z", "arxiv_id": "2101.1", "title": "x"}) is True
    jb = "https://doi.org/10.1/b"
    assert hv.works_same_identity({**pre, "versions": [{"doi": jb}]}, {"doi": jb, "title": "zz"}) is True
    assert hv.works_same_identity({"doi": jb, "title": "zz"}, {**pre, "versions": [{"doi": jb}]}) is True
    assert hv.works_same_identity({"doi": "https://doi.org/10.1/b", "title": "zz"}, {**base, "versions": [{"arxiv_id": "9.9"}], "arxiv_id": None}) is False
    assert hv.works_same_identity({"doi": "https://doi.org/10.1/j", "title": "x", "versions": [{"arxiv_id": "5.5"}]},
                                  {"doi": "https://doi.org/10.48550/arXiv.5.5", "arxiv_id": "5.5", "title": "y"}) is True
    assert hv.works_same_identity({"doi": "https://doi.org/10.48550/arXiv.5.5", "arxiv_id": "5.5", "title": "y"},
                                  {"doi": "https://doi.org/10.1/j", "title": "x", "versions": [{"arxiv_id": "5.5"}]}) is True
    assert hv.works_same_identity({"title": GOOD_TITLE, "authors": ["Smith, A"]}, {"title": GOOD_TITLE, "authors": ["Jones, B"]}) is False
    assert hv.works_same_identity({"title": GOOD_TITLE, "authors": "Doe, J"}, {"title": GOOD_TITLE, "authors": ["Doe, J"]}) is True
    assert hv.works_same_identity({"title": GOOD_TITLE}, {"title": "Totally different"}) is False


# -- pending OA downloads -----------------------------------------------------------------------------------------


def test_small_pending_helpers():
    cache = {"10.1/a": (["u1"], ["s2"])}
    assert hv._s2_from_cache("https://doi.org/10.1/A", cache) == (["u1"], ["s2"]) and hv._s2_from_cache("10.1/zz", cache) == ([], [])
    assert hv._s2_from_cache(None, cache) == ([], []) and hv._s2_from_cache("10.1/a", None) == ([], [])
    urls: list[str] = ["https://arxiv.org/pdf/1"]
    sources: list[str] = []
    hv._append_trusted_urls(urls, sources, ["https://arxiv.org/pdf/1", "https://arxiv.org/pdf/2", ""], ["osti"])
    assert urls == ["https://arxiv.org/pdf/1", "https://arxiv.org/pdf/2"] and sources == ["osti"]
    hv._append_trusted_urls(urls, sources, ["https://arxiv.org/pdf/2"], ["dup"])
    assert sources == ["osti"]
    assert hv._dedupe_sources(["a", " ", "a", None, "b"]) == ["a", "b"]  # type: ignore[list-item]
    paper: dict[str, Any] = {}
    hv._record_oa_resolution(paper, ["u"], ["one"])
    hv._record_oa_resolution(paper, ["u"], ["one", "two"])
    assert paper["oa_source"] == ["one", "two"] and paper["pdf_urls"] == ["u"] and len(paper["provenance"]) == 2
    bare: dict[str, Any] = {}
    hv._record_oa_resolution(bare, [], [])
    assert bare == {"pdf_urls": []}


def test_missing_pdf_dois_lists_included_works_without_a_file_once(repo):
    (repo.root / "papers").mkdir(exist_ok=True)
    (repo.root / "papers" / "x.pdf").write_bytes(PDF)
    papers = [{"selection": {"included": True}, "doi": "https://doi.org/10.1/a"}, {"selection": {"included": True}, "doi": "10.1/A"},
              {"selection": {"included": True}, "doi": "https://doi.org/10.1/b", "pdf_path": "papers/x.pdf"},
              {"selection": {"included": False}, "doi": "10.1/c"}, {"selection": {"included": True}, "doi": None}]
    assert hv._missing_pdf_dois(papers) == ["10.1/a"]


def test_openalex_oa_lookup_builds_the_request_and_tolerates_failures(monkeypatch):
    seen: list[tuple[str, dict[str, str]]] = []
    payload: Any = {"best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2101.00001"}}
    monkeypatch.setattr(hv, "hardened_get_json", lambda url, headers=None: seen.append((url, headers or {})) or payload)
    urls, arxiv_id, _ = hv._openalex_oa_urls_for_doi("10.1/a b", "researcher@lbl.gov")
    assert seen[0][0] == "https://api.openalex.org/works/doi:10.1%2Fa%20b?mailto=researcher%40lbl.gov"
    assert "mailto:researcher@lbl.gov" in seen[0][1]["User-Agent"] and urls and arxiv_id == "2101.00001"
    hv._openalex_oa_urls_for_doi("10.1/a", "not-an-email")
    assert "?" not in seen[1][0] and "mailto" not in seen[1][1]["User-Agent"]
    assert hv._openalex_oa_urls_for_doi("not a doi", None) == ([], None, [])
    payload = ["not", "a", "dict"]
    assert hv._openalex_oa_urls_for_doi("10.1/a", None) == ([], None, [])

    def boom(url, headers=None):
        raise RuntimeError("down")

    monkeypatch.setattr(hv, "hardened_get_json", boom)
    assert hv._openalex_oa_urls_for_doi("10.1/a", None) == ([], None, [])


def test_resolve_oa_pdfs_tries_each_resolver_only_until_it_has_a_url(monkeypatch):
    called: list[str] = []

    def stub(name: str, urls: list[str]):
        def fn(*args: Any, **kw: Any) -> tuple[list[str], list[str]]:
            called.append(name)
            return urls, [name] if urls else []

        return fn

    monkeypatch.setattr(hv, "_openalex_oa_urls_for_doi", lambda doi, mailto: ([], None, []))
    for name, urls in {"fetch_unpaywall_oa_pdfs": [], "fetch_osti_oa_pdfs": [], "fetch_core_oa_pdfs": [],
                       "fetch_europepmc_search_oa_pdfs": [], "fetch_crossref_tdm_pdfs": ["https://arxiv.org/pdf/2101.00009"]}.items():
        monkeypatch.setattr(hv.dl, name, stub(name, urls))
    paper: dict[str, Any] = {"doi": "https://doi.org/10.1/a"}
    urls, sources = hv._resolve_oa_pdfs(paper, "me@example.org")
    assert urls == ["https://arxiv.org/pdf/2101.00009"] and sources == ["fetch_crossref_tdm_pdfs"]
    assert called == ["fetch_unpaywall_oa_pdfs", "fetch_osti_oa_pdfs", "fetch_core_oa_pdfs", "fetch_europepmc_search_oa_pdfs",
                      "fetch_crossref_tdm_pdfs"]
    assert paper["pdf_urls"] == urls and paper["provenance"][0]["kind"] == "oa_resolution"
    called.clear()
    assert hv._resolve_oa_pdfs({"doi": "https://doi.org/10.1/a"}, None)[0] == ["https://arxiv.org/pdf/2101.00009"]
    assert "fetch_unpaywall_oa_pdfs" not in called  # no contact address, no Unpaywall


def test_resolve_oa_pdfs_merges_cache_openalex_and_arxiv_ids(monkeypatch):
    monkeypatch.setattr(hv, "_openalex_oa_urls_for_doi", lambda doi, mailto: (["https://arxiv.org/pdf/2101.00020"], "2101.00020", ["openalex"]))
    cache = {"10.1/a": (["https://arxiv.org/pdf/2101.00021"], ["semantic-scholar"])}
    paper: dict[str, Any] = {"doi": "https://doi.org/10.1/a", "pdf_urls": ["https://arxiv.org/pdf/2101.00022"]}
    urls, sources = hv._resolve_oa_pdfs(paper, None, s2_cache=cache)
    assert {u.rsplit("/", 1)[-1] for u in urls} >= {"2101.00020", "2101.00021", "2101.00022"}
    assert sources == ["semantic-scholar", "openalex"] and paper["arxiv_id"] == "2101.00020"
    only_arxiv = hv._resolve_oa_pdfs({"arxiv_id": "2101.5"}, None)
    assert only_arxiv[0] == ["https://arxiv.org/pdf/2101.5"] and only_arxiv[1] == []


def included(title: str, doi: str | None, **extra: Any) -> dict[str, Any]:
    return row(title, doi=doi, selection={"included": True}, **extra)


def test_harvest_pending_downloads_skips_and_fails(repo, monkeypatch, caplog):
    papers = [included(TITLES[0], "https://doi.org/10.1/p0"), included(TITLES[1], "https://doi.org/10.1/p1"),
              included(TITLES[2], "https://doi.org/10.1/p2"), row(TITLES[3], doi="https://doi.org/10.1/p3"),
              included(TITLES[4], None, pdf_path="papers/rsoxs/have.pdf")]
    (repo.dest / "have.pdf").write_bytes(PDF)
    save(repo.dest, papers)
    batch: list[list[str]] = []
    monkeypatch.setattr(hv.dl, "fetch_semantic_scholar_batch_oa_pdfs", lambda dois: batch.append(dois) or {"10.1/p0": (["u"], ["s2"])})
    monkeypatch.setattr(hv.dl, "host_delay", lambda host: 2.0)
    resolved = {TITLES[0]: (["https://arxiv.org/pdf/2101.00030"], ["s2"]), TITLES[1]: ([], []),
                TITLES[2]: (["https://arxiv.org/pdf/2101.00031"], [])}
    monkeypatch.setattr(hv, "_resolve_oa_pdfs", lambda paper, mailto, s2_cache=None: resolved[paper["title"]])
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    fake = downloads(monkeypatch, {"https://arxiv.org/pdf/2101.00030": PDF})
    with caplog.at_level(logging.INFO, logger="harvest_rsoxs"):
        summary = hv.harvest_pending_pdfs(repo.dest, mailto="m@example.org", delay=0.5)
    assert (summary["attempted"], summary["downloaded"], summary["skipped"], summary["failed"]) == (3, 1, 1, 1)
    assert sorted(batch[0]) == ["10.1/p0", "10.1/p1", "10.1/p2"] and "CORE_API_KEY not set" in caplog.text
    by = load(repo.dest)
    assert by[TITLES[0]]["pdf_path"].endswith("10.1_p0.pdf") and by[TITLES[0]]["ingestion"]["status"] == "pending"
    assert by[TITLES[1]]["ingestion"]["error"] == "no reliable OA PDF URL"
    assert by[TITLES[2]]["ingestion"]["error"].startswith("download failed: https://arxiv.org/pdf/2101.00031")
    assert sorted(fake.calls) == ["https://arxiv.org/pdf/2101.00030", "https://arxiv.org/pdf/2101.00031"]
    assert sorted(repo.sleeps) == [0.5, 2.0, 2.0]  # the skipped row waits `delay`; downloads wait for the host floor


def test_harvest_pending_limit_and_periodic_save(repo, monkeypatch):
    save(repo.dest, [included(f"Resonant paper {i} on RSoXS number {i}", f"https://doi.org/10.1/m{i}") for i in range(30)])
    monkeypatch.setattr(hv.dl, "fetch_semantic_scholar_batch_oa_pdfs", lambda dois: {})
    monkeypatch.setattr(hv, "_resolve_oa_pdfs", lambda paper, mailto, s2_cache=None: (["https://arxiv.org/pdf/2101.1"], []))
    downloads(monkeypatch)  # every download fails
    monkeypatch.setenv("CORE_API_KEY", "key-from-env")
    saves: list[int] = []
    real_save = hv._save_manifest
    monkeypatch.setattr(hv, "_save_manifest", lambda path, data: saves.append(1) or real_save(path, data))
    summary = hv.harvest_pending_pdfs(repo.dest, delay=0, limit=26)
    assert summary["attempted"] == 26 and summary["failed"] == 26 and len(saves) == 2  # at 25 attempts, and the final save
    assert hv.harvest_pending_pdfs(repo.dest, delay=0, limit=0)["attempted"] == 0


# -- command line -------------------------------------------------------------------------------------------------


@pytest.fixture
def cli(repo, monkeypatch):
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(dotenv_entry, "load_entrypoint_dotenv", lambda anchor: calls.append(("dotenv", None)))
    monkeypatch.setattr(hv.dl, "set_delay_floor", lambda seconds: calls.append(("floor", seconds)))
    monkeypatch.setattr(hv, "import_pubtemp", lambda dest, path: calls.append(("pubtemp", (dest, path))) or {"n": 1})
    monkeypatch.setattr(hv, "harvest_pending_pdfs", lambda dest, **kw: calls.append(("pending", (dest, kw))) or {"n": 2})
    monkeypatch.setattr(hv, "ingest_known_works", lambda dest, queries, **kw: calls.append(("add", (dest, queries, kw))) or [])
    monkeypatch.setattr(hv, "rescore_manifest", lambda dest: calls.append(("rescore", dest)) or {})
    monkeypatch.setattr(hv, "harvest", lambda **kw: calls.append(("harvest", kw)) or {})
    monkeypatch.delenv("OPENALEX_EMAIL", raising=False)

    def run(*argv: str) -> list[tuple[str, Any]]:
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["f2w-harvest", "--dest", str(repo.dest), *argv])
        hv.main()
        return list(calls)

    return run


def test_parse_args_defaults_and_the_resolve_oa_alias(monkeypatch):
    monkeypatch.setenv("OPENALEX_EMAIL", "env@example.org")
    args = hv.parse_args([])
    assert (args.max_pdfs, args.per_query, args.delay, args.mailto, args.add_work) == (200, 50, 1.0, "env@example.org", [])
    assert args.harvest_pending is False and args.import_pubtemp is None and args.harvest_limit is None
    assert hv.parse_args(["--resolve-oa"]).harvest_pending is True
    assert hv.parse_args(["--import-pubtemp"]).import_pubtemp == str(hv.PUBTEMP_DEFAULT)
    assert hv.parse_args(["--add-work", "a", "--add-work", "b"]).add_work == ["a", "b"]
    with pytest.raises(SystemExit) as stop:
        hv.parse_args(["--max-pdfs", "many"])
    assert stop.value.code == 2


def test_main_default_rescores_then_harvests_with_the_parsed_limits(cli, repo):
    calls = cli("--max-pdfs", "9", "--per-query", "4", "--delay", "0.2", "--mailto", "m@example.org")
    assert [c[0] for c in calls] == ["dotenv", "floor", "rescore", "harvest"]
    assert calls[1][1] == 0.2
    assert calls[3][1] == {"dest_root": repo.dest, "max_pdfs": 9, "per_query": 4, "mailto": "m@example.org", "delay": 0.2}


def test_main_rescore_only_add_work_pending_and_pubtemp_paths(cli, repo, tmp_path):
    assert [c[0] for c in cli("--rescore")] == ["dotenv", "floor", "rescore"]
    added = cli("--add-work", "10.1/a", "--add-work", "A title", "--mailto", "m@example.org", "--delay", "0")
    assert added[-1] == ("add", (repo.dest, ["10.1/a", "A title"], {"mailto": "m@example.org", "delay": 0.0}))
    pending = cli("--harvest-pending", "--harvest-limit", "3")
    assert pending[-1][0] == "pending" and pending[-1][1][1]["limit"] == 3 and "harvest" not in [c[0] for c in pending]
    rtf = tmp_path / "x.rtf"
    only = cli("--import-pubtemp", str(rtf))
    assert [c[0] for c in only] == ["dotenv", "floor", "pubtemp"] and only[-1][1] == (repo.dest, rtf)
    both = cli("--import-pubtemp", str(rtf), "--resolve-oa")
    assert [c[0] for c in both][-2:] == ["pubtemp", "pending"]


def test_main_still_runs_when_the_dotenv_helper_fails(cli, monkeypatch):
    def broken(anchor):
        raise OSError("unreadable .env")

    monkeypatch.setattr(dotenv_entry, "load_entrypoint_dotenv", broken)
    assert [c[0] for c in cli("--rescore")] == ["floor", "rescore"]
