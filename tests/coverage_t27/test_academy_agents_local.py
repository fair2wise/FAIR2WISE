"""MonitoredAgent / TermExtractorAgent / UserAgent logic run in-process with a fake handle.

No Academy exchange, Globus endpoint or dashboard server is started: ``Agent.__init__`` only sets
private fields, so the classes can be exercised directly.
"""

from __future__ import annotations

import asyncio
import logging
import queue
from types import SimpleNamespace
import uuid

import pytest

pytest.importorskip("academy")

import academy.exception
from academy.identifier import AgentId

from app.modules.term_extractor import academy_agent, monitored_agent
from app.modules.term_extractor.message import Log, Registration, Stats, UserPrompt
from app.modules.term_extractor.user_agent import UserAgent

UID = uuid.UUID(int=7)


class FakeHandle:
    def __init__(self):
        self.sent = []
        self.prompts = []
        self.fail_with = None

    async def message(self, sender, message):
        if self.fail_with:
            raise self.fail_with
        self.sent.append((sender, message))

    async def prompt_user(self, sender, prompt):
        self.prompts.append((sender, prompt))
        return "yes"


class Agent(monitored_agent.MonitoredAgent):
    agent_id = SimpleNamespace(uid=UID)  # shadows Agent.agent_id, which needs a live runtime context


@pytest.fixture
def agent():
    return Agent(FakeHandle())


def run(coro):
    return asyncio.run(coro)


def test_log_handler_forwards_records_but_skips_transport_internals():
    buf = queue.SimpleQueue()
    h = monitored_agent._UserAgentLogHandler(buf)
    h.setFormatter(logging.Formatter("%(name)s|%(message)s"))
    h.emit(logging.LogRecord("app.x", logging.WARNING, "f", 1, "boom", None, None))
    for noisy in ("globus_sdk.auth", "pika.channel", "asyncio", "globus_compute_sdk"):
        h.emit(logging.LogRecord(noisy, logging.INFO, "f", 1, "noise", None, None))
    assert buf.get_nowait() == ("WARNING", "app.x|boom")
    assert buf.empty()


def test_name_defaults_to_class_name(agent):
    assert agent.agent_name == "Agent"
    assert Agent(FakeHandle(), agent_name="named").agent_name == "named"


def test_log_and_prompt_actions_message_the_user_agent(agent):
    agent._agent_uid_str = str(UID)
    run(agent.log("hello", level="ERROR"))
    sender, msg = agent.user_agent.sent[0]
    assert sender == str(UID) and msg == Log(agent_id=str(UID), agent_name="Agent", message="hello", level="ERROR")
    assert run(agent.prompt_user_agent("ok?", ["y", "n"])) == "yes"
    assert agent.user_agent.prompts[0][1] == UserPrompt(agent_id=str(UID), prompt="ok?", responses=["y", "n"])


def test_geolocation_returns_payload_or_empty_on_any_failure(agent, monkeypatch):
    class Resp:
        def __init__(self, bad):
            self.bad = bad

        def raise_for_status(self):
            if self.bad:
                raise RuntimeError("503")

        def json(self):
            return {"loc": "1,2"}

    urls = []
    monkeypatch.setattr(monitored_agent.requests, "get", lambda url, timeout: urls.append((url, timeout)) or Resp(False))
    assert run(agent.get_geolocation()) == {"loc": "1,2"} and urls == [("https://ipinfo.io/json", 6)]
    monkeypatch.setattr(monitored_agent.requests, "get", lambda url, timeout: Resp(True))
    assert run(agent.get_geolocation()) == {}


def test_registration_carries_host_facts_and_geolocation(agent, monkeypatch):
    agent._agent_uid_str = str(UID)

    async def geo():
        return {"loc": "3,4"}

    monkeypatch.setattr(agent, "get_geolocation", geo)
    monkeypatch.setattr(monitored_agent.socket, "getfqdn", lambda: "node.example")
    run(agent.agent_registration())
    reg = agent.user_agent.sent[0][1]
    assert isinstance(reg, Registration)
    assert reg.fqdn == "node.example" and reg.geolocation == {"loc": "3,4"} and reg.gpu == "?"
    assert reg.agent_name == "Agent" and reg.agent_id == str(UID)


def test_gather_stats_reports_process_memory_and_survives_missing_gpu_library(agent):
    agent._agent_uid_str = str(UID)
    stats = run(agent.gather_stats())
    assert isinstance(stats, Stats) and stats.agent_id == str(UID)
    assert stats.memory_rss_mb > 0 and stats.memory_vms_mb > 0 and stats.gpu == []


def test_gather_stats_reads_each_gpu_when_nvml_is_present(agent, monkeypatch):
    import sys

    agent._agent_uid_str = str(UID)
    nvml = SimpleNamespace(
        nvmlInit=lambda: None,
        nvmlDeviceGetCount=lambda: 2,
        nvmlDeviceGetHandleByIndex=lambda i: i,
        nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=10 * (h + 1)),
        nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(used=2 * 1024**2, total=8 * 1024**2),
    )
    monkeypatch.setitem(sys.modules, "pynvml", nvml)
    gpu = run(agent.gather_stats()).gpu
    assert gpu == [
        {"index": 0, "utilization_percent": 10, "memory_used_mb": 2.0, "memory_total_mb": 8.0},
        {"index": 1, "utilization_percent": 20, "memory_used_mb": 2.0, "memory_total_mb": 8.0},
    ]


def test_drain_logs_forwards_until_cancelled(agent):
    agent._agent_uid_str = str(UID)
    agent._log_buf = queue.SimpleQueue()
    agent._log_buf.put(("INFO", "one"))

    async def scenario():
        task = asyncio.create_task(agent._drain_logs())
        for _ in range(100):
            if agent.user_agent.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await task  # cancellation is swallowed by design

    run(scenario())
    assert agent.user_agent.sent[0][1].message == "one"


def test_drain_and_stats_loops_stop_quietly_when_user_agent_terminated(agent):
    agent._agent_uid_str = str(UID)
    agent._log_buf = queue.SimpleQueue()
    agent._log_buf.put(("INFO", "x"))
    agent.user_agent.fail_with = academy.exception.AgentTerminatedError(AgentId(uid=UID, name=None, role="agent"))
    run(agent._drain_logs())
    run(agent._report_stats(report_period_s=0))


def test_drain_and_stats_loops_propagate_unexpected_send_errors(agent):
    agent._agent_uid_str = str(UID)
    agent._log_buf = queue.SimpleQueue()
    agent._log_buf.put(("INFO", "x"))
    agent.user_agent.fail_with = RuntimeError("down")
    with pytest.raises(RuntimeError):
        run(agent._drain_logs())
    with pytest.raises(RuntimeError):
        run(agent._report_stats(report_period_s=0))


def test_startup_installs_handler_and_shutdown_removes_it(agent, monkeypatch):
    async def geo():
        return {}

    monkeypatch.setattr(agent, "get_geolocation", geo)
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level

    async def scenario():
        await agent.agent_on_startup()
        assert agent._log_handler in root.handlers
        await agent.agent_on_shutdown()
        await asyncio.sleep(0)

    try:
        run(scenario())
        assert root.handlers == before
        assert any(isinstance(m, Registration) for _, m in agent.user_agent.sent)
    finally:
        root.setLevel(level)
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)


# ---------------------------------------------------------------------------------- TermExtractorAgent
class FakeOrchestrator:
    last = None

    def __init__(self, **kw):
        self.kw = kw
        self.store = _Store()
        FakeOrchestrator.last = self

    def process_pdf(self, path):
        return 4

    def process_directory(self, path):
        return {"dir": path}

    def process_page(self, text, filename, page):
        return bool(text)


class _Store:
    metadata = {"pages": 3}

    def __len__(self):
        return 9


@pytest.fixture
def term_agent(monkeypatch, tmp_path):
    async def noop(self):
        return None

    monkeypatch.setattr(academy_agent.MonitoredAgent, "agent_on_startup", noop)
    monkeypatch.setattr(academy_agent, "Orchestrator", FakeOrchestrator)
    monkeypatch.setenv("CBORG_BASE_URL", "https://env.example")
    monkeypatch.setenv("CBORG_API_KEY", "env-key")
    return academy_agent.TermExtractorAgent(
        FakeHandle(), "model", "out.json", backend="cborg", log_file=str(tmp_path / "logs" / "a.log")
    )


def test_term_agent_resolves_env_at_construction_and_builds_orchestrator_on_startup(term_agent, tmp_path):
    assert term_agent._cborg_base == "https://env.example" and term_agent._cborg_api_key == "env-key"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        run(term_agent.agent_on_startup())
        assert (tmp_path / "logs").is_dir()
        kw = FakeOrchestrator.last.kw
        assert kw["model"] == "model" and kw["backend"] == "cborg" and kw["cborg_api_key"] == "env-key"
        assert kw["max_workers"] == 2 and kw["output_file"] == "out.json"
    finally:
        for h in list(root.handlers):
            if h not in before:
                h.close()
                root.removeHandler(h)


def test_term_agent_actions_delegate_to_orchestrator_in_executor(term_agent):
    run(_startup_without_logfile(term_agent))
    assert run(term_agent.process_pdf("a.pdf")) == 4
    assert run(term_agent.process_directory("d")) == {"dir": "d"}
    assert run(term_agent.process_page("t", "f", 1)) is True
    assert run(term_agent.get_term_count()) == 9
    status = run(term_agent.get_status())
    assert status == {"pages": 3} and status is not _Store.metadata


async def _startup_without_logfile(agent):
    agent._log_file = None
    await agent.agent_on_startup()


def test_term_agent_defaults_base_url_without_env(monkeypatch):
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    a = academy_agent.TermExtractorAgent(FakeHandle(), "m", "o")
    assert a._cborg_base == "https://api.cborg.lbl.gov" and a._cborg_api_key is None


# ---------------------------------------------------------------------------------- UserAgent
def test_user_agent_routes_messages_to_dashboard_and_blocks_prompt_until_answered(monkeypatch):
    calls = []

    class FakeDash:
        def __init__(self, host, port):
            self.host, self.port = host, port

        def agent_heartbeat(self, s):
            calls.append(("hb", s))

        def push_log(self, s, m):
            calls.append(("log", m.message))

        def push_stats(self, s, m):
            calls.append(("stats", m.cpu_percent))

        def register_agent(self, s, m):
            calls.append(("reg", m.agent_name))

        def push_prompt(self, s, p):
            return "pid"

        def wait_for_response(self, pid):
            return f"answer-{pid}"

        def _snapshot(self):
            return {"agents": {}}

    from app.modules.term_extractor import user_agent as ua

    monkeypatch.setattr(ua, "Dashboard", FakeDash)
    u = UserAgent(host="127.0.0.1", port=1)
    run(u.message("s", Log(agent_id="a", agent_name="n", message="m")))
    run(u.message("s", Stats(agent_id="a", cpu_percent=1.0, memory_rss_mb=1, memory_vms_mb=1)))
    run(u.message("s", Registration("a", "n", "f", "c", "g", "o", "x", "3", {})))
    run(u.message("s", object()))  # unknown payloads only heartbeat
    assert [c[0] for c in calls] == ["hb", "log", "hb", "stats", "hb", "reg", "hb"]
    assert run(u.prompt_user("s", UserPrompt("a", "p", []))) == "answer-pid"
    assert run(u.get_messages()) == {"agents": {}}
