"""Tests for Paper Finder (no network, no Flask server bind)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import harvest_rsoxs as hv
import paper_finder as pf


def _paper(
    *,
    doi: str = "https://doi.org/10.1002/adma.202000001",
    status: str = "skipped",
    pdf_path: str | None = None,
    title: str = "A polymer RSoXS paper",
    year: int = 2020,
    arxiv_id: str | None = None,
) -> dict:
    return {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "title": title,
        "publication_year": year,
        "source": "openalex",
        "pdf_path": pdf_path,
        "ingestion": {
            "status": status,
            "kg_version": "v1",
            "error": "no reliable OA PDF URL" if status == "skipped" else None,
        },
    }


def _write_manifest(dest: Path, papers: list[dict]) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "manifest.json"
    hv._save_manifest(path, {"corpus": "rsoxs", "papers": papers})
    return path


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool = False):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    dest = tmp_path / "papers" / "rsoxs"
    dest.mkdir(parents=True, exist_ok=True)
    staging = tmp_path / "papers" / "staging"
    staging.mkdir(exist_ok=True)
    return (
        pf.QueueState(dest_root=dest, staging_dir=staging, dry_run=dry_run),
        dest,
        staging,
    )


def test_queue_filter_skips_on_disk_restricted_and_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    on_disk = tmp_path / "papers" / "rsoxs" / "2020" / "10.1002_adma.202000001.pdf"
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(b"%PDF-1.4\n")
    papers = [
        _paper(status="skipped", pdf_path=None, title="Need human"),
        _paper(
            doi="https://doi.org/10.1002/adma.202000002",
            status="failed",
            pdf_path=None,
            title="Also need human",
        ),
        _paper(
            doi="https://doi.org/10.1002/adma.202000001",
            status="skipped",
            pdf_path=str(on_disk.relative_to(tmp_path)),
            title="Already filed",
        ),
        _paper(
            doi="https://doi.org/10.1002/adma.202000003",
            status="restricted",
            pdf_path=None,
            title="Human said no access",
        ),
        _paper(
            doi="https://doi.org/10.1002/adma.202000004",
            status="pending",
            pdf_path=None,
            title="OA pending",
        ),
    ]
    rows = pf.queue_rows(papers, skipped_keys=set())
    titles = {row["title"] for row in rows}
    assert titles == {"Need human", "Also need human"}


def test_restricted_status_drops_row_from_queue_and_writes_manifest(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    app = pf.create_app(state)
    client = app.test_client()

    listed = client.get("/")
    assert listed.status_code == 200
    assert b"A polymer RSoXS paper" in listed.data

    marked = client.post("/action", data={"action": "unavailable"})
    assert marked.status_code == 302

    saved = json.loads((dest / "manifest.json").read_text())
    assert saved["papers"][0]["ingestion"]["status"] == "restricted"
    assert "manual review" in (saved["papers"][0]["ingestion"]["error"] or "")

    empty = client.get("/")
    assert b"A polymer RSoXS paper" not in empty.data
    assert b"Nothing left to review" in empty.data


def test_assign_moves_pdf_and_writes_harvest_fields(tmp_path, monkeypatch):
    state, dest, staging = _state(tmp_path, monkeypatch)
    paper = _paper()
    _write_manifest(dest, [paper])
    src = staging / "browser-save.pdf"
    src.write_bytes(b"%PDF-1.4\nmanual\n")
    state.detected_files.append(src)

    app = pf.create_app(state)
    client = app.test_client()
    response = client.post(
        "/action",
        data={"action": "assign", "filename": "browser-save.pdf"},
    )
    assert response.status_code == 302

    dest_pdf = dest / "2020" / "10.1002_adma.202000001.pdf"
    assert dest_pdf.is_file()
    assert dest_pdf.read_bytes().startswith(b"%PDF-1.4")
    assert not src.exists()

    saved = json.loads((dest / "manifest.json").read_text())
    row = saved["papers"][0]
    assert row["pdf_path"] == "papers/rsoxs/2020/10.1002_adma.202000001.pdf"
    assert row["pdf_sha256"] == hv._sha256(dest_pdf)
    assert row["downloaded_at"]
    assert row["ingestion"]["status"] == "pending"
    assert row["ingestion"]["error"] is None


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    state, dest, staging = _state(tmp_path, monkeypatch, dry_run=True)
    _write_manifest(dest, [_paper()])
    original = (dest / "manifest.json").read_text()
    src = staging / "browser-save.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    state.detected_files.append(src)

    app = pf.create_app(state)
    client = app.test_client()
    client.post("/action", data={"action": "unavailable"})
    client.post("/action", data={"action": "assign", "filename": "browser-save.pdf"})
    client.post("/action", data={"action": "skip"})

    assert (dest / "manifest.json").read_text() == original
    assert not (dest / pf.QUEUE_STATE_FILENAME).exists()
    assert src.is_file()
    assert not (dest / "2020" / "10.1002_adma.202000001.pdf").exists()


def test_open_does_not_http_fetch(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    opened: list[str] = []
    fetch_calls: list[str] = []

    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))

    def _boom(*_args, **_kwargs):
        fetch_calls.append("urlopen")
        raise AssertionError("Paper Finder must not HTTP-fetch the publisher")

    monkeypatch.setattr(hv.urllib.request, "urlopen", _boom)
    if "urllib.request" in sys.modules:
        monkeypatch.setattr(sys.modules["urllib.request"], "urlopen", _boom)

    app = pf.create_app(state)
    client = app.test_client()
    response = client.post("/action", data={"action": "open"})
    assert response.status_code == 302
    assert opened == ["https://doi.org/10.1002/adma.202000001"]
    assert fetch_calls == []
    assert "requests" not in sys.modules or not hasattr(pf, "requests")


def test_open_arxiv_when_no_doi(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(
        dest,
        [
            _paper(
                doi="",
                arxiv_id="2401.00001",
                title="Preprint only",
            )
        ],
    )
    opened: list[str] = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))
    app = pf.create_app(state)
    client = app.test_client()
    client.post("/action", data={"action": "open"})
    assert opened == ["https://arxiv.org/abs/2401.00001"]


def test_local_bind_loopback_only():
    assert pf.local_bind("127.0.0.1", 5057) == ("127.0.0.1", 5057)
    with pytest.raises(ValueError, match="127.0.0.1"):
        pf.local_bind("0.0.0.0", 5057)
    for port in (5174, 5175, 8090):
        with pytest.raises(ValueError, match="reserved"):
            pf.local_bind("127.0.0.1", port)


def test_default_watch_path_is_papers_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("PAPER_FINDER_STAGING", raising=False)
    monkeypatch.delenv("PAPER_FINDER_DOWNLOADS", raising=False)
    assert pf.default_staging_dir() == tmp_path / "papers" / "staging"
    created = pf.ensure_staging_dir(pf.default_staging_dir())
    assert created.is_dir()
    args = pf.parse_args(["serve"])
    assert args.staging_dir is None
    alias = pf.parse_args(["serve", "--downloads-dir", str(tmp_path / "alt")])
    assert alias.staging_dir == tmp_path / "alt"
    named = pf.parse_args(["serve", "--staging-dir", str(tmp_path / "stage")])
    assert named.staging_dir == tmp_path / "stage"


def test_serve_parse_args_does_not_bind():
    args = pf.parse_args(["serve", "--port", "5057", "--dry-run"])
    assert args.command == "serve"
    assert args.port == 5057
    assert args.dry_run is True


def test_reload_drops_rows_resolved_by_parallel_harvest(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    paper = _paper()
    _write_manifest(dest, [paper])
    app = pf.create_app(state)
    client = app.test_client()
    assert b"A polymer RSoXS paper" in client.get("/").data

    filed = dest / "2020" / "10.1002_adma.202000001.pdf"
    filed.parent.mkdir(parents=True)
    filed.write_bytes(b"%PDF-1.4\noa\n")
    paper["pdf_path"] = str(filed.relative_to(tmp_path))
    paper["ingestion"]["status"] = "pending"
    paper["ingestion"]["error"] = None
    paper["downloaded_at"] = hv._now()
    paper["pdf_sha256"] = hv._sha256(filed)
    hv._save_manifest(dest / "manifest.json", {"corpus": "rsoxs", "papers": [paper]})

    assert b"Nothing left to review" in client.get("/").data


def test_reset_removes_queue_state(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    app = pf.create_app(state)
    client = app.test_client()
    client.post("/action", data={"action": "skip"})
    assert (dest / pf.QUEUE_STATE_FILENAME).exists()
    assert pf.reset_queue(dest) is True
    assert not (dest / pf.QUEUE_STATE_FILENAME).exists()
    fresh, _, _ = _state(tmp_path, monkeypatch)
    fresh_app = pf.create_app(fresh)
    assert b"A polymer RSoXS paper" in fresh_app.test_client().get("/").data


def test_create_app_does_not_call_flask_run(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    run = MagicMock()
    monkeypatch.setattr(pf.Flask, "run", run)
    pf.create_app(state)
    run.assert_not_called()


def test_get_index_does_not_open_browser(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    opened: list[str] = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))
    app = pf.create_app(state)
    client = app.test_client()
    page = client.get("/")
    detected = client.get("/detected")
    assert page.status_code == 200
    assert detected.status_code == 200
    assert detected.get_json() == {"detected": []}
    assert opened == []
    assert b'http-equiv="refresh"' not in page.data
    assert b"ev.key === ' '" not in page.data
    assert b"pubs.rsc" not in page.data
    assert b"doi.org" not in page.data or b'href="https://doi.org' not in page.data
    assert b"RSC 429" in page.data
    assert b"fetch('/detected'" in page.data


def test_open_debounces_repeat_webbrowser(tmp_path, monkeypatch):
    state, dest, _staging = _state(tmp_path, monkeypatch)
    _write_manifest(dest, [_paper()])
    opened: list[str] = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))
    app = pf.create_app(state)
    client = app.test_client()
    client.post("/action", data={"action": "open"})
    client.post("/action", data={"action": "open"})
    assert opened == ["https://doi.org/10.1002/adma.202000001"]


def test_launch_user_browser_debounces_without_http(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))
    did, url, t1 = pf.launch_user_browser(
        "https://doi.org/10.1039/c5xx", now=100.0
    )
    did2, _, _ = pf.launch_user_browser(
        "https://doi.org/10.1039/c5xx",
        last_url=url,
        last_at=t1,
        now=105.0,
    )
    assert did is True
    assert did2 is False
    assert opened == ["https://doi.org/10.1039/c5xx"]
