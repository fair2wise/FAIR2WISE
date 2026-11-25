"""Drive LLM term extraction over PDFs, page by page, into a ``TermStore``."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from . import passages, provenance, source_repos
from ..cborg_errors import IP_NOT_AUTHORIZED_LOG_TEXT, CborgIpNotAuthorized, as_ip_not_authorized
from ..cborg_http import (
    LLM_MAX_RETRIES,
    call_with_deadline,
    llm_timeout,
    openai_http_kwargs,
    require_cborg_api_key,
)
from ..cborg_limiter import sync_slot
from ..literature.pdf_reader import open_pdf
from ..llm_admission import shared_admission_enabled
from ..llm_failures import plain_call_failure, provider_label
from ..model_policy import require_free_cborg_model
from .agent import build_graph
from .clients import make_chat_client
from .merge_index import DEFAULT_EMBED_MODEL, DEFAULT_MERGE_TOP_K, TermMergeIndex
from .models import RelationRecord, TermRecord, union_evidence
from .pub_clean import merge_known_publication
from .prompts import build_page_prompt
from .schema import SchemaHelper
from .services import build_services, extract_and_attach_properties
from .store import TermStore
from .tools import ToolState, build_tools

logger = logging.getLogger(__name__)

#: Passage coverage counts per paper (see :meth:`TermStore.run_passage_stats`) and the share of terms with a passage
#: below which a paper is reported with a WARNING (design d: 90%).
PASSAGE_COUNTS = ("terms", "with_passage", "quoted", "located", "rejected", "uncovered", "relations",
                  "relations_with_evidence", "evidence_sentences", "relations_without")
COVERAGE_WARN_RATIO = 0.9
#: Share of a paper's relations that must have an evidence sentence before the log stops warning (design e: 50%).
RELATION_WARN_RATIO = 0.5

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


def _extract_terms_json_from_text(text: str) -> dict[str, Any]:
    """Extract the largest JSON object containing a top-level ``terms`` key."""
    if not text:
        return {"terms": []}
    pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
    matches = list(re.finditer(pattern, text))
    matches.sort(key=lambda m: -len(m.group(0)))
    for match in matches:
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:  # reply is not JSON: no object
            continue
        if isinstance(obj, dict) and isinstance(obj.get("terms"), list):
            return obj
    return {"terms": []}


def _keyword_tokens(values: Sequence[str]) -> list[str]:
    tokens: list[str] = []
    seen = set()
    for value in values:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}", str(value).lower()):
            if token in _PAGE_STOPWORDS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def _focus_phrases(values: Sequence[str]) -> list[str]:
    phrases: list[str] = []
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
    """Extract terms from PDF pages with an LLM agent and persist them via ``TermStore``.

    Builds the schema helper, store, services and merge index once; pages are
    processed on a thread pool of ``max_workers``.
    """

    #: Class-level defaults so an Orchestrator built without ``__init__`` (test doubles) is unbounded.
    schema_prompt_budget: int | None = None
    #: ``file name -> the library's publication metadata of that paper`` (set by the library extractor); ``None`` reads the PDF only.
    known_publications: Callable[[str], dict[str, Any] | None] | None = None
    _bounded_context: tuple[str, dict[str, int] | None] | None = None

    #: Polled before each page; ``True`` ends the work (the owning job stopped or lost its lease).
    cancel_check: Callable[[], bool] | None = None
    #: Set by the first CBORG ``ip_not_authorized`` refusal; once set no further LLM call is made (it is terminal
    #: for this orchestrator: another page, retry or fallback call would be refused the same way).
    refusal: CborgIpNotAuthorized | None = None
    #: Optional progress sink ``events(kind, **info)`` for a job's live view. Kinds: ``paper_opened`` (``name``,
    #: ``pages``), ``page_done`` (``name``, ``done``, ``total``), ``paper_merging`` (``name``). Called from page
    #: threads; an exception it raises is swallowed (progress must never fail an extraction).
    events: Callable[..., None] | None = None

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
        cborg_base: str | None = None,
        cborg_api_key: str | None = None,
        ollama_url: str = "http://localhost:11434",
        chebi_obo_path: str | None = "storage/ontologies/chebi.obo",
        merge_top_k: int = DEFAULT_MERGE_TOP_K,
        merge_embed_model: str = "",
        sanitize_schema_descriptions: bool = False,
        schema_prompt_budget: int | None = None,
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
        # The flag is passed only when set, so every shipped-schema construction is exactly what it was.
        self.schema_helper = SchemaHelper(schema_path=schema_path, **({"sanitize_descriptions": True} if sanitize_schema_descriptions else {}))
        # Set only for user schemas (see the library extractor): the prompt text is bounded; shipped schemas stay unbounded.
        self.schema_prompt_budget = schema_prompt_budget
        self._bounded_context = None
        self.store = TermStore(output_file)
        self.services = build_services(chebi_obo_path=chebi_obo_path)
        self.merge_index = TermMergeIndex(
            model_name=merge_embed_model or DEFAULT_EMBED_MODEL,
            top_k=merge_top_k,
        )
        logger.info("Building term merge index from %d existing names", len(self.store))
        try:
            self.merge_index.ensure(self.store.all_display_names())
        except Exception:
            logger.exception("Failed to build term merge index")
            raise

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

    def _build_tools(self, llm_base: ChatOpenAI) -> list[BaseTool]:
        def _llm_invoke(prompt: str) -> str:
            from langchain_core.messages import HumanMessage

            # A hung transport must not pin the calling page thread for ever: bound the whole call.
            content = call_with_deadline(lambda: llm_base.invoke([HumanMessage(content=prompt)])).content
            return content if isinstance(content, str) else str(content or "")

        self._tool_state = ToolState(
            store=self.store,
            schema=self.schema_helper,
            services=self.services,
            llm_invoke=_llm_invoke,
            merge_index=self.merge_index,
        )
        return build_tools(self._tool_state)

    def _uses_cborg_admission(self) -> bool:
        """Whether page calls take a CBORG slot.

        Legacy: only backend ``"cborg"``. With shared admission (H1-3) the
        ``"cborg-openai"`` backend, which also reaches CBORG, joins the cap too.
        """
        backend = getattr(self, "backend", "cborg")
        if backend == "cborg":
            return True
        return backend == "cborg-openai" and shared_admission_enabled()

    def _build_llm(self) -> ChatOpenAI:
        if self.backend == "ollama":
            logger.debug("Building LLM: backend=ollama url=%s model=%s", self.ollama_url, self.model)
            return ChatOpenAI(
                model=self.model,
                base_url=self.ollama_url.rstrip("/") + "/v1",
                api_key=SecretStr("ollama"),
                temperature=self.temperature,
                timeout=llm_timeout().read,
                max_retries=LLM_MAX_RETRIES,
            )
        logger.debug("Building LLM: backend=cborg base=%s model=%s", self.cborg_base, self.model)
        # Free-model policy (owner decision 2026-11-09): refused before the key is read or a client exists.
        model = require_free_cborg_model(self.model, context="extraction model")
        return ChatOpenAI(
            model=model,
            api_key=SecretStr(require_cborg_api_key(self.cborg_api_key)),
            base_url=self.cborg_base,
            temperature=self.temperature,
            streaming=True,
            timeout=llm_timeout().read,
            max_retries=LLM_MAX_RETRIES,
            http_socket_options=(),
            **openai_http_kwargs(asynchronous=False),
        )

    # ------------------------------------------------------------------
    # Processing pipeline
    # ------------------------------------------------------------------

    def _emit(self, kind: str, **info: Any) -> None:  # noqa: ANN401  # Any: small JSON-like event fields
        """Pass one progress event to :attr:`events`; never raises."""
        sink = self.events
        if sink is None:
            return
        try:
            sink(kind, **info)
        except Exception:  # noqa: BLE001 - a progress sink (lease lost, stop requested) must not break extraction
            return

    def _describe(self, exc: BaseException) -> str:
        """Plain-words text of a failed model call (provider, model id, reason; nothing from the provider's message)."""
        provider, model = self._provider_model()
        return plain_call_failure(exc, provider=provider, model=model)

    def _provider_model(self) -> tuple[str, str]:
        """``(cborg|ollama, model id)`` this orchestrator calls (for log lines)."""
        return provider_label(getattr(self, "backend", "cborg")), str(getattr(self, "model", "?"))

    def _note_refusal(self, exc: BaseException) -> bool:
        """Record CBORG's address refusal (once, with the fixed text only); True when ``exc`` is that refusal."""
        refusal = as_ip_not_authorized(exc)
        if refusal is None:
            return False
        if self.refusal is None:
            self.refusal = refusal
            logger.error("Extraction stopped: %s", IP_NOT_AUTHORIZED_LOG_TEXT)
        return True

    def _schema_context(self) -> str:
        """The schema text of the page prompt: the full rendering, or the bounded one when a budget is set (computed once)."""
        if self.schema_prompt_budget is None:
            return self.schema_helper.get_schema_context_for_llm()
        if self._bounded_context is None:
            self._bounded_context = self.schema_helper.bounded_schema_context(self.schema_prompt_budget)
        return self._bounded_context[0]

    def schema_prompt_condensed(self) -> dict[str, int] | None:
        """``{"full_chars", "used_chars", "budget"}`` when the schema prompt is condensed for this run, else ``None``."""
        if self.schema_prompt_budget is None:
            return None
        self._schema_context()
        return self._bounded_context[1] if self._bounded_context else None

    def process_page(self, text: str, filename: str, page_num: int) -> bool:
        """Invoke the agent graph on one page of text. Returns True if any terms were added/updated."""
        if self.cancel_check is not None and self.cancel_check():
            return False  # the owning job stopped or lost its lease: no more LLM calls for it
        if self.refusal is not None:
            return False  # CBORG refused this network address: terminal, no further call
        if not text or len(text.split()) < 20:
            logger.info("Skipping page %d of %s (insufficient text).", page_num + 1, filename)
            return False
        provider, model = self._provider_model()
        logger.info("%s page %d: asking %s model %s", filename, page_num + 1, provider, model)
        schema_ctx = self._schema_context()
        prompt = build_page_prompt(schema_ctx, filename, page_num, text)
        terms_before = len(self.store)
        tool_state = getattr(self, "_tool_state", None)
        if tool_state is not None:
            tool_state.set_page_context(filename, page_num + 1, text)
        try:
            try:
                with sync_slot(enabled=self._uses_cborg_admission()):
                    result = self.graph.invoke({"messages": [HumanMessage(content=prompt)]})
            except Exception as e:
                if self._note_refusal(e):
                    return False  # terminal: no fallback call, nothing that could echo the refused address
                logger.error("%s page %d: %s", filename, page_num + 1, self._describe(e))
                added = self._fallback_json_extract(text, filename, page_num, schema_ctx)
                if added:
                    self.store.save()
                return added
        finally:
            if tool_state is not None:
                tool_state.clear_page_context()
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
            if not self._note_refusal(exc):
                logger.warning("%s page %d: JSON fallback failed: %s", filename, page_num + 1, self._describe(exc))
            return False
        result = {"messages": [SimpleNamespace(content=content)]}
        added = self._register_json_terms_from_result(result, filename, page_num, text)
        if added:
            logger.info("Recovered %s page %d with JSON fallback", filename, page_num + 1)
        return added

    def _register_json_terms_from_result(
        self,
        result: dict[str, Any],
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
            # Grounding (design b): the sentence is kept only if found in the paper's text; else the mention is located.
            tool_state = getattr(self, "_tool_state", None)
            held = getattr(tool_state, "paper_pages", None)
            grounded = passages.ground_context(
                term=fixed["term"],
                sentence=str(raw.get("context") or "") or None,
                source_paper=filename,
                page=page_num + 1,
                page_text=text,
                paper_pages=held[1] if held and held[0] == filename else None,
            )
            snippet = grounded.snippet
            self.store.note_passage(
                filename, TermStore.normalize(fixed["term"]), kind=snippet.kind if snippet else None,
                rejected=int(grounded.rejected),
            )
            relation_records = [RelationRecord.from_dict(r) for r in fixed.get("relations", [])]
            for relation in relation_records:   # located relation evidence: sentences naming both endpoints (no model)
                entries = passages.relation_evidence_for(
                    subject=fixed["term"], obj=relation.related_term, source_paper=filename, page=page_num + 1,
                    page_text=text, paper_pages=held[1] if held and held[0] == filename else None)
                union_evidence(relation.evidence, entries)
                self.store.note_relation(
                    filename, f"{TermStore.normalize(fixed['term'])}|{relation.relation}|"
                    f"{TermStore.normalize(relation.related_term)}", len(entries))
            record = TermRecord(
                term=fixed["term"],
                definition=fixed.get("definition", ""),
                category=fixed.get("category", "Thing"),
                raw_category=fixed.get("raw_category"),
                formula=fixed.get("formula"),
                relations=relation_records,
                pages=[page_num + 1],
                source_papers=[filename],
                context_snippets=[snippet] if snippet else [],
            )
            _key, modified = self.store.upsert(record)
            updated = updated or modified
        if updated:
            logger.info("Registered %d JSON fallback term(s) from %s page %d", len(terms), filename, page_num + 1)
        return updated

    def _set_paper_pages(self, filename: str, page_texts: list[str]) -> None:
        """Hand the paper's page texts (read once per paper) to the tools for passage grounding."""
        tool_state = getattr(self, "_tool_state", None)
        if tool_state is not None:
            tool_state.paper_pages = (filename, page_texts)

    def _finish_paper_passages(self, filename: str) -> dict[str, int]:
        """Drop the paper's page texts, log its passage coverage and return its counts (this run only).

        One info line per paper; a WARNING when fewer than ``COVERAGE_WARN_RATIO`` of its terms carry a passage.
        """
        tool_state = getattr(self, "_tool_state", None)
        if tool_state is not None:
            tool_state.paper_pages = None
        row = self.store.run_passage_stats().get(filename) or dict.fromkeys(PASSAGE_COUNTS, 0)
        logger.info(
            "passages %s: %d terms, %d with a passage (%d quoted, %d located), %d sentences rejected, %d uncovered",
            filename, row["terms"], row["with_passage"], row["quoted"], row["located"], row["rejected"], row["uncovered"],
        )
        if row["terms"] and row["with_passage"] < COVERAGE_WARN_RATIO * row["terms"]:
            logger.warning(
                "passages %s: only %d of %d terms (%.0f%%) have a supporting passage (threshold %.0f%%)",
                filename, row["with_passage"], row["terms"], 100 * row["with_passage"] / row["terms"],
                100 * COVERAGE_WARN_RATIO,
            )
        if row["relations"]:
            logger.info(
                "relation evidence %s: %d relations, %d with a sentence naming both ends (%d sentences stored), %d without",
                filename, row["relations"], row["relations_with_evidence"], row["evidence_sentences"],
                row["relations_without"],
            )
            if row["relations_with_evidence"] < RELATION_WARN_RATIO * row["relations"]:
                logger.warning(
                    "relation evidence %s: only %d of %d relations (%.0f%%) have a sentence naming both ends (threshold %.0f%%)",
                    filename, row["relations_with_evidence"], row["relations"],
                    100 * row["relations_with_evidence"] / row["relations"], 100 * RELATION_WARN_RATIO,
                )
        return row

    def _publication_metadata(self, filename: str, pdf_meta: dict[str, Any]) -> dict[str, Any]:
        """The library's own record of the paper first, then the PDF's fields for what it lacks (junk never used)."""
        known = None
        lookup = self.known_publications
        if lookup is not None:
            try:
                known = lookup(filename)
            except Exception as e:  # noqa: BLE001 - a record that cannot be read leaves the PDF's fields
                logger.warning("Publication record lookup failed for %s: %s", filename, type(e).__name__)
        return merge_known_publication(pdf_meta, known)

    def relation_check_summary(self) -> dict[str, Any]:
        """The relation repairs of this run (see ``TermStore.relation_check_report``), logged once per distinct pattern.

        ``{}`` when nothing was repaired. One log line per pattern with its count, not one per occurrence.
        """
        report = self.store.relation_check_report()
        for row in report.get("patterns", []):
            what = {"attribute": "stored as an attribute of the term, not a relation",
                    "swapped": "was the wrong way round and was stored swapped, verified",
                    "invalid": "does not fit the schema and is marked verified=False"}[row["outcome"]]
            shape = row["relation"] if row["outcome"] == "attribute" else f"{row['subject_class']} -{row['relation']}-> {row['object_class']}"
            logger.warning("relation %s %s: %d times", shape, what, row["count"])
        return report

    def passage_summary(self) -> dict[str, Any]:
        """Passage coverage of this run for the run summary: ``total`` and ``papers`` (per file name)."""
        papers = self.store.run_passage_stats()
        total = dict.fromkeys(PASSAGE_COUNTS, 0)
        for row in papers.values():
            for key in total:
                total[key] += row[key]
        return {"total": total, "papers": papers}

    def process_pdf(self, pdf_path: str) -> int:
        """Open a PDF and process all pages in parallel. Returns pages that yielded terms."""
        try:
            doc = open_pdf(pdf_path)
        except Exception as e:
            logger.error("Cannot open PDF %s: %s", Path(pdf_path).name, type(e).__name__)
            return 0
        filename = Path(pdf_path).name
        total_pages = doc.page_count
        self._emit("paper_opened", name=filename, pages=total_pages)
        self.store.increment("processed_pages_total", total_pages)
        pages_with_terms = 0
        tool_state = getattr(self, "_tool_state", None)
        if tool_state is not None:
            tool_state.current_source_paper = filename

        # PDF-derived publication metadata (never from the term LLM).
        try:
            pub_meta = provenance.extract_pub_metadata(doc, pdf_path)
        except Exception as e:
            logger.warning("Pub-metadata extraction failed for %s: %s", filename, e)
            pub_meta = {}
        pub_meta = self._publication_metadata(filename, pub_meta)

        logger.debug("Processing '%s' (%d pages) with %d workers", filename, total_pages, self.max_workers)
        page_texts = passages.page_texts_from_doc(doc)
        self._set_paper_pages(filename, page_texts)
        self._emit("paper_chunked", name=filename, pages=total_pages)
        pages_done = 0

        def _process(page_num: int) -> bool:
            text = page_texts[page_num]
            added = self.process_page(text, filename, page_num)
            if self.refusal is not None:
                return bool(added)
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
                if not self._note_refusal(e):
                    logger.warning("%s page %d: code-snippet pass failed: %s", filename, page_num + 1, self._describe(e))
                snips_added = False
            return bool(added or snips_added)

        with ThreadPoolExecutor(max_workers=self.max_workers) as exe:
            futures = {exe.submit(_process, i): i for i in range(total_pages)}
            for fut in as_completed(futures):
                page_i = futures[fut]
                pages_done += 1
                self._emit("page_done", name=filename, done=pages_done, total=total_pages)
                try:
                    if fut.result():
                        pages_with_terms += 1
                        logger.debug("Page %d/%d of %s yielded terms", page_i + 1, total_pages, filename)
                    else:
                        logger.debug("Page %d/%d of %s: no new terms", page_i + 1, total_pages, filename)
                except Exception as e:
                    logger.error("%s page %d: unexpected %s", filename, page_i + 1, type(e).__name__)

        self._emit("paper_merging", name=filename)
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
        self._finish_paper_passages(filename)
        if tool_state is not None:
            tool_state.current_source_paper = None
        return pages_with_terms

    def select_relevant_pages(
        self,
        page_texts: Sequence[str],
        query: str,
        missing_topics: Sequence[str] | None = None,
        *,
        max_pages: int = 6,
    ) -> list[int]:
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
        missing_topics: Sequence[str] | None = None,
        max_pages: int = 6,
    ) -> dict[str, Any]:
        """Process query-relevant pages from one PDF while always extracting publication metadata."""
        try:
            doc = open_pdf(pdf_path)
        except Exception as e:
            logger.error("Cannot open PDF %s: %s", pdf_path, e)
            return {
                "status": "error",
                "message": f"Cannot open PDF {pdf_path}: {e}",
                "filename": Path(pdf_path).name,
            }

        filename = Path(pdf_path).name
        total_pages = doc.page_count

        try:
            pub_meta = provenance.extract_pub_metadata(doc, pdf_path)
        except Exception as e:
            logger.warning("Pub-metadata extraction failed for %s: %s", filename, e)
            pub_meta = {}
        pub_meta = self._publication_metadata(filename, pub_meta)

        page_texts = passages.page_texts_from_doc(doc)
        self._set_paper_pages(filename, page_texts)
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
            if _looks_code_like(text) and self.refusal is None:
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
                    if not self._note_refusal(e):
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
        passage_row = self._finish_paper_passages(filename)
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
            "passages": passage_row,
        }

    def process_directory_targeted(
        self,
        data_dir: str,
        *,
        query: str,
        missing_topics: Sequence[str] | None = None,
        max_pages: int = 6,
    ) -> dict[str, Any]:
        """Walk data_dir and partially process PDFs against one query."""
        if not data_dir or not Path(data_dir).is_dir():
            msg = f"Directory not found: {data_dir}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        pdfs = sorted(p for p in Path(data_dir).rglob("*.pdf") if p.is_file())
        if not pdfs:
            logger.warning("No PDFs in %s", data_dir)

        pdf_results: list[dict[str, Any]] = []
        for idx, pdf_path in enumerate(pdfs, start=1):
            logger.info("[%d/%d] Targeted processing: %s", idx, len(pdfs), pdf_path.name)
            result = self.process_pdf_targeted(
                str(pdf_path),
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
            "passages": self.passage_summary(),
            **({"relation_checks": checks} if (checks := self.relation_check_summary()) else {}),
        }

    def process_directory(self, data_dir: str) -> dict[str, Any]:
        """Walk data_dir, process all PDFs, assign importance scores, save final output."""
        if not data_dir or not Path(data_dir).is_dir():
            msg = f"Directory not found: {data_dir}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        pdfs = sorted(p for p in Path(data_dir).rglob("*.pdf") if p.is_file())
        if not pdfs:
            logger.warning("No PDFs in %s", data_dir)

        for idx, pdf_path in enumerate(pdfs, start=1):
            logger.info("[%d/%d] Processing: %s", idx, len(pdfs), pdf_path.name)
            self.process_pdf(str(pdf_path))

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
            "passages": self.passage_summary(),
            **({"relation_checks": checks} if (checks := self.relation_check_summary()) else {}),
        }


def run_extraction(
    pdf_dir: Path,
    output_json: Path,
    *,
    model: str,
    backend: str = "cborg",
    cborg_base: str | None = None,
    cborg_api_key: str | None = None,
    ollama_url: str = "http://localhost:11434",
    schema_path: str = "storage/schema/matkg_schema.yaml",
    temperature: float = 0.0,
    context_length: int = 50,
    max_workers: int = 8,
    chebi_obo_path: str | None = None,
    merge_top_k: int = DEFAULT_MERGE_TOP_K,
    merge_embed_model: str = "",
) -> dict[str, Any]:
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
        merge_top_k=merge_top_k,
        merge_embed_model=merge_embed_model,
    )
    return o.process_directory(str(pdf_dir))
