"""Orchestrator helpers, fallback extraction, PDF/directory drivers and construction (all fakes)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.term_extractor import orchestrator as orch_mod
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.store import TermStore


# ---------------------------------------------------------------- pure helpers
def test_short_error_collapses_whitespace_and_truncates():
    assert orch_mod._short_error(ValueError("a\n  b\t c")) == "a b c"
    out = orch_mod._short_error(ValueError("x" * 50), limit=10)
    assert out == "x" * 10 + "... [truncated]"


def test_extract_terms_json_picks_largest_valid_object_with_terms_list():
    assert orch_mod._extract_terms_json_from_text("") == {"terms": []}
    text = 'x {"terms": "not a list"} y {"terms": [{"term": "a"}], "n": {"k": {"j": 1}}} {broken'
    assert orch_mod._extract_terms_json_from_text(text)["terms"] == [{"term": "a"}]
    assert orch_mod._extract_terms_json_from_text("plain prose") == {"terms": []}


def test_context_for_term_is_gone():
    """The first-sentence-by-punctuation rule and the text[:500] fallback were removed (design b): grounding replaces them."""
    assert not hasattr(orch_mod, "_context_for_term")


def test_keyword_tokens_and_phrases_dedup_and_drop_stopwords():
    toks = orch_mod._keyword_tokens(["Papers about x-ray scattering using this", "x-ray SCATTERING data"])
    assert toks == ["x-ray", "scattering", "data"]
    assert orch_mod._focus_phrases(["  Hello   World ", "hello world", "ab"]) == ["hello world"]


def test_page_relevance_scoring():
    assert orch_mod._score_page_relevance("   ", ["a"], []) == 0.0
    text = "Abstract\nadaptive hello interval routing"
    # multiword phrase +4, two token hits +2, section heading +0.5
    assert orch_mod._score_page_relevance(text, ["adaptive", "routing", "absent"], ["hello interval"]) == 6.5
    assert orch_mod._score_page_relevance("plain", ["plain"], ["solo"]) == 1.0


def test_looks_code_like():
    assert orch_mod._looks_code_like("see https://github.com/org/repo for details")
    assert orch_mod._looks_code_like("def f(x):\n  return x")
    assert not orch_mod._looks_code_like("") and not orch_mod._looks_code_like("prose about polymers")


def test_select_relevant_pages_fallbacks():
    o = Orchestrator.__new__(Orchestrator)
    assert o.select_relevant_pages(["a"], "q", max_pages=0) == []
    assert o.select_relevant_pages(["", "  "], "query") == []
    # nothing scores > 0: first three non-empty pages are used
    assert o.select_relevant_pages(["alpha", "beta", "gamma", "delta"], "zzz unrelated") == [0, 1, 2]


# ---------------------------------------------------------------- admission switch
def test_uses_cborg_admission_depends_on_backend_and_shared_flag(monkeypatch):
    o = Orchestrator.__new__(Orchestrator)
    o.backend = "cborg"
    assert o._uses_cborg_admission() is True
    o.backend = "ollama"
    assert o._uses_cborg_admission() is False
    o.backend = "cborg-openai"
    monkeypatch.setattr(orch_mod, "shared_admission_enabled", lambda: False)
    assert o._uses_cborg_admission() is False
    monkeypatch.setattr(orch_mod, "shared_admission_enabled", lambda: True)
    assert o._uses_cborg_admission() is True
    del o.backend
    assert o._uses_cborg_admission() is True  # legacy default


# ---------------------------------------------------------------- _build_llm
class FakeChatOpenAI:
    def __init__(self, **kw):
        self.kw = kw


def test_build_llm_ollama_and_cborg(monkeypatch):
    monkeypatch.setattr(orch_mod, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(orch_mod, "require_cborg_api_key", lambda k: k or "required")
    monkeypatch.setattr(orch_mod, "openai_http_kwargs", lambda asynchronous: {"http_client": "H"})
    o = Orchestrator.__new__(Orchestrator)
    o.model, o.temperature, o.ollama_url, o.cborg_base, o.cborg_api_key = "m", 0.2, "http://o:1/", "https://c", None
    o.backend = "ollama"
    kw = o._build_llm().kw
    assert kw["base_url"] == "http://o:1/v1" and kw["api_key"].get_secret_value() == "ollama"
    o.backend = "cborg"
    kw = o._build_llm().kw
    assert kw["base_url"] == "https://c" and kw["streaming"] is True and kw["http_client"] == "H"
    assert kw["api_key"].get_secret_value() == "required"


# ---------------------------------------------------------------- construction
class FakeSchemaHelper:
    def __init__(self, schema_path):
        self.schema_path = schema_path

    def get_schema_context_for_llm(self):
        return "SCHEMA"

    def validate_and_fix_term(self, raw):
        return dict(raw)

    def get_code_domain_feature_context(self):
        return "- None"


class FakeMergeIndex:
    fail = False

    def __init__(self, model_name, top_k):
        self.model_name, self.top_k, self.ensured = model_name, top_k, None

    def ensure(self, names):
        if FakeMergeIndex.fail:
            raise RuntimeError("no model")
        self.ensured = list(names)


class FakeLLM:
    def __init__(self):
        self.bound = None
        self.invoked = []

    def bind_tools(self, tools):
        self.bound = list(tools)
        return "bound-llm"

    def invoke(self, messages):
        self.invoked.append(messages)
        return SimpleNamespace(content=["structured", "content"])


@pytest.fixture
def build_env(monkeypatch, tmp_path):
    llm = FakeLLM()
    monkeypatch.setattr(orch_mod, "SchemaHelper", FakeSchemaHelper)
    monkeypatch.setattr(orch_mod, "TermMergeIndex", FakeMergeIndex)
    monkeypatch.setattr(orch_mod, "build_services", lambda chebi_obo_path: SimpleNamespace(chebi_lookup=None))
    monkeypatch.setattr(orch_mod, "make_chat_client", lambda **kw: SimpleNamespace(kw=kw))
    monkeypatch.setattr(orch_mod, "build_graph", lambda llm, tools: SimpleNamespace(llm=llm, tools=tools))
    monkeypatch.setattr(Orchestrator, "_build_llm", lambda self: llm)
    monkeypatch.setattr(orch_mod, "build_tools", lambda state: ["t1", "t2"])
    FakeMergeIndex.fail = False
    return llm, tmp_path


def test_orchestrator_construction_wires_components_and_env_defaults(build_env, monkeypatch):
    llm, tmp = build_env
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)
    monkeypatch.setenv("CBORG_API_KEY", "env-key")
    o = Orchestrator("m", str(tmp / "out.json"), backend="ollama", chebi_obo_path=None, merge_embed_model="emb")
    assert o.cborg_base == "https://api.cborg.lbl.gov" and o.cborg_api_key == "env-key"
    assert o.merge_index.model_name == "emb" and o.merge_index.ensured == []
    assert o._snippet_client.kw["backend"] == "ollama" and o.graph.llm == "bound-llm" and o.graph.tools == ["t1", "t2"]
    assert llm.bound == ["t1", "t2"]
    # the tool-side LLM callable flattens non-string message content
    assert o._tool_state.llm_invoke("prompt") == str(["structured", "content"])
    llm.invoke = lambda messages: SimpleNamespace(content="plain")
    assert o._tool_state.llm_invoke("prompt") == "plain"
    llm.invoke = lambda messages: SimpleNamespace(content=None)
    assert o._tool_state.llm_invoke("prompt") == ""


def test_orchestrator_construction_reraises_merge_index_failure(build_env):
    _, tmp = build_env
    FakeMergeIndex.fail = True
    with pytest.raises(RuntimeError, match="no model"):
        Orchestrator("m", str(tmp / "out.json"), chebi_obo_path=None)


def test_run_extraction_builds_orchestrator_and_processes_directory(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        def __init__(self, **kw):
            seen["kw"] = kw

        def process_directory(self, d):
            return {"dir": d}

    monkeypatch.setattr(orch_mod, "Orchestrator", Fake)
    out = orch_mod.run_extraction(tmp_path, tmp_path / "o.json", model="m", backend="ollama", max_workers=3)
    assert out == {"dir": str(tmp_path)}
    assert seen["kw"]["output_file"] == str(tmp_path / "o.json") and seen["kw"]["max_workers"] == 3


# ---------------------------------------------------------------- page processing
def _bare(tmp_path, **extra):
    o = Orchestrator.__new__(Orchestrator)
    o.store = TermStore(str(tmp_path / "t.json"))
    o.schema_helper = FakeSchemaHelper("s")
    o.services = SimpleNamespace(prop_extractor=SimpleNamespace(extract=lambda t, n: []),
                                 prop_normalizer=None)
    o.temperature = 0.0
    o.backend = "ollama"
    o.max_workers = 2
    for k, v in extra.items():
        setattr(o, k, v)
    return o


PAGE = " ".join(["word"] * 30)


def test_process_page_skips_short_pages(tmp_path):
    o = _bare(tmp_path)
    assert o.process_page("too short", "a.pdf", 0) is False


def test_process_page_agent_failure_recovers_via_json_fallback_and_clears_context(tmp_path):
    reply = json.dumps({"terms": [{"term": "Polystyrene", "definition": "polymer", "category": "Chemical",
                                   "relations": [{"relation": "is_a", "related_term": "Polymer"}]}]})
    client = SimpleNamespace(chat=lambda prompt, temperature, timeout: reply)

    class Graph:
        def invoke(self, payload):
            raise RuntimeError("tool parser exploded")

    state = SimpleNamespace(set_page_context=lambda *a: calls.append(a), clear_page_context=lambda: calls.append("clear"))
    calls = []
    o = _bare(tmp_path, graph=Graph(), _snippet_client=client, _tool_state=state)
    page = PAGE + ". Polystyrene is a polymer and the film was annealed for ten minutes on a hot plate before the measuring of the scattering began."
    assert o.process_page(page, "a.pdf", 2) is True
    assert calls == [("a.pdf", 3, page), "clear"]
    rec = o.store.get(TermStore.normalize("Polystyrene"))
    assert rec.pages == [3] and rec.source_papers == ["a.pdf"] and rec.relations[0].related_term == "Polymer"
    # The model gave no sentence: the mention is located in the page (kind located), not the first matching sentence.
    assert (rec.context_snippets[0].kind, rec.context_snippets[0].page) == ("located", 3)
    assert rec.context_snippets[0].text.startswith("Polystyrene is a polymer and the film was annealed")


def test_process_page_fallback_failure_and_unusable_replies_return_false(tmp_path):
    class Graph:
        def invoke(self, payload):
            raise RuntimeError("boom")

    def failing_chat(prompt, temperature, timeout):
        raise TimeoutError("slow")

    o = _bare(tmp_path, graph=Graph(), _snippet_client=SimpleNamespace(chat=failing_chat))
    assert o.process_page(PAGE, "a.pdf", 0) is False
    o._snippet_client = SimpleNamespace(chat=lambda **kw: "no json at all")
    o._snippet_client = SimpleNamespace(chat=lambda prompt, temperature, timeout: "no json at all")
    assert o.process_page(PAGE, "a.pdf", 0) is False


def test_process_page_uses_json_in_final_message_when_agent_registered_nothing(tmp_path):
    final = SimpleNamespace(content=[json.dumps({"terms": [{"term": "Silicon", "definition": "d",
                                                            "category": "Chemical"}, "junk", {"term": " "}]})])

    class Graph:
        def invoke(self, payload):
            return {"messages": [final]}

    o = _bare(tmp_path, graph=Graph())
    assert o.process_page(PAGE + " Silicon wafers.", "p.pdf", 0) is True
    assert o.store.get(TermStore.normalize("Silicon")) is not None
    # a second identical answer changes nothing
    assert o.process_page(PAGE + " Silicon wafers.", "p.pdf", 0) is False


def test_process_page_property_extraction_alone_counts_as_update(tmp_path, monkeypatch):
    class Graph:
        def invoke(self, payload):
            return {"messages": []}

    o = _bare(tmp_path, graph=Graph())
    monkeypatch.setattr(orch_mod, "extract_and_attach_properties", lambda text, store, services: True)
    assert o.process_page(PAGE, "a.pdf", 0) is True


def test_register_json_terms_handles_odd_results(tmp_path):
    o = _bare(tmp_path)
    assert o._register_json_terms_from_result("nope", "f", 0, "t") is False
    assert o._register_json_terms_from_result({"messages": []}, "f", 0, "t") is False
    empty = {"messages": [SimpleNamespace(content='{"terms": []}')]}
    assert o._register_json_terms_from_result(empty, "f", 0, "t") is False


# ---------------------------------------------------------------- PDF / directory drivers
class FakeDoc:
    def __init__(self, texts):
        self.texts = texts
        self.page_count = len(texts)

    def load_page(self, i):
        return SimpleNamespace(get_text=lambda: self.texts[i])


@pytest.fixture
def pdf_env(monkeypatch, tmp_path):
    texts = {"good.pdf": [PAGE + " alpha", PAGE + " beta"], "bad.pdf": None}

    def fake_open(path):
        t = texts[Path(path).name]
        if t is None:
            raise OSError("corrupt")
        return FakeDoc(t)

    monkeypatch.setattr(orch_mod, "open_pdf", fake_open)
    monkeypatch.setattr(orch_mod.provenance, "extract_pub_metadata", lambda doc, p: {"paper_title": "T"})
    monkeypatch.setattr(orch_mod.provenance, "extract_code_snippets", lambda *a, **k: [{"code_snippet": "x"}])
    monkeypatch.setattr(orch_mod.source_repos, "extract_github_code_snippets", lambda text, source_paper: [{"r": 1}])
    return tmp_path


def _driver(tmp_path, process_page):
    o = _bare(tmp_path, _snippet_client=object())
    o.process_page = process_page
    o.store.add_code_snippets = lambda snips, pub_meta: bool(snips)
    return o


def test_process_pdf_counts_pages_stamps_metadata_and_tolerates_failures(pdf_env, monkeypatch):
    tmp_path = pdf_env
    o = _driver(tmp_path, lambda text, fn, i: i == 0)
    o._tool_state = SimpleNamespace(current_source_paper=None)
    stamped = []
    o.store.stamp_source_metadata = lambda fn, meta: stamped.append((fn, meta))
    assert o.process_pdf(str(tmp_path / "bad.pdf")) == 0
    n = o.process_pdf(str(tmp_path / "good.pdf"))
    assert n == 2  # page 1 via terms, page 2 via code snippets
    assert stamped == [("good.pdf", {"paper_title": "T"})]
    assert o.store.metadata["processed_files"] == 1 and o.store.metadata["processed_pages_total"] == 2
    assert o.store.metadata["github_code_snippets"] == 1
    assert o._tool_state.current_source_paper is None


def test_process_pdf_survives_metadata_snippet_and_page_errors(pdf_env, monkeypatch):
    tmp_path = pdf_env

    def bad_meta(doc, p):
        raise ValueError("meta")

    def bad_snips(*a, **k):
        raise ValueError("snips")

    def bad_repo(text, source_paper):
        raise ValueError("repo")

    monkeypatch.setattr(orch_mod.provenance, "extract_pub_metadata", bad_meta)
    monkeypatch.setattr(orch_mod.provenance, "extract_code_snippets", bad_snips)
    monkeypatch.setattr(orch_mod.source_repos, "extract_github_code_snippets", bad_repo)

    def page(text, fn, i):
        if i == 1:
            raise RuntimeError("page died")
        return True

    o = _driver(tmp_path, page)
    assert o.process_pdf(str(tmp_path / "good.pdf")) == 1
    assert o.store.metadata["processed_files"] == 1


def test_process_directory_validates_walks_and_summarises(pdf_env):
    tmp_path = pdf_env
    o = _driver(tmp_path, lambda t, f, i: True)
    assert o.process_directory(str(tmp_path / "missing"))["status"] == "error"
    assert o.process_directory("")["status"] == "error"
    d = tmp_path / "pdfs"
    (d / "sub").mkdir(parents=True)
    (d / "good.pdf").write_bytes(b"")
    (d / "sub" / "bad.pdf").write_bytes(b"")
    out = o.process_directory(str(d))
    assert out["status"] == "success" and out["processed_files"] == 1 and out["output_file"] == o.store.output_file
    empty = tmp_path / "empty"
    empty.mkdir()
    assert o.process_directory(str(empty))["unique_terms"] == 0


def test_targeted_pdf_open_failure_and_code_pass_errors(pdf_env, monkeypatch):
    tmp_path = pdf_env
    o = _driver(tmp_path, lambda t, f, i: False)
    res = o.process_pdf_targeted(str(tmp_path / "bad.pdf"), query="alpha")
    assert res["status"] == "error" and res["filename"] == "bad.pdf"

    monkeypatch.setattr(orch_mod.provenance, "extract_pub_metadata", lambda d, p: (_ for _ in ()).throw(ValueError()))
    pages = []
    o.process_page = lambda t, f, i: pages.append(i) or False
    res = o.process_pdf_targeted(str(tmp_path / "good.pdf"), query="zzz-no-match", max_pages=1)
    assert res["status"] == "success" and res["low_confidence"] is True and res["selected_pages"] == [1]
    assert pages == [0]


def test_targeted_pdf_code_like_pages_use_snippet_pass_even_when_it_fails(pdf_env, monkeypatch):
    tmp_path = pdf_env
    monkeypatch.setattr(orch_mod, "open_pdf", lambda p: FakeDoc(["import numpy as np alpha beta"]))
    monkeypatch.setattr(orch_mod.provenance, "extract_code_snippets",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm")))
    o = _driver(tmp_path, lambda t, f, i: False)
    res = o.process_pdf_targeted(str(tmp_path / "x.pdf"), query="alpha")
    assert res["processed_pages_with_terms"] == 0 and res["low_confidence"] is False
    monkeypatch.setattr(orch_mod.provenance, "extract_code_snippets", lambda *a, **k: [{"code_snippet": "c"}])
    assert o.process_pdf_targeted(str(tmp_path / "x.pdf"), query="alpha")["processed_pages_with_terms"] == 1


def test_process_directory_targeted_aggregates_results(pdf_env):
    tmp_path = pdf_env
    o = _driver(tmp_path, lambda t, f, i: True)
    assert o.process_directory_targeted(str(tmp_path / "nope"), query="q")["status"] == "error"
    d = tmp_path / "pdfs"
    d.mkdir()
    (d / "good.pdf").write_bytes(b"")
    (d / "bad.pdf").write_bytes(b"")
    out = o.process_directory_targeted(str(d), query="alpha", missing_topics=["beta"], max_pages=2)
    assert out["extraction_mode"] == "targeted" and out["processed_files"] == 1
    assert out["source_pages_total"] == 2 and out["processed_pages_total"] == 2
    assert [r["status"] for r in out["pdf_results"]] == ["error", "success"]
