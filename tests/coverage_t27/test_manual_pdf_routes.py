"""The manual PDF flow over HTTP (``:local``): the 200 ``filed`` contract, confirm, ``to=``, undo and the batch drop."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api_v1"))

from _v1_support import build_local  # noqa: E402

from app.modules.jobs.keys import OperationInputs
from app.modules.library.harvest import Candidate, Harvester
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from tests.library.test_library_service import pdf
from tests.library.test_manual_pdf import DOI_P, DOI_Q, TITLE_P, TITLE_Q, ContentProbe
from tests.security.security_fakes import local_settings

# The id a real run has: its job id (a bare hex id was once assumed, and the routes answered 404 for every real run).
RUN = OperationInputs(corpus_scope=("rsoxs",), params={"topic": "rsoxs"}).job_identity("library_harvest")
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


# -- the UI's fixtures are the contract its client is tested against ---------------------------------------

FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "src" / "app" / "components" / "__fixtures__"


def _shape(value):
    """The keys of a JSON value, recursively, with the type of every leaf (``None`` leaves match any type)."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return None if value is None else type(value).__name__


def _same_keys(real, pinned, path="$"):
    if isinstance(real, dict) and isinstance(pinned, dict):
        assert set(real) == set(pinned), (path, sorted(real), sorted(pinned))
        for key in real:
            _same_keys(real[key], pinned[key], f"{path}.{key}")
    elif isinstance(real, list) and isinstance(pinned, list):
        if real and pinned:
            _same_keys(real[0], pinned[0], path + "[0]")
    elif real is not None and pinned is not None:
        assert real == pinned, (path, real, pinned)


def test_the_ui_fixtures_pin_the_real_response_shapes(world):
    import json

    app, harvester, probe, ids, lib = world
    c = app.client()
    needs = _post(c, f"/candidates/{ids['p']}/pdf", _strong(probe, DOI_Q, TITLE_Q, "q")).json()
    filed = _post(c, f"/candidates/{ids['q']}/pdf", _strong(probe, DOI_Q, TITLE_Q, "q")).json()
    unmatched = _post(c, f"/runs/{RUN}/pdf", probe.add(pdf("none"), title="Nothing alike", page_text="Nothing alike")).json()
    undone = c.post(f"{BASE}/candidates/{ids['q']}/undo").json()
    cands = c.get(f"{BASE}/runs/{RUN}/candidates").json()
    real = {"harvest_filed": filed, "harvest_needs_confirmation": needs, "harvest_unmatched": unmatched,
            "harvest_undo": undone, "harvest_candidates": cands}
    for name, body in real.items():
        if os.environ.get("DUMP_FIXTURES_TO"):
            Path(os.environ["DUMP_FIXTURES_TO"], f"{name}.json").write_text(json.dumps(body, indent=2) + "\n")
            continue
        pinned = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        _same_keys(_shape(body), _shape(pinned))
