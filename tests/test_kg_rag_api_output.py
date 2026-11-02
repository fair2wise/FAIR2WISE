"""Characterization of the exact bytes kg_rag_api's CLI paths write to stdout/stderr.

Pins every user-facing line of ``run_competency_questions``, ``call_llm``, ``answer`` and ``main`` so
the print -> ``f2w.output`` logging conversion cannot change what a user sees.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.modules import kg_rag_api as k
from app.modules.kg_rag_api import Fore, Style

Y, G, M, C, R, Z = Fore.YELLOW, Fore.GREEN, Fore.MAGENTA, Fore.CYAN, Fore.RED, Style.RESET_ALL


class Cli:
    model = "m-1"

    async def chat(self, messages):
        return "BASE" if messages == "base" else "RAG[Domain Knowledge] x\n"


class Conv:
    def __init__(self, tag):
        self.tag = tag

    def build(self, prompt):
        return self.tag

    def add(self, prompt, resp):
        pass


class Tracker:
    def __init__(self):
        self.logged = []

    def log(self, mn):
        self.logged.append(mn)


class KG:
    def build_context(self, *a, **kw):
        return "ctx"


@pytest.fixture(autouse=True)
def _fake_retrieval(monkeypatch):
    monkeypatch.setattr(k, "retrieve_nodes", lambda q, kg: [
        SimpleNamespace(id="a", score_prp=0.5, evidence_ct=0),
        SimpleNamespace(id="b", score_prp=1.234, evidence_ct=2)])
    monkeypatch.setattr(k, "LLM_TIMEOUT", 120)


def _io(capsys, out, err=""):
    cap = capsys.readouterr()
    assert cap.out == out
    assert cap.err == err


def test_call_llm_status_line(capsys):
    assert asyncio.run(k.call_llm(Cli(), "rag", "KG-RAG")) == "RAG[Domain Knowledge] x\n"
    _io(capsys, f"{Y}Calling m-1 for KG-RAG (timeout=120s)...{Z}\n")


@pytest.mark.parametrize("show_baseline", [False, True])
def test_answer_output(capsys, monkeypatch, show_baseline):
    monkeypatch.setattr(k, "SHOW_BASELINE", show_baseline)
    monkeypatch.setattr(k, "build_baseline_prompt", lambda q: "bp")
    tracker = Tracker()
    asyncio.run(k.answer("what%s?", KG(), Cli(), Conv("rag"), Conv("base"), tracker))
    exp = (f"{M}\nQ: what%s?{Z}\n"
           f"{C}Selected: ['a:0.50', 'b:1.23']{Z}\n"
           f"{Y}Building retrieved context...{Z}\n")
    if show_baseline:
        exp += f"{Y}Calling m-1 for baseline (timeout=120s)...{Z}\n"
    exp += f"{Y}Calling m-1 for KG-RAG (timeout=120s)...{Z}\n"
    if show_baseline:
        exp += f"{G}\n[Baseline]\nBASE{Z}\n"
    exp += f"{G}\n[KG-RAG]\nRAG[Domain Knowledge] x\n{Z}\n"
    _io(capsys, exp)
    assert len(tracker.logged) == 1


def test_competency_output(capsys, tmp_path):
    infile = tmp_path / "q.txt"
    long_q = "x" * 3
    infile.write_text(f"{long_q}\n\nsecond 100%\n")
    tracker = Tracker()
    asyncio.run(k.run_competency_questions(
        KG(), Cli(), Conv("rag"), Conv("base"), infile, tmp_path / "o.json", tracker))
    resp = "RAG[Domain Knowledge] x\n"
    exp = ""
    for i, q in enumerate([long_q, "second 100%"], 1):
        exp += f"{Y}\n[Q{i}] {q}{Z}\n{G}[Baseline]\nBASE...{Z}\n{G}[KG-RAG]\n{resp[:500]}...{Z}\n"
    _io(capsys, exp)


def test_competency_truncates_to_500(capsys, tmp_path):
    class Big(Cli):
        async def chat(self, messages):
            return "z" * 700

    infile = tmp_path / "q.txt"
    infile.write_text("only\n")
    asyncio.run(k.run_competency_questions(
        KG(), Big(), Conv("rag"), Conv("base"), infile, tmp_path / "o.json", Tracker()))
    _io(capsys, f"{Y}\n[Q1] only{Z}\n{G}[Baseline]\n{'z' * 500}...{Z}\n{G}[KG-RAG]\n{'z' * 500}...{Z}\n")


def _raising(exc):
    async def _main_async(args):
        raise exc
    return _main_async


def test_main_keyboard_interrupt(capsys, monkeypatch):
    monkeypatch.setattr(k, "main_async", _raising(KeyboardInterrupt()))
    k.main(SimpleNamespace())
    _io(capsys, "\nInterrupted.\n")


def test_main_runtime_error(capsys, monkeypatch):
    monkeypatch.setattr(k, "main_async", _raising(RuntimeError("bad %s")))
    k.main(SimpleNamespace())
    _io(capsys, f"{R}\nError: bad %s{Z}\n")
