"""Isolated in-process harness for the frozen F2W legacy UI contract (T18A).

Builds ``app.modules.f2w_agent.api.create_app`` and
``app.modules.kg_rag_api.create_fastapi_app`` with deterministic fakes in
place of every model, paper provider, Splash and Tiled call. Nothing here
opens a network socket: ``socket.connect`` is patched to raise.

The same scripted flows are used by ``record_fixtures.py`` (writes the
baseline fixtures) and ``test_legacy_ui_contract.py`` (replays them).
"""
from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Must be set before app.modules.kg_rag_api is imported (it reads
# KG_RAG_RETRIEVAL_BACKEND at import time; kg_rag_api.py:45).
os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
os.environ["F2W_SOURCE_RAG"] = "0"
os.environ["F2W_LIVE_TILED"] = "0"

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures"
WORKSPACE_TOKEN = "<WORKSPACE>"
REPO_TOKEN = "<REPO>"
HOST_TOKEN = "<HOST>"

# Environment variables that could carry credentials or real endpoints.
_SCRUB_ENV = (
    "CBORG_API_KEY", "CBORG_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "MP_API_KEY", "GITHUB_TOKEN", "TILED_URI", "TILED_API_KEY", "TILED_CATALOG_URI",
    "TILED_GRAPH_ENABLED", "SPLASH_URI", "SPLASH_LINKS_URI", "KG_RAG_CBORG_BASE_URL",
    "KG_RAG_OLLAMA_URL", "F2W_WORKFLOW_MODE", "F2W_EXTRACTION_MODE", "F2W_MODEL",
    "KG_RAG_MODEL", "KG_RAG_CBORG_MODEL", "KG_RAG_OLLAMA_MODEL", "KG_RAG_BACKEND",
    "F2W_BACKEND", "FAIR2WISE_CONFIG",
)

GRAPH_FILENAME = "matkg_contract_v1.json"

SEED_GRAPH: Dict[str, Any] = {
    "things": [
        {
            "id": "matkg:TermA",
            "name": "Term A",
            "category": "Technique",
            "description": "Synthetic contract node A (resonant scattering).",
            "publications": [
                {
                    "source_paper": "contract_paper.pdf",
                    "paper_title": "Synthetic contract paper on resonant scattering",
                    "doi": "10.0000/contract.a",
                    "publication_year": 2024,
                    "authors": ["Contract Author"],
                    "keywords": ["resonant", "scattering"],
                }
            ],
            "source_papers": ["contract_paper.pdf"],
        },
        {
            "id": "matkg:Other",
            "name": "Other node",
            "category": "Material",
            "description": "Synthetic contract node B (polymer film).",
            "publications": [],
        },
        {
            "id": "matkg:Code1",
            "name": "gamma snippet",
            "category": "CodeSnippet",
            "function_name": "gamma",
            "code_language": "python",
            "code_snippet": "def gamma():\n    return 0",
        },
    ],
    "associations": [
        {"subject": "matkg:TermA", "predicate": "rel:has_code_snippet", "object": "matkg:Code1"},
        {"subject": "matkg:TermA", "predicate": "rel:related_to", "object": "matkg:Other"},
    ],
}

CANDIDATES = [
    {
        "id": "Wcontract1",
        "doi": "10.0000/contract.cand1",
        "title": "Candidate paper one",
        "abstract": "Synthetic abstract for candidate one.",
        "publication_year": 2023,
        "score": 0.91,
        "repository": "openalex",
        "pdf_urls": ["https://example.invalid/cand1.pdf"],
    },
    {
        "id": "Wcontract2",
        "doi": "10.0000/contract.cand2",
        "title": "Candidate paper two",
        "abstract": "Synthetic abstract for candidate two.",
        "publication_year": 2022,
        "score": 0.4,
        "repository": "arxiv",
        "pdf_urls": ["https://example.invalid/cand2.pdf"],
    },
]


class NetworkBlocked(RuntimeError):
    """Raised when code under test tries to open a socket."""


def _blocked_connect(self, *args, **kwargs):  # pragma: no cover - only on violation
    raise NetworkBlocked(f"network access blocked in legacy UI contract harness: {args!r}")


@dataclass
class Ctx:
    """Mutable state shared by the fakes of one harness instance."""

    workspace: Path
    repo_root: Path
    kg_rebuilt: bool = False
    download_fail: bool = False
    splash_entities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    splash_links: List[Dict[str, Any]] = field(default_factory=list)
    calls: Dict[str, int] = field(default_factory=dict)
    planner_calls: List[Dict[str, Any]] = field(default_factory=list)

    def bump(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1


# --------------------------------------------------------------------------- fakes


def _graph_things(path: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    return [t for t in data.get("things") or [] if isinstance(t, dict)]


def make_fakes(ctx: Ctx):
    """Return fake agent classes bound to *ctx*."""

    class FakeRetrieval:
        """Deterministic RetrievalAgent. Verdict depends on the question text."""

        def __init__(self, *args, **kwargs):
            self.graph_file = kwargs.get("graph_file") or (args[0] if args else None)
            self._live_tiled = bool(kwargs.get("live_tiled"))

        async def reload_kg(self, graph_file, graph_source=None, **kwargs):
            self.graph_file = graph_file
            return {"status": "reloaded", "graph_source_requested": graph_source, "graph_source_used": graph_source}

        def set_live_tiled(self, enabled, uri=None):
            self._live_tiled = bool(enabled)

        async def query(self, question, *args, **kwargs):
            ctx.bump("retrieval.query")
            q = str(question).lower()
            if "explode" in q:
                raise RuntimeError("synthetic retrieval failure")
            if "crash the verdict" in q:
                return ["malformed", "verdict"]
            if "insufficient" in q and ctx.kg_rebuilt:
                # Post-extraction re-check sees the rebuilt KG exactly once.
                ctx.kg_rebuilt = False
            elif "insufficient" in q:
                return {
                    "status": "success",
                    "sufficient": False,
                    "missing_topics": ["synthetic missing topic"],
                    "selected": [],
                    "direct_evidence_count": 0,
                    "no_evidence": True,
                    "graph_source_requested": "splash",
                    "graph_source_used": "splash",
                }
            return {
                "status": "success",
                "sufficient": True,
                "answer": "Synthetic grounded answer (\u03bc-scale, UTF-8) citing [KG: matkg:TermA].",
                "selected": ["matkg:TermA", "matkg:Other"],
                "direct_evidence_count": 2,
                "no_evidence": False,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

        async def search_node_scores(self, query, limit=10):
            tokens = [t for t in re.split(r"\W+", str(query).lower()) if t]
            matches = []
            for thing in _graph_things(str(self.graph_file)):
                text = f"{thing.get('name', '')} {thing.get('description', '')}".lower()
                score = sum(1 for t in tokens if t in text)
                if score:
                    matches.append({"id": thing["id"], "score": round(score / max(1, len(tokens)), 3)})
            matches.sort(key=lambda m: (-m["score"], m["id"]))
            return {"retrieval_backend": "lexical", "matches": matches[:limit]}

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def find_and_download(self, *args, **kwargs):
            raise AssertionError("agentic HTTP path must not call find_and_download")

        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            ctx.bump("download.search_candidates")
            return {"status": "success", "count": len(CANDIDATES), "candidates": [dict(c) for c in CANDIDATES]}

        async def download_selected(self, query, missing_topics=None, target_dir="pdfs", candidates=None,
                                    max_papers=1, **kwargs):
            ctx.bump("download.download_selected")
            titles = [str((c or {}).get("title")) for c in (candidates or [])]
            if ctx.download_fail or "Candidate paper two" in titles:
                return {"status": "success", "count": 0, "downloaded": [], "failed": 1, "skipped": 0}
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            pdf = target / "candidate_one.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsynthetic contract body")
            return {"status": "success", "count": 1, "downloaded": [str(pdf)], "failed": 0, "skipped": 0}

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            pass

        async def extract(self, *args, **kwargs):
            ctx.bump("extractor.extract")
            return {"status": "success", "unique_terms": 2, "processed_files": 1}

        async def extract_targeted(self, *args, **kwargs):
            ctx.bump("extractor.extract_targeted")
            return {
                "status": "success",
                "extraction_mode": "targeted",
                "unique_terms": 2,
                "processed_files": 1,
                "processed_pages_total": 2,
                "processed_pages_with_terms": 1,
                "pdf_results": [
                    {
                        "filename": "candidate_one.pdf",
                        "extraction_state": "partial",
                        "selected_pages": [1, 2],
                        "processed_pages_total": 2,
                        "processed_pages_with_terms": 1,
                        "source_pages_total": 8,
                    }
                ],
            }

    class FakeDebate:
        def __init__(self, *args, **kwargs):
            pass

        async def decide(self, question, retrieval_probe, candidates=None, round_no=1):
            ctx.bump("debate.decide")
            if retrieval_probe.get("sufficient"):
                return {
                    "hypothesis": "KG enough",
                    "objections": [],
                    "selected_action": "answer_from_kg",
                    "reason": "KG evidence sufficient",
                    "candidate_titles": [],
                    "candidate_indices": [],
                }
            return {
                "hypothesis": "Candidate one fills the gap",
                "objections": ["Candidate two is weaker"],
                "selected_action": "download_selected",
                "reason": "Candidate one has the best abstract evidence",
                "candidate_titles": ["Candidate paper one"],
                "candidate_indices": [0],
            }

    class FakeChatClient:
        model = "fake-contract-model"

        def chat(self, prompt, *args, **kwargs):
            ctx.bump("llm.chat")
            text = str(prompt)
            if "You classify fresh turns" in text:
                turn = text.rsplit("USER_TURN:", 1)[-1].strip().lower()
                label = "mundane_conversation" if turn.startswith("hello") else "scientific_or_uncertain"
                return json.dumps({"classification": label, "reason": "fake classifier"})
            return "Synthetic deterministic model text."

    def fake_make_chat_client(*args, **kwargs):
        return FakeChatClient()

    return FakeRetrieval, FakeDownload, FakeExtractor, FakeDebate, fake_make_chat_client


def _install_splash_fakes(monkeypatch, ctx: Ctx, kg_update) -> None:
    """In-memory Splash store mirroring the baseline kg_update.splash_* calls."""
    for thing in SEED_GRAPH["things"]:
        sid = "splash-" + thing["id"].split(":", 1)[1]
        ctx.splash_entities[sid] = {
            "id": sid,
            "entityType": thing["category"],
            "name": thing["name"],
            "uri": thing["id"],
            "properties": {"matkg_id": thing["id"]},
        }
    ctx.splash_links.append(
        {"id": "link-1", "subjectId": "splash-TermA", "predicate": "rel:has_code_snippet", "objectId": "splash-Code1"}
    )

    def find_entity(matkg_id, **kwargs):
        for entity in ctx.splash_entities.values():
            props = entity.get("properties") or {}
            if entity.get("uri") == matkg_id or props.get("matkg_id") == matkg_id or entity["id"] == matkg_id:
                return dict(entity)
        return None

    def update_entity(entity_id, *, name=None, entity_type=None, properties=None, **kwargs):
        ctx.bump("splash.update_entity")
        entity = ctx.splash_entities[entity_id]
        if name is not None:
            entity["name"] = name
        if entity_type is not None:
            entity["entityType"] = entity_type
        if properties:
            props = dict(entity.get("properties") or {})
            props.update(properties)
            entity["properties"] = props
        return dict(entity)

    def create_entity(*, entity_type, name, uri=None, properties=None, **kwargs):
        ctx.bump("splash.create_entity")
        entity_id = f"splash-new-{len(ctx.splash_entities) + 1}"
        entity = {"id": entity_id, "entityType": entity_type, "name": name, "uri": uri,
                  "properties": dict(properties or {})}
        ctx.splash_entities[entity_id] = entity
        return dict(entity)

    def create_link(*, subject_id, predicate, object_id, **kwargs):
        ctx.bump("splash.create_link")
        link = {"id": f"link-new-{len(ctx.splash_links) + 1}", "subjectId": subject_id,
                "predicate": predicate, "objectId": object_id}
        ctx.splash_links.append(link)
        return dict(link)

    def find_links(*, subject_id=None, predicate=None, object_id=None, **kwargs):
        out = []
        for link in ctx.splash_links:
            if subject_id and link["subjectId"] != subject_id:
                continue
            if predicate and link["predicate"] != predicate:
                continue
            if object_id and link["objectId"] != object_id:
                continue
            out.append(dict(link))
        return out

    def delete_link(link_id, **kwargs):
        ctx.bump("splash.delete_link")
        before = len(ctx.splash_links)
        ctx.splash_links[:] = [link for link in ctx.splash_links if link["id"] != link_id]
        return len(ctx.splash_links) < before

    def load_splash_graph(**kwargs):
        ctx.bump("splash.load_graph")
        return json.loads(json.dumps(SEED_GRAPH))

    def rebuild_kg(terms, kg):
        ctx.bump("kg_update.rebuild_kg")
        ctx.kg_rebuilt = True
        data = json.loads(json.dumps(SEED_GRAPH))
        Path(kg).parent.mkdir(parents=True, exist_ok=True)
        Path(kg).write_text(json.dumps(data), encoding="utf-8")
        return {"status": "success", "nodes": len(data["things"]), "edges": len(data["associations"])}

    def splash_reimport(*args, **kwargs):
        ctx.bump("kg_update.splash_reimport")
        return {"status": "success"}

    monkeypatch.setattr(kg_update, "splash_find_entity_by_matkg_id", find_entity)
    monkeypatch.setattr(kg_update, "splash_update_entity", update_entity)
    monkeypatch.setattr(kg_update, "splash_create_entity", create_entity)
    monkeypatch.setattr(kg_update, "splash_create_link", create_link)
    monkeypatch.setattr(kg_update, "splash_find_links", find_links)
    monkeypatch.setattr(kg_update, "splash_delete_link", delete_link)
    monkeypatch.setattr(kg_update, "load_splash_graph", load_splash_graph)
    monkeypatch.setattr(kg_update, "rebuild_kg", rebuild_kg)
    monkeypatch.setattr(kg_update, "splash_reimport", splash_reimport)


class FakePlanner:
    """Deterministic planning model for the T03B supervisor route (F2W_RUNTIME=langgraph).

    Calls ``answer_from_knowledge`` with the user's question, as QA_DELEGATION_PROMPT asks.
    Kept apart from ``Ctx.calls`` so the recorded fake call counts stay as frozen.
    """

    model_name = "fake-planner"

    def __init__(self, ctx: "Ctx") -> None:
        self.ctx = ctx
        self.tools: List[str] = []

    def bind_tools(self, tools, **kwargs):
        self.tools = [t["function"]["name"] for t in tools]
        return self

    def _reply(self, messages):
        from langchain_core.messages import AIMessage

        question = next((m.content for m in reversed(messages) if getattr(m, "type", "") == "human"), "")
        self.ctx.planner_calls.append({"tools": list(self.tools), "question": question})
        n = len(self.ctx.planner_calls)
        return AIMessage(content="", tool_calls=[{"name": "answer_from_knowledge", "args": {"question": question},
                                                  "id": f"plan-{n}", "type": "tool_call"}])

    def invoke(self, messages, *args, **kwargs):
        return self._reply(messages)

    async def ainvoke(self, messages, *args, **kwargs):
        return self._reply(messages)


RUNTIME_FIXTURE_DIR = FIXTURE_DIR / "runtime"


def expected_response(fid: str, recorded: Dict[str, Any],
                      environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The expected response of ``fid`` for the runtime mode in ``environ`` (H2 Q2, T03B).

    Default mode: the recorded ``http/<fid>.json`` response. ``F2W_RUNTIME=langgraph``:
    ``runtime/<fid>.langgraph[.direct].json`` when one exists (only ``traces_get``: the
    trace's ``env.settings`` records ``F2W_RUNTIME``, and the supervisor route's planning
    call appears in ``llm_calls``); an explicit ``F2W_QA_ROUTE`` is added to that snapshot.
    """
    env = os.environ if environ is None else environ
    if (env.get("F2W_RUNTIME") or "").strip().lower() != "langgraph":
        return recorded
    route = (env.get("F2W_QA_ROUTE") or "").strip()
    name = f"{fid}.langgraph{'.direct' if route.lower() == 'direct' else ''}.json"
    path = RUNTIME_FIXTURE_DIR / name
    if not path.exists():
        return recorded
    expected = json.loads(path.read_text(encoding="utf-8"))
    if route:
        expected["json"]["env"]["settings"]["F2W_QA_ROUTE"] = route
    return expected


def _fake_tiled_lookup():
    from app.modules.tiled_graph import TiledLookupGraph, TiledLookupResult

    def lookup(node_id, **kwargs):
        if "esaf-contract" not in str(node_id).lower():
            return TiledLookupResult(status="disabled", fallback="json_kg")
        kg = TiledLookupGraph()
        kg.add_node({"id": "beamline:ESAF-contract", "name": "ESAF contract", "category": "ESAF",
                     "properties": {"esaf_number": "contract"}})
        kg.add_node({"id": "beamline:Sample-contract", "name": "Sample contract", "category": "Sample"})
        kg.add_edge("beamline:ESAF-contract", "rel:hasSample", "beamline:Sample-contract")
        return TiledLookupResult(graph=kg, status="ok", source="fake")

    return lookup


def install(monkeypatch, workspace: Path, repo_root: Path) -> Ctx:
    """Isolate env/cwd, block sockets and patch every external dependency."""
    # kg_rag_api.py:81, extract_terms.py:37 and f2w_agent/cli.py call load_dotenv() at
    # import. Refuse to import app code next to a .env (live credentials).
    for candidate in (repo_root / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            raise RuntimeError(
                f"refusing to run the legacy UI contract harness next to {candidate}; "
                "run it from a clean export (git archive) without .env"
            )
    workspace.mkdir(parents=True, exist_ok=True)
    for name in _SCRUB_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KG_RAG_RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setenv("KG_RAG_GRAPH_SOURCE", "json")
    monkeypatch.setenv("F2W_SOURCE_RAG", "0")
    monkeypatch.setenv("F2W_LIVE_TILED", "0")
    monkeypatch.setenv("F2W_TRACE_ENABLED", "1")
    monkeypatch.setenv("F2W_TRACE_DIR", str(workspace / "traces"))
    monkeypatch.setenv("HYBRID_RAG_LITERATURE_INDEX", str(workspace / "missing-lit-index"))
    monkeypatch.setenv("HYBRID_RAG_OPS_INDEX", str(workspace / "missing-ops-index"))
    monkeypatch.setenv("MPLCONFIGDIR", str(workspace / "mpl"))
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _blocked_connect(None, *a))

    kg_dir = workspace / "storage" / "kg"
    kg_dir.mkdir(parents=True, exist_ok=True)
    (kg_dir / GRAPH_FILENAME).write_text(json.dumps(SEED_GRAPH, indent=2), encoding="utf-8")
    monkeypatch.chdir(workspace)

    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent import trace as trace_mod
    from app.modules.term_extractor import clients

    ctx = Ctx(workspace=workspace, repo_root=repo_root)
    retrieval, download, extractor, debate, make_client = make_fakes(ctx)
    monkeypatch.setattr(api_mod, "RetrievalAgent", retrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", download)
    monkeypatch.setattr(api_mod, "ExtractorAgent", extractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", debate)
    monkeypatch.setattr(clients, "make_chat_client", make_client)
    monkeypatch.setattr(api_mod, "lookup_experiment_identity", _fake_tiled_lookup())
    monkeypatch.setattr(
        api_mod, "_search_openalex_publications",
        lambda *a, **k: (_ for _ in ()).throw(NetworkBlocked("OpenAlex disabled in harness")),
    )
    # trace.git_sha shells out to git; keep it deterministic.
    monkeypatch.setattr(trace_mod, "_GIT_SHA", None, raising=False)
    monkeypatch.setattr(trace_mod, "_GIT_SHA_READY", True, raising=False)
    _install_splash_fakes(monkeypatch, ctx, api_mod.kg_update)
    from app.modules.runtime_bridge import legacy_adapter

    planner = FakePlanner(ctx)
    monkeypatch.setattr(legacy_adapter, "planning_model_provider", lambda pipeline: (lambda: planner))
    return ctx


def make_ui_app(ctx: Ctx, kg_mode: str = "splash", subdir: str = "run"):
    """Construct the 19-route UI app exactly as the launcher does (cli.py:175-177)."""
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    graph = str(Path("storage/kg") / GRAPH_FILENAME)
    cfg = CoordinatorConfig(
        backend="cborg",
        model="lbl/cborg-chat",
        graph=graph,
        kg_mode=kg_mode,
        workdir=ctx.workspace / subdir,
        max_rounds=3,
        workflow_mode="agentic",
        extraction_mode="targeted",
        targeted_max_pages=6,
        download_delay_seconds=0.0,
        validate_downloads=False,
        workers=1,
    )
    return api_mod.create_app(cfg)


def make_model_app(monkeypatch, ctx: Ctx):
    """Construct kg_rag_api.create_fastapi_app with a fake chat client."""
    from app.modules import kg_rag_api

    class FakeModelClient:
        model = "fake-contract-model"

        async def chat(self, *args, **kwargs):
            return "Synthetic KG-RAG answer."

    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical", raising=False)
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "", raising=False)
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: FakeModelClient())
    graph = ctx.workspace / "storage" / "kg" / GRAPH_FILENAME
    return kg_rag_api.create_fastapi_app(str(graph), backend="ollama")


# --------------------------------------------------------------------------- sanitizing


def sanitize_text(text: str, ctx: Ctx) -> str:
    """Replace workspace/repo paths and host names with stable placeholders."""
    roots: List[Tuple[str, str]] = []
    for raw, token in ((ctx.workspace, WORKSPACE_TOKEN), (ctx.repo_root, REPO_TOKEN)):
        for variant in {str(raw), str(Path(raw).resolve())}:
            roots.append((variant, token))
            if variant.startswith("/private/"):
                roots.append((variant[len("/private"):], token))
    roots.sort(key=lambda item: len(item[0]), reverse=True)
    for raw, token in roots:
        text = text.replace(raw, token)
    host = socket.gethostname()
    if host:
        text = text.replace(host, HOST_TOKEN)
    home = os.environ.get("HOME")
    if home and len(home) > 3:
        text = text.replace(home, "<HOME>")
    return text


def sanitize_obj(obj: Any, ctx: Ctx) -> Any:
    return json.loads(sanitize_text(json.dumps(obj, ensure_ascii=False), ctx))


# --------------------------------------------------------------------------- shapes


def shape(value: Any) -> Any:
    """Recursive type skeleton: dict -> {key: shape}, list -> [union of item shapes]."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        seen: List[Any] = []
        for item in value:
            s = shape(item)
            if s not in seen:
                seen.append(s)
        return seen
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return "str"


SSE_FRAME = re.compile(r"event: (?P<event>[a-z_]+)\ndata: (?P<data>[^\n]*)\n\n")


def parse_sse(raw: bytes) -> List[Dict[str, Any]]:
    """Strictly parse the baseline LF framing; raise on any stray bytes."""
    text = raw.decode("utf-8")
    pos = 0
    events: List[Dict[str, Any]] = []
    while pos < len(text):
        match = SSE_FRAME.match(text, pos)
        if not match:
            raise AssertionError(f"non-baseline SSE framing at byte {pos}: {text[pos:pos + 80]!r}")
        events.append({"event": match.group("event"), "data": json.loads(match.group("data"))})
        pos = match.end()
    return events


# --------------------------------------------------------------------------- flows

# Each step: (fixture_id, method, path, kwargs, notes). Steps of one flow run in
# order against one app instance, because pending state is server-side.
SID = "contract-session-1"


def flows() -> Dict[str, Dict[str, Any]]:
    return {
        "splash_app": {
            "kg_mode": "splash",
            "steps": [
                ("health", "GET", "/health", {}, "api.py:6625-6642"),
                ("cors_preflight_allowed_origin", "OPTIONS", "/chat/stream",
                 {"headers": {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                              "Access-Control-Request-Headers": "content-type"}},
                 "DEFAULT_CORS_ORIGINS api.py:6603-6608, allow_credentials=True api.py:6616-6622"),
                ("cors_preflight_other_origin", "OPTIONS", "/chat/stream",
                 {"headers": {"Origin": "http://example.invalid", "Access-Control-Request-Method": "POST"}},
                 "origin not in the default list is refused"),
                ("cors_simple_get_allowed_origin", "GET", "/settings",
                 {"headers": {"Origin": "http://127.0.0.1:5175"}}, "credentialed CORS response headers"),
                ("settings_get", "GET", "/settings", {}, "api.py:6645-6647"),
                ("graph_get", "GET", "/graph", {}, "api.py:6671-6673"),
                ("graph_nodes_search", "POST", "/graph/nodes/search",
                 {"json": {"query": "resonant scattering", "limit": 5}}, "api.py:6676-6683"),
                ("graph_nodes_search_422_empty", "POST", "/graph/nodes/search",
                 {"json": {"query": "", "limit": 5}}, "GraphNodeSearchRequest min_length=1 (api.py:294-297)"),
                ("graph_node_get", "GET", "/graph/node/matkg:TermA", {}, "api.py:6716-6748"),
                ("graph_node_get_404", "GET", "/graph/node/matkg:Missing", {}, "api.py:6747-6748"),
                ("graph_neighborhood_tiled", "GET", "/graph/neighborhood/beamline:ESAF-contract",
                 {"params": {"hops": 3}}, "api.py:6686-6694 with faked lookup_experiment_identity"),
                ("graph_neighborhood_404", "GET", "/graph/neighborhood/matkg:TermA", {},
                 "api.py:6693-6694 (live Tiled lookup returns no graph)"),
                ("publications_search", "POST", "/publications/search",
                 {"json": {"query": "resonant scattering", "max_results": 5, "include_external": False}},
                 "api.py:6806-6815; real krag.KnowledgeGraph + lexical retrieve_nodes"),
                ("chat_stream_direct_response", "POST", "/chat/stream",
                 {"json": {"message": "hello there", "session_id": SID, "client_turn_id": "turn-direct-1"}},
                 "api.py:6818-6908; orchestrator classifies mundane -> direct_response"),
                ("chat_stream_answered", "POST", "/chat/stream",
                 {"json": {"message": "What is Term A resonant scattering?", "session_id": SID,
                           "client_turn_id": "turn-answered-1",
                           "messages": [{"role": "user", "content": "hello there"},
                                        {"role": "assistant", "content": ""}],
                           "ui_settings": {"graphSource": "splash_links"},
                           "source_rag": False, "use_live_tiled": False}},
                 "answered path; blank assistant history accepted (api.py:116-121)"),
                ("chat_stream_insufficient_to_download", "POST", "/chat/stream",
                 {"json": {"message": "Explain the insufficient synthetic topic", "session_id": SID,
                           "client_turn_id": "turn-insufficient-1"}},
                 "insufficient -> candidate search -> debate -> awaiting_download_decision"),
                ("chat_action_stream_error_missing_candidate", "POST", "/chat/action/stream",
                 {"json": {"decision": "yes", "kind": "download", "session_id": SID,
                           "client_turn_id": "turn-action-err-1", "parent_turn_id": "turn-insufficient-1"}},
                 "download yes without candidate_index -> ValueError -> SSE error stream_error (api.py:6973-6981)"),
                ("chat_action_stream_download_yes", "POST", "/chat/action/stream",
                 {"json": {"decision": "yes", "kind": "download", "candidate_index": 0, "session_id": SID,
                           "client_turn_id": "turn-action-dl-1", "parent_turn_id": "turn-insufficient-1"}},
                 "approved download -> awaiting_extraction_decision"),
                ("chat_action_stream_extraction_yes", "POST", "/chat/action/stream",
                 {"json": {"decision": "yes", "kind": "extraction", "session_id": SID,
                           "client_turn_id": "turn-action-ex-1", "parent_turn_id": "turn-action-dl-1"}},
                 "approved targeted extraction -> rebuild -> splash_reimport -> reload -> answered"),
                ("chat_action_no_pending", "POST", "/chat/action",
                 {"json": {"decision": "yes", "kind": "extraction", "session_id": SID,
                           "client_turn_id": "turn-action-none-1"}},
                 "api.py:6790-6803; status no_pending_action"),
                ("chat_stream_error_empty_json_selection", "POST", "/chat/stream",
                 {"json": {"message": "What is Term A?", "session_id": SID, "client_turn_id": "turn-err-1",
                           "graph_source": "json", "json_graph_paths": [" "]}},
                 "per-turn JSON override whose paths normalize to [] -> ValueError('JSON graph mode requires "
                 "a graph file') (api.py:5061) after orchestrator progress -> SSE error stream_error"),
                ("chat_stream_error_malformed_verdict", "POST", "/chat/stream",
                 {"json": {"message": "Please crash the verdict", "session_id": SID,
                           "client_turn_id": "turn-err-2"}},
                 "FAULT INJECTION: fake retrieval returns a non-dict verdict; AttributeError escapes "
                 "_ask_locked after retrieval_started -> SSE error stream_error (api.py:6882-6890)"),
                ("chat_stream_json_override_missing_file_falls_back", "POST", "/chat/stream",
                 {"json": {"message": "What is Term A?", "session_id": SID, "client_turn_id": "turn-err-3",
                           "graph_source": "json", "json_graph_path": "storage/kg/does_not_exist.json"}},
                 "missing per-turn JSON file is skipped and falls back to graph_path() (api.py:5063-5069); "
                 "NOT a ValueError, contrary to f2w_llm_qa_baseline ST-12"),
                ("chat_stream_retrieval_error", "POST", "/chat/stream",
                 {"json": {"message": "Please explode the retrieval", "session_id": SID,
                           "client_turn_id": "turn-reterr-1"}},
                 "retrieval raises -> ChatResponse status retrieval_error sent as SSE event 'error'"),
                ("chat_answered", "POST", "/chat",
                 {"json": {"message": "What is Term A resonant scattering?", "session_id": "contract-session-2",
                           "client_turn_id": "turn-chat-1"}}, "api.py:6771-6787 non-stream answered"),
                ("chat_insufficient_to_download", "POST", "/chat",
                 {"json": {"message": "Explain the insufficient synthetic topic", "session_id": "contract-session-3",
                           "client_turn_id": "turn-chat-ins-1"}}, "non-stream awaiting_download_decision"),
                ("chat_action_decline", "POST", "/chat/action",
                 {"json": {"decision": "no", "kind": "download", "session_id": "contract-session-3",
                           "client_turn_id": "turn-chat-decline-1", "parent_turn_id": "turn-chat-ins-1"}},
                 "decline -> stopped_by_user"),
                ("chat_400_empty_json_selection", "POST", "/chat",
                 {"json": {"message": "What is Term A?", "session_id": "contract-session-2",
                           "client_turn_id": "turn-chat-400", "graph_source": "json",
                           "json_graph_paths": [" "]}},
                 "same ValueError as the stream case -> HTTP 400 detail on the non-stream route (api.py:6786-6787)"),
                ("chat_500_malformed_verdict", "POST", "/chat",
                 {"json": {"message": "Please crash the verdict", "session_id": "contract-session-2",
                           "client_turn_id": "turn-chat-500"}},
                 "FAULT INJECTION: non-ValueError exceptions are not mapped on /chat -> HTTP 500 plain text"),
                ("chat_insufficient_for_failed_download", "POST", "/chat",
                 {"json": {"message": "Explain the insufficient synthetic topic", "session_id": "contract-session-4",
                           "client_turn_id": "turn-chat-ins-2"}}, "set up a download pending in session 4"),
                ("chat_action_stream_download_failed_retry", "POST", "/chat/action/stream",
                 {"json": {"decision": "yes", "kind": "download", "candidate_index": 1,
                           "session_id": "contract-session-4", "client_turn_id": "turn-action-dlfail-1",
                           "parent_turn_id": "turn-chat-ins-2"}},
                 "fake download of candidate 1 fails -> awaiting_download_decision again, index marked unavailable"),
                ("chat_action_stream_decline", "POST", "/chat/action/stream",
                 {"json": {"decision": "no", "kind": "download", "session_id": "contract-session-4",
                           "client_turn_id": "turn-action-decline-1", "parent_turn_id": "turn-action-dlfail-1"}},
                 "decline over the stream route -> complete stopped_by_user"),
                ("chat_422_bad_session", "POST", "/chat",
                 {"json": {"message": "x", "session_id": "../escape"}}, "ChatRequest session_id pattern"),
                ("chat_stream_422_empty_message", "POST", "/chat/stream",
                 {"json": {"message": ""}}, "validation happens before the stream starts"),
                ("graph_node_patch_fields_snippets_relationship", "PATCH", "/graph/node/matkg:TermA",
                 {"json": {
                     "label": "Term A edited", "type": "Technique", "description": "edited description",
                     "publications": [{"paper_title": "Edited Paper", "doi": "10.0000/contract.edit",
                                       "source_paper": "edited.pdf", "publication_year": 2025}],
                     "linked_code_snippets": [
                         {"id": "matkg:Code1", "label": "gamma snippet edited", "function_name": "gamma",
                          "code_language": "python", "code_snippet": "def gamma():\n    return 1",
                          "_action": "upsert"},
                         {"label": "beta snippet", "function_name": "beta", "code_language": "python",
                          "code_snippet": "def beta():\n    return 2", "_action": "upsert"}],
                     "relationship_updates": [
                         {"action": "add", "source": "matkg:Other", "predicate": "Affects",
                          "target": "matkg:TermA"}]}},
                 "api.py:6751-6760 / update_graph_node 2978-3046 with in-memory Splash fake"),
                ("graph_node_patch_unlink_and_remove", "PATCH", "/graph/node/matkg:TermA",
                 {"json": {
                     "linked_code_snippets": [{"id": "matkg:Code1", "_action": "unlink"}],
                     "relationship_updates": [
                         {"action": "remove", "source": "matkg:Other", "predicate": "rel:affects",
                          "target": "matkg:TermA"}]}},
                 "unlink + relationship remove; snippets omitted from the list are also unlinked (api.py:3331-3332)"),
                ("graph_node_patch_400_self_relationship", "PATCH", "/graph/node/matkg:TermA",
                 {"json": {"relationship_updates": [
                     {"action": "add", "source": "matkg:TermA", "predicate": "rel:x", "target": "matkg:TermA"}]}},
                 "api.py:3062-3063"),
                ("graph_node_patch_422_bad_action", "PATCH", "/graph/node/matkg:TermA",
                 {"json": {"linked_code_snippets": [{"id": "matkg:Code1", "_action": "delete"}]}},
                 "LinkedCodeSnippetUpdate _action pattern upsert|unlink"),
                ("graph_upload", "POST", "/graph/upload",
                 {"json": {"filename": "contract_upload", "graph": {
                     "things": [{"id": "matkg:U1", "name": "Uploaded node", "category": "Thing"}],
                     "associations": []}}}, "api.py:6763-6768 / save_uploaded_graph 3387-3394"),
                ("graph_upload_400_invalid", "POST", "/graph/upload",
                 {"json": {"filename": "bad.json", "graph": {"nodes": []}}}, "api.py:3389-3390"),
                ("graph_node_get_uploaded", "GET", "/graph/node/matkg:U1",
                 {"params": {"json_graph_path": "<WORKSPACE>/run/uploads/contract_upload.json"}},
                 "json_graph_path query restricted to storage/kg or uploads (api.py:3396-3412)"),
                ("traces_get", "GET", "/traces/turn-answered-1", {}, "api.py:7002-7007; tracing enabled"),
                ("traces_get_404", "GET", "/traces/turn-does-not-exist", {}, "api.py:7005-7006"),
                ("telemetry_ui", "POST", "/telemetry/ui",
                 {"json": {"events": [{"type": "citation_click", "turn_id": "turn-answered-1",
                                       "ts": "2026-01-01T00:00:00Z", "data": {"node_id": "matkg:TermA"}}]}},
                 "api.py:7010-7016"),
                ("annotations", "POST", "/annotations",
                 {"json": {"turn_id": "turn-answered-1", "verdict": "pass", "failure_tags": [],
                           "note": "contract", "annotator": "contract", "ts": "2026-01-01T00:00:00Z"}},
                 "api.py:7019-7029"),
                ("annotations_422_bad_verdict", "POST", "/annotations",
                 {"json": {"turn_id": "turn-answered-1", "verdict": "maybe"}}, "AnnotationRequest verdict pass|fail"),
                ("session_reset_named", "POST", "/session/reset", {"json": {"session_id": SID}},
                 "api.py:6658-6660"),
                ("session_reset_legacy", "POST", "/session/reset", {}, "no body -> __legacy__ session"),
                ("session_reset_422", "POST", "/session/reset", {"json": {"session_id": "../escape"}},
                 "SessionResetRequest pattern"),
                ("session_delete", "DELETE", f"/session/{SID}", {}, "api.py:6663-6668"),
                ("session_delete_400_legacy", "DELETE", "/session/__legacy__", {},
                 "legacy key cannot be deleted (api.py:4248-4250)"),
                ("session_delete_400_invalid", "DELETE", "/session/bad.id", {},
                 "no pydantic pattern on the path param; service ValueError -> 400"),
                ("settings_put_ollama", "PUT", "/settings",
                 {"json": {"backend": "ollama", "workflow_mode": "agentic", "extraction_mode": "full",
                           "targeted_max_pages": 4, "kg_query_max_nodes": 250, "kg_query_hops": 3,
                           "source_rag": False, "use_live_tiled": False}},
                 "api.py:6650-6655 / apply_settings 2726-2869"),
                ("settings_put_422_bad_graph_source", "PUT", "/settings",
                 {"json": {"graph_source": "splash_links"}},
                 "the browser token splash_links is NOT accepted on the wire; graphSourceToApi maps it"),
                ("settings_put_json_mode", "PUT", "/settings",
                 {"json": {"backend": "cborg", "model": "lbl/cborg-chat", "graph_source": "json",
                           "json_graph_path": f"storage/kg/{GRAPH_FILENAME}",
                           "json_graph_paths": [f"storage/kg/{GRAPH_FILENAME}"]}},
                 "switch to JSON mode, UI settingsToApiPayload shape"),
                ("graph_node_patch_400_json_mode", "PATCH", "/graph/node/matkg:TermA",
                 {"json": {"label": "should fail"}}, "JSON mode is read-only (api.py:2980-2981)"),
                ("graph_get_json_mode", "GET", "/graph", {}, "JSON-mode graph payload"),
                ("settings_put_back_to_splash", "PUT", "/settings", {"json": {"graph_source": "splash"}},
                 "UI graphSourceToApi('splash_links') == 'splash'"),
            ],
        },
    }


MODEL_STEPS = [
    ("model_api_chat", "POST", "/api/chat",
     {"json": {"model": "kg-rag:latest", "messages": [{"role": "user", "content": "What is Term A?"}]}},
     "kg_rag_api.py:246-259"),
    ("model_api_chat_400", "POST", "/api/chat", {"json": {"messages": []}}, "kg_rag_api.py:251-252"),
    ("model_openai_chat_completions", "POST", "/v1/chat/completions",
     {"json": {"model": "kg-rag:latest", "messages": [{"role": "user", "content": "What is Term A?"}]}},
     "kg_rag_api.py:262-282"),
    ("model_openai_chat_completions_400", "POST", "/v1/chat/completions", {"json": {"messages": []}},
     "kg_rag_api.py:267-268"),
    ("model_api_tags", "GET", "/api/tags", {}, "kg_rag_api.py:285-291"),
    ("model_api_version", "GET", "/api/version", {}, "kg_rag_api.py:294-296"),
    ("model_v1_models", "GET", "/v1/models", {}, "kg_rag_api.py:299-311"),
    ("model_api_ps", "GET", "/api/ps", {}, "kg_rag_api.py:314-316"),
]

# Values that change per run (wall clock); replaced by a token before comparison.
VOLATILE_KEYS = {"started_at", "finished_at", "elapsed_seconds", "created"}
VOLATILE_TOKEN = "<VOLATILE>"
_CHATCMPL = re.compile(r"^chatcmpl-\d+$")


def normalize_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (VOLATILE_TOKEN if (k in VOLATILE_KEYS or k.endswith("_ms")) else normalize_volatile(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [normalize_volatile(v) for v in value]
    if isinstance(value, str) and _CHATCMPL.match(value):
        return "chatcmpl-" + VOLATILE_TOKEN
    return value


CORS_HEADERS = ("access-control-allow-origin", "access-control-allow-credentials",
                "access-control-allow-methods", "access-control-allow-headers", "vary")


def resolve_placeholders(value: Any, ctx: Ctx) -> Any:
    """Turn <WORKSPACE> tokens in recorded requests back into real paths."""
    return json.loads(json.dumps(value).replace(WORKSPACE_TOKEN, str(ctx.workspace)))


def client_for(app):
    """TestClient that records 500s instead of re-raising them."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def perform(client, ctx: Ctx, method: str, path: str, kwargs: Dict[str, Any]):
    real = resolve_placeholders(kwargs, ctx)
    return client.request(method, path, **real)


def response_record(resp, ctx: Ctx) -> Dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    record: Dict[str, Any] = {"status_code": resp.status_code, "content_type": ctype}
    cors = {h: resp.headers[h] for h in CORS_HEADERS if h in resp.headers}
    if cors:
        record["cors_headers"] = cors
    if ctype.startswith("text/event-stream"):
        record["sse_events"] = [
            {"event": e["event"], "data": sanitize_obj(e["data"], ctx)} for e in parse_sse(resp.content)
        ]
    else:
        try:
            record["json"] = normalize_volatile(sanitize_obj(resp.json(), ctx))
        except ValueError:
            record["text"] = sanitize_text(resp.text, ctx)
    return record
