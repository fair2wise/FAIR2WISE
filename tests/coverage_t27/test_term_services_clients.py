"""Term-extractor services bundle and chat clients (HTTP and SDK replaced by fakes)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.modules.term_extractor import clients, services
from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.store import TermStore


class _FakeValidator:
    def __init__(self, api_key=None):
        self.api_key = api_key


class _FakeChebi:
    def __init__(self, path):
        if path.endswith("bad.obo"):
            raise ValueError("unparseable")
        self.path = path


@pytest.fixture
def patched_services(monkeypatch):
    monkeypatch.setattr(services, "ChemicalFormulaValidator", _FakeValidator)
    monkeypatch.setattr(services, "ChebiOboLookup", _FakeChebi)
    monkeypatch.delenv("MP_API_KEY", raising=False)


def test_build_services_key_precedence_and_blank_key(patched_services, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        svc = services.build_services()
    assert svc.formula_checker.api_key is None and svc.chebi_lookup is None
    assert "MP_API_KEY not set" in caplog.text
    monkeypatch.setenv("MP_API_KEY", "env-key")
    assert services.build_services().formula_checker.api_key == "env-key"
    assert services.build_services(mp_api_key="arg-key").formula_checker.api_key == "arg-key"


def test_build_services_loads_chebi_only_when_file_exists_and_parses(patched_services, tmp_path, caplog):
    good = tmp_path / "chebi.obo"
    good.write_text("x")
    bad = tmp_path / "bad.obo"
    bad.write_text("x")
    assert services.build_services(chebi_obo_path=str(good)).chebi_lookup.path == str(good)
    assert services.build_services(chebi_obo_path=str(tmp_path / "missing.obo")).chebi_lookup is None
    with caplog.at_level(logging.WARNING):
        assert services.build_services(chebi_obo_path=str(bad)).chebi_lookup is None
    assert "Failed to load ChEBI ontology" in caplog.text


def _store(tmp_path, *names):
    store = TermStore(str(tmp_path / "terms.json"))
    for n in names:
        store.upsert(TermRecord(term=n, definition="d", category="Chemical"))
    return store


def test_extract_and_attach_properties_attaches_once_and_marks_failed_conversions(tmp_path):
    store = _store(tmp_path, "Na3Bi")
    svc = SimpleNamespace(
        prop_extractor=services.PhysicalPropertyExtractor(),
        prop_normalizer=services.PropertyNormalizer(),
    )
    text = "The density of Na3Bi is 6.7 g/cm3. The band gap of Na3Bi is 1.9 furlong."
    assert services.extract_and_attach_properties(text, store, svc) is True
    props = {p.property: p for p in store.get(TermStore.normalize("Na3Bi")).properties}
    assert props["density"].value == pytest.approx(6700.0) and props["density"].unit == "kg/m3"
    assert props["density"].verified is True
    assert props["band_gap"].verified is False and props["band_gap"].value is None and props["band_gap"].unit == ""
    # identical text again attaches nothing new
    assert services.extract_and_attach_properties(text, store, svc) is False


def test_extract_and_attach_properties_empty_cases(tmp_path):
    svc = SimpleNamespace(prop_extractor=services.PhysicalPropertyExtractor(),
                          prop_normalizer=services.PropertyNormalizer())
    assert services.extract_and_attach_properties("anything", _store(tmp_path), svc) is False
    store = _store(tmp_path, "Na3Bi")
    assert services.extract_and_attach_properties("Nothing about it.", store, svc) is False


# ----------------------------------------------------------------------------- clients
@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(clients, "record_llm_call", lambda **kw: calls.append(kw))
    return calls


def test_ollama_client_posts_payload_and_traces(monkeypatch, recorded):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "hello"}}

    def post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return Resp()

    monkeypatch.setattr(clients.requests, "post", post)
    c = clients.make_chat_client("OLLAMA", "m1", ollama_url="http://fake:1/")
    assert c.chat("hi", temperature=0.3, timeout=7, trace_label="lbl") == "hello"
    assert seen["url"] == "http://fake:1/api/chat" and seen["timeout"] == 7
    assert seen["json"]["options"] == {"temperature": 0.3} and seen["json"]["stream"] is False
    assert recorded[0]["label"] == "lbl" and recorded[0]["response"] == "hello" and recorded[0]["error"] is None


def test_ollama_client_missing_content_is_empty_and_errors_are_traced_then_raised(monkeypatch, recorded):
    class Resp:
        def __init__(self, fail):
            self.fail = fail

        def raise_for_status(self):
            if self.fail:
                raise RuntimeError("500")

        def json(self):
            return {"message": {"content": None}}

    state = {"fail": False}
    monkeypatch.setattr(clients.requests, "post", lambda *a, **k: Resp(state["fail"]))
    c = clients.OllamaChatClient("m", "http://x")
    assert c.chat("p") == ""
    assert recorded[-1]["label"] == "chat"
    state["fail"] = True
    with pytest.raises(RuntimeError):
        c.chat("p")
    assert isinstance(recorded[-1]["error"], RuntimeError) and recorded[-1]["response"] is None


def test_cborg_client_streams_through_admission_slot(monkeypatch, recorded):
    created = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            created.update(kw)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            created["request"] = kw
            return iter(["chunk"])

    monkeypatch.setattr(clients.openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(clients, "require_cborg_api_key", lambda key: key or "resolved")
    monkeypatch.setattr(clients, "openai_http_kwargs", lambda asynchronous: {"http_client": "H"})
    monkeypatch.setattr(clients, "collect_stream_text", lambda resp: "".join(resp))
    c = clients.make_chat_client("cborg-openai", "gpt", cborg_base="https://base.example/v1/")
    assert created["base_url"] == "https://base.example/v1" and created["api_key"] == "resolved"
    assert created["http_client"] == "H"
    assert c.chat("hi", temperature=0.5, timeout=9) == "chunk"
    assert created["request"] == {"model": "gpt", "messages": [{"role": "user", "content": "hi"}],
                                  "temperature": 0.5, "timeout": 9, "stream": True}
    assert recorded[0]["timeout"] == 9


def test_cborg_base_url_falls_back_to_env_then_default(monkeypatch):
    seen = []
    monkeypatch.setattr(clients.openai, "OpenAI", lambda **kw: seen.append(kw["base_url"]))
    monkeypatch.setattr(clients, "require_cborg_api_key", lambda key: "k")
    monkeypatch.setattr(clients, "openai_http_kwargs", lambda asynchronous: {})
    monkeypatch.setenv("CBORG_BASE_URL", "https://env.example/")
    clients.CBorgChatClient("m")
    monkeypatch.delenv("CBORG_BASE_URL")
    clients.CBorgChatClient("m")
    assert seen == ["https://env.example", "https://api.cborg.lbl.gov"]


def test_make_chat_client_rejects_unknown_backend_and_defaults_to_ollama():
    assert isinstance(clients.make_chat_client("", "m"), clients.OllamaChatClient)
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        clients.make_chat_client("bedrock", "m")
