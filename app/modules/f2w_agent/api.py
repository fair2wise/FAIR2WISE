"""FastAPI bridge for the FAIR2WISE 3-agent chat loop."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import kg_update
from .coordinator import Coordinator, CoordinatorConfig
from app.modules import kg_rag_api as krag

from .debate_agent import EvidenceDebateAgent
from .download_agent import (
    DownloadAgent,
    _reconstruct_abstract,
    _safe_name,
    _sanitize_openalex_search,
)
from .extractor_agent import ExtractorAgent
from .orchestrator_agent import (
    WorkflowOrchestratorAgent,
    _extracted_terms_followup,
    _paper_reference_followup,
)
from .paper_evidence_agent import PaperEvidenceAgent, summarize_extracted_terms
from .retrieval_agent import RetrievalAgent
from app.modules.project_config import config_value, get_config
from .session_memory import SessionMemory
from .workflow_state import WorkflowStateStore

logger = logging.getLogger(__name__)


class ChatMessageInput(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    # Pending candidate cards intentionally have no answer text. Accept blank
    # legacy history entries here; _history_payload discards them.
    content: str = ""


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    messages: List[ChatMessageInput] = Field(default_factory=list)
    session_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
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
    pending: Optional[Dict[str, Any]] = None
    orchestration: Optional[Dict[str, Any]] = None


class ChatActionRequest(BaseModel):
    decision: str = Field(..., pattern="^(yes|no)$")
    kind: Optional[str] = Field(default=None, pattern="^(download|extraction)$")
    candidate_index: Optional[int] = Field(default=None, ge=0)
    messages: List[ChatMessageInput] = Field(default_factory=list)
    session_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    graph_source: Optional[str] = Field(default=None, pattern="^(splash|json)$")
    json_graph_path: Optional[str] = None


class GraphUploadRequest(BaseModel):
    filename: str = "uploaded_graph.json"
    graph: Dict[str, Any]


class GraphUploadResponse(BaseModel):
    graph: GraphPayload
    graph_path: str
    filename: str


class GraphNodeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=25)


class GraphNodeSearchResult(BaseModel):
    node: GraphNode
    score: float


class GraphNodeSearchResponse(BaseModel):
    query: str
    retrieval_backend: str
    results: List[GraphNodeSearchResult] = Field(default_factory=list)


class LinkedCodeSnippetUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: Optional[str] = None
    function_name: Optional[str] = None
    code_language: Optional[str] = None
    code_snippet: Optional[str] = None
    action: str = Field(default="upsert", alias="_action", pattern="^(upsert|unlink)$")


class GraphRelationshipUpdate(BaseModel):
    action: str = Field(..., pattern="^(add|remove)$")
    source: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)


class GraphNodeUpdateRequest(BaseModel):
    label: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    code_snippet: Optional[str] = None
    publications: Optional[List[Dict[str, Any]]] = None
    linked_code_snippets: Optional[List[LinkedCodeSnippetUpdate]] = None
    relationship_updates: Optional[List[GraphRelationshipUpdate]] = None


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


class SessionResetResponse(BaseModel):
    status: str
    session_memory: str
    session_memory_has_context: bool
    workflow_state: Optional[str] = None
    workflow_phase: str = "idle"


class SessionResetRequest(BaseModel):
    session_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )


class AgentSettingsResponse(BaseModel):
    backend: str
    model: str
    graph_source: str
    workflow_mode: str = "agentic"
    extraction_mode: str = "targeted"
    targeted_max_pages: int = 6
    json_graph_path: Optional[str] = None
    available_json_graphs: List[str] = Field(default_factory=list)
    available_cborg_models: List[str] = Field(default_factory=list)
    default_ollama_model: str = "deepseek-r1:70b"


class AgentSettingsUpdate(BaseModel):
    backend: Optional[str] = Field(default=None, pattern="^(cborg|ollama)$")
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    graph_source: Optional[str] = Field(default=None, pattern="^(splash|json)$")
    workflow_mode: Optional[str] = Field(default=None, pattern="^(deterministic|agentic)$")
    extraction_mode: Optional[str] = Field(default=None, pattern="^(full|targeted)$")
    targeted_max_pages: Optional[int] = Field(default=None, ge=1, le=100)
    json_graph_path: Optional[str] = None


ProgressEmitter = Callable[[str, str, Dict[str, Any]], Awaitable[None]]

DEFAULT_JSON_GRAPH = "storage/kg/matkg_xray_papers_cborg_chat.json"

# Legacy CBORG ids that still appear in old env/localStorage values.
_CBORG_MODEL_ALIASES = {
    "google/gemini-flash": "gemini-flash",
    "google/gemini-flash-lite": "gemini-2.5-flash-lite",
    "google/gemini-pro": "gemini-pro",
    "google/gemini-flash-high": "gemini-flash-high",
    "google/gemini-pro-high": "gemini-pro-high",
    # CBORG alias gemini-flash-lite currently resolves to an unavailable Vertex preview.
    "gemini-flash-lite": "gemini-2.5-flash-lite",
}


def normalize_cborg_model(model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    cleaned = str(model).strip()
    if not cleaned:
        return cleaned
    return _CBORG_MODEL_ALIASES.get(cleaned, cleaned)


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
    model: str = "lbl/cborg-chat"
    graph_source: str = "splash"
    workflow_mode: str = "agentic"
    extraction_mode: str = "targeted"
    targeted_max_pages: int = 6
    json_graph_path: Optional[str] = None


@dataclass
class ChatSessionContext:
    memory: SessionMemory
    workflow: WorkflowStateStore
    pending: Optional[Dict[str, Any]] = None
    last_orchestration: Optional[Dict[str, Any]] = None


def default_runtime_model(backend: str, configured: Optional[str] = None) -> str:
    if configured and str(configured).strip():
        model = str(configured).strip()
        if backend == "cborg":
            model = normalize_cborg_model(model) or model
        return model
    if backend == "ollama":
        for env_name in ("KG_RAG_OLLAMA_MODEL", "KG_RAG_MODEL"):
            env_value = os.environ.get(env_name)
            if env_value and env_value.strip():
                return env_value.strip()
        return str(config_value("kg_rag.ollama.model", "deepseek-r1:70b") or "deepseek-r1:70b")
    for env_name in ("KG_RAG_CBORG_MODEL", "KG_RAG_MODEL", "F2W_MODEL"):
        env_value = os.environ.get(env_name)
        if env_value and env_value.strip():
            model = env_value.strip()
            return normalize_cborg_model(model) or model
    default = str(config_value("kg_rag.cborg.model", "lbl/cborg-chat") or "lbl/cborg-chat")
    return normalize_cborg_model(default) or default


def list_cborg_models(*, current_model: Optional[str] = None) -> List[str]:
    configured = get_config("f2w_agent.cborg_models", [])
    models: List[str] = []
    seen: set[str] = set()
    if isinstance(configured, list):
        for item in configured:
            normalized = normalize_cborg_model(str(item).strip())
            if normalized and normalized not in seen:
                seen.add(normalized)
                models.append(normalized)
    if not models:
        models = [default_runtime_model("cborg")]
        seen.update(models)
    normalized_current = normalize_cborg_model(current_model)
    if normalized_current and normalized_current not in seen:
        models = [normalized_current, *models]
    return models


def default_ollama_model_name() -> str:
    return default_runtime_model("ollama")


def _string_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _safe_candidate_filename(candidate: Dict[str, Any]) -> str:
    """Expected downloaded PDF filename for an OpenAlex candidate summary."""
    return f"{_safe_name(candidate)}.pdf"


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
    has_explicit_publications = "publications" in raw and isinstance(raw.get("publications"), list)
    for pub in raw.get("publications") or []:
        if not isinstance(pub, dict):
            continue
        clean = {field: pub.get(field) for field in _PUBLICATION_FIELDS if pub.get(field) not in (None, "", [])}
        source = clean.get("source_paper")
        if source and not clean.get("doi"):
            clean = {**_publication_from_source_identifier(str(source)), **clean}
        pages = pub.get("pages")
        if isinstance(pages, list) and pages:
            clean["pages"] = pages
        if clean:
            publications.append(clean)
    if has_explicit_publications:
        return publications
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


def _clean_publication_entry(publication: Dict[str, Any]) -> Dict[str, Any]:
    clean = {
        field: publication.get(field)
        for field in _PUBLICATION_FIELDS
        if publication.get(field) not in (None, "", [])
    }
    pages = publication.get("pages")
    if isinstance(pages, list) and pages:
        clean["pages"] = pages
    source = clean.get("source_paper")
    if source and not clean.get("doi"):
        clean = {**_publication_from_source_identifier(str(source)), **clean}
    return clean


def _is_temp_snippet_id(snippet_id: Optional[str]) -> bool:
    if not snippet_id:
        return True
    lowered = snippet_id.lower()
    return lowered.startswith("temp:") or lowered.startswith("new:")


def _new_snippet_matkg_id(*, label: str = "", function_name: str = "", code: str = "") -> str:
    seed = function_name or label or "snippet"
    slug = re.sub(r"[^a-zA-Z0-9]+", "", seed)[:40] or "snippet"
    code_hash = uuid.uuid5(uuid.NAMESPACE_URL, code or slug).hex[:8]
    return f"matkg:snippet{slug}{code_hash}"


def _load_session_graph(graph_path: Path) -> Dict[str, Any]:
    if not graph_path.exists():
        return {"things": [], "associations": []}
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"things": [], "associations": []}
    if not isinstance(data, dict):
        return {"things": [], "associations": []}
    data.setdefault("things", [])
    data.setdefault("associations", [])
    return data


def _save_session_graph(graph_path: Path, data: Dict[str, Any]) -> None:
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_thing(data: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    things = data.get("things") or []
    if not isinstance(things, list):
        return None
    for raw in things:
        if isinstance(raw, dict) and _string_value(raw.get("id")) == node_id:
            return raw
    return None


def _normalize_node_type(value: str) -> str:
    cleaned = _string_value(value).replace("matkg:", "").replace("rel:", "").strip()
    if not cleaned:
        raise ValueError("Node type cannot be empty")
    # Preserve common PascalCase schema names; otherwise keep user casing/spacing trimmed.
    return cleaned


_RELATIONSHIP_CURIE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _normalize_relationship_predicate(value: str) -> str:
    cleaned = _string_value(value)
    if not cleaned:
        raise ValueError("Relationship predicate cannot be empty")
    if ":" in cleaned:
        if not _RELATIONSHIP_CURIE_RE.fullmatch(cleaned):
            raise ValueError(f"Invalid relationship predicate: {value}")
        return cleaned
    local_name = re.sub(r"[^A-Za-z0-9]+", "_", cleaned).strip("_").lower()
    if not local_name or not re.match(r"^[a-z0-9]", local_name):
        raise ValueError(f"Invalid relationship predicate: {value}")
    return f"rel:{local_name}"


def _apply_node_field_updates(
    thing: Dict[str, Any],
    *,
    label: Optional[str],
    node_type: Optional[str],
    description: Optional[str],
    code_snippet: Optional[str],
    publications: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Apply editable fields onto a MatKG thing dict; return splash property patch."""
    splash_props: Dict[str, Any] = {}
    if label is not None:
        thing["name"] = label
    if node_type is not None:
        previous = _string_value(thing.get("category") or thing.get("type"))
        if previous and previous != node_type and "raw_category" not in thing:
            thing["raw_category"] = previous
            splash_props["raw_category"] = previous
        thing["category"] = node_type
        thing["type"] = f"matkg:{node_type}"
        splash_props["category"] = node_type
        splash_props["type"] = f"matkg:{node_type}"
    if description is not None:
        thing["description"] = description
        splash_props["description"] = description
        if _is_code_snippet_raw(thing):
            thing["code_description"] = description
            splash_props["code_description"] = description
    if code_snippet is not None:
        thing["code_snippet"] = code_snippet
        splash_props["code_snippet"] = code_snippet
    if publications is not None:
        cleaned = [_clean_publication_entry(pub) for pub in publications if isinstance(pub, dict)]
        cleaned = [pub for pub in cleaned if pub]
        thing["publications"] = cleaned
        splash_props["publications"] = cleaned
        for field in _PUBLICATION_FIELDS:
            if field == "source_paper":
                continue
            if field in thing and field not in {"authors", "institutions", "keywords"}:
                # Clear conflicting single-source fallback scalars when replacing the list.
                if field in (
                    "publication_year",
                    "paper_title",
                    "doi",
                    "journal",
                    "volume",
                    "issue",
                    "pages_range",
                    "abstract_text",
                ):
                    thing[field] = None
        sources = sorted(
            {
                str(pub.get("source_paper") or "").strip()
                for pub in cleaned
                if str(pub.get("source_paper") or "").strip()
            }
        )
        thing["source_papers"] = sources
        splash_props["source_papers"] = sources
    return splash_props


def _snippet_thing_payload(
    *,
    snippet_id: str,
    label: str,
    function_name: Optional[str],
    code_language: Optional[str],
    code_snippet: str,
) -> Dict[str, Any]:
    return {
        "id": snippet_id,
        "name": label or function_name or snippet_id,
        "category": "CodeSnippet",
        "type": "matkg:CodeSnippet",
        "description": "",
        "function_name": function_name,
        "code_language": code_language,
        "code_snippet": code_snippet,
        "publications": [],
        "source_papers": [],
        "context_snippets": [],
        "properties": [],
    }


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


MAX_HISTORY_MESSAGES = 8
CANDIDATE_PAPERS_INTRO = (
    "I could not answer this query reliably from the retrieved context. "
    "Below is a list of papers that might be relevant to the subject."
)
DIRECT_DOWNLOAD_PAPERS_INTRO = (
    "I found these open-access paper matches. Tell me which paper to download "
    "by title, number, DOI, or repository."
)


def _direct_download_query(message: str, fallback_topic: str = "") -> str:
    """Extract a paper title/topic from an explicit download command."""
    text = re.sub(r"\s+", " ", str(message or "").strip())
    arxiv_match = re.search(
        r"(?:arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if arxiv_match:
        return arxiv_match.group(1)
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE)
    if doi_match:
        return doi_match.group(0).rstrip(".,;)")

    query = re.sub(
        r"^(?:please\s+)?(?:"
        r"(?:(?:can|could|would|will)\s+you\s+)(?:please\s+)?|"
        r"i\s+(?:want|need)\s+(?:you\s+)?to\s+|"
        r"i(?:'d|\s+would)\s+like\s+(?:you\s+)?to\s+"
        r")?"
        r"(?:download|fetch|grab)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\b(?:from|on)\s+arxiv\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(?:via|using|through)\s+openalex\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(
        r"^(?:another|a\s+different)\s+(?:paper|article)\s+(?:about|on|titled)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^(?:a|an|the)\s+(?:paper|article)\s+(?:about|on|titled)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^(?:a|an|the)\s+(?:paper|article)\s*", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" \t\r\n.,;:?!'\"")
    generic = {
        "", "another", "another one", "another paper", "another article",
        "a different one", "different one", "a different paper", "a different article", "one",
        "it", "that", "the recommended", "recommended", "the recommended one",
        "the recommended paper", "recommended paper",
    }
    return str(fallback_topic or "").strip() if query.casefold() in generic else query


def _is_arxiv_candidate(candidate: Dict[str, Any]) -> bool:
    repository = str(candidate.get("repository") or "").casefold()
    urls = " ".join(str(url) for url in (candidate.get("pdf_urls") or [])).casefold()
    doi = str(candidate.get("doi") or "").casefold()
    identifier = str(candidate.get("id") or "").casefold()
    return bool(
        "arxiv" in repository
        or "arxiv.org" in urls
        or "arxiv.org" in identifier
        or "10.48550/arxiv" in doi
        or doi.startswith("arxiv:")
    )


def _extraction_outcome_text(result: Dict[str, Any]) -> str:
    """Summarize extractor output without treating cumulative terms as newly added."""
    processed_files = int(result.get("processed_files") or 0)
    processed_pages = int(result.get("processed_pages_total") or 0)
    pages_with_terms = int(result.get("processed_pages_with_terms") or 0)
    unique_terms = int(result.get("unique_terms") or 0)
    parts = [
        f"processed {processed_files} paper{'s' if processed_files != 1 else ''}",
    ]
    if processed_pages:
        parts.append(
            f"inspected {processed_pages} page{'s' if processed_pages != 1 else ''}"
        )
        parts.append(
            f"{pages_with_terms} page{'s' if pages_with_terms != 1 else ''} yielded extractable terms"
        )
    parts.append(
        f"term store now contains {unique_terms} unique term{'s' if unique_terms != 1 else ''}"
    )
    return "Extraction completed: " + "; ".join(parts) + "."


def _post_extraction_answer(
    extraction: Dict[str, Any],
    term_report: Dict[str, Any],
    question: str,
    verdict: Dict[str, Any],
) -> str:
    """Combine extraction facts with an explicit original-query verdict."""
    sections = [_extraction_outcome_text(extraction)]
    if term_report.get("sufficient"):
        sections.append(str(term_report.get("answer") or "").strip())
    else:
        sections.append(
            "Extracted-term summary unavailable: "
            + str(term_report.get("answer") or "no page-grounded terms were recorded.")
        )

    if str(verdict.get("status") or "").endswith("_error"):
        sections.append(
            "Support for original query: not evaluated.\n"
            "Why: the updated knowledge graph could not be checked against the original query "
            f'“{question}”: {verdict.get("error") or verdict.get("status")}.'
        )
        return "\n\n".join(section for section in sections if section)

    if verdict.get("sufficient"):
        direct_count = int(verdict.get("direct_evidence_count") or 0)
        reason = "updated retrieval found enough direct, grounded evidence"
        if direct_count:
            reason += f" across {direct_count} evidence-bearing KG node{'s' if direct_count != 1 else ''}"
        sections.append(
            "Support for original query: yes.\n"
            f"Why: {reason}. The updated knowledge graph now contains enough direct evidence "
            "to answer the original query.\n\n"
            + str(verdict.get("answer") or "")
        )
        return "\n\n".join(section for section in sections if section)

    missing = [str(topic).strip() for topic in (verdict.get("missing_topics") or []) if str(topic).strip()]
    reason = (
        "the extracted concepts still do not provide enough direct evidence to answer "
        f'“{question}” reliably.'
    )
    if verdict.get("no_evidence"):
        reason = "updated retrieval found no direct evidence that answers the original query."
    missing_text = "\n".join(f"- {topic}" for topic in missing) if missing else "- Complete grounded answer"
    sections.append(
        "Support for original query: no.\n"
        f"Why: {reason}\n"
        "Missing topics:\n"
        f"{missing_text}\n\n"
        "The updated knowledge graph still does not contain enough direct evidence to answer "
        "the original query reliably. No additional paper search was started."
    )
    return "\n\n".join(section for section in sections if section)


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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _normalize_chat_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s']", " ", text.lower())).strip()


def _history_payload(messages: Optional[List[ChatMessageInput]]) -> List[Dict[str, str]]:
    history: List[Dict[str, str]] = []
    for message in (messages or [])[-MAX_HISTORY_MESSAGES:]:
        if isinstance(message, dict):
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "").strip()
        else:
            role = str(message.role or "").strip()
            content = str(message.content or "").strip()
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content[:4000]})
    return history


def _needs_history_rewrite(question: str, history: List[Dict[str, str]]) -> bool:
    if not history:
        return False
    return _looks_contextual_followup(question)


def _looks_contextual_followup(question: str) -> bool:
    normalized = _normalize_chat_text(question)
    if not normalized:
        return False
    followup_patterns = (
        r"\b(it|that|this|those|these|them|they|there)\b",
        r"\b(first|second|third|last|previous|latter|former)\b",
        r"\b(one|ones|same|other|another)\b",
        r"^what about\b",
        r"^how about\b",
        r"^compare\b",
        r"^and\b",
        r"^why\b",
        r"^how so\b",
        r"^tell me more\b",
        r"^more\b",
        r"^continue\b",
        r"^expand\b",
        r"^find more\b",
        r"^give me more\b",
        r"^show more\b",
        r"\bsame topic\b",
        r"\bthis topic\b",
    )
    return any(re.search(pattern, normalized) for pattern in followup_patterns)


def _select_candidates_by_indices(
    candidates: List[Dict[str, Any]],
    indices: Any,
) -> List[Dict[str, Any]]:
    if isinstance(indices, int):
        indices = [indices]
    if not isinstance(indices, list):
        return []
    selected: List[Dict[str, Any]] = []
    for item in indices:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates):
            selected.append(candidates[idx])
    return selected


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
    """Reusable in-process orchestrated pipeline for HTTP chat requests."""

    def __init__(self, cfg: CoordinatorConfig) -> None:
        self.cfg = cfg
        self.coord = Coordinator(cfg)
        self.memory = SessionMemory(self.coord.workdir / "session_memory.json")
        self.workflow = WorkflowStateStore(self.coord.workdir / "workflow_state.json")
        self.lock = asyncio.Lock()
        # Pending approvals survive backend and one-shot CLI restarts.
        self.pending: Optional[Dict[str, Any]] = self.workflow.pending
        self._last_orchestration: Optional[Dict[str, Any]] = None
        self._active_session_key = "__legacy__"
        self._session_contexts: Dict[str, ChatSessionContext] = {
            self._active_session_key: ChatSessionContext(
                memory=self.memory,
                workflow=self.workflow,
                pending=self.pending,
            )
        }
        available_json_graphs = list_storage_kg_json_files()
        initial_graph_source = "json" if cfg.graph and cfg.kg_mode == "json" else "splash"
        initial_backend = cfg.backend if cfg.backend in {"cborg", "ollama"} else "cborg"
        initial_model = default_runtime_model(initial_backend, cfg.model)
        self.runtime = RuntimeSettings(
            backend=initial_backend,
            model=initial_model,
            graph_source=initial_graph_source,
            workflow_mode=cfg.workflow_mode if cfg.workflow_mode in {"deterministic", "agentic"} else "agentic",
            extraction_mode=cfg.extraction_mode if cfg.extraction_mode in {"full", "targeted"} else "targeted",
            targeted_max_pages=cfg.targeted_max_pages if cfg.targeted_max_pages > 0 else 6,
            json_graph_path=cfg.graph if cfg.graph and initial_graph_source == "json" else default_json_graph_path(
                configured_graph=cfg.graph,
                available=available_json_graphs,
            ),
        )
        self.cfg.model = initial_model
        self._rebuild_agents()
        if self.runtime.graph_source == "splash":
            self._sync_session_graph_from_splash()

    @staticmethod
    def _session_key(session_id: Optional[str]) -> str:
        if session_id is None:
            return "__legacy__"
        value = session_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
            raise ValueError("Invalid session ID")
        return value

    def _store_active_session(self) -> None:
        self._session_contexts[self._active_session_key] = ChatSessionContext(
            memory=self.memory,
            workflow=self.workflow,
            pending=self.pending,
            last_orchestration=self._last_orchestration,
        )

    def _activate_session(self, session_id: Optional[str]) -> None:
        key = self._session_key(session_id)
        if key == self._active_session_key:
            return
        self._store_active_session()
        context = self._session_contexts.get(key)
        if context is None:
            context_dir = self.coord.workdir / "chat_sessions" / key
            memory = SessionMemory(context_dir / "session_memory.json")
            workflow = WorkflowStateStore(context_dir / "workflow_state.json")
            context = ChatSessionContext(
                memory=memory,
                workflow=workflow,
                pending=workflow.pending,
            )
            self._session_contexts[key] = context
        self._active_session_key = key
        self.memory = context.memory
        self.workflow = context.workflow
        self.pending = context.pending
        self._last_orchestration = context.last_orchestration

    def _sync_session_graph_from_splash(self) -> bool:
        """Refresh session MatKG JSON from splash so UI reads survive restarts."""
        try:
            data = kg_update.load_splash_graph(timeout=5)
            if not (data.get("things") or []):
                logger.info("Skipping splash→session sync: splash graph is empty")
                return False
            dest = self._session_graph_path()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as exc:
            logger.warning("Failed to sync session KG from splash: %s", exc)
            return False

    def _active_model(self) -> str:
        return default_runtime_model(self.runtime.backend, self.runtime.model)

    def _rebuild_agents(self) -> None:
        model = self._active_model()
        graph_file = str(self.graph_path())
        self.retrieval = RetrievalAgent(
            graph_file=graph_file,
            graph_source=self.runtime.graph_source,
            backend=self.runtime.backend,
            model=model,
        )
        self.download = DownloadAgent(
            backend=self.runtime.backend,
            model=model,
            download_delay_seconds=self.cfg.download_delay_seconds,
            validate_downloads=self.cfg.validate_downloads,
        )
        self.debate = EvidenceDebateAgent(
            backend=self.runtime.backend,
            model=model,
        )
        self.orchestrator = WorkflowOrchestratorAgent(
            backend=self.runtime.backend,
            model=model,
            max_steps=getattr(self.cfg, "max_orchestration_steps", 12),
        )
        self.paper_evidence = PaperEvidenceAgent(
            backend=self.runtime.backend,
            model=model,
        )
        self.extractor = ExtractorAgent(
            backend=self.runtime.backend,
            model=model,
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
            return self._resolve_runtime_json_graph_path(self.runtime.json_graph_path)
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
            model=self._active_model(),
            graph_source=self.runtime.graph_source,
            workflow_mode=self.runtime.workflow_mode,
            extraction_mode=self.runtime.extraction_mode,
            targeted_max_pages=self.runtime.targeted_max_pages,
            json_graph_path=json_graph_path,
            available_json_graphs=available,
            available_cborg_models=list_cborg_models(current_model=self.runtime.model),
            default_ollama_model=default_ollama_model_name(),
        )

    async def apply_settings(self, update: AgentSettingsUpdate) -> AgentSettingsResponse:
        async with self.lock:
            backend_changed = False
            model_changed = False
            graph_changed = False

            if update.backend is not None and update.backend != self.runtime.backend:
                self.runtime.backend = update.backend
                backend_changed = True
                if update.model is None:
                    self.runtime.model = default_runtime_model(self.runtime.backend)
                    self.cfg.model = self.runtime.model
                    model_changed = True

            if update.model is not None:
                normalized = normalize_cborg_model(update.model.strip()) or ""
                if not normalized:
                    raise ValueError("Model name cannot be empty")
                if normalized != self.runtime.model:
                    self.runtime.model = normalized
                    self.cfg.model = normalized
                    model_changed = True

            if update.graph_source is not None and update.graph_source != self.runtime.graph_source:
                self.runtime.graph_source = update.graph_source
                graph_changed = True

            if update.workflow_mode is not None:
                self.runtime.workflow_mode = update.workflow_mode
                self.cfg.workflow_mode = update.workflow_mode

            if update.extraction_mode is not None:
                self.runtime.extraction_mode = update.extraction_mode
                self.cfg.extraction_mode = update.extraction_mode

            if update.targeted_max_pages is not None:
                self.runtime.targeted_max_pages = int(update.targeted_max_pages)
                self.cfg.targeted_max_pages = int(update.targeted_max_pages)

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
                active_graph_path = self._resolve_runtime_json_graph_path(self.runtime.json_graph_path)
                active_graph_source = "json"
            else:
                active_graph_path = self._session_graph_path()
                active_graph_source = "splash"
                if graph_changed:
                    self._sync_session_graph_from_splash()
                    active_graph_path = self._session_graph_path()

            if backend_changed or model_changed:
                self._rebuild_agents()

            if backend_changed or graph_changed:
                await self.retrieval.reload_kg(
                    str(active_graph_path),
                    graph_source=active_graph_source,
                )

            return self.settings_response()

    def graph_payload(self) -> GraphPayload:
        return graph_payload_from_file(self.graph_path())

    async def search_graph_nodes(self, query: str, limit: int = 10) -> GraphNodeSearchResponse:
        query = query.strip()
        if not query:
            raise ValueError("Search query is required")
        # Ask for extra candidates because unresolved Unknown stubs are not useful in the UI.
        ranked = await self.retrieval.search_node_scores(query, min(25, max(limit * 3, limit)))
        graph_path = self.graph_path()
        results: List[GraphNodeSearchResult] = []
        for match in ranked.get("matches") or []:
            node = graph_node_from_file(graph_path, _string_value(match.get("id")))
            if node is None or node.type.strip().lower() == "unknown":
                continue
            results.append(
                GraphNodeSearchResult(node=node, score=float(match.get("score") or 0.0))
            )
            if len(results) >= limit:
                break
        return GraphNodeSearchResponse(
            query=query,
            retrieval_backend=_string_value(ranked.get("retrieval_backend"), "lexical"),
            results=results,
        )

    async def update_graph_node(self, node_id: str, update: GraphNodeUpdateRequest) -> GraphNode:
        if self.runtime.graph_source == "json":
            raise ValueError("Editing node properties requires splash graph mode")

        graph_path = self.graph_path()
        data = _load_session_graph(graph_path)
        thing = _find_thing(data, node_id)
        if thing is None:
            raise FileNotFoundError(f"Node not found: {node_id}")

        splash_entity = kg_update.splash_find_entity_by_matkg_id(node_id)
        if splash_entity is None:
            raise FileNotFoundError(f"Splash entity not found for node: {node_id}")

        if update.relationship_updates is not None:
            self._validate_relationship_updates(
                data,
                edited_node_id=node_id,
                updates=update.relationship_updates,
            )

        normalized_type = _normalize_node_type(update.type) if update.type is not None else None
        will_be_snippet = (
            normalized_type.lower() == "codesnippet"
            if normalized_type is not None
            else _is_code_snippet_raw(thing)
        )
        if update.code_snippet is not None and not will_be_snippet:
            raise ValueError("code_snippet can only be set on CodeSnippet nodes")

        splash_props = _apply_node_field_updates(
            thing,
            label=update.label,
            node_type=normalized_type,
            description=update.description,
            code_snippet=update.code_snippet,
            publications=update.publications,
        )

        kg_update.splash_update_entity(
            splash_entity["id"],
            name=update.label if update.label is not None else None,
            entity_type=normalized_type,
            properties=splash_props or None,
        )

        if update.linked_code_snippets is not None:
            await self._apply_linked_snippet_updates(
                data,
                subject_node_id=node_id,
                subject_splash_id=str(splash_entity["id"]),
                updates=update.linked_code_snippets,
            )

        if update.relationship_updates is not None:
            self._apply_relationship_updates(
                data,
                edited_node_id=node_id,
                updates=update.relationship_updates,
            )

        _save_session_graph(graph_path, data)
        await self.retrieval.reload_kg(str(graph_path), graph_source="splash")

        node = graph_node_from_file(graph_path, node_id)
        if node is None:
            raise FileNotFoundError(f"Node not found after update: {node_id}")
        return node

    @staticmethod
    def _validate_relationship_updates(
        data: Dict[str, Any],
        *,
        edited_node_id: str,
        updates: List[GraphRelationshipUpdate],
    ) -> None:
        for update in updates:
            source = update.source.strip()
            target = update.target.strip()
            _normalize_relationship_predicate(update.predicate)
            if edited_node_id not in {source, target}:
                raise ValueError("Relationship update must involve the edited node")
            if source == target:
                raise ValueError("Self relationships are not supported")
            if _find_thing(data, source) is None:
                raise FileNotFoundError(f"Relationship source node not found: {source}")
            if _find_thing(data, target) is None:
                raise FileNotFoundError(f"Relationship target node not found: {target}")

    def _apply_relationship_updates(
        self,
        data: Dict[str, Any],
        *,
        edited_node_id: str,
        updates: List[GraphRelationshipUpdate],
    ) -> None:
        associations = data.setdefault("associations", [])
        if not isinstance(associations, list):
            associations = []
            data["associations"] = associations

        self._validate_relationship_updates(
            data,
            edited_node_id=edited_node_id,
            updates=updates,
        )

        for update in updates:
            source = update.source.strip()
            target = update.target.strip()
            predicate = _normalize_relationship_predicate(update.predicate)
            source_entity = kg_update.splash_find_entity_by_matkg_id(source)
            target_entity = kg_update.splash_find_entity_by_matkg_id(target)
            if source_entity is None:
                raise FileNotFoundError(f"Splash entity not found for node: {source}")
            if target_entity is None:
                raise FileNotFoundError(f"Splash entity not found for node: {target}")

            def is_exact(raw: Any) -> bool:
                if not isinstance(raw, dict):
                    return False
                return (
                    _string_value(raw.get("subject") or raw.get("source")) == source
                    and _string_value(raw.get("predicate"), "rel:related_to") == predicate
                    and _string_value(raw.get("object") or raw.get("target")) == target
                )

            splash_links = kg_update.splash_find_links(
                subject_id=str(source_entity["id"]),
                predicate=predicate,
                object_id=str(target_entity["id"]),
            )
            if update.action == "add":
                if not any(is_exact(raw) for raw in associations):
                    associations.append(
                        {"subject": source, "predicate": predicate, "object": target}
                    )
                if not splash_links:
                    kg_update.splash_create_link(
                        subject_id=str(source_entity["id"]),
                        predicate=predicate,
                        object_id=str(target_entity["id"]),
                    )
                continue

            associations[:] = [raw for raw in associations if not is_exact(raw)]
            for link in splash_links:
                kg_update.splash_delete_link(str(link["id"]))

    async def _apply_linked_snippet_updates(
        self,
        data: Dict[str, Any],
        *,
        subject_node_id: str,
        subject_splash_id: str,
        updates: List[LinkedCodeSnippetUpdate],
    ) -> None:
        current = {
            snippet.id: snippet
            for snippet in _linked_code_snippets_from_data(data, subject_node_id)
        }
        desired_ids: set[str] = set()
        associations = data.setdefault("associations", [])
        if not isinstance(associations, list):
            associations = []
            data["associations"] = associations
        things = data.setdefault("things", [])
        if not isinstance(things, list):
            things = []
            data["things"] = things

        def unlink_snippet(snippet_id: str) -> None:
            kept = [
                assoc
                for assoc in associations
                if not (
                    isinstance(assoc, dict)
                    and _string_value(assoc.get("predicate"), "rel:related_to") == "rel:has_code_snippet"
                    and (
                        (
                            _string_value(assoc.get("subject") or assoc.get("source")) == subject_node_id
                            and _string_value(assoc.get("object") or assoc.get("target")) == snippet_id
                        )
                        or (
                            _string_value(assoc.get("object") or assoc.get("target")) == subject_node_id
                            and _string_value(assoc.get("subject") or assoc.get("source")) == snippet_id
                        )
                    )
                )
            ]
            associations[:] = kept
            snippet_entity = kg_update.splash_find_entity_by_matkg_id(snippet_id)
            if snippet_entity is None:
                return
            for link in kg_update.splash_find_links(
                subject_id=subject_splash_id,
                predicate="rel:has_code_snippet",
                object_id=str(snippet_entity["id"]),
            ):
                kg_update.splash_delete_link(str(link["id"]))
            for link in kg_update.splash_find_links(
                subject_id=str(snippet_entity["id"]),
                predicate="rel:has_code_snippet",
                object_id=subject_splash_id,
            ):
                kg_update.splash_delete_link(str(link["id"]))

        for item in updates:
            if item.action == "unlink":
                if item.id:
                    unlink_snippet(item.id)
                continue

            code_text = item.code_snippet
            if code_text is None and item.id and item.id in current:
                code_text = current[item.id].code_snippet
            code_text = str(code_text or "").strip()
            if not code_text:
                raise ValueError("linked code snippets require a code_snippet body")

            label = (item.label or "").strip()
            function_name = (item.function_name or "").strip() or None
            code_language = (item.code_language or "").strip() or None
            if not label:
                label = function_name or "code snippet"

            if item.id and not _is_temp_snippet_id(item.id):
                snippet_id = item.id
                existing = _find_thing(data, snippet_id)
                if existing is None:
                    existing = _snippet_thing_payload(
                        snippet_id=snippet_id,
                        label=label,
                        function_name=function_name,
                        code_language=code_language,
                        code_snippet=code_text,
                    )
                    things.append(existing)
                else:
                    existing["name"] = label
                    existing["function_name"] = function_name
                    existing["code_language"] = code_language
                    existing["code_snippet"] = code_text
                    existing["category"] = "CodeSnippet"

                splash_snippet = kg_update.splash_find_entity_by_matkg_id(snippet_id)
                snippet_props = {
                    "matkg_id": snippet_id,
                    "code_snippet": code_text,
                    "function_name": function_name,
                    "code_language": code_language,
                }
                if splash_snippet is None:
                    splash_snippet = kg_update.splash_create_entity(
                        entity_type="CodeSnippet",
                        name=label,
                        uri=snippet_id,
                        properties=snippet_props,
                    )
                    kg_update.splash_create_link(
                        subject_id=subject_splash_id,
                        predicate="rel:has_code_snippet",
                        object_id=str(splash_snippet["id"]),
                    )
                    associations.append(
                        {
                            "subject": subject_node_id,
                            "predicate": "rel:has_code_snippet",
                            "object": snippet_id,
                        }
                    )
                else:
                    kg_update.splash_update_entity(
                        str(splash_snippet["id"]),
                        name=label,
                        properties=snippet_props,
                    )
                    linked = any(
                        isinstance(assoc, dict)
                        and _string_value(assoc.get("predicate"), "rel:related_to") == "rel:has_code_snippet"
                        and (
                            (
                                _string_value(assoc.get("subject") or assoc.get("source")) == subject_node_id
                                and _string_value(assoc.get("object") or assoc.get("target")) == snippet_id
                            )
                            or (
                                _string_value(assoc.get("object") or assoc.get("target")) == subject_node_id
                                and _string_value(assoc.get("subject") or assoc.get("source")) == snippet_id
                            )
                        )
                        for assoc in associations
                    )
                    if not linked:
                        kg_update.splash_create_link(
                            subject_id=subject_splash_id,
                            predicate="rel:has_code_snippet",
                            object_id=str(splash_snippet["id"]),
                        )
                        associations.append(
                            {
                                "subject": subject_node_id,
                                "predicate": "rel:has_code_snippet",
                                "object": snippet_id,
                            }
                        )
                desired_ids.add(snippet_id)
                continue

            snippet_id = _new_snippet_matkg_id(
                label=label,
                function_name=function_name or "",
                code=code_text,
            )
            things.append(
                _snippet_thing_payload(
                    snippet_id=snippet_id,
                    label=label,
                    function_name=function_name,
                    code_language=code_language,
                    code_snippet=code_text,
                )
            )
            associations.append(
                {
                    "subject": subject_node_id,
                    "predicate": "rel:has_code_snippet",
                    "object": snippet_id,
                }
            )
            splash_snippet = kg_update.splash_create_entity(
                entity_type="CodeSnippet",
                name=label,
                uri=snippet_id,
                properties={
                    "matkg_id": snippet_id,
                    "code_snippet": code_text,
                    "function_name": function_name,
                    "code_language": code_language,
                },
            )
            kg_update.splash_create_link(
                subject_id=subject_splash_id,
                predicate="rel:has_code_snippet",
                object_id=str(splash_snippet["id"]),
            )
            desired_ids.add(snippet_id)

        for stale_id in set(current) - desired_ids:
            unlink_snippet(stale_id)

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

    def _resolve_runtime_json_graph_path(self, path: str) -> Path:
        graph_path = Path(path)
        if not graph_path.is_absolute():
            graph_path = (project_root() / graph_path).resolve()
        else:
            graph_path = graph_path.resolve()
        if self.cfg.graph and graph_path == Path(self.cfg.graph).resolve():
            if not graph_path.exists():
                raise FileNotFoundError(f"JSON graph not found: {path}")
            return graph_path
        return self._resolve_json_graph_path(path)

    async def ask(
        self,
        question: str,
        *,
        messages: Optional[List[ChatMessageInput]] = None,
        session_id: Optional[str] = None,
        graph_source: Optional[str] = None,
        json_graph_path: Optional[str] = None,
        auto_approve: bool = False,
        ) -> ChatResponse:
        original_question = question.strip()
        async with self.lock:
            self._activate_session(session_id)
            if self.pending:
                pending_response = await self._handle_pending_message(original_question, emit=None)
                if pending_response is not None:
                    return pending_response
            self.workflow.update(orchestration_steps=0)
            extraction_response = await self._maybe_report_active_extraction(original_question, emit=None)
            if extraction_response is not None:
                await self._finalize_memory(original_question, extraction_response, effective_question=original_question)
                return extraction_response
            paper_response = await self._maybe_query_active_paper(original_question, emit=None)
            if paper_response is not None:
                await self._finalize_memory(original_question, paper_response, effective_question=original_question)
                return paper_response
            self.workflow.update(
                current_query=original_question,
                current_topic=original_question,
                phase="idle",
                last_route=None,
                approved_action=None,
                post_extraction_sufficient=None,
                orchestration_steps=0,
            )
            prepared = await self._prepare_orchestrated_question(original_question, messages, emit=None)
            if prepared.get("status") == "direct_response":
                response = self._direct_response(
                    answer=str(prepared.get("answer") or ""),
                    reason=str(prepared.get("reason") or ""),
                )
                await self._finalize_memory(original_question, response, effective_question=original_question)
                return response
            if prepared.get("status") == "query_extracted_paper":
                response = await self._maybe_query_active_paper(
                    original_question, emit=None, force=True, decide=False
                )
                if response is not None:
                    await self._finalize_memory(original_question, response, effective_question=original_question)
                    return response
            if prepared.get("status") == "report_extraction":
                response = await self._maybe_report_active_extraction(
                    original_question, emit=None, force=True, decide=False
                )
                if response is not None:
                    await self._finalize_memory(original_question, response, effective_question=original_question)
                    return response
            effective_question = str(prepared.get("question") or original_question)
            if prepared.get("status") == "direct_download_search":
                response = await self._start_direct_download_search(original_question, emit=None)
                effective_question = str(
                    (self.pending or {}).get("effective_question") or effective_question
                )
            else:
                response = await self._ask_locked(
                    effective_question,
                    emit=None,
                    graph_source=graph_source,
                    json_graph_path=json_graph_path,
                )
            self._remember_pending_meta(original_question, effective_question, graph_source, json_graph_path)
            auto_finalized = False
            while auto_approve and response.pending:
                kind = str(response.pending.get("kind") or "")
                candidate_index = 0 if kind == "download" else None
                response = await self._act_locked(
                    "yes", kind, emit=None, candidate_index=candidate_index
                )
                auto_finalized = kind == "extraction"
                self._remember_pending_meta(original_question, effective_question, graph_source, json_graph_path)
            if response.pending is None and not auto_finalized:
                await self._finalize_memory(original_question, response, effective_question=effective_question)
            return response

    async def ask_with_progress(
        self,
        question: str,
        emit: ProgressEmitter,
        *,
        messages: Optional[List[ChatMessageInput]] = None,
        session_id: Optional[str] = None,
        graph_source: Optional[str] = None,
        json_graph_path: Optional[str] = None,
        auto_approve: bool = False,
        ) -> ChatResponse:
        original_question = question.strip()
        async with self.lock:
            self._activate_session(session_id)
            if self.pending:
                pending_response = await self._handle_pending_message(original_question, emit=emit)
                if pending_response is not None:
                    return pending_response
            self.workflow.update(orchestration_steps=0)
            extraction_response = await self._maybe_report_active_extraction(original_question, emit=emit)
            if extraction_response is not None:
                await self._finalize_memory(original_question, extraction_response, effective_question=original_question)
                return extraction_response
            paper_response = await self._maybe_query_active_paper(original_question, emit=emit)
            if paper_response is not None:
                await self._finalize_memory(original_question, paper_response, effective_question=original_question)
                return paper_response
            self.workflow.update(
                current_query=original_question,
                current_topic=original_question,
                phase="idle",
                last_route=None,
                approved_action=None,
                post_extraction_sufficient=None,
                orchestration_steps=0,
            )
            prepared = await self._prepare_orchestrated_question(original_question, messages, emit=emit)
            if prepared.get("status") == "direct_response":
                response = self._direct_response(
                    answer=str(prepared.get("answer") or ""),
                    reason=str(prepared.get("reason") or ""),
                )
                await self._finalize_memory(original_question, response, effective_question=original_question)
                return response
            if prepared.get("status") == "query_extracted_paper":
                response = await self._maybe_query_active_paper(
                    original_question, emit=emit, force=True, decide=False
                )
                if response is not None:
                    await self._finalize_memory(original_question, response, effective_question=original_question)
                    return response
            if prepared.get("status") == "report_extraction":
                response = await self._maybe_report_active_extraction(
                    original_question, emit=emit, force=True, decide=False
                )
                if response is not None:
                    await self._finalize_memory(original_question, response, effective_question=original_question)
                    return response
            effective_question = str(prepared.get("question") or original_question)
            if prepared.get("status") == "direct_download_search":
                response = await self._start_direct_download_search(original_question, emit=emit)
                effective_question = str(
                    (self.pending or {}).get("effective_question") or effective_question
                )
            else:
                response = await self._ask_locked(
                    effective_question,
                    emit=emit,
                    graph_source=graph_source,
                    json_graph_path=json_graph_path,
                )
            self._remember_pending_meta(original_question, effective_question, graph_source, json_graph_path)
            auto_finalized = False
            while auto_approve and response.pending:
                kind = str(response.pending.get("kind") or "")
                candidate_index = 0 if kind == "download" else None
                response = await self._act_locked(
                    "yes", kind, emit=emit, candidate_index=candidate_index
                )
                auto_finalized = kind == "extraction"
                self._remember_pending_meta(original_question, effective_question, graph_source, json_graph_path)
            if response.pending is None and not auto_finalized:
                await self._finalize_memory(original_question, response, effective_question=effective_question)
            return response

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

    def _set_pending(self, pending: Optional[Dict[str, Any]], *, phase: Optional[str] = None) -> None:
        self.pending = pending
        values: Dict[str, Any] = {"pending": pending}
        if phase is not None:
            values["phase"] = phase
        self.workflow.update(**values)

    def _normalize_pending_state(self) -> Optional[Dict[str, Any]]:
        """Migrate legacy/in-memory pending payloads into durable token state."""
        if not isinstance(self.pending, dict):
            return None
        pending = dict(self.pending)
        pending.setdefault("approval_token", uuid.uuid4().hex)
        kind = pending.get("kind")
        values: Dict[str, Any] = {
            "pending": pending,
            "phase": f"awaiting_{kind}_approval",
        }
        if kind == "download":
            values["candidates"] = [
                candidate
                for candidate in (pending.get("candidate_list") or [])
                if isinstance(candidate, dict)
            ]
        elif kind == "extraction":
            downloaded = pending.get("downloaded") or []
            candidate = pending.get("selected_candidate") or {}
            active = dict(self.workflow.data.get("active_paper") or {})
            if downloaded and not active:
                path = Path(str(downloaded[0]))
                active = {
                    "paper_id": str(candidate.get("id") or candidate.get("doi") or path.name),
                    "filename": path.name,
                    "path": str(path),
                    "title": str(candidate.get("title") or path.name),
                    "topic": pending.get("effective_question") or pending.get("original_question") or "",
                    "status": "downloaded",
                }
            values["active_paper"] = active
        self.pending = pending
        self.workflow.update(**values)
        return pending

    async def _orchestrator_decision(
        self,
        user_turn: str,
        emit: Optional[ProgressEmitter],
        *,
        route_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.workflow.snapshot()
        decision = await self.orchestrator.decide(
            user_turn,
            state,
            route_hint,
            [
                "WorkflowOrchestratorAgent",
                "RetrievalAgent",
                "PaperEvidenceAgent",
                "DownloadAgent",
                "EvidenceDebateAgent",
                "ExtractorAgent",
            ],
        )
        steps = int(state.get("orchestration_steps") or 0) + 1
        phase = str(self.workflow.data.get("phase") or "idle")
        self._last_orchestration = {
            "action": decision["action"],
            "agent": decision["agent"],
            "reason": decision["reason"],
            "state": phase,
        }
        self.workflow.update(
            last_route=decision,
            orchestration_steps=steps,
        )
        await self._emit(
            emit,
            "orchestrator_decision",
            f"Orchestrator selected {decision['action']} via {decision['agent']}",
            action=decision["action"],
            agent=decision["agent"],
            reason=decision["reason"],
            state=phase,
        )
        return decision

    async def _maybe_query_active_paper(
        self,
        question: str,
        emit: Optional[ProgressEmitter],
        force: bool = False,
        decide: bool = True,
    ) -> Optional[ChatResponse]:
        state = self.workflow.snapshot()
        if not force and not _paper_reference_followup(question, state):
            return None
        decision = (
            await self._orchestrator_decision(
                question, emit, route_hint="query_extracted_paper"
            )
            if decide
            else dict(self.workflow.data.get("last_route") or {})
        )
        if decision.get("action") != "query_extracted_paper":
            return None
        paper = state.get("active_paper") or {}
        result = await self.paper_evidence.query(
            question,
            str(paper.get("path") or ""),
            str(self.coord.extraction_manifest),
        )
        phase = "paper_answered" if result.get("sufficient") else "paper_insufficient"
        self.workflow.update(phase=phase, last_route=decision)
        if self._last_orchestration:
            self._last_orchestration["state"] = phase
        return self._response(
            "paper_answered" if result.get("sufficient") else "paper_insufficient",
            str(result.get("answer") or "The eligible PDF pages were insufficient."),
            bool(result.get("sufficient")),
            {},
            [{"paper_evidence": result}],
            self.graph_path(),
            publications_override=[],
        )

    async def _maybe_report_active_extraction(
        self,
        question: str,
        emit: Optional[ProgressEmitter],
        force: bool = False,
        decide: bool = True,
    ) -> Optional[ChatResponse]:
        state = self.workflow.snapshot()
        if not force and not _extracted_terms_followup(question, state):
            return None
        decision = (
            await self._orchestrator_decision(question, emit, route_hint="report_extraction")
            if decide
            else dict(self.workflow.data.get("last_route") or {})
        )
        if decision.get("action") != "report_extraction":
            return None
        paper = state.get("active_paper") or {}
        result = summarize_extracted_terms(
            str(self.coord.session_terms),
            str(self.coord.extraction_manifest),
            str(paper.get("filename") or ""),
        )
        phase = "paper_answered" if result.get("sufficient") else "paper_insufficient"
        self.workflow.update(phase=phase, last_route=decision)
        if self._last_orchestration:
            self._last_orchestration["state"] = phase
        return self._response(
            "extraction_reported" if result.get("sufficient") else "paper_insufficient",
            str(result.get("answer") or "No page-grounded extracted terms were found."),
            bool(result.get("sufficient")),
            {},
            [{"extraction_report": result}],
            self.graph_path(),
            publications_override=[],
        )

    async def _handle_pending_message(
        self,
        message: str,
        emit: Optional[ProgressEmitter],
    ) -> Optional[ChatResponse]:
        if not self.pending:
            return None
        if self.pending.get("kind") == "download":
            return await self._handle_pending_download_message(message, emit=emit)
        normalized = _normalize_chat_text(message)
        if re.search(r"\b(yes|approve|extract|run extraction|go ahead|continue)\b", normalized):
            return await self._act_locked("yes", "extraction", emit)
        if re.search(r"\b(no|decline|skip|cancel|do not|don't)\b", normalized):
            return await self._act_locked("no", "extraction", emit)
        await self._orchestrator_decision(message, emit)
        verdict = self.pending.get("verdict") or {}
        return self._response(
            "awaiting_extraction_decision",
            "The downloaded paper is waiting for explicit extraction approval. Say ‘run extraction’ or ‘skip’.",
            False,
            verdict,
            [],
            self.graph_path(),
            pending=self._pending_payload(),
            publications_override=[],
        )

    def _direct_response(self, *, answer: str, reason: str = "") -> ChatResponse:
        self.workflow.update(phase="direct_responded")
        if self._last_orchestration:
            self._last_orchestration["state"] = "direct_responded"
        graph_path = self.graph_path()
        return ChatResponse(
            status="direct_response",
            answer=answer,
            sufficient=False,
            node_ids=[],
            publications=[],
            confidence=1.0,
            rounds=[
                {
                    "routing": {
                        "requires_agents": False,
                        "reason": reason,
                    }
                }
            ],
            graph=graph_payload_from_file(graph_path),
            graph_source_requested=self.runtime.graph_source,
            graph_source_used=self.runtime.graph_source,
            workdir=str(Path(self.cfg.workdir)),
            orchestration=self._last_orchestration,
        )

    def _record_memory(
        self,
        original_question: str,
        response: ChatResponse,
        *,
        effective_question: str,
    ) -> None:
        self.memory.record_turn(
            user_message=original_question,
            effective_question=effective_question,
            answer=response.answer,
            status=response.status,
            sufficient=response.sufficient,
            node_ids=response.node_ids,
            publications=response.publications,
            rounds=[dict(item) for item in response.rounds],
        )

    async def _finalize_memory(
        self,
        original_question: str,
        response: ChatResponse,
        *,
        effective_question: str,
    ) -> None:
        self._record_memory(original_question, response, effective_question=effective_question)
        await self._maybe_compress_memory()

    async def _maybe_compress_memory(self) -> None:
        if not self.memory.needs_compression():
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.memory.compress(lambda prompt: self._chat_completion(prompt, timeout=60)),
            )
        except Exception:
            # Compression is best-effort; never let it break the chat turn.
            pass

    def _remember_pending_meta(
        self,
        original_question: str,
        effective_question: str,
        graph_source: Optional[str],
        json_graph_path: Optional[str],
    ) -> None:
        if not self.pending:
            return
        self.pending.setdefault("original_question", original_question)
        self.pending.setdefault("effective_question", effective_question)
        self.pending.setdefault("graph_source", graph_source)
        self.pending.setdefault("json_graph_path", json_graph_path)
        self._set_pending(self.pending)

    async def reset_session_context(self, session_id: Optional[str] = None) -> SessionResetResponse:
        async with self.lock:
            self._activate_session(session_id)
            self.memory.clear()
            self.workflow.clear()
            self.pending = None
            self._last_orchestration = None
            return SessionResetResponse(
                status="reset",
                session_memory=str(self.memory.path),
                session_memory_has_context=self.memory.has_context(),
                workflow_state=str(self.workflow.path),
                workflow_phase=str(self.workflow.data.get("phase") or "idle"),
            )

    async def delete_session_context(self, session_id: str) -> Dict[str, str]:
        key = self._session_key(session_id)
        if key == "__legacy__":
            raise ValueError("Legacy session cannot be deleted")
        async with self.lock:
            if key == self._active_session_key:
                self._activate_session(None)
            self._session_contexts.pop(key, None)
            context_dir = self.coord.workdir / "chat_sessions" / key
            if context_dir.exists():
                shutil.rmtree(context_dir)
            return {"status": "deleted", "session_id": key}

    async def _prepare_chat_question(
        self,
        question: str,
        messages: Optional[List[ChatMessageInput]],
    ) -> Dict[str, str]:
        history = _history_payload(messages)
        route = await self._judge_agent_requirement(question, history)
        if not route.get("requires_agents", True):
            answer = await self._generate_direct_response(question, history)
            return {
                "status": "direct_response",
                "question": question,
                "answer": answer,
                "reason": str(route.get("reason") or "LLM router determined agents are not needed."),
            }
        if not _needs_history_rewrite(question, history) and not (
            self.memory.has_context() and _looks_contextual_followup(question)
        ):
            return {"status": "kg_question", "question": question}
        rewritten = await self._rewrite_standalone_question(question, history)
        return {"status": "kg_question", "question": rewritten or question}

    async def _prepare_orchestrated_question(
        self,
        question: str,
        messages: Optional[List[ChatMessageInput]],
        emit: Optional[ProgressEmitter],
    ) -> Dict[str, str]:
        """Let the orchestrator classify a fresh turn, then resolve references.

        ``_judge_agent_requirement`` is no longer part of production routing. A
        monkeypatched instance method is honored as a compatibility hint for
        older integrations while they migrate.
        """
        history = _history_payload(messages)
        route_hint: Optional[str] = None
        if "_judge_agent_requirement" in self.__dict__:
            legacy = await self._judge_agent_requirement(question, history)
            route_hint = "retrieve_kg" if legacy.get("requires_agents", True) else "direct_response"
        decision = await self._orchestrator_decision(question, emit, route_hint=route_hint)
        action_name = str(decision.get("action") or "stop_insufficient")
        if action_name == "direct_response":
            answer = await self._generate_direct_response(question, history)
            return {
                "status": "direct_response",
                "question": question,
                "answer": answer,
                "reason": str(decision.get("reason") or "Direct response selected."),
            }
        if action_name == "query_extracted_paper":
            return {"status": "query_extracted_paper", "question": question}
        if action_name == "report_extraction":
            return {"status": "report_extraction", "question": question}
        if action_name == "search_candidates":
            return {"status": "direct_download_search", "question": question}
        if action_name != "retrieve_kg":
            return {
                "status": "direct_response",
                "question": question,
                "answer": str(decision.get("reason") or "The workflow stopped safely."),
                "reason": str(decision.get("reason") or "Invalid initial transition."),
            }
        if not _needs_history_rewrite(question, history) and not (
            self.memory.has_context() and _looks_contextual_followup(question)
        ):
            return {"status": "kg_question", "question": question}
        rewritten = await self._rewrite_standalone_question(question, history)
        return {"status": "kg_question", "question": rewritten or question}

    async def _start_direct_download_search(
        self,
        message: str,
        emit: Optional[ProgressEmitter],
    ) -> ChatResponse:
        active_paper = self.workflow.data.get("active_paper") or {}
        fallback_topic = str(active_paper.get("topic") or "") if isinstance(active_paper, dict) else ""
        query = _direct_download_query(message, fallback_topic)
        if not query:
            self.workflow.update(phase="idle")
            return self._response(
                "download_query_needed",
                "Tell me the paper title, arXiv ID, DOI, or topic you want me to search for.",
                False,
                {},
                [],
                self.graph_path(),
                publications_override=[],
            )

        search_action = getattr(self.download, "search_candidates", None)
        if not callable(search_action):
            self.workflow.update(phase="stop_insufficient")
            return self._response(
                "agent_unavailable",
                "DownloadAgent does not provide paper search; stopped safely.",
                False,
                {},
                [],
                self.graph_path(),
                publications_override=[],
            )

        await self._emit(
            emit,
            "candidate_search_started",
            "Literature scout searching arXiv and OpenAlex",
            round=1,
            preflight=1,
            query=query,
            missing_topics=[],
        )
        try:
            search = await search_action(
                query,
                missing_topics=[],
                candidate_pool=self.cfg.candidate_pool,
            )
        except Exception as exc:
            self.workflow.update(phase="stop_insufficient")
            return self._response(
                "candidate_search_error",
                f"Paper search failed safely: {exc}",
                False,
                {},
                [],
                self.graph_path(),
                publications_override=[],
            )

        raw_candidates = [
            candidate for candidate in (search.get("candidates") or [])
            if isinstance(candidate, dict)
        ]
        # DownloadAgent already ranks by relevance. Stable partition keeps that
        # ordering inside each repository group while preferring arXiv copies.
        candidates = (
            [candidate for candidate in raw_candidates if _is_arxiv_candidate(candidate)]
            + [candidate for candidate in raw_candidates if not _is_arxiv_candidate(candidate)]
        )[:5]
        titles = [
            str(candidate.get("title") or candidate.get("doi") or candidate.get("id") or "Untitled")
            for candidate in candidates
        ]
        await self._emit(
            emit,
            "candidate_search_result",
            f"Literature scout found {len(candidates)} candidate(s)",
            round=1,
            preflight=1,
            count=len(candidates),
            candidate_titles=titles,
            scores=[float(c.get("score") or c.get("_score") or 0.0) for c in candidates],
        )
        if not candidates:
            self.workflow.update(phase="idle", candidates=[], unavailable_candidate_indices=[])
            return self._response(
                "no_download_candidates",
                f"I could not find a reliable open-access paper matching **{query}**.",
                False,
                {},
                [{"candidate_search": search}],
                self.graph_path(),
                publications_override=[],
            )

        pending = {
            "kind": "download",
            "approval_token": uuid.uuid4().hex,
            "verdict": {},
            "missing_topics": [],
            "candidates": candidates,
            "candidate_list": candidates,
            "selected_candidate": candidates[0],
            "alternatives": candidates[1:],
            "reason": "Direct paper download request",
            "round_no": 1,
            "original_question": message,
            "effective_question": query,
            "direct_download": True,
        }
        self.workflow.update(
            phase="candidate_selected",
            current_query=query,
            current_topic=query,
            candidates=candidates,
            unavailable_candidate_indices=[],
        )
        self._set_pending(pending, phase="awaiting_download_approval")
        await self._orchestrator_decision(query, emit)
        await self._emit(
            emit,
            "awaiting_download_decision",
            "Waiting for you to name a candidate paper in chat",
            round=1,
            candidate_titles=titles,
        )
        return self._response(
            "awaiting_download_decision",
            DIRECT_DOWNLOAD_PAPERS_INTRO,
            False,
            {},
            [{"candidate_search": search}],
            self.graph_path(),
            pending=self._pending_payload(),
            publications_override=[],
        )

    async def _judge_agent_requirement(
        self,
        question: str,
        history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        prompt = (
            "You route messages for a FAIR2WISE materials-science assistant.\n"
            "Decide whether the user message needs the retrieval/download/extraction "
            "agent workflow over a materials knowledge graph and scientific papers.\n\n"
            "Return ONLY JSON with this schema:\n"
            '{"requires_agents": true|false, "reason": string}\n\n'
            "Set requires_agents=false for greetings, tests, thanks, meta-chat, UI/help "
            "questions, or general conversation that can be answered without KG/paper evidence.\n"
            "Set requires_agents=true for materials-science questions, requests for citations, "
            "papers, evidence, code snippets from the KG, or follow-ups that need prior "
            "KG-grounded context.\n\n"
            f"{self.memory.memory_section()}\n"
            f"HISTORY:\n{json.dumps(history[-MAX_HISTORY_MESSAGES:], ensure_ascii=False)}\n\n"
            f"USER_MESSAGE:\n{question}"
        )

        def run() -> Dict[str, Any]:
            raw = self._chat_completion(prompt, timeout=60)
            obj = _parse_json_object(raw)
            if "requires_agents" not in obj:
                return {"requires_agents": True, "reason": "Router returned no usable JSON."}
            return {
                "requires_agents": _coerce_bool(obj.get("requires_agents")),
                "reason": str(obj.get("reason") or ""),
            }

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, run)
        except Exception as exc:
            return {"requires_agents": True, "reason": f"Router failed: {exc}"}

    async def _generate_direct_response(
        self,
        question: str,
        history: List[Dict[str, str]],
    ) -> str:
        prompt = (
            "You are FAIR2WISE, a concise materials-science assistant. The router has "
            "determined that the retrieval/download/extraction agents are not needed. "
            "Answer conversationally and briefly. Do not claim to have searched the KG "
            "or papers.\n\n"
            f"{self.memory.memory_section()}\n"
            f"HISTORY:\n{json.dumps(history[-MAX_HISTORY_MESSAGES:], ensure_ascii=False)}\n\n"
            f"USER_MESSAGE:\n{question}"
        )

        def run() -> str:
            return str(self._chat_completion(prompt, timeout=60) or "").strip()

        try:
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, run)
        except Exception:
            answer = "I'm here. Ask a materials question when you want me to use the knowledge graph."
        return answer or "I'm here. Ask a materials question when you want me to use the knowledge graph."

    def _chat_completion(self, prompt: str, *, timeout: int) -> str:
        from app.modules.term_extractor.clients import make_chat_client

        cli = make_chat_client(
            backend=self.runtime.backend,
            model=self._active_model(),
            cborg_base=os.environ.get("CBORG_BASE_URL"),
            cborg_api_key=os.environ.get("CBORG_API_KEY"),
        )
        return str(cli.chat(prompt, temperature=0.0, timeout=timeout) or "")

    async def _rewrite_standalone_question(
        self,
        question: str,
        history: List[Dict[str, str]],
    ) -> str:
        prompt = (
            "Rewrite the current user turn into a standalone materials-science question "
            "using the conversation history. Do not answer. Preserve technical terms. "
            "Return only the rewritten question.\n\n"
            f"{self.memory.memory_section()}\n"
            f"HISTORY:\n{json.dumps(history[-MAX_HISTORY_MESSAGES:], ensure_ascii=False)}\n\n"
            f"CURRENT_USER_TURN:\n{question}"
        )

        def run() -> str:
            return str(self._chat_completion(prompt, timeout=60) or "").strip()

        try:
            loop = asyncio.get_event_loop()
            rewritten = await loop.run_in_executor(None, run)
        except Exception:
            return question
        rewritten = re.sub(r"^['\"]|['\"]$", "", rewritten.strip())
        return rewritten if rewritten else question

    async def _retired_deterministic_locked(
        self,
        question: str,
        emit: Optional[ProgressEmitter],
        *,
        graph_source: Optional[str],
        json_graph_path: Optional[str],
    ) -> ChatResponse:
        # Legacy workflow mode names are accepted by settings/configuration, but
        # both resolve to this one canonical orchestrated implementation.
        return await self._ask_locked(
            question,
            emit,
            graph_source=graph_source,
            json_graph_path=json_graph_path,
        )

        rounds: List[Dict[str, Any]] = []
        last_verdict: Dict[str, Any] = {}
        effective_graph_source = (graph_source or self.runtime.graph_source).lower()

        if effective_graph_source == "json":
            selected_path = json_graph_path or self.runtime.json_graph_path
            if not selected_path:
                raise ValueError("JSON graph mode requires a graph file")
            active_graph_path = self._resolve_runtime_json_graph_path(selected_path)
            active_graph_source = "json"
        else:
            active_graph_path = self._session_graph_path()
            active_graph_source = "splash"

        try:
            await self.retrieval.reload_kg(str(active_graph_path), graph_source=active_graph_source)
        except Exception as exc:
            self.workflow.update(phase="retrieval_error")
            return self._response(
                "retrieval_error",
                f"Retrieval agent could not load the active graph: {exc}",
                False,
                {},
                [],
                active_graph_path,
            )

        for round_no in range(1, self.cfg.max_rounds + 1):
            await self._emit(
                emit,
                "retrieval_started",
                "Retrieval agent searching the KG",
                round=round_no,
            )
            try:
                verdict = await self.retrieval.query(question)
            except Exception as exc:
                self.workflow.update(phase="retrieval_error")
                return self._response(
                    "retrieval_error",
                    f"Retrieval failed safely: {exc}",
                    False,
                    {},
                    rounds,
                    active_graph_path,
                )
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
                self.workflow.update(phase="retrieval_error")
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
            self.coord._record_full_extraction(pending_pdfs, ext)
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
            active_graph_path = self._resolve_runtime_json_graph_path(selected_path)
            active_graph_source = "json"
        else:
            active_graph_path = self._session_graph_path()
            active_graph_source = "splash"

        try:
            await self.retrieval.reload_kg(str(active_graph_path), graph_source=active_graph_source)
        except Exception as exc:
            self.workflow.update(phase="retrieval_error")
            return self._response(
                "retrieval_error",
                f"Retrieval agent could not load the active graph: {exc}",
                False,
                {},
                [],
                active_graph_path,
            )

        for round_no in range(1, self.cfg.max_rounds + 1):
            await self._emit(
                emit,
                "retrieval_started",
                "Retrieval agent searching the KG",
                round=round_no,
            )
            try:
                verdict = await self.retrieval.query(question)
            except Exception as exc:
                self.workflow.update(phase="retrieval_error")
                return self._response(
                    "retrieval_error",
                    f"Retrieval failed safely: {exc}",
                    False,
                    {},
                    rounds,
                    active_graph_path,
                )
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
                self.workflow.update(phase="retrieval_error")
                if self._last_orchestration:
                    self._last_orchestration["state"] = "retrieval_error"
                return self._response(
                    "retrieval_error",
                    f"Retrieval failed: {verdict.get('error') or verdict.get('status')}",
                    False,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            if verdict.get("sufficient"):
                self.workflow.update(
                    phase="answered",
                    post_extraction_sufficient=True
                    if self.workflow.data.get("phase") == "paper_extracted"
                    else self.workflow.data.get("post_extraction_sufficient"),
                )
                if self._last_orchestration:
                    self._last_orchestration["state"] = "answered"
                return self._response(
                    "answered",
                    str(verdict.get("answer") or ""),
                    True,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            if active_graph_source == "json":
                self.workflow.update(phase="stop_insufficient")
                await self._orchestrator_decision(question, emit)
                return self._response(
                    "insufficient_json_graph",
                    "The selected JSON graph did not contain enough direct evidence to answer this question.",
                    False,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            missing = verdict.get("missing_topics") or [question]
            self.workflow.update(
                phase="retrieval_insufficient",
                current_query=question,
                round_no=round_no,
            )
            candidates: List[Dict[str, Any]] = []
            debate_summary: Dict[str, Any] = {}
            search_query = question
            for preflight_no in range(1, 3):
                search_decision = await self._orchestrator_decision(question, emit)
                if search_decision.get("action") != "search_candidates":
                    return self._response(
                        "stop_insufficient",
                        str(search_decision.get("reason") or "Candidate search was not allowed."),
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                await self._emit(
                    emit,
                    "candidate_search_started",
                    "Literature scout searching OpenAlex abstracts",
                    round=round_no,
                    preflight=preflight_no,
                    query=search_query,
                    missing_topics=missing,
                )
                search_action = getattr(self.download, "search_candidates", None)
                if not callable(search_action):
                    self.workflow.update(phase="stop_insufficient")
                    return self._response(
                        "agent_unavailable",
                        "DownloadAgent does not provide candidate metadata search; stopped safely.",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                try:
                    search = await search_action(
                        search_query,
                        missing_topics=missing,
                        candidate_pool=self.cfg.candidate_pool,
                    )
                except Exception as exc:
                    self.workflow.update(phase="stop_insufficient")
                    return self._response(
                        "candidate_search_error",
                        f"Candidate metadata search failed safely: {exc}",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                candidates = search.get("candidates") or []
                self.workflow.update(
                    phase="candidates_found" if candidates else "stop_insufficient",
                    candidates=candidates,
                    unavailable_candidate_indices=[],
                )
                round_info["candidate_search"] = search
                candidate_titles = [
                    str(c.get("title") or c.get("doi") or c.get("id") or "Untitled")
                    for c in candidates[:5]
                ]
                if not candidates:
                    await self._orchestrator_decision(question, emit)
                    return self._response(
                        "stop_insufficient",
                        "No usable open-access paper candidates were found.",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )

                debate_decision = await self._orchestrator_decision(question, emit)
                if debate_decision.get("action") != "consult_debate":
                    return self._response(
                        "stop_insufficient",
                        str(debate_decision.get("reason") or "Candidate ranking was not allowed."),
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                await self._emit(
                    emit,
                    "candidate_search_result",
                    f"Literature scout found {len(candidates)} candidate(s)",
                    round=round_no,
                    preflight=preflight_no,
                    count=len(candidates),
                    candidate_titles=candidate_titles,
                    scores=[float(c.get("score") or c.get("_score") or 0.0) for c in candidates[:5]],
                )

                await self._emit(
                    emit,
                    "debate_started",
                    "Evidence debate judging candidate value",
                    round=round_no,
                    preflight=preflight_no,
                )
                debate_action = getattr(self.debate, "decide", None)
                if not callable(debate_action):
                    self.workflow.update(phase="stop_insufficient")
                    return self._response(
                        "agent_unavailable",
                        "EvidenceDebateAgent is unavailable; candidate selection stopped safely.",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                try:
                    debate_summary = await debate_action(question, verdict, candidates, round_no)
                except Exception as exc:
                    self.workflow.update(phase="stop_insufficient")
                    return self._response(
                        "debate_error",
                        f"Candidate ranking failed safely: {exc}",
                        False,
                        verdict,
                        rounds,
                        self.graph_path(),
                    )
                round_info["debate"] = debate_summary
                await self._emit(
                    emit,
                    "debate_result",
                    str(debate_summary.get("reason") or "Evidence debate finished"),
                    round=round_no,
                    preflight=preflight_no,
                    **debate_summary,
                )

                action_name = str(debate_summary.get("selected_action") or "")
                if action_name == "refine_search" and debate_summary.get("refined_query") and preflight_no == 1:
                    await self._emit(
                        emit,
                        "action_selected",
                        "Evidence debate requested a narrower abstract search",
                        round=round_no,
                        selected_action=action_name,
                        reason=debate_summary.get("reason"),
                        candidate_titles=debate_summary.get("candidate_titles") or [],
                    )
                    search_query = str(debate_summary["refined_query"])
                    self.workflow.update(phase="retrieval_insufficient")
                    continue
                break

            action_name = str(debate_summary.get("selected_action") or "")
            await self._emit(
                emit,
                "action_selected",
                f"Evidence debate selected {action_name or 'stop_insufficient'}",
                round=round_no,
                selected_action=action_name or "stop_insufficient",
                reason=debate_summary.get("reason"),
                candidate_titles=debate_summary.get("candidate_titles") or [],
            )

            if action_name == "answer_from_kg" and verdict.get("sufficient"):
                return self._response(
                    "answered",
                    str(verdict.get("answer") or ""),
                    True,
                    verdict,
                    rounds,
                    active_graph_path,
                )

            if action_name != "download_selected":
                self.workflow.update(phase="stop_insufficient")
                await self._orchestrator_decision(question, emit)
                return self._response(
                    "stop_insufficient",
                    str(debate_summary.get("reason") or "Candidate evidence was too weak."),
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
                )

            best, alternatives = self._pick_candidate(candidates, debate_summary)
            if best is None:
                self.workflow.update(phase="stop_insufficient")
                return self._response(
                    "stop_insufficient",
                    str(
                        debate_summary.get("reason")
                        or "No candidate papers were found to fill the evidence gap."
                    ),
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
                )

            # Interactive gating: pause and ask the user before downloading.
            paper_candidates = [c for c in [best, *alternatives] if isinstance(c, dict)]
            candidate_index = next(
                (index for index, candidate in enumerate(candidates) if candidate is best),
                0,
            )
            approval_token = uuid.uuid4().hex
            pending = {
                "kind": "download",
                "approval_token": approval_token,
                "verdict": verdict,
                "missing_topics": list(missing),
                "candidates": candidates,
                "candidate_list": paper_candidates,
                "selected_candidate": best,
                "alternatives": alternatives,
                "reason": debate_summary.get("reason") or "",
                "round_no": round_no,
                "active_graph_source": active_graph_source,
            }
            self.workflow.update(
                phase="candidate_selected",
                candidates=paper_candidates,
            )
            self._set_pending(pending, phase="awaiting_download_approval")
            await self._orchestrator_decision(question, emit)
            pending_payload = self._pending_payload()
            await self._emit(
                emit,
                "awaiting_download_decision",
                "Waiting for you to name a candidate paper in chat",
                round=round_no,
                candidate_titles=[c.get("title") for c in paper_candidates if isinstance(c, dict)],
            )
            return self._response(
                "awaiting_download_decision",
                CANDIDATE_PAPERS_INTRO,
                False,
                verdict,
                rounds,
                self.graph_path(),
                pending=pending_payload,
                publications_override=[],
            )

        return self._response(
            "max_rounds",
            f"Reached max rounds ({self.cfg.max_rounds}) without sufficient evidence.",
            False,
            last_verdict,
            rounds,
            self.graph_path(),
        )

    def _pick_candidate(
        self,
        candidates: List[Dict[str, Any]],
        debate_summary: Dict[str, Any],
    ) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Best candidate (debate pick, else top-ranked) plus a few alternatives."""
        if not candidates:
            return None, []
        selected = _select_candidates_by_indices(candidates, debate_summary.get("candidate_indices"))
        best = selected[0] if selected else candidates[0]
        alternatives = [c for c in candidates if c is not best][:3]
        return best, alternatives

    async def _extract_rebuild_reload(
        self,
        *,
        emit: Optional[ProgressEmitter],
        question: str,
        missing: List[str],
        round_no: int,
        round_info: Dict[str, Any],
        verdict: Dict[str, Any],
        rounds: List[Dict[str, Any]],
        approved_pdfs: List[Path],
    ) -> Optional[ChatResponse]:
        """Stage -> extract -> rebuild -> reload. Returns ChatResponse only on failure/no-op."""
        round_pdf_dir, pending_pdfs = self.coord._stage_unprocessed_pdfs(
            round_no,
            source_pdfs=approved_pdfs,
        )
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

        targeted = self.runtime.extraction_mode == "targeted"
        await self._emit(
            emit,
            "extraction_started",
            "Extractor agent reading targeted pages from approved PDFs"
            if targeted
            else "Extractor agent reading approved PDFs",
            round=round_no,
            pdfs=[p.name for p in pending_pdfs],
            mode=self.runtime.extraction_mode,
            max_pages=self.runtime.targeted_max_pages if targeted else None,
        )
        try:
            if targeted:
                extraction_action = getattr(self.extractor, "extract_targeted", None)
                if not callable(extraction_action):
                    raise RuntimeError("ExtractorAgent does not provide targeted extraction")
                ext = await extraction_action(
                    str(round_pdf_dir),
                    str(self.coord.session_terms),
                    question,
                    list(missing),
                    self.runtime.targeted_max_pages,
                )
            else:
                extraction_action = getattr(self.extractor, "extract", None)
                if not callable(extraction_action):
                    raise RuntimeError("ExtractorAgent does not provide full extraction")
                ext = await extraction_action(str(round_pdf_dir), str(self.coord.session_terms))
        except Exception as exc:
            return self._response(
                "extraction_error",
                f"Extraction failed safely: {exc}",
                False,
                verdict,
                rounds,
                self.graph_path(),
            )
        round_info["extraction"] = ext
        if not isinstance(ext, dict) or ext.get("status") == "error":
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
            mode=self.runtime.extraction_mode,
            term_count=int(ext.get("unique_terms") or 0),
            processed_files=int(ext.get("processed_files") or 0),
            processed_pages_total=int(ext.get("processed_pages_total") or 0),
            processed_pages_with_terms=int(ext.get("processed_pages_with_terms") or 0),
        )
        if targeted:
            self.coord._record_partial_extraction(
                query=question,
                missing_topics=list(missing),
                pdfs=pending_pdfs,
                result=ext,
            )
        else:
            self.coord._record_full_extraction(pending_pdfs, ext)
            self.coord._mark_processed_pdfs(pending_pdfs)

        await self._emit(
            emit,
            "kg_rebuild_started",
            "KG builder updating session graph",
            round=round_no,
        )
        try:
            kg = kg_update.rebuild_kg(str(self.coord.session_terms), str(self.coord.session_kg))
        except Exception as exc:
            return self._response(
                "kg_rebuild_error",
                f"KG rebuild failed safely: {exc}",
                False,
                verdict,
                rounds,
                self.graph_path(),
            )
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
            try:
                splash = kg_update.splash_reimport(
                    str(self.coord.session_kg),
                    splash_repo=self.cfg.splash_repo,
                    allow_wipe=self.cfg.allow_splash_wipe,
                )
            except Exception as exc:
                return self._response(
                    "splash_error",
                    f"Splash reimport failed safely: {exc}",
                    False,
                    verdict,
                    rounds,
                    self.graph_path(),
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
        try:
            reload_res = await self.retrieval.reload_kg(str(self.coord.session_kg))
        except Exception as exc:
            return self._response(
                "retrieval_error",
                f"Updated KG reload failed safely: {exc}",
                False,
                verdict,
                rounds,
                self.graph_path(),
            )
        round_info["reload"] = reload_res
        await self._emit(
            emit,
            "reload_result",
            f"Reloaded KG with {reload_res.get('nodes', 0)} node(s)",
            round=round_no,
            node_count=int(reload_res.get("nodes") or 0),
            status=str(reload_res.get("status") or ""),
        )
        return None

    # ------------------------------------------------------------------
    # Interactive resume (agentic gating)
    # ------------------------------------------------------------------
    def _pending_candidate_list(self) -> List[Dict[str, Any]]:
        pending = self.pending or {}
        candidates = [c for c in (pending.get("candidate_list") or []) if isinstance(c, dict)]
        if candidates:
            return candidates
        best = pending.get("selected_candidate")
        alternatives = pending.get("alternatives") or []
        return [c for c in [best, *alternatives] if isinstance(c, dict)]

    def _heuristic_pending_download_intent(
        self,
        message: str,
        candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        normalized = _normalize_chat_text(message)
        wants_download = bool(re.search(r"\b(download|fetch|get|take|choose|select|use|grab)\b", normalized))
        declines = bool(
            re.search(
                r"\b(don't|do not|no download|none|neither|decline|cancel|skip all|skip download)\b",
                normalized,
            )
        )
        if declines and not re.search(r"\bbut\b.*\bdownload\b", normalized):
            return {"action": "decline", "candidate_index": None}

        failed = {int(i) for i in ((self.pending or {}).get("failed_candidate_indices") or [])}
        if normalized in {"yes", "approve", "approved", "go ahead", "continue"}:
            for index in range(len(candidates)):
                if index not in failed:
                    return {"action": "download", "candidate_index": index}
        if wants_download and re.search(r"\b(another|next|different)\b", normalized):
            for index in range(len(candidates)):
                if index not in failed:
                    return {"action": "download", "candidate_index": index}

        number_match = re.search(r"\b(?:candidate|paper|option)\s*#?\s*([1-9]\d*)\b", normalized)
        if wants_download and number_match:
            index = int(number_match.group(1)) - 1
            if 0 <= index < len(candidates):
                return {"action": "download", "candidate_index": index}

        recommended_index = next(
            (index for index in range(len(candidates)) if index not in failed),
            0,
        )
        ordinals = (
            ("first", 0),
            ("recommended", recommended_index),
            ("second", 1),
            ("third", 2),
            ("fourth", 3),
        )
        if wants_download:
            for word, index in ordinals:
                if re.search(rf"\b{word}\b", normalized) and index < len(candidates):
                    return {"action": "download", "candidate_index": index}

        if wants_download and "arxiv" in normalized:
            for index, candidate in enumerate(candidates):
                repository = str(candidate.get("repository") or "").lower()
                urls = " ".join(str(url) for url in (candidate.get("pdf_urls") or [])).lower()
                doi = str(candidate.get("doi") or "").lower()
                if "arxiv" in repository or "arxiv.org" in urls or "10.48550/arxiv" in doi:
                    return {"action": "download", "candidate_index": index}

        if wants_download:
            message_tokens = set(normalized.split()) - {
                "download", "fetch", "get", "take", "choose", "select", "use", "grab",
                "paper", "candidate", "option", "the", "a", "an", "please",
            }
            best_index: Optional[int] = None
            best_score = 0.0
            for index, candidate in enumerate(candidates):
                title_tokens = set(_normalize_chat_text(str(candidate.get("title") or "")).split())
                if not title_tokens:
                    continue
                score = len(message_tokens & title_tokens) / max(1, len(message_tokens))
                if score > best_score:
                    best_index, best_score = index, score
            if best_index is not None and best_score >= 0.5:
                return {"action": "download", "candidate_index": best_index}
            if len(candidates) == 1:
                return {"action": "download", "candidate_index": 0}
        return None

    async def _interpret_pending_download_message(self, message: str) -> Dict[str, Any]:
        candidates = self._pending_candidate_list()
        heuristic = self._heuristic_pending_download_intent(message, candidates)
        if heuristic is not None:
            return heuristic

        listing = [
            {
                "index": index,
                "title": candidate.get("title"),
                "doi": candidate.get("doi"),
                "repository": candidate.get("repository"),
                "unavailable": index in {
                    int(i) for i in ((self.pending or {}).get("failed_candidate_indices") or [])
                },
            }
            for index, candidate in enumerate(candidates)
        ]
        prompt = (
            "Interpret a user's reply to a candidate-paper list. Match meaning, title fragments, "
            "ordinals, DOI, and repository names. Return ONLY JSON:\n"
            '{"action":"download|decline|clarify|new_question","candidate_index":integer|null,"reason":string}\n\n'
            "Use download when the user asks to fetch one listed paper. Use decline when they refuse "
            "all downloads. Use clarify when download intent is clear but paper identity is ambiguous. "
            "Use new_question only when the message is unrelated to this candidate decision. Never "
            "select a candidate marked unavailable if another candidate matches.\n\n"
            f"{self.memory.memory_section()}\n"
            f"CANDIDATES:\n{json.dumps(listing, ensure_ascii=False)}\n\n"
            f"USER_MESSAGE:\n{message}"
        )

        def run() -> Dict[str, Any]:
            return _parse_json_object(self._chat_completion(prompt, timeout=60))

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run)
        except Exception:
            return {"action": "clarify", "candidate_index": None}
        action = str(result.get("action") or "clarify").strip().lower()
        if action not in {"download", "decline", "clarify", "new_question"}:
            action = "clarify"
        index = result.get("candidate_index")
        try:
            index = int(index) if index is not None else None
        except (TypeError, ValueError):
            index = None
        if action == "download" and (index is None or not 0 <= index < len(candidates)):
            action = "clarify"
            index = None
        return {"action": action, "candidate_index": index}

    async def _handle_pending_download_message(
        self,
        message: str,
        emit: Optional[ProgressEmitter],
    ) -> Optional[ChatResponse]:
        intent = await self._interpret_pending_download_message(message)
        action_name = intent.get("action")
        if action_name == "decline":
            return await self._act_locked("no", "download", emit)
        if action_name == "download":
            return await self._act_locked(
                "yes",
                "download",
                emit,
                candidate_index=int(intent["candidate_index"]),
            )

        verdict = (self.pending or {}).get("verdict") or {}
        return self._response(
            "awaiting_download_decision",
            "Tell me which candidate to download by title, number, DOI, or repository; "
            "or say that you do not want any paper downloaded.",
            False,
            verdict,
            [],
            self.graph_path(),
            pending=self._pending_payload(),
            publications_override=[],
        )

    def _no_pending_response(self) -> ChatResponse:
        return self._response(
            "no_pending_action",
            "There is no pending decision to act on. Ask a new question.",
            False,
            {},
            [],
            self.graph_path(),
        )

    async def act(
        self,
        decision: str,
        kind: Optional[str] = None,
        *,
        candidate_index: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> ChatResponse:
        async with self.lock:
            self._activate_session(session_id)
            return await self._act_locked(decision, kind, emit=None, candidate_index=candidate_index)

    async def act_with_progress(
        self,
        decision: str,
        emit: ProgressEmitter,
        kind: Optional[str] = None,
        *,
        candidate_index: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> ChatResponse:
        async with self.lock:
            self._activate_session(session_id)
            return await self._act_locked(decision, kind, emit=emit, candidate_index=candidate_index)

    async def _act_locked(
        self,
        decision: str,
        kind: Optional[str],
        emit: Optional[ProgressEmitter],
        *,
        candidate_index: Optional[int] = None,
    ) -> ChatResponse:
        pending = self._normalize_pending_state()
        if not pending:
            return self._no_pending_response()
        pending_kind = pending.get("kind")
        if kind is not None and kind != pending_kind:
            raise ValueError(f"Pending action is {pending_kind}, not {kind}")
        verdict = pending.get("verdict") or {}
        original_question = pending.get("original_question") or ""
        effective_question = pending.get("effective_question") or original_question

        if decision == "no":
            self._set_pending(None, phase="stopped_by_user")
            self.workflow.update(approved_action=None)
            if pending_kind == "download":
                message = (
                    "Okay - I will not download a paper. The current knowledge graph does not "
                    "have enough direct evidence to answer this question."
                )
                publications_override: Optional[List[Dict[str, Any]]] = None
            else:
                message = (
                    "Okay - I downloaded the paper but will not run extraction. Ask again if "
                    "you would like me to extract from it."
                )
                publications_override = []
            response = self._response(
                "stopped_by_user",
                message,
                False,
                verdict,
                [],
                self.graph_path(),
                publications_override=publications_override,
            )
            await self._finalize_memory(original_question, response, effective_question=effective_question)
            return response

        if pending_kind == "download":
            if decision != "yes":
                raise ValueError("Download actions require decision=yes with a candidate_index")
            candidate_list = [c for c in (pending.get("candidate_list") or []) if isinstance(c, dict)]
            if not candidate_list:
                best = pending.get("selected_candidate")
                alternatives = pending.get("alternatives") or []
                candidate_list = [c for c in [best, *alternatives] if isinstance(c, dict)]
            if candidate_index is None:
                raise ValueError("candidate_index is required to download a paper")
            if candidate_index < 0 or candidate_index >= len(candidate_list):
                raise ValueError("candidate_index is out of range")
            unavailable = {
                int(index) for index in self.workflow.data.get("unavailable_candidate_indices") or []
            }
            if candidate_index in unavailable:
                raise ValueError("candidate_index is unavailable")
            self.workflow.update(
                approved_action={
                    "kind": "download",
                    "approval_token": pending.get("approval_token"),
                    "candidate_index": candidate_index,
                }
            )
            approved = await self._orchestrator_decision(effective_question, emit)
            if approved.get("action") != "download_selected":
                raise ValueError("Download approval did not match the pending action")
            pending = {**pending, "selected_candidate": candidate_list[candidate_index]}
            self.workflow.update(approved_action=None, phase="downloading")
            return await self._resume_download(pending, emit)
        self.workflow.update(
            approved_action={
                "kind": "extraction",
                "approval_token": pending.get("approval_token"),
            }
        )
        approved = await self._orchestrator_decision(effective_question, emit)
        if approved.get("action") != "extract_selected":
            raise ValueError("Extraction approval did not match the pending action")
        self.workflow.update(approved_action=None, phase="extracting")
        return await self._resume_extraction(pending, emit)

    async def _resume_download(
        self,
        pending: Dict[str, Any],
        emit: Optional[ProgressEmitter],
    ) -> ChatResponse:
        verdict = pending.get("verdict") or {}
        best = pending.get("selected_candidate")
        missing = pending.get("missing_topics") or []
        question = pending.get("effective_question") or pending.get("original_question") or ""
        original_question = pending.get("original_question") or question
        round_no = int(pending.get("round_no") or 1)
        rounds: List[Dict[str, Any]] = [{"round": round_no, "retrieval": verdict}]

        await self._emit(
            emit,
            "download_started",
            "Download agent fetching the approved paper",
            round=round_no,
            candidate_titles=[best.get("title")] if isinstance(best, dict) else [],
        )
        download_action = getattr(self.download, "download_selected", None)
        if not callable(download_action):
            self._set_pending(None, phase="stop_insufficient")
            return self._response(
                "agent_unavailable",
                "DownloadAgent cannot execute the approved download; stopped safely.",
                False,
                verdict,
                rounds,
                self.graph_path(),
                publications_override=[],
            )
        try:
            dl = await download_action(
                question,
                missing_topics=missing,
                target_dir=str(self.coord.pdf_dir),
                candidates=[best] if best else [],
                max_papers=1,
                validate_downloads=False,
                refresh_urls=True,
            )
        except Exception as exc:
            self._set_pending(None, phase="stop_insufficient")
            return self._response(
                "download_error",
                f"The approved download failed safely: {exc}",
                False,
                verdict,
                rounds,
                self.graph_path(),
                publications_override=[],
            )
        downloaded = dl.get("downloaded") or []
        downloaded_names = [Path(p).name for p in downloaded]
        await self._emit(
            emit,
            "download_result",
            f"Downloaded {dl.get('count', 0)} PDF(s)"
            + (f": {', '.join(downloaded_names)}" if downloaded_names else ""),
            round=round_no,
            count=int(dl.get("count") or 0),
            titles=downloaded_names,
            skipped=int(dl.get("skipped") or 0),
            failed=int(dl.get("failed") or 0),
        )
        if dl.get("count", 0) == 0:
            candidate_list = [c for c in (pending.get("candidate_list") or []) if isinstance(c, dict)]
            selected_index = next(
                (
                    index
                    for index, candidate in enumerate(candidate_list)
                    if candidate is best
                    or (
                        str(candidate.get("id") or candidate.get("doi") or candidate.get("title") or "")
                        == str((best or {}).get("id") or (best or {}).get("doi") or (best or {}).get("title") or "")
                    )
                ),
                None,
            )
            failed_indices = {
                int(index) for index in (pending.get("failed_candidate_indices") or [])
            }
            if selected_index is not None:
                failed_indices.add(selected_index)
            next_pending = {
                **pending,
                "failed_candidate_indices": sorted(failed_indices),
            }
            self.workflow.update(
                unavailable_candidate_indices=sorted(failed_indices),
                phase="awaiting_download_approval",
            )
            self._set_pending(next_pending)
            response = self._response(
                "awaiting_download_decision",
                self._download_failure_message(dl, best)
                + " Ask me to download another candidate by title, number, DOI, or repository.",
                False,
                verdict,
                rounds,
                self.graph_path(),
                pending=self._pending_payload(),
                publications_override=[],
            )
            return response

        filename = downloaded_names[0] if downloaded_names else _safe_candidate_filename(best or {})
        publication = self._candidate_publication(best or {}, filename)
        paper_id = str((best or {}).get("id") or (best or {}).get("doi") or filename)
        active_paper = {
            "paper_id": paper_id,
            "filename": filename,
            "path": str(downloaded[0]) if downloaded else str(self.coord.pdf_dir / filename),
            "title": str((best or {}).get("title") or filename),
            "topic": question,
            "status": "downloaded",
        }
        next_pending = {
            "kind": "extraction",
            "approval_token": uuid.uuid4().hex,
            "verdict": verdict,
            "missing_topics": missing,
            "selected_candidate": best,
            "downloaded": downloaded,
            "reason": "",
            "round_no": round_no,
            "original_question": original_question,
            "effective_question": question,
            "active_graph_source": pending.get("active_graph_source"),
        }
        self.workflow.update(active_paper=active_paper, phase="paper_downloaded")
        self._set_pending(next_pending, phase="awaiting_extraction_approval")
        await self._orchestrator_decision(question, emit)
        pending_payload = self._pending_payload()
        await self._emit(
            emit,
            "awaiting_extraction_decision",
            "Paper downloaded - waiting for your approval to run targeted extraction",
            round=round_no,
        )
        return self._response(
            "awaiting_extraction_decision",
            "",
            False,
            verdict,
            rounds,
            self.graph_path(),
            pending=pending_payload,
            publications_override=[],
        )

    async def _resume_extraction(
        self,
        pending: Dict[str, Any],
        emit: Optional[ProgressEmitter],
    ) -> ChatResponse:
        verdict = pending.get("verdict") or {}
        missing = pending.get("missing_topics") or []
        question = pending.get("effective_question") or pending.get("original_question") or ""
        original_question = pending.get("original_question") or question
        round_no = int(pending.get("round_no") or 1)
        round_info: Dict[str, Any] = {"round": round_no, "retrieval": verdict}
        rounds: List[Dict[str, Any]] = [round_info]
        approved_pdfs = [Path(str(path)) for path in (pending.get("downloaded") or [])]

        failure = await self._extract_rebuild_reload(
            emit=emit,
            question=question,
            missing=list(missing),
            round_no=round_no,
            round_info=round_info,
            verdict=verdict,
            rounds=rounds,
            approved_pdfs=approved_pdfs,
        )
        if failure is not None:
            self._set_pending(None, phase=str(failure.status))
            await self._finalize_memory(original_question, failure, effective_question=question)
            return failure

        extraction = round_info.get("extraction") or {}
        pdf_results = [
            item for item in (extraction.get("pdf_results") or []) if isinstance(item, dict)
        ]
        active_paper = dict(self.workflow.data.get("active_paper") or {})
        active_paper["status"] = "extracted"
        active_paper["extraction_mode"] = self.runtime.extraction_mode
        self._set_pending(None, phase="paper_extracted")
        self.workflow.update(
            active_paper=active_paper,
            extraction={
                "mode": self.runtime.extraction_mode,
                "processed_pages_total": extraction.get("processed_pages_total"),
                "processed_pages_with_terms": extraction.get("processed_pages_with_terms"),
                "selected_pages": pdf_results[0].get("selected_pages") if pdf_results else [],
            },
        )
        decision = await self._orchestrator_decision(question, emit)
        if decision.get("action") != "retrieve_kg":
            response = self._response(
                "orchestration_stopped",
                str(decision.get("reason") or "Extraction completed, but re-check was stopped."),
                False,
                verdict,
                rounds,
                self.graph_path(),
            )
            await self._finalize_memory(original_question, response, effective_question=question)
            return response

        active_graph_path = self.graph_path()
        await self._emit(
            emit,
            "retrieval_started",
            "Retrieval agent re-checking the updated KG",
            round=round_no,
        )
        new_verdict = await self.retrieval.query(question)
        round_info["retrieval_after"] = new_verdict
        term_report = summarize_extracted_terms(
            str(self.coord.session_terms),
            str(self.coord.extraction_manifest),
            str(active_paper.get("filename") or ""),
            max_terms=6,
        )
        round_info["extraction_report"] = term_report
        extraction_answer = _post_extraction_answer(
            round_info.get("extraction") or {},
            term_report,
            question,
            new_verdict,
        )
        selected_count = len(new_verdict.get("selected") or [])
        await self._emit(
            emit,
            "retrieval_result",
            f"Retrieved {selected_count} KG node(s); "
            + ("evidence sufficient" if new_verdict.get("sufficient") else "evidence still insufficient"),
            round=round_no,
            selected_count=selected_count,
            sufficient=bool(new_verdict.get("sufficient")),
            missing_topics=new_verdict.get("missing_topics") or [],
        )

        selected_ids = [str(n) for n in (new_verdict.get("selected") or [])]
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

        if str(new_verdict.get("status", "")).endswith("_error"):
            self._set_pending(None, phase="retrieval_error")
            response = self._response(
                "retrieval_error",
                extraction_answer,
                False,
                new_verdict,
                rounds,
                active_graph_path,
            )
            await self._finalize_memory(original_question, response, effective_question=question)
            return response

        if new_verdict.get("sufficient"):
            self._set_pending(None, phase="answered")
            self.workflow.update(post_extraction_sufficient=True)
            if self._last_orchestration:
                self._last_orchestration["state"] = "answered"
            response = self._response(
                "answered",
                extraction_answer,
                True,
                new_verdict,
                rounds,
                active_graph_path,
            )
            await self._finalize_memory(original_question, response, effective_question=question)
            return response

        self._set_pending(None, phase="post_extraction_insufficient")
        self.workflow.update(post_extraction_sufficient=False)
        if self._last_orchestration:
            self._last_orchestration["state"] = "post_extraction_insufficient"
        response = self._response(
            "insufficient_evidence",
            extraction_answer,
            False,
            new_verdict,
            rounds,
            active_graph_path,
        )
        await self._finalize_memory(original_question, response, effective_question=question)
        return response

    def _response(
        self,
        status: str,
        answer: str,
        sufficient: bool,
        verdict: Dict[str, Any],
        rounds: List[Dict[str, Any]],
        graph_path: Path,
        *,
        pending: Optional[Dict[str, Any]] = None,
        publications_override: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        selected_ids = [str(n) for n in (verdict.get("selected") or [])]
        publications = (
            publications_override
            if publications_override is not None
            else publications_for_selected_nodes(graph_path, selected_ids)
        )
        return ChatResponse(
            status=status,
            answer=answer,
            sufficient=sufficient,
            node_ids=selected_ids,
            publications=publications,
            confidence=_confidence(verdict),
            rounds=rounds,
            graph=graph_payload_from_file(graph_path),
            graph_source_requested=verdict.get("graph_source_requested"),
            graph_source_used=verdict.get("graph_source_used"),
            workdir=str(Path(self.cfg.workdir)),
            pending=pending,
            orchestration=self._last_orchestration,
        )

    @staticmethod
    def _candidate_public(
        candidate: Optional[Dict[str, Any]],
        *,
        index: Optional[int] = None,
        recommended: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(candidate, dict):
            return None
        payload = {
            "title": str(candidate.get("title") or candidate.get("doi") or candidate.get("id") or "Untitled"),
            "doi": candidate.get("doi"),
            "publication_year": candidate.get("publication_year"),
            "abstract": str(candidate.get("abstract") or "")[:600],
            "score": float(candidate.get("score") or candidate.get("_score") or 0.0),
            "repository": candidate.get("repository"),
        }
        if index is not None:
            payload["index"] = index
        if recommended:
            payload["recommended"] = True
        return payload

    def _download_papers_public(self) -> List[Dict[str, Any]]:
        candidates = self._pending_candidate_list()
        failed = {int(index) for index in ((self.pending or {}).get("failed_candidate_indices") or [])}
        recommended_index = next(
            (index for index in range(len(candidates)) if index not in failed),
            None,
        )
        papers: List[Dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            public = self._candidate_public(
                candidate,
                index=index,
                recommended=index == recommended_index,
            )
            if public:
                if index in failed:
                    public["unavailable"] = True
                papers.append(public)
        return papers

    def _candidate_publication(self, candidate: Dict[str, Any], filename: Optional[str] = None) -> Dict[str, Any]:
        return {
            "paper_title": str(candidate.get("title") or "Untitled"),
            "doi": candidate.get("doi"),
            "publication_year": candidate.get("publication_year"),
            "source_paper": filename or _safe_candidate_filename(candidate),
            "abstract_text": str(candidate.get("abstract") or "")[:1600],
        }

    @staticmethod
    def _download_failure_message(
        dl: Dict[str, Any],
        candidate: Optional[Dict[str, Any]],
    ) -> str:
        title = str((candidate or {}).get("title") or "the selected paper")
        if int(dl.get("semantic_rejected") or 0) > 0:
            return (
                f"Downloaded **{title}**, but it did not pass validation. "
                "Try another candidate."
            )
        if int(dl.get("failed") or 0) > 0:
            return (
                f"The open-access PDF for **{title}** could not be retrieved. "
                "The repository copy may be temporarily unavailable. "
                "Try another candidate."
            )
        return f"The open-access paper **{title}** could not be downloaded."

    def _pending_payload(self) -> Optional[Dict[str, Any]]:
        if not self.pending:
            return None
        kind = self.pending.get("kind")
        if kind == "download":
            return {
                "kind": kind,
                "papers": self._download_papers_public(),
            }

        candidate = self._candidate_public(self.pending.get("selected_candidate"))
        if isinstance(candidate, dict):
            downloaded = self.pending.get("downloaded") or []
            if downloaded:
                candidate["source_paper"] = Path(str(downloaded[0])).name
        return {
            "kind": kind,
            "candidate": candidate,
        }


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
            "model": service._active_model(),
            "workflow_mode": service.runtime.workflow_mode,
            "extraction_mode": service.runtime.extraction_mode,
            "targeted_max_pages": service.runtime.targeted_max_pages,
            "session_memory": str(service.memory.path),
            "session_memory_has_context": service.memory.has_context(),
            "workflow_state": str(service.workflow.path),
            "workflow_phase": service.workflow.data.get("phase"),
            "pending_approval": (service.pending or {}).get("kind"),
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

    @app.post("/session/reset", response_model=SessionResetResponse)
    async def reset_session(req: Optional[SessionResetRequest] = None) -> SessionResetResponse:
        return await service.reset_session_context(req.session_id if req else None)

    @app.delete("/session/{session_id}")
    async def delete_session(session_id: str) -> Dict[str, str]:
        try:
            return await service.delete_session_context(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/graph", response_model=GraphPayload)
    async def graph() -> GraphPayload:
        return service.graph_payload()

    @app.post("/graph/nodes/search", response_model=GraphNodeSearchResponse)
    async def search_graph_nodes(req: GraphNodeSearchRequest) -> GraphNodeSearchResponse:
        try:
            return await service.search_graph_nodes(req.query, req.limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Node search failed: {exc}") from exc

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

    @app.patch("/graph/node/{node_id}", response_model=GraphNode)
    async def patch_graph_node(node_id: str, req: GraphNodeUpdateRequest) -> GraphNode:
        try:
            return await service.update_graph_node(node_id, req)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Splash update failed: {exc}") from exc

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
                messages=req.messages,
                session_id=req.session_id,
                graph_source=req.graph_source,
                json_graph_path=req.json_graph_path,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/chat/action", response_model=ChatResponse)
    async def chat_action(req: ChatActionRequest) -> ChatResponse:
        try:
            return await service.act(
                req.decision,
                req.kind,
                candidate_index=req.candidate_index,
                session_id=req.session_id,
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
                        messages=req.messages,
                        session_id=req.session_id,
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

    @app.post("/chat/action/stream")
    async def chat_action_stream(req: ChatActionRequest) -> StreamingResponse:
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
                    response = await service.act_with_progress(
                        req.decision,
                        emit,
                        req.kind,
                        candidate_index=req.candidate_index,
                        session_id=req.session_id,
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
