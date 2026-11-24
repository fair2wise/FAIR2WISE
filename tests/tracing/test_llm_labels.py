"""The ``llm.call <label>`` span name and ``f2w.llm.label`` come from a closed set, never from user or model text."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest

from app.modules import tracing
from app.modules.tracing import runtime

REPO = Path(__file__).resolve().parents[2]


def _literal_labels() -> set[str]:
    """Every string literal passed as ``label=``/``trace_label=`` (or to ``call_llm`` positionally) under ``app/``."""
    found: set[str] = set()
    keyword = re.compile(r"""\b(?:trace_)?label\s*=\s*["']([A-Za-z][\w.-]*)["']""")
    positional = re.compile(r"""krag\.call_llm\(|call_llm\(""")
    for path in (REPO / "app").rglob("*.py"):
        if "/tracing/" in str(path) or "/legacy/" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        found |= set(keyword.findall(text))
        if positional.search(text):
            found |= set(re.findall(r"""call_llm\([^"]*?["']((?:KG-RAG|baseline)[\w-]*)["']\s*\)""", text, re.S))
    return found


def test_every_literal_label_in_code_is_in_the_closed_set() -> None:
    literals = _literal_labels()
    assert {"general-answer", "supervisor-plan", "pdf-validate"} <= literals
    assert literals <= runtime.LLM_LABELS, sorted(literals - runtime.LLM_LABELS)


@pytest.mark.parametrize("label,expected", [
    ("general-answer", "general-answer"), ("KG-RAG-judge", "KG-RAG-judge"), ("chat", "chat"),
    ("What is the bandgap of Nanoscale Perovskite Study 10.1000/x?", "other"), ("../../etc/passwd", "other"),
    ("", "other"), ("GENERAL-ANSWER", "other"),
])
def test_llm_call_span_name_and_label_come_from_the_closed_set(
        exporter: Any, label: str, expected: str) -> None:  # noqa: ANN401
    tracing.record_model_call(label=label, model="m", latency_ms=1.0, prompt_chars=3, response_chars=4, error=None)
    (span,) = exporter.get_finished_spans()
    assert span.name == f"llm.call {expected}" and span.attributes["f2w.llm.label"] == expected
    assert span.name == "llm.call " + expected


def test_label_set_is_bounded() -> None:
    assert len(runtime.LLM_LABELS) <= 32 and "other" in runtime.LLM_LABELS
