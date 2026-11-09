"""Bounded LLM calls: a hung transport or a trickling stream ends at a deadline (no network)."""

from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest

from app.modules import cborg_http
from app.modules.term_extractor import clients


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text), message=None)])


def test_call_with_deadline_returns_the_result_and_reraises_errors():
    assert cborg_http.call_with_deadline(lambda: 7, 5) == 7
    with pytest.raises(ValueError, match="boom"):
        cborg_http.call_with_deadline(_raise_value_error, 5)


def _raise_value_error() -> int:
    raise ValueError("boom")


def test_a_hung_call_ends_at_the_deadline_without_blocking_the_caller():
    hang = threading.Event()  # a stubbed transport that never answers
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        cborg_http.call_with_deadline(lambda: hang.wait(30), 0.1)
    assert time.monotonic() - started < 2
    hang.set()


def test_a_trickling_stream_is_cut_at_the_total_deadline():
    def trickle():
        while True:
            time.sleep(0.01)
            yield _chunk("x")

    with pytest.raises(TimeoutError):
        cborg_http.collect_stream_text(trickle(), deadline=time.monotonic() + 0.1)
    assert cborg_http.collect_stream_text([_chunk("a"), _chunk("b")], deadline=time.monotonic() + 5) == "ab"


def test_timeouts_have_a_finite_per_phase_bound():
    limits = cborg_http.llm_timeout()
    assert limits.connect == cborg_http.LLM_CONNECT_TIMEOUT and limits.read == cborg_http.LLM_READ_TIMEOUT
    assert cborg_http.LLM_MAX_RETRIES >= 0 and cborg_http.LLM_TOTAL_DEADLINE > cborg_http.LLM_READ_TIMEOUT


def test_the_cborg_client_bounds_a_stream_that_never_ends(monkeypatch):
    def endless():
        while True:
            time.sleep(0.005)
            yield _chunk("x")

    client = clients.CBorgChatClient.__new__(clients.CBorgChatClient)
    client.model = "m"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: endless())))
    monkeypatch.setattr(clients, "LLM_MAX_RETRIES", 0)
    with pytest.raises(TimeoutError):
        client.chat("hi", timeout=0.1)


def _llm_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "llm-call" and t.is_alive())


def test_abandoned_calls_are_bounded_and_the_slots_come_back(monkeypatch):
    monkeypatch.setattr(cborg_http, "LLM_MAX_ABANDONED", 3)
    hang = threading.Event()
    before = _llm_threads()
    for _ in range(3):
        with pytest.raises(TimeoutError, match="deadline"):
            cborg_http.call_with_deadline(lambda: hang.wait(30), 0.05)
    assert _llm_threads() == before + 3
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="abandoned"):  # refused at once: no fourth thread is spawned
        cborg_http.call_with_deadline(lambda: hang.wait(30), 5)
    assert time.monotonic() - started < 1 and _llm_threads() == before + 3
    hang.set()  # the hung transports finally return: their slots are free again
    end = time.monotonic() + 5
    while _llm_threads() > before and time.monotonic() < end:
        time.sleep(0.01)
    assert cborg_http.call_with_deadline(lambda: 7, 5) == 7
    assert cborg_http.abandoned_calls() == 0


def test_a_call_that_finishes_in_time_never_counts_as_abandoned(monkeypatch):
    monkeypatch.setattr(cborg_http, "LLM_MAX_ABANDONED", 1)
    for _ in range(5):
        assert cborg_http.call_with_deadline(lambda: 1, 5) == 1
    assert cborg_http.abandoned_calls() == 0
