"""Dashboard state machine and Flask routes, driven through the Flask test client (no sockets)."""

from __future__ import annotations

import json
import queue
import threading

import pytest

pytest.importorskip("flask")

from app.modules.term_extractor import dashboard as dash
from app.modules.term_extractor.message import Log, Registration, Stats, UserPrompt


def _reg(geo=None, **kw):
    base = dict(agent_id="id1", agent_name="extractor", fqdn="host.example", cpu="x", gpu="", os="linux",
                arch="arm", python_version="3.12", geolocation=geo or {})
    base.update(kw)
    return Registration(**base)


@pytest.fixture
def board(tmp_path, monkeypatch):
    (tmp_path / "LBNL.png").write_bytes(b"png")
    monkeypatch.setattr(dash, "_ASSETS_PATH", tmp_path)
    monkeypatch.setattr(dash, "_ASSETS_DIR", str(tmp_path))
    return dash.Dashboard(host="127.0.0.1", port=0)


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_register_normalises_ipinfo_loc_and_matches_logo(board):
    q = board._subscribe()
    board.register_agent("A", _reg({"loc": "37.87,-122.27", "org": "LBNL", "hostname": "h"}))
    agent = board._snapshot()["agents"]["A"]
    assert agent["geo"]["lat"] == 37.87 and agent["geo"]["lon"] == -122.27
    assert agent["logo_url"] == "/assets/LBNL.png"
    (msg,) = _drain(q)
    assert msg.startswith("event: registration\n") and '"agent_name": "extractor"' in msg


def test_register_tolerates_malformed_loc_and_unknown_org(board):
    board.register_agent("B", _reg({"loc": "not-a-pair", "org": "Nowhere"}))
    agent = board._snapshot()["agents"]["B"]
    assert agent["geo"] is None
    assert agent["logo_url"] == "/assets/logo-Academy-2025-200x200-dark-bg.png"


def test_logo_lookup_without_assets_dir_is_none(board, tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "_ASSETS_PATH", tmp_path / "missing")
    assert board._find_facility_logo("LBNL", "") is None


def test_heartbeat_only_creates_missing_agent_and_broadcasts(board):
    q = board._subscribe()
    board.agent_heartbeat("A")
    first = board._snapshot()["agents"]["A"]["last_seen"]
    board.agent_heartbeat("A")
    assert board._snapshot()["agents"]["A"]["last_seen"] == first
    assert len(_drain(q)) == 2


def test_log_buffer_is_capped_and_snapshot_returns_tail(board):
    for i in range(2005):
        board.push_log("A", Log(agent_id="id", agent_name="n", message=f"m{i}"))
    assert len(board._logs) == 2000 and board._logs[0]["message"] == "m5"
    snap = board._snapshot()
    assert len(snap["logs"]) == 200 and snap["logs"][-1]["message"] == "m2004"


def test_stats_round_and_merge_into_agent(board):
    board.push_stats("A", Stats(agent_id="id", cpu_percent=12.5, memory_rss_mb=10.26, memory_vms_mb=20.04))
    agent = board._snapshot()["agents"]["A"]
    assert agent["memory_rss_mb"] == 10.3 and agent["memory_vms_mb"] == 20.0 and agent["gpu_stats"] == []


def test_prompt_roundtrip_wakes_waiter_and_clears_queue(board):
    board.register_agent("A", _reg())
    pid = board.push_prompt("A", UserPrompt(agent_id="id1", prompt="ok?", responses=["y", "n"]))
    assert board._snapshot()["prompts"][0]["agent"] == "extractor"
    got: list[str] = []
    t = threading.Thread(target=lambda: got.append(board.wait_for_response(pid)))
    t.start()
    board.submit_response(pid, "y")
    t.join(5)
    assert got == ["y"] and board._snapshot()["prompts"] == []
    assert board.wait_for_response("unknown") == ""


def test_dismiss_answers_with_empty_string(board):
    board.register_agent("A", _reg())
    pid = board.push_prompt("A", UserPrompt(agent_id="id1", prompt="p", responses=[]))
    board.dismiss_prompt(pid)
    assert board.wait_for_response(pid) == ""


def test_broadcast_drops_subscribers_whose_queue_is_full(board):
    slow = board._subscribe()
    healthy = board._subscribe()
    for _ in range(250):
        slow.put_nowait("x")
    board._broadcast("log", {"a": 1})
    assert slow not in board._subscribers and healthy in board._subscribers
    board._unsubscribe(slow)  # already removed: must not raise


def test_routes_index_assets_respond_dismiss_shutdown(board):
    board.register_agent("A", _reg())
    client = board._app.test_client()
    assert b"AGENT MONITOR" in client.get("/").data
    assert client.get("/assets/LBNL.png").data == b"png"
    assert client.get("/assets/../secrets").status_code == 404

    pid = board.push_prompt("A", UserPrompt(agent_id="id1", prompt="p", responses=[]))
    assert client.post(f"/respond/{pid}", data=json.dumps({"response": "yes"})).status_code == 204
    assert board.wait_for_response(pid) == "yes"
    pid2 = board.push_prompt("A", UserPrompt(agent_id="id1", prompt="p", responses=[]))
    assert client.post(f"/respond/{pid2}", data="not json").status_code == 204
    assert board.wait_for_response(pid2) == ""
    pid3 = board.push_prompt("A", UserPrompt(agent_id="id1", prompt="p", responses=[]))
    assert client.post(f"/dismiss/{pid3}").status_code == 204

    assert client.post("/shutdown/id1").status_code == 204  # no callback registered
    seen = []
    board.set_shutdown_callback(seen.append)
    client.post("/shutdown/id1")
    assert seen == ["id1"]


def test_events_stream_sends_init_snapshot_then_heartbeat_and_unsubscribes(board, monkeypatch):
    board.register_agent("A", _reg())
    client = board._app.test_client()
    resp = client.get("/events")
    assert resp.mimetype == "text/event-stream"
    stream = resp.response
    first = next(stream)
    assert first.startswith(b"event: init") and b'"A"' in first
    assert len(board._subscribers) == 1
    board._broadcast("log", {"x": 1})
    assert b"event: log" in next(stream)
    # idle queue -> heartbeat comment (shrink the 25s wait)
    board._subscribers[0].get = lambda timeout: (_ for _ in ()).throw(queue.Empty())
    assert next(stream) == b": heartbeat\n\n"
    stream.close()
    assert board._subscribers == []


def test_start_runs_flask_in_daemon_thread(board, monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target, kwargs, daemon):
            started.update(target=target, kwargs=kwargs, daemon=daemon)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(dash.threading, "Thread", FakeThread)
    board.start()
    assert started["daemon"] and started["started"]
    assert started["kwargs"] == {"host": "127.0.0.1", "port": 0, "threaded": True}
    assert started["target"] == board._app.run
