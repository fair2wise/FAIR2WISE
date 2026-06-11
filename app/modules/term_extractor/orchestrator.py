import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional

import fitz
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from .agent import build_graph
from .prompts import build_page_prompt
from .schema import SchemaHelper
from .services import Services, build_services, extract_and_attach_properties
from .store import TermStore
from .tools import ToolState, build_tools

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        model: str,
        output_file: str,
        *,
        backend: str = "cborg",
        schema_path: str = "storage/schema/matkg_schema.yaml",
        temperature: float = 0.0,
        context_length: int = 50,
        max_workers: int = 4,
        cborg_base: Optional[str] = None,
        cborg_api_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        chebi_obo_path: Optional[str] = "storage/ontologies/chebi.obo",
    ):
        self.model = model
        self.backend = backend
        self.temperature = temperature
        self.context_length = context_length
        self.max_workers = max_workers
        self.cborg_base = cborg_base or os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
        self.cborg_api_key = cborg_api_key or os.environ.get("CBORG_API_KEY")
        self.ollama_url = ollama_url

        logger.info(
            "Initializing Orchestrator: model=%s backend=%s workers=%d output=%s",
            model, backend, max_workers, output_file,
        )
        self.schema_helper = SchemaHelper(schema_path=schema_path)
        self.store = TermStore(output_file)
        self.services = build_services(chebi_obo_path=chebi_obo_path)

        tools = self._build_tools()
        llm = self._build_llm().bind_tools(tools)
        self.graph = build_graph(llm=llm, tools=tools)
        logger.debug("Orchestrator ready: %d tools bound", len(tools))

    def _build_tools(self) -> list:
        state = ToolState(
            store=self.store,
            schema=self.schema_helper,
            services=self.services,
        )
        return build_tools(state)

    def _build_llm(self) -> ChatOpenAI:
        if self.backend == "ollama":
            logger.debug("Building LLM: backend=ollama url=%s model=%s", self.ollama_url, self.model)
            return ChatOpenAI(
                model=self.model,
                base_url=self.ollama_url.rstrip("/") + "/v1",
                api_key="ollama",
                temperature=self.temperature,
            )
        logger.debug("Building LLM: backend=cborg base=%s model=%s", self.cborg_base, self.model)
        return ChatOpenAI(
            model=self.model,
            api_key=self.cborg_api_key,
            base_url=self.cborg_base,
            temperature=self.temperature,
        )

    # ------------------------------------------------------------------
    # Processing pipeline
    # ------------------------------------------------------------------

    def process_page(self, text: str, filename: str, page_num: int) -> bool:
        """Invoke the agent graph on one page of text. Returns True if any terms were added/updated."""
        if not text or len(text.split()) < 20:
            logger.info("Skipping page %d of %s (insufficient text).", page_num + 1, filename)
            return False
        logger.debug("process_page: %s page %d", filename, page_num + 1)
        schema_ctx = self.schema_helper.get_schema_context_for_llm()
        prompt = build_page_prompt(schema_ctx, filename, page_num, text)
        terms_before = len(self.store)
        try:
            self.graph.invoke({"messages": [HumanMessage(content=prompt)]})
        except Exception as e:
            logger.error("Agent failed on %s page %d: %s", filename, page_num + 1, e)
            return False
        added = len(self.store) > terms_before
        prop_updated = extract_and_attach_properties(text, self.store, self.services)
        if added or prop_updated:
            self.store.save()
        return added or prop_updated

    def process_pdf(self, pdf_path: str) -> int:
        """Open a PDF and process all pages in parallel. Returns pages that yielded terms."""
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error("Cannot open PDF %s: %s", pdf_path, e)
            return 0
        filename = os.path.basename(pdf_path)
        total_pages = doc.page_count
        self.store.increment("processed_pages_total", total_pages)
        pages_with_terms = 0
        logger.debug("Processing '%s' (%d pages) with %d workers", filename, total_pages, self.max_workers)

        def _process(page_num: int) -> bool:
            return self.process_page(doc.load_page(page_num).get_text(), filename, page_num)

        with ThreadPoolExecutor(max_workers=self.max_workers) as exe:
            futures = {exe.submit(_process, i): i for i in range(total_pages)}
            for fut in as_completed(futures):
                page_i = futures[fut]
                try:
                    if fut.result():
                        pages_with_terms += 1
                        logger.debug("Page %d/%d of %s yielded terms", page_i + 1, total_pages, filename)
                    else:
                        logger.debug("Page %d/%d of %s: no new terms", page_i + 1, total_pages, filename)
                except Exception as e:
                    logger.error("Error on page %d of %s: %s", page_i + 1, filename, e)

        self.store.increment("processed_files")
        self.store.increment("processed_pages_with_terms", pages_with_terms)
        logger.info("Finished '%s': %d/%d pages yielded terms", filename, pages_with_terms, total_pages)
        return pages_with_terms

    def process_directory(self, data_dir: str) -> Dict[str, Any]:
        """Walk data_dir, process all PDFs, assign importance scores, save final output."""
        if not os.path.isdir(data_dir):
            msg = f"Directory not found: {data_dir}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        pdfs = sorted(f for f in os.listdir(data_dir) if f.lower().endswith(".pdf"))
        if not pdfs:
            logger.warning("No PDFs in %s", data_dir)

        for idx, fname in enumerate(pdfs, start=1):
            logger.info("[%d/%d] Processing: %s", idx, len(pdfs), fname)
            self.process_pdf(os.path.join(data_dir, fname))

        self.store.assign_importance()
        self.store.save()

        meta = self.store.metadata
        logger.info(
            "Done. Files: %d, Pages total: %d, Pages w/ terms: %d, Unique terms: %d",
            meta.get("processed_files", 0),
            meta.get("processed_pages_total", 0),
            meta.get("processed_pages_with_terms", 0),
            len(self.store),
        )
        return {
            "status": "success",
            "processed_files": meta.get("processed_files", 0),
            "processed_pages_total": meta.get("processed_pages_total", 0),
            "processed_pages_with_terms": meta.get("processed_pages_with_terms", 0),
            "unique_terms": len(self.store),
            "output_file": self.store.output_file,
        }


def run_extraction(
    pdf_dir: Path,
    output_json: Path,
    *,
    model: str,
    backend: str = "cborg",
    cborg_base: Optional[str] = None,
    cborg_api_key: Optional[str] = None,
    ollama_url: str = "http://localhost:11434",
    schema_path: str = "storage/schema/matkg_schema.yaml",
    temperature: float = 0.0,
    context_length: int = 50,
    max_workers: int = 4,
    chebi_obo_path: Optional[str] = None,
) -> dict:
    """Drop-in replacement for extract_terms.run_extraction."""
    logger.info("run_extraction: dir=%s output=%s model=%s backend=%s", pdf_dir, output_json, model, backend)
    o = Orchestrator(
        model=model,
        output_file=str(output_json),
        backend=backend,
        schema_path=schema_path,
        temperature=temperature,
        context_length=context_length,
        max_workers=max_workers,
        cborg_base=cborg_base,
        cborg_api_key=cborg_api_key,
        ollama_url=ollama_url,
        chebi_obo_path=chebi_obo_path,
    )
    return o.process_directory(str(pdf_dir))
