"""CBORG 403 ``ip_not_authorized``: recognised in every shape, shown as one fixed message, never retried.

No network: the OpenAI SDK runs over ``httpx.MockTransport`` so the real SDK retry logic is what is
counted. The refusal body carries a public address on purpose; it must appear nowhere in what the app
returns, raises or logs.
"""

from __future__ import annotations

import logging

import httpx
import openai
import pytest

from app.modules.cborg_errors import (
    IP_NOT_AUTHORIZED_MESSAGE,
    IP_NOT_AUTHORIZED_REASON,
    CborgIpNotAuthorized,
    as_ip_not_authorized,
    is_ip_not_authorized,
)
from app.modules.contracts.errors import ErrorCode
from app.modules.f2w_agent.retrieval_agent import describe_llm_failure
from app.modules.model_catalog import CachedModelCatalog, list_cborg_models_live

ADDRESSES = ("203.0.113.77", "2001:db8::1234")


def _body(shape: str) -> dict:
    detail = f"request from IP address {ADDRESSES[0]} / {ADDRESSES[1]} is not authorized"
    if shape == "flat":
        return {"type": "ip_not_authorized", "message": detail}
    if shape == "nested":
        return {"error": {"type": "ip_not_authorized", "message": detail, "code": "403"}}
    if shape == "code":
        return {"error": {"code": "ip_not_authorized", "message": detail}}
    raise AssertionError(shape)


def _openai(handler):
    return openai.OpenAI(api_key="k", base_url="https://cborg.invalid",
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=2)


def _refusing(shape: str = "nested"):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(403, json=_body(shape))

    return _openai(handler), calls


@pytest.mark.parametrize("shape", ["flat", "nested", "code"])
def test_recognised_from_the_real_sdk_error_in_each_body_shape(shape):
    client, calls = _refusing(shape)
    with pytest.raises(openai.PermissionDeniedError) as err:
        client.models.list()
    assert is_ip_not_authorized(err.value)
    assert len(calls) == 1  # the SDK does not retry a 403 (max_retries=2): terminal for the call
    typed = as_ip_not_authorized(err.value)
    assert isinstance(typed, CborgIpNotAuthorized)
    assert typed.code is ErrorCode.UNAVAILABLE and typed.retryable is False
    assert typed.reason == IP_NOT_AUTHORIZED_REASON
    assert str(typed) == IP_NOT_AUTHORIZED_MESSAGE
    assert not any(a in str(typed) for a in ADDRESSES)


def test_other_failures_are_not_mistaken_for_it():
    def other(status: int, body: dict) -> Exception:
        response = httpx.Response(status, json=body, request=httpx.Request("GET", "https://x"))
        cls = openai.PermissionDeniedError if status == 403 else openai.RateLimitError
        return cls("m", response=response, body=body)

    assert not is_ip_not_authorized(other(403, {"error": {"type": "key_not_allowed"}}))
    assert not is_ip_not_authorized(other(429, {"error": {"type": "ip_not_authorized"}}))  # wrong status
    assert not is_ip_not_authorized(RuntimeError("boom"))
    assert as_ip_not_authorized(RuntimeError("boom")) is None
    # last resort: the exception text of a 403 that carries no parsed body
    err = openai.PermissionDeniedError("Error code: 403 - ip_not_authorized",
                                       response=httpx.Response(403, request=httpx.Request("GET", "https://x")),
                                       body=None)
    assert is_ip_not_authorized(err)


def test_model_listing_reports_the_fixed_message_once_and_never_the_address(caplog):
    client, calls = _refusing()
    with caplog.at_level(logging.DEBUG):
        listing = list_cborg_models_live(client=client, slot=lambda: _NullSlot())
    assert not listing.available and listing.models == ()
    assert listing.error == IP_NOT_AUTHORIZED_MESSAGE
    assert listing.error_code == IP_NOT_AUTHORIZED_REASON
    assert listing.to_dict()["error_code"] == IP_NOT_AUTHORIZED_REASON
    assert len(calls) == 1
    assert not any(a in caplog.text or a in repr(listing) for a in ADDRESSES)


def test_cached_catalog_does_not_ask_again_within_the_failure_ttl():
    client, calls = _refusing()
    now = [0.0]
    catalog = CachedModelCatalog(lambda: list_cborg_models_live(client=client, slot=lambda: _NullSlot()),
                                 clock=lambda: now[0])
    assert catalog.get().error_code == IP_NOT_AUTHORIZED_REASON
    assert catalog.get().error_code == IP_NOT_AUTHORIZED_REASON
    assert len(calls) == 1


def test_chat_failure_text_is_the_specific_message():
    client, _ = _refusing()
    with pytest.raises(openai.PermissionDeniedError) as err:
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    text = describe_llm_failure(err.value)
    assert IP_NOT_AUTHORIZED_MESSAGE in text and not any(a in text for a in ADDRESSES)
    assert describe_llm_failure(CborgIpNotAuthorized()).startswith(IP_NOT_AUTHORIZED_MESSAGE)


def test_sync_chat_client_raises_the_typed_refusal_without_a_cause_chain(monkeypatch):
    from app.modules.term_extractor import clients

    monkeypatch.setattr(clients, "require_free_cborg_model", lambda m, context="": m)
    monkeypatch.setattr(clients, "record_llm_call", lambda **kw: None)
    client, calls = _refusing()
    chat = clients.CBorgChatClient("lbl/cborg-chat", api_key="k")
    chat.client = client
    with pytest.raises(CborgIpNotAuthorized) as err:
        chat.chat("hello", timeout=5)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert len(calls) == 1


def test_async_kg_rag_client_raises_the_typed_refusal(monkeypatch):
    import asyncio

    from app.modules import kg_rag_api

    monkeypatch.setattr("app.modules.model_policy.require_free_cborg_model", lambda m, context="": m)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(403, json=_body("nested"))

    chat = kg_rag_api.CBorgClient(model="lbl/cborg-chat", api_key="k")
    chat.client = openai.AsyncOpenAI(api_key="k", base_url="https://cborg.invalid", max_retries=2,
                                     http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(CborgIpNotAuthorized):
        asyncio.run(chat.chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 1


def test_orchestrator_stops_at_the_first_refusal(monkeypatch, caplog):
    from app.modules.term_extractor import orchestrator as mod

    calls = {"graph": 0, "fallback": 0}

    class Graph:
        def invoke(self, _payload):
            calls["graph"] += 1
            raise CborgIpNotAuthorized()

    orch = mod.Orchestrator.__new__(mod.Orchestrator)
    orch.graph = Graph()
    orch.store = []
    orch.schema_helper = type("S", (), {"get_schema_context_for_llm": lambda self: ""})()
    orch._fallback_json_extract = lambda *a, **k: calls.__setitem__("fallback", calls["fallback"] + 1) or False  # type: ignore[method-assign]
    monkeypatch.setattr(mod, "sync_slot", lambda enabled=False: _NullSlot())
    monkeypatch.setattr(mod, "build_page_prompt", lambda *a, **k: "p")
    orch._uses_cborg_admission = lambda: False  # type: ignore[method-assign]
    text = "word " * 40
    with caplog.at_level(logging.DEBUG):
        assert orch.process_page(text, "a.pdf", 0) is False
        assert orch.process_page(text, "a.pdf", 1) is False
    assert calls == {"graph": 1, "fallback": 0}  # no fallback call, no second page
    assert isinstance(orch.refusal, CborgIpNotAuthorized)
    assert caplog.text.count(IP_NOT_AUTHORIZED_MESSAGE) == 1


def test_job_stage_ends_at_the_first_refusal_instead_of_failing_every_unit():
    from app.modules.jobs.stages import WorkUnit, chunked_stage

    seen: list[str] = []
    failed: list[str] = []

    class Sc:
        def guard(self): ...
        def unit(self, *_a): return None
        def progress(self, **_k): ...
        def fail_unit(self, uid, *_a): failed.append(uid)
        def save_unit(self, *_a): ...

    def process(_sc, unit):
        seen.append(unit.unit_id)
        raise CborgIpNotAuthorized()

    run = chunked_stage(lambda _sc: [WorkUnit(unit_id=f"u{i}", unit_key="k", payload=i) for i in range(5)], process)
    with pytest.raises(CborgIpNotAuthorized):
        run(Sc())  # type: ignore[arg-type]
    assert seen == ["u0"] and failed == []  # the job ends failed with the refusal text; no other paper is tried


class _NullSlot:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
