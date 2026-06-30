"""FAIR2WISE 3-agent KG-RAG pipeline.

Three Academy agents coordinate a retrieve -> (download -> extract -> update KG)
-> retrieve loop:

- RetrievalAgent: KG retrieval + strict sufficiency judgement / answer.
- DownloadAgent:  OpenAlex relevance search + PDF download.
- ExtractorAgent: term extraction (copied branch term_extractor) + KG update.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repo's existing modules importable in every style they expect
# (`app.modules.*`, `modules.*`, and top-level `scripts/*`).
_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "app", _ROOT / "scripts"):
    sp = str(_p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

__all__ = [
    "RetrievalAgent",
    "DownloadAgent",
    "ExtractorAgent",
]


def __getattr__(name: str):  # lazy exports to keep import light
    if name == "RetrievalAgent":
        from .retrieval_agent import RetrievalAgent

        return RetrievalAgent
    if name == "DownloadAgent":
        from .download_agent import DownloadAgent

        return DownloadAgent
    if name == "ExtractorAgent":
        from .extractor_agent import ExtractorAgent

        return ExtractorAgent
    raise AttributeError(name)
