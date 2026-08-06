import logging
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

import fitz
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.modules.cborg_http import openai_http_kwargs

from app.modules.cborg_limiter import sync_slot

from . import provenance, source_repos
from .agent import build_graph
from .clients import make_chat_client
from .models import ContextSnippet, RelationRecord, TermRecord
from .prompts import build_page_prompt
from .schema import SchemaHelper
from .services import Services, build_services, extract_and_attach_properties
from .store import TermStore
from .tools import ToolState, build_tools

logger = logging.getLogger(__name__)

_PAGE_STOPWORDS = {
    "about",
    "after",
    "also",
    "between",
    "could",
    "from",
    "have",
    "into",
    "paper",
    "papers",
    "relevant",
    "show",
    "shows",
    "that",
    "their",
    "there",
    "these",
    "this",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
    "without",
}


def _short_error(exc: Exception, *, limit: int = 800) -> str:
    text = str(exc)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "... [truncated]"


def _extract_terms_json_from_text(text: str) -> Dict[str, Any]:
    """Extract the largest JSON object containing a top-level ``terms`` key."""
    if not text:
        return {"terms": []}
    pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
    matches = list(re.finditer(pattern, text))
    matches.sort(key=lambda m: -len(m.group(0)))
    for match in matches:
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("terms"), list):
            return obj
    return {"terms": []}


def _context_for_term(text: str, term: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    term_lower = term.lower()
    for sentence in sentences:
        if term_lower in sentence.lower():
            return sentence.strip()
    return text.strip()[:500]


def _keyword_tokens(values: Sequence[str]) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for value in values:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}", str(value).lower()):
            if token in _PAGE_STOPWORDS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def _focus_phrases(values: Sequence[str]) -> List[str]:
    phrases: List[str] = []
    seen = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value).strip().lower())
        if len(text) < 4:
            continue
        if text not in seen:
            seen.add(text)
            phrases.append(text)
    return phrases


def _score_page_relevance(text: str, tokens: Sequence[str], phrases: Sequence[str]) -> float:
    if not text or not text.strip():
        return 0.0
    lower = text.lower()
    score = 0.0
    for phrase in phrases:
        if len(phrase.split()) > 1 and phrase in lower:
            score += 4.0
    for token in tokens:
        if re.search(rf"\b{re.escape(token)}\b", lower):
            score += 1.0
    if re.search(r"(?im)^\s*(abstract|introduction|methods?|results?|discussion|conclusions?)\b", text):
        score += 0.5
    return score


def _looks_code_like(text: str) -> bool:
    if not text:
        return False
    patterns = (
        r"```",
        r"\bdef\s+\w+\s*\(",
        r"\bclass\s+\w+",
        r"\bfunction\s+\w*\s*\(",
        r"\bimport\s+\w+",
        r"\bfor\s+\w+\s+in\s+",
        r"github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+",
        r">>>\s*\w+",
    )
    return any(re.search(pattern, text) for pattern in patterns)


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
        max_workers: int = 8,
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

        # Synchronous client used by the PDF-derived code-snippet context pass.
        self._snippet_client = make_chat_client(
            backend=self.backend,
            model=self.model,
            ollama_url=self.ollama_url,
            cborg_base=self.cborg_base,
            cborg_api_key=self.cborg_api_key,
        )

        llm_base = self._build_llm()
        tools = self._build_tools(llm_base)
        self.graph = build_graph(llm=llm_base.bind_tools(tools), tools=tools)
        logger.debug("Orchestrator ready: %d tools bound", len(tools))

    def _build_tools(self, llm_base: ChatOpenAI) -> list:
        def _llm_invoke(prompt: str) -> str:
            from langchain_core.messages import HumanMessage
            return llm_base.invoke([HumanMessage(content=prompt)]).content or ""

        state = ToolState(
            store=self.store,
            schema=self.schema_helper,
            services=self.services,
            llm_invoke=_llm_invoke,
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
            **openai_http_kwargs(asynchronous=False),
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
            with sync_slot(enabled=getattr(self, "backend", "cborg") == "cborg"):
                result = self.graph.invoke({"messages": [HumanMessage(content=prompt)]})
        except Exception as e:
            logger.error("Agent failed on %s page %d: %s", filename, page_num + 1, _short_error(e))
            added = self._fallback_json_extract(text, filename, page_num, schema_ctx)
            if added:
                self.store.save()
            return added
        added = len(self.store) > terms_before
        if not added:
            added = self._register_json_terms_from_result(result, filename, page_num, text)
        prop_updated = extract_and_attach_properties(text, self.store, self.services)
        if added or prop_updated:
            self.store.save()
        return added or prop_updated

    def _fallback_json_extract(
        self,
        text: str,
        filename: str,
        page_num: int,
        schema_ctx: str,
    ) -> bool:
        """Retry failed tool-call extraction as plain JSON, avoiding hosted-vLLM tool parsing."""
        fallback_prompt = (
            "Extract key materials-science terms from the page below. Return ONLY valid JSON "
            "with a top-level key \"terms\". Do not include prose, markdown, or tool calls. "
            "Each term should include term, definition, category, formula, and relations. "
            "Relations must be objects with relation and related_term keys.\n\n"
            f"schema_context:\n{schema_ctx}\n\n"
            f"PAPER: {filename}\nPAGE: {page_num + 1}\n\n"
            f"CONTENT:\n{text[-8000:] if len(text) > 8000 else text}"
        )
        try:
            content = self._snippet_client.chat(
                fallback_prompt,
                temperature=self.temperature,
                timeout=240,
            )
        except Exception as exc:
            logger.warning(
                "JSON fallback failed on %s page %d: %s",
                filename,
                page_num + 1,
                _short_error(exc),
            )
            return False
        result = {"messages": [SimpleNamespace(content=content)]}
        added = self._register_json_terms_from_result(result, filename, page_num, text)
        if added:
            logger.info("Recovered %s page %d with JSON fallback", filename, page_num + 1)
        return added

    def _register_json_terms_from_result(
        self,
        result: Dict[str, Any],
        filename: str,
        page_num: int,
        text: str,
    ) -> bool:
        """Persist JSON terms when the model answered directly instead of using tools."""
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if not messages:
            return False
        content = getattr(messages[-1], "content", "") or ""
        if isinstance(content, list):
            content = "\n".join(str(part) for part in content)
        data = _extract_terms_json_from_text(str(content))
        terms = data.get("terms", [])
        if not isinstance(terms, list) or not terms:
            return False

        updated = False
        for raw in terms:
            if not isinstance(raw, dict) or not str(raw.get("term", "")).strip():
                continue
            fixed = self.schema_helper.validate_and_fix_term(dict(raw))
            context = str(raw.get("context") or _context_for_term(text, fixed["term"]))
            record = TermRecord(
                term=fixed["term"],
                definition=fixed.get("definition", ""),
                category=fixed.get("category", "Thing"),
                raw_category=fixed.get("raw_category"),
                formula=fixed.get("formula"),
                relations=[RelationRecord.from_dict(r) for r in fixed.get("relations", [])],
                pages=[page_num + 1],
                source_papers=[filename],
                context_snippets=[
                    ContextSnippet(text=context, source_paper=filename, page=page_num + 1)
                ] if context else [],
            )
            _key, modified = self.store.upsert(record)
            updated = updated or modified
        if updated:
            logger.info("Registered %d JSON fallback term(s) from %s page %d", len(terms), filename, page_num + 1)
        return updated

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

        # PDF-derived publication metadata (never from the term LLM).
        try:
            pub_meta = provenance.extract_pub_metadata(doc, pdf_path)
        except Exception as e:
            logger.warning("Pub-metadata extraction failed for %s: %s", filename, e)
            pub_meta = {}

        logger.debug("Processing '%s' (%d pages) with %d workers", filename, total_pages, self.max_workers)
        page_texts = [doc.load_page(i).get_text() for i in range(total_pages)]

        def _process(page_num: int) -> bool:
            text = page_texts[page_num]
            added = self.process_page(text, filename, page_num)
            try:
                snips = provenance.extract_code_snippets(
                    text,
                    self._snippet_client,
                    self.schema_helper,
                    source_paper=filename,
                    page=page_num + 1,
                    temperature=self.temperature,
                )
                snips_added = self.store.add_code_snippets(snips, pub_meta=pub_meta)
            except Exception as e:
                logger.warning("Code-snippet pass failed on %s page %d: %s", filename, page_num + 1, e)
                snips_added = False
            return bool(added or snips_added)

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

        try:
            repo_snips = source_repos.extract_github_code_snippets(
                "\n\n".join(page_texts),
                source_paper=filename,
            )
            if self.store.add_code_snippets(repo_snips, pub_meta=pub_meta):
                self.store.increment("github_code_snippets", len(repo_snips))
                logger.info("Added %d GitHub-derived code snippet(s) for %s", len(repo_snips), filename)
        except Exception as e:
            logger.warning("GitHub source-code pass failed on %s: %s", filename, e)

        # Stamp source-scoped metadata onto every term/snippet citing this PDF.
        self.store.stamp_source_metadata(filename, pub_meta)
        self.store.save()

        self.store.increment("processed_files")
        self.store.increment("processed_pages_with_terms", pages_with_terms)
        logger.info("Finished '%s': %d/%d pages yielded terms", filename, pages_with_terms, total_pages)
        return pages_with_terms

    def select_relevant_pages(
        self,
        page_texts: Sequence[str],
        query: str,
        missing_topics: Optional[Sequence[str]] = None,
        *,
        max_pages: int = 6,
    ) -> List[int]:
        """Return zero-based page indices most relevant to the query/missing topics."""
        if max_pages <= 0:
            return []
        focus_values = [query, *(missing_topics or [])]
        tokens = _keyword_tokens(focus_values)
        phrases = _focus_phrases(focus_values)
        scored = [
            (idx, _score_page_relevance(text, tokens, phrases))
            for idx, text in enumerate(page_texts)
            if text and text.strip()
        ]
        if not scored:
            return []
        positives = [item for item in scored if item[1] > 0]
        pool = positives if positives else scored[: min(3, len(scored))]
        pool = sorted(pool, key=lambda item: (-item[1], item[0]))[:max_pages]
        return sorted(idx for idx, _score in pool)

    def process_pdf_targeted(
        self,
        pdf_path: str,
        *,
        query: str,
        missing_topics: Optional[Sequence[str]] = None,
        max_pages: int = 6,
    ) -> Dict[str, Any]:
        """Process query-relevant pages from one PDF while always extracting publication metadata."""
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error("Cannot open PDF %s: %s", pdf_path, e)
            return {
                "status": "error",
                "message": f"Cannot open PDF {pdf_path}: {e}",
                "filename": os.path.basename(pdf_path),
            }

        filename = os.path.basename(pdf_path)
        total_pages = doc.page_count

        try:
            pub_meta = provenance.extract_pub_metadata(doc, pdf_path)
        except Exception as e:
            logger.warning("Pub-metadata extraction failed for %s: %s", filename, e)
            pub_meta = {}

        page_texts = [doc.load_page(i).get_text() for i in range(total_pages)]
        selected_pages = self.select_relevant_pages(
            page_texts,
            query,
            missing_topics or [],
            max_pages=max_pages,
        )
        focus_values = [query, *(missing_topics or [])]
        tokens = _keyword_tokens(focus_values)
        phrases = _focus_phrases(focus_values)
        selected_scores = [
            _score_page_relevance(page_texts[page], tokens, phrases)
            for page in selected_pages
        ]
        low_confidence = bool(page_texts and (not selected_scores or max(selected_scores) <= 0))
        selected_one_based = [page + 1 for page in selected_pages]
        pages_with_terms = 0

        logger.info(
            "Targeted processing '%s': selected %d/%d page(s): %s",
            filename,
            len(selected_pages),
            total_pages,
            selected_one_based,
        )
        self.store.increment("processed_pages_total", len(selected_pages))

        def _process(page_num: int) -> bool:
            text = page_texts[page_num]
            added = self.process_page(text, filename, page_num)
            snips_added = False
            if _looks_code_like(text):
                try:
                    snips = provenance.extract_code_snippets(
                        text,
                        self._snippet_client,
                        self.schema_helper,
                        source_paper=filename,
                        page=page_num + 1,
                        temperature=self.temperature,
                    )
                    snips_added = self.store.add_code_snippets(snips, pub_meta=pub_meta)
                except Exception as e:
                    logger.warning("Code-snippet pass failed on %s page %d: %s", filename, page_num + 1, e)
            return bool(added or snips_added)

        if selected_pages:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(selected_pages))) as exe:
                futures = {exe.submit(_process, page_num): page_num for page_num in selected_pages}
                for fut in as_completed(futures):
                    page_i = futures[fut]
                    try:
                        if fut.result():
                            pages_with_terms += 1
                            logger.debug("Targeted page %d/%d of %s yielded terms", page_i + 1, total_pages, filename)
                    except Exception as e:
                        logger.error("Error on targeted page %d of %s: %s", page_i + 1, filename, e)

        self.store.stamp_source_metadata(filename, pub_meta)
        self.store.save()
        self.store.increment("processed_files")
        self.store.increment("processed_pages_with_terms", pages_with_terms)
        self.store.increment("partial_processed_files")
        logger.info(
            "Finished targeted '%s': %d/%d selected pages yielded terms",
            filename,
            pages_with_terms,
            len(selected_pages),
        )
        return {
            "status": "success",
            "filename": filename,
            "extraction_state": "partial",
            "source_pages_total": total_pages,
            "selected_pages": selected_one_based,
            "selected_page_count": len(selected_pages),
            "processed_pages_total": len(selected_pages),
            "processed_pages_with_terms": pages_with_terms,
            "unique_terms": len(self.store),
            "output_file": self.store.output_file,
            "low_confidence": low_confidence,
        }

    def process_directory_targeted(
        self,
        data_dir: str,
        *,
        query: str,
        missing_topics: Optional[Sequence[str]] = None,
        max_pages: int = 6,
    ) -> Dict[str, Any]:
        """Walk data_dir and partially process PDFs against one query."""
        if not os.path.isdir(data_dir):
            msg = f"Directory not found: {data_dir}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        pdfs = sorted(f for f in os.listdir(data_dir) if f.lower().endswith(".pdf"))
        if not pdfs:
            logger.warning("No PDFs in %s", data_dir)

        pdf_results: List[Dict[str, Any]] = []
        for idx, fname in enumerate(pdfs, start=1):
            logger.info("[%d/%d] Targeted processing: %s", idx, len(pdfs), fname)
            result = self.process_pdf_targeted(
                os.path.join(data_dir, fname),
                query=query,
                missing_topics=missing_topics or [],
                max_pages=max_pages,
            )
            pdf_results.append(result)

        self.store.assign_importance()
        self.store.save()

        processed_files = sum(1 for item in pdf_results if item.get("status") == "success")
        processed_pages_total = sum(int(item.get("processed_pages_total") or 0) for item in pdf_results)
        processed_pages_with_terms = sum(int(item.get("processed_pages_with_terms") or 0) for item in pdf_results)
        source_pages_total = sum(int(item.get("source_pages_total") or 0) for item in pdf_results)
        logger.info(
            "Targeted done. Files: %d, Selected pages: %d, Pages w/ terms: %d, Unique terms: %d",
            processed_files,
            processed_pages_total,
            processed_pages_with_terms,
            len(self.store),
        )
        return {
            "status": "success",
            "extraction_mode": "targeted",
            "processed_files": processed_files,
            "processed_pages_total": processed_pages_total,
            "source_pages_total": source_pages_total,
            "processed_pages_with_terms": processed_pages_with_terms,
            "unique_terms": len(self.store),
            "output_file": self.store.output_file,
            "pdf_results": pdf_results,
        }

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
    max_workers: int = 8,
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
