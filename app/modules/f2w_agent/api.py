"""FastAPI bridge for the FAIR2WISE 3-agent chat loop."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import kg_update
from .coordinator import Coordinator, CoordinatorConfig
from app.modules import kg_rag_api as krag

from .download_agent import DownloadAgent, _reconstruct_abstract, _sanitize_openalex_search
from .extractor_agent import ExtractorAgent
from .retrieval_agent import RetrievalAgent


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    graph_source: Optional[str] = Field(default=None, pattern="^(splash|json)$")
    json_graph_path: Optional[str] = None


class LinkedCodeSnippet(BaseModel):
    id: str
    label: str = ""
    function_name: Optional[str] = None
    code_language: Optional[str] = None
    code_snippet: str
    publications: List[Dict[str, Any]] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    description: str = ""
    publications: List[Dict[str, Any]] = Field(default_factory=list)
    code_snippet: Optional[str] = None
    code_language: Optional[str] = None
    function_name: Optional[str] = None
    linked_code_snippets: List[LinkedCodeSnippet] = Field(default_factory=list)


class GraphEdge(BaseModel):
    source: str
    target: str
    predicate: str = "rel:related_to"


class GraphPayload(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    source_path: str


class ChatResponse(BaseModel):
    status: str
    answer: str
    sufficient: bool
    node_ids: List[str]
    publications: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float
    rounds: List[Dict[str, Any]]
    graph: GraphPayload
    graph_source_requested: Optional[str] = None
    graph_source_used: Optional[str] = None
    workdir: str


class GraphUploadRequest(BaseModel):
    filename: str = "uploaded_graph.json"
    graph: Dict[str, Any]


class GraphUploadResponse(BaseModel):
    graph: GraphPayload
    graph_path: str
    filename: str


class PublicationSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=20, ge=1, le=50)
    include_external: bool = False


class PublicationSearchResponse(BaseModel):
    status: str
    query: str
    publications: List[Dict[str, Any]] = Field(default_factory=list)
    matched_node_ids: List[str] = Field(default_factory=list)
    source: str


class AgentSettingsResponse(BaseModel):
    backend: str
    graph_source: str
    json_graph_path: Optional[str] = None
    available_json_graphs: List[str] = Field(default_factory=list)


class AgentSettingsUpdate(BaseModel):
    backend: Optional[str] = Field(default=None, pattern="^(cborg|ollama)$")
    graph_source: Optional[str] = Field(default=None, pattern="^(splash|json)$")
    json_graph_path: Optional[str] = None


ProgressEmitter = Callable[[str, str, Dict[str, Any]], Awaitable[None]]

DEFAULT_JSON_GRAPH = "storage/kg/matkg_xray_papers_cborg_chat.json"


def project_root() -> Path:
    return Path.cwd()


def storage_kg_dir(root: Optional[Path] = None) -> Path:
    return (root or project_root()) / "storage" / "kg"


def list_storage_kg_json_files(root: Optional[Path] = None) -> List[str]:
    kg_dir = storage_kg_dir(root)
    if not kg_dir.is_dir():
        return []
    paths = sorted(kg_dir.glob("*.json"), key=lambda path: path.name.lower())
    base = root or project_root()
    return [
        str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
        for path in paths
    ]


def default_json_graph_path(
    *,
    configured_graph: Optional[str],
    available: Optional[List[str]] = None,
) -> Optional[str]:
    options = available if available is not None else list_storage_kg_json_files()
    if configured_graph:
        normalized = configured_graph.replace("\\", "/")
        if normalized in options:
            return normalized
        configured_name = Path(configured_graph).name
        for option in options:
            if Path(option).name == configured_name:
                return option
    if DEFAULT_JSON_GRAPH.replace("\\", "/") in options:
        return DEFAULT_JSON_GRAPH
    return options[0] if options else configured_graph


@dataclass
class RuntimeSettings:
    backend: str = "cborg"
    graph_source: str = "splash"
    json_graph_path: Optional[str] = None


def _string_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


_PUBLICATION_FIELDS = (
    "source_paper",
    "publication_year",
    "paper_title",
    "authors",
    "institutions",
    "doi",
    "journal",
    "volume",
    "issue",
    "pages_range",
    "abstract_text",
    "keywords",
)

_DOI_PDF_FILENAME_RE = re.compile(r"^(10\.\d{4,})[_/](.+)\.pdf$", re.I)
# Wiley-style PDF names omit the DOI slash: 10.1002/aenm.201702831 → 10.1002aenm.201702831.pdf
_DOI_PDF_FILENAME_STRIPPED_RE = re.compile(r"^(10\.\d{4,})([a-z][a-z0-9]*\.\d+)\.pdf$", re.I)
_ARXIV_PDF_FILENAME_RE = re.compile(r"^(?:arxiv[_-]?)?(\d{4}\.\d{4,5}(?:v\d+)?)\.pdf$", re.I)


def _doi_from_pdf_filename(source: str) -> Optional[str]:
    doi_match = _DOI_PDF_FILENAME_RE.match(source)
    if doi_match:
        return f"{doi_match.group(1)}/{doi_match.group(2)}"
    stripped_match = _DOI_PDF_FILENAME_STRIPPED_RE.match(source)
    if stripped_match:
        return f"{stripped_match.group(1)}/{stripped_match.group(2)}"
    return None


def _publication_from_source_identifier(source: str) -> Dict[str, Any]:
    """Derive stable identifiers from source filenames without inventing metadata."""
    clean_source = str(source or "").strip()
    publication: Dict[str, Any] = {"source_paper": clean_source} if clean_source else {}
    doi = _doi_from_pdf_filename(clean_source)
    if doi:
        publication["doi"] = doi
        return publication
    arxiv_match = _ARXIV_PDF_FILENAME_RE.match(clean_source)
    if arxiv_match:
        publication["doi"] = f"arXiv:{arxiv_match.group(1)}"
    return publication


def _publication_key(publication: Dict[str, Any]) -> str:
    doi = str(publication.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    source = str(publication.get("source_paper") or "").strip().lower()
    title = str(publication.get("paper_title") or "").strip().lower()
    return f"source-title:{source}:{title}"


def _node_publications(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    publications: List[Dict[str, Any]] = []
    for pub in raw.get("publications") or []:
        if not isinstance(pub, dict):
            continue
        clean = {field: pub.get(field) for field in _PUBLICATION_FIELDS if pub.get(field) not in (None, "", [])}
        source = clean.get("source_paper")
        if source and not clean.get("doi"):
            clean = {**_publication_from_source_identifier(str(source)), **clean}
        if clean:
            publications.append(clean)
    if publications:
        return publications

    source_metadata = raw.get("source_metadata") or {}
    if isinstance(source_metadata, dict):
        for source, meta in source_metadata.items():
            if not isinstance(meta, dict):
                continue
            clean = {field: meta.get(field) for field in _PUBLICATION_FIELDS if meta.get(field) not in (None, "", [])}
            source_text = str(source).strip()
            if source_text:
                clean = {**_publication_from_source_identifier(source_text), **clean}
            if clean:
                publications.append(clean)
    if publications:
        return publications

    source_papers = raw.get("source_papers") or []
    if not isinstance(source_papers, list):
        source_papers = [source_papers]
    source_papers = [str(source).strip() for source in source_papers if str(source).strip()]
    if len(set(source_papers)) <= 1:
        clean = {field: raw.get(field) for field in _PUBLICATION_FIELDS if raw.get(field) not in (None, "", [])}
        if source_papers and "source_paper" not in clean:
            clean = {**_publication_from_source_identifier(source_papers[0]), **clean}
        if clean:
            publications.append(clean)
    elif source_papers:
        for source in sorted(set(source_papers)):
            publications.append(_publication_from_source_identifier(source))
    return publications


def _merge_publication_pages(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    incoming_pages = incoming.get("pages")
    if not isinstance(incoming_pages, list):
        return
    current = existing.get("pages")
    if not isinstance(current, list):
        current = []
    merged_pages = list(current)
    for page in incoming_pages:
        if page not in merged_pages:
            merged_pages.append(page)
    if merged_pages:
        existing["pages"] = merged_pages


def _publications_for_graph_node(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect publication records tied to a KG node, including snippet sources."""
    merged: Dict[str, Dict[str, Any]] = {}

    def add_publication(publication: Dict[str, Any]) -> None:
        key = _publication_key(publication)
        if not key or key == "source-title::":
            return
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(publication)
            return
        for field in _PUBLICATION_FIELDS:
            if not existing.get(field) and publication.get(field):
                existing[field] = publication[field]
        _merge_publication_pages(existing, publication)

    for publication in _node_publications(raw):
        add_publication(publication)

    for snippet in raw.get("context_snippets") or []:
        if not isinstance(snippet, dict):
            continue
        source = str(snippet.get("source_paper") or "").strip()
        if not source:
            continue
        entry: Dict[str, Any] = _publication_from_source_identifier(source)
        page = snippet.get("page")
        if page is not None:
            entry["pages"] = [page]
        add_publication(entry)

    source_papers = raw.get("source_papers") or []
    if not isinstance(source_papers, list):
        source_papers = [source_papers]
    for source in source_papers:
        source_text = str(source).strip()
        if source_text:
            add_publication(_publication_from_source_identifier(source_text))

    return sorted(
        merged.values(),
        key=lambda pub: (
            str(pub.get("paper_title") or pub.get("source_paper") or "").lower(),
            str(pub.get("doi") or "").lower(),
        ),
    )


def _is_code_snippet_raw(raw: Dict[str, Any]) -> bool:
    category = _string_value(raw.get("category") or raw.get("type")).lower()
    return category == "codesnippet" or bool(str(raw.get("code_snippet") or "").strip())


def _linked_code_snippets_from_data(data: Dict[str, Any], node_id: str) -> List[LinkedCodeSnippet]:
    linked_ids: set[str] = set()
    associations = data.get("associations") or []
    if isinstance(associations, list):
        for raw in associations:
            if not isinstance(raw, dict):
                continue
            source = _string_value(raw.get("subject") or raw.get("source"))
            target = _string_value(raw.get("object") or raw.get("target"))
            if source == node_id and target:
                linked_ids.add(target)
            elif target == node_id and source:
                linked_ids.add(source)

    raw_nodes = data.get("things") or []
    by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_nodes, list):
        for raw in raw_nodes:
            if isinstance(raw, dict):
                raw_id = _string_value(raw.get("id"))
                if raw_id:
                    by_id[raw_id] = raw

    snippets: List[LinkedCodeSnippet] = []
    for linked_id in sorted(linked_ids):
        raw = by_id.get(linked_id)
        if raw is None or not _is_code_snippet_raw(raw):
            continue
        code = str(raw.get("code_snippet") or "").strip()
        if not code:
            continue
        snippets.append(
            LinkedCodeSnippet(
                id=linked_id,
                label=_string_value(raw.get("name") or raw.get("label"), linked_id),
                function_name=_string_value(raw.get("function_name")) or None,
                code_language=_string_value(raw.get("code_language")) or None,
                code_snippet=code,
                publications=_publications_for_graph_node(raw),
            )
        )
    return snippets


def _graph_node_from_raw(
    raw: Dict[str, Any],
    *,
    include_code: bool = False,
    include_linked_code: bool = False,
    graph_data: Optional[Dict[str, Any]] = None,
) -> GraphNode:
    node_id = _string_value(raw.get("id"))
    label = _string_value(raw.get("name") or raw.get("label"), node_id)
    node_type = _string_value(raw.get("category") or raw.get("type"), "Thing")
    description = _string_value(
        raw.get("description")
        or raw.get("definition")
        or raw.get("code_description")
    )
    code_snippet: Optional[str] = None
    code_language: Optional[str] = None
    function_name: Optional[str] = None
    if include_code:
        code_text = str(raw.get("code_snippet") or "").strip()
        if code_text:
            code_snippet = code_text
            code_language = _string_value(raw.get("code_language")) or None
            function_name = _string_value(raw.get("function_name")) or None

    linked_code_snippets: List[LinkedCodeSnippet] = []
    if include_linked_code and graph_data is not None:
        linked_code_snippets = _linked_code_snippets_from_data(graph_data, node_id)

    return GraphNode(
        id=node_id,
        label=label,
        type=node_type,
        description=description,
        publications=_publications_for_graph_node(raw),
        code_snippet=code_snippet,
        code_language=code_language,
        function_name=function_name,
        linked_code_snippets=linked_code_snippets,
    )


def graph_node_from_file(
    graph_path: Path,
    node_id: str,
    *,
    include_linked_code: bool = True,
) -> Optional[GraphNode]:
    if not graph_path.exists() or not node_id:
        return None
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    raw_nodes = data.get("things") or []
    if not isinstance(raw_nodes, list):
        return None
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        if _string_value(raw.get("id")) == node_id:
            return _graph_node_from_raw(
                raw,
                include_code=True,
                include_linked_code=include_linked_code,
                graph_data=data,
            )
    return None


def graph_payload_from_file(graph_path: Path) -> GraphPayload:
    """Load a MatKG JSON file into the compact UI graph contract."""
    with graph_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    raw_nodes = data.get("things") or []
    raw_edges = data.get("associations") or []
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []

    if isinstance(raw_nodes, list):
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            node_id = _string_value(raw.get("id"))
            if not node_id:
                continue
            nodes.append(_graph_node_from_raw(raw))

    if isinstance(raw_edges, list):
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            source = _string_value(raw.get("subject") or raw.get("source"))
            target = _string_value(raw.get("object") or raw.get("target"))
            if not source or not target:
                continue
            edges.append(
                GraphEdge(
                    source=source,
                    target=target,
                    predicate=_string_value(raw.get("predicate"), "rel:related_to"),
                )
            )

    return GraphPayload(nodes=nodes, edges=edges, source_path=str(graph_path))


def graph_subset_from_file(graph_path: Path, node_ids: List[str]) -> Dict[str, Any]:
    """Return the induced subgraph (nodes + internal edges) for the given ids.

    Used to stream retrieved nodes to the UI as soon as retrieval finishes,
    before the slower download/extraction steps run.
    """
    wanted = [str(n) for n in node_ids if str(n)]
    empty: Dict[str, Any] = {"nodes": [], "edges": []}
    if not wanted or not graph_path.exists():
        return empty

    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty

    wanted_set = set(wanted)
    raw_nodes = data.get("things") or []
    by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_nodes, list):
        for raw in raw_nodes:
            if isinstance(raw, dict):
                node_id = _string_value(raw.get("id"))
                if node_id in wanted_set:
                    by_id[node_id] = raw

    # Preserve the retrieval order of the selected ids.
    nodes: List[Dict[str, Any]] = []
    for node_id in wanted:
        raw = by_id.get(node_id)
        if raw is None:
            continue
        node = _graph_node_from_raw(
            raw,
            include_code=True,
            include_linked_code=True,
            graph_data=data,
        )
        nodes.append(_model_to_jsonable(node))

    present = {node["id"] for node in nodes}
    edges: List[Dict[str, Any]] = []
    raw_edges = data.get("associations") or []
    if isinstance(raw_edges, list):
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            source = _string_value(raw.get("subject") or raw.get("source"))
            target = _string_value(raw.get("object") or raw.get("target"))
            if source in present and target in present:
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "predicate": _string_value(raw.get("predicate"), "rel:related_to"),
                    }
                )

    return {"nodes": nodes, "edges": edges}


def _model_to_jsonable(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.loads(model.json())


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _confidence(verdict: Dict[str, Any]) -> float:
    if verdict.get("sufficient"):
        selected = len(verdict.get("selected") or [])
        direct = int(verdict.get("direct_evidence_count") or 0)
        return min(0.98, 0.72 + selected * 0.015 + direct * 0.04)
    if verdict.get("no_evidence"):
        return 0.05
    return 0.35


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _publication_relevance(query: str, publication: Dict[str, Any]) -> float:
    query_tokens = _token_set(query)
    if not query_tokens:
        return 0.0
    text_parts: List[str] = []
    for field in ("paper_title", "abstract_text", "journal", "source_paper"):
        value = publication.get(field)
        if value:
            text_parts.append(str(value))
    for field in ("keywords", "authors"):
        values = publication.get(field)
        if isinstance(values, list):
            text_parts.extend(str(value) for value in values)
    for node in publication.get("supporting_nodes") or []:
        if isinstance(node, dict):
            text_parts.extend(str(node.get(field) or "") for field in ("name", "category"))
    text_tokens = _token_set(" ".join(text_parts))
    if not text_tokens:
        return 0.0
    title_tokens = _token_set(str(publication.get("paper_title") or ""))
    keyword_tokens = _token_set(" ".join(str(value) for value in publication.get("keywords") or []))
    supporting_tokens = _token_set(
        " ".join(
            str(node.get("name") or "")
            for node in publication.get("supporting_nodes") or []
            if isinstance(node, dict)
        )
    )
    score = len(query_tokens & text_tokens) / len(query_tokens)
    score += 0.35 * len(query_tokens & title_tokens) / len(query_tokens)
    score += 0.2 * len(query_tokens & keyword_tokens) / len(query_tokens)
    score += 0.25 * len(query_tokens & supporting_tokens) / len(query_tokens)
    if publication.get("doi"):
        score += 0.03
    return score


def _rank_publications(query: str, publications: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    indexed = list(enumerate(publications))
    ranked = sorted(
        indexed,
        key=lambda item: (
            _publication_relevance(query, item[1]),
            bool(item[1].get("supporting_nodes")),
            str(item[1].get("publication_year") or ""),
        ),
        reverse=True,
    )
    return [publication for _, publication in ranked[:max_results]]


def _normalize_doi(value: Any) -> str:
    doi = str(value or "").strip()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I).strip()


def _merge_publications_prefer_existing(
    base: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def add(publication: Dict[str, Any]) -> None:
        pub = dict(publication)
        if pub.get("doi"):
            pub["doi"] = _normalize_doi(pub.get("doi"))
        key = _publication_key(pub)
        if not key or key == "source-title::":
            return
        existing = merged.get(key)
        if existing is None:
            merged[key] = pub
            order.append(key)
            return
        for field in _PUBLICATION_FIELDS:
            if not existing.get(field) and pub.get(field):
                existing[field] = pub[field]
        if existing.get("supporting_nodes") or pub.get("supporting_nodes"):
            current = existing.setdefault("supporting_nodes", [])
            seen = {node.get("id") for node in current if isinstance(node, dict)}
            for node in pub.get("supporting_nodes") or []:
                if isinstance(node, dict) and node.get("id") not in seen:
                    current.append(node)
                    seen.add(node.get("id"))

    for publication in base:
        add(publication)
    for publication in incoming:
        add(publication)
    return [merged[key] for key in order]


def _openalex_work_to_publication(work: Dict[str, Any]) -> Dict[str, Any]:
    authors: List[str] = []
    institutions: List[str] = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(str(name))
        for institution in authorship.get("institutions") or []:
            if not isinstance(institution, dict):
                continue
            inst_name = institution.get("display_name")
            if inst_name and str(inst_name) not in institutions:
                institutions.append(str(inst_name))
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    biblio = work.get("biblio") or {}
    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    pages_range = ""
    if first_page and last_page:
        pages_range = f"{first_page}-{last_page}"
    elif first_page:
        pages_range = str(first_page)
    elif last_page:
        pages_range = str(last_page)
    keywords = [
        str(keyword.get("display_name") or keyword.get("keyword") or "").strip()
        for keyword in work.get("keywords") or work.get("concepts") or []
        if isinstance(keyword, dict) and str(keyword.get("display_name") or keyword.get("keyword") or "").strip()
    ]
    publication: Dict[str, Any] = {
        "source_paper": str(work.get("id") or "").rstrip("/").split("/")[-1] or "OpenAlex",
        "paper_title": work.get("title") or work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "authors": authors,
        "institutions": institutions,
        "doi": _normalize_doi(work.get("doi")),
        "journal": source.get("display_name"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages_range": pages_range,
        "abstract_text": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "keywords": keywords[:10],
    }
    return {key: value for key, value in publication.items() if value not in (None, "", [])}


def _search_openalex_publications(query: str, max_results: int) -> List[Dict[str, Any]]:
    try:
        from pyalex import Works, config as pyalex_config
    except Exception:
        return []

    mailto = os.environ.get("OPENALEX_EMAIL")
    if mailto:
        pyalex_config.email = mailto
    try:
        works = (
            Works()
            .search(_sanitize_openalex_search(query))
            .get(per_page=min(max_results * 2, 50))
        )
    except Exception:
        return []
    publications = [_openalex_work_to_publication(work) for work in works or [] if isinstance(work, dict)]
    return [publication for publication in publications if publication.get("paper_title") or publication.get("doi")]


def publications_for_selected_nodes(graph_path: Path, node_ids: List[str]) -> List[Dict[str, Any]]:
    """Collect deduped publication records for selected KG nodes."""
    if not graph_path.exists() or not node_ids:
        return []
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_nodes = data.get("things") or []
    if not isinstance(raw_nodes, list):
        return []
    by_id = {raw.get("id"): raw for raw in raw_nodes if isinstance(raw, dict)}
    merged: Dict[str, Dict[str, Any]] = {}

    for node_id in node_ids:
        raw = by_id.get(node_id)
        if not raw:
            continue
        node_ref = {
            "id": node_id,
            "name": _string_value(raw.get("name") or raw.get("label"), node_id),
            "category": _string_value(raw.get("category") or raw.get("type"), "Thing"),
        }
        for publication in _node_publications(raw):
            key = _publication_key(publication)
            if not key or key == "source-title::":
                continue
            existing = merged.setdefault(key, {**publication, "supporting_nodes": []})
            existing_nodes = {node["id"] for node in existing["supporting_nodes"]}
            if node_ref["id"] not in existing_nodes:
                existing["supporting_nodes"].append(node_ref)

    return sorted(
        merged.values(),
        key=lambda pub: (
            str(pub.get("paper_title") or pub.get("source_paper") or "").lower(),
            str(pub.get("doi") or "").lower(),
        ),
    )


class AgentPipelineService:
    """Reusable in-process 3-agent pipeline for HTTP chat requests."""

    def __init__(self, cfg: CoordinatorConfig) -> None:
        self.cfg = cfg
        self.coord = Coordinator(cfg)
        self.lock = asyncio.Lock()
        available_json_graphs = list_storage_kg_json_files()
        initial_graph_source = "splash" if cfg.kg_mode in {"splash", "splash_links"} else "json"
        initial_backend = cfg.backend if cfg.backend in {"cborg", "ollama"} else "cborg"
        self.runtime = RuntimeSettings(
            backend=initial_backend,
            graph_source=initial_graph_source,
            json_graph_path=default_json_graph_path(
                configured_graph=cfg.graph,
                available=available_json_graphs,
            ),
        )
        self._rebuild_agents()

    def _rebuild_agents(self) -> None:
        graph_file = str(self.graph_path())
        self.retrieval = RetrievalAgent(
            graph_file=graph_file,
            graph_source=self.runtime.graph_source,
            backend=self.runtime.backend,
            model=self.cfg.model,
        )
        self.download = DownloadAgent(
            backend=self.runtime.backend,
            model=self.cfg.model,
            download_delay_seconds=self.cfg.download_delay_seconds,
            validate_downloads=self.cfg.validate_downloads,
        )
        self.extractor = ExtractorAgent(
            backend=self.runtime.backend,
            model=self.cfg.model,
            schema_path=self.cfg.schema_path,
            chebi_obo_path=self.cfg.chebi_obo_path,
            max_workers=self.cfg.workers,
        )

    def _session_graph_path(self) -> Path:
        if self.coord.session_kg.exists():
            return self.coord.session_kg
        return Path(self.coord.initial_graph)

    def graph_path(self) -> Path:
        if self.runtime.graph_source == "json" and self.runtime.json_graph_path:
            return self._resolve_json_graph_path(self.runtime.json_graph_path)
        return self._session_graph_path()

    def settings_response(self) -> AgentSettingsResponse:
        available = list_storage_kg_json_files()
        json_graph_path = self.runtime.json_graph_path
        if self.runtime.graph_source == "json" and not json_graph_path:
            json_graph_path = default_json_graph_path(
                configured_graph=self.cfg.graph,
                available=available,
            )
        return AgentSettingsResponse(
            backend=self.runtime.backend,
            graph_source=self.runtime.graph_source,
            json_graph_path=json_graph_path,
            available_json_graphs=available,
        )

    async def apply_settings(self, update: AgentSettingsUpdate) -> AgentSettingsResponse:
        async with self.lock:
            backend_changed = False
            graph_changed = False

            if update.backend is not None and update.backend != self.runtime.backend:
                self.runtime.backend = update.backend
                backend_changed = True

            if update.graph_source is not None and update.graph_source != self.runtime.graph_source:
                self.runtime.graph_source = update.graph_source
                graph_changed = True

            if update.json_graph_path is not None:
                normalized = update.json_graph_path.replace("\\", "/")
                if normalized != (self.runtime.json_graph_path or "").replace("\\", "/"):
                    self.runtime.json_graph_path = normalized
                    graph_changed = True

            available = list_storage_kg_json_files()
            if self.runtime.graph_source == "json":
                if not self.runtime.json_graph_path:
                    self.runtime.json_graph_path = default_json_graph_path(
                        configured_graph=self.cfg.graph,
                        available=available,
                    )
                if not self.runtime.json_graph_path:
                    raise ValueError("No JSON knowledge graph files found in storage/kg")
                active_graph_path = self._resolve_json_graph_path(self.runtime.json_graph_path)
                active_graph_source = "json"
            else:
                active_graph_path = self._session_graph_path()
                active_graph_source = "splash"

            if backend_changed:
                self._rebuild_agents()

            if backend_changed or graph_changed:
                await self.retrieval.reload_kg(
                    str(active_graph_path),
                    graph_source=active_graph_source,
                )

            return self.settings_response()

    def graph_payload(self) -> GraphPayload:
        return graph_payload_from_file(self.graph_path())

    async def search_publications(
        self,
        query: str,
        *,
        max_results: int = 20,
        include_external: bool = False,
    ) -> PublicationSearchResponse:
        query = query.strip()
        if not query:
            raise ValueError("Search query is required")
        graph_path = self.graph_path()
        loop = asyncio.get_event_loop()

        def retrieve_node_ids() -> List[str]:
            graph_source = "json" if self.runtime.graph_source == "json" else self.cfg.kg_mode
            kg = krag.KnowledgeGraph(str(graph_path), graph_source=graph_source)
            infos = krag.retrieve_nodes(query, kg)
            return [str(getattr(info, "id", info)) for info in infos]

        matched_node_ids = await loop.run_in_executor(None, retrieve_node_ids)
        publications = publications_for_selected_nodes(graph_path, matched_node_ids)
        publications = _rank_publications(query, publications, max_results)
        if include_external:
            external = await loop.run_in_executor(
                None,
                lambda: _search_openalex_publications(query, max_results),
            )
            publications = _merge_publications_prefer_existing(publications, external)
            publications = _rank_publications(query, publications, max_results)

        return PublicationSearchResponse(
            status="ok",
            query=query,
            publications=publications,
            matched_node_ids=matched_node_ids,
            source="kg+openalex" if include_external else "kg",
        )

    def _upload_dir(self) -> Path:
        path = Path(self.cfg.workdir) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _uploaded_graph_path(self, filename: str) -> Path:
        clean = Path(filename or "uploaded_graph.json").name
        if not clean.lower().endswith(".json"):
            clean = f"{clean}.json"
        return self._upload_dir() / clean

    def save_uploaded_graph(self, filename: str, graph: Dict[str, Any]) -> GraphUploadResponse:
        if not isinstance(graph.get("things"), list) or not isinstance(graph.get("associations"), list):
            raise ValueError("Uploaded graph must be a MatKG JSON with list fields: things and associations")
        graph_path = self._uploaded_graph_path(filename)
        graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        payload = graph_payload_from_file(graph_path)
        return GraphUploadResponse(graph=payload, graph_path=str(graph_path), filename=graph_path.name)

    def _resolve_json_graph_path(self, path: str) -> Path:
        graph_path = Path(path)
        if not graph_path.is_absolute():
            graph_path = (project_root() / graph_path).resolve()
        else:
            graph_path = graph_path.resolve()

        allowed_roots = [
            self._upload_dir().resolve(),
            storage_kg_dir().resolve(),
        ]
        if not any(graph_path.is_relative_to(root) for root in allowed_roots):
            raise ValueError("JSON graph path must be under storage/kg or uploaded graphs")
        if not graph_path.exists():
            raise FileNotFoundError(f"JSON graph not found: {path}")
        return graph_path

    async def ask(
        self,
        question: str,
        *,
        graph_source: Optional[str] = None,
        json_graph_path: Optional[str] = None,
    ) -> ChatResponse:
        async with self.lock:
            return await self._ask_locked(
                question.strip(),
                emit=None,
                graph_source=graph_source,
                json_graph_path=json_graph_path,
            )

    async def ask_with_progress(
        self,
        question: str,
        emit: ProgressEmitter,
        *,
        graph_source: Optional[str] = None,
        json_graph_path: Optional[str] = None,
    ) -> ChatResponse:
        async with self.lock:
            return await self._ask_locked(
                question.strip(),
                emit=emit,
                graph_source=graph_source,
                json_graph_path=json_graph_path,
            )

    async def _emit(
        self,
        emit: Optional[ProgressEmitter],
        event: str,
        message: str,
        **data: Any,
    ) -> None:
        if emit is None:
            return
        await emit(event, message, data)

    async def _ask_locked(
        self,
        question: str,
        emit: Optional[ProgressEmitter],
        *,
        graph_source: Optional[str],
        json_graph_path: Optional[str],
    ) -> ChatResponse:
        rounds: List[Dict[str, Any]] = []
        last_verdict: Dict[str, Any] = {}
        effective_graph_source = (graph_source or self.runtime.graph_source).lower()

        if effective_graph_source == "json":
            selected_path = json_graph_path or self.runtime.json_graph_path
            if not selected_path:
                raise ValueError("JSON graph mode requires a graph file")
            active_graph_path = self._resolve_json_graph_path(selected_path)
            active_graph_source = "json"
        else:
            active_graph_path = self._session_graph_path()
            active_graph_source = "splash"

        await self.retrieval.reload_kg(str(active_graph_path), graph_source=active_graph_source)

        for round_no in range(1, self.cfg.max_rounds + 1):
            await self._emit(
                emit,
                "retrieval_started",
                "Retrieval agent searching the KG",
                round=round_no,
            )
            verdict = await self.retrieval.query(question)
            last_verdict = verdict
            round_info: Dict[str, Any] = {
                "round": round_no,
                "retrieval": verdict,
            }
            rounds.append(round_info)

            selected_count = len(verdict.get("selected") or [])
            await self._emit(
                emit,
                "retrieval_result",
                (
                    f"Retrieved {selected_count} KG node(s); "
                    + ("evidence sufficient" if verdict.get("sufficient") else "evidence insufficient")
                ),
                round=round_no,
                selected_count=selected_count,
                direct_evidence_count=int(verdict.get("direct_evidence_count") or 0),
                sufficient=bool(verdict.get("sufficient")),
                missing_topics=verdict.get("missing_topics") or [],
            )

            selected_ids = [str(n) for n in (verdict.get("selected") or [])]
            if selected_ids:
                subset = graph_subset_from_file(active_graph_path, selected_ids)
                await self._emit(
                    emit,
                    "graph_update",
                    f"Mapping {len(subset['nodes'])} node(s) onto the graph",
                    round=round_no,
                    node_ids=selected_ids,
                    graph=subset,
                )

            if str(verdict.get("status", "")).endswith("_error"):
                return self._response(
                    "retrieval_error",
                    f"Retrieval failed: {verdict.get('error') or verdict.get('status')}",
                    False,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            if verdict.get("sufficient"):
                return self._response(
                    "answered",
                    str(verdict.get("answer") or ""),
                    True,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            if active_graph_source == "json":
                return self._response(
                    "insufficient_json_graph",
                    "The selected JSON graph did not contain enough direct evidence to answer this question.",
                    False,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            missing = verdict.get("missing_topics") or [question]
            await self._emit(
                emit,
                "download_started",
                "Download agent finding relevant papers",
                round=round_no,
                missing_topics=missing,
            )
            dl = await self.download.find_and_download(
                question,
                missing_topics=missing,
                target_dir=str(self.coord.pdf_dir),
                max_papers=self.cfg.max_papers,
                candidate_pool=self.cfg.candidate_pool,
            )
            round_info["download"] = dl
            downloaded_names = [Path(p).name for p in (dl.get("downloaded") or [])]
            await self._emit(
                emit,
                "download_result",
                (
                    f"Downloaded {dl.get('count', 0)} PDF(s)"
                    + (f": {', '.join(downloaded_names)}" if downloaded_names else "")
                ),
                round=round_no,
                count=int(dl.get("count") or 0),
                titles=downloaded_names,
                skipped=int(dl.get("skipped") or 0),
                failed=int(dl.get("failed") or 0),
            )
            if dl.get("count", 0) == 0:
                return self._response(
                    "no_new_papers",
                    "I could not find new relevant open-access PDFs to gather more evidence.",
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
                )

            round_pdf_dir, pending_pdfs = self.coord._stage_unprocessed_pdfs(round_no)
            round_info["pending_pdfs"] = [p.name for p in pending_pdfs]
            if not pending_pdfs:
                return self._response(
                    "no_unprocessed_pdfs",
                    "Downloaded papers were already processed; no new evidence was available.",
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
                )

            await self._emit(
                emit,
                "extraction_started",
                "Extractor agent reading new PDFs",
                round=round_no,
                pdfs=[p.name for p in pending_pdfs],
            )
            ext = await self.extractor.extract(str(round_pdf_dir), str(self.coord.session_terms))
            round_info["extraction"] = ext
            if ext.get("status") == "error":
                return self._response(
                    "extraction_error",
                    f"Extraction failed: {ext.get('message') or ext}",
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
                )
            await self._emit(
                emit,
                "extraction_result",
                (
                    f"Extracted {ext.get('unique_terms', 0)} term(s) "
                    f"from {ext.get('processed_files', 0)} PDF(s)"
                ),
                round=round_no,
                term_count=int(ext.get("unique_terms") or 0),
                processed_files=int(ext.get("processed_files") or 0),
                processed_pages_with_terms=int(ext.get("processed_pages_with_terms") or 0),
            )
            self.coord._mark_processed_pdfs(pending_pdfs)

            await self._emit(
                emit,
                "kg_rebuild_started",
                "KG builder updating session graph",
                round=round_no,
            )
            kg = kg_update.rebuild_kg(str(self.coord.session_terms), str(self.coord.session_kg))
            round_info["kg"] = kg
            await self._emit(
                emit,
                "kg_rebuild_result",
                f"Rebuilt KG: {kg.get('nodes', 0)} node(s), {kg.get('edges', 0)} edge(s)",
                round=round_no,
                node_count=int(kg.get("nodes") or 0),
                edge_count=int(kg.get("edges") or 0),
            )

            if self.cfg.kg_mode == "splash":
                await self._emit(
                    emit,
                    "splash_reimport_started",
                    "Splash importer refreshing graph store",
                    round=round_no,
                )
                splash = kg_update.splash_reimport(
                    str(self.coord.session_kg),
                    splash_repo=self.cfg.splash_repo,
                    allow_wipe=self.cfg.allow_splash_wipe,
                )
                round_info["splash"] = splash
                if splash.get("status") == "error":
                    return self._response(
                        "splash_error",
                        f"Splash reimport failed: {splash.get('message') or splash}",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                await self._emit(
                    emit,
                    "splash_reimport_result",
                    "Splash graph store refreshed",
                    round=round_no,
                    status=str(splash.get("status") or ""),
                )

            await self._emit(
                emit,
                "reload_started",
                "Retrieval agent reloading updated KG",
                round=round_no,
            )
            reload_res = await self.retrieval.reload_kg(str(self.coord.session_kg))
            round_info["reload"] = reload_res
            active_graph_path = self.graph_path()
            await self._emit(
                emit,
                "reload_result",
                f"Reloaded KG with {reload_res.get('nodes', 0)} node(s)",
                round=round_no,
                node_count=int(reload_res.get("nodes") or 0),
                status=str(reload_res.get("status") or ""),
            )

        return self._response(
            "max_rounds",
            f"Reached max rounds ({self.cfg.max_rounds}) without sufficient evidence.",
            False,
            last_verdict,
            rounds,
            self.graph_path(),
        )

    def _response(
        self,
        status: str,
        answer: str,
        sufficient: bool,
        verdict: Dict[str, Any],
        rounds: List[Dict[str, Any]],
        graph_path: Path,
    ) -> ChatResponse:
        return ChatResponse(
            status=status,
            answer=answer,
            sufficient=sufficient,
            node_ids=[str(n) for n in (verdict.get("selected") or [])],
            publications=publications_for_selected_nodes(
                graph_path,
                [str(n) for n in (verdict.get("selected") or [])],
            ),
            confidence=_confidence(verdict),
            rounds=rounds,
            graph=graph_payload_from_file(graph_path),
            graph_source_requested=verdict.get("graph_source_requested"),
            graph_source_used=verdict.get("graph_source_used"),
            workdir=str(Path(self.cfg.workdir)),
        )


def create_app(cfg: CoordinatorConfig, *, cors_origins: Optional[List[str]] = None) -> FastAPI:
    app = FastAPI(title="FAIR2WISE Agent Pipeline API")
    service = AgentPipelineService(cfg)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "workdir": str(cfg.workdir),
            "kg_mode": service.runtime.graph_source,
            "backend": service.runtime.backend,
            "max_rounds": cfg.max_rounds,
        }

    @app.get("/settings", response_model=AgentSettingsResponse)
    async def get_settings() -> AgentSettingsResponse:
        return service.settings_response()

    @app.put("/settings", response_model=AgentSettingsResponse)
    async def update_settings(req: AgentSettingsUpdate) -> AgentSettingsResponse:
        try:
            return await service.apply_settings(req)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/graph", response_model=GraphPayload)
    async def graph() -> GraphPayload:
        return service.graph_payload()

    @app.get("/graph/node/{node_id}", response_model=GraphNode)
    async def graph_node(
        node_id: str,
        json_graph_path: Optional[str] = None,
    ) -> GraphNode:
        graph_path = service.graph_path()
        if json_graph_path:
            graph_path = service._resolve_json_graph_path(json_graph_path)
        node = graph_node_from_file(graph_path, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
        return node

    @app.post("/graph/upload", response_model=GraphUploadResponse)
    async def upload_graph(req: GraphUploadRequest) -> GraphUploadResponse:
        try:
            return service.save_uploaded_graph(req.filename, req.graph)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        try:
            return await service.ask(
                req.message,
                graph_source=req.graph_source,
                json_graph_path=req.json_graph_path,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/publications/search", response_model=PublicationSearchResponse)
    async def publication_search(req: PublicationSearchRequest) -> PublicationSearchResponse:
        try:
            return await service.search_publications(
                req.query,
                max_results=req.max_results,
                include_external=req.include_external,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest) -> StreamingResponse:
        async def events():
            queue: asyncio.Queue[Optional[tuple[str, Dict[str, Any]]]] = asyncio.Queue()

            async def emit(event: str, message: str, data: Dict[str, Any]) -> None:
                await queue.put((
                    "progress",
                    {
                        "phase": event,
                        "message": message,
                        **data,
                    },
                ))

            async def run() -> None:
                try:
                    response = await service.ask_with_progress(
                        req.message,
                        emit,
                        graph_source=req.graph_source,
                        json_graph_path=req.json_graph_path,
                    )
                    payload = _model_to_jsonable(response)
                    if response.status.endswith("_error"):
                        await queue.put(("error", payload))
                    else:
                        await queue.put(("complete", payload))
                except Exception as exc:  # pragma: no cover - defensive stream boundary
                    await queue.put(("error", {"status": "stream_error", "message": str(exc)}))
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    event, data = item
                    yield _sse(event, data)
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def run_api(cfg: CoordinatorConfig, *, host: str, port: int, cors_origins: Optional[List[str]] = None) -> None:
    import uvicorn

    uvicorn.run(create_app(cfg, cors_origins=cors_origins), host=host, port=port)
