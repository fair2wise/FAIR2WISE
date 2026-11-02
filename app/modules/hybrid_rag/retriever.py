"""PaperRetriever: page-aware chunks from literature and ops indexes."""
from __future__ import annotations

from collections.abc import Iterable
import logging
from pathlib import Path

from .evidence import Evidence, evidence_from_chunk
from .index_store import SourceIndex

logger = logging.getLogger(__name__)


class PaperRetriever:
    """Same contract for literature PDFs and bl1101 ops docs. Indexes stay separate."""

    def __init__(
        self,
        *,
        literature: SourceIndex | None = None,
        ops: SourceIndex | None = None,
    ) -> None:
        self.literature = literature
        self.ops = ops

    @classmethod
    def from_roots(
        cls,
        literature_root: Path | None = None,
        ops_root: Path | None = None,
    ) -> PaperRetriever:
        """Build a retriever from optional literature and ops index roots; a missing root means no index."""
        lit = SourceIndex(Path(literature_root)) if literature_root else None
        ops = SourceIndex(Path(ops_root)) if ops_root else None
        return cls(literature=lit, ops=ops)

    def retrieve(
        self,
        query: str,
        *,
        k: int = 12,
        include_literature: bool = True,
        include_ops: bool = True,
        kg_work_ids: Iterable[str] | None = None,
        kg_entity_ids: Iterable[str] | None = None,
    ) -> list[Evidence]:
        """Search each index independently. Missing indexes return no hits."""
        hits: list[Evidence] = []
        work_ids = {str(w) for w in (kg_work_ids or []) if str(w).strip()}
        entity_ids = {str(e) for e in (kg_entity_ids or []) if str(e).strip()}
        if include_literature:
            hits.extend(
                self._search_one(self.literature, query, k=k, work_ids=work_ids, entity_ids=entity_ids)
            )
        if include_ops:
            hits.extend(self._search_one(self.ops, query, k=k, work_ids=work_ids, entity_ids=entity_ids))
        return hits

    def _search_one(
        self,
        index: SourceIndex | None,
        query: str,
        *,
        k: int,
        work_ids: set[str],
        entity_ids: set[str],
    ) -> list[Evidence]:
        if index is None or not index.available:
            return []
        try:
            raw = index.search(query, k=k)
        except Exception as exc:
            logger.warning("Source index search failed at %s: %s", index.root, exc)
            return []
        out: list[Evidence] = []
        for chunk, score, method in raw:
            if work_ids and (
                str(chunk.get("work_id") or "") in work_ids
                or str(chunk.get("doc_id") or "") in work_ids
            ):
                score = float(score) + 0.75
                method = "kg_overlap" if method == "bm25" else "hybrid"
            if entity_ids:
                chunk_ents = {str(e) for e in (chunk.get("entity_ids") or [])}
                if chunk_ents & entity_ids:
                    score = float(score) + 0.5
                    method = "hybrid"
            out.append(evidence_from_chunk(chunk, score=score, retrieval_method=method))
        return out
