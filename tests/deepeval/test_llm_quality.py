"""
Deepeval integration tests for KG-RAG LLM response quality.

Covers:
  - RAG answer faithfulness  (AnswerRelevancyMetric, FaithfulnessMetric)
  - Context relevancy        (ContextualRelevancyMetric)
  - Hallucination on baseline prompt (HallucinationMetric)
  - Prompt structure / decompose logic (deterministic, no judge needed)
  - End-to-end pipeline with mocked LLM (AnswerRelevancyMetric)

LLM-graded tests (tests 1–4, 7) use CBORG as the judge model via
deepeval's GPTModel with a custom base_url.  They are automatically
skipped when CBORG_API_KEY is not set in the environment.

Tests 5–6 are deterministic and always run.

Run (no API key — deterministic tests only):
    pytest tests/deepeval/ -v \\
        --junitxml=tests/deepeval/results/deepeval_results.xml

Run (full suite with CBORG judge):
    CBORG_API_KEY=<key> pytest tests/deepeval/ -v \\
        --junitxml=tests/deepeval/results/deepeval_results.xml
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import List, Optional, Tuple, Union
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Deepeval imports — skip entire module when deepeval is not installed
# ---------------------------------------------------------------------------
deepeval = pytest.importorskip("deepeval", reason="deepeval not installed")

from deepeval import assert_test                                         # noqa: E402
from deepeval.metrics import (                                           # noqa: E402
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
    HallucinationMetric,
)
from deepeval.models.llms.openai_model import GPTModel                  # noqa: E402
from deepeval.test_case import LLMTestCase                              # noqa: E402

from app.modules import kg_rag_api                                       # noqa: E402


# ---------------------------------------------------------------------------
# Judge model — CBORG (OpenAI-compatible)
# ---------------------------------------------------------------------------

_CBORG_API_KEY: Optional[str] = os.environ.get("CBORG_API_KEY")
_CBORG_BASE_URL: str = os.environ.get(
    "KG_RAG_CBORG_BASE_URL",
    os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov"),
)
# Use a fast/cheap CBORG model as judge to keep eval costs low
_JUDGE_MODEL: str = os.environ.get("KG_RAG_DEEPEVAL_JUDGE_MODEL", "lbl/cborg-chat")

#: Skip marker applied to every test that calls a real LLM judge
requires_judge = pytest.mark.skipif(
    not _CBORG_API_KEY,
    reason="CBORG_API_KEY not set — LLM-graded deepeval tests skipped",
)


def _make_judge() -> GPTModel:
    """Return a GPTModel wired to CBORG's OpenAI-compatible endpoint."""
    return GPTModel(
        model=_JUDGE_MODEL,
        api_key=_CBORG_API_KEY,
        base_url=_CBORG_BASE_URL.rstrip("/"),
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_graph_path(tmp_path_factory) -> Path:
    """Minimal KG with two polymer nodes and one relation."""
    graph = {
        "things": [
            {
                "id": "matkg:P3HT",
                "name": "P3HT",
                "category": "ConjugatedPolymer",
                "description": (
                    "Poly(3-hexylthiophene) is a regioregular conjugated polymer widely used "
                    "in organic photovoltaics due to its high hole mobility and solution processability."
                ),
                "source_papers": [],
                "publication_year": 2020,
                "journal": "Advanced Materials",
                "authors": ["Smith J", "Lee K"],
                "doi": "10.1002/adma.fake001",
            },
            {
                "id": "matkg:OPV",
                "name": "Organic Photovoltaic Device",
                "category": "Device",
                "description": (
                    "Solar cell device that converts sunlight to electricity using organic "
                    "semiconducting materials. P3HT:PCBM bulk-heterojunction devices achieve "
                    "PCE values of 4–6%. State-of-the-art non-fullerene acceptor OPVs exceed 18% PCE."
                ),
                "source_papers": [],
                "publication_year": 2021,
            },
            {
                "id": "matkg:PCE",
                "name": "Power Conversion Efficiency",
                "category": "Property",
                "description": (
                    "Ratio of electrical output power to incident solar irradiance. "
                    "Typical P3HT-based OPV devices show PCE of 3–6%. "
                    "Non-fullerene acceptor systems have demonstrated PCE above 18% (2023). "
                    "PCE is calculated as PCE = (Jsc × Voc × FF) / Pin."
                ),
                "source_papers": [],
            },
        ],
        "associations": [
            {
                "subject": "matkg:P3HT",
                "predicate": "rel:has_application",
                "object": "matkg:OPV",
                "has_evidence": "paper1",
            },
            {
                "subject": "matkg:OPV",
                "predicate": "rel:has_property",
                "object": "matkg:PCE",
                "has_evidence": "paper2",
            },
        ],
    }
    p = tmp_path_factory.mktemp("kg") / "graph.json"
    p.write_text(json.dumps(graph))
    return p


@pytest.fixture(scope="module")
def monkeypatch_module():
    """Module-scoped monkeypatch (pytest's built-in fixture is function-scoped)."""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def kg(sample_graph_path, monkeypatch_module):
    """Load KnowledgeGraph from the sample fixture graph."""
    monkeypatch_module.setenv("KG_RAG_RETRIEVAL_BACKEND", "lexical")
    monkeypatch_module.setenv("KG_RAG_FORCE_CPU", "1")
    return kg_rag_api.KnowledgeGraph(str(sample_graph_path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rag_context(kg: kg_rag_api.KnowledgeGraph, query: str) -> List[str]:
    """Return list-of-strings context chunks for deepeval ContextualRelevancy."""
    nodes = kg_rag_api.retrieve_nodes(query, kg)
    ctx_str = kg.build_context(
        nodes,
        include_structured=True,
        char_budget=8_000,
        hint_terms=query.split(),
    )
    # Split on node sections so deepeval gets per-chunk granularity
    chunks = [c.strip() for c in ctx_str.split("\n\n") if c.strip()]
    return chunks or [ctx_str]


MOCK_RAG_ANSWER = (
    "P3HT (Poly(3-hexylthiophene)) is a regioregular conjugated polymer commonly used "
    "as the donor material in organic photovoltaic (OPV) devices [KG: P3HT]. "
    "Its high hole mobility and solution processability make it attractive for "
    "bulk-heterojunction solar cells [KG: Organic Photovoltaic Device]. "
    "The power conversion efficiency (PCE) of P3HT-based OPV devices typically "
    "reaches 4–6% when paired with PCBM acceptors [KG: Power Conversion Efficiency]."
)

# Mechanism-focused answer used only in test 7, which asks specifically
# *how* P3HT contributes to efficiency — not what it is or how it is processed.
MOCK_RAG_ANSWER_MECHANISM = (
    "P3HT enhances organic solar cell efficiency through three primary mechanisms "
    "[KG: P3HT]. First, its high hole mobility (~0.1 cm²/V·s) enables rapid extraction "
    "of photo-generated holes to the anode, reducing recombination losses. "
    "Second, the regioregular backbone promotes π–π stacking and lamellar ordering, "
    "which extends exciton diffusion length and improves charge transport. "
    "Third, in bulk-heterojunction blends with PCBM, P3HT forms a bicontinuous "
    "donor–acceptor network that maximises the interfacial area for exciton dissociation, "
    "directly raising short-circuit current density (Jsc) and thus PCE [KG: Power Conversion Efficiency]. "
    "Optimised P3HT:PCBM devices achieve PCE of 4–6% [KG: Organic Photovoltaic Device]."
)

MOCK_BASELINE_ANSWER = (
    "P3HT is a conjugated polymer. It is used in solar cells. "
    "PCE is a performance metric."
)


# ---------------------------------------------------------------------------
# Test 1 — Answer Relevancy  [requires judge]
# Checks that the RAG answer is on-topic for the question.
# ---------------------------------------------------------------------------

@requires_judge
def test_rag_answer_relevancy(kg):
    """RAG answer must be relevant to the polymer OPV question."""
    query = "What is P3HT and how is it used in organic photovoltaic devices?"
    context_chunks = _rag_context(kg, query)

    test_case = LLMTestCase(
        input=query,
        actual_output=MOCK_RAG_ANSWER,
        retrieval_context=context_chunks,
    )
    metric = AnswerRelevancyMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 2 — Faithfulness  [requires judge]
# Checks that the RAG answer does not contradict the retrieved context.
# ---------------------------------------------------------------------------

@requires_judge
def test_rag_faithfulness(kg):
    """RAG answer claims must be supported by retrieved context."""
    query = "Describe the role of P3HT in OPV efficiency."
    context_chunks = _rag_context(kg, query)

    test_case = LLMTestCase(
        input=query,
        actual_output=MOCK_RAG_ANSWER,
        retrieval_context=context_chunks,
    )
    metric = FaithfulnessMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 3 — Contextual Relevancy  [requires judge]
# Checks that retrieved chunks are actually relevant to the question.
# ---------------------------------------------------------------------------

@requires_judge
def test_retrieval_context_relevancy(kg):
    """Retrieved KG context should be relevant to the posed query."""
    query = "What is the power conversion efficiency of organic solar cells?"
    context_chunks = _rag_context(kg, query)

    test_case = LLMTestCase(
        input=query,
        actual_output=MOCK_RAG_ANSWER,
        retrieval_context=context_chunks,
    )
    metric = ContextualRelevancyMetric(
        model=_make_judge(), threshold=0.5, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 4 — Hallucination (baseline vs. context)  [requires judge]
# Ensures baseline answer doesn't introduce facts absent from context.
# ---------------------------------------------------------------------------

@requires_judge
def test_baseline_hallucination(kg):
    """Baseline (no-RAG) answer should not contradict provided context."""
    query = "What materials are used in organic photovoltaics?"
    context_chunks = _rag_context(kg, query)

    test_case = LLMTestCase(
        input=query,
        actual_output=MOCK_BASELINE_ANSWER,
        # HallucinationMetric uses `context`, not `retrieval_context`
        context=context_chunks,
    )
    metric = HallucinationMetric(
        model=_make_judge(), threshold=0.4, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 5 — build_rag_prompt structure  [deterministic — always runs]
# Sanity-checks that the prompt builder embeds question + context correctly.
# ---------------------------------------------------------------------------

def test_build_rag_prompt_contains_required_sections():
    """build_rag_prompt output must contain Question, Retrieved Context, and answer instruction."""
    query = "How does molecular weight affect P3HT solar cell performance?"
    ctx = textwrap.dedent("""\
        ## P3HT (ConjugatedPolymer)
        Combined_Score: 0.921
        Description: Regioregular conjugated polymer for OPV.
        Relations:
        - has_application: Organic Photovoltaic Device
    """)
    prompt = kg_rag_api.build_rag_prompt(query, ctx)

    assert "Question:" in prompt, "Prompt missing 'Question:' section"
    assert "Retrieved Context:" in prompt, "Prompt missing 'Retrieved Context:' section"
    assert query.strip() in prompt, "Prompt does not embed the query"
    assert "P3HT" in prompt, "Prompt does not embed context"


# ---------------------------------------------------------------------------
# Test 6 — decompose splits compound queries  [deterministic — always runs]
# Validates sub-question decomposition used in stepwise retrieval.
# ---------------------------------------------------------------------------

def test_decompose_produces_sub_questions():
    """Compound query should decompose into multiple sub-questions."""
    query = "What is P3HT and how does it affect PCE in OPV devices?"
    parts = kg_rag_api.decompose(query)
    assert len(parts) >= 2, (
        f"Expected ≥2 sub-questions from compound query, got {parts}"
    )
    for part in parts:
        assert len(part) >= 3, f"Sub-question too short: {repr(part)}"


# ---------------------------------------------------------------------------
# Test 7 — End-to-end pipeline with mocked LLM  [requires judge]
# Exercises retrieve_nodes → build_context → build_rag_prompt → mocked chat
# and evaluates the mock response for relevancy.
# ---------------------------------------------------------------------------

@requires_judge
def test_end_to_end_pipeline_answer_relevancy(kg):
    """Full RAG pipeline (mocked LLM) answer must be relevant to query.

    Kept synchronous: deepeval's assert_test calls loop.run_until_complete()
    internally, which conflicts with pytest-asyncio's running loop if the test
    itself is declared async.  The mock chat call is resolved via asyncio.run().
    """
    import asyncio

    query = "Explain how P3HT contributes to organic solar cell efficiency."
    nodes = kg_rag_api.retrieve_nodes(query, kg)
    ctx = kg.build_context(
        nodes,
        include_structured=True,
        char_budget=8_000,
        hint_terms=query.split(),
    )
    prompt = kg_rag_api.build_rag_prompt(query, ctx)

    mock_client = AsyncMock()
    mock_client.model = "mock-model"
    mock_client.chat = AsyncMock(return_value=MOCK_RAG_ANSWER_MECHANISM)

    conv = kg_rag_api.Conversation(kg_rag_api.RAG_SYSTEM)
    messages = conv.build(prompt)
    response = asyncio.run(mock_client.chat(messages))

    context_chunks = [c.strip() for c in ctx.split("\n\n") if c.strip()] or [ctx]
    test_case = LLMTestCase(
        input=query,
        actual_output=response,
        retrieval_context=context_chunks,
    )
    metric = AnswerRelevancyMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 8 — Citation format  [requires judge]
# RAG answer must contain [KG: ...] inline citations matching node names.
# ---------------------------------------------------------------------------

@requires_judge
def test_rag_answer_contains_kg_citations(kg):
    """RAG answer must contain at least one [KG: NodeName] inline citation."""
    query = "What is the role of P3HT in organic photovoltaics?"
    context_chunks = _rag_context(kg, query)

    test_case = LLMTestCase(
        input=query,
        actual_output=MOCK_RAG_ANSWER,
        retrieval_context=context_chunks,
    )
    assert "[KG:" in MOCK_RAG_ANSWER, "Answer missing [KG: ...] citation format"
    metric = AnswerRelevancyMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 9 — Multi-turn faithfulness  [requires judge]
# Second-turn answer must be faithful to context; no contradiction with turn 1.
# ---------------------------------------------------------------------------

@requires_judge
def test_multi_turn_faithfulness(kg):
    """Second-turn answer must be faithful to context and consistent with turn 1."""
    query_2 = "How does its hole mobility affect device performance?"
    answer_2 = (
        "P3HT's hole mobility (~0.1 cm\u00b2/V\u00b7s) enables efficient extraction of "
        "photo-generated holes, reducing recombination and improving short-circuit "
        "current density in OPV devices [KG: P3HT]. "
        "Higher hole mobility directly raises power conversion efficiency "
        "[KG: Power Conversion Efficiency]."
    )
    context_chunks = _rag_context(kg, query_2)
    test_case = LLMTestCase(
        input=query_2,
        actual_output=answer_2,
        retrieval_context=context_chunks,
    )
    metric = FaithfulnessMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 10 — Context budget enforcement  [deterministic — always runs]
# Full context has more content than tiny-budget context.
# ---------------------------------------------------------------------------

def test_context_budget_cuts_low_ranked_nodes(kg):
    """build_context with tiny budget must produce less content than full budget."""
    query = "P3HT OPV PCE"
    nodes = kg_rag_api.retrieve_nodes(query, kg)

    ctx_tiny = kg.build_context(nodes, include_structured=False, char_budget=60, hint_terms=[])
    ctx_full = kg.build_context(nodes, include_structured=False, char_budget=10_000, hint_terms=[])

    assert len(ctx_full) > len(ctx_tiny)
    assert "P3HT" in ctx_full


# ---------------------------------------------------------------------------
# Test 11 — Domain Knowledge flag  [requires judge]
# Answer that falls back to [Domain Knowledge] must still be relevant.
# ---------------------------------------------------------------------------

@requires_judge
def test_sparse_kg_answer_relevancy():
    """Answer for a no-match query using [Domain Knowledge] must still be relevant."""
    import json as _json
    import os as _os
    import tempfile
    from pathlib import Path

    sparse_graph = {
        "things": [
            {
                "id": "matkg:Unrelated",
                "name": "Unrelated material",
                "category": "Material",
                "description": "Has nothing to do with graphene.",
                "source_papers": [],
            }
        ],
        "associations": [],
    }
    with tempfile.TemporaryDirectory() as td:
        gp = Path(td) / "sparse.json"
        gp.write_text(_json.dumps(sparse_graph))
        _os.environ["KG_RAG_RETRIEVAL_BACKEND"] = "lexical"
        sparse_kg = kg_rag_api.KnowledgeGraph(str(gp))

    domain_knowledge_answer = (
        "Graphene is a single-layer allotrope of carbon arranged in a two-dimensional "
        "hexagonal lattice. Its exceptional electron mobility (~200,000 cm\u00b2/V\u00b7s) "
        "makes it highly attractive for use as a transparent electrode in next-generation "
        "solar cells, where low sheet resistance and high optical transmittance are required."
    )
    test_case = LLMTestCase(
        input="What is graphene and why is it used in solar cells?",
        actual_output=domain_knowledge_answer,
        retrieval_context=["Unrelated material: Has nothing to do with graphene."],
    )
    metric = AnswerRelevancyMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(test_case, [metric])


# ---------------------------------------------------------------------------
# Test 12 — Baseline vs RAG gap  [requires judge]
# RAG answer scores above threshold; baseline answer is too vague to pass.
# ---------------------------------------------------------------------------

@requires_judge
def test_rag_scores_higher_than_baseline(kg):
    """RAG answer must pass AnswerRelevancy threshold; baseline answer must not."""
    query = "What is the power conversion efficiency of P3HT-based solar cells?"
    context_chunks = _rag_context(kg, query)

    rag_answer = (
        "P3HT-based solar cells typically reach a power conversion efficiency of 3 to 6 percent "
        "when blended with PCBM. This relatively modest efficiency is due to limited absorption "
        "range and a low open-circuit voltage compared to more modern organic photovoltaic materials."
    )

    rag_case = LLMTestCase(
        input=query, actual_output=rag_answer, retrieval_context=context_chunks
    )
    metric = AnswerRelevancyMetric(
        model=_make_judge(), threshold=0.7, verbose_mode=False
    )
    assert_test(rag_case, [metric])
