"""Deterministic route-regex checks. No network and no API key.

Rows marked xfail describe bugs in the current matchers. strict=True makes
the suite fail once the matcher is fixed and the assertion starts passing.
"""
from __future__ import annotations

import pytest

from app.modules.f2w_agent.retrieval_agent import (
    _is_conceptual_question,
    _is_numeric_claim,
    _is_ops_layout_question,
)
from app.modules.tiled_graph import is_experiment_identity_question

pytestmark = pytest.mark.eval


@pytest.mark.parametrize(
    "question",
    [
        "how is the hardware connected in order",
        "what is upstream of the exit slits",
    ],
)
def test_ops_layout_positive(question: str) -> None:
    assert _is_ops_layout_question(question) is True


def test_ops_layout_in_order_to_is_not_hardware_order() -> None:
    assert _is_ops_layout_question("In order to measure RSoXS, what energy should I use?") is False


def test_numeric_claim_photon_energy() -> None:
    assert _is_numeric_claim("What photon energy for the carbon K-edge?") is True


def test_numeric_claim_beamline_specialty_is_not_numeric() -> None:
    assert _is_numeric_claim("What does the beamline specialize in?") is False


@pytest.mark.xfail(
    strict=True,
    reason="_NUMERIC_CLAIM_RE matches 'temperature' and treats a conceptual question as numeric",
)
def test_numeric_claim_temperature_dependent_is_not_numeric() -> None:
    assert _is_numeric_claim("Explain temperature-dependent RSoXS") is False


def test_conceptual_explain_question() -> None:
    question = "Explain the concept of radiative versus non-radiative loss channels"
    assert _is_conceptual_question(question) is True


def test_conceptual_thanks_is_not_conceptual() -> None:
    assert _is_conceptual_question("thanks!") is False


def test_conceptual_numeric_question_is_not_conceptual() -> None:
    assert _is_conceptual_question("What photon energy for the carbon K-edge?") is False


@pytest.mark.parametrize(
    "question",
    [
        "Tell me about ESAF 2026-00041",
        "What is in P202600041-01?",
        "Show me my last scan",
    ],
)
def test_experiment_identity_positive(question: str) -> None:
    assert is_experiment_identity_question(question) is True


def test_experiment_identity_thanks_is_false() -> None:
    assert is_experiment_identity_question("thanks!") is False


@pytest.mark.xfail(
    strict=True,
    reason="_IDENTITY_RE matches bare run(s)/sample(s)/scan(s)",
)
@pytest.mark.parametrize(
    "question",
    [
        "How do I run CyRSoXS?",
        "What sample classes suit RSoXS?",
    ],
)
def test_experiment_identity_false_positive(question: str) -> None:
    assert is_experiment_identity_question(question) is False
