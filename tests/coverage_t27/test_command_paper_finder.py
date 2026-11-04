"""``f2w-paper-finder`` (app.modules.commands.paper_finder) beyond tests/test_paper_finder.py (T27f).

Flask test client, a fake watchdog observer, a fake browser and a fake clock: no port is bound, nothing is fetched.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any

import pytest

from app.modules.commands import harvest_rsoxs as hv
from app.modules.commands import paper_finder as pf


def paper(title="Alpha", *, doi="https://doi.org/10.1/a", status="skipped", year=2020, arxiv_id=None, **extra: Any):
    return {"doi": doi, "arxiv_id": arxiv_id, "title": title, "publication_year": year, "source": "openalex",
            "pdf_path": None, "ingestion": {"status": status, "error": "no oa"}, **extra}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    dest = tmp_path / "papers" / "rsoxs"
    staging = tmp_path / "papers" / "staging"
    staging.mkdir(parents=True)
    dest.mkdir(parents=True)
    opened: list[str] = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url) or True)

    def make(papers, *, dry_run=False):
        hv._save_manifest(dest / "manifest.json", {"corpus": "rsoxs", "papers": papers})
        state = pf.QueueState(dest_root=dest, staging_dir=staging, dry_run=dry_run)
        return state, pf.create_app(state).test_client()

    return make, dest, staging, opened


def _manifest(dest: Path) -> list[dict[str, Any]]:
    return json.loads((dest / "manifest.json").read_text())["papers"]


# -- small helpers ---------------------------------------------------------------------------------------------


def test_local_bind_refuses_non_loopback_reserved_and_out_of_range_ports():
    assert pf.local_bind("127.0.0.1", 5057) == ("127.0.0.1", 5057)
    for host, port in (("0.0.0.0", 5057), ("localhost", 5057), ("127.0.0.1", 5174), ("127.0.0.1", 5175),
                       ("127.0.0.1", 8090), ("127.0.0.1", 0), ("127.0.0.1", 70000)):
        with pytest.raises(ValueError):
            pf.local_bind(host, port)


def test_staging_dir_resolution_prefers_the_new_variable_then_the_old_one(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "REPO_ROOT", tmp_path)
    monkeypatch.delenv(pf.STAGING_ENV, raising=False)
    monkeypatch.delenv(pf.DOWNLOADS_ENV, raising=False)
    assert pf.default_staging_dir() == tmp_path / "papers" / "staging"
    monkeypatch.setenv(pf.DOWNLOADS_ENV, str(tmp_path / "old"))
    assert pf.default_staging_dir() == tmp_path / "old"
    monkeypatch.setenv(pf.STAGING_ENV, "  " + str(tmp_path / "new") + " ")
    assert pf.default_staging_dir() == tmp_path / "new"


def test_ensure_staging_dir_creates_nested_folders(tmp_path):
    target = tmp_path / "a" / "b"
    assert pf.ensure_staging_dir(target) == target and target.is_dir()


@pytest.mark.parametrize(("row", "expected"), [
    ({"journal": "  Nature  "}, "Nature"),
    ({"journal": "", "host_venue": {"display_name": "Science"}}, "Science"),
    ({"venue": {"name": "J. Venue"}}, "J. Venue"),
    ({"container_title": {"name": " "}, "primary_location": {"source": {"display_name": "PRL"}}}, "PRL"),
    ({"primary_location": {"source": {"display_name": ""}}, "pubtemp_kind": "preprint", "source": "arxiv"},
     "preprint · arxiv"),
    ({"primary_location": "bad", "source": "openalex"}, "openalex"),
    ({}, ""),
])
def test_journal_label_falls_back_through_venue_fields(row, expected):
    assert pf.journal_label(row) == expected


def test_open_url_prefers_doi_then_arxiv_then_nothing():
    assert pf.open_url({"doi": "10.1/x"}).endswith("10.1/x")
    assert pf.open_url({"doi": "", "arxiv_id": " 2101.00001 "}) == "https://arxiv.org/abs/2101.00001"
    assert pf.open_url({"title": "x"}) is None


def test_paper_key_uses_doi_then_arxiv_then_path_then_title():
    assert pf._paper_key({"doi": "https://doi.org/10.1/ABC"}) == pf._paper_key({"doi": "10.1/abc"}) != ""
    assert pf._paper_key({"arxiv_id": "2101.0001V1"}) == "2101.0001v1"
    assert pf._paper_key({"pdf_path": "P/X.pdf", "title": "T"}) == "p/x.pdf"
    assert pf._paper_key({"title": "Some Title"}) == "some title" and pf._paper_key({}) == ""


def test_mark_restricted_keeps_other_ingestion_fields():
    row = {"ingestion": {"status": "skipped", "kg_version": "v1"}}
    pf.mark_restricted(row)
    assert row["ingestion"]["status"] == "restricted" and row["ingestion"]["kg_version"] == "v1"
    assert "manual review" in row["ingestion"]["error"]


def test_queue_rows_sort_by_title_and_hide_session_skips():
    rows = [paper("beta", doi="10.1/b"), paper("Alpha", doi="10.1/a"), paper("gamma", doi="10.1/g", status="pending"),
            paper("delta", doi="10.1/d", status="failed")]
    assert [r["title"] for r in pf.queue_rows(rows, set())] == ["Alpha", "beta", "delta"]
    assert [r["title"] for r in pf.queue_rows(rows, {pf._paper_key(rows[0])})] == ["Alpha", "delta"]


# -- assigning -------------------------------------------------------------------------------------------------


def test_assign_refuses_empty_missing_and_clashing_files(env, caplog):
    _, dest, staging, _ = env
    row = paper()
    empty = staging / "empty.pdf"
    empty.write_bytes(b"")
    with caplog.at_level(logging.WARNING, logger="paper_finder"):
        assert pf.assign_pdf(row, empty, dest, dry_run=False) is None
        assert pf.assign_pdf(row, staging / "gone.pdf", dest, dry_run=False) is None
        good = staging / "x.pdf"
        good.write_bytes(b"%PDF-1.4 x")
        clash = dest / "2020" / f"{hv._safe_name(row)}.pdf"
        clash.parent.mkdir(parents=True)
        clash.write_bytes(b"already here")
        assert pf.assign_pdf(row, good, dest, dry_run=False) is None
    assert "not a non-empty file" in caplog.text and "destination already exists" in caplog.text
    assert good.exists() and clash.read_bytes() == b"already here" and row["ingestion"]["status"] == "skipped"


def test_assign_with_an_unknown_year_files_under_unknown(env):
    _, dest, staging, _ = env
    row = paper(year=None)
    src = staging / "n.pdf"
    src.write_bytes(b"%PDF-1.4 n")
    out = pf.assign_pdf(row, src, dest, dry_run=False)
    assert out is not None and out.parent.name == "unknown" and not src.exists()
    assert row["pdf_sha256"] and row["ingestion"] == {"status": "pending", "error": None}


# -- staging watcher -------------------------------------------------------------------------------------------


def test_wait_for_stable_file_needs_a_steady_nonzero_size(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(pf.time, "sleep", sleeps.append)
    steady = tmp_path / "a.pdf"
    steady.write_bytes(b"x")
    assert pf._wait_for_stable_file(steady, checks=2, interval=0.1) is True and sleeps == [0.1, 0.1]
    empty = tmp_path / "e.pdf"
    empty.write_bytes(b"")
    assert pf._wait_for_stable_file(empty, checks=2, interval=0.0) is False
    assert pf._wait_for_stable_file(tmp_path / "vanished.pdf") is False


def test_wait_for_stable_file_restarts_the_count_when_the_size_changes(tmp_path, monkeypatch):
    target = tmp_path / "grow.pdf"
    target.write_bytes(b"x")
    sizes = iter([1, 2, 2, 3, 3, 3])

    class Stat:
        def __init__(self, size):
            self.st_size = size

    monkeypatch.setattr(Path, "stat", lambda self: Stat(next(sizes)))
    monkeypatch.setattr(pf.time, "sleep", lambda s: None)
    assert pf._wait_for_stable_file(target, checks=2, interval=0) is True


def test_watcher_records_only_completed_pdfs_once(tmp_path, monkeypatch, caplog):
    detected: list[Path] = []
    watcher = pf.StagingWatcher(detected, threading.Lock())
    done, partial, text = tmp_path / "ok.PDF", tmp_path / "half.pdf", tmp_path / "n.txt"
    for path in (done, partial, text):
        path.write_bytes(b"x")
    monkeypatch.setattr(pf, "_wait_for_stable_file", lambda p: p != partial)
    with caplog.at_level(logging.INFO, logger="paper_finder"):
        for path in (done, done, partial, text):
            watcher._maybe_record(path)
    assert detected == [done] and "Ignoring incomplete download: half.pdf" in caplog.text


def test_watcher_survives_an_os_error_while_settling(tmp_path, monkeypatch, caplog):
    detected: list[Path] = []

    def boom(path):
        raise PermissionError("denied")

    monkeypatch.setattr(pf, "_wait_for_stable_file", boom)
    with caplog.at_level(logging.WARNING, logger="paper_finder"):
        pf.StagingWatcher(detected, threading.Lock())._maybe_record(tmp_path / "x.pdf")
    assert detected == [] and "Error watching x.pdf" in caplog.text


def test_watcher_handles_created_and_moved_events_but_not_directories(tmp_path, monkeypatch):
    detected: list[Path] = []
    watcher = pf.StagingWatcher(detected, threading.Lock())
    monkeypatch.setattr(pf, "_wait_for_stable_file", lambda p: True)

    class Event:
        def __init__(self, src, *, is_directory=False, dest=None):
            self.src_path, self.is_directory = src, is_directory
            if dest is not None:
                self.dest_path = dest

    watcher.on_created(Event("/s/a.pdf"))
    watcher.on_created(Event("/s/dir.pdf", is_directory=True))
    watcher.on_moved(Event("/tmp/x.crdownload", dest="/s/b.pdf"))
    watcher.on_moved(Event("/tmp/y", is_directory=True, dest="/s/c.pdf"))
    watcher.on_moved(Event("/tmp/z"))
    assert detected == [Path("/s/a.pdf"), Path("/s/b.pdf")]


# -- queue state -----------------------------------------------------------------------------------------------


def test_queue_state_round_trips_and_survives_a_corrupt_file(env, caplog):
    make, dest, _, _ = env
    state, _ = make([paper()])
    state.current_index, state.skipped_keys = 3, {"b", "a"}
    state.save_persisted()
    assert json.loads(state.state_path.read_text()) == {"current_index": 3, "skipped_keys": ["a", "b"]}
    fresh = pf.QueueState(dest_root=dest, staging_dir=dest)
    fresh.load_persisted()
    assert fresh.current_index == 3 and fresh.skipped_keys == {"a", "b"}
    state.state_path.write_text("{broken")
    again = pf.QueueState(dest_root=dest, staging_dir=dest)
    with caplog.at_level(logging.WARNING, logger="paper_finder"):
        again.load_persisted()
    assert again.current_index == 0 and "starting fresh" in caplog.text


def test_dry_run_state_writes_neither_state_nor_manifest(env):
    make, dest, _, _ = env
    state, _ = make([paper()], dry_run=True)
    before = (dest / "manifest.json").read_bytes()
    state.save_persisted()
    state.save_manifest({"papers": []})
    assert not state.state_path.exists() and (dest / "manifest.json").read_bytes() == before


def test_current_clamps_the_cursor_to_the_queue(env):
    make, _, _, _ = env
    state, _ = make([paper("A", doi="10.1/a"), paper("B", doi="10.1/b")])
    state.current_index = 9
    row, position, total = state.current(state.load_manifest())
    assert (row["title"], position, total) == ("B", 2, 2)
    empty, _ = make([])
    assert empty.current(empty.load_manifest()) == (None, 0, 0)
    empty.current_index = 5
    empty.clamp_index(empty.load_manifest())
    assert empty.current_index == 0


# -- web routes ------------------------------------------------------------------------------------------------


def test_index_shows_the_current_paper_and_the_empty_state(env):
    make, dest, _, _ = env
    _, client = make([paper("Zeta study", primary_location={"source": {"display_name": "Nature Mat"}})])
    page = client.get("/").get_data(as_text=True)
    assert "Zeta study" in page and "Nature Mat" in page and "1 of 1 remaining" in page and "Open in browser" in page
    _, empty = make([])
    assert "Nothing left to review" in empty.get("/").get_data(as_text=True)


def test_detected_files_are_listed_with_assign_and_dismiss(env):
    make, _, staging, _ = env
    state, client = make([paper()])
    state.detected_files.append(staging / "saved.pdf")
    assert client.get("/detected").get_json() == {"detected": ["saved.pdf"]}
    page = client.get("/").get_data(as_text=True)
    assert "saved.pdf" in page and 'value="assign"' in page and 'value="dismiss"' in page


def test_open_without_doi_or_arxiv_launches_nothing(env, caplog):
    make, _, _, opened = env
    _, client = make([paper(doi="", arxiv_id=None)])
    with caplog.at_level(logging.WARNING, logger="paper_finder"):
        resp = client.post("/action", data={"action": "open"})
    assert resp.status_code == 302 and opened == [] and "nothing to open" in caplog.text


def test_unavailable_marks_the_paper_restricted_and_persists(env):
    make, dest, _, _ = env
    state, client = make([paper("A", doi="10.1/a"), paper("B", doi="10.1/b")])
    assert client.post("/action", data={"action": "unavailable"}).status_code == 302
    by_title = {p["title"]: p["ingestion"]["status"] for p in _manifest(dest)}
    assert by_title == {"A": "restricted", "B": "skipped"} and state.state_path.is_file()


def test_skip_hides_the_paper_for_the_session_without_touching_the_manifest(env):
    make, dest, _, _ = env
    state, client = make([paper("A", doi="10.1/a"), paper("B", doi="10.1/b")])
    before = (dest / "manifest.json").read_bytes()
    client.post("/action", data={"action": "skip"})
    assert [p["title"] for p in state.pending(state.load_manifest())] == ["B"]
    assert json.loads(state.state_path.read_text())["skipped_keys"] == [pf._paper_key(paper("A", doi="10.1/a"))]
    assert (dest / "manifest.json").read_bytes() == before


def test_actions_on_an_empty_queue_are_harmless(env):
    make, _, _, opened = env
    state, client = make([])
    for act in ("open", "unavailable", "skip", "assign", "dismiss"):
        assert client.post("/action", data={"action": act, "filename": "x.pdf"}).status_code == 302
    assert opened == [] and state.skipped_keys == set()


def test_assign_files_the_detected_pdf_and_removes_it_from_the_list(env):
    make, dest, staging, _ = env
    state, client = make([paper("A", doi="10.1/a", year=2019)])
    src = staging / "saved.pdf"
    src.write_bytes(b"%PDF-1.4 saved")
    state.detected_files.append(src)
    client.post("/action", data={"action": "assign", "filename": "saved.pdf"})
    row = _manifest(dest)[0]
    assert row["ingestion"]["status"] == "pending" and row["pdf_path"].startswith("papers/rsoxs/2019/")
    assert (hv.REPO_ROOT / row["pdf_path"]).is_file() and not src.exists() and state.detected_files == []


def test_assign_of_an_unknown_file_or_a_refused_move_changes_nothing(env):
    make, dest, staging, _ = env
    state, client = make([paper("A", doi="10.1/a")])
    before = (dest / "manifest.json").read_bytes()
    client.post("/action", data={"action": "assign", "filename": "nope.pdf"})
    gone = staging / "gone.pdf"
    state.detected_files.append(gone)  # listed, but no longer on disk: the move is refused
    client.post("/action", data={"action": "assign", "filename": "gone.pdf"})
    assert (dest / "manifest.json").read_bytes() == before and state.detected_files == [gone]


def test_dismiss_drops_a_detected_file_and_ignores_unknown_names(env):
    make, _, staging, _ = env
    state, client = make([paper()])
    state.detected_files.extend([staging / "a.pdf", staging / "b.pdf"])
    client.post("/action", data={"action": "dismiss", "filename": "a.pdf"})
    client.post("/action", data={"action": "dismiss", "filename": "zzz.pdf"})
    assert state.detected_files == [staging / "b.pdf"]


def test_unknown_action_is_logged_and_redirects_home(env, caplog):
    make, _, _, _ = env
    _, client = make([paper()])
    with caplog.at_level(logging.WARNING, logger="paper_finder"):
        resp = client.post("/action", data={"action": "format-disk"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/") and "Unknown action" in caplog.text
    assert client.get("/action").status_code == 405


# -- CLI -------------------------------------------------------------------------------------------------------


def test_parse_args_defaults_aliases_and_required_command(capsys):
    args = pf.parse_args(["serve"])
    assert (args.command, args.port, args.dry_run, args.staging_dir, args.log_level) == ("serve", 5057, False, None, "INFO")
    assert args.dest == hv.REPO_ROOT / "papers" / "rsoxs"
    assert pf.parse_args(["serve", "--downloads-dir", "/d", "--port", "6000", "--dry-run"]).staging_dir == Path("/d")
    assert pf.parse_args(["reset", "--dest", "/c"]).dest == Path("/c")
    for bad in ([], ["frobnicate"], ["serve", "--port", "x"]):
        with pytest.raises(SystemExit) as stop:
            pf.parse_args(bad)
        assert stop.value.code == 2
    capsys.readouterr()


def test_reset_queue_reports_whether_a_state_file_was_removed(tmp_path):
    state = tmp_path / pf.QUEUE_STATE_FILENAME
    assert pf.reset_queue(tmp_path) is False
    state.write_text("{}")
    assert pf.reset_queue(tmp_path) is True and not state.exists()


def test_main_reset_removes_state_and_never_serves(tmp_path, monkeypatch):
    (tmp_path / pf.QUEUE_STATE_FILENAME).write_text("{}")
    monkeypatch.setattr(pf, "serve", lambda **kw: pytest.fail("reset must not serve"))
    pf.main(["reset", "--dest", str(tmp_path), "--log-level", "debug"])
    assert not (tmp_path / pf.QUEUE_STATE_FILENAME).exists()


def test_main_serve_passes_the_parsed_arguments(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(pf, "serve", lambda **kw: seen.update(kw))
    monkeypatch.setattr(pf, "default_staging_dir", lambda: tmp_path / "default-staging")
    pf.main(["serve", "--dest", str(tmp_path), "--port", "6001", "--dry-run", "--log-level", "nonsense"])
    assert seen == {"dest": tmp_path, "port": 6001, "staging_dir": tmp_path / "default-staging", "dry_run": True}
    pf.main(["serve", "--staging-dir", str(tmp_path / "mine")])
    assert seen["staging_dir"] == tmp_path / "mine" and seen["dry_run"] is False and seen["port"] == 5057


class FakeObserver:
    instances: list[FakeObserver] = []

    def __init__(self):
        self.scheduled, self.started, self.stopped, self.joined, self.daemon = [], False, False, None, False
        FakeObserver.instances.append(self)

    def schedule(self, handler, path):
        self.scheduled.append((handler, path))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = timeout


def test_serve_binds_loopback_watches_staging_and_stops_the_observer(tmp_path, monkeypatch):
    FakeObserver.instances.clear()
    monkeypatch.setattr(pf, "Observer", FakeObserver)
    runs: list[dict[str, Any]] = []
    monkeypatch.setattr(pf.Flask, "run", lambda self, **kw: runs.append(kw))
    staging = tmp_path / "new" / "staging"
    pf.serve(dest=tmp_path, port=5099, staging_dir=staging, dry_run=True)
    assert staging.is_dir() and runs == [{"host": "127.0.0.1", "port": 5099, "debug": False, "use_reloader": False}]
    obs = FakeObserver.instances[0]
    assert obs.started and obs.stopped and obs.joined == 2 and obs.daemon is True
    assert obs.scheduled[0][1] == str(staging) and isinstance(obs.scheduled[0][0], pf.StagingWatcher)


def test_serve_restores_the_queue_and_stops_the_observer_when_the_server_dies(tmp_path, monkeypatch):
    FakeObserver.instances.clear()
    monkeypatch.setattr(pf, "Observer", FakeObserver)
    (tmp_path / pf.QUEUE_STATE_FILENAME).write_text(json.dumps({"current_index": 2, "skipped_keys": ["k"]}))
    created: list[pf.QueueState] = []
    real_create = pf.create_app
    monkeypatch.setattr(pf, "create_app", lambda state: created.append(state) or real_create(state))

    def die(self, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(pf.Flask, "run", die)
    with pytest.raises(KeyboardInterrupt):
        pf.serve(dest=tmp_path, port=5099, staging_dir=tmp_path / "s", dry_run=False)
    assert created[0].current_index == 2 and created[0].skipped_keys == {"k"} and FakeObserver.instances[0].stopped


@pytest.mark.parametrize("port", [5174, 5175, 8090, 0])
def test_serve_refuses_reserved_ports_before_creating_anything(tmp_path, monkeypatch, port):
    monkeypatch.setattr(pf, "Observer", lambda: pytest.fail("no observer for a refused port"))
    with pytest.raises(ValueError):
        pf.serve(dest=tmp_path, port=port, staging_dir=tmp_path / "never", dry_run=False)
    assert not (tmp_path / "never").exists()
