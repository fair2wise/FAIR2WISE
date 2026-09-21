"""Unit tests for hybrid source RAG (PaperRetriever → EvidenceRanker → ContextBuilder)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.modules.f2w_agent.multi_kg import ScoredHit
from app.modules.f2w_agent.retrieval_agent import RetrievalAgent
from app.modules.hybrid_rag.chunker import chunk_text_document
from app.modules.hybrid_rag.context import ContextBuilder
from app.modules.hybrid_rag.evidence import Evidence
from app.modules.hybrid_rag.index_store import SourceIndex, write_index
from app.modules.hybrid_rag.pipeline import (
    HybridSourceRAG,
    build_literature_index,
    build_ops_index,
    hybrid_rag_enabled,
)
from app.modules.hybrid_rag.ranker import EvidenceRanker
from app.modules.hybrid_rag.retriever import PaperRetriever
from app.modules.hybrid_rag.sources import collect_ops_docs, select_literature_docs, work_id_for_paper


def _write_index(root: Path, *, corpus: str, doc_id: str, text: str, heading: str = "") -> None:
    locator = f"{doc_id} §{heading}" if heading else doc_id
    write_index(
        root,
        [
            {
                "chunk_id": f"{corpus}:{doc_id}:c0",
                "corpus": corpus,
                "work_id": "doi:10.example/p3ht" if corpus == "literature" else None,
                "doc_id": doc_id,
                "path": f"papers/{doc_id}" if corpus == "literature" else f"docs/{doc_id}",
                "page": 3 if corpus == "literature" else None,
                "locator": locator if corpus != "literature" else "p.3",
                "heading": heading,
                "text": text,
                "chunker_version": "1.0.0",
                "title": doc_id,
                "doi": "10.example/p3ht" if corpus == "literature" else "",
                "url": "",
            }
        ],
        index_name=corpus,
        corpus_revision="test",
    )


def test_ops_chunks_use_path_heading_locator(tmp_path):
    md = tmp_path / "bl1101knowledge.md"
    md.write_text("# Beam path\n\nM103 is the KB pair between mono and exit slits.\n", encoding="utf-8")
    chunks = chunk_text_document(md, doc_id="knowledge", corpus="bl1101")
    assert chunks
    assert chunks[0]["corpus"] == "bl1101"
    assert chunks[0]["page"] is None
    assert "§" in chunks[0]["locator"] or chunks[0]["heading"] == "Beam path"


def test_paper_retriever_skips_missing_index(tmp_path):
    retriever = PaperRetriever.from_roots(tmp_path / "no-lit", tmp_path / "no-ops")
    hits = retriever.retrieve("what is RSoXS")
    assert hits == []


def test_literature_and_ops_indexes_stay_separate(tmp_path):
    lit_root = tmp_path / "literature"
    ops_root = tmp_path / "ops"
    _write_index(
        lit_root,
        corpus="literature",
        doc_id="p3ht.pdf",
        text="P3HT dopant contrast measured with P-RSoXS at the carbon K-edge.",
    )
    _write_index(
        ops_root,
        corpus="bl1101",
        doc_id="blueprint.html",
        text="M103 Kirkpatrick-Baez pair focuses the beam onto the exit slits.",
        heading="M103",
    )
    lit_hits = SourceIndex(lit_root).search("P3HT")
    ops_hits = SourceIndex(ops_root).search("M103")
    assert lit_hits
    assert ops_hits
    assert all(chunk["corpus"] == "literature" for chunk, _, _ in lit_hits)
    assert all(chunk["corpus"] == "bl1101" for chunk, _, _ in ops_hits)
    mixed = PaperRetriever.from_roots(lit_root, ops_root).retrieve("beamline M103 P3HT")
    types = {item.source_type for item in mixed}
    assert types == {"literature", "ops"}
    assert {item.corpus for item in mixed} == {"literature", "bl1101"}


def test_ranker_keeps_tagged_objects_not_a_fused_blob():
    lit = Evidence(
        source_kind="paper_chunk",
        source_id="lit-1",
        score=1.2,
        text="P3HT domain spacing from resonant scattering.",
        source_type="literature",
        paper_id="p3ht.pdf",
        page=3,
        locator="p.3",
        provenance={"corpus": "literature"},
    )
    ops = Evidence(
        source_kind="paper_chunk",
        source_id="ops-1",
        score=1.1,
        text="M103 is the KB pair on the 11.0.1.2 beam path.",
        source_type="ops",
        paper_id="blueprint.html",
        heading="M103",
        locator="blueprint.html:10-20 §M103",
        provenance={"corpus": "bl1101"},
    )
    ranked = EvidenceRanker().rank([lit, ops], "how is M103 used", intent="layout")
    assert [item.source_id for item in ranked] == ["ops-1", "lit-1"]
    assert all(item.source_kind == "paper_chunk" for item in ranked)
    assert {item.source_type for item in ranked} == {"literature", "ops"}
    fused = " ".join(item.text for item in ranked)
    assert "P3HT" in fused and "M103" in fused
    assert ranked[0].text != fused
    assert ranked[0].corpus == "bl1101"
    assert ranked[1].corpus == "literature"


def test_context_builder_does_not_fuse_literature_and_ops():
    lit = Evidence(
        source_kind="paper_chunk",
        source_id="lit-1",
        score=1.0,
        text="CyRSoXS simulates polarized RSoXS patterns.",
        source_type="literature",
        paper_id="cyrsoxs.pdf",
        page=2,
        provenance={"corpus": "literature", "path": "papers/cyrsoxs.pdf"},
    )
    ops = Evidence(
        source_kind="paper_chunk",
        source_id="ops-1",
        score=1.0,
        text="AXIS-SXR-40 is the detector at the end of the beam path.",
        source_type="ops",
        heading="Detector",
        locator="als.html:1-20 §Detector",
        provenance={"corpus": "bl1101", "path": "als_11-0-1-2.html"},
    )
    ctx = ContextBuilder().build([lit, ops], intent="why_how")
    assert "### Literature source evidence" in ctx
    assert "### Beamline ops-doc evidence (bl1101)" in ctx
    assert "[PDF: cyrsoxs.pdf p.2]" in ctx
    assert "[OPS: als_11-0-1-2.html §Detector]" in ctx
    lit_part, ops_part = ctx.split("### Beamline ops-doc evidence (bl1101)")
    assert "CyRSoXS" in lit_part
    assert "AXIS-SXR-40" not in lit_part
    assert "AXIS-SXR-40" in ops_part
    assert "CyRSoXS" not in ops_part


def test_work_id_collapses_doi_versions():
    paper = {
        "doi": "10.1039/C8SC04537B",
        "arxiv_id": "1810.00001",
        "title": "CyRSoXS",
        "versions": [{"doi": "10.1039/C8SC04537B"}, {"arxiv_id": "1810.00001"}],
    }
    assert work_id_for_paper(paper) == "doi:10.1039/c8sc04537b"


def test_select_literature_gold_is_capped(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    papers = [
        {
            "title": f"P3HT RSoXS paper {i}",
            "doi": f"10.example/{i}",
            "pdf_path": str(pdf),
            "kg_primary": True,
            "kg_seed_rank": i,
        }
        for i in range(20)
    ]
    monkeypatch.setattr(
        "app.modules.hybrid_rag.sources.load_harvest_papers",
        lambda path: papers,
    )
    monkeypatch.setattr(
        "app.modules.hybrid_rag.sources.resolve_pdf_path",
        lambda paper, root: pdf,
    )
    selected = select_literature_docs(root=tmp_path, mode="gold", limit=5)
    assert len(selected) == 5
    assert len({doc["work_id"] for doc in selected}) == 5


def test_collect_ops_docs_never_includes_pdfs(tmp_path):
    (tmp_path / "papers/rsoxs/2024").mkdir(parents=True)
    (tmp_path / "papers/rsoxs/2024/paper.pdf").write_bytes(b"%PDF")
    pages = tmp_path / ".cache/bl1101/pages"
    pages.mkdir(parents=True)
    (pages / "als_11-0-1-2.html").write_text("<h1>11.0.1.2</h1><p>RSoXS beamline</p>", encoding="utf-8")
    docs = collect_ops_docs(root=tmp_path)
    assert docs
    assert all(Path(doc["path"]).suffix.lower() != ".pdf" for doc in docs)


def test_build_indexes_do_not_share_a_directory(tmp_path):
    pages = tmp_path / ".cache/bl1101/pages"
    pages.mkdir(parents=True)
    (pages / "als_11-0-1-2.html").write_text(
        "<h2>Energy</h2><p>165 to 1500 eV at ALS 11.0.1.2.</p>",
        encoding="utf-8",
    )
    lit = tmp_path / "idx/literature"
    ops = tmp_path / "idx/ops"
    lit_meta = build_literature_index(dest=lit, root=tmp_path, mode="gold", limit=1)
    ops_meta = build_ops_index(dest=ops, root=tmp_path)
    assert lit != ops
    assert "literature" in (lit_meta.get("index_name") or "literature")
    assert ops_meta.get("index_name") == "ops_bl1101"
    assert SourceIndex(lit).available or lit_meta.get("status") == "no_pdfs"
    assert SourceIndex(ops).available
    assert SourceIndex(ops).search("1500 eV")


def test_hybrid_rag_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("F2W_SOURCE_RAG", raising=False)
    monkeypatch.delenv("HYBRID_RAG_ENABLED", raising=False)
    assert hybrid_rag_enabled() is False
    monkeypatch.setenv("F2W_SOURCE_RAG", "1")
    assert hybrid_rag_enabled() is True
    monkeypatch.setenv("F2W_SOURCE_RAG", "0")
    monkeypatch.setenv("HYBRID_RAG_ENABLED", "1")
    assert hybrid_rag_enabled() is False


def test_hybrid_pack_survives_missing_indexes(tmp_path):
    rag = HybridSourceRAG(literature_root=tmp_path / "missing-lit", ops_root=tmp_path / "missing-ops")
    packed = rag.pack("what is RSoXS")
    assert packed["chunk_count"] == 0
    assert packed["source_context"] == ""
    assert packed["literature_available"] is False
    assert packed["ops_available"] is False


def test_retrieval_agent_missing_index_does_not_crash(monkeypatch):
    agent = RetrievalAgent(graph_source="json")
    kg = SimpleNamespace(
        nodes={"n1": {"source_papers": ["paper.pdf"], "description": "RSoXS", "category": "ExperimentalTechnique"}},
        out_edges={},
        graph_source_requested="json",
        graph_source_used="json",
        graph_id="rsoxs_v1",
        graph_label="science",
        context_calls=0,
    )

    def build_context(*args, **kwargs):
        kg.context_calls += 1
        return "Description: RSoXS"

    kg.build_context = build_context
    agent._kg = kg
    agent._graphs = {"rsoxs_v1": kg}
    agent._hybrid = HybridSourceRAG(
        literature_root=Path("/tmp/fair2wise-missing-lit-index"),
        ops_root=Path("/tmp/fair2wise-missing-ops-index"),
    )

    from app.modules.f2w_agent import retrieval_agent as ra

    monkeypatch.setattr(
        ra.krag,
        "retrieve_nodes",
        lambda question, kg, **kwargs: [SimpleNamespace(id="n1", evidence_ct=1, name="RSoXS", category="ExperimentalTechnique", score_prp=1.0)],
    )
    monkeypatch.setattr(ra.krag, "make_chat_client", lambda backend, model=None: SimpleNamespace(model="fake"))

    async def fake_llm(cli, messages, label):
        return '{"sufficient": true, "answer": "RSoXS is resonant soft X-ray scattering.", "missing_topics": []}'

    monkeypatch.setattr(ra.krag, "call_llm", fake_llm)
    result = asyncio.run(agent.query("what is RSoXS", source_rag=True))
    assert result["status"] == "success"
    assert result["hybrid_rag"]["literature_available"] is False
    assert result["hybrid_rag"]["ops_available"] is False
    assert result["sufficient"] is True


def test_query_default_off_does_not_call_paper_retriever(monkeypatch):
    agent = RetrievalAgent(graph_source="json", source_rag=False)
    kg = SimpleNamespace(
        nodes={"n1": {"source_papers": ["paper.pdf"], "description": "RSoXS", "category": "ExperimentalTechnique"}},
        out_edges={},
        graph_source_requested="json",
        graph_source_used="json",
        graph_id="rsoxs_v1",
        graph_label="science",
    )
    kg.build_context = lambda *a, **k: "Description: RSoXS"
    agent._kg = kg
    agent._graphs = {"rsoxs_v1": kg}

    from app.modules.f2w_agent import retrieval_agent as ra

    monkeypatch.setattr(
        ra.krag,
        "retrieve_nodes",
        lambda question, kg, **kwargs: [
            SimpleNamespace(id="n1", evidence_ct=1, name="RSoXS", category="ExperimentalTechnique", score_prp=1.0)
        ],
    )
    monkeypatch.setattr(ra.krag, "make_chat_client", lambda backend, model=None: SimpleNamespace(model="fake"))

    async def fake_llm(cli, messages, label):
        return '{"sufficient": true, "answer": "RSoXS is resonant soft X-ray scattering.", "missing_topics": []}'

    monkeypatch.setattr(ra.krag, "call_llm", fake_llm)

    def boom(*_args, **_kwargs):
        raise AssertionError("PaperRetriever should not run when source RAG is off")

    monkeypatch.setattr(PaperRetriever, "retrieve", boom)
    pack_calls = []
    monkeypatch.setattr(agent, "_hybrid_pack", lambda *a, **k: pack_calls.append(1) or {})

    result = asyncio.run(agent.query("what is RSoXS"))
    assert pack_calls == []
    assert result["hybrid_rag"]["chunk_count"] == 0
    assert result["sufficient"] is True


def test_ask_locked_default_off_does_not_call_paper_retriever(tmp_path, monkeypatch):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    graph = tmp_path / "matkg_rsoxs_v1.json"
    graph.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "matkg:RSoXS",
                        "name": "RSoXS",
                        "category": "ExperimentalTechnique",
                        "description": "Resonant soft X-ray scattering.",
                        "source_papers": ["paper.pdf"],
                    }
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KG_RAG_RETRIEVAL_BACKEND", "lexical")
    monkeypatch.delenv("F2W_SOURCE_RAG", raising=False)
    monkeypatch.delenv("HYBRID_RAG_ENABLED", raising=False)

    from app.modules.f2w_agent import retrieval_agent as ra

    monkeypatch.setattr(ra.krag, "make_chat_client", lambda backend, model=None: SimpleNamespace(model="fake"))

    async def fake_llm(cli, messages, label):
        return '{"sufficient": true, "answer": "RSoXS is a soft X-ray scattering technique.", "missing_topics": []}'

    monkeypatch.setattr(ra.krag, "call_llm", fake_llm)

    pack_calls = []
    original_pack = ra.RetrievalAgent._hybrid_pack

    def tracking_pack(self, *args, **kwargs):
        pack_calls.append(1)
        return original_pack(self, *args, **kwargs)

    monkeypatch.setattr(ra.RetrievalAgent, "_hybrid_pack", tracking_pack)
    monkeypatch.setattr(
        PaperRetriever,
        "retrieve",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("PaperRetriever should not run when source RAG is off")),
    )

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(graph=str(graph), workdir=tmp_path / "run", kg_mode="json", max_rounds=1)
    )
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": True, "reason": "needs retrieval"}
    )
    response = asyncio.run(service._ask_locked("what is RSoXS", None, graph_source="json", json_graph_path=str(graph)))
    assert pack_calls == []
    assert response.status != "retrieval_error"


def test_ask_locked_survives_missing_source_index(tmp_path, monkeypatch):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    graph = tmp_path / "matkg_rsoxs_v1.json"
    graph.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "matkg:RSoXS",
                        "name": "RSoXS",
                        "category": "ExperimentalTechnique",
                        "description": "Resonant soft X-ray scattering.",
                        "source_papers": ["paper.pdf"],
                    }
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HYBRID_RAG_LITERATURE_INDEX", str(tmp_path / "missing-lit"))
    monkeypatch.setenv("HYBRID_RAG_OPS_INDEX", str(tmp_path / "missing-ops"))
    monkeypatch.setenv("KG_RAG_RETRIEVAL_BACKEND", "lexical")

    from app.modules.f2w_agent import retrieval_agent as ra

    monkeypatch.setattr(ra.krag, "make_chat_client", lambda backend, model=None: SimpleNamespace(model="fake"))

    async def fake_llm(cli, messages, label):
        return '{"sufficient": true, "answer": "RSoXS is a soft X-ray scattering technique.", "missing_topics": []}'

    monkeypatch.setattr(ra.krag, "call_llm", fake_llm)

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(graph=str(graph), workdir=tmp_path / "run", kg_mode="json", max_rounds=1)
    )
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": True, "reason": "needs retrieval"}
    )
    response = asyncio.run(
        service._ask_locked(
            "what is RSoXS",
            None,
            graph_source="json",
            json_graph_path=str(graph),
            source_rag=True,
        )
    )
    assert response.status != "retrieval_error"
    assert "crash" not in (response.answer or "").lower()
    assert response.status in {"answered", "insufficient_json_graph", "stop_insufficient"}


def test_hybrid_evidence_from_agent_query_keeps_source_types(tmp_path, monkeypatch):
    lit = tmp_path / "lit"
    ops = tmp_path / "ops"
    _write_index(lit, corpus="literature", doc_id="p3ht.pdf", text="P3HT morphology by P-RSoXS.")
    _write_index(
        ops,
        corpus="bl1101",
        doc_id="als.html",
        text="Beamline 11.0.1.2 specializes in resonant soft X-ray scattering.",
        heading="Capabilities",
    )
    agent = RetrievalAgent(graph_source="json")
    kg = SimpleNamespace(
        nodes={"n1": {"description": "RSoXS", "category": "ExperimentalTechnique", "source_papers": ["p3ht.pdf"]}},
        out_edges={},
        graph_source_requested="json",
        graph_source_used="json",
        graph_id="rsoxs_v1",
        graph_label="science",
    )
    kg.build_context = lambda *a, **k: "Description: RSoXS"
    agent._kg = kg
    agent._graphs = {"rsoxs_v1": kg}
    agent._hybrid = HybridSourceRAG(literature_root=lit, ops_root=ops)

    from app.modules.f2w_agent import retrieval_agent as ra

    monkeypatch.setattr(
        ra.krag,
        "retrieve_nodes",
        lambda question, kg, **kwargs: [
            SimpleNamespace(id="n1", evidence_ct=1, name="RSoXS", category="ExperimentalTechnique", score_prp=1.0)
        ],
    )
    packed = agent._hybrid_pack(
        "what is P3HT contrast on 11.0.1.2",
        [
            ScoredHit(
                id="n1",
                graph_id="rsoxs_v1",
                score=1.0,
                evidence_ct=1,
                category="ExperimentalTechnique",
                name="RSoXS",
                payload=SimpleNamespace(id="n1", name="RSoXS", category="ExperimentalTechnique", description="RSoXS"),
            )
        ],
        agent._graphs,
    )
    types = {ev.source_type for ev in packed["evidence"] if ev.source_kind == "paper_chunk"}
    assert "literature" in types
    assert "ops" in types
    ctx = packed["source_context"]
    assert "### Literature source evidence" in ctx
    assert "### Beamline ops-doc evidence" in ctx
    assert "[PDF:" in ctx
    assert "[OPS:" in ctx
