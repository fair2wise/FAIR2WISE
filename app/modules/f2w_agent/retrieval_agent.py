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
from concurrent.futures import ThreadPoolExecutor
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
from app.modules.f2w_agent.multi_kg import (
    DEFAULT_KG_QUERY_HOPS,
    DEFAULT_KG_QUERY_MAX_NODES,
    ScoredHit,
    clamp_kg_query_hops,
    clamp_kg_query_max_nodes,
    graph_id_for_path,
    graph_label_for_id,
    is_ops_graph_id,
    merge_hits,
    schema_path_for_graph,
    unique_graph_paths,
)

logger = logging.getLogger(__name__)


# RSoXS v1 (and similar technique graphs) are vocabulary/relation seeds: an
# audit found ~64 ontology terms and ~32 json2kg-ready measurement nodes. Missing
# photon_energy slots must not be treated as "cannot teach RSoXS".
JUDGE_SYSTEM = (
    "You are an evidence adjudicator for a materials-science knowledge graph. "
    "You are given a user question, optional conversation history, and a "
    "Retrieved Context block taken from a knowledge graph and source PDFs. "
    "Decide whether you can answer without inventing measurement numbers.\n"
    "Graph character: technique KGs such as RSoXS v1 are vocabulary/relation "
    "seeds (ontology aliases plus json2kg-ready RSoXSMeasurement nodes). Empty "
    "photon_energy slots or missing numeric properties do NOT mean you cannot "
    "teach, describe a beamline specialty, name sample classes, outline an "
    "analysis workflow, or show example code. They mean you must not invent "
    "those numbers.\n"
    "Classify the question, then apply the matching bar:\n"
    "A) Conceptual / overview / teaching / definition / compare / "
    "'what is X' / 'summarize this graph' / 'give me a summary' / "
    "beamline specialty / sample classes / analysis workflow / how-to / "
    "example code:\n"
    "   SUFFICIENT when retrieved nodes are on-topic (technique names, "
    "RSoXSMeasurement, related variants such as P-RSoXS, VT-RSoXS, NRSS, "
    "CyRSoXS, Nika, tools, or publication nodes). Synthesize a teaching answer "
    "from names, types, descriptions, relations, snippets, and publication "
    "titles in the context. If the graph lacks a beamline-ops graph, code "
    "objects, or energy slots, say that first, then still elaborate. "
    "Paraphrasing and combining those grounded facts is required. "
    "Cite supporting publications inside the answer with [KG:graph_id: name] "
    "or [KG: ...] or [PDF: filename p.N] or [OPS: file §heading] that appear in "
    "the context; include the graph_id so the reader knows whether a node came "
    "from the science KG or 11.0.1.2 ops. Literature PDFs and beamline ops docs "
    "are different sources — do not treat an [OPS: ...] cite as a paper. "
    "Do not refuse and dump papers as a substitute for answering.\n"
    "Do NOT use class A science elaboration (NRSS, CyRSoXS, cuprate papers, YBCO) "
    "for hardware-layout / beam-path / 'connected in order' questions; those are class C.\n"
    "B) Specific numeric or measurement claims (photon energy, eV, q-range, "
    "temperature, counts, a paper's measured value):\n"
    "   SUFFICIENT only when the slot, snippet, or property is present in context. "
    "If the number is absent, sufficient=false. Never invent eV, q, or other "
    "measurement numbers.\n"
    "C) Hardware layout / beam path / how devices are connected in order / "
    "what comes after X at ALS 11.0.1.2:\n"
    "   Answer from the 11.0.1.2 ops KG (graph_id bl1101) as an ordered list or "
    "path along directed edges (beam_path_next, upstream_of, feeds, connected_to). "
    "Cite the Gabe blueprint and GitHub/source_papers that appear in context. "
    "Do not answer 11.0.1.2 infrastructure with cuprate RSXS papers, YBCO, NRSS, "
    "or CyRSoXS. If directed topology is thin, say so first, then list ops nodes "
    "(motors, detectors, AXIS-SXR-40, BeamlineStage) — still ops, never science "
    "elaboration. SUFFICIENT when any ops hardware / stage / detector nodes are "
    "on-topic.\n"
    "Hard rules:\n"
    "1) Prefer Retrieved Context for measurement facts. For conceptual / how-to / "
    "specialty / sample-class / workflow / example-code questions, you MAY also "
    "use conversation history, related technique names in context, and high-level "
    "domain knowledge. Mark any sentence that is not a KG slot or snippet as "
    "not a KG measurement fact. Do not refuse these intents merely because "
    "numeric slots are empty.\n"
    "2) Never invent authors, years, DOIs, journals, numeric values, or papers "
    "that do not appear in the context.\n"
    "3) If the Retrieved Context is empty or clearly off-topic (a different "
    "material or technique than asked), sufficient=false.\n"
    "4) Do not treat a missing photon_energy slot as insufficient for conceptual "
    "questions when on-topic RSoXSMeasurement / technique / publication nodes "
    "are present.\n"
    "5) If sufficient, answer the question. Ground KG-backed claims with inline "
    "[KG:graph_id: ...] or [KG: ...] or [PDF: filename p.N] or [OPS: file §heading] "
    "citations that appear literally in the context. When Retrieved Context labels "
    "a graph_id, include that graph_id in the citation. Cite papers as [PDF: ...] "
    "and beamline docs as [OPS: ...]; never fuse them.\n"
    "6) When you reproduce a CodeSnippet code block, append this exact disclaimer on its "
    "own line immediately after the closing fence: " + krag.CODE_SNIPPET_DISCLAIMER + "\n"
    "Respond with a SINGLE JSON object and nothing else, using this schema:\n"
    '{"sufficient": true|false, "answer": string|null, '
    '"missing_topics": [string, ...]}\n'
    "When sufficient=false for a numeric measurement claim, set answer=null and "
    "list the missing slots as short search-friendly phrases. When the question "
    "is conceptual / how-to and on-topic nodes exist, prefer sufficient=true "
    "with an answer that discloses graph gaps and then elaborates."
)


_CONCEPTUAL_QUESTION_RE = re.compile(
    r"\b(teach|explain|overview|introduc|tell me about|summari[sz]e|"
    r"give me a summary|what(?:'s|s| is)\b|what does\b|what kind\b|"
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

LEEWAY_SYSTEM = (
    "You answer materials-science questions from a vocabulary/relation knowledge graph "
    "that often lacks numeric slots and executable code objects. "
    "First say what the graph lacks for this question (for example: no beamline-ops graph, "
    "no analysis-code objects, no filled photon_energy slots). "
    "Then still elaborate using retrieved nodes, publication titles, related techniques "
    "such as NRSS, CyRSoXS, Nika, and P-RSoXS, conversation so far, and high-level domain "
    "knowledge. Mark domain-knowledge sentences as not a KG measurement fact. "
    "Never invent numeric values (eV, q, temperature) or papers/authors/DOIs that are not "
    "in the retrieved context. "
    "If the question is a hardware layout / beam-path / connected-in-order question, "
    "ignore the NRSS/CyRSoXS/science elaboration instruction: answer only from 11.0.1.2 "
    "ops nodes as an ordered path, never cuprate papers. "
    "Return ONLY the answer text, not JSON."
)

LAYOUT_LEEWAY_SYSTEM = (
    "You answer ALS Beamline 11.0.1.2 hardware-layout questions from the ops knowledge "
    "graph (graph_id bl1101). Return an ordered list / path along directed edges "
    "(beam_path_next, upstream_of, feeds, connected_to): source → EPU/optics → M103 → "
    "exit slits → … → sample → detector. Cite the Gabe blueprint and GitHub sources "
    "that appear in the retrieved context as [KG:bl1101: …] or [OPS: file §heading]. "
    "If directed topology is thin or missing, say that first, then list ops hardware "
    "nodes (motors, detectors, AXIS-SXR-40, BeamlineStage). "
    "Do not mention CyRSoXS, NRSS, Nika, P-RSoXS, YBCO, cuprate papers, or the science "
    "KG. Do not invent numeric measurement values. "
    "Return ONLY the answer text, not JSON."
)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _coerce_missing_topics(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        value = []
    return [str(t).strip() for t in value if str(t).strip()]


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
    if _is_ops_layout_question(question):
        return LAYOUT_LEEWAY_SYSTEM
    return LEEWAY_SYSTEM


def _format_history_for_judge(history: Optional[List[Dict[str, str]]]) -> str:
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
        if hops is not None:
            self._kg_query_hops = clamp_kg_query_hops(hops)
        if max_nodes is not None:
            self._kg_query_max_nodes = clamp_kg_query_max_nodes(max_nodes)

    def set_source_rag(self, enabled: bool) -> None:
        self._source_rag = bool(enabled)

    def set_live_tiled(self, enabled: bool, *, uri: Optional[str] = None) -> None:
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
        if self._graphs:
            return self._graphs
        if self._kg is not None:
            gid = getattr(self._kg, "graph_id", None) or graph_id_for_path(str(self._graph_file))
            if not getattr(self._kg, "graph_id", None):
                self._annotate_kg(self._kg, str(self._graph_file), graph_id=gid)
            return {gid: self._kg}
        return {}

    async def _ensure_graphs(self) -> Dict[str, Any]:
        graphs = self._active_graphs()
        if graphs:
            return graphs
        loop = asyncio.get_event_loop()
        if self._graph_files:
            try:
                self._graphs = await loop.run_in_executor(None, self._build_kgs)
                self._kg = next(iter(self._graphs.values()), None)
            except Exception:
                self._kg = await loop.run_in_executor(None, self._build_kg)
        elif self._kg is None:
            self._kg = await loop.run_in_executor(None, self._build_kg)
        return self._active_graphs()

    def _source_meta(self, graphs: Dict[str, Any]) -> Dict[str, Any]:
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
        }

    def _merge_live_tiled(
        self,
        question: str,
        graphs: Dict[str, Any],
        kg_hits: List[ScoredHit],
    ) -> Tuple[Dict[str, Any], List[ScoredHit]]:
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
        """Fan out retrieval across *graphs* in parallel via a thread pool.

        Returns an unmerged list of :class:`ScoredHit` objects tagged with
        ``graph_id``.  Each graph runs in its own thread so N graphs take
        ≈1× the latency of the slowest graph instead of N×.
        """
        if not graphs:
            return []

        def _retrieve_one(item: Tuple[str, Any]) -> Tuple[str, Any, List[Any]]:
            gid, kg = item
            try:
                infos: List[Any] = list(_call_retrieve_nodes(question, kg, hops, max_nodes))
            except Exception as exc:  # pragma: no cover
                logger.warning("KG retrieval failed for %s / %r: %s", gid, question, exc)
                infos = []
            if layout:
                infos = infos + _beam_path_nodeinfos(kg)
            return gid, kg, infos

        raw_hits: List[ScoredHit] = []
        n_workers = max(1, len(graphs))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            for gid, kg, infos in pool.map(_retrieve_one, graphs.items()):
                for info in infos:
                    raw_hits.append(
                        ScoredHit(
                            id=str(getattr(info, "id", info)),
                            graph_id=gid,
                            score=float(
                                getattr(info, "score_prp", getattr(info, "score", 0.0)) or 0.0
                            ),
                            evidence_ct=int(getattr(info, "evidence_ct", 0) or 0),
                            category=str(getattr(info, "category", "") or ""),
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

    def _fanout_hits(self, question: str, graphs: Dict[str, Any]) -> List[ScoredHit]:
        """Retrieve hits from all active graphs and merge by evidence rank.

        Intent router rules
        -------------------
        * Layout / beam-path questions → query ops KG(s) first and only.
          If the ops KG returns no hits, fall back to non-ops (science) KGs so
          the user still gets *something* rather than an empty context.
        * All other questions → fan out across every non-Tiled graph.
        """
        layout = _is_ops_layout_question(question)
        non_tiled = {gid: kg for gid, kg in graphs.items() if gid != TILED_GRAPH_ID}
        hops = max(self._kg_query_hops, LAYOUT_KG_QUERY_HOPS) if layout else self._kg_query_hops
        max_nodes = self._kg_query_max_nodes

        if layout:
            ops_only = {gid: kg for gid, kg in non_tiled.items() if is_ops_graph_id(gid)}
            active = ops_only if ops_only else non_tiled
        else:
            active = non_tiled

        raw_hits = self._parallel_retrieve(question, active, hops, max_nodes, layout)

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
        by_graph: Dict[str, List[Any]] = {}
        for hit in hits:
            if hit.payload is None:
                continue
            by_graph.setdefault(hit.graph_id, []).append(hit.payload)
        parts: List[str] = []
        remaining = krag.CTX_SOFT_LIMIT
        for gid, infos in by_graph.items():
            if remaining <= 0:
                break
            kg = graphs.get(gid)
            if kg is None:
                continue
            label = getattr(kg, "graph_label", graph_label_for_id(gid))
            header = (
                f"### Knowledge graph `{gid}` ({label})\n"
                f"Cite nodes from this graph as [KG:{gid}: <name>].\n"
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
        kg_ctx = self._build_kg_context(question, hits, graphs)
        parts = [part for part in (kg_ctx, source_context) if str(part or "").strip()]
        return "\n\n".join(parts)

    def _hybrid_pack(
        self,
        question: str,
        hits: Sequence[ScoredHit],
        graphs: Dict[str, Any],
    ) -> Dict[str, Any]:
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

        meta = self._source_meta(graphs)
        primary = next(iter(graphs.values()), self._kg)
        hits: List[ScoredHit] = []
        if graphs:
            try:
                hits = await loop.run_in_executor(None, self._fanout_hits, question, graphs)
            except Exception as exc:
                logger.warning("KG retrieval failed for %r: %s", question, exc)
                hits = []
        graphs, hits = self._merge_live_tiled(question, graphs, hits)
        meta = self._source_meta(graphs)
        meta["live_tiled"] = dict(self._last_tiled_meta or {})

        enabled = self._source_rag if source_rag is None else bool(source_rag)
        if enabled:
            hybrid = await loop.run_in_executor(
                None, lambda: self._hybrid_pack(question, hits, graphs)
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

        selected = [hit.id for hit in hits]
        selected_hits = [hit.as_dict() for hit in hits]
        direct_evidence_count = sum(
            1
            for hit in hits
            if hit.payload is not None and _has_direct_evidence(graphs.get(hit.graph_id), hit.payload)
        )
        conceptual = _is_conceptual_question(question)
        numeric = _is_numeric_claim(question)
        no_evidence = (
            len(selected) == 0 and chunk_count == 0
        ) or (
            direct_evidence_count == 0 and chunk_count == 0 and not conceptual
        )
        hybrid_meta = {
            "hybrid_rag": {
                "intent": hybrid.get("intent"),
                "chunk_count": chunk_count,
                "literature_available": bool(hybrid.get("literature_available")),
                "ops_available": bool(hybrid.get("ops_available")),
            }
        }
        if no_evidence:
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
                lambda: self._build_merged_context(
                    question, hits, graphs, source_context=source_context
                ),
            )
        except Exception as exc:
            logger.warning("KG context build failed for %r: %s", question, exc)
            if source_context.strip():
                ctx = source_context
            else:
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
        verdict = _parse_judge(raw)

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

    async def _synthesize_leeway_answer(
        self,
        cli: Any,
        question: str,
        ctx: str,
        history: Optional[List[Dict[str, str]]],
        missing: List[str],
    ) -> Optional[str]:
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
        try:
            raw = await krag.call_llm(
                cli,
                krag.Conversation(leeway_system_for(question)).build(prompt),
                "KG-RAG-leeway",
            )
        except Exception as exc:
            logger.warning("Leeway synthesis failed for %r: %s", question, exc)
            return None
        text = str(raw or "").strip()
        return text or None
