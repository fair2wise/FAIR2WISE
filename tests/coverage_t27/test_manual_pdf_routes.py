"""The manual PDF flow over HTTP (``:local``): the 200 ``filed`` contract, confirm, ``to=``, undo and the batch drop."""
from __future__ import annotations

import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api_v1"))

from _v1_support import build_local  # noqa: E402

from app.modules.library.harvest import Candidate, Harvester
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from tests.library.test_library_service import pdf
from tests.library.test_manual_pdf import DOI_P, DOI_Q, TITLE_P, TITLE_Q, ContentProbe
from tests.security.security_fakes import local_settings

RUN = "ab" * 8
BASE = "/v1/library/harvest"
CAP = 20_000


@pytest.fixture
def world(monkeypatch, tmp_path):
    lib = tmp_path / "papers"
    lib.mkdir()
    probe = ContentProbe()
    service = LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(), probe=probe,
                             breaker=QuotaBreaker(), max_upload_bytes=CAP)
    app = build_local(monkeypatch, tmp_path, library=service)
    harvester = app.app.state.f2w_harvest
    assert isinstance(harvester, Harvester)
    harvester.store.create_run(RUN, topic="rsoxs", category="rsoxs", schema=None, sources=["arxiv"], max_downloads=3)
    ids = {}
    for name, title, doi, status in (("p", TITLE_P, DOI_P, "blocked"), ("q", TITLE_Q, DOI_Q, "failed")):
        ids[name] = harvester.store.save_candidate(RUN, Candidate(title=title, source="openalex", year=2020, doi=doi),
                                                   status=status)
    return app, harvester, probe, ids, lib


def _post(client, path, data, name="mine.pdf"):
    return client.post(f"{BASE}{path}", files={"file": (name, io.BytesIO(data), "application/pdf")})


def _strong(probe, doi, title, tag):
    return probe.add(pdf(tag), title=title, page_text=f"{title}\nhttps://doi.org/{doi}\nAbstract")


def test_strong_files_without_a_click_and_undo_reverses_it(world):
    app, harvester, probe, ids, lib = world
    c = app.client()
    body = _post(c, f"/candidates/{ids['p']}/pdf", _strong(probe, DOI_P, TITLE_P, "p")).json()
    assert body["filed"] is True and body["verdict"]["kind"] == "strong"
    assert body["candidate"]["provenance"] == "user_supplied" and body["candidate"]["supplied_by"] == "local-operator"
    assert [p for p in lib.rglob("*.pdf")]
    listed = {x["id"]: x for x in c.get(f"{BASE}/runs/{RUN}/candidates").json()["candidates"]}
    assert listed[ids["p"]]["status"] == "filed" and listed[ids["p"]]["provenance"] == "user_supplied"
    undone = c.post(f"{BASE}/candidates/{ids['p']}/undo")
    assert undone.status_code == 200 and undone.json()["candidate"]["status"] == "blocked"
    assert not [p for p in lib.rglob("*.pdf")] and str(lib) not in undone.text
    assert c.post(f"{BASE}/candidates/{ids['p']}/undo").status_code == 409
    assert c.post(f"{BASE}/candidates/{'0' * 16}/undo").status_code == 404
    assert c.post(f"{BASE}/candidates/zz/undo").status_code == 404


def test_a_mismatch_answers_both_and_to_files_it_under_the_other_candidate(world):
    app, harvester, probe, ids, lib = world
    c = app.client()
    data = _strong(probe, DOI_Q, TITLE_Q, "q")
    ask = _post(c, f"/candidates/{ids['p']}/pdf", data).json()
    assert ask["filed"] is False and ask["needs_confirmation"] is True and ask["verdict"]["kind"] == "mismatch"
    assert ask["other_candidate"]["id"] == ids["q"] and not [p for p in lib.rglob("*.pdf")]
    moved = c.post(f"{BASE}/candidates/{ids['p']}/pdf?to={ids['q']}",
                   files={"file": ("q.pdf", io.BytesIO(data), "application/pdf")}).json()
    assert moved["filed"] is True and moved["candidate"]["id"] == ids["q"]
    bad = c.post(f"{BASE}/candidates/{ids['p']}/pdf?to=NOT-HEX", files={"file": ("q.pdf", io.BytesIO(data), "application/pdf")})
    assert bad.status_code == 404


def test_run_drop_strong_weak_unmatched_and_per_file_limits(world):
    app, harvester, probe, ids, lib = world
    c = app.client()
    got = _post(c, f"/runs/{RUN}/pdf", _strong(probe, DOI_Q, TITLE_Q, "q")).json()
    assert got["filed"] is True and got["candidate"]["id"] == ids["q"]
    similar = probe.add(pdf("sim"), title=TITLE_P + " (revised)", page_text=TITLE_P + " revised\nno doi here")
    weak = _post(c, f"/runs/{RUN}/pdf", similar).json()
    assert weak["filed"] is False and weak["needs_confirmation"] is True and weak["candidate"]["id"] == ids["p"]
    assert harvester.store.candidate(ids["p"])["status"] == "blocked"
    confirmed = c.post(f"{BASE}/candidates/{ids['p']}/pdf?confirm=true",
                       files={"file": ("s.pdf", io.BytesIO(similar), "application/pdf")}).json()
    assert confirmed["filed"] is True and confirmed["candidate"]["provenance"] == "user_supplied_confirmed"
    before = sorted(lib.rglob("*.pdf"))
    unmatched = probe.add(pdf("none"), title="Nothing alike", page_text="Nothing alike at all")
    assert _post(c, f"/runs/{RUN}/pdf", unmatched).json() == {"filed": False, "unmatched": True}
    assert sorted(lib.rglob("*.pdf")) == before
    assert _post(c, f"/runs/{RUN}/pdf", b"%PDF-" + b"x" * (CAP + 1)).status_code == 413
    assert _post(c, f"/runs/{RUN}/pdf", b"MZ not a pdf").status_code == 415
    assert _post(c, f"/runs/{'cd' * 8}/pdf", unmatched).status_code == 404
    assert _post(c, "/runs/NOT-HEX/pdf", unmatched).status_code == 404
    assert c.post(f"{BASE}/runs/{RUN}/pdf").status_code == 415   # multipart is required
