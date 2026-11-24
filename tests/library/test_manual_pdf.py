"""The manual PDF flow, backend (design-manual-pdf-flow.md, step 2): verdict, confirm, other candidate, undo, batch drop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.library.harvest import Candidate, Harvester, HarvestStore
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.library.test_library_service import pdf
from tests.security.security_fakes import local_settings

RUN = "ab" * 8
TITLE_P = "Resonant soft X-ray scattering of polymer blend morphology"
TITLE_Q = "Grazing incidence scattering of block copolymer thin films"
DOI_P, DOI_Q = "10.1021/acs.macromol.0c01782", "10.1038/s41563-020-0001-2"


class ContentProbe:
    """A probe keyed by the PDF's bytes (the library probes a temp copy, so a file name says nothing)."""

    def __init__(self) -> None:
        self.table: dict[bytes, ProbeResult] = {}

    def add(self, data: bytes, **kw: Any) -> bytes:
        self.table[data] = ProbeResult(**{"status": "ok", "pages": 1, **kw})
        return data

    def __call__(self, path: Path) -> ProbeResult:
        return self.table.get(path.read_bytes(), ProbeResult(status="ok", pages=1, page_text="no identifiers here"))


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.lib = tmp_path / "papers"
        self.lib.mkdir()
        self.probe = ContentProbe()
        self.service = LibraryService(self.lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                                      probe=self.probe)
        self.store = HarvestStore(tmp_path / "state" / "harvest.sqlite")
        self.harvester = Harvester(self.service, self.store, searchers={}, delay=0.0)
        self.store.create_run(RUN, topic="rsoxs", category="rsoxs", schema=None, sources=["arxiv"], max_downloads=9)
        self.p = self.add(TITLE_P, DOI_P, "paywalled")
        self.q = self.add(TITLE_Q, DOI_Q, "paywalled")

    def add(self, title: str, doi: str | None, status: str) -> str:
        return self.store.save_candidate(RUN, Candidate(title=title, source="openalex", year=2020, doi=doi), status=status)

    def manifest(self) -> list[dict[str, Any]]:
        path = self.lib / "rsoxs" / "manifest.json"
        return json.loads(path.read_text())["papers"] if path.is_file() else []

    def files(self) -> list[str]:
        return sorted(str(p.relative_to(self.lib)) for p in self.lib.rglob("*.pdf"))


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


def strong_pdf(w: World, doi: str = DOI_P, title: str = TITLE_P, tag: str = "a") -> bytes:
    return w.probe.add(pdf(tag), title=title, page_text=f"{title}\nA. Author\nhttps://doi.org/{doi}\nAbstract")


def test_a_strong_match_files_at_once_with_provenance_and_who_supplied_it(w: World):
    out = w.harvester.supply_pdf(w.p, "p.pdf", strong_pdf(w), supplied_by="alice")
    assert out["filed"] is True and out["verdict"]["kind"] == "strong" and out["duplicate"] is False
    cand = out["candidate"]
    assert cand["status"] == "filed" and cand["provenance"] == "user_supplied" and cand["supplied_by"] == "alice"
    assert cand["match"] is None
    row = w.manifest()[0]
    assert row["provenance"] == "user_supplied" and row["supplied_by"] == "alice" and row["pdf_sha256"]
    assert w.files() == [out["paper"]["relpath"]]
    assert "prev_status" not in cand and "filed_sha" not in cand


def test_an_unreadable_pdf_is_weak_and_stores_nothing(w: World):
    data = w.probe.add(pdf("scan"), status="encrypted")
    out = w.harvester.supply_pdf(w.p, "scan.pdf", data)
    assert out["filed"] is False and out["needs_confirmation"] is True and out["verdict"]["kind"] == "weak"
    assert out["candidate"]["id"] == w.p and out["other_candidate"] is None
    assert w.files() == [] and w.manifest() == [] and w.store.candidate(w.p)["status"] == "paywalled"
    assert w.service.list_papers()["papers"] == []


def test_confirm_files_a_weak_match_as_confirmed(w: World):
    data = w.probe.add(pdf("scan"), status="unreadable")
    out = w.harvester.supply_pdf(w.p, "scan.pdf", data, confirm=True, supplied_by="bob")
    assert out["filed"] is True and out["candidate"]["provenance"] == "user_supplied_confirmed"
    assert w.manifest()[0]["provenance"] == "user_supplied_confirmed" and w.manifest()[0]["supplied_by"] == "bob"
    assert "confirmed by the user" in w.store.candidate(w.p)["detail"]


def test_a_mismatch_asks_with_both_dois_and_titles_and_confirm_records_both(w: World):
    preprint = "10.48550/arxiv.2001.00001"
    data = w.probe.add(pdf("pre"), title="A different paper", page_text=f"A different paper\ndoi:{preprint}")
    out = w.harvester.supply_pdf(w.p, "pre.pdf", data)
    assert out["filed"] is False and out["verdict"]["kind"] == "mismatch"
    assert out["verdict"]["found_dois"] == [preprint] and out["verdict"]["found_title"] == "A different paper"
    assert out["candidate"] == {"id": w.p, "title": TITLE_P, "doi": DOI_P}
    assert w.files() == [] and w.manifest() == []
    done = w.harvester.supply_pdf(w.p, "pre.pdf", data, confirm=True)
    assert done["filed"] is True and done["candidate"]["provenance"] == "user_supplied_mismatch_confirmed"
    assert done["candidate"]["match"] == {"candidate_doi": DOI_P, "candidate_title": TITLE_P,
                                          "found_dois": [preprint], "found_title": "A different paper"}
    assert w.manifest()[0]["provenance"] == "user_supplied_mismatch_confirmed"


def test_the_dois_of_another_open_candidate_is_offered_and_to_files_it_there(w: World):
    data = strong_pdf(w, DOI_Q, TITLE_Q, "q")
    out = w.harvester.supply_pdf(w.p, "q.pdf", data)   # dropped on the wrong row
    assert out["filed"] is False and out["verdict"]["kind"] == "mismatch"
    assert out["other_candidate"] == {"id": w.q, "title": TITLE_Q, "doi": DOI_Q, "status": "paywalled"}
    moved = w.harvester.supply_pdf(w.p, "q.pdf", data, to=w.q, supplied_by="carol")
    assert moved["filed"] is True and moved["candidate"]["id"] == w.q and moved["verdict"]["kind"] == "strong"
    assert moved["candidate"]["provenance"] == "user_supplied"
    assert w.store.candidate(w.p)["status"] == "paywalled" and w.store.candidate(w.q)["status"] == "filed"


def test_to_must_be_an_open_candidate_of_the_same_run(w: World):
    w.store.create_run("cd" * 8, topic="x", category="other", schema=None, sources=["arxiv"], max_downloads=1)
    foreign = w.store.save_candidate("cd" * 8, Candidate(title="Other run", source="arxiv", year=2020), status="paywalled")
    with pytest.raises(ContractError) as err:
        w.harvester.supply_pdf(w.p, "x.pdf", strong_pdf(w), to=foreign)
    assert err.value.code is ErrorCode.CONFLICT
    settled = w.add("Settled", "10.1000/settled", "filed")
    with pytest.raises(ContractError) as err2:
        w.harvester.supply_pdf(w.p, "x.pdf", strong_pdf(w), to=settled)
    assert err2.value.code is ErrorCode.CONFLICT and w.files() == []


@pytest.mark.parametrize("status", ["blocked", "failed", "paywalled"])
def test_every_manual_status_accepts_a_pdf(w: World, status: str):
    cid = w.add("Another resonant scattering study", "10.1109/example.2020.1", status)
    data = strong_pdf(w, "10.1109/example.2020.1", "Another resonant scattering study", status)
    out = w.harvester.supply_pdf(cid, "a.pdf", data)
    assert out["filed"] is True and out["candidate"]["status"] == "filed"


@pytest.mark.parametrize("status", ["filed", "downloaded", "in_library", "dismissed", "skipped"])
def test_a_settled_candidate_is_refused(w: World, status: str):
    cid = w.add("Settled study", "10.1109/settled.2020.1", status)
    with pytest.raises(ContractError) as err:
        w.harvester.supply_pdf(cid, "a.pdf", strong_pdf(w))
    assert err.value.code is ErrorCode.CONFLICT and w.files() == []


def test_undo_restores_status_and_removes_file_manifest_row_and_catalog_row(w: World):
    other = w.harvester.supply_pdf(w.q, "q.pdf", strong_pdf(w, DOI_Q, TITLE_Q, "q"))
    w.store.set_status(w.p, "blocked", detail="host blocked")   # prev_status must be this, not paywalled
    out = w.harvester.supply_pdf(w.p, "p.pdf", strong_pdf(w))
    assert len(w.manifest()) == 2 and len(w.service.list_papers()["papers"]) == 2
    before = w.store.runs()[0]["counts"]
    undone = w.harvester.undo_supplied(w.p)
    assert undone["removed"] == {"file": True, "manifest": True}
    cand = undone["candidate"]
    assert cand["status"] == "blocked" and cand["relpath"] is None and cand["provenance"] is None
    assert cand["supplied_by"] is None and cand["match"] is None
    assert w.files() == [other["paper"]["relpath"]]
    assert [r["doi"] for r in w.manifest()] == [f"https://doi.org/{DOI_Q}"]
    assert [p["relpath"] for p in w.service.list_papers()["papers"]] == [other["paper"]["relpath"]]
    after = w.store.runs()[0]["counts"]
    assert after.get("filed") == before["filed"] - 1 and after["blocked"] == before.get("blocked", 0) + 1
    assert not (w.lib / out["paper"]["relpath"]).exists()
    again = w.harvester.supply_pdf(w.p, "p.pdf", strong_pdf(w))   # and the paper can be supplied again
    assert again["filed"] is True


def test_undo_is_refused_for_a_candidate_the_user_did_not_supply(w: World):
    for status in ("paywalled", "downloaded", "filed"):
        cid = w.add(f"Study {status}", f"10.1109/{status}.1", status)
        with pytest.raises(ContractError) as err:
            w.harvester.undo_supplied(cid)
        assert err.value.code is ErrorCode.CONFLICT
    with pytest.raises(ContractError) as missing:
        w.harvester.undo_supplied("0" * 16)
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_undo_does_not_delete_a_file_whose_sha256_differs(w: World):
    out = w.harvester.supply_pdf(w.p, "p.pdf", strong_pdf(w))
    target = w.lib / out["paper"]["relpath"]
    target.write_bytes(pdf("someone replaced this file"))
    undone = w.harvester.undo_supplied(w.p)
    assert undone["removed"]["file"] is False and target.is_file()
    assert undone["candidate"]["status"] == "paywalled"


def test_an_identical_file_already_in_the_library_is_in_library_and_not_undoable(w: World):
    data = strong_pdf(w)
    w.service.upload("already.pdf", data, "rsoxs")
    out = w.harvester.supply_pdf(w.p, "again.pdf", data)
    assert out["filed"] is True and out["duplicate"] is True and out["candidate"]["status"] == "in_library"
    assert out["candidate"]["provenance"] is None and len(w.files()) == 1
    with pytest.raises(ContractError) as err:
        w.harvester.undo_supplied(w.p)
    assert err.value.code is ErrorCode.CONFLICT and len(w.files()) == 1


def test_size_and_magic_are_checked_per_file(w: World):
    with pytest.raises(HTTPException) as big:
        w.harvester.supply_pdf(w.p, "b.pdf", b"%PDF-" + b"x" * (w.service.max_upload_bytes + 1))
    assert big.value.status_code == 413
    with pytest.raises(HTTPException) as bad:
        w.harvester.supply_pdf(w.p, "b.pdf", b"MZ not a pdf")
    assert bad.value.status_code == 415


# -- the batch drop ----------------------------------------------------------------------------------------


def test_run_drop_files_a_strong_match_against_the_right_candidate(w: World):
    out = w.harvester.supply_pdf_to_run(RUN, "any-name.pdf", strong_pdf(w, DOI_Q, TITLE_Q, "q"), supplied_by="dana")
    assert out["filed"] is True and out["candidate"]["id"] == w.q and out["candidate"]["supplied_by"] == "dana"
    assert w.store.candidate(w.p)["status"] == "paywalled"


def test_run_drop_weak_needs_confirmation_and_unmatched_stores_nothing(w: World):
    scan = w.probe.add(pdf("scan"), status="encrypted")
    unmatched = w.harvester.supply_pdf_to_run(RUN, "scan.pdf", scan)
    assert unmatched == {"filed": False, "unmatched": True} and w.files() == [] and w.manifest() == []
    similar = w.probe.add(pdf("sim"), title="Resonant soft X-ray scattering polymer blend morphologies",
                          page_text="Resonant soft X-ray scattering polymer blend morphologies\nno doi here")
    weak = w.harvester.supply_pdf_to_run(RUN, "sim.pdf", similar)
    assert weak["filed"] is False and weak["needs_confirmation"] is True and weak["candidate"]["id"] == w.p
    assert weak["verdict"]["kind"] == "weak" and w.files() == []
    nothing = w.probe.add(pdf("none"), title="Unrelated", page_text="Unrelated words only")
    assert w.harvester.supply_pdf_to_run(RUN, "n.pdf", nothing)["unmatched"] is True


def test_run_drop_a_tie_is_unmatched(w: World):
    w.store.set_status(w.p, "dismissed")
    w.add(TITLE_P, None, "blocked")   # two candidates without a DOI that match this PDF equally well
    twin = w.add("Polymer blend morphology: resonant soft X-ray scattering", None, "failed")
    data = w.probe.add(pdf("tie"), title=TITLE_P, page_text=f"{TITLE_P}\nno doi")
    out = w.harvester.supply_pdf_to_run(RUN, "tie.pdf", data)
    assert out == {"filed": False, "unmatched": True} and w.files() == [] and twin


def test_run_drop_only_considers_open_candidates_and_known_runs(w: World):
    for cid in (w.p, w.q):
        w.store.set_status(cid, "dismissed")
    assert w.harvester.supply_pdf_to_run(RUN, "a.pdf", strong_pdf(w)) == {"filed": False, "unmatched": True}
    with pytest.raises(ContractError) as err:
        w.harvester.supply_pdf_to_run("ef" * 8, "a.pdf", strong_pdf(w))
    assert err.value.code is ErrorCode.NOT_FOUND


def test_remove_filed_never_follows_a_link_or_leaves_the_library(w: World, tmp_path: Path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(pdf("outside"))
    (w.lib / "rsoxs").mkdir(exist_ok=True)
    (w.lib / "rsoxs" / "link.pdf").symlink_to(outside)
    sha = hashlib.sha256(outside.read_bytes()).hexdigest()
    assert w.service.remove_filed("rsoxs/link.pdf", sha)["file"] is False and outside.is_file()
