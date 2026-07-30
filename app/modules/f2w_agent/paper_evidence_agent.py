"""Grounded question answering over the active extracted PDF."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from academy.agent import Agent, action

logger = logging.getLogger(__name__)

MAX_LLM_PAGES = 8
MAX_PAGE_CHARS = 7000


def _tokens(text: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+./_-]{2,}", str(text or "").casefold())
        if token not in {"what", "were", "with", "from", "that", "this", "paper", "study", "said"}
    }


def _parse_json_object(raw: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_entry(manifest_path: Path, filename: str) -> Dict[str, Any]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Extraction manifest is missing or corrupt: {exc}") from exc
    papers = data.get("papers") if isinstance(data, dict) else None
    entry = papers.get(filename) if isinstance(papers, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"No extraction manifest entry for {filename}")
    return entry


def _eligible_pages(entry: Dict[str, Any], page_count: int) -> Tuple[str, List[int]]:
    state = str(entry.get("extraction_state") or "")
    if state == "full":
        # A full extraction processed the whole document unless an explicit page
        # list was recorded by a newer manifest writer.
        explicit = (entry.get("full") or {}).get("selected_pages") if isinstance(entry.get("full"), dict) else None
        pages = explicit if isinstance(explicit, list) and explicit else list(range(1, page_count + 1))
        return "full", [int(p) for p in pages if str(p).isdigit() and 1 <= int(p) <= page_count]

    selected = entry.get("selected_pages")
    if str(entry.get("extraction_state") or "") == "full" and not selected:
        full = entry.get("full") if isinstance(entry.get("full"), dict) else {}
        selected = full.get("selected_pages")
    if not isinstance(selected, list):
        partials = entry.get("partials")
        latest = partials[-1] if isinstance(partials, list) and partials else {}
        selected = latest.get("selected_pages") if isinstance(latest, dict) else []
    pages = []
    for value in selected or []:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= page <= page_count and page not in pages:
            pages.append(page)
    return "targeted", pages


def _claims_are_cited(answer: str, filename: str, eligible: set[int]) -> bool:
    return not _citation_errors(answer, filename, eligible)


def _citation_errors(answer: str, filename: str, eligible: set[int]) -> List[str]:
    """Explain why an answer fails the paper-evidence citation contract."""
    if not answer.strip():
        return ["answer is empty"]
    citations = re.findall(r"\[PDF:\s*([^]]+?)\s+p\.(\d+)\]", answer)
    if not citations:
        return ["answer contains no page citations"]
    errors: List[str] = []
    for name, page_text in citations:
        if Path(name.strip()).name != filename:
            errors.append(f"citation uses wrong filename: {name.strip()}")
        if int(page_text) not in eligible:
            errors.append(f"citation uses ineligible page: {page_text}")

    # Require every factual sentence or bullet-fragment to carry its own
    # citation. Markdown headings and short labels are presentation, not claims.
    claims: List[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or (line.endswith(":") and "[PDF:" not in line):
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if not line or not re.search(r"[A-Za-z]", line):
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?!\[PDF:)", line)
        claims.extend(sentence.strip() for sentence in sentences if re.search(r"[A-Za-z]", sentence))
    if not claims:
        errors.append("answer contains no factual claims")
    for index, claim in enumerate(claims, start=1):
        if not re.search(r"\[PDF:\s*[^]]+\s+p\.\d+\]", claim):
            errors.append(f"claim {index} has no citation")
    return list(dict.fromkeys(errors))


def summarize_extracted_terms(
    terms_path: str,
    manifest_path: str,
    filename: str,
    *,
    max_terms: int = 16,
    query: str = "",
    missing_topics: Optional[List[str]] = None,
    relevant_node_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a deterministic, page-cited summary from extracted term records.

    When query context is provided, directly matching extracted terms rank
    first. Explicit term-summary requests omit query context and retain the
    complete paper-oriented summary.
    """
    try:
        entry = _manifest_entry(Path(manifest_path), filename)
        raw = json.loads(Path(terms_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "manifest_error", "sufficient": False,
            "answer": f"I could not safely read extracted terms: {exc}",
            "used_pages": [], "missing_topics": ["extracted terms"],
        }

    selected = entry.get("selected_pages")
    if not isinstance(selected, list):
        partials = entry.get("partials")
        latest = partials[-1] if isinstance(partials, list) and partials else {}
        selected = latest.get("selected_pages") if isinstance(latest, dict) else []
    eligible = {int(page) for page in selected or [] if str(page).isdigit() and int(page) > 0}
    if str(entry.get("extraction_state") or "") == "full" and not eligible:
        eligible = {
            int(page)
            for term in (raw.get("terms") or [])
            if isinstance(term, dict) and filename in (term.get("source_papers") or [])
            for page in (term.get("pages") or [])
            if str(page).isdigit() and int(page) > 0
        }

    records: List[Dict[str, Any]] = []
    for term in raw.get("terms") or []:
        if not isinstance(term, dict) or filename not in (term.get("source_papers") or []):
            continue
        snippet_pages = {
            int(snippet.get("page"))
            for snippet in (term.get("context_snippets") or [])
            if isinstance(snippet, dict)
            and Path(str(snippet.get("source_paper") or "")).name == filename
            and str(snippet.get("page")).isdigit()
        }
        pages = sorted(snippet_pages & eligible)
        if not pages:
            pages = sorted(
                int(page) for page in (term.get("pages") or [])
                if str(page).isdigit() and int(page) in eligible
            )
        name = str(term.get("term") or "").strip()
        if name and pages:
            category = str(term.get("category") or term.get("raw_category") or "Term")
            records.append({
                "term": name,
                "definition": re.sub(r"\s+", " ", str(term.get("definition") or "")).strip(),
                "category": category,
                "pages": pages,
            })

    if not records:
        return {
            "status": "insufficient", "sufficient": False,
            "answer": "No page-grounded terms were found for this paper on its eligible extracted pages.",
            "used_pages": [], "missing_topics": ["extracted terms"],
        }

    limit = max(1, int(max_terms))
    focus_text = " ".join(
        [
            str(query or ""),
            *[str(topic) for topic in (missing_topics or [])],
        ]
    ).strip()
    focus_tokens = _tokens(focus_text)
    node_names = [
        str(name).strip()
        for name in (relevant_node_names or [])
        if str(name).strip()
    ]
    normalized_node_names = {re.sub(r"\W+", " ", name.casefold()).strip() for name in node_names}

    for item in records:
        normalized_term = re.sub(r"\W+", " ", item["term"].casefold()).strip()
        term_tokens = _tokens(item["term"])
        definition_tokens = _tokens(item["definition"])
        category_tokens = _tokens(item["category"])
        node_match = any(
            normalized_term == node_name
            or normalized_term in node_name
            or node_name in normalized_term
            for node_name in normalized_node_names
            if node_name
        )
        item["relevance_score"] = (
            (100 if node_match else 0)
            + 8 * len(term_tokens & focus_tokens)
            + 2 * len(definition_tokens & focus_tokens)
            + len(category_tokens & focus_tokens)
        )

    relevant = [item for item in records if int(item["relevance_score"]) > 0]
    query_scoped = bool(focus_text or node_names)
    if query_scoped:
        pool = relevant or records
        pool.sort(
            key=lambda item: (
                -int(item["relevance_score"]),
                min(item["pages"]),
                item["term"].casefold(),
            )
        )
        shown = pool[:limit if relevant else min(limit, 3)]
    else:
        records.sort(key=lambda item: (min(item["pages"]), item["term"].casefold()))
        buckets = {
            page: [item for item in records if min(item["pages"]) == page]
            for page in sorted({min(item["pages"]) for item in records})
        }
        shown = []
        depth = 0
        while len(shown) < limit and any(depth < len(items) for items in buckets.values()):
            for items in buckets.values():
                if depth < len(items) and len(shown) < limit:
                    shown.append(items[depth])
            depth += 1

    if query_scoped and relevant:
        lines = [
            f"Extracted {len(records)} page-grounded terms; "
            f"{len(relevant)} matched the initial query. Relevant extracted terms:"
        ]
        relevance_mode = "matched"
    elif query_scoped:
        lines = [
            f"Extracted {len(records)} page-grounded terms. No extracted term directly "
            "matched the initial query; terms from the query-targeted pages:"
        ]
        relevance_mode = "query_targeted_pages"
    else:
        lines = [f"Extracted {len(records)} page-grounded terms; key terms:"]
        relevance_mode = "all_terms"

    used_pages: set[int] = set()
    for item in shown:
        page = item["pages"][0]
        used_pages.add(page)
        definition = item["definition"] or "Extracted paper concept."
        lines.append(
            f"- **{item['term']}** ({item['category']}): {definition} "
            f"[PDF: {filename} p.{page}]"
        )
    omitted_count = len((relevant or records) if query_scoped else records) - len(shown)
    if omitted_count > 0:
        lines.append(f"- {omitted_count} additional terms omitted from this compact summary.")
    return {
        "status": "success", "sufficient": True, "answer": "\n".join(lines),
        "used_pages": sorted(used_pages), "missing_topics": [],
        "term_count": len(records),
        "relevant_term_count": len(relevant) if query_scoped else len(records),
        "displayed_term_count": len(shown),
        "relevance_mode": relevance_mode,
    }


class PaperEvidenceAgent(Agent):
    """Answer only from pages that the extraction manifest marks eligible."""

    def __init__(self, *, backend: Optional[str] = None, model: Optional[str] = None) -> None:
        super().__init__()
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model

    def _chat(self, prompt: str) -> str:
        from app.modules.term_extractor.clients import make_chat_client

        client = make_chat_client(
            backend=self._backend,
            model=self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat"),
            cborg_base=os.environ.get("CBORG_BASE_URL"),
            cborg_api_key=os.environ.get("CBORG_API_KEY"),
        )
        return str(client.chat(prompt, temperature=0.0, timeout=120) or "")

    def _query(self, question: str, pdf_path: str, manifest_path: str) -> Dict[str, Any]:
        import fitz

        path = Path(pdf_path)
        if not path.is_file():
            return {
                "status": "missing_pdf", "sufficient": False, "answer": "",
                "used_pages": [], "missing_topics": [question],
            }
        try:
            entry = _manifest_entry(Path(manifest_path), path.name)
            with fitz.open(path) as doc:
                mode, eligible = _eligible_pages(entry, len(doc))
                if not eligible:
                    return {
                        "status": "insufficient", "sufficient": False,
                        "answer": "The extraction manifest contains no eligible pages for this paper.",
                        "used_pages": [], "missing_topics": [question],
                    }
                page_text = {
                    page: str(doc[page - 1].get_text("text") or "").strip()
                    for page in eligible
                }
        except Exception as exc:
            logger.warning("Paper evidence read failed: %s", exc)
            return {
                "status": "manifest_error", "sufficient": False,
                "answer": f"I could not safely read the extracted-paper manifest: {exc}",
                "used_pages": [], "missing_topics": [question],
            }

        nonempty = [(page, text) for page, text in page_text.items() if text]
        query_tokens = _tokens(question)
        ranked = sorted(
            nonempty,
            key=lambda item: (len(query_tokens & _tokens(item[1])), len(item[1])),
            reverse=True,
        )
        chosen = ranked[:MAX_LLM_PAGES] if mode == "full" else ranked
        if not chosen:
            return {
                "status": "insufficient", "sufficient": False,
                "answer": "The eligible extracted pages contain no readable text.",
                "used_pages": [], "missing_topics": [question],
            }

        context = "\n\n".join(
            f"--- {path.name} PAGE {page} ---\n{text[:MAX_PAGE_CHARS]}" for page, text in chosen
        )
        prompt = (
            "Answer the question using ONLY the supplied PDF pages. Return ONLY JSON with keys "
            "status, sufficient, answer, used_pages, missing_topics. Every factual sentence or bullet "
            f"in answer must include a citation exactly like [PDF: {path.name} p.N]. Cite only supplied "
            "page numbers. If the pages do not answer the question, set sufficient=false, answer an "
            "explicit insufficiency statement, and identify missing_topics. Do not propose downloading "
            "or searching for another paper.\n\n"
            f"QUESTION: {question}\n\nELIGIBLE_PDF_PAGES:\n{context}"
        )
        try:
            result = _parse_json_object(self._chat(prompt))
        except Exception as exc:
            return {
                "status": "paper_llm_error", "sufficient": False,
                "answer": f"The eligible PDF pages could not be evaluated: {exc}",
                "used_pages": [], "missing_topics": [question],
            }

        sufficient = bool(result.get("sufficient"))
        answer = str(result.get("answer") or "").strip()
        requested_pages = result.get("used_pages") if isinstance(result.get("used_pages"), list) else []
        used_pages = []
        chosen_pages = {page for page, _ in chosen}
        for value in requested_pages:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page in chosen_pages and page not in used_pages:
                used_pages.append(page)
        missing = [str(item) for item in (result.get("missing_topics") or []) if str(item).strip()]
        citation_errors = _citation_errors(answer, path.name, set(eligible)) if sufficient else []
        if sufficient and citation_errors:
            repair_prompt = (
                "Repair ONLY the citation formatting of the candidate answer. Return ONLY JSON with keys "
                "status, sufficient, answer, used_pages, missing_topics. Preserve supported content, but "
                "ensure every factual sentence or bullet has its own exact citation in the form "
                f"[PDF: {path.name} p.N]. Use only eligible pages {sorted(eligible)}. Remove any claim that "
                "cannot be cited from those pages.\n\n"
                f"VALIDATION_ERRORS: {json.dumps(citation_errors)}\n"
                f"QUESTION: {question}\nCANDIDATE_ANSWER: {answer}\n\nELIGIBLE_PDF_PAGES:\n{context}"
            )
            try:
                repaired = _parse_json_object(self._chat(repair_prompt))
            except Exception:
                repaired = {}
            repaired_answer = str(repaired.get("answer") or "").strip()
            repaired_sufficient = bool(repaired.get("sufficient"))
            repair_errors = _citation_errors(repaired_answer, path.name, set(eligible))
            if repaired_sufficient and not repair_errors:
                answer = repaired_answer
                sufficient = True
                requested_pages = repaired.get("used_pages") if isinstance(repaired.get("used_pages"), list) else []
                used_pages = []
                for value in requested_pages:
                    try:
                        page = int(value)
                    except (TypeError, ValueError):
                        continue
                    if page in chosen_pages and page not in used_pages:
                        used_pages.append(page)
                missing = []
            else:
                sufficient = False
                answer = (
                    "An answer was generated, but its page citations could not be validated "
                    "against the eligible extracted pages."
                )
                missing = missing or [question]
                used_pages = []
        if not sufficient:
            return {
                "status": "insufficient", "sufficient": False,
                "answer": answer or "The eligible extracted pages do not answer this question.",
                "used_pages": used_pages, "missing_topics": missing or [question],
            }
        return {
            "status": "success", "sufficient": True, "answer": answer,
            "used_pages": used_pages, "missing_topics": [],
        }

    @action
    async def query(self, question: str, pdf_path: str, manifest_path: str) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._query, question, pdf_path, manifest_path)
