"""CLI for the FAIR2WISE orchestrated KG-RAG pipeline.

Subcommands:
    api    FastAPI bridge for the prototype web UI
    ask    one-shot question or approval turn through the orchestrator
    chat   interactive loop
    status print resolved configuration
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from .coordinator import (
    Coordinator,
    CoordinatorConfig,
    default_extraction_mode,
    default_targeted_max_pages,
    default_workers,
    default_workflow_mode,
)

load_dotenv()


def _cfg(args: argparse.Namespace) -> CoordinatorConfig:
    workflow_mode = "agentic" if args.agentic else args.workflow_mode
    return CoordinatorConfig(
        backend=args.backend,
        model=args.model,
        graph=args.graph,
        seed_terms=args.seed_terms,
        kg_mode=args.kg_mode,
        workdir=Path(args.workdir),
        schema_path=args.schema,
        chebi_obo_path=args.chebi,
        max_rounds=args.max_rounds,
        max_papers=args.max_papers,
        candidate_pool=args.candidate_pool,
        workers=args.workers,
        splash_repo=args.splash_repo,
        download_delay_seconds=args.download_delay,
        validate_downloads=not args.no_download_validation,
        allow_splash_wipe=args.allow_splash_wipe,
        workflow_mode=workflow_mode,
        extraction_mode=args.extraction_mode,
        targeted_max_pages=args.targeted_max_pages,
        max_orchestration_steps=args.max_orchestration_steps,
        auto_approve=args.auto_approve,
    )


async def _run_chat(cfg: CoordinatorConfig) -> None:
    coord = Coordinator(cfg)
    print("FAIR2WISE orchestrated KG-RAG chat. Type 'exit' to quit.")
    while True:
        try:
            q = input("\nAsk> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in {"exit", "quit", ""}:
            break
        await coord.run([q])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="f2w-agent",
        description="FAIR2WISE orchestrated KG-RAG pipeline.",
    )
    p.add_argument("--backend", default="cborg", choices=["cborg", "cborg-openai", "ollama"])
    p.add_argument("--model", default=None, help="LLM model name")
    p.add_argument("--graph", default=None, help="Initial KG JSON for retrieval")
    p.add_argument("--seed-terms", default=None, help="Seed extracted-terms JSON (cumulative base)")
    p.add_argument("--kg-mode", default="splash", choices=["json", "splash"], help="KG update/reload mode")
    p.add_argument("--workdir", default="runs/session", help="Session working directory")
    p.add_argument("--schema", default="storage/schema/matkg_schema.yaml")
    p.add_argument("--chebi", default=None, help="Optional ChEBI .obo path")
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--max-papers", type=int, default=3)
    p.add_argument("--candidate-pool", type=int, default=25)
    p.add_argument("--workflow-mode", choices=["deterministic", "agentic"], default=default_workflow_mode())
    p.add_argument("--agentic", action="store_true", help="Alias for --workflow-mode agentic")
    p.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve download and extraction automatically (intended for scripted CLI runs)",
    )
    p.add_argument("--max-orchestration-steps", type=int, default=12)
    p.add_argument("--extraction-mode", choices=["full", "targeted"], default=default_extraction_mode())
    p.add_argument("--targeted-max-pages", type=int, default=default_targeted_max_pages())
    p.add_argument("--download-delay", type=float, default=1.0, help="Seconds to wait between PDF download attempts")
    p.add_argument(
        "--no-download-validation",
        action="store_true",
        help="Disable best-effort LLM relevance validation for downloaded PDFs",
    )
    p.add_argument("--workers", type=int, default=default_workers())
    p.add_argument("--splash-repo", default=None, help="Path to splash_links repo (splash mode)")
    p.add_argument(
        "--allow-splash-wipe",
        action="store_true",
        help="Allow splash mode to delete links.sqlite before re-import",
    )

    sub = p.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="Run HTTP API for the prototype web UI")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8090)
    api.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        help="Allowed CORS origin; repeat for multiple origins",
    )
    a = sub.add_parser("ask", help="Ask one question")
    a.add_argument("question")
    sub.add_parser("chat", help="Interactive chat loop")
    sub.add_parser("status", help="Print configuration")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _cfg(args)

    if args.command == "status":
        import json
        print(json.dumps({
            "backend": cfg.backend, "model": cfg.model, "graph": cfg.graph,
            "seed_terms": cfg.seed_terms, "kg_mode": cfg.kg_mode,
            "workdir": str(cfg.workdir), "max_rounds": cfg.max_rounds,
            "max_papers": cfg.max_papers, "download_delay": cfg.download_delay_seconds,
            "validate_downloads": cfg.validate_downloads,
            "allow_splash_wipe": cfg.allow_splash_wipe,
            "workflow_mode": cfg.workflow_mode,
            "extraction_mode": cfg.extraction_mode,
            "targeted_max_pages": cfg.targeted_max_pages,
            "max_orchestration_steps": cfg.max_orchestration_steps,
            "auto_approve": cfg.auto_approve,
        }, indent=2))
        return 0

    try:
        if args.command == "ask":
            asyncio.run(Coordinator(cfg).run([args.question]))
        elif args.command == "api":
            from .api import run_api

            run_api(cfg, host=args.host, port=args.port, cors_origins=args.cors_origin)
        elif args.command == "chat":
            asyncio.run(_run_chat(cfg))
        else:  # pragma: no cover
            raise SystemExit(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
