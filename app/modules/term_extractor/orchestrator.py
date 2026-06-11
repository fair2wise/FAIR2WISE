import os
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage

from .agent import build_graph


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
        # TODO: init SchemaHelper, ChemicalFormulaValidator, ChebiOboLookup here
        # TODO: pass llm= into build_graph so agent_node can use it
        self.graph = build_graph()

    def process_page(self, text: str, filename: str, page_num: int) -> bool:
        # TODO: build prompt, invoke graph, return whether any terms were registered
        pass

    def process_pdf(self, pdf_path: str) -> int:
        # TODO: open PDF, iterate pages, call process_page per page in parallel
        pass

    def process_directory(self, data_dir: str) -> dict:
        # TODO: walk directory, call process_pdf per file, compute importance, save
        pass


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
    """Drop-in replacement for extract_terms_agent.run_extraction."""
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
