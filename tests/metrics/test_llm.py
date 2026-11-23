"""Model-call metrics at the one choke point (``trace.record_llm_call``): provider, outcome, no model label."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from app.modules import metrics
from app.modules.cborg_errors import CborgIpNotAuthorized
from app.modules.f2w_agent import trace
from app.modules.metrics.llm import outcome_of, provider_of
from app.modules.term_extractor import clients

from .conftest import points


class FakeStatusError(Exception):
    def __init__(self, status: int, body: object = None) -> None:
        super().__init__("provider said no: sk-secret prompt text")
        self.status_code = status
        self.body = body


class FakeTimeout(Exception):
    pass


def calls(reader: Any) -> dict[tuple[str, str], int]:  # noqa: ANN401
    return {(p.attributes["provider"], p.attributes["outcome"]): p.value for p in points(reader, "f2w.llm.calls")}


@pytest.mark.parametrize("error,expected", [
    (None, "ok"),
    (CborgIpNotAuthorized(), "ip_not_authorized"),
    (FakeStatusError(403, {"error": {"type": "ip_not_authorized"}}), "ip_not_authorized"),
    (FakeStatusError(403, {"error": {"type": "forbidden"}}), "auth"),
    (FakeStatusError(401), "auth"),
    (FakeStatusError(429), "rate_limited"),
    (FakeStatusError(504), "timeout"),
    (FakeStatusError(500), "error"),
    (TimeoutError(), "timeout"),
    (requests.Timeout(), "timeout"),
    (FakeTimeout(), "timeout"),
    (RuntimeError("anything else"), "error"),
])
def test_outcome_classification(error: BaseException | None, expected: str) -> None:
    assert outcome_of(error) == expected


def test_timeout_wrapped_with_a_cause_is_still_a_timeout() -> None:
    wrapped = RuntimeError("call exceeded 30s")
    wrapped.__cause__ = TimeoutError()
    assert outcome_of(wrapped) == "timeout"


def test_provider_from_client_objects() -> None:
    assert provider_of(clients.OllamaChatClient("m", "http://x:11434")) == "ollama"
    assert provider_of(type("CBorgClient", (), {})()) == "cborg"
    assert provider_of(object()) == "other"


def test_record_llm_call_counts_provider_outcome_and_duration(reader: Any) -> None:  # noqa: ANN401
    trace.record_llm_call(label="x", model="gpt-secret-model", messages=[{"role": "user", "content": "PROMPT-TEXT"}],
                          response="ok", latency_ms=1500.0, provider="cborg")
    trace.record_llm_call(label="x", model="llama-secret", messages=[], response=None, latency_ms=10.0,
                          error=FakeStatusError(429), provider="ollama")
    trace.record_llm_call(label="x", model=None, messages=[], response=None, latency_ms=10.0,
                          error=CborgIpNotAuthorized(), provider="cborg")
    trace.record_llm_call(label="x", model=None, messages=[], response="r", latency_ms=1.0, provider="gpt-4 turbo")
    assert calls(reader) == {("cborg", "ok"): 1, ("ollama", "rate_limited"): 1, ("cborg", "ip_not_authorized"): 1,
                             ("other", "ok"): 1}
    dur = points(reader, "f2w.llm.call.duration")
    assert list(dur[0].explicit_bounds) == [0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 180, 300]
    blob = repr(points(reader, "f2w.llm.calls")) + repr(dur)
    for needle in ("secret", "PROMPT", "gpt-4", "model"):
        assert needle not in blob
    assert all(set(p.attributes) == {"provider", "outcome"} for p in points(reader, "f2w.llm.calls"))


def test_ollama_client_call_site_records_cborg_refusal_and_success(monkeypatch: pytest.MonkeyPatch, reader: Any) -> None:  # noqa: ANN401
    class Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"message": {"content": "hi"}}

    monkeypatch.setattr(clients.requests, "post", lambda *a, **k: Resp())
    assert clients.OllamaChatClient("m", "http://ollama:11434").chat("p") == "hi"

    def boom(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        raise requests.Timeout("slow")

    monkeypatch.setattr(clients.requests, "post", boom)
    with pytest.raises(requests.Timeout):
        clients.OllamaChatClient("m", "http://ollama:11434").chat("p")
    assert calls(reader) == {("ollama", "ok"): 1, ("ollama", "timeout"): 1}


def test_cborg_path_marks_ip_refusal_and_is_free_when_off(reader: Any) -> None:  # noqa: ANN401
    def refused() -> str:
        raise FakeStatusError(403, {"type": "ip_not_authorized", "message": "your address is 198.51.100.7"})

    with pytest.raises(CborgIpNotAuthorized):
        clients._traced_chat("m", "p", None, 5, refused, provider="cborg")
    assert calls(reader) == {("cborg", "ip_not_authorized"): 1}
    assert "198.51" not in repr(points(reader, "f2w.llm.calls"))


def test_llm_recording_is_a_no_op_when_off() -> None:
    assert not metrics.is_enabled()
    metrics.record_llm_call(provider="cborg", error=RuntimeError("x"), seconds=1.0)
