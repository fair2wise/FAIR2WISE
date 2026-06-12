import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from academy.agent import Agent, action

from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class TermExtractorAgent(Agent):
    """Academy agent wrapping the LangGraph term-extraction pipeline.

    Runs on the remote Globus Compute endpoint; the Orchestrator is
    initialized during agent_on_startup so heavy setup happens on NERSC.
    """

    def __init__(
        self,
        model: str,
        output_file: str,
        *,
        backend: str = "ollama",
        schema_path: str = "storage/schema/matkg_schema.yaml",
        temperature: float = 0.0,
        context_length: int = 50,
        max_workers: int = 2,
        cborg_base: Optional[str] = None,
        cborg_api_key: Optional[str] = None,
        chebi_obo_path: Optional[str] = None,
        log_file: Optional[str] = None,
    ) -> None:
        super().__init__()
        # These are serialized and sent to the remote executor.
        # Resolve env vars now so the remote side doesn't need the .env file.
        self._model = model
        self._output_file = output_file
        self._backend = backend
        self._schema_path = schema_path
        self._temperature = temperature
        self._context_length = context_length
        self._max_workers = max_workers
        self._cborg_base = cborg_base or os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
        self._cborg_api_key = cborg_api_key or os.environ.get("CBORG_API_KEY")
        self._chebi_obo_path = chebi_obo_path
        self._log_file = log_file
        self._orchestrator: Optional[Orchestrator] = None

    async def agent_on_startup(self) -> None:
        """Called once on the remote executor after the agent is launched."""
        if self._log_file:
            log_path = Path(self._log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logging.getLogger().addHandler(handler)
            logging.getLogger().setLevel(logging.INFO)
        logger.info("TermExtractorAgent starting up on remote endpoint")
        self._orchestrator = Orchestrator(
            model=self._model,
            output_file=self._output_file,
            backend=self._backend,
            schema_path=self._schema_path,
            temperature=self._temperature,
            context_length=self._context_length,
            max_workers=self._max_workers,
            cborg_base=self._cborg_base,
            cborg_api_key=self._cborg_api_key,
            chebi_obo_path=self._chebi_obo_path,
        )
        logger.info("Orchestrator ready")

    # ------------------------------------------------------------------
    # Remote-callable actions
    # ------------------------------------------------------------------

    @action
    async def process_pdf(self, pdf_path: str) -> int:
        """Process a single PDF and return the number of pages that yielded terms."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._orchestrator.process_pdf, pdf_path)

    @action
    async def process_directory(self, data_dir: str) -> Dict[str, Any]:
        """Process all PDFs in data_dir and return extraction summary."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._orchestrator.process_directory, data_dir)

    @action
    async def process_page(self, text: str, filename: str, page_num: int) -> bool:
        """Process a single page of text. Returns True if terms were added/updated."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._orchestrator.process_page, text, filename, page_num
        )

    @action
    async def get_term_count(self) -> int:
        """Return the number of unique terms currently in the store."""
        return len(self._orchestrator.store)

    @action
    async def get_status(self) -> Dict[str, Any]:
        """Return current extraction metadata/progress."""
        return dict(self._orchestrator.store.metadata)
