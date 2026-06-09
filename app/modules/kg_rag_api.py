#!/usr/bin/env python
"""KG-RAG API: Knowledge-Graph-augmented Retrieval-Augmented Generation for materials science.

Provides semantic/lexical retrieval over a JSON knowledge graph, LLM chat clients
(Ollama, CBORG), a FastAPI proxy for OpenWebUI, and an interactive CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import math
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Deque, Dict, List, Optional, Protocol, Sequence, Tuple
import aiohttp
import openai
from colorama import Fore, Style, init as colorama_init
from dotenv import load_dotenv
import faiss  # type: ignore
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import numpy as np
# from nvtx import annotate
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
import torch
import uvicorn

load_dotenv()


def _load_kg_deps() -> None:
    """Ensure numeric/ML dependencies are available."""
    return

# ───────────────────── optional noun-phrase extraction ─────────────────────
_NLTK_OK = False
word_tokenize = None
pos_tag = None
RegexpParser = None


# ───────────────────── configuration ─────────────────────
LLM_BACKEND = os.environ.get("KG_RAG_BACKEND", "ollama").lower()
OLLAMA_MODEL = os.environ.get("KG_RAG_OLLAMA_MODEL", os.environ.get("KG_RAG_MODEL", "deepseek-r1:70b"))
OLLAMA_API_URL = os.environ.get("KG_RAG_OLLAMA_URL", "http://localhost:11434/api/chat")
CBORG_MODEL = os.environ.get("KG_RAG_CBORG_MODEL", os.environ.get("KG_RAG_MODEL", "lbl/cborg-chat"))
CBORG_BASE_URL = os.environ.get(
    "KG_RAG_CBORG_BASE_URL",
    os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov"),
)
LLM_TEMPERATURE = float(os.environ.get("KG_RAG_TEMPERATURE", "0.4"))
LLM_TIMEOUT = int(os.environ.get("KG_RAG_LLM_TIMEOUT", "120"))
SHOW_BASELINE = bool(int(os.environ.get("KG_RAG_SHOW_BASELINE", "0")))
GRAPH_FILE = os.environ.get(
    "KG_RAG_GRAPH",
    "storage/kg/matkg_qwen3_235b_580papers.json",
)
PDF_DIR = os.environ.get("KG_RAG_PDF_DIR", "polymer_papers")

DEFAULT_K = int(os.environ.get("KG_RAG_TOPK", "12"))
EMBED_MODEL = os.environ.get("KG_RAG_EMBED_MODEL", "all-MiniLM-L6-v2")
DEFAULT_RETRIEVAL_BACKEND = "lexical" if sys.version_info >= (3, 14) else "semantic"
RETRIEVAL_BACKEND = os.environ.get("KG_RAG_RETRIEVAL_BACKEND", DEFAULT_RETRIEVAL_BACKEND).lower()
USER_BATCH_OVERRIDE: Optional[str] = os.environ.get("KG_RAG_BATCH")

PDF_SNIPPET_LEN = int(os.environ.get("KG_RAG_SNIP", "1_000"))
CONTEXT_CHAR_BUDGET = int(os.environ.get("KG_RAG_CTX_CHARS", "16_000"))
CTX_SOFT_LIMIT = int(CONTEXT_CHAR_BUDGET * 0.75)

FORCE_CPU = bool(os.environ.get("KG_RAG_FORCE_CPU"))
MAX_TEXT_CHARS = int(os.environ.get("KG_RAG_MAX_TEXT_CHARS", "1024"))

#  Retrieval & ranking
ENABLE_BFS = bool(int(os.environ.get("KG_RAG_ENABLE_BFS", "1")))
BFS_SEED_TOPK = int(os.environ.get("KG_RAG_BFS_TOPK", str(DEFAULT_K * 2)))
MAX_BFS_HOPS = int(os.environ.get("KG_RAG_MAX_HOPS", "1"))

STEPWISE = bool(int(os.environ.get("KG_RAG_STEPWISE", "1")))
STEPWISE_MAX_STEPS = int(os.environ.get("KG_RAG_STEPWISE_MAX_STEPS", "6"))

PRP_W_SEM, PRP_W_DEPTH, PRP_W_LEX, PRP_W_EVID = (
    0.8,
    0.6,
    0.6,
    0.3,
)

GENERIC_PENALTY = float(os.environ.get("KG_RAG_GENERIC_PENALTY", "0.8"))
CTX_VOLUME_TRIPLES = int(os.environ.get("KG_RAG_CONTEXT_VOLUME", "150"))
STRUCT_CTX = bool(int(os.environ.get("KG_RAG_STRUCT_CTX", "1")))

#  Misc
DEBUG = bool(int(os.environ.get("KG_RAG_DEBUG", "0")))
MAX_PDF_CACHE = int(os.environ.get("KG_RAG_PDF_CACHE", "256"))

# ───────────────────── logging ─────────────────────


class _Fmt(logging.Formatter):
    """Coloured log formatter using colorama."""

    # @annotate('_Fmt::format')
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with cyan timestamp and magenta level."""
        t = Fore.CYAN + self.formatTime(record) + Style.RESET_ALL
        return f"{t} {Fore.MAGENTA}{record.levelname}{Style.RESET_ALL}: {record.getMessage()}"


_hdl = logging.StreamHandler(sys.stdout)
_hdl.setFormatter(_Fmt())
logger = logging.getLogger("kg_rag_ollama")
logger.addHandler(_hdl)
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)

#  Terminal colours
colorama_init(autoreset=True)


# ───────────────────── FastAPI proxy for OpenWebUI ─────────────────────

def create_fastapi_app(
    graph_file: str,
    backend: str = LLM_BACKEND,
    model: Optional[str] = None,
) -> FastAPI:
    """Create a FastAPI app that proxies chat requests through the KG-RAG pipeline.

    Args:
        graph_file: Path to the KG graph JSON file.
        backend: LLM backend to use ('ollama', 'cborg', or 'cborg-openai').
        model: Model name override; defaults to backend-specific env var.

    Returns:
        Configured FastAPI application with /v1/chat/completions endpoint.
    """
    app = FastAPI(title="KG-RAG Chat Proxy")

    kg = KnowledgeGraph(graph_file)
    cli = make_chat_client(backend=backend, model=model)
    rag_c = Conversation(RAG_SYSTEM)
    # base_c = Conversation(BASELINE_SYSTEM)
    gap_tracker = MissingNodeTracker(graph_file)

    @app.post("/api/chat")
    async def api_chat(req: Request):
        """Handle incoming chat request: retrieve KG context, call LLM, return response."""
        body = await req.json()
        messages = body.get("messages", [])
        if not messages:
            return JSONResponse({"error": "No messages"}, status_code=400)

        q = messages[-1]["content"]

        infos = retrieve_nodes(q, kg)
        ctx = kg.build_context(
            infos,
            include_structured=STRUCT_CTX,
            char_budget=CTX_SOFT_LIMIT,
            hint_terms=_tokenize(q),
        )

        rag_prompt = build_rag_prompt(q, ctx)
        rag_resp = await cli.chat(rag_c.build(rag_prompt))

        missing: List[MissingNode] = []
        if all(ni.evidence_ct == 0 for ni in infos):
            missing.append(MissingNode(q, "unknown", "no evidence in KG", time.time()))
        for m in re.findall(r"\[Domain Knowledge\](.*?)\n", rag_resp):
            ent = m.strip() or "unspecified"
            missing.append(MissingNode(q, ent, "llm_fallback", time.time()))
        for mn in missing:
            gap_tracker.log(mn)

        return {
            "model": cli.model,
            "message": {"role": "assistant", "content": rag_resp},
            "done": True,
        }

    @app.get("/api/tags")
    async def list_models():
        """Return available model tags for OpenWebUI compatibility."""
        return {
            "models": [
                {"name": "kg-rag:latest", "model": "kg-rag:latest", "modified_at": "2025-09-17T00:00:00Z"}
            ]
        }

    @app.get("/api/ps")
    async def list_processes():
        """Return running processes (stub for OpenWebUI compatibility)."""
        return {"processes": []}

    return app


def run_fastapi(graph_file: str, backend: str = LLM_BACKEND, model: Optional[str] = None):
    """Launch the FastAPI KG-RAG server on port 11435.

    Args:
        graph_file: Path to the KG graph JSON file.
        backend: LLM backend to use ('ollama', 'cborg', or 'cborg-openai').
        model: Model name override; defaults to backend-specific env var.
    """
    app = create_fastapi_app(graph_file, backend=backend, model=model)
    uvicorn.run(app, host="0.0.0.0", port=11435)

# ───────────────────── Knowledge Gap Tracking ─────────────────────
# We can use this to log missing nodes for later curation


@dataclass
class MissingNode:
    """Record of an entity queried but not found in the KG."""

    query: str
    entity: str
    reason: str   # e.g., "no evidence in KG", "llm_fallback"
    timestamp: float


class MissingNodeTracker:
    """Tracks entities queried but not found in the KG, persisted as JSONL."""

    def __init__(self, kg_file: str) -> None:
        """Initialize tracker with output path derived from KG filename."""
        # derive file name from KG file
        kg_name = Path(kg_file).stem
        out_dir = Path("storage/knowledge_gaps")
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / f"missing_nodes_{kg_name}.jsonl"

        # touch file if it doesn't exist
        if not self.path.exists():
            self.path.touch()

    def log(self, node: MissingNode) -> None:
        """Append a missing node record as JSONL."""
        rec = {
            "query": node.query,
            "entity": node.entity,
            "reason": node.reason,
            "timestamp": node.timestamp,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(
            Fore.RED + f"[GapTracker] Logged missing node: {node.entity} ({node.reason})" + Style.RESET_ALL
        )


# ───────────────────── helpers ─────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
GENERIC_PAT = re.compile(r"(generic|material|property|parameter|technique|process|device)s?$", re.I)


# @annotate('_strip_ansi')
def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


# @annotate('_tokenize')
def _tokenize(text: str) -> List[str]:
    """Split text into lowercase alphanumeric tokens."""
    return re.findall(r"[A-Za-z0-9]+", text.lower())


# @annotate('_noun_phrases')
def _noun_phrases(text: str) -> List[str]:
    """Extract noun phrases via NLTK chunking, or return text as-is if NLTK unavailable."""
    if not _NLTK_OK:
        return [text] if text.strip() else []
    toks = word_tokenize(text)
    tags = pos_tag(toks)
    grammar = "NP: {<DT>?<JJ.*>*<NN.*>+}"
    tree = RegexpParser(grammar).parse(tags)
    out: List[str] = []
    for subtree in tree.subtrees(lambda t: t.label() == "NP"):
        phrase = " ".join(w for w, _ in subtree.leaves()).strip()
        if phrase:
            out.append(phrase)
    return out or [text]


# @annotate('extract_query_entities')
def extract_query_entities(q: str) -> List[str]:
    """Return deduplicated noun phrases + ≥3-char tokens."""
    ents: List[str] = []
    ents.extend([np for np in _noun_phrases(q) if len(np) >= 3])
    ents.extend([tok for tok in _tokenize(q) if len(tok) >= 3])
    seen: set[str] = set()
    uniq: List[str] = []
    for e in ents:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# @annotate('auto_device')
def auto_device() -> str:
    """Return 'cuda' if available and not forced to CPU, otherwise 'cpu'."""
    _load_kg_deps()
    if FORCE_CPU:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


# @annotate('cuda_warmup')
def cuda_warmup(device: str) -> None:
    """Warm up CUDA with a dummy matmul to avoid cold-start latency on first query."""
    _load_kg_deps()
    if device.startswith("cuda") and torch.cuda.is_available():
        try:
            torch.cuda.set_device(0)
            torch.cuda.init()
            a = torch.empty((4096, 4096), device=device).normal_()
            _ = a @ a.t()
        except Exception as exc:  # pragma: no cover
            logger.warning("CUDA warm-up failed: %s", exc)


# @annotate('snippet_text')
def snippet_text(txt: str, length: int, hints: Sequence[str] | None) -> str:
    """Extract a text snippet of given length, centered around hint term occurrences."""
    if not txt or length <= 0:
        return ""
    if len(txt) <= length:
        return txt
    if hints:
        low = txt.lower()
        hits = [low.find(h.lower()) for h in hints if low.find(h.lower()) != -1]
        if hits:
            i = min(hits)
            start = max(i - length // 4, 0)
            return txt[start: start + length]
    return txt[:length]


def format_domain_features(features: Any, *, multiline: bool = False) -> str:
    """Format schema-driven CodeSnippet domain features for search and context."""
    if not isinstance(features, list):
        return ""
    parts: List[str] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        name = str(feature.get("feature_name") or "").strip()
        value = str(feature.get("feature_value") or "").strip()
        if not name or not value:
            continue
        units = str(feature.get("feature_units") or "").strip()
        source = str(feature.get("feature_source_text") or "").strip()
        rendered = f"{name}: {value}"
        if units:
            rendered = f"{rendered} {units}"
        if source and multiline:
            rendered = f"{rendered} ({source})"
        parts.append(rendered)
    if multiline:
        return "\n".join(f"- {part}" for part in parts)
    return " ".join(parts)


# ───────────────────── dataclasses ─────────────────────
@dataclass(slots=True)
class NodeScore:
    """A KG node with a retrieval score and BFS depth."""

    id: str
    score: float
    depth: int = 0


@dataclass(slots=True)
class NodeInfo:
    """Enriched node info with semantic, graph, and lexical scores for PRP ranking."""

    id: str
    name: str
    category: str
    description: str
    score_sem: float
    score_graph: float
    depth: int
    lexical_overlap: float
    evidence_ct: int
    publication_year: int | None = None

    @property
    # @annotate('NodeInfo::score_prp')
    def score_prp(self) -> float:
        """Compute composite PRP score blending semantic, graph, lexical, evidence, and recency."""
        depth_fac = 1.0 / (1.0 + self.depth)
        evid = math.tanh(self.evidence_ct / 5.0)
        # Recency boost: papers from last 3 years get up to 0.1 bonus,
        # decaying linearly for older papers. No penalty for missing year.
        recency = 0.0
        if self.publication_year:
            current_year = datetime.datetime.now(datetime.timezone.utc).year
            age = max(0, current_year - self.publication_year)
            recency = max(0.0, 0.1 * (1.0 - age / 10.0))
        return (
            PRP_W_SEM * self.score_sem
            + PRP_W_DEPTH * depth_fac * self.score_graph
            + PRP_W_LEX * self.lexical_overlap
            + PRP_W_EVID * evid
            + recency
        )


# ───────────────────── PDF cache ─────────────────────
from functools import lru_cache  # noqa: E402


@lru_cache(maxsize=MAX_PDF_CACHE)
# @annotate('load_pdf_text')
def load_pdf_text(path: str) -> str:
    """Load and cache full text from a PDF file. Returns empty string if file missing or unreadable."""
    if not Path(path).exists():
        logger.debug("PDF missing, skipping evidence lookup: %s", path)
        return ""
    _load_kg_deps()
    try:
        doc = fitz.open(path)
    except Exception as exc:  # pragma: no cover
        logger.debug("PDF open failed %s - %s", path, exc)
        return ""
    try:
        txt = "".join(pg.get_text() for pg in doc)
    finally:
        doc.close()
    return txt


# ───────────────────── KnowledgeGraph ─────────────────────
class KnowledgeGraph:
    """In-memory knowledge graph with lexical and semantic search over nodes."""

    # @annotate('KnowledgeGraph::__init__')
    def __init__(self, graph_file: str, embed_model: str = EMBED_MODEL) -> None:
        """Load KG from JSON, build adjacency index, and initialize retrieval backend."""
        logger.info(Fore.YELLOW + "Loading KG..." + Style.RESET_ALL)
        with open(graph_file, "r") as fh:
            data = json.load(fh)
        self.nodes: Dict[str, Dict[str, Any]] = {n["id"]: n for n in data["things"]}
        self.out_edges: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in data["associations"]:
            self.out_edges[e["subject"]].append(e)

        self._canon_to_id: Dict[str, str] = {}
        for nid, n in self.nodes.items():
            canon = re.sub(r"[^a-z0-9]", "", n.get("name", "").lower())
            self._canon_to_id.setdefault(canon, nid)

        texts, self.ids = [], []
        for nid, n in self.nodes.items():
            src = " ".join(n.get("source_papers") or [])
            title = n.get("paper_title") or ""
            domain_features = format_domain_features(n.get("domain_features"))
            code_bits = " ".join(
                str(n.get(field) or "")
                for field in ("function_name", "code_domain", "code_description", "code_language")
            )
            txt = (
                f"{n.get('name','')} {n.get('description','')} {src} {title} "
                f"{code_bits} {domain_features}"
            ).strip()[:MAX_TEXT_CHARS]
            texts.append(txt)
            self.ids.append(nid)

        self.retrieval_backend = RETRIEVAL_BACKEND
        self._lexical_docs = [set(_tokenize(txt)) for txt in texts]
        if self.retrieval_backend == "semantic":
            self._build_semantic_index(texts, embed_model)
        elif self.retrieval_backend != "lexical":
            raise ValueError(f"Unknown KG_RAG_RETRIEVAL_BACKEND: {self.retrieval_backend}")

        self._cache: Dict[str, List[NodeScore]] = {}
        logger.info(
            Fore.GREEN
            + f"KG ready ({len(self.ids)} nodes, retrieval={self.retrieval_backend})."
            + Style.RESET_ALL
        )

    def _build_semantic_index(self, texts: Sequence[str], embed_model: str) -> None:
        """Encode node texts with SentenceTransformer and build a FAISS IVF-Flat index."""
        _load_kg_deps()
        device = auto_device()
        cuda_warmup(device)
        logger.info("Loading embedding model %s on %s...", embed_model, device)
        self.embed_model = SentenceTransformer(embed_model, device=device)

        try:
            _ = self.embed_model.encode(
                ["_smoke_"],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            logger.error("Initial encode failed on %s (%s) - switching to CPU", device, exc)
            self.embed_model = SentenceTransformer(embed_model, device="cpu")
            device = "cpu"

        self.batch_size = int(USER_BATCH_OVERRIDE or (16 if device == "cuda" else 32))
        logger.info("Encode batch size = %d", self.batch_size)
        logger.info("Encoding %d nodes (≤%d chars)...", len(texts), MAX_TEXT_CHARS)
        embs: List[np.ndarray] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i: i + self.batch_size]
            try:
                vecs = self.embed_model.encode(
                    chunk,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
            except Exception as exc:
                if device == "cuda":
                    logger.error("GPU encode failed (%s) → retry CPU...", exc)
                    self.embed_model = SentenceTransformer(embed_model, device="cpu")
                    vecs = self.embed_model.encode(
                        chunk,
                        convert_to_numpy=True,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                    )
                else:
                    raise
            embs.append(vecs)
        self._emb = np.vstack(embs).astype("float32")
        self._build_faiss_index(self._emb)
        self.id_map = np.asarray(self.ids)

    #  FAISS index ----------------------------------------------------------
    # @annotate('KnowledgeGraph::_build_faiss_index')
    def _build_faiss_index(self, emb: np.ndarray) -> None:
        """Build a FAISS IVF-Flat (or FlatIP) index from embedding matrix, with GPU fallback."""
        dim, N = emb.shape[1], emb.shape[0]
        nlist = max(64, int(np.sqrt(N) * 2))
        logger.info("Building IVF-Flat: dim=%d nlist=%d vectors=%d", dim, nlist, N)
        cpu_index = faiss.index_factory(dim, f"IVF{nlist},Flat", faiss.METRIC_INNER_PRODUCT)
        use_gpu = (not FORCE_CPU) and faiss.get_num_gpus() > 0
        if use_gpu:
            res = faiss.StandardGpuResources()
            try:
                self.index = faiss.index_cpu_to_gpu(res, 0, cpu_index)
                try:
                    self.index.train(emb)
                except faiss.FaissException:
                    self.index = faiss.GpuIndexFlatIP(res, dim)
                self.index.add(emb)
                self.index.nprobe = min(32, nlist // 4)
                return
            except Exception as exc:
                logger.error("GPU FAISS build failed (%s) → CPU.", exc)
        try:
            cpu_index.train(emb)
        except faiss.FaissException:
            cpu_index = faiss.IndexFlatIP(dim)
        cpu_index.add(emb)
        self.index = cpu_index
        if hasattr(self.index, "nprobe"):
            self.index.nprobe = min(32, nlist // 4)  # type: ignore[attr-defined]

    #  Semantic search ------------------------------------------------------
    # @annotate('KnowledgeGraph::_norm')
    def _norm(self, d: np.ndarray) -> np.ndarray:
        """Normalize inner-product distances from [-1,1] to [0,1]."""
        return np.clip((d + 1.0) * 0.5, 0.0, 1.0)

    def _semantic_search(self, q: str, topk: int) -> List[NodeScore]:
        """Search nodes by embedding similarity using FAISS."""
        if q in self._cache:
            return self._cache[q]
        q_vec = self.embed_model.encode([q], convert_to_numpy=True, normalize_embeddings=True)
        dists, idx = self.index.search(q_vec.astype("float32"), topk)
        hits = [
            NodeScore(self.id_map[i], float(s), depth=0)
            for i, s in zip(idx[0], self._norm(dists[0]))
        ]
        #  canonical de-dup
        seen: set[str] = set()
        uniq: List[NodeScore] = []
        for h in hits:
            canon = re.sub(r"[^a-z0-9]", "", self.nodes[h.id].get("name", "").lower())
            if canon in seen:
                continue
            seen.add(canon)
            uniq.append(h)
        self._cache[q] = uniq
        return uniq

    def _lexical_search(self, q: str, topk: int) -> List[NodeScore]:
        """Search nodes by token overlap (Jaccard-like) with query terms."""
        if q in self._cache:
            return self._cache[q]
        qt = set(t for t in _tokenize(q) if len(t) >= 3)
        if not qt:
            return []
        hits: List[NodeScore] = []
        for nid, toks in zip(self.ids, self._lexical_docs):
            overlap = len(qt & toks)
            if overlap == 0:
                continue
            name_tokens = set(_tokenize(self.nodes[nid].get("name", "")))
            name_hit = len(qt & name_tokens)
            score = (overlap / max(1, len(qt))) + (0.5 * name_hit)
            hits.append(NodeScore(nid, min(score, 1.0), depth=0))
        hits.sort(key=lambda h: h.score, reverse=True)
        self._cache[q] = hits[:topk]
        return self._cache[q]

    # @annotate('KnowledgeGraph::semantic_search')
    def semantic_search(self, q: str, topk: int = DEFAULT_K * 2) -> List[NodeScore]:
        """Dispatch to semantic or lexical search based on configured retrieval backend."""
        if self.retrieval_backend == "semantic":
            return self._semantic_search(q, topk)
        return self._lexical_search(q, topk)

    #  Weighted BFS ---------------------------------------------------------
    # @annotate('KnowledgeGraph::weighted_bfs')
    def weighted_bfs(self, seeds: Sequence[NodeScore], hops: int) -> List[NodeScore]:
        """Expand seed nodes via weighted breadth-first search over KG edges."""
        if not seeds:
            return []
        visited: Dict[str, float] = {}
        depths: Dict[str, int] = {}
        dq: Deque[Tuple[str, float, int]] = deque((s.id, s.score, 0) for s in seeds)
        while dq:
            nid, score, depth = dq.popleft()
            if depth > hops:
                continue
            if score <= visited.get(nid, 0.0):
                continue
            visited[nid] = score
            depths[nid] = depth
            for e in self.out_edges.get(nid, []):
                nbr = e["object"]
                pred = e["predicate"]
                edge_w = 1.5 if not pred.endswith("RELATED_TO") else 1.2
                edge_w += fuzz.partial_ratio(pred, "RELATED_TO") / 100.0
                if GENERIC_PAT.search(self.nodes.get(nbr, {}).get("name", "")):
                    edge_w *= GENERIC_PENALTY
                nxt_score = score * edge_w / (depth + 1.0)
                if nxt_score > visited.get(nbr, 0.0):
                    dq.append((nbr, nxt_score, depth + 1))
        return sorted(
            (NodeScore(nid, sc, depth=depths[nid]) for nid, sc in visited.items()),
            key=lambda x: x.score,
            reverse=True,
        )

    #  NodeInfo build -------------------------------------------------------
    # @annotate('KnowledgeGraph::build_nodeinfo')
    def build_nodeinfo(
        self, sem: Sequence[NodeScore], graph: Sequence[NodeScore], q_tokens: Sequence[str]
    ) -> List[NodeInfo]:
        """Merge semantic and graph scores into ranked NodeInfo objects with lexical overlap."""
        qt = [t.lower() for t in q_tokens if t]
        gmap, smap = {n.id: n for n in graph}, {n.id: n for n in sem}
        ids = set(gmap) | set(smap)
        out: List[NodeInfo] = []
        for nid in ids:
            raw = self.nodes[nid]
            name = raw.get("name", nid)
            desc = raw.get("description", "")
            txt_low = f"{name} {desc}".lower()
            hit = sum(1 for t in qt if t in txt_low)
            lex = math.sqrt(hit) / max(1, len(qt))
            evid = len(raw.get("source_papers", [])) + len(self.out_edges.get(nid, []))
            sem_sc = smap.get(nid, NodeScore(nid, 0.0)).score
            g_sc = gmap.get(nid, NodeScore(nid, 0.0)).score
            depth = gmap.get(nid, NodeScore(nid, 0, 0)).depth
            if GENERIC_PAT.search(name):
                sem_sc *= GENERIC_PENALTY
                g_sc *= GENERIC_PENALTY
            out.append(
                NodeInfo(
                    id=nid,
                    name=name,
                    category=raw.get("category", "?"),
                    description=desc,
                    score_sem=sem_sc,
                    score_graph=g_sc,
                    depth=depth,
                    lexical_overlap=lex,
                    evidence_ct=evid,
                    publication_year=raw.get("publication_year"),
                )
            )
        return out

    #  Context assembly -----------------------------------------------------
    # @annotate('KnowledgeGraph::build_context')
    def build_context(
        self,
        nodes: Sequence[NodeInfo],
        include_structured: bool,
        char_budget: int,
        hint_terms: Sequence[str] | None,
    ) -> str:
        """Assemble retrieved context string from ranked nodes for LLM prompt injection.

        Args:
            nodes: Ranked NodeInfo objects to render.
            include_structured: Whether to prepend structured KG triples.
            char_budget: Maximum character budget for the context block.
            hint_terms: Query tokens used to center PDF snippet extraction.

        Returns:
            Formatted context string with node metadata, code blocks, and relations.
        """
        parts: List[str] = []
        chars = 0

        if include_structured:
            triples: List[str] = []
            for ni in nodes:
                for e in self.out_edges.get(ni.id, []):
                    tgt = self.nodes.get(e["object"], {})
                    triples.append(
                        f"({ni.name}) -[{e['predicate'].split(':')[-1]}]-> ({tgt.get('name', e['object'])})"
                    )
                    if len(triples) >= CTX_VOLUME_TRIPLES:
                        break
                if len(triples) >= CTX_VOLUME_TRIPLES:
                    break
            blk = "Structured_KG_Facts:\n" + "\n".join(triples)
            parts.append(blk)
            chars += len(blk)

        for ni in nodes:
            raw = self.nodes[ni.id]
            lines = [
                f"## {ni.name} ({ni.category})",
                f"Combined_Score: {ni.score_prp:.3f}",
            ]
            if ni.description:
                lines.append(f"Description: {ni.description}")
            if raw.get("formula"):
                lines.append(f"Formula: {raw['formula']}")
            # publication provenance - shown for all nodes
            if raw.get("paper_title"):
                lines.append(f"Paper_Title: {raw['paper_title']}")
            if raw.get("publication_year"):
                lines.append(f"Publication_Year: {raw['publication_year']}")
            if raw.get("doi"):
                lines.append(f"DOI: {raw['doi']}")
            if raw.get("authors"):
                lines.append(f"Authors: {', '.join(raw['authors'])}")
            if raw.get("journal"):
                lines.append(f"Journal: {raw['journal']}")
            if raw.get("source_papers"):
                lines.append(f"Source_Papers: {', '.join(raw['source_papers'][:3])}")
            if raw.get("category") == "CodeSnippet":
                if not (raw.get("code_snippet") or "").strip():
                    continue
                if raw.get("function_name"):
                    lines.append(f"Function: {raw['function_name']}")
                if raw.get("code_domain"):
                    lines.append(f"Domain: {raw['code_domain']}")
                if raw.get("paper_authors"):
                    lines.append(f"Paper_Authors: {', '.join(raw['paper_authors'])}")
                domain_features = format_domain_features(raw.get("domain_features"), multiline=True)
                if domain_features:
                    lines.append(f"Domain_Features:\n{domain_features}")
                lang = raw.get("code_language") or ""
                lines.append(f"Code ({lang}):\n```{lang}\n{raw['code_snippet']}\n```")
            for pdf in raw.get("source_papers", [])[:3]:
                path = str(Path(PDF_DIR) / pdf)
                txt = load_pdf_text(path)
                snip = snippet_text(txt, PDF_SNIPPET_LEN, hint_terms)
                if snip:
                    lines.append(f"[PDF {pdf}]\n{snip}")
            if self.out_edges.get(ni.id):
                lines.append("Relations:")
                for e in sorted(self.out_edges[ni.id], key=lambda x: x["predicate"]):
                    tgt = self.nodes.get(e["object"], {})
                    pred = e["predicate"].split(":")[-1]
                    lines.append(f"- {pred}: {tgt.get('name', e['object'])}")
            sec = "\n".join(lines)
            parts.append(sec)
            chars += len(sec)
            if chars >= char_budget:
                break
        return _strip_ansi("\n\n".join(parts))


# ───────────────────── retrieval orchestrator ─────────────────────
# @annotate('decompose')
def decompose(q: str) -> List[str]:
    """Split a compound query into sub-questions for stepwise retrieval."""
    segs = re.split(r"[?;,]|\\band\\b|\\bthen\\b", q, flags=re.I)
    out = [s.strip() for s in segs if len(s.strip()) >= 3]
    return out or [q]


# @annotate('retrieve_nodes')
def retrieve_nodes(q: str, kg: KnowledgeGraph) -> List[NodeInfo]:
    """Retrieve and rank KG nodes relevant to query, including BFS expansion."""
    ents = extract_query_entities(q)
    seeds = kg.semantic_search(q)[: DEFAULT_K * 2]

    if STEPWISE:
        for sub in decompose(q)[:STEPWISE_MAX_STEPS]:
            seeds.extend(kg.semantic_search(sub)[:DEFAULT_K])

    #  keep highest score per node
    s_map: Dict[str, NodeScore] = {}
    for ns in seeds:
        cur = s_map.get(ns.id)
        if cur is None or ns.score > cur.score:
            s_map[ns.id] = ns
    sem = list(s_map.values())

    graph: List[NodeScore] = []
    if ENABLE_BFS:
        graph = kg.weighted_bfs(
            sorted(sem, key=lambda x: x.score, reverse=True)[:BFS_SEED_TOPK],
            hops=MAX_BFS_HOPS,
        )

    infos = kg.build_nodeinfo(sem, graph, ents)
    ranked = sorted(
        infos,
        key=lambda x: (x.score_prp, x.evidence_ct),
        reverse=True,
    )

    # Cap CodeSnippet nodes to top 6 to prevent code from saturating
    # context on code-heavy queries (each snippet renders full code body).
    MAX_SNIPPETS = 6
    code_ct = 0
    capped: List[NodeInfo] = []
    for ni in ranked:
        if ni.category == "CodeSnippet":
            code_ct += 1
            if code_ct > MAX_SNIPPETS:
                continue
        capped.append(ni)
        if len(capped) >= DEFAULT_K:
            break

    return capped

# ───────────────────── Ask QCs ─────────────────────


async def run_competency_questions(
    kg: KnowledgeGraph, cli: ChatClient, rag_c: Conversation, base_c: Conversation,
    infile: Path, out_json: Path, gap_tracker: MissingNodeTracker
) -> None:
    """Run a batch of competency questions, saving baseline and KG-RAG responses to JSON."""
    # load questions
    with open(infile, "r") as f:
        questions = [line.strip() for line in f if line.strip()]

    results = []
    for i, q in enumerate(questions, 1):
        print(Fore.YELLOW + f"\n[Q{i}] {q}" + Style.RESET_ALL)

        infos = retrieve_nodes(q, kg)
        ctx = kg.build_context(
            infos,
            include_structured=STRUCT_CTX,
            char_budget=CTX_SOFT_LIMIT,
            hint_terms=_tokenize(q),
        )

        base_prompt = build_baseline_prompt(q)
        rag_prompt = build_rag_prompt(q, ctx)

        base_resp = await cli.chat(base_c.build(base_prompt))
        rag_resp = await cli.chat(rag_c.build(rag_prompt))

        base_c.add(base_prompt, base_resp)
        rag_c.add(rag_prompt, rag_resp)

        results.append({
            "question_num": i,
            "question": q,
            "baseline": base_resp,
            "kg_rag": rag_resp,
        })

        print(Fore.GREEN + "[Baseline]\n" + base_resp[:500] + "..." + Style.RESET_ALL)
        print(Fore.GREEN + "[KG-RAG]\n" + rag_resp[:500] + "..." + Style.RESET_ALL)

        missing: List[MissingNode] = []
        if all(ni.evidence_ct == 0 for ni in infos):
            missing.append(MissingNode(q, "unknown", "no evidence in KG", time.time()))
        for m in re.findall(r"\[Domain Knowledge\](.*?)\n", rag_resp):
            ent = m.strip() or "unspecified"
            missing.append(MissingNode(q, ent, "llm_fallback", time.time()))
        for mn in missing:
            gap_tracker.log(mn)

        # 🔥 Save incrementally after each question
        with open(out_json, "w") as jf:
            json.dump(results, jf, indent=2)

        logger.info(f"Progress saved after Q{i} → {out_json}")


# ───────────────────── LLM clients ─────────────────────
class ChatClient(Protocol):
    """Protocol for async LLM chat clients used by the KG-RAG pipeline."""

    model: str

    async def chat(self, messages: Sequence[Dict[str, str]]) -> str:
        """Send messages and return the assistant response."""
        ...


class OllamaClient:
    """Async Ollama chat client for local LLM inference."""

    # @annotate('OllamaClient::__init__')
    def __init__(self, url: str = OLLAMA_API_URL, model: str = OLLAMA_MODEL) -> None:
        """Initialize with Ollama API URL and model name."""
        self.url, self.model = url, model
        self.timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)

    async def chat(self, messages: Sequence[Dict[str, str]]) -> str:
        """Send messages to Ollama and return the assistant response."""
        async with aiohttp.ClientSession(timeout=self.timeout) as sess:
            r = await sess.post(
                self.url,
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": list(messages),
                    "options": {"temperature": LLM_TEMPERATURE},
                },
            )
            r.raise_for_status()
            js = await r.json()
        return js.get("message", {}).get("content", "")


class CBorgClient:
    """
    OpenAI-compatible CBORG client for KG-RAG chat.
    Env: CBORG_API_KEY, CBORG_BASE_URL or KG_RAG_CBORG_BASE_URL.
    """

    def __init__(
        self,
        model: str = CBORG_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Initialize with CBORG model, API key, and base URL."""
        self.model = model
        self.client = openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("CBORG_API_KEY"),
            base_url=(base_url or CBORG_BASE_URL).rstrip("/"),
        )

    async def chat(self, messages: Sequence[Dict[str, str]]) -> str:
        """Send messages to CBORG and return the assistant response."""
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=LLM_TEMPERATURE,
                timeout=LLM_TIMEOUT,
            )
        except openai.APIConnectionError as exc:
            raise RuntimeError(
                f"CBORG connection failed for {self.model}. Check CBORG_BASE_URL/network. {exc}"
            ) from exc
        except openai.APITimeoutError as exc:
            raise RuntimeError(
                f"CBORG request timed out after {LLM_TIMEOUT}s for {self.model}."
            ) from exc
        except openai.AuthenticationError as exc:
            raise RuntimeError("CBORG authentication failed. Check CBORG_API_KEY.") from exc
        return resp.choices[-1].message.content or ""


def make_chat_client(backend: str = LLM_BACKEND, model: Optional[str] = None) -> ChatClient:
    """Instantiate the appropriate async chat client for the given backend."""
    b = (backend or "ollama").lower()
    if b == "ollama":
        return OllamaClient(model=model or OLLAMA_MODEL)
    if b in {"cborg", "cborg-openai"}:
        return CBorgClient(model=model or CBORG_MODEL)
    raise ValueError(f"Unknown KG-RAG LLM backend: {backend}")


# ───────────────────── conversation helpers ─────────────────────
class Conversation:
    """Multi-turn conversation buffer with system prompt, user/assistant history."""

    # @annotate('Conversation::__init__')
    def __init__(self, system_prompt: str) -> None:
        """Initialize with a system prompt message."""
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # @annotate('Conversation::add')
    def add(self, user: str, assistant: str) -> None:
        """Append a user/assistant turn pair to history."""
        self.messages.append({"role": "user", "content": user})
        self.messages.append({"role": "assistant", "content": assistant})

    # @annotate('Conversation::build')
    def build(self, user: str, prepend: str | None = None) -> List[Dict[str, str]]:
        """Build message list for LLM call, optionally prepending a system message."""
        msgs = list(self.messages)
        if prepend:
            msgs.append({"role": "system", "content": prepend})
        msgs.append({"role": "user", "content": user})
        return msgs


BASELINE_SYSTEM = (
    "You are an expert materials-science assistant. Answer clearly and concisely. "
    "If unsure, say so."
)
# RAG_SYSTEM = (
#     "You are an expert materials-science assistant with access to a retrieved KG context. "
#     "Use it as evidence, but flag gaps if context is missing or noisy."
# )


# @annotate('build_baseline_prompt')
def build_baseline_prompt(q: str) -> str:
    """Build a simple non-RAG baseline prompt."""
    return f"Question: {q}\n\nAnswer:"


RAG_SYSTEM = (
    "You are an expert materials-science assistant with access to a retrieved KG/PDF context. "
    "Your task is to provide the most natural, well-written scientific answer possible. "
    "Guidelines:\n"
    "1) Start by answering the question directly, in clear scientific language. "
    "2) Use information from the Retrieved Context when relevant, citing it inline as [KG: NodeName] or [PDF: file.pdf]. "
    "When citing a KG node, use the entity's name as it appears, "
    "not a placeholder like [KG: NodeName]. "
    "3) If the context adds important details, weave them naturally into your explanation. "
    "4) If something is missing, briefly note the gap or add minimal domain knowledge, marked as [Domain Knowledge]. "
    "5) Avoid rigid templates—write as you would in a scientific review article, with a mix of paragraphs and short lists. "
    "6) If sources disagree, mention the discrepancy briefly. "
    "7) When listing or comparing multiple papers/sources, rank them by relevance to the "
    "question first, then by recency (most recent first). Always include the publication "
    "year when known (e.g., 'Smith et al., 2023'). If the user asks about papers, "
    "include title, authors, year, and DOI when available in the context. "
)


def build_rag_prompt(q: str, ctx: str) -> str:
    """
    Build a grounded RAG prompt that enforces: strict grounding, paired citations,
    clear sections, and conflict/uncertainty handling.

    Citations:
      - Cite KG nodes by their section heading exactly as it appears in context, e.g., '## Poly(3-hexylthiophene) (Material)' → cite as [KG: Poly(3-hexylthiophene)].
      - Cite PDF snippets by their literal tag as shown, e.g., '[PDF somefile.pdf]' → cite as [PDF: somefile.pdf].
      - Only cite strings that literally appear in the Retrieved Context block.
    """
    return (
        f"Question:\n{q.strip()}\n\n"
        f"Retrieved Context:\n{ctx.strip()}\n\n"
        "Write a natural, coherent scientific answer that integrates the Retrieved Context. "
        "Use inline citations [KG: ...] or [PDF: ...] when grounding claims. "
        "Skip irrelevant context unless it highlights a limitation. "
        "Note any gaps or minimal fallback knowledge under [Domain Knowledge]."
    )

# ───────────────────── main Q&A loop ─────────────────────


async def call_llm(cli: ChatClient, messages: Sequence[Dict[str, str]], label: str) -> str:
    """Call the LLM with timeout handling, printing status to stdout."""
    print(
        Fore.YELLOW
        + f"Calling {cli.model} for {label} (timeout={LLM_TIMEOUT}s)..."
        + Style.RESET_ALL,
        flush=True,
    )
    try:
        return await asyncio.wait_for(cli.chat(messages), timeout=LLM_TIMEOUT + 5)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"{label} call exceeded {LLM_TIMEOUT}s. Try --timeout 30, another model, or check CBORG."
        ) from exc


async def answer(
    q: str,
    kg: KnowledgeGraph,
    cli: ChatClient,
    rag_c: Conversation,
    base_c: Conversation,
    gap_tracker: MissingNodeTracker
) -> None:
    """Answer a single question: retrieve KG context, call LLM, print result, log gaps."""
    print(Fore.MAGENTA + f"\nQ: {q}" + Style.RESET_ALL)
    infos = retrieve_nodes(q, kg)
    print(
        Fore.CYAN
        + "Selected: "
        + str([f"{n.id}:{n.score_prp:.2f}" for n in infos])
        + Style.RESET_ALL
    )
    print(Fore.YELLOW + "Building retrieved context..." + Style.RESET_ALL, flush=True)
    ctx = kg.build_context(
        infos,
        include_structured=STRUCT_CTX,
        char_budget=CTX_SOFT_LIMIT,
        hint_terms=_tokenize(q),
    )

    base_prompt = build_baseline_prompt(q)
    rag_prompt = build_rag_prompt(q, ctx)

    base_resp = await call_llm(cli, base_c.build(base_prompt), "baseline") if SHOW_BASELINE else ""
    rag_resp = await call_llm(cli, rag_c.build(rag_prompt), "KG-RAG")

    if SHOW_BASELINE:
        base_c.add(base_prompt, base_resp)
    rag_c.add(rag_prompt, rag_resp)

    if SHOW_BASELINE:
        print(Fore.GREEN + "\n[Baseline]\n" + base_resp + Style.RESET_ALL)
    print(Fore.GREEN + "\n[KG-RAG]\n" + rag_resp + Style.RESET_ALL)

    missing: List[MissingNode] = []
    if all(ni.evidence_ct == 0 for ni in infos):
        missing.append(MissingNode(q, "unknown", "no evidence in KG", time.time()))

    # after rag_resp is generated
    for m in re.findall(r"\[Domain Knowledge\](.*?)\n", rag_resp):
        ent = m.strip() or "unspecified"
        missing.append(MissingNode(q, ent, "llm_fallback", time.time()))

    # persist
    for mn in missing:
        gap_tracker.log(mn)


async def main_async(args) -> None:
    """Async entry point: interactive loop, one-shot question, or competency batch."""
    # ap = argparse.ArgumentParser()
    # ap.add_argument("--graph", type=Path, default=GRAPH_FILE)
    # ap.add_argument("--question", type=str, help="One-shot question, then exit")
    # ap.add_argument("--competency", action="store_true", help="Run full competency Q set")
    # ap.add_argument("--api", action="store_true", help="Run as FastAPI server")

    # args = ap.parse_args()

    kg = KnowledgeGraph(str(args.graph))
    gap_tracker = MissingNodeTracker(str(args.graph))
    cli = make_chat_client(backend=args.backend, model=args.model)
    rag_c = Conversation(RAG_SYSTEM)
    base_c = Conversation(BASELINE_SYSTEM)

    if args.question:
        await answer(args.question, kg, cli, rag_c, base_c, gap_tracker)
        return

    if args.competency:
        infile = Path("storage/competency_questions/thomas_f.txt")
        out_json = Path("storage/competency_questions/competency_results_qwen3_235b_580papers.json")
        await run_competency_questions(kg, cli, rag_c, base_c, infile, out_json, gap_tracker)
        return

    while True:
        try:
            q = input(Fore.YELLOW + "Ask (exit to quit): " + Style.RESET_ALL).strip()
        except EOFError:
            break
        if q.lower() in {"exit", "quit", ""}:
            break
        await answer(q, kg, cli, rag_c, base_c, gap_tracker)


# @annotate('main')
def main(args) -> None:  # pragma: no cover
    """CLI entry point — wraps main_async with KeyboardInterrupt handling."""
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except RuntimeError as exc:
        print(Fore.RED + f"\nError: {exc}" + Style.RESET_ALL)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, default=GRAPH_FILE)
    ap.add_argument("--question", type=str, help="One-shot question, then exit")
    ap.add_argument("--competency", action="store_true", help="Run full competency Q set")
    ap.add_argument("--api", action="store_true", help="Run as FastAPI server")
    ap.add_argument(
        "--backend",
        choices=["ollama", "cborg", "cborg-openai"],
        default=LLM_BACKEND,
        help="LLM backend for baseline and KG-RAG chat",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=None,
        help="Backend model name. Defaults to KG_RAG_OLLAMA_MODEL or KG_RAG_CBORG_MODEL.",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=LLM_TIMEOUT,
        help="LLM request timeout in seconds.",
    )
    ap.add_argument(
        "--show-baseline",
        action="store_true",
        default=SHOW_BASELINE,
        help="Also generate and print a non-RAG baseline answer.",
    )

    args = ap.parse_args()
    LLM_TIMEOUT = args.timeout
    SHOW_BASELINE = args.show_baseline

    if args.api:
        run_fastapi(str(args.graph), backend=args.backend, model=args.model)
    else:
        main(args)
