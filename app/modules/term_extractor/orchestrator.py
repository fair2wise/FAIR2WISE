import datetime
import json
import logging
import os
from pathlib import Path
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional

import fitz
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from ..agents.chebi import ChebiOboLookup
from ..agents.chem_checker import ChemicalFormulaValidator
from ..agents.properties import PhysicalPropertyExtractor, PropertyNormalizer
from .agent import build_graph
from .prompts import build_page_prompt
from .schema import SchemaHelper
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
    ):
        self.model = model
        self.output_file = output_file
        self.backend = backend
        self.schema_path = schema_path
        self.temperature = temperature
        self.context_length = context_length
        self.max_workers = max_workers
        self.cborg_base = cborg_base or os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
        self.cborg_api_key = cborg_api_key or os.environ.get("CBORG_API_KEY")
        self.ollama_url = ollama_url

        # Step 1: load schema / helpers
        self.schema_helper = SchemaHelper(schema_path=schema_path)

        mp_api_key = os.environ.get("MP_API_KEY", "")
        if not mp_api_key:
            logger.warning("MP_API_KEY not set; formula validation may be incomplete.")
        self.formula_checker = ChemicalFormulaValidator(api_key=mp_api_key or "JziDvAj2FWxzonCe2hketK1yz4bKHRlA") # To add to tools later

        # ChebiOboLookup is actually not used
        try:
            self.chebi_lookup = ChebiOboLookup("storage/ontologies/chebi.obo")
        except Exception as e:
            logger.warning("Failed to load ChEBI ontology: %s", e)
            self.chebi_lookup = None

        self.prop_extractor = PhysicalPropertyExtractor()
        self.prop_normalizer = PropertyNormalizer()

        # Step 2: initialise in-memory state + load existing output JSON
        self.terms_dict: Dict[str, Dict[str, Any]] = {}
        self._bk_terms: Dict[str, str] = {}  # display_text → normalised key
        self.metadata: Dict[str, Any] = {
            "extraction_date": datetime.datetime.utcnow().isoformat() + "Z",
            "processed_files": 0,
            "processed_pages_total": 0,
            "processed_pages_with_terms": 0,
            "version": "2.1",
        }
        self._state_lock = threading.Lock()  # guards terms_dict / _bk_terms
        self._save_lock = threading.Lock()
        self._tl = threading.local()  # per-thread dirty flag set by tools

        os.makedirs(os.path.dirname(os.path.abspath(self.output_file)), exist_ok=True)
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file) as fh:
                    prev = json.load(fh)
                for term in prev.get("terms", []):
                    key = term["term"].strip().lower()
                    self.terms_dict[key] = term
                    self._bk_terms[term["term"]] = key
                self.metadata.update(prev.get("metadata", {}))
                logger.info("Loaded %d existing terms from %s", len(self.terms_dict), self.output_file)
            except Exception as e:
                logger.warning("Could not load previous terms from %s: %s", self.output_file, e)

        # Build tools as closures, then wire up LLM + graph
        tools = self._build_tools()
        llm = self._build_llm().bind_tools(tools)
        self.graph = build_graph(llm=llm, tools=tools)

    def _build_tools(self) -> list:
        state = ToolState(
            terms_dict=self.terms_dict,
            bk_terms=self._bk_terms,
            state_lock=self._state_lock,
            schema_helper=self.schema_helper,
            formula_checker=self.formula_checker,
            chebi_lookup=self.chebi_lookup,
            mark_updated=self._mark_updated,
        )
        return build_tools(state)

    def _build_llm(self) -> ChatOpenAI:
        """Return a LangChain ChatOpenAI instance for the configured backend."""
        if self.backend == "ollama":
            # Ollama exposes an OpenAI-compatible /v1 endpoint
            return ChatOpenAI(
                model=self.model,
                base_url=self.ollama_url.rstrip("/") + "/v1",
                api_key="ollama",
                temperature=self.temperature,
            )
        # cborg / cborg-openai
        return ChatOpenAI(
            model=self.model,
            api_key=self.cborg_api_key,
            base_url=self.cborg_base,
            temperature=self.temperature,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def normalize_term(self, term: str) -> str:
        return term.strip().lower()

    def _looks_like_formula(self, s: str) -> bool:
        return bool(re.search(r"[A-Z][a-z]?[\d]", s or ""))

    def _save_terms_threadsafe(self) -> None:
        with self._save_lock:
            try:
                terms_out = []
                for t in self.terms_dict.values():
                    if "properties" not in t:
                        t["properties"] = []
                    terms_out.append(t)
                out = {"metadata": self.metadata, "terms": terms_out}
                with open(self.output_file, "w") as fh:
                    json.dump(out, fh, indent=2)
                logger.debug("Saved %d terms to %s", len(self.terms_dict), self.output_file)
            except Exception as e:
                logger.error("Failed to save terms: %s", e)

    def _extract_and_attach_properties(self, full_text: str) -> bool:
        if not self.terms_dict:
            return False
        material_names = [t["term"] for t in self.terms_dict.values()]
        raw_props = self.prop_extractor.extract(full_text, material_names)
        if not raw_props:
            return False
        normalized_props = self.prop_normalizer.normalize(raw_props)
        updated = False
        for p in normalized_props:
            mat_key = self.normalize_term(p["material"])
            if mat_key not in self.terms_dict:
                continue
            props_list = self.terms_dict[mat_key].setdefault("properties", [])
            existing = {(pr["property"], pr["value"], pr["unit"], pr["context"]) for pr in props_list}
            tup = (p["property"], p["normalized_value"], p["normalized_unit"], p["context"])
            if tup not in existing:
                props_list.append({
                    "property": p["property"],
                    "value": p["normalized_value"],
                    "unit": p["normalized_unit"],
                    "uncertainty": p.get("uncertainty_value"),
                    "context": p["context"],
                    "verified": not p["unit_conversion_failed"],
                })
                logger.info("Attached property '%s' to '%s'", p["property"], p["material"])
                updated = True
        return updated

    def _mark_updated(self) -> None:
        """Called by a tool closure to signal that terms_dict was modified."""
        self._tl.updated = True

    def _consume_updated(self) -> bool:
        updated = getattr(self._tl, "updated", False)
        self._tl.updated = False
        return updated

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
        self._tl.updated = False
        try:
            self.graph.invoke({"messages": [HumanMessage(content=prompt)]})
        except Exception as e:
            logger.error("Agent failed on %s page %d: %s", filename, page_num + 1, e)
            return False
        added = self._consume_updated()
        prop_updated = self._extract_and_attach_properties(text)
        if added or prop_updated:
            self._save_terms_threadsafe()
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
        self.metadata["processed_pages_total"] += total_pages
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

        self.metadata["processed_files"] += 1
        self.metadata["processed_pages_with_terms"] += pages_with_terms
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

        for term_data in self.terms_dict.values():
            occ = len(term_data.get("pages", []))
            papers = len(set(term_data.get("source_papers", [])))
            if papers > 1 or occ > 5:
                term_data["importance"] = "high"
            elif occ > 2:
                term_data["importance"] = "medium"
            else:
                term_data["importance"] = "low"

        self._save_terms_threadsafe()
        logger.info(
            "Done. Files: %d, Pages total: %d, Pages w/ terms: %d, Unique terms: %d",
            self.metadata["processed_files"],
            self.metadata["processed_pages_total"],
            self.metadata["processed_pages_with_terms"],
            len(self.terms_dict),
        )
        return {
            "status": "success",
            "processed_files": self.metadata["processed_files"],
            "processed_pages_total": self.metadata["processed_pages_total"],
            "processed_pages_with_terms": self.metadata["processed_pages_with_terms"],
            "unique_terms": len(self.terms_dict),
            "output_file": self.output_file,
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
) -> dict:
    """Drop-in replacement for extract_terms.run_extraction."""
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
    )
    return o.process_directory(str(pdf_dir))
