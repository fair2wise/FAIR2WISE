"""Hybrid source RAG facade: index builders + retrieve/rank/pack."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .chunker import CHUNKER_VERSION, chunk_pdf, chunk_text_document, iter_nonempty
from .context import ContextBuilder
from .evidence import Evidence, infer_intent, kg_evidence_from_hit
from .index_store import SourceIndex, merge_chunks, write_index
from .ranker import EvidenceRanker
from .retriever import PaperRetriever
from .sources import (
    collect_ops_docs,
    repo_root,
    select_literature_docs,
    sha256_file,
)

logger = logging.getLogger(__name__)

DEFAULT_LIT_INDEX = "storage/source_index/literature"
DEFAULT_OPS_INDEX = "storage/source_index/ops_bl1101"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def literature_index_root(root: Optional[Path] = None) -> Path:
    override = os.environ.get("HYBRID_RAG_LITERATURE_INDEX")
    if override:
        return Path(override)
    return (root or repo_root()) / DEFAULT_LIT_INDEX


def ops_index_root(root: Optional[Path] = None) -> Path:
    override = os.environ.get("HYBRID_RAG_OPS_INDEX")
    if override:
        return Path(override)
    return (root or repo_root()) / DEFAULT_OPS_INDEX


def _env_flag(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned if cleaned else None


def hybrid_rag_enabled() -> bool:
    """Server default for source RAG. Off unless F2W_SOURCE_RAG / HYBRID_RAG_ENABLED is truthy."""
    raw = _env_flag("F2W_SOURCE_RAG")
    if raw is None:
        raw = _env_flag("HYBRID_RAG_ENABLED") or "0"
    return raw.lower() in {"1", "true", "yes", "on"}


def build_literature_index(
    *,
    dest: Optional[Path] = None,
    root: Optional[Path] = None,
    mode: str = "gold",
    limit: int = 12,
    incremental: bool = True,
) -> Dict[str, Any]:
    base = Path(root or repo_root())
    out = Path(dest or literature_index_root(base))
    docs = select_literature_docs(root=base, mode=mode, limit=limit)
    existing = SourceIndex(out)
    existing_chunks = existing.load() if incremental and existing.available else []
    known_sha = {
        str(chunk.get("doc_id")): str(chunk.get("sha256") or "")
        for chunk in existing_chunks
        if chunk.get("doc_id")
    }
    new_chunks: List[Dict[str, Any]] = []
    replaced: List[str] = []
    skipped = 0
    for doc in docs:
        path = Path(doc["path"])
        try:
            digest = sha256_file(path)
        except OSError:
            continue
        doc_id = str(doc["doc_id"])
        if incremental and known_sha.get(doc_id) == digest:
            skipped += 1
            continue
        extra = {
            "title": doc.get("title"),
            "doi": doc.get("doi"),
            "sha256": digest,
            "corpus_revision": f"harvest-{mode}",
        }
        produced = iter_nonempty(
            chunk_pdf(
                path,
                work_id=str(doc["work_id"]),
                doc_id=doc_id,
                corpus="literature",
                extra=extra,
            )
        )
        if produced:
            replaced.append(doc_id)
            new_chunks.extend(produced)
    merged = merge_chunks(existing_chunks, new_chunks, replace_doc_ids=replaced)
    if not merged and not existing_chunks:
        meta = write_index(
            out,
            [],
            index_name="literature",
            corpus_revision=f"empty-{_utcnow()}",
            extra_meta={"mode": mode, "status": "no_pdfs"},
        )
        return meta
    if not new_chunks and existing.available:
        meta = existing.meta()
        meta["skipped_unchanged"] = skipped
        return meta
    meta = write_index(
        out,
        merged,
        index_name="literature",
        corpus_revision=f"literature-{mode}-{_utcnow()}",
        extra_meta={
            "mode": mode,
            "chunker_version": CHUNKER_VERSION,
            "indexed_docs": replaced,
            "skipped_unchanged": skipped,
        },
    )
    return meta


def build_ops_index(
    *,
    dest: Optional[Path] = None,
    root: Optional[Path] = None,
    incremental: bool = True,
) -> Dict[str, Any]:
    base = Path(root or repo_root())
    out = Path(dest or ops_index_root(base))
    docs = collect_ops_docs(root=base)
    existing = SourceIndex(out)
    existing_chunks = existing.load() if incremental and existing.available else []
    known_sha = {
        str(chunk.get("doc_id")): str(chunk.get("sha256") or "")
        for chunk in existing_chunks
        if chunk.get("doc_id")
    }
    new_chunks: List[Dict[str, Any]] = []
    replaced: List[str] = []
    skipped = 0
    for doc in docs:
        path = Path(doc["path"])
        try:
            digest = sha256_file(path)
        except OSError:
            continue
        doc_id = str(doc["doc_id"])
        if incremental and known_sha.get(doc_id) == digest:
            skipped += 1
            continue
        extra = {
            "title": doc.get("title"),
            "url": doc.get("url"),
            "sha256": digest,
            "corpus_revision": "bl1101-ops",
            "access": doc.get("access"),
        }
        produced = iter_nonempty(
            chunk_text_document(
                path,
                doc_id=doc_id,
                corpus="bl1101",
                url=str(doc.get("url") or ""),
                extra=extra,
            )
        )
        if produced:
            replaced.append(doc_id)
            new_chunks.extend(produced)
    merged = merge_chunks(existing_chunks, new_chunks, replace_doc_ids=replaced)
    if not merged and not existing_chunks:
        return write_index(
            out,
            [],
            index_name="ops_bl1101",
            corpus_revision=f"empty-{_utcnow()}",
            extra_meta={"status": "no_ops_docs"},
        )
    if not new_chunks and existing.available:
        meta = existing.meta()
        meta["skipped_unchanged"] = skipped
        return meta
    return write_index(
        out,
        merged,
        index_name="ops_bl1101",
        corpus_revision=f"ops-bl1101-{_utcnow()}",
        extra_meta={"indexed_docs": replaced, "skipped_unchanged": skipped},
    )


class HybridSourceRAG:
    """PaperRetriever → EvidenceRanker → ContextBuilder around two indexes."""

    def __init__(
        self,
        *,
        literature_root: Optional[Path] = None,
        ops_root: Optional[Path] = None,
    ) -> None:
        self.literature_root = Path(literature_root) if literature_root else literature_index_root()
        self.ops_root = Path(ops_root) if ops_root else ops_index_root()
        self.retriever = PaperRetriever.from_roots(self.literature_root, self.ops_root)
        self.ranker = EvidenceRanker()
        self.context = ContextBuilder()

    @classmethod
    def from_env(cls) -> "HybridSourceRAG":
        return cls()

    def retrieve_chunks(
        self,
        question: str,
        *,
        include_literature: bool = True,
        include_ops: bool = True,
        kg_work_ids: Optional[Sequence[str]] = None,
        kg_entity_ids: Optional[Sequence[str]] = None,
        k: int = 12,
    ) -> List[Evidence]:
        try:
            return self.retriever.retrieve(
                question,
                k=k,
                include_literature=include_literature,
                include_ops=include_ops,
                kg_work_ids=kg_work_ids,
                kg_entity_ids=kg_entity_ids,
            )
        except Exception as exc:
            logger.warning("Hybrid source RAG retrieve failed: %s", exc)
            return []

    def pack(
        self,
        question: str,
        *,
        kg_hits: Sequence[Any] = (),
        include_literature: bool = True,
        include_ops: bool = True,
        layout: bool = False,
        numeric: bool = False,
        kg_work_ids: Optional[Sequence[str]] = None,
        kg_entity_ids: Optional[Sequence[str]] = None,
        char_budget: int = 6000,
    ) -> Dict[str, Any]:
        intent = infer_intent(question, layout=layout, numeric=numeric)
        kg_evidence = [kg_evidence_from_hit(hit) for hit in kg_hits]
        if layout:
            include_literature = False
            include_ops = True
        chunks = self.retrieve_chunks(
            question,
            include_literature=include_literature,
            include_ops=include_ops,
            kg_work_ids=kg_work_ids,
            kg_entity_ids=kg_entity_ids,
        )
        ranked = self.ranker.rank([*kg_evidence, *chunks], question, intent=intent)
        source_ctx = self.context.build(ranked, char_budget=char_budget, intent=intent)
        return {
            "intent": intent,
            "evidence": ranked,
            "source_context": source_ctx,
            "chunk_count": sum(1 for item in ranked if item.source_kind == "paper_chunk"),
            "literature_available": bool(
                self.retriever.literature and self.retriever.literature.available
            ),
            "ops_available": bool(self.retriever.ops and self.retriever.ops.available),
        }
