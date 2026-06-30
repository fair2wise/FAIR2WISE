"""DownloadAgent: relevance-driven paper acquisition from OpenAlex.

Given a user query plus the topics the RetrievalAgent found missing, this agent:
  1. searches OpenAlex for a candidate pool,
  2. reconstructs each candidate's abstract and keeps only open-access works with
     a usable PDF link,
  3. ranks candidates by relevance to the query (LLM scoring of title+abstract,
     with a lexical-overlap fallback),
  4. downloads the top-N PDFs into the target directory, skipping ones already
     present.

Abstract-first filtering keeps actual PDF downloads minimal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from academy.agent import Agent, action

logger = logging.getLogger(__name__)


MAX_SEARCH_QUERIES = 5


def _sanitize_openalex_search(text: str) -> str:
    """Remove OpenAlex wildcard syntax from user/LLM-generated search text."""
    # OpenAlex treats ? and * as wildcard operators in stemmed search and rejects
    # many natural-language questions. Keep the meaning, drop the operators.
    cleaned = re.sub(r"[*?]+", " ", text or "")
    return " ".join(cleaned.split())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %.2f", name, raw, default)
        return default


def _reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """Rebuild abstract text from an OpenAlex abstract_inverted_index."""
    if not inverted_index:
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def _pdf_urls(work: Dict[str, Any]) -> List[str]:
    """Open-access URLs in PDF-first preference order."""
    urls: List[str] = []
    for loc_key in ("best_oa_location", "primary_location"):
        loc = work.get(loc_key) or {}
        url = loc.get("pdf_url")
        if url:
            urls.append(url)
    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        urls.append(oa["oa_url"])

    seen = set()
    out = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _safe_name(work: Dict[str, Any]) -> str:
    """Filename stem from DOI (preferred) or OpenAlex id."""
    doi = work.get("doi")
    if doi:
        return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi).replace("/", "_")
    wid = (work.get("id") or "").rstrip("/").split("/")[-1]
    return wid or "openalex_work"


def _lexical_score(query: str, text: str) -> float:
    """Token-overlap fallback relevance score in [0, 1]."""
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    t = set(re.findall(r"[a-z0-9]+", text.lower()))
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)


def _dedupe_key(work: Dict[str, Any]) -> str:
    doi = (work.get("doi") or "").strip().lower()
    if doi:
        return doi
    return (work.get("id") or "").strip().lower() or json.dumps(work, sort_keys=True, default=str)[:200]


def _search_queries(query: str, missing_topics: List[str]) -> List[str]:
    """Build focused OpenAlex queries, broad first then missing-topic variants."""
    out: List[str] = []

    def add(text: str) -> None:
        text = _sanitize_openalex_search(text)
        if text and text.lower() not in {q.lower() for q in out}:
            out.append(text)

    missing = [m for m in (missing_topics or []) if str(m).strip()]
    add(" ".join([query, *missing]))
    for topic in missing[: MAX_SEARCH_QUERIES - 1]:
        add(str(topic))
    add(query)
    return out[:MAX_SEARCH_QUERIES]


def _parse_json_object(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _download_manifest_path(target_dir: Path) -> Path:
    return target_dir.parent / "downloads.jsonl"


def _manifest_record(
    *,
    query: str,
    missing_topics: List[str],
    work: Dict[str, Any],
    dest: Path,
    urls: List[str],
    attempted_urls: List[str],
    status: str,
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "ts": time.time(),
        "query": query,
        "missing_topics": missing_topics,
        "openalex_id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("title"),
        "score": work.get("_score"),
        "dest": str(dest),
        "urls": urls,
        "attempted_urls": attempted_urls,
        "status": status,
        "reason": reason,
    }


def _append_download_record(manifest: Path, record: Dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _extract_pdf_preview(path: Path, max_pages: int = 2, max_chars: int = 4000) -> str:
    """Extract a small text preview for semantic validation."""
    try:
        import fitz

        doc = fitz.open(str(path))
        try:
            pages = min(max_pages, len(doc))
            chunks = []
            for i in range(pages):
                chunks.append(doc[i].get_text("text") or "")
            return "\n".join(chunks).strip()[:max_chars]
        finally:
            doc.close()
    except Exception as e:
        logger.warning("Could not extract PDF preview from %s (%s)", path, e)
        return ""


class DownloadAgent(Agent):
    """Academy agent that finds and downloads query-relevant papers from OpenAlex."""

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        mailto: Optional[str] = None,
        download_delay_seconds: Optional[float] = None,
        validate_downloads: Optional[bool] = None,
    ) -> None:
        super().__init__()
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model
        self._mailto = mailto or os.environ.get("OPENALEX_EMAIL")
        self._download_delay_seconds = (
            download_delay_seconds
            if download_delay_seconds is not None
            else _env_float("F2W_DOWNLOAD_DELAY_SECONDS", 1.0)
        )
        self._validate_downloads = (
            validate_downloads
            if validate_downloads is not None
            else _env_bool("F2W_VALIDATE_DOWNLOADED_PDFS", True)
        )

    # ------------------------------------------------------------------
    def _search_one(self, works, search_text: str, pool: int) -> List[Dict[str, Any]]:
        try:
            results = (
                works()
                .search(search_text)
                .filter(open_access={"is_oa": True})
                .get(per_page=min(pool, 50))
            )
            return list(results or [])
        except Exception as e:
            logger.warning("OpenAlex OA search failed for %r (%s); retrying without OA filter", search_text, e)

        try:
            results = works().search(search_text).get(per_page=min(pool, 50))
            return list(results or [])
        except Exception as e:
            logger.warning("OpenAlex fallback search failed for %r (%s)", search_text, e)
            return []

    def _search_candidates(self, query: str, missing_topics: List[str], pool: int) -> List[Dict[str, Any]]:
        from pyalex import Works, config as pyalex_config

        if self._mailto:
            pyalex_config.email = self._mailto

        merged: List[Dict[str, Any]] = []
        seen = set()
        for search_text in _search_queries(query, missing_topics):
            for work in self._search_one(Works, search_text, pool):
                key = _dedupe_key(work)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(work)
                if len(merged) >= pool:
                    return merged
        return merged

    def _rank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Score candidates by relevance to the query. LLM with lexical fallback."""
        scored = candidates
        try:
            from app.modules.term_extractor.clients import make_chat_client

            cli = make_chat_client(
                backend=self._backend,
                model=self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat"),
                cborg_base=os.environ.get("CBORG_BASE_URL"),
                cborg_api_key=os.environ.get("CBORG_API_KEY"),
            )
            listing = []
            for i, w in enumerate(candidates):
                title = w.get("title") or ""
                abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))[:1200]
                listing.append(f"[{i}] TITLE: {title}\nABSTRACT: {abstract}")
            prompt = (
                "Rate how relevant each paper is to answering the QUESTION, using only the "
                "title and abstract. Return ONLY a JSON object mapping the integer index to a "
                "relevance score in [0,1], e.g. {\"0\": 0.9, \"1\": 0.2}.\n\n"
                f"QUESTION:\n{query}\n\nPAPERS:\n" + "\n\n".join(listing)
            )
            raw = cli.chat(prompt, temperature=0.0, timeout=120)
            m = re.search(r"\{[\s\S]*\}", raw)
            scores = json.loads(m.group(0)) if m else {}
            for i, w in enumerate(candidates):
                w["_score"] = float(scores.get(str(i), 0.0))
        except Exception as e:
            logger.warning("LLM ranking failed (%s); using lexical overlap", e)
            for w in candidates:
                text = (w.get("title") or "") + " " + _reconstruct_abstract(w.get("abstract_inverted_index"))
                w["_score"] = _lexical_score(query, text)
        scored = sorted(candidates, key=lambda w: w.get("_score", 0.0), reverse=True)
        return scored

    def _sleep_between_download_attempts(self) -> None:
        if self._download_delay_seconds > 0:
            time.sleep(self._download_delay_seconds)

    def _validate_downloaded_pdf(self, query: str, work: Dict[str, Any], path: Path) -> bool:
        """Best-effort semantic check; fail open if validator cannot run."""
        if not self._validate_downloads:
            return True

        preview = _extract_pdf_preview(path)
        if not preview:
            logger.warning("Skipping semantic PDF validation for %s: no extractable preview", path.name)
            return True

        try:
            from app.modules.term_extractor.clients import make_chat_client

            cli = make_chat_client(
                backend=self._backend,
                model=self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat"),
                cborg_base=os.environ.get("CBORG_BASE_URL"),
                cborg_api_key=os.environ.get("CBORG_API_KEY"),
            )
            title = work.get("title") or ""
            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))[:1600]
            prompt = (
                "You are validating a downloaded PDF for a retrieval-augmented paper "
                "download agent. Use only the provided OpenAlex title/abstract and "
                "PDF text preview. Return ONLY JSON with this schema: "
                '{"is_paper": true|false, "is_relevant": true|false, "reason": string}.\n\n'
                f"QUESTION:\n{query}\n\n"
                f"OPENALEX_TITLE:\n{title}\n\n"
                f"OPENALEX_ABSTRACT:\n{abstract}\n\n"
                f"PDF_PREVIEW:\n{preview}"
            )
            raw = cli.chat(prompt, temperature=0.0, timeout=120)
            verdict = _parse_json_object(raw)
            is_paper = bool(verdict.get("is_paper"))
            is_relevant = bool(verdict.get("is_relevant"))
            if is_paper and is_relevant:
                return True
            logger.warning(
                "Rejected PDF %s by semantic validation: %s",
                path.name,
                verdict.get("reason") or "not a relevant paper",
            )
            return False
        except Exception as e:
            logger.warning("Semantic PDF validation failed for %s (%s); keeping file", path.name, e)
            return True

    def _download(self, query: str, missing_topics: List[str], target_dir: str,
                  max_papers: int, candidate_pool: int) -> Dict[str, Any]:
        import download_pdfs as dl  # scripts/download_pdfs.py

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        manifest = _download_manifest_path(target)

        candidates = self._search_candidates(query, missing_topics, candidate_pool)
        # keep only works with an open-access URL. download_pdf validates bytes.
        candidates = [w for w in candidates if _pdf_urls(w)]
        if not candidates:
            return {
                "status": "success",
                "downloaded": [],
                "count": 0,
                "skipped": 0,
                "failed": 0,
                "semantic_rejected": 0,
                "url_attempts": 0,
                "oa_url_attempts": 0,
                "candidates": 0,
                "manifest": str(manifest),
            }

        ranked = self._rank(query, candidates)

        downloaded: List[str] = []
        skipped = 0
        failed = 0
        semantic_rejected = 0
        url_attempts = 0
        oa_url_attempts = 0
        for w in ranked:
            if len(downloaded) >= max_papers:
                break
            dest = target / f"{_safe_name(w)}.pdf"
            urls = _pdf_urls(w)
            if dest.exists():
                skipped += 1
                _append_download_record(
                    manifest,
                    _manifest_record(
                        query=query,
                        missing_topics=missing_topics,
                        work=w,
                        dest=dest,
                        urls=urls,
                        attempted_urls=[],
                        status="skipped_existing",
                    ),
                )
                continue

            ok = False
            attempted_urls: List[str] = []
            for url in urls:
                attempted_urls.append(url)
                url_attempts += 1
                if url == (w.get("open_access") or {}).get("oa_url"):
                    oa_url_attempts += 1
                ok = dl.download_pdf(url, str(dest))
                if ok:
                    break
                self._sleep_between_download_attempts()
            if ok and dest.exists() and dest.stat().st_size > 0:
                if self._validate_downloaded_pdf(query, w, dest):
                    downloaded.append(str(dest))
                    _append_download_record(
                        manifest,
                        _manifest_record(
                            query=query,
                            missing_topics=missing_topics,
                            work=w,
                            dest=dest,
                            urls=urls,
                            attempted_urls=attempted_urls,
                            status="downloaded",
                        ),
                    )
                    logger.info("Downloaded %s (score=%.2f)", dest.name, w.get("_score", 0.0))
                    self._sleep_between_download_attempts()
                else:
                    failed += 1
                    semantic_rejected += 1
                    _append_download_record(
                        manifest,
                        _manifest_record(
                            query=query,
                            missing_topics=missing_topics,
                            work=w,
                            dest=dest,
                            urls=urls,
                            attempted_urls=attempted_urls,
                            status="semantic_rejected",
                        ),
                    )
                    dest.unlink(missing_ok=True)
            else:
                failed += 1
                if dest.exists():
                    dest.unlink(missing_ok=True)
                _append_download_record(
                    manifest,
                    _manifest_record(
                        query=query,
                        missing_topics=missing_topics,
                        work=w,
                        dest=dest,
                        urls=urls,
                        attempted_urls=attempted_urls,
                        status="failed",
                        reason="all urls failed or invalid",
                    ),
                )

        return {
            "status": "success",
            "downloaded": downloaded,
            "count": len(downloaded),
            "skipped": skipped,
            "failed": failed,
            "semantic_rejected": semantic_rejected,
            "url_attempts": url_attempts,
            "oa_url_attempts": oa_url_attempts,
            "candidates": len(candidates),
            "target_dir": str(target),
            "manifest": str(manifest),
        }

    @action
    async def find_and_download(
        self,
        query: str,
        missing_topics: Optional[List[str]] = None,
        target_dir: str = "pdfs",
        max_papers: int = 3,
        candidate_pool: int = 25,
    ) -> Dict[str, Any]:
        """Search OpenAlex, rank by relevance, download top-N PDFs."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._download(
                query, missing_topics or [], target_dir, max_papers, candidate_pool
            ),
        )
