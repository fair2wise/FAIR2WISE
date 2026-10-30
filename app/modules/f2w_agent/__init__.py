"""FAIR2WISE orchestrated KG-RAG pipeline.

Academy agents coordinate a retrieve -> (download -> extract -> update KG)
-> retrieve loop:

- RetrievalAgent: KG retrieval + strict sufficiency judgement / answer.
- DownloadAgent:  OpenAlex relevance search + PDF download.
- ExtractorAgent: term extraction (copied branch term_extractor) + KG update.
- EvidenceDebateAgent: candidate-ranking specialist selected by the orchestrator.
- WorkflowOrchestratorAgent: persistent, constrained pipeline controller.
- PaperEvidenceAgent: manifest-bounded answers from the active extracted PDF.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "RetrievalAgent",
    "DownloadAgent",
    "ExtractorAgent",
    "EvidenceDebateAgent",
    "WorkflowOrchestratorAgent",
    "PaperEvidenceAgent",
]


def __getattr__(name: str) -> Any:  # lazy exports to keep import light; Any: the lazily imported class
    """Load Academy agent classes on first attribute access."""
    if name == "RetrievalAgent":
        from .retrieval_agent import RetrievalAgent

        return RetrievalAgent
    if name == "DownloadAgent":
        from .download_agent import DownloadAgent

        return DownloadAgent
    if name == "ExtractorAgent":
        from .extractor_agent import ExtractorAgent

        return ExtractorAgent
    if name == "EvidenceDebateAgent":
        from .debate_agent import EvidenceDebateAgent

        return EvidenceDebateAgent
    if name == "WorkflowOrchestratorAgent":
        from .orchestrator_agent import WorkflowOrchestratorAgent

        return WorkflowOrchestratorAgent
    if name == "PaperEvidenceAgent":
        from .paper_evidence_agent import PaperEvidenceAgent

        return PaperEvidenceAgent
    raise AttributeError(name)
