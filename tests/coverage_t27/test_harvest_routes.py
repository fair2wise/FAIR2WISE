"""``/v1/library/harvest`` routes on ``:local`` (T27f): runs, candidates, supplying a PDF and dismissing (fake probe)."""
from __future__ import annotations

import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api_v1"))

from _v1_support import build_als, build_local  # noqa: E402

from app.modules.library.harvest import Candidate, Harvester
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from tests.library.test_library_service import FakeProbe, pdf
from tests.security.security_fakes import local_settings

RUN = "ab" * 8
BASE = "/v1/library/harvest"


@pytest.fixture
def world(monkeypatch, tmp_path):
    lib = tmp_path / "papers"
    lib.mkdir()
    service = LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                             probe=FakeProbe(), breaker=QuotaBreaker())
    app = build_local(monkeypatch, tmp_path, library=service)
    harvester = app.app.state.f2w_harvest
    assert isinstance(harvester, Harvester)
    harvester.store.create_run(RUN, topic="rsoxs", category="rsoxs", schema=None, sources=["arxiv"], max_downloads=3)
    cand = Candidate(title="Paywalled study", source="arxiv", year=2020, doi="10.1/p",
                     landing_url="https://doi.org/10.1/p")
    cid = harvester.store.save_candidate(RUN, cand, status="paywalled")
    other = Candidate(title="Fetched study", source="arxiv", year=2021, doi="10.1/f")
    done = harvester.store.save_candidate(RUN, other, status="filed")
    return app, harvester, cid, done, lib


def _post_pdf(client, cid, data=None, name="mine.pdf"):
    return client.post(f"{BASE}/candidates/{cid}/pdf",
                       files={"file": (name, io.BytesIO(pdf("mine") if data is None else data), "application/pdf")})


def test_runs_lists_the_run_with_counts_per_status(world):
    app, _, _, _, _ = world
    body = app.client().get(f"{BASE}/runs").json()
    assert [r["id"] for r in body["runs"]] == [RUN]
    counts = body["runs"][0]["counts"]
    assert counts["paywalled"] == 1 and counts["filed"] == 1


def test_candidates_of_a_run_and_unknown_or_malformed_run_ids(world):
    app, _, cid, _, _ = world
    c = app.client()
    listed = c.get(f"{BASE}/runs/{RUN}/candidates").json()["candidates"]
    assert {x["id"]: x["status"] for x in listed}[cid] == "paywalled"
    assert c.get(f"{BASE}/runs/{'cd' * 8}/candidates").status_code == 404
    assert c.get(f"{BASE}/runs/NOT-HEX/candidates").status_code == 404


def test_supplying_a_pdf_files_it_into_the_runs_category(world):
    app, harvester, cid, _, lib = world
    resp = _post_pdf(app.client(), cid)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate"]["status"] == "filed" and body["duplicate"] is False
    relpath = body["paper"]["relpath"]
    assert relpath.startswith("rsoxs/") and (lib / relpath).is_file()
    assert harvester.store.candidate(cid)["status"] == "filed"
    assert str(lib) not in resp.text
    assert _post_pdf(app.client(), cid).status_code == 409  # no longer paywalled


def test_supplying_a_pdf_refuses_bad_ids_unknown_candidates_and_non_pdf(world):
    app, _, cid, done, _ = world
    c = app.client()
    assert _post_pdf(c, "zz").status_code == 404
    assert _post_pdf(c, "0" * 16).status_code == 404
    assert _post_pdf(c, done).status_code == 409
    assert _post_pdf(c, cid, b"MZ not a pdf").status_code == 415
    assert c.post(f"{BASE}/candidates/{cid}/pdf").status_code == 415  # multipart is required


def test_dismiss_marks_the_candidate_and_refuses_a_second_dismissal(world):
    app, harvester, cid, done, _ = world
    c = app.client()
    resp = c.post(f"{BASE}/candidates/{cid}/dismiss")
    assert resp.status_code == 200 and resp.json()["candidate"]["status"] == "dismissed"
    assert harvester.store.candidate(cid)["detail"] == "dismissed by hand"
    assert c.post(f"{BASE}/candidates/{cid}/dismiss").status_code == 409
    assert c.post(f"{BASE}/candidates/{done}/dismiss").status_code == 409
    assert c.post(f"{BASE}/candidates/xyz/dismiss").status_code == 404
    assert c.post(f"{BASE}/candidates/{'0' * 16}/dismiss").status_code == 404


def test_the_routes_fail_closed_without_a_harvester(world):
    app, _, _, _, _ = world
    app.app.state.f2w_harvest = None
    resp = app.client().get(f"{BASE}/runs")
    assert resp.status_code >= 400 and "harvester is not installed" in resp.text


def test_the_routes_are_absent_on_als(monkeypatch, tmp_path):
    als = build_als(monkeypatch, tmp_path)
    assert als.client("admin").get(f"{BASE}/runs").status_code == 404
