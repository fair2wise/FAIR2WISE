#!/usr/bin/env python
"""
agent.py — CBORG-orchestrated KG-RAG agent.

CBORG (LBL LLM gateway, OpenAI-compatible) drives tool-use retrieval over
the knowledge graph. Ollama synthesizes the final answer from the accumulated
KG context.

Usage:
    python app/agent.py                          # interactive REPL
    python app/agent.py --question "..."         # one-shot
    python app/agent.py --graph path/to/kg.json  # custom KG
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI
from colorama import Fore, Style
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.kg_rag_ollama_api import (
    CTX_SOFT_LIMIT,
    GRAPH_FILE,
    OLLAMA_API_URL,
    OLLAMA_MODEL,
    RAG_SYSTEM,
    STRUCT_CTX,
    Conversation,
    KnowledgeGraph,
    NodeInfo,
    OllamaClient,
    _tokenize,
    build_rag_prompt,
    retrieve_nodes,
)

CBORG_BASE_URL = os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
CBORG_API_KEY = os.environ.get("CBORG_API_KEY", "")
AGENT_MODEL = os.environ.get("AGENT_CLAUDE_MODEL", "amazon/claude-haiku-3-5")

# ── Tool definitions ───────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_kg",
            "description": (
                "Semantic search over the materials-science knowledge graph. "
                "Returns the top matching nodes with their names, categories, descriptions, "
                "and relevance scores. Call multiple times with different phrasings to "
                "maximize coverage of the topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Scientific query string to search for in the KG",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node",
            "description": (
                "Retrieve full details for a specific KG node by its matkg: ID, "
                "including all properties, relations, and source provenance. "
                "Use this to dig into a promising node found via search_kg."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The matkg:-prefixed node ID (e.g. 'matkg:p3ht')",
                    }
                },
                "required": ["node_id"],
            },
        },
    },
]

AGENT_SYSTEM = (
    "You are a retrieval orchestrator for a materials-science knowledge graph. "
    "Your job is to gather all KG context needed to answer a scientific question. "
    "Strategy:\n"
    "  1. Call search_kg with the main question.\n"
    "  2. Decompose complex questions and call search_kg on each sub-topic.\n"
    "  3. Call get_node on the most promising nodes to get full details.\n"
    "  4. Once you have sufficient context, stop calling tools.\n"
    "Do NOT write the final scientific answer — a separate model will synthesize "
    "from the nodes you retrieved. Just collect evidence."
)

# ── Tool dispatch ──────────────────────────────────────────────────────────────


def _tool_search_kg(query: str, kg: KnowledgeGraph) -> dict[str, Any]:
    infos = retrieve_nodes(query, kg)
    return {
        "query": query,
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "category": n.category,
                "description": n.description[:300],
                "score": round(n.score_prp, 3),
                "evidence_count": n.evidence_ct,
            }
            for n in infos
        ],
    }


def _tool_get_node(node_id: str, kg: KnowledgeGraph) -> dict[str, Any]:
    node = kg.nodes.get(node_id)
    if node is None:
        return {"error": f"Node '{node_id}' not found in KG"}
    return {
        "id": node_id,
        "name": node.get("name", ""),
        "category": node.get("category", ""),
        "description": node.get("description", ""),
        "properties": node.get("properties", []),
        "formula": node.get("formula", ""),
        "relations": [
            {"predicate": e.get("predicate", ""), "object": e.get("object", "")}
            for e in kg.out_edges.get(node_id, [])[:20]
        ],
        "sources": node.get("sources", []),
    }


def _dispatch(name: str, inputs: dict, kg: KnowledgeGraph) -> str:
    if name == "search_kg":
        return json.dumps(_tool_search_kg(inputs["query"], kg))
    if name == "get_node":
        return json.dumps(_tool_get_node(inputs["node_id"], kg))
    return json.dumps({"error": f"Unknown tool: {name}"})


# ── Agent loop ─────────────────────────────────────────────────────────────────


async def run_agent(
    question: str,
    kg: KnowledgeGraph,
    cli: OllamaClient,
    rag_c: Conversation,
) -> str:
    """
    CBORG drives tool-use retrieval; Ollama synthesizes the final answer.
    Returns the RAG answer string.
    """
    client = OpenAI(api_key=CBORG_API_KEY, base_url=CBORG_BASE_URL)
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question},
    ]
    fetched_node_ids: list[str] = []

    # ── CBORG tool-use loop ────────────────────────────────────────────────────
    while True:
        response = client.chat.completions.create(
            model=AGENT_MODEL,
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )
        choice = response.choices[0]
        msg = choice.message

        # Append assistant turn as a plain dict
        assistant_entry: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if choice.finish_reason != "tool_calls" or not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            inputs = json.loads(tc.function.arguments)
            print(
                Fore.CYAN
                + f"  [cborg→tool] {tc.function.name}({json.dumps(inputs)})"
                + Style.RESET_ALL
            )
            result_str = _dispatch(tc.function.name, inputs, kg)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
            )
            if tc.function.name == "search_kg":
                nodes = json.loads(result_str).get("nodes", [])
                fetched_node_ids.extend(n["id"] for n in nodes)
            elif tc.function.name == "get_node":
                fetched_node_ids.append(inputs["node_id"])

    # ── Build KG context ───────────────────────────────────────────────────────
    infos = retrieve_nodes(question, kg)
    existing_ids = {n.id for n in infos}

    for nid in dict.fromkeys(fetched_node_ids):
        if nid not in existing_ids:
            raw = kg.nodes.get(nid)
            if raw:
                infos.append(
                    NodeInfo(
                        id=nid,
                        name=raw.get("name", nid),
                        category=raw.get("category", ""),
                        description=raw.get("description", ""),
                        score_sem=0.5,
                        score_graph=0.5,
                        depth=0,
                        lexical_overlap=0.0,
                        evidence_ct=len(raw.get("source_papers", []))
                        + len(kg.out_edges.get(nid, [])),
                    )
                )

    ctx = kg.build_context(
        infos,
        include_structured=STRUCT_CTX,
        char_budget=CTX_SOFT_LIMIT,
        hint_terms=_tokenize(question),
    )

    # ── Ollama RAG synthesis ───────────────────────────────────────────────────
    rag_prompt = build_rag_prompt(question, ctx)
    rag_resp = await cli.chat(rag_c.build(rag_prompt))
    rag_c.add(rag_prompt, rag_resp)
    return rag_resp


# ── Entry points ───────────────────────────────────────────────────────────────


async def main_async() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="CBORG-orchestrated KG-RAG agent")
    ap.add_argument("--graph", type=Path, default=GRAPH_FILE, help="KG JSON file")
    ap.add_argument("--question", type=str, help="One-shot question, then exit")
    args = ap.parse_args()

    kg = KnowledgeGraph(str(args.graph))
    cli = OllamaClient(url=OLLAMA_API_URL, model=OLLAMA_MODEL)
    rag_c = Conversation(RAG_SYSTEM)

    if args.question:
        resp = await run_agent(args.question, kg, cli, rag_c)
        print(Fore.GREEN + "\n[KG-RAG Answer]\n" + resp + Style.RESET_ALL)
        return

    while True:
        try:
            q = input("\nAsk (exit to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("exit", "quit", ""):
            break
        resp = await run_agent(q, kg, cli, rag_c)
        print(Fore.GREEN + "\n[KG-RAG Answer]\n" + resp + Style.RESET_ALL)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
