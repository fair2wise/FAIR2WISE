"""Free-model policy (owner decision 2026-11-09): the allowlist, the live-price refinement, and one refusal per
CBORG call path. No network, no key, no real model: every refusal happens before a client exists.

The suite runs with ``F2W_MODEL_POLICY_FILE`` pointing at ``tests/fixtures/model_policy_test_allowlist.json``
(the real list plus the fake ids the test doubles use; ``tests/conftest.py``). The unit tests below read the real
packaged file explicitly; the refusal tests use provider-prefixed ids that are in neither list.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import pytest

from app.modules import model_policy as mp
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.model_catalog import ModelListing, list_cborg_models_live
from app.modules.model_policy import (
    DEFAULT_POLICY_PATH,
    ModelPolicyError,
    canonical_model_id,
    load_policy,
    read_policy,
    require_free_cborg_model,
)

REPO = Path(__file__).resolve().parents[1]
PAID = "openai/gpt-5"
PAID_IDS = ("openai/gpt-5", "anthropic/claude-opus-4", "google/gemini-pro", "amazon/gpt-oss-20b", "gemini-flash",
            "claude-sonnet", "gpt-5", "mistral-large")
FREE_IDS = ("lbl/cborg-chat", "cborg-chat", "LBL/CBORG-DEEPTHOUGHT", "lbl/gpt-oss-120b", "gpt-oss-120b",
            "lbl/cborg-coder-ultra", "mistral-large-4", "lbl/gemma-4-thinking", "nomic-embed-text",
            "lbl/nomic-embed-vision", "lbl/glm-5.3-flash", "lbl/inkling-small")


@pytest.fixture
def real_policy():
    return read_policy(DEFAULT_POLICY_PATH)


# ------------------------------------------------------------------ the data file


def test_packaged_file_is_the_owner_editable_allowlist_with_source_and_as_of(real_policy):
    data = json.loads(DEFAULT_POLICY_PATH.read_text())
    assert data["as_of"] == "2026-11-06" and "cborg.lbl.gov" in data["source"]
    assert "2026-11-09" in data["decision"]
    assert real_policy.error is None and real_policy.default_model == "lbl/cborg-chat"
    assert set(data["free_models"]) >= {"lbl/cborg-chat", "lbl/cborg-deepthought", "lbl/gpt-oss-120b",
                                        "lbl/gpt-oss-20b", "lbl/cborg-coder-ultra", "lbl/nomic-embed-text"}
    # the three ids of the H3 B4/C4 free-model run are allowed
    for model in ("lbl/cborg-chat", "lbl/cborg-deepthought", "lbl/gpt-oss-120b"):
        assert real_policy.is_allowed(model)


def test_pyproject_ships_the_allowlist_with_the_package():
    text = (REPO / "pyproject.toml").read_text()
    assert '"f2w.modules" = ["free_cborg_models.json"]' in text


@pytest.mark.parametrize("model", FREE_IDS)
def test_free_ids_pass_in_bare_lbl_and_case_variants(real_policy, model):
    assert real_policy.is_allowed(model)
    assert real_policy.require(model, context="t") == model.strip()


@pytest.mark.parametrize("model", PAID_IDS)
def test_paid_or_unknown_ids_are_refused_with_denied(real_policy, model):
    assert not real_policy.is_allowed(model)
    with pytest.raises(ModelPolicyError) as info:
        real_policy.require(model, context="unit test")
    err = info.value
    assert isinstance(err, ContractError) and err.code is ErrorCode.DENIED and err.retryable is False
    assert err.reason == "model_not_free" and err.model == model
    assert model in str(err) and "unit test" in str(err) and "2026-11-09" in str(err)
    assert "lbl/cborg-chat" in str(err)


def test_canonical_spellings():
    assert canonical_model_id("cborg-chat") == "lbl/cborg-chat"
    assert canonical_model_id(" LBL/Cborg-Chat ") == "lbl/cborg-chat"
    assert canonical_model_id("openai/gpt-5") == "openai/gpt-5"
    assert canonical_model_id("") == "" and canonical_model_id("   ") == ""


def test_alias_resolves_to_its_target(real_policy):
    assert real_policy.canonical("mistral-large-4") == "lbl/cborg-coder-ultra"
    assert real_policy.is_allowed("mistral-large-4")


def test_empty_id_is_refused(real_policy):
    with pytest.raises(ModelPolicyError, match="empty"):
        real_policy.require("", context="t")
    assert real_policy.fallback("") == ("lbl/cborg-chat", True)
    assert real_policy.fallback(None) == ("lbl/cborg-chat", True)
    assert real_policy.fallback("cborg-chat") == ("cborg-chat", False)


def test_filter_keeps_order_and_drops_duplicates_and_paid(real_policy):
    assert real_policy.filter(["cborg-chat", PAID, "lbl/gpt-oss-120b", "cborg-chat"]) == ["cborg-chat", "lbl/gpt-oss-120b"]


# ------------------------------------------------------------------ live price refinement


def _listing(prices, *models):
    return ModelListing("cborg", "live", tuple(models), prices=prices)


def test_live_price_zero_keeps_an_allowlisted_model_and_a_price_above_zero_refuses_it(real_policy):
    free = _listing({"cborg-chat": 0.0}, "cborg-chat")
    assert real_policy.is_allowed("lbl/cborg-chat", free) and real_policy.is_allowed("cborg-chat", free)
    paid_now = _listing({"cborg-chat": 0.000002}, "cborg-chat")
    assert not real_policy.is_allowed("lbl/cborg-chat", paid_now)
    with pytest.raises(ModelPolicyError, match="prices it"):
        real_policy.require("lbl/cborg-chat", context="t", listing=paid_now)
    # the live price never admits a model that is not in the allowlist
    assert not real_policy.is_allowed(PAID, _listing({PAID: 0.0}, PAID))
    # no price known: the allowlist alone decides
    assert real_policy.is_allowed("cborg-chat", _listing({}, "cborg-chat"))


def test_listing_price_lookup_matches_bare_and_lbl_spellings():
    listing = _listing({"cborg-chat": 1.5, "lbl/gpt-oss-120b": 0.0}, "cborg-chat")
    assert listing.price_of("lbl/cborg-chat") == 1.5 and listing.price_of("CBORG-CHAT") == 1.5
    assert listing.price_of("gpt-oss-120b") == 0.0 and listing.price_of("other") is None


def test_live_listing_extracts_prices_when_the_payload_carries_them():
    class Models:
        def list(self, timeout):
            return {"data": [
                {"id": "cborg-chat", "object": "model"},                                   # no price
                {"id": "cborg-coder", "pricing": {"prompt": "0", "completion": 0}},      # free
                {"id": "gpt-5", "model_info": {"input_cost_per_token": 0.00001}},       # paid (LiteLLM shape)
                {"id": "claude-x", "input_cost_per_token": "$0.000003", "input": "text"},  # paid, modality ignored
            ]}

    class Client:
        models = Models()

    import contextlib

    listing = list_cborg_models_live(client=Client(), slot=contextlib.nullcontext)
    assert listing.available and listing.models == ("cborg-chat", "cborg-coder", "claude-x", "gpt-5")
    assert listing.prices == {"cborg-coder": 0.0, "gpt-5": 0.00001, "claude-x": 0.000003}
    assert listing.price_of("cborg-chat") is None
    assert listing.to_dict()["models"] == list(listing.models)  # payload shape unchanged


def test_free_cborg_models_offers_only_the_free_part_of_a_live_list(monkeypatch):
    monkeypatch.setenv(mp.POLICY_FILE_VAR, str(DEFAULT_POLICY_PATH))
    live = _listing({"cborg-coder": 2.0}, "cborg-chat", "cborg-coder", PAID, "gpt-oss-120b")
    assert mp.free_cborg_models(live) == ["cborg-chat", "gpt-oss-120b"]
    unavailable = ModelListing("cborg", "unavailable", error="no key")
    assert mp.free_cborg_models(unavailable) == list(read_policy(DEFAULT_POLICY_PATH).free_models)


# ------------------------------------------------------------------ loading: owner edits, fail closed


def test_missing_or_invalid_file_is_an_empty_policy_that_refuses_everything(tmp_path):
    missing = read_policy(tmp_path / "nope.json")
    assert missing.error and missing.allowed == frozenset()
    with pytest.raises(ModelPolicyError, match="unusable"):
        missing.require("lbl/cborg-chat", context="t")
    bad = tmp_path / "bad.json"
    bad.write_text('{"free_models": "lbl/cborg-chat"}')
    assert read_policy(bad).error and not read_policy(bad).is_allowed("lbl/cborg-chat")
    bad.write_text('{"free_models": ["lbl/x"], "default_model": "lbl/y"}')
    assert "default_model" in (read_policy(bad).error or "")


def test_env_override_and_edit_without_code_change_is_picked_up(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"source": "s", "as_of": "d", "default_model": "lbl/a", "free_models": ["lbl/a"]}))
    monkeypatch.setenv(mp.POLICY_FILE_VAR, str(path))
    assert mp.policy_path() == path
    assert mp.is_free_cborg_model("a") and not mp.is_free_cborg_model("b")
    assert mp.default_free_model() == "lbl/a"
    # the owner edits the file: the next call sees the change (mtime-keyed cache)
    import os
    import time

    path.write_text(json.dumps({"source": "s", "as_of": "d", "default_model": "lbl/a", "free_models": ["lbl/a", "lbl/b"]}))
    os.utime(path, ns=(time.time_ns() + 10_000_000_000, time.time_ns() + 10_000_000_000))
    assert mp.is_free_cborg_model("b")
    assert load_policy(path) is load_policy(path)  # cached while unchanged


def test_refusal_message_never_carries_a_key(real_policy, monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", "sk-secret-key-value-123")
    with pytest.raises(ModelPolicyError) as info:
        real_policy.require(PAID, context="t")
    assert "sk-secret" not in str(info.value)


# ------------------------------------------------------------------ refusal per call path (no client, no request)


def _real(monkeypatch):
    monkeypatch.setenv(mp.POLICY_FILE_VAR, str(DEFAULT_POLICY_PATH))


def _no_client(monkeypatch, module, name):
    def boom(*a, **k):
        raise AssertionError("a client was built for a refused model")

    monkeypatch.setattr(module, name, boom)


def test_path_term_extractor_chat_client(monkeypatch):
    _real(monkeypatch)
    from app.modules.term_extractor import clients

    _no_client(monkeypatch, clients.openai, "OpenAI")
    with pytest.raises(ModelPolicyError, match="CBORG chat client"):
        clients.CBorgChatClient(PAID, api_key="k")
    with pytest.raises(ModelPolicyError):
        clients.make_chat_client("cborg", "anthropic/claude-x", cborg_api_key="k")
    # Ollama is unaffected
    assert clients.make_chat_client("ollama", "anthropic/claude-x").model == "anthropic/claude-x"


def test_path_legacy_extract_terms_cli_client(monkeypatch):
    _real(monkeypatch)
    from app.modules.legacy import extract_terms_cli as legacy

    _no_client(monkeypatch, legacy.openai, "OpenAI")
    with pytest.raises(ModelPolicyError, match="legacy extraction CLI"):
        legacy.CBorgChatClient("google/gemini-pro", api_key="k")


def test_path_kg_rag_client(monkeypatch):
    _real(monkeypatch)
    from app.modules import kg_rag_api

    _no_client(monkeypatch, kg_rag_api.openai, "AsyncOpenAI")
    with pytest.raises(ModelPolicyError, match="KG-RAG chat client"):
        kg_rag_api.CBorgClient(model=PAID, api_key="k")


def test_path_extraction_orchestrator_model(monkeypatch):
    _real(monkeypatch)
    from app.modules.term_extractor import orchestrator as orch_mod
    from app.modules.term_extractor.orchestrator import Orchestrator

    _no_client(monkeypatch, orch_mod, "ChatOpenAI")
    monkeypatch.setattr(orch_mod, "require_cborg_api_key", lambda k: (_ for _ in ()).throw(AssertionError("key read")))
    o = Orchestrator.__new__(Orchestrator)
    o.model, o.temperature, o.ollama_url, o.cborg_base, o.cborg_api_key = PAID, 0.0, "http://o:1/", "https://c", None
    o.backend = "cborg"
    with pytest.raises(ModelPolicyError, match="extraction model"):
        o._build_llm()


def test_path_f2w_run_cli_refuses_before_running(monkeypatch, tmp_path, caplog):
    _real(monkeypatch)
    from app.modules.commands import run as run_cmd

    (tmp_path / "pdfs").mkdir()
    (tmp_path / "pdfs" / "a.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("CBORG_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--pdf-dir", str(tmp_path / "pdfs"), "--model", PAID,
                                      "--log-file", str(tmp_path / "run.log"), "--dry-run"])
    with pytest.raises(SystemExit) as info:
        run_cmd.main()
    assert info.value.code == 1
    assert "free-model policy" in (tmp_path / "run.log").read_text()
    # the default model is the policy's default free model (no paid default anywhere)
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--pdf-dir", str(tmp_path / "pdfs"),
                                      "--log-file", str(tmp_path / "run2.log"), "--dry-run"])
    run_cmd.main()
    assert "Model    : lbl/cborg-chat" in (tmp_path / "run2.log").read_text()


def test_path_library_extract_job_fails_before_any_orchestrator(monkeypatch, tmp_path):
    _real(monkeypatch)
    from app.modules.contracts.jobs import JobState
    from tests.library.test_library_extract import World

    world = World(tmp_path)
    world.put("a.pdf", "P3HT|Polymer")
    handle, state = world.run(world.params(["a.pdf"], model=PAID))
    assert state is JobState.FAILED
    assert "free-model policy" in world.job_error(handle)
    assert world.factory_calls == []


def test_path_library_models_route_offers_only_free_ids(monkeypatch, tmp_path):
    _real(monkeypatch)
    from app.modules.library import bundles as B
    from app.modules.library.kg_service import KgLibrary
    from app.modules.model_catalog import CachedModelCatalog

    listing = _listing({"cborg-coder": 3.0}, "cborg-chat", "cborg-coder", PAID, "gpt-oss-20b")
    kg = KgLibrary(B.BundleStore(tmp_path / "kgs"), catalog=CachedModelCatalog(lambda: listing))
    body = kg.models()
    assert body["models"] == ["cborg-chat", "gpt-oss-20b"] and body["model_policy"]["as_of"] == "2026-11-06"


def test_path_judge_model_setting(monkeypatch, tmp_path):
    _real(monkeypatch)
    from app.modules.model_catalog import CachedModelCatalog
    from app.modules.validation_settings import JudgeModelService, JudgeModelStore, judge_settings_path
    from tests.literature.lit_fakes import local_ctx

    listing = _listing({}, "cborg-chat", "cborg-deepthought", PAID)
    store = JudgeModelStore(judge_settings_path(tmp_path))
    svc = JudgeModelService(store, CachedModelCatalog(lambda: listing))
    ctx = local_ctx(capabilities=frozenset({"settings.global"}))
    with pytest.raises(ModelPolicyError, match="judge model"):
        svc.update(ctx, PAID)
    assert store.read()["judge_model"] is None
    view = svc.view(ctx)
    assert view["available_models"] == ["cborg-chat", "cborg-deepthought"]
    assert view["judge_model_allowed"] is None and view["model_policy"]["default_model"] == "lbl/cborg-chat"
    # a paid id saved before the policy is flagged, never offered
    store.write(PAID, updated_by="old")
    view = svc.view(ctx)
    assert view["judge_model_allowed"] is False and PAID not in view["available_models"]
    assert svc.update(ctx, "cborg-deepthought")["judge_model_allowed"] is True


def test_path_judge_model_route_returns_denied(monkeypatch, tmp_path):
    _real(monkeypatch)
    sys.path.insert(0, str(REPO / "tests" / "api_v1"))
    from _v1_support import build_local

    from app.modules.model_catalog import CachedModelCatalog
    from app.modules.validation_settings import JudgeModelService, JudgeModelStore, judge_settings_path

    listing = _listing({}, "cborg-chat", PAID)
    svc = JudgeModelService(JudgeModelStore(judge_settings_path(tmp_path / "wd")), CachedModelCatalog(lambda: listing))
    app = build_local(monkeypatch, tmp_path, judge_settings=svc)
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": PAID})
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "denied" and PAID in resp.json()["message"]
    assert app.client().get("/v1/validation/judge-model").json()["available_models"] == ["cborg-chat"]


def test_path_labeling_cli_refuses_labeller_judge_and_model_under_test(monkeypatch, tmp_path, capsys):
    _real(monkeypatch)
    from evals.binary.labeling import cli
    from tests.evals_binary import label_fakes as F

    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.delenv("F2W_DOTENV", raising=False)
    F.install_synthetic(monkeypatch, tmp_path)
    calls = []

    def factory(model):
        calls.append(model)
        return F.fake_factory()(model)

    base = ["--paper", "A", "--papers-root", str(tmp_path / "papers"), "--out", str(tmp_path / "labels")]
    lister = F.lister("lbl/gpt-oss-120b", "lbl/cborg-deepthought", PAID)
    for argv in (["--model", PAID, *base],
                 ["--model", "lbl/gpt-oss-120b", "--judge-model", "anthropic/claude-x", *base],
                 ["--model", "lbl/gpt-oss-120b", "--model-under-test", "google/gemini-pro", *base]):
        assert cli.main(argv, client_factory=factory, lister=lister) == 2
        assert "free-model policy" in capsys.readouterr().err
    assert calls == []
    # --list-models prints only the free part of the live list
    monkeypatch.setenv("CBORG_API_KEY", "test-only-key")
    assert cli.main(["--list-models"], lister=lister) == 0
    out = capsys.readouterr().out
    assert PAID not in out and "lbl/gpt-oss-120b" in out and "free-model policy" in out


def test_path_binary_runner_refuses_a_paid_judge_or_model_under_test(monkeypatch, tmp_path, capsys):
    _real(monkeypatch)
    from evals.binary import runner

    for extra in (["--judge-model", PAID], ["--model-under-test", "anthropic/claude-x"]):
        assert runner.main(["--out-dir", str(tmp_path / "out"), *extra]) == 2
        assert "free-model policy" in capsys.readouterr().err


def test_path_admitted_chat_helper_agents(monkeypatch):
    _real(monkeypatch)
    from app.modules.agent_runtime.llm_gate import LlmModelRefused
    from app.modules.f2w_agent.admitted_chat import legacy_admitted_chat
    from app.modules.term_extractor import clients

    _no_client(monkeypatch, clients, "make_chat_client")

    class Budget:
        reserved = 0

        def reserve(self, *, model, label):
            self.reserved += 1
            return "r"

        def settle(self, reservation, *, cost_usd, served):
            raise AssertionError("settled")

    budget = Budget()
    chat = legacy_admitted_chat("cborg", PAID, budget)
    with pytest.raises(LlmModelRefused, match="free-model policy"):
        chat.complete("hi", timeout=1, label="orchestrator-classify")
    assert budget.reserved == 0  # refused before the reservation, the slot and the client
    # a local backend is not checked by the policy (the client factory decides)
    local = legacy_admitted_chat("ollama", PAID, budget)
    monkeypatch.setattr(clients, "make_chat_client", lambda **kw: type("C", (), {
        "chat": lambda self, prompt, **k: "ok"})())
    assert local.complete("hi", timeout=1, label="x") == "ok"


def test_path_agent_settings_model_under_test_falls_back_to_the_free_default(monkeypatch, tmp_path):
    _real(monkeypatch)
    from app.modules.f2w_agent import api as api_mod
    from tests.test_agent_settings import _settings_client

    assert api_mod.default_runtime_model("cborg", PAID) == "lbl/cborg-chat"
    assert api_mod.default_runtime_model("cborg", "cborg-deepthought") == "cborg-deepthought"
    assert api_mod.default_runtime_model("ollama", PAID) == PAID  # local backends are unaffected
    monkeypatch.setenv("KG_RAG_CBORG_MODEL", "google/gemini-pro")
    assert api_mod.default_runtime_model("cborg") == "lbl/cborg-chat"
    monkeypatch.delenv("KG_RAG_CBORG_MODEL")
    offered = api_mod.list_cborg_models(current_model=PAID)
    assert PAID not in offered and "lbl/cborg-chat" in offered
    assert all(mp.is_free_cborg_model(m) for m in offered)

    client = _settings_client(tmp_path, monkeypatch)
    body = client.get("/settings").json()
    assert all(mp.is_free_cborg_model(m) for m in body["available_cborg_models"])
    put = client.put("/settings", json={"backend": "cborg", "model": PAID})
    assert put.status_code == 200  # frozen route: shape unchanged, value filtered
    assert put.json()["model"] == "lbl/cborg-chat"
    assert client.get("/settings").json()["model"] == "lbl/cborg-chat"


# ------------------------------------------------------------------ static: no paid id as a default


PAID_PATTERN = re.compile(r"openai/|anthropic/|google/|amazon/|gemini|claude|gpt-5|gpt-6", re.IGNORECASE)
DEFAULT_MARKERS = re.compile(r"default|DEFAULT|environ\.get\(|=\s*[\"']|:\s*str\s*=|model\s*=\s*[\"']|model:\s*[\"']")
ALIAS_ENTRY = re.compile(r"""^\s*["'][^"']+["']\s*:\s*["'][^"']+["'],?\s*$""")  # alias-map rows, not defaults


def _source_files():
    for base, suffixes in ((REPO / "app", (".py",)), (REPO / "ui" / "src", (".ts", ".tsx"))):
        for path in base.rglob("*"):
            if path.suffix not in suffixes or "graphify-out" in path.parts or "node_modules" in path.parts:
                continue
            if "/tests/" in str(path) or path.name.endswith((".test.ts", ".test.tsx")):
                continue
            yield path


def test_no_paid_provider_model_id_is_a_default_in_app_or_ui():
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not PAID_PATTERN.search(line):
                continue
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "/*")) or ALIAS_ENTRY.match(line):
                continue  # comments and the legacy alias maps (keys, not defaults; they are refused anyway)
            if DEFAULT_MARKERS.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {stripped}")
    assert offenders == [], "\n".join(offenders)


def test_known_paid_defaults_were_replaced():
    run_py = (REPO / "app/modules/commands/run.py").read_text()
    assert "amazon/gpt-oss-20b" not in run_py
    pipeline = (REPO / "app/run_pipeline_cborg.py").read_text()
    assert "google/gemini-flash-lite" not in pipeline and '"lbl/cborg-chat"' in pipeline
