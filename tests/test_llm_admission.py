"""Tests for the shared LLM admission controller (H1-3) and CBORG fail-closed keys (H1-4)."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from app.modules import cborg_limiter, llm_admission
from app.modules.cborg_http import require_cborg_api_key
from app.modules.llm_admission import AdmissionController, ProviderAdmission, get_controller


@pytest.fixture(autouse=True)
def _fresh_controllers(monkeypatch):
    monkeypatch.delenv("F2W_SHARED_ADMISSION", raising=False)
    llm_admission.reset_controllers()
    yield
    llm_admission.reset_controllers()


class RateLimitError(Exception):
    """Mimics openai.RateLimitError by class name and response headers."""

    def __init__(self, retry_after: str):
        super().__init__("429")
        self.response = SimpleNamespace(status_code=429, headers={"retry-after": retry_after})


def test_sync_and_async_callers_share_one_cap():
    ctl = AdmissionController(2)
    hold = threading.Event()

    def worker():
        with ctl.slot():
            hold.wait(1)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    while ctl.in_flight < 2:
        time.sleep(0.005)

    async def async_caller():
        async with ctl.aslot():
            return ctl.in_flight

    async def scenario():
        task = asyncio.create_task(async_caller())
        await asyncio.sleep(0.05)
        assert not task.done(), "async caller must wait while threads hold both slots"
        hold.set()
        return await task

    assert asyncio.run(scenario()) <= 2
    for t in threads:
        t.join()
    assert ctl.peak == 2 and ctl.in_flight == 0


def test_rate_limit_starts_cooldown_for_every_caller():
    ctl = AdmissionController(5)
    with pytest.raises(RateLimitError):
        with ctl.slot():
            raise RateLimitError("0.2")
    start = time.monotonic()
    with ctl.slot():
        waited = time.monotonic() - start
    assert waited >= 0.15
    assert ctl.try_acquire() is True
    ctl.release()


def test_cancelled_async_waiter_does_not_leak_a_slot():
    ctl = AdmissionController(1)

    async def scenario():
        async with ctl.aslot():
            waiter = asyncio.create_task(ctl.aslot().__aenter__())
            await asyncio.sleep(0.03)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        return ctl.in_flight

    assert asyncio.run(scenario()) == 0


def test_release_without_acquire_raises():
    with pytest.raises(RuntimeError):
        AdmissionController(1).release()


def test_controller_cap_read_on_first_use(monkeypatch):
    monkeypatch.setenv("CBORG_MAX_CONCURRENCY", "3")
    assert get_controller("cborg").cap == 3
    assert get_controller("CBORG") is get_controller("cborg")


def test_provider_admission_facade_uses_shared_controller(monkeypatch):
    monkeypatch.setenv("CBORG_MAX_CONCURRENCY", "1")
    admission = ProviderAdmission()
    with admission.slot("cborg"):
        assert get_controller("cborg").in_flight == 1
    assert get_controller("cborg").in_flight == 0


def test_cborg_limiter_legacy_path_when_flag_off():
    with cborg_limiter.sync_slot():
        assert get_controller("cborg").in_flight == 0


def test_cborg_limiter_routes_to_shared_controller_when_flag_on(monkeypatch):
    monkeypatch.setenv("F2W_SHARED_ADMISSION", "1")
    with cborg_limiter.sync_slot():
        assert get_controller("cborg").in_flight == 1

    async def scenario():
        async with cborg_limiter.async_slot():
            return get_controller("cborg").in_flight

    assert asyncio.run(scenario()) == 1
    assert get_controller("cborg").in_flight == 0
    with cborg_limiter.sync_slot(enabled=False):
        assert get_controller("cborg").in_flight == 0


def test_orchestrator_cborg_openai_joins_cap_only_with_flag(monkeypatch):
    from app.modules.term_extractor.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.backend = "cborg-openai"
    assert orch._uses_cborg_admission() is False
    monkeypatch.setenv("F2W_SHARED_ADMISSION", "1")
    assert orch._uses_cborg_admission() is True
    orch.backend = "ollama"
    assert orch._uses_cborg_admission() is False


def test_extract_terms_cborg_client_takes_slot_with_flag(monkeypatch):
    from app.modules import extract_terms

    client = extract_terms.CBorgChatClient.__new__(extract_terms.CBorgChatClient)
    seen = []
    client._chat_unadmitted = lambda prompt, **kw: seen.append(get_controller("cborg").in_flight) or "ok"
    assert client.chat("p") == "ok"
    monkeypatch.setenv("F2W_SHARED_ADMISSION", "1")
    assert client.chat("p") == "ok"
    assert seen == [0, 1]


# --- H1-4: fail closed without CBORG_API_KEY ----------------------------------

def test_require_cborg_api_key_refuses_openai_fallback(monkeypatch):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-cborg")
    with pytest.raises(RuntimeError, match="CBORG_API_KEY is not set"):
        require_cborg_api_key(None)
    assert require_cborg_api_key("explicit") == "explicit"
    monkeypatch.setenv("CBORG_API_KEY", "from-env")
    assert require_cborg_api_key(None) == "from-env"


@pytest.mark.parametrize("factory", ["sync", "async", "extract_terms"])
def test_cborg_clients_fail_closed_without_key(monkeypatch, factory):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-cborg")
    if factory == "sync":
        from app.modules.term_extractor.clients import CBorgChatClient
    elif factory == "async":
        from app.modules.kg_rag_api import CBorgClient as CBorgChatClient
    else:
        from app.modules.extract_terms import CBorgChatClient
    with pytest.raises(RuntimeError, match="CBORG_API_KEY is not set"):
        CBorgChatClient(model="lbl/cborg-chat")
