"""RetrievalAgent: KG retrieval + sufficiency judgement.

Wraps the existing KG-RAG retrieval stack in [kg_rag_api.py]. For each question
it retrieves and ranks KG nodes, builds the grounded context, then asks the LLM
to judge whether that context can answer the question without hallucinating.
Conceptual / teaching questions may be answered by synthesizing on-topic nodes;
numeric measurement claims still need explicit slots or snippets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from academy.agent import Agent, action

from app.modules import kg_rag_api as krag
from app.modules.hybrid_rag.pipeline import HybridSourceRAG, hybrid_rag_enabled
from app.modules.tiled_graph import (
    TILED_GRAPH_ID,
    is_experiment_identity_question,
    load_tiled_config,
    lookup_experiment_identity,
)
from app.modules.f2w_agent.materials_project import lookup_materials_project
from app.modules.f2w_agent.multi_kg import (
    DEFAULT_KG_QUERY_HOPS,
    DEFAULT_KG_QUERY_MAX_NODES,
    ScoredHit,
    clamp_kg_query_hops,
    clamp_kg_query_max_nodes,
    graph_id_for_path,
    graph_label_for_id,
    is_ops_graph_id,
    is_science_graph_id,
    merge_hits,
    schema_path_for_graph,
    unique_graph_paths,
)
from app.modules.f2w_agent.prompts import (
    JUDGE_SYSTEM,
    LAYOUT_LEEWAY_SYSTEM,
    LEEWAY_SYSTEM,
)
from app.modules.f2w_agent.trace import bind_context, note_error, record_retrieval

logger = logging.getLogger(__name__)



_CONCEPTUAL_QUESTION_RE = re.compile(
    r"\b(teach|explain|overview|introduc|tell me about|summari[sz]e|"
    r"give me a summary|what(?:'s|s| is)\b|what does\b|what are\b|what kinds?\b|"
    r"how does\b|how do\b|how to\b|how would\b|how can\b|"
    r"compare\b|difference between|trained on|"
    r"speciali[sz]e|sample class|samples?\b|analysis (?:code|workflow)|"
    r"workflow|example code|give (?:me )?(?:an )?example|show (?:me )?(?:an )?example)\b",
    re.IGNORECASE,
)
# "beamline" is a place/instrument word, not a numeric slot. Do not treat
# "what does the ALS RSoXS beamline specialize in" as a measurement claim.
_NUMERIC_CLAIM_RE = re.compile(
    r"\b(photon[_\s-]?energy|\beV\b|q-?range|temperature|kelvin|how many|"
    r"what (?:is|was) the (?:value|energy|temperature|wavelength|q[- ]value)|"
    r"measured value|exact value|numeric (?:value|slot))\b",
    re.IGNORECASE,
)
_TECHNIQUE_CATEGORY_HINTS = (
    "measurement",
    "technique",
    "method",
    "instrument",
    "scattering",
    "publication",
    "paper",
    "codesnippet",
    "beamline",
    "beamlinestage",
    "endstation",
    "motor",
    "detector",
    "processvariable",
    "esaf",
    "proposal",
    "sample",
    "blueskyrun",
)
LAYOUT_KG_QUERY_HOPS = 16
_BEAM_PATH_PREDICATES = {
    "rel:beam_path_next",
    "rel:upstream_of",
    "rel:feeds",
    "rel:connected_to",
}

# Hardware topology / optical order — not "in order to measure RSoXS".
_OPS_LAYOUT_RE = re.compile(
    r"(?:"
    r"how is(?: all(?: of)?)? the hardware connected"
    r"|hardware connected"
    r"|connected(?:\s+\w+){0,6}\s+in order"
    r"|in order.{0,40}hardware"
    r"|beam[\s-]?path"
    r"|beamline layout"
    r"|optical (?:layout|path|order)"
    r"|what comes (?:after|before)\b"
    r"|(?:upstream|downstream) of\b"
    r"|hardware (?:layout|topology|order)"
    r"|layout of (?:the )?(?:beamline|hardware|endstation|optics)"
    r"|how (?:is|are) (?:the )?(?:devices?|optics|stages?|components?) connected"
    # Catch "how is the AXIS-SXR-40 connected?" and similar named-device questions.
    # Matches "how is/are [1–5 tokens including hyphens] connected".
    r"|how (?:is|are) (?:the )?(?:[\w][\w\-\.:]*(?:\s+[\w][\w\-\.:]*){0,4})\s+connected\b"
    r")",
    re.IGNORECASE,
)

def _coerce_bool(value: Any) -> bool:
    """Coerce JSON/bool/string flags to bool; unknown strings are false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _coerce_missing_topics(value: Any) -> List[str]:
    """Normalize judge ``missing_topics`` to a list of non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        value = []
    return [str(t).strip() for t in value if str(t).strip()]


_TILED_INTENT_RE = re.compile(
    r"\b(experiment|scan|run|ESAF|proposal|samples?|measurements?|who ran|ran the|"
    r"Thomas|which runs?|my runs?|last scan|catalog|tiled)\b",
    re.IGNORECASE,
)
_OPS_INTENT_RE = re.compile(
    r"\b(beamline|BL1101|energy range|detectors?|endstation|beam|contact|"
    r"beamline scientist|hardware|motor|PV|ophyd|optics)\b",
    re.IGNORECASE,
)
_LITERATURE_INTENT_RE = re.compile(
    r"\b(tell me about|what is|how does|literature|paper|publication|technique|"
    r"RSoXS|P-RSoXS|scattering|nanostructure|polymer|synchrotron)\b",
    re.IGNORECASE,
)


def _classify_query_intent(query: str) -> str:
    """Classify query into intent bucket using keyword heuristics (no LLM needed).

    Returns one of: 'tiled', 'ops', 'literature', 'general'.
    """
    text = str(query or "").strip()
    if not text:
        return "general"
    if _TILED_INTENT_RE.search(text):
        return "tiled"
    if _OPS_INTENT_RE.search(text):
        return "ops"
    if _LITERATURE_INTENT_RE.search(text):
        return "literature"
    return "general"


def _is_numeric_claim(question: str) -> bool:
    """True only for questions that ask for a measurement number (eV, q, T, counts)."""
    return bool(_NUMERIC_CLAIM_RE.search(question or ""))


def _is_ops_layout_question(question: str) -> bool:
    """True for hardware connected-in-order / beam-path / layout intents (ops KG first)."""
    return bool(_OPS_LAYOUT_RE.search(question or ""))


def _is_conceptual_question(question: str) -> bool:
    """True for teaching/overview/how-to questions that may use definitions and relations."""
    text = (question or "").strip()
    if not text:
        return False
    if _is_numeric_claim(text):
        return False
    if _is_ops_layout_question(text):
        return True
    return bool(_CONCEPTUAL_QUESTION_RE.search(text))


def leeway_system_for(question: str) -> str:
    """Pick the layout leeway prompt for beam-path questions, else the default."""
    if _is_ops_layout_question(question):
        return LAYOUT_LEEWAY_SYSTEM
    return LEEWAY_SYSTEM


def _format_history_for_judge(history: Optional[List[Dict[str, str]]]) -> str:
    """Format the last eight chat turns for the sufficiency-judge prompt."""
    if not history:
        return ""
    lines: List[str] = []
    for message in history[-8:]:
        role = str(message.get("role") or "").strip() or "user"
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:1200]}")
    if not lines:
        return ""
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def build_judge_prompt(
    question: str,
    ctx: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Build the sufficiency-judge user prompt."""
    if _is_numeric_claim(question):
        kind = "specific numeric claim (require slots/snippets for numbers; do not invent them)"
    elif _is_ops_layout_question(question):
        kind = (
            "hardware layout / beam path / connected in order (class C): ordered path from "
            "11.0.1.2 ops KG along directed edges; cite blueprint + GitHub; never CyRSoXS/YBCO"
        )
    elif _is_conceptual_question(question):
        kind = (
            "conceptual/overview/how-to (synthesize a teaching answer from on-topic nodes; "
            "disclose graph gaps, then elaborate; mark non-slot content as not a KG measurement fact)"
        )
    else:
        kind = (
            "non-numeric question (do not refuse for missing eV/q slots; "
            "disclose graph gaps, then elaborate from retrieved nodes)"
        )
    history_block = _format_history_for_judge(history)
    return (
        f"{history_block}"
        f"Question:\n{question.strip()}\n\n"
        f"Retrieved Context:\n{ctx.strip() or '(empty)'}\n\n"
        f"Question type hint: {kind}.\n"
        "Decide sufficiency under the hard rules and return the JSON object."
    )


def _parse_judge(raw: str) -> Dict[str, Any]:
    """Tolerantly parse the judge's JSON object from LLM output."""
    if not raw:
        return {"sufficient": False, "answer": None, "missing_topics": []}
    # Largest balanced JSON object.
    pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
    matches = sorted(re.finditer(pattern, raw), key=lambda m: -len(m.group(0)))
    for m in matches:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "sufficient" in obj:
            obj.setdefault("answer", None)
            obj["missing_topics"] = _coerce_missing_topics(obj.get("missing_topics"))
            obj["sufficient"] = _coerce_bool(obj.get("sufficient"))
            if obj["sufficient"] and not str(obj.get("answer") or "").strip():
                obj["sufficient"] = False
            return obj
    return {"sufficient": False, "answer": None, "missing_topics": [], "raw": raw}


def _has_direct_evidence(kg: Any, node_info: Any) -> bool:
    """Evidence strong enough to ask the judge.

    Snippets and publications still count. For conceptual questions, a non-empty
    description or a technique/measurement category is also enough to try
    synthesizing an answer. Graph degree alone is not enough.
    """
    raw = getattr(kg, "nodes", {}).get(getattr(node_info, "id", ""), {}) if kg is not None else {}
    if not isinstance(raw, dict):
        raw = {}
    if raw.get("source_papers") or raw.get("publications") or raw.get("context_snippets") or raw.get("code_snippet"):
        return True
    if str(raw.get("description") or getattr(node_info, "description", "") or "").strip():
        return True
    category = str(
        raw.get("category")
        or raw.get("type")
        or raw.get("raw_category")
        or getattr(node_info, "category", "")
        or ""
    ).lower()
    if any(hint in category for hint in _TECHNIQUE_CATEGORY_HINTS):
        return True
    for edge in getattr(kg, "out_edges", {}).get(getattr(node_info, "id", ""), []) if kg is not None else []:
        if edge.get("has_evidence") or edge.get("evidence") or edge.get("source_papers"):
            return True
    return False


def _call_retrieve_nodes(question: str, kg: Any, hops: int, max_nodes: int) -> List[Any]:
    """Call ``retrieve_nodes`` with hops/max_nodes, falling back if the signature is older."""
    try:
        return krag.retrieve_nodes(question, kg, hops=hops, max_nodes=max_nodes)
    except TypeError:
        return krag.retrieve_nodes(question, kg)


def _beam_path_nodeinfos(kg: Any) -> List[Any]:
    """Promote documented beam-path stages so layout answers can list them in order."""
    indexed: List[Tuple[int, str, Dict[str, Any]]] = []
    nodes = getattr(kg, "nodes", {}) or {}
    for nid, raw in nodes.items():
        if not isinstance(raw, dict):
            continue
        index = raw.get("beam_path_index")
        if index in (None, "", []):
            continue
        try:
            indexed.append((int(index), str(nid), raw))
        except (TypeError, ValueError):
            continue
    if not indexed:
        # Walk directed successors from nodes that emit beam_path_next.
        ordered_ids: List[str] = []
        seen = set()
        starts = [
            nid
            for nid, raw in nodes.items()
            if any(
                str(edge.get("predicate") or "") in _BEAM_PATH_PREDICATES
                for edge in getattr(kg, "out_edges", {}).get(nid, [])
            )
            and not any(
                str(edge.get("object") or "") == nid
                and str(edge.get("predicate") or "") in _BEAM_PATH_PREDICATES
                for other_edges in getattr(kg, "out_edges", {}).values()
                for edge in other_edges
            )
        ]
        queue = list(starts)
        while queue:
            nid = queue.pop(0)
            if nid in seen or nid not in nodes:
                continue
            seen.add(nid)
            ordered_ids.append(nid)
            for edge in getattr(kg, "out_edges", {}).get(nid, []):
                if str(edge.get("predicate") or "") == "rel:beam_path_next":
                    target = str(edge.get("object") or "")
                    if target and target not in seen:
                        queue.append(target)
        indexed = [
            (i, nid, nodes[nid])
            for i, nid in enumerate(ordered_ids, start=1)
            if isinstance(nodes.get(nid), dict)
        ]
    infos: List[Any] = []
    for index, nid, raw in sorted(indexed, key=lambda item: item[0]):
        infos.append(
            SimpleNamespace(
                id=nid,
                name=str(raw.get("name") or nid),
                category=str(raw.get("category") or "BeamlineStage"),
                description=str(raw.get("description") or ""),
                score_prp=2.0 + max(0, 50 - index) / 50.0,
                evidence_ct=max(1, int(raw.get("evidence_ct") or 1)),
            )
        )
    return infos


class RetrievalAgent(Agent):
    """Academy agent that retrieves KG context and judges answer sufficiency."""

    def __init__(
        self,
        *,
        graph_file: Optional[str] = None,
        graph_files: Optional[List[str]] = None,
        graph_source: str = "json",
        backend: Optional[str] = None,
        model: Optional[str] = None,
        kg_query_hops: int = DEFAULT_KG_QUERY_HOPS,
        kg_query_max_nodes: int = DEFAULT_KG_QUERY_MAX_NODES,
        source_rag: Optional[bool] = None,
        live_tiled: Optional[bool] = None,
        tiled_uri: Optional[str] = None,
    ) -> None:
        """Configure graph paths, LLM backend, query caps, hybrid RAG, and live Tiled."""
        super().__init__()
        files = unique_graph_paths(list(graph_files or []))
        if graph_file and graph_file not in files:
            files = unique_graph_paths([graph_file, *files])
        if not files:
            files = [krag.GRAPH_FILE]
        self._graph_files = files
        self._graph_file = files[0]
        self._graph_source = graph_source
        self._backend = backend or krag.LLM_BACKEND
        self._model = model
        self._kg_query_hops = clamp_kg_query_hops(kg_query_hops)
        self._kg_query_max_nodes = clamp_kg_query_max_nodes(kg_query_max_nodes)
        self._kg = None
        self._graphs: Dict[str, Any] = {}
        self._skipped: List[Dict[str, str]] = []
        self._source_rag = hybrid_rag_enabled() if source_rag is None else bool(source_rag)
        self._hybrid = HybridSourceRAG.from_env()
        cfg = load_tiled_config(uri=tiled_uri, enabled=live_tiled)
        self._live_tiled = cfg.enabled
        self._tiled_uri = cfg.uri
        self._last_tiled_meta: Dict[str, Any] = cfg.snapshot()
        self._last_mp_meta: Dict[str, Any] = {}
        self._last_intent: str = "general"

    def _build_kg(self):
        """Build a KnowledgeGraph honoring the configured source (json/splash)."""
        return krag.KnowledgeGraph(str(self._graph_file), graph_source=self._graph_source)

    def _annotate_kg(
        self,
        kg: Any,
        path: str,
        *,
        existing: Optional[Dict[str, Any]] = None,
        graph_id: Optional[str] = None,
    ) -> str:
        """Stamp *kg* with graph_id/label/schema/path, disambiguating collisions in *existing*."""
        gid = graph_id or graph_id_for_path(path)
        taken = existing if existing is not None else self._graphs
        base = gid
        suffix = 2
        while gid in taken:
            gid = f"{base}_{suffix}"
            suffix += 1
        kg.graph_id = gid
        kg.graph_label = graph_label_for_id(gid)
        kg.schema_path = schema_path_for_graph(path)
        kg.graph_path = str(path)
        return gid

    def _skip_reason(self, path: str) -> Optional[str]:
        """Return why *path* cannot be loaded, or None if the file looks usable."""
        candidate = Path(path)
        if not candidate.exists():
            return "missing"
        try:
            if candidate.stat().st_size == 0:
                return "empty"
        except OSError as exc:
            return str(exc)
        return None

    def _build_kgs(self) -> Dict[str, Any]:
        """Load each configured JSON KG, skipping missing/empty/broken files."""
        graphs: Dict[str, Any] = {}
        skipped: List[Dict[str, str]] = []
        for path in self._graph_files:
            reason = self._skip_reason(path)
            if reason:
                logger.warning("Skipping %s KG %s", reason, path)
                skipped.append({"path": path, "reason": reason})
                continue
            try:
                kg = krag.KnowledgeGraph(str(path), graph_source=self._graph_source)
            except Exception as exc:
                logger.warning("Skipping KG %s: %s", path, exc)
                skipped.append({"path": path, "reason": str(exc)})
                continue
            if not getattr(kg, "nodes", None):
                logger.warning("Skipping empty KG %s", path)
                skipped.append({"path": path, "reason": "empty"})
                continue
            gid = self._annotate_kg(kg, path, existing=graphs)
            graphs[gid] = kg
        self._skipped = skipped
        return graphs

    def set_query_limits(self, *, hops: Optional[int] = None, max_nodes: Optional[int] = None) -> None:
        """Clamp and store neighborhood hops / max nodes for the next retrieve."""
        if hops is not None:
            self._kg_query_hops = clamp_kg_query_hops(hops)
        if max_nodes is not None:
            self._kg_query_max_nodes = clamp_kg_query_max_nodes(max_nodes)

    def set_source_rag(self, enabled: bool) -> None:
        """Enable or disable hybrid literature/ops source RAG for later queries."""
        self._source_rag = bool(enabled)

    def set_live_tiled(self, enabled: bool, *, uri: Optional[str] = None) -> None:
        """Toggle live Tiled Graph lookup and optionally replace the Tiled URI."""
        cfg = load_tiled_config(uri=uri if uri is not None else self._tiled_uri, enabled=enabled)
        self._live_tiled = cfg.enabled
        self._tiled_uri = cfg.uri
        self._last_tiled_meta = cfg.snapshot()

    @action
    async def reload_kg(
        self,
        graph_file: Optional[str] = None,
        graph_source: Optional[str] = None,
        graph_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Rebuild the in-memory KG(s). Missing/empty files are skipped."""
        if graph_source:
            self._graph_source = graph_source
        if graph_files is not None:
            files = unique_graph_paths(graph_files)
            if graph_file and graph_file not in files:
                files = unique_graph_paths([graph_file, *files])
            if files:
                self._graph_files = files
                self._graph_file = files[0]
            loop = asyncio.get_event_loop()
            self._graphs = await loop.run_in_executor(None, self._build_kgs)
            self._kg = next(iter(self._graphs.values()), None)
        else:
            if graph_file:
                self._graph_file = graph_file
                self._graph_files = unique_graph_paths([graph_file])
            loop = asyncio.get_event_loop()
            self._kg = await loop.run_in_executor(None, self._build_kg)
            self._graphs = {}
            if self._kg is not None:
                gid = self._annotate_kg(self._kg, str(self._graph_file))
                self._graphs[gid] = self._kg
        primary = self._kg
        return {
            "status": "reloaded",
            "graph_file": str(self._graph_file),
            "graph_files": list(self._graph_files),
            "graph_ids": list(self._graphs),
            "skipped": list(self._skipped),
            "nodes": sum(len(getattr(kg, "nodes", {}) or {}) for kg in self._graphs.values()),
            "graph_source_requested": getattr(
                primary, "graph_source_requested", self._graph_source
            ),
            "graph_source_used": getattr(primary, "graph_source_used", self._graph_source),
        }

    def _active_graphs(self) -> Dict[str, Any]:
        """Return loaded graphs, wrapping a legacy single ``_kg`` if needed."""
        if self._graphs:
            return self._graphs
        if self._kg is not None:
            gid = getattr(self._kg, "graph_id", None) or graph_id_for_path(str(self._graph_file))
            if not getattr(self._kg, "graph_id", None):
                self._annotate_kg(self._kg, str(self._graph_file), graph_id=gid)
            return {gid: self._kg}
        return {}

    async def _ensure_graphs(self) -> Dict[str, Any]:
        """Load graphs on first use; raise if none can be opened."""
        graphs = self._active_graphs()
        if graphs:
            return graphs
        loop = asyncio.get_event_loop()
        if self._graph_files:
            try:
                self._graphs = await loop.run_in_executor(None, self._build_kgs)
                self._kg = next(iter(self._graphs.values()), None)
            except (AttributeError, TypeError):
                raise
            except Exception:
                logger.warning(
                    "Failed to build multi-KG from %s; falling back to single KG",
                    self._graph_files,
                    exc_info=True,
                )
                self._kg = await loop.run_in_executor(None, self._build_kg)
        elif self._kg is None:
            self._kg = await loop.run_in_executor(None, self._build_kg)
        result = self._active_graphs()
        if not result:
            raise RuntimeError(
                "No knowledge graphs loaded; cannot answer questions. "
                "Check graph file paths and logs above."
            )
        return result

    def _source_meta(self, graphs: Dict[str, Any]) -> Dict[str, Any]:
        """Metadata block describing graph source, query caps, Tiled, intent, and MP."""
        primary = next(iter(graphs.values()), self._kg)
        return {
            "graph_source_requested": getattr(
                primary, "graph_source_requested", self._graph_source
            ),
            "graph_source_used": getattr(primary, "graph_source_used", self._graph_source),
            "graph_ids": list(graphs),
            "kg_query_hops": self._kg_query_hops,
            "kg_query_max_nodes": self._kg_query_max_nodes,
            "live_tiled": dict(self._last_tiled_meta or {}),
            "kg_intent": self._last_intent,
            "materials_project": dict(getattr(self, "_last_mp_meta", {}) or {}),
        }

    def _merge_live_tiled(
        self,
        question: str,
        graphs: Dict[str, Any],
        kg_hits: List[ScoredHit],
    ) -> Tuple[Dict[str, Any], List[ScoredHit]]:
        """Merge live Tiled identity hits into *graphs*/*kg_hits* when the question needs them."""
        if not self._live_tiled:
            self._last_tiled_meta = load_tiled_config(
                uri=self._tiled_uri, enabled=False
            ).snapshot()
            self._last_tiled_meta["status"] = "disabled"
            return graphs, kg_hits
        if not is_experiment_identity_question(question):
            self._last_tiled_meta = {
                **load_tiled_config(uri=self._tiled_uri, enabled=True).snapshot(),
                "status": "skipped",
                "used": False,
                "hits": 0,
            }
            return graphs, kg_hits
        pack = lookup_experiment_identity(
            question, uri=self._tiled_uri, enabled=True
        )
        self._last_tiled_meta = pack.meta()
        self._last_tiled_meta["tiled_uri"] = self._tiled_uri
        active = dict(graphs)
        if pack.graph is not None and pack.graph.nodes:
            active[TILED_GRAPH_ID] = pack.graph
            if isinstance(getattr(self, "_graphs", None), dict):
                self._graphs[TILED_GRAPH_ID] = pack.graph
            else:
                self._graphs = {TILED_GRAPH_ID: pack.graph}
        if pack.hits:
            merged = merge_hits([*pack.hits, *kg_hits], limit=self._kg_query_max_nodes)
            return active, merged
        return active, kg_hits

    async def search_node_scores(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Rank nodes across selected KGs without invoking the answer-generation workflow."""
        loop = asyncio.get_event_loop()
        graphs = await self._ensure_graphs()
        kg_hits: List[ScoredHit] = []
        graphs, tiled_hits = self._merge_live_tiled(query, graphs, kg_hits)
        matches: List[Dict[str, Any]] = []
        backend = "lexical"
        for hit in tiled_hits:
            matches.append(
                {
                    "id": hit.id,
                    "score": float(hit.score),
                    "graph_id": hit.graph_id,
                    "graph_label": hit.graph_label or TILED_GRAPH_ID,
                }
            )
        for gid, kg in graphs.items():
            if gid == TILED_GRAPH_ID:
                backend = getattr(kg, "retrieval_backend", "graphql")
                continue
            backend = getattr(kg, "retrieval_backend", backend)
            hits = await loop.run_in_executor(None, kg.semantic_search, query, limit)
            for hit in hits:
                matches.append(
                    {
                        "id": str(hit.id),
                        "score": float(hit.score),
                        "graph_id": gid,
                        "graph_label": getattr(kg, "graph_label", graph_label_for_id(gid)),
                    }
                )
        matches.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return {
            "retrieval_backend": backend,
            "matches": matches[:limit],
            "graph_ids": list(graphs),
        }

    def _parallel_retrieve(
        self,
        question: str,
        graphs: Dict[str, Any],
        hops: int,
        max_nodes: int,
        layout: bool,
    ) -> List[ScoredHit]:
        """Fan out retrieval across *graphs* and return unmerged :class:`ScoredHit` objects.

        Implementation note
        -------------------
        This method is always called from within ``loop.run_in_executor`` (i.e.
        it already runs in a background thread from asyncio's default thread
        pool).  Creating *another* ThreadPoolExecutor here would spawn N extra
        threads on top of that existing thread — all sharing the same GIL for
        Python-bound lexical search — while adding ~8 MB of stack overhead per
        thread and causing concurrent memory allocation spikes that trigger the
        macOS jetsam OOM-killer on memory-constrained hosts.

        We therefore iterate graphs sequentially.  The latency cost is
        negligible for ≤3 KGs: each lexical search completes in <50 ms, so two
        graphs cost ~100 ms instead of ~50 ms.  If semantic (FAISS) retrieval
        is ever re-enabled and true parallelism is desired, reinstate the pool
        *only* for that backend, guarded by a per-KG cache hit check.
        """
        if not graphs:
            return []

        raw_hits: List[ScoredHit] = []
        for gid, kg in graphs.items():
            try:
                infos: List[Any] = list(_call_retrieve_nodes(question, kg, hops, max_nodes))
            except (AttributeError, TypeError):
                raise
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "KG retrieval failed for %s / %r: %s",
                    gid,
                    question,
                    exc,
                    exc_info=True,
                )
                infos = []
            if layout:
                infos = infos + _beam_path_nodeinfos(kg)
            for info in infos:
                _raw_node = getattr(kg, "nodes", {}).get(str(getattr(info, "id", info)), {}) or {}
                _category = str(getattr(info, "category", "") or "") or str(
                    _raw_node.get("entity_type") or _raw_node.get("entityType") or ""
                )
                raw_hits.append(
                    ScoredHit(
                        id=str(getattr(info, "id", info)),
                        graph_id=gid,
                        score=float(
                            getattr(info, "score_prp", getattr(info, "score", 0.0)) or 0.0
                        ),
                        evidence_ct=int(getattr(info, "evidence_ct", 0) or 0),
                        category=_category,
                        name=str(getattr(info, "name", "") or getattr(info, "id", "")),
                        graph_label=getattr(kg, "graph_label", graph_label_for_id(gid)),
                        payload=info,
                    )
                )
        return raw_hits

    @staticmethod
    def _substring_device_fallback(
        question: str,
        graphs: Dict[str, Any],
        limit: int = 10,
    ) -> List[ScoredHit]:
        """Return KG nodes whose *name* appears verbatim (case-insensitive) in *question*.

        Used as a last-resort supplement when embedding/BM25 retrieval misses a
        named device (e.g. "how is the AXIS-SXR-40 connected?").  Only nodes
        with a name of ≥4 characters are considered to avoid spurious matches on
        short abbreviations.
        """
        q_lower = question.lower()
        hits: List[ScoredHit] = []
        seen: set = set()
        for gid, kg in graphs.items():
            nodes = getattr(kg, "nodes", {}) or {}
            for nid, raw in nodes.items():
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "").strip()
                if len(name) < 4:
                    continue
                if name.lower() not in q_lower:
                    continue
                if nid in seen:
                    continue
                seen.add(nid)
                hits.append(
                    ScoredHit(
                        id=nid,
                        graph_id=gid,
                        score=1.5,  # slightly above normal retrieval floor
                        evidence_ct=max(1, int(raw.get("evidence_ct") or 1)),
                        category=str(raw.get("category") or ""),
                        name=name,
                        graph_label=getattr(kg, "graph_label", graph_label_for_id(gid)),
                        payload=SimpleNamespace(
                            id=nid,
                            name=name,
                            category=str(raw.get("category") or ""),
                            description=str(raw.get("description") or ""),
                            score_prp=1.5,
                            evidence_ct=max(1, int(raw.get("evidence_ct") or 1)),
                        ),
                    )
                )
                if len(hits) >= limit:
                    return hits
        return hits

    @staticmethod
    def _rsoxs_popular_nodes(kg: Any, graph_id: str, max_n: int) -> List[ScoredHit]:
        """Return the most evidence-rich nodes from a science/RSoXS KG.

        When BM25 returns few rsoxs hits for a literature-intent query (because
        the question uses general terms like "materials" while the KG stores
        specific material instances), these popularity-boosted nodes ensure that
        at least the most-cited materials surface in context.

        Popularity signal: ``len(source_papers)`` or ``len(papers)`` (list of
        paper references).  Old-format KGs (v1/v2) use ``source_papers``; new-
        format KGs (v3+) use ``papers``.  Only nodes with ≥ 2 papers are
        included.
        Score = 0.3 + len(papers)/100 — kept below normal retrieval hits but
        above zero so they don't displace high-quality BM25 results after merge.
        """
        nodes = getattr(kg, "nodes", None) or {}
        # Guard: kg.nodes should always be a dict from KnowledgeGraph, but
        # handle the list case defensively in case a caller passes a raw KG dict.
        if isinstance(nodes, list):
            nodes = {n.get("id", str(i)): n for i, n in enumerate(nodes) if isinstance(n, dict)}

        logger.info(
            "RSoXS popular nodes: scanning %d nodes in graph %s (max_n=%d)",
            len(nodes),
            graph_id,
            max_n,
        )
        candidates: List[Tuple[int, str, Dict[str, Any]]] = []
        for nid, raw in nodes.items():
            if not isinstance(raw, dict):
                continue
            # Old-format KGs (v1/v2) store paper refs in "source_papers";
            # new-format KGs (v3+) store DOI strings in "papers".
            papers = raw.get("source_papers") or raw.get("papers")
            if not isinstance(papers, list):
                continue
            count = len(papers)
            if count < 2:
                continue
            candidates.append((count, str(nid), raw))

        candidates.sort(key=lambda x: x[0], reverse=True)

        graph_label = getattr(kg, "graph_label", graph_label_for_id(graph_id))
        hits: List[ScoredHit] = []
        for count, nid, raw in candidates[:max_n]:
            score = 0.3 + count / 100.0
            entity_type = str(
                raw.get("entity_type") or raw.get("entityType") or ""
            )
            name = str(raw.get("label") or raw.get("name") or nid)
            hits.append(
                ScoredHit(
                    id=nid,
                    graph_id=graph_id,
                    score=score,
                    evidence_ct=count,
                    category=entity_type,
                    name=name,
                    graph_label=graph_label,
                    payload=SimpleNamespace(
                        id=nid,
                        name=name,
                        category=entity_type,
                        description=str(raw.get("description") or ""),
                        score_prp=score,
                        evidence_ct=count,
                    ),
                )
            )
        return hits

    def _intent_routed_retrieve(
        self,
        question: str,
        graphs: Dict[str, Any],
        hops: int,
        max_nodes: int,
        intent: str,
    ) -> List[ScoredHit]:
        """Fan out retrieval with intent-based candidate allocation.

        Primary KG matching the intent gets 60% of candidate slots; remaining
        KGs share the other 40%.  Falls back to equal split when there is only
        one KG or intent is 'general'.
        """
        if intent == "general" or len(graphs) <= 1:
            return self._parallel_retrieve(question, graphs, hops, max_nodes, layout=False)

        # Identify primary vs secondary graphs by intent
        if intent in ("ops", "tiled"):
            # Tiled data lives in _merge_live_tiled; ops KGs take priority here
            primary = {gid: kg for gid, kg in graphs.items() if is_ops_graph_id(gid)}
        else:  # 'literature'
            primary = {gid: kg for gid, kg in graphs.items() if not is_ops_graph_id(gid)}

        secondary = {gid: kg for gid, kg in graphs.items() if gid not in primary}

        if not primary or not secondary:
            # Can't split — just do equal allocation
            return self._parallel_retrieve(question, graphs, hops, max_nodes, layout=False)

        primary_max = max(1, int(max_nodes * 0.6))
        other_count = max(1, len(secondary))
        secondary_max = max(1, int(max_nodes * 0.4 / other_count))

        logger.debug(
            "Intent routing %r → intent=%s primary=%s (max=%d) secondary=%s (max=%d)",
            question[:80],
            intent,
            list(primary),
            primary_max,
            list(secondary),
            secondary_max,
        )

        raw_hits = self._parallel_retrieve(question, primary, hops, primary_max, layout=False)
        raw_hits += self._parallel_retrieve(question, secondary, hops, secondary_max, layout=False)

        # Literature-intent popularity supplement for RSoXS science KGs.
        # BM25 scores poorly for general-category questions (e.g. "what kinds of
        # materials") against a KG of specific material instances — supplement
        # with the most paper-cited nodes so they don't get crowded out by ops nodes.
        if intent == "literature":
            rsoxs_hit_count = sum(1 for h in raw_hits if is_science_graph_id(h.graph_id))
            threshold = max_nodes * 0.3
            if rsoxs_hit_count < threshold:
                existing_ids = {h.id for h in raw_hits}
                for gid, kg in primary.items():
                    if not is_science_graph_id(gid):
                        continue
                    n_supplement = max(1, int(threshold) - rsoxs_hit_count + 1)
                    supplement = self._rsoxs_popular_nodes(kg, gid, max_n=n_supplement)
                    new_hits = [h for h in supplement if h.id not in existing_ids]
                    if new_hits:
                        logger.info(
                            "RSoXS popularity supplement: adding %d popular node(s) for %r",
                            len(new_hits),
                            question[:80],
                        )
                        raw_hits = raw_hits + new_hits
                        existing_ids.update(h.id for h in new_hits)

        return raw_hits

    def _fanout_hits(self, question: str, graphs: Dict[str, Any]) -> List[ScoredHit]:
        """Retrieve hits from all active graphs and merge by evidence rank.

        Intent router rules
        -------------------
        * Layout / beam-path questions → query ops KG(s) first and only.
          If the ops KG returns no hits, fall back to non-ops (science) KGs so
          the user still gets *something* rather than an empty context.
        * All other questions → intent-routed fanout across every non-Tiled graph.
          - 'ops' intent: ops KGs get 60% of candidate slots.
          - 'literature' intent: science KGs get 60% of candidate slots.
          - 'tiled' intent: ops KGs get 60% (Tiled is handled separately).
          - 'general' intent: equal split across all KGs.
        """
        layout = _is_ops_layout_question(question)
        non_tiled = {gid: kg for gid, kg in graphs.items() if gid != TILED_GRAPH_ID}
        hops = max(self._kg_query_hops, LAYOUT_KG_QUERY_HOPS) if layout else self._kg_query_hops
        max_nodes = self._kg_query_max_nodes
        self._trace_layout_ops_only_applied = False

        if layout:
            ops_only = {gid: kg for gid, kg in non_tiled.items() if is_ops_graph_id(gid)}
            active = ops_only if ops_only else non_tiled
            self._trace_layout_ops_only_applied = bool(ops_only)
        else:
            active = non_tiled

        if layout:
            raw_hits = self._parallel_retrieve(question, active, hops, max_nodes, layout)
        else:
            intent = _classify_query_intent(question)
            self._last_intent = intent
            raw_hits = self._intent_routed_retrieve(
                question, active, hops, max_nodes, intent
            )

        # Named-device substring fallback (layout questions): when a device name
        # appears literally in the question (e.g. "AXIS-SXR-40"), guarantee that
        # node is always included even if embedding/BM25 retrieval misses it.
        if layout:
            ops_graphs = ops_only if ops_only else non_tiled
            sub_hits = self._substring_device_fallback(question, ops_graphs)
            if sub_hits:
                existing_ids = {h.id for h in raw_hits}
                new_sub = [h for h in sub_hits if h.id not in existing_ids]
                if new_sub:
                    logger.info(
                        "Substring fallback added %d node(s) for layout question %r: %s",
                        len(new_sub),
                        question,
                        [h.id for h in new_sub],
                    )
                raw_hits = raw_hits + new_sub

        # Intent router fallback: layout question but ops returned nothing →
        # include science KGs so the answer is at least partially grounded.
        if layout and not raw_hits and active is not non_tiled:
            science_graphs = {
                gid: kg for gid, kg in non_tiled.items() if not is_ops_graph_id(gid)
            }
            if science_graphs:
                raw_hits = self._parallel_retrieve(
                    question, science_graphs, hops, max_nodes, layout=False
                )

        return merge_hits(raw_hits, limit=max_nodes)

    def _kg_work_ids(self, hits: Sequence[ScoredHit], graphs: Dict[str, Any]) -> List[str]:
        """Collect source-paper filenames and DOIs from hit nodes for hybrid RAG."""
        ids: List[str] = []
        seen: set[str] = set()
        for hit in hits:
            kg = graphs.get(hit.graph_id)
            raw = getattr(kg, "nodes", {}).get(hit.id, {}) if kg is not None else {}
            if not isinstance(raw, dict):
                continue
            for paper in raw.get("source_papers") or []:
                name = Path(str(paper)).name
                if name and name not in seen:
                    seen.add(name)
                    ids.append(name)
            doi = str(raw.get("doi") or "").strip().lower()
            if doi:
                token = f"doi:{doi}"
                if token not in seen:
                    seen.add(token)
                    ids.append(token)
        return ids

    def _build_kg_context(self, question: str, hits: Sequence[ScoredHit], graphs: Dict[str, Any]) -> str:
        """Build per-graph context blocks, giving the intent-primary graph the budget first."""
        by_graph: Dict[str, List[Any]] = {}
        for hit in hits:
            if hit.payload is None:
                continue
            by_graph.setdefault(hit.graph_id, []).append(hit.payload)

        # Reorder graph sections so the intent-primary graph gets the majority of the
        # context budget.  Hits are sorted by score (descending) so the ops KG's
        # high-scoring named-component nodes often insert themselves first — even when
        # the query is about RSoXS science.  Reordering fixes that.
        if len(by_graph) > 1:
            intent = getattr(self, "_last_intent", "general")
            if intent == "literature":
                # Science/RSoXS graphs first so their context fills the budget.
                ordered_ids = [
                    *[gid for gid in by_graph if is_science_graph_id(gid)],
                    *[gid for gid in by_graph if not is_science_graph_id(gid)],
                ]
                by_graph = {gid: by_graph[gid] for gid in ordered_ids if gid in by_graph}
            elif intent in ("ops", "tiled"):
                # Ops graphs first.
                ordered_ids = [
                    *[gid for gid in by_graph if is_ops_graph_id(gid)],
                    *[gid for gid in by_graph if not is_ops_graph_id(gid)],
                ]
                by_graph = {gid: by_graph[gid] for gid in ordered_ids if gid in by_graph}
            # "general" intent: keep original score-based insertion order.

        parts: List[str] = []
        remaining = krag.CTX_SOFT_LIMIT
        for gid, infos in by_graph.items():
            if remaining <= 0:
                break
            kg = graphs.get(gid)
            if kg is None:
                continue
            label = getattr(kg, "graph_label", graph_label_for_id(gid))
            cite_tokens: List[str] = []
            nodes_index = getattr(kg, "nodes", None) or {}
            if isinstance(nodes_index, list):
                nodes_index = {
                    str(n.get("id")): n
                    for n in nodes_index
                    if isinstance(n, dict) and n.get("id")
                }
            for info in infos:
                nid = str(getattr(info, "id", "") or "")
                raw: Dict[str, Any] = {}
                if isinstance(nodes_index, dict) and nid:
                    raw = nodes_index.get(nid) or {}
                name = str(
                    getattr(info, "name", "")
                    or raw.get("name")
                    or raw.get("label")
                    or nid
                ).strip()
                if name:
                    cite_tokens.append(f"[KG: {gid}: {name}]")
            allowed = ""
            if cite_tokens:
                allowed = "Allowed citations (copy these tokens exactly):\n" + "\n".join(
                    f"- {token}" for token in cite_tokens
                ) + "\n"
            header = (
                f"### Knowledge graph `{gid}` ({label}). "
                f"Cite nodes from this graph as [KG: {gid}: <name>].\n"
                f"{allowed}"
            )
            try:
                ctx = kg.build_context(
                    infos,
                    include_structured=krag.STRUCT_CTX,
                    char_budget=remaining,
                    hint_terms=krag._tokenize(question),
                    include_pdf_snippets=False,
                )
            except TypeError:
                ctx = kg.build_context(
                    infos,
                    include_structured=krag.STRUCT_CTX,
                    char_budget=remaining,
                    hint_terms=krag._tokenize(question),
                )
            part = header + (ctx or "")
            parts.append(part)
            remaining -= len(part)
        return "\n\n".join(parts)

    def _build_merged_context(
        self,
        question: str,
        hits: Sequence[ScoredHit],
        graphs: Dict[str, Any],
        source_context: str = "",
    ) -> str:
        """Concatenate KG context with optional PDF/ops/MP source context."""
        kg_ctx = self._build_kg_context(question, hits, graphs)
        parts = [part for part in (kg_ctx, source_context) if str(part or "").strip()]
        return "\n\n".join(parts)

    def _hybrid_pack(
        self,
        question: str,
        hits: Sequence[ScoredHit],
        graphs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Invoke the HybridSourceRAG pipeline and return a source-context pack dict."""
        empty = {
            "intent": "factual",
            "evidence": [],
            "source_context": "",
            "chunk_count": 0,
            "literature_available": False,
            "ops_available": False,
        }
        hybrid = getattr(self, "_hybrid", None)
        if hybrid is None:
            return empty
        try:
            layout = _is_ops_layout_question(question)
            return hybrid.pack(
                question,
                kg_hits=hits,
                include_literature=not layout,
                include_ops=True,
                layout=layout,
                numeric=_is_numeric_claim(question),
                kg_work_ids=self._kg_work_ids(hits, graphs),
                kg_entity_ids=[hit.id for hit in hits],
            )
        except Exception as exc:
            logger.warning("Hybrid source RAG skipped for %r: %s", question, exc)
            note_error("RetrievalAgent._hybrid_pack", exc)
            return empty

    @action
    async def query(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        source_rag: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Retrieve context for ``question`` and judge whether it suffices to answer."""
        loop = asyncio.get_event_loop()
        graphs = await self._ensure_graphs()
        self._trace_layout_ops_only_applied = False
        self._trace_merged_context = None
        self._trace_raw_judge = None
        self._trace_parsed_verdict = None
        self._trace_leeway_used = False
        self._trace_leeway_prompt = None

        meta = self._source_meta(graphs)
        primary = next(iter(graphs.values()), self._kg)
        hits: List[ScoredHit] = []
        if graphs:
            try:
                hits = await loop.run_in_executor(
                    None, bind_context(self._fanout_hits), question, graphs
                )
            except Exception as exc:
                logger.warning("KG retrieval failed for %r: %s", question, exc)
                note_error("RetrievalAgent.query._fanout_hits", exc)
                hits = []
        graphs, hits = self._merge_live_tiled(question, graphs, hits)
        meta = self._source_meta(graphs)
        meta["live_tiled"] = dict(self._last_tiled_meta or {})

        enabled = self._source_rag if source_rag is None else bool(source_rag)
        if enabled:
            hybrid = await loop.run_in_executor(
                None, bind_context(lambda: self._hybrid_pack(question, hits, graphs))
            )
        else:
            hybrid = {
                "intent": "factual",
                "evidence": [],
                "source_context": "",
                "chunk_count": 0,
                "literature_available": False,
                "ops_available": False,
            }
        source_context = str(hybrid.get("source_context") or "")
        chunk_count = int(hybrid.get("chunk_count") or 0)
        mp_pack = await loop.run_in_executor(None, lambda: lookup_materials_project(question))
        self._last_mp_meta = {
            "triggered": bool(mp_pack.get("triggered")),
            "queries": list(mp_pack.get("queries") or []),
            "record_count": len(mp_pack.get("records") or []),
            "skipped": mp_pack.get("skipped"),
        }
        mp_context = str(mp_pack.get("context") or "")
        if mp_context.strip():
            source_context = "\n\n".join(
                part for part in (source_context, mp_context) if str(part).strip()
            )

        selected = [hit.id for hit in hits]
        selected_hits = [hit.as_dict() for hit in hits]
        direct_evidence_count = sum(
            1
            for hit in hits
            if hit.payload is not None and _has_direct_evidence(graphs.get(hit.graph_id), hit.payload)
        )
        conceptual = _is_conceptual_question(question)
        numeric = _is_numeric_claim(question)
        has_mp = bool(mp_context.strip())
        no_evidence = (
            len(selected) == 0 and chunk_count == 0 and not has_mp
        ) or (
            direct_evidence_count == 0 and chunk_count == 0 and not conceptual and not has_mp
        )
        hybrid_meta = {
            "hybrid_rag": {
                "intent": hybrid.get("intent"),
                "chunk_count": chunk_count,
                "literature_available": bool(hybrid.get("literature_available")),
                "ops_available": bool(hybrid.get("ops_available")),
            },
            "materials_project": dict(self._last_mp_meta),
        }
        if no_evidence:
            self._publish_retrieval_trace(
                question,
                hits=hits,
                meta=meta,
                hybrid_meta=hybrid_meta,
                sufficient=False,
                no_evidence=True,
                direct_evidence_count=0,
                missing_topics=[question],
            )
            return {
                "status": "success",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": selected,
                "selected_hits": selected_hits,
                "no_evidence": True,
                "direct_evidence_count": 0,
                **meta,
                **hybrid_meta,
            }

        try:
            ctx = await loop.run_in_executor(
                None,
                bind_context(lambda: self._build_merged_context(
                    question, hits, graphs, source_context=source_context
                )),
            )
            self._trace_merged_context = ctx
        except Exception as exc:
            logger.warning("KG context build failed for %r: %s", question, exc)
            note_error("RetrievalAgent.query._build_merged_context", exc)
            if source_context.strip():
                ctx = source_context
                self._trace_merged_context = ctx
            else:
                self._publish_retrieval_trace(
                    question,
                    hits=hits,
                    meta=meta,
                    hybrid_meta=hybrid_meta,
                    sufficient=False,
                    no_evidence=False,
                    direct_evidence_count=direct_evidence_count,
                    missing_topics=[question],
                )
                return {
                    "status": "context_error",
                    "question": question,
                    "sufficient": False,
                    "answer": None,
                    "missing_topics": [question],
                    "selected": selected,
                    "selected_hits": selected_hits,
                    "no_evidence": False,
                    "direct_evidence_count": direct_evidence_count,
                    "error": str(exc),
                    **meta,
                    **hybrid_meta,
                }

        try:
            cli = krag.make_chat_client(backend=self._backend, model=self._model)
            raw = await krag.call_llm(
                cli,
                krag.Conversation(JUDGE_SYSTEM).build(
                    build_judge_prompt(question, ctx, history=history)
                ),
                "KG-RAG-judge",
            )
        except Exception as exc:
            logger.warning("Retrieval judge failed for %r: %s", question, exc)
            note_error("RetrievalAgent.query.judge", exc)
            self._publish_retrieval_trace(
                question,
                hits=hits,
                meta=meta,
                hybrid_meta=hybrid_meta,
                sufficient=False,
                no_evidence=False,
                direct_evidence_count=direct_evidence_count,
                missing_topics=[question],
            )
            return {
                "status": "judge_error",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": selected,
                "selected_hits": selected_hits,
                "no_evidence": False,
                "direct_evidence_count": direct_evidence_count,
                "error": str(exc),
                **meta,
                **hybrid_meta,
            }
        self._trace_raw_judge = raw
        verdict = _parse_judge(raw)
        self._trace_parsed_verdict = verdict

        sufficient = bool(verdict.get("sufficient"))
        missing = verdict.get("missing_topics") or []
        if not sufficient and not missing:
            missing = [question]
        answer = verdict.get("answer") if sufficient else None

        if not sufficient and (selected or chunk_count) and not numeric:
            leeway = await self._synthesize_leeway_answer(
                cli, question, ctx, history, missing
            )
            if leeway:
                sufficient = True
                answer = leeway
                self._trace_leeway_used = True

        self._publish_retrieval_trace(
            question,
            hits=hits,
            meta=meta,
            hybrid_meta=hybrid_meta,
            sufficient=sufficient,
            no_evidence=False,
            direct_evidence_count=direct_evidence_count,
            missing_topics=list(missing),
        )
        return {
            "status": "success",
            "question": question,
            "sufficient": sufficient,
            "answer": answer if sufficient else None,
            "missing_topics": missing,
            "selected": selected,
            "selected_hits": selected_hits,
            "no_evidence": False,
            "direct_evidence_count": direct_evidence_count,
            **meta,
            **hybrid_meta,
            "graph_source_requested": getattr(
                primary, "graph_source_requested", self._graph_source
            ),
            "graph_source_used": getattr(primary, "graph_source_used", self._graph_source),
        }

    def _publish_retrieval_trace(
        self,
        question: str,
        *,
        hits: Sequence[ScoredHit],
        meta: Dict[str, Any],
        hybrid_meta: Dict[str, Any],
        sufficient: bool,
        no_evidence: bool,
        direct_evidence_count: int,
        missing_topics: List[str],
    ) -> None:
        """Copy retrieval internals onto the active turn trace. Never raises."""
        try:
            selected_hits = []
            for hit in hits:
                raw = hit.as_dict()
                selected_hits.append({
                    "id": raw.get("id"),
                    "graph_id": raw.get("graph_id"),
                    "graph_label": raw.get("graph_label"),
                    "score": raw.get("score"),
                    "category": raw.get("category"),
                })
            record_retrieval({
                "selected_hits": selected_hits,
                "sufficient": sufficient,
                "no_evidence": no_evidence,
                "direct_evidence_count": direct_evidence_count,
                "missing_topics": list(missing_topics),
                "live_tiled": dict(meta.get("live_tiled") or {}),
                "hybrid_rag": dict((hybrid_meta.get("hybrid_rag") or {})),
                "route_flags": {
                    "_is_ops_layout_question": _is_ops_layout_question(question),
                    "_is_numeric_claim": _is_numeric_claim(question),
                    "_is_conceptual_question": _is_conceptual_question(question),
                    "is_experiment_identity_question": is_experiment_identity_question(question),
                },
                "layout_ops_only_filter": bool(getattr(self, "_trace_layout_ops_only_applied", False)),
                "merged_context": getattr(self, "_trace_merged_context", None),
                "raw_judge_output": getattr(self, "_trace_raw_judge", None),
                "parsed_verdict": getattr(self, "_trace_parsed_verdict", None),
                "leeway_used": bool(getattr(self, "_trace_leeway_used", False)),
                "leeway_system_prompt": getattr(self, "_trace_leeway_prompt", None),
            })
        except Exception as exc:
            logger.warning("Retrieval trace publish failed: %s", exc)

    async def _synthesize_leeway_answer(
        self,
        cli: Any,
        question: str,
        ctx: str,
        history: Optional[List[Dict[str, str]]],
        missing: List[str],
    ) -> Optional[str]:
        """Second-pass LLM answer that discloses graph gaps then still teaches from context."""
        history_block = _format_history_for_judge(history)
        missing_text = ", ".join(missing) if missing else (
            "numeric slots / code objects / beamline-ops graph"
        )
        prompt = (
            f"{history_block}"
            f"Question:\n{question.strip()}\n\n"
            f"Graph gaps to disclose:\n{missing_text}\n\n"
            f"Retrieved Context:\n{ctx.strip() or '(empty)'}\n\n"
            "Write the answer now."
        )
        self._trace_leeway_prompt = (
            "LAYOUT_LEEWAY_SYSTEM" if _is_ops_layout_question(question) else "LEEWAY_SYSTEM"
        )
        try:
            raw = await krag.call_llm(
                cli,
                krag.Conversation(leeway_system_for(question)).build(prompt),
                "KG-RAG-leeway",
            )
        except Exception as exc:
            logger.warning("Leeway synthesis failed for %r: %s", question, exc)
            note_error("RetrievalAgent._synthesize_leeway_answer", exc)
            return None
        text = str(raw or "").strip()
        if text:
            self._trace_leeway_used = True
        return text or None
