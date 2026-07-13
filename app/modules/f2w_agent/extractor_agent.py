"""ExtractorAgent: local Academy wrapper around the term_extractor Orchestrator.

This is the in-process extraction agent used by the local coordinator loop. It
wraps the (copied + provenance-patched) ``term_extractor.Orchestrator`` directly,
without the Globus/dashboard monitoring stack. The full monitored
``term_extractor.TermExtractorAgent`` remains available for the remote path.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from academy.agent import Agent, action

logger = logging.getLogger(__name__)


class ExtractorAgent(Agent):
    """Academy agent that extracts terms from a PDF directory into a terms JSON."""

    def __init__(
        self,
        *,
        backend: str = "cborg",
        model: Optional[str] = None,
        schema_path: str = "storage/schema/matkg_schema.yaml",
        chebi_obo_path: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        max_workers: int = 8,
    ) -> None:
        super().__init__()
        self._backend = backend
        self._model = model or os.environ.get("EXTRACT_TERMS_MODEL", "lbl/cborg-chat")
        self._schema_path = schema_path
        self._chebi_obo_path = chebi_obo_path
        self._ollama_url = ollama_url
        self._temperature = temperature
        self._max_workers = max_workers
        self._cborg_base = os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
        self._cborg_api_key = os.environ.get("CBORG_API_KEY")

    def _run(self, pdf_dir: str, terms_json: str, max_workers: Optional[int]) -> Dict[str, Any]:
        from app.modules.term_extractor import Orchestrator

        Path(terms_json).parent.mkdir(parents=True, exist_ok=True)
        orch = Orchestrator(
            model=self._model,
            output_file=str(terms_json),
            backend=self._backend,
            schema_path=self._schema_path,
            temperature=self._temperature,
            max_workers=max_workers or self._max_workers,
            cborg_base=self._cborg_base,
            cborg_api_key=self._cborg_api_key,
            ollama_url=self._ollama_url,
            chebi_obo_path=self._chebi_obo_path,
        )
        return orch.process_directory(str(pdf_dir))

    def _run_targeted(
        self,
        pdf_dir: str,
        terms_json: str,
        query: str,
        missing_topics: Optional[List[str]],
        max_pages: int,
        max_workers: Optional[int],
    ) -> Dict[str, Any]:
        from app.modules.term_extractor import Orchestrator

        Path(terms_json).parent.mkdir(parents=True, exist_ok=True)
        orch = Orchestrator(
            model=self._model,
            output_file=str(terms_json),
            backend=self._backend,
            schema_path=self._schema_path,
            temperature=self._temperature,
            max_workers=max_workers or self._max_workers,
            cborg_base=self._cborg_base,
            cborg_api_key=self._cborg_api_key,
            ollama_url=self._ollama_url,
            chebi_obo_path=self._chebi_obo_path,
        )
        return orch.process_directory_targeted(
            str(pdf_dir),
            query=query,
            missing_topics=missing_topics or [],
            max_pages=max_pages,
        )

    @action
    async def extract(
        self,
        pdf_dir: str,
        terms_json: str,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Extract/merge terms from ``pdf_dir`` into cumulative ``terms_json``."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run, pdf_dir, terms_json, max_workers)

    @action
    async def extract_targeted(
        self,
        pdf_dir: str,
        terms_json: str,
        query: str,
        missing_topics: Optional[List[str]] = None,
        max_pages: int = 6,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Extract/merge query-relevant terms from selected pages in ``pdf_dir``."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._run_targeted,
            pdf_dir,
            terms_json,
            query,
            missing_topics or [],
            max_pages,
            max_workers,
        )
