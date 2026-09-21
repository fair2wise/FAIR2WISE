"""Common Evidence model for KG hits, paper/ops chunks, and Tiled runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

SOURCE_KINDS = ("kg", "paper_chunk", "tiled_run")
SOURCE_TYPES = ("literature", "ops", "kg", "tiled")
CORPORA = ("literature", "bl1101")

CHUNKER_VERSION = "1.0.0"
LEXICAL_INDEX_VERSION = "bm25-1"

INTENT_WEIGHTS: Dict[str, Dict[str, float]] = {
    "factual": {"kg": 0.70, "paper_chunk": 0.25, "tiled_run": 0.05},
    "why_how": {"kg": 0.25, "paper_chunk": 0.70, "tiled_run": 0.05},
    "layout": {"kg": 0.45, "paper_chunk": 0.55, "tiled_run": 0.00},
    "scan": {"kg": 0.10, "paper_chunk": 0.10, "tiled_run": 0.80},
}


@dataclass
class Evidence:
    """One ranked retrieval hit. Literature and ops stay distinct objects."""

    source_kind: str
    source_id: str
    score: float
    text: str
    source_type: str = "kg"
    paper_id: Optional[str] = None
    work_id: Optional[str] = None
    page: Optional[int] = None
    locator: str = ""
    heading: str = ""
    entity_ids: List[str] = field(default_factory=list)
    matched_slots: Dict[str, Any] = field(default_factory=dict)
    retrieval_method: str = "hybrid"
    provenance: Dict[str, Any] = field(default_factory=dict)
    graph_id: str = ""
    name: str = ""
    category: str = ""
    payload: Any = None

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source_kind {self.source_kind!r}")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source_type {self.source_type!r}")
        corpus = str(self.provenance.get("corpus") or "")
        if self.source_kind == "paper_chunk" and not corpus:
            if self.source_type == "ops":
                self.provenance["corpus"] = "bl1101"
            elif self.source_type == "literature":
                self.provenance["corpus"] = "literature"

    @property
    def corpus(self) -> str:
        return str(self.provenance.get("corpus") or "")

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("payload", None)
        return data


def infer_intent(question: str, *, layout: bool = False, numeric: bool = False) -> str:
    text = (question or "").strip().lower()
    if layout:
        return "layout"
    if numeric:
        return "factual"
    if any(token in text for token in ("last scan", "current scan", "this run", "runcard")):
        return "scan"
    if any(
        token in text
        for token in (
            "why",
            "how did",
            "how do authors",
            "how was",
            "methods",
            "caveat",
            "interpret",
            "concluded",
            "derived",
        )
    ):
        return "why_how"
    return "factual"


def kg_evidence_from_hit(hit: Any) -> Evidence:
    payload = getattr(hit, "payload", hit)
    graph_id = str(getattr(hit, "graph_id", "") or "")
    nid = str(getattr(hit, "id", getattr(payload, "id", "")) or "")
    name = str(getattr(hit, "name", getattr(payload, "name", nid)) or nid)
    category = str(getattr(hit, "category", getattr(payload, "category", "")) or "")
    score = float(getattr(hit, "score", getattr(payload, "score_prp", 0.0)) or 0.0)
    description = str(getattr(payload, "description", "") or "")
    return Evidence(
        source_kind="kg",
        source_id=f"kg:{graph_id}:{nid}",
        score=score,
        text=description,
        source_type="kg",
        entity_ids=[nid] if nid else [],
        retrieval_method="kg_overlap",
        provenance={"graph_id": graph_id, "corpus": graph_id},
        graph_id=graph_id,
        name=name,
        category=category,
        payload=payload,
        locator=f"KG:{graph_id}:{name}" if graph_id else name,
    )


def evidence_from_chunk(
    chunk: Dict[str, Any],
    *,
    score: float,
    retrieval_method: str,
) -> Evidence:
    corpus = str(chunk.get("corpus") or "literature")
    source_type = "ops" if corpus == "bl1101" else "literature"
    page = chunk.get("page")
    try:
        page_i = int(page) if page not in (None, "") else None
    except (TypeError, ValueError):
        page_i = None
    return Evidence(
        source_kind="paper_chunk",
        source_id=str(chunk.get("chunk_id") or chunk.get("doc_id") or "chunk"),
        score=float(score),
        text=str(chunk.get("text") or ""),
        source_type=source_type,
        paper_id=str(chunk.get("doc_id") or "") or None,
        work_id=str(chunk.get("work_id") or "") or None,
        page=page_i,
        locator=str(chunk.get("locator") or ""),
        heading=str(chunk.get("heading") or ""),
        entity_ids=list(chunk.get("entity_ids") or []),
        matched_slots=dict(chunk.get("matched_slots") or {}),
        retrieval_method=retrieval_method,
        provenance={
            "corpus": corpus,
            "doc_id": chunk.get("doc_id"),
            "path": chunk.get("path"),
            "url": chunk.get("url"),
            "revision": chunk.get("corpus_revision"),
            "index_version": chunk.get("chunker_version"),
            "title": chunk.get("title"),
            "doi": chunk.get("doi"),
        },
        name=str(chunk.get("title") or chunk.get("doc_id") or ""),
    )


def tagged_source_types(items: Sequence[Evidence]) -> List[str]:
    return [item.source_type for item in items]
