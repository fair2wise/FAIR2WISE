"""Coordinator: launches the 3 agents and runs the retrieve/grow/retrieve loop.

Flow per question (up to ``max_rounds``):
  1. RetrievalAgent.query -> if sufficient, answer and stop.
  2. else DownloadAgent.find_and_download(missing_topics) -> if nothing new, stop.
  3. ExtractorAgent.extract -> kg_update.rebuild_kg (+ splash reimport in splash
     mode) -> RetrievalAgent.reload_kg, then repeat.
"""
from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from academy.exchange import LocalExchangeFactory
from academy.manager import Manager

from . import kg_update
from .debate_agent import EvidenceDebateAgent
from .download_agent import DownloadAgent
from .extractor_agent import ExtractorAgent
from .retrieval_agent import RetrievalAgent

logger = logging.getLogger(__name__)


def default_workers() -> int:
    """Page-level extraction parallelism (``F2W_WORKERS`` / config / fallback)."""
    from ..project_config import config_value

    value = config_value("extract_terms.max_workers", fallback=8, cast=int)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 8
    return parsed if parsed > 0 else 8


def default_workflow_mode() -> str:
    """Workflow mode (``F2W_WORKFLOW_MODE`` / config / deterministic fallback)."""
    from ..project_config import config_value

    value = str(config_value("f2w_agent.workflow_mode", fallback="deterministic") or "").strip().lower()
    return value if value in {"deterministic", "agentic"} else "deterministic"


@dataclass
class CoordinatorConfig:
    backend: str = "cborg"
    model: Optional[str] = None
    graph: Optional[str] = None
    seed_terms: Optional[str] = None
    kg_mode: str = "json"  # "json" | "splash"
    workdir: Path = field(default_factory=lambda: Path("runs/session"))
    schema_path: str = "storage/schema/matkg_schema.yaml"
    chebi_obo_path: Optional[str] = None
    max_rounds: int = 3
    max_papers: int = 3
    candidate_pool: int = 25
    workers: int = field(default_factory=default_workers)
    splash_repo: Optional[str] = None
    download_delay_seconds: float = 1.0
    validate_downloads: bool = True
    allow_splash_wipe: bool = False
    workflow_mode: str = field(default_factory=default_workflow_mode)


def _empty_terms(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metadata": {}, "terms": [], "code_snippets": []}, indent=2))


def _empty_kg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"things": [], "associations": []}, indent=2))


def _load_processed_pdfs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed processed PDF manifest: %s", path)
        return set()
    if not isinstance(data, list):
        logger.warning("Ignoring non-list processed PDF manifest: %s", path)
        return set()
    return {str(item) for item in data}


def _save_processed_pdfs(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(processed), indent=2))


class Coordinator:
    """Owns session state and drives the multi-agent loop over one Manager."""

    def __init__(self, cfg: CoordinatorConfig) -> None:
        self.cfg = cfg
        self.cfg.workflow_mode = str(self.cfg.workflow_mode or "deterministic").strip().lower()
        if self.cfg.workflow_mode not in {"deterministic", "agentic"}:
            self.cfg.workflow_mode = "deterministic"
        self.workdir = Path(cfg.workdir)
        self.pdf_dir = self.workdir / "pdfs"
        self.extract_rounds_dir = self.workdir / "extract_rounds"
        self.processed_pdfs_manifest = self.workdir / "processed_pdfs.json"
        self.session_terms = self.workdir / "terms.json"
        self.session_kg = self.workdir / "kg.json"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

        configured_graph = Path(cfg.graph) if cfg.graph else None

        # Seed cumulative terms + initial KG.
        if cfg.seed_terms and Path(cfg.seed_terms).exists():
            shutil.copy2(cfg.seed_terms, self.session_terms)
            kg_update.rebuild_kg(str(self.session_terms), str(self.session_kg))
        else:
            if not self.session_terms.exists():
                _empty_terms(self.session_terms)
            # No seed terms means configured graph is source of truth. Keep the
            # session graph in sync so UI-highlighted retrieved node IDs render.
            if configured_graph and configured_graph.exists():
                shutil.copy2(configured_graph, self.session_kg)

        # Initial graph the retrieval agent reads.
        if configured_graph and configured_graph.exists():
            self.initial_graph = str(configured_graph)
        else:
            if not self.session_kg.exists():
                _empty_kg(self.session_kg)
            self.initial_graph = str(self.session_kg)

    async def run(self, questions: List[str]) -> None:
        """Launch the agents and answer each question through the loop."""
        async with await Manager.from_exchange_factory(
            factory=LocalExchangeFactory(),
            executors=ThreadPoolExecutor(max_workers=max(4, self.cfg.workers)),
        ) as manager:
            retrieval = await manager.launch(
                RetrievalAgent(
                    graph_file=self.initial_graph,
                    graph_source=self.cfg.kg_mode,
                    backend=self.cfg.backend,
                    model=self.cfg.model,
                )
            )
            download = await manager.launch(
                DownloadAgent(
                    backend=self.cfg.backend,
                    model=self.cfg.model,
                    download_delay_seconds=self.cfg.download_delay_seconds,
                    validate_downloads=self.cfg.validate_downloads,
                )
            )
            extractor = await manager.launch(
                ExtractorAgent(
                    backend=self.cfg.backend,
                    model=self.cfg.model,
                    schema_path=self.cfg.schema_path,
                    chebi_obo_path=self.cfg.chebi_obo_path,
                    max_workers=self.cfg.workers,
                )
            )
            debate = None
            if self.cfg.workflow_mode == "agentic":
                debate = await manager.launch(
                    EvidenceDebateAgent(
                        backend=self.cfg.backend,
                        model=self.cfg.model,
                    )
                )
            try:
                for q in questions:
                    await self._answer(q, retrieval, download, extractor, debate)
            finally:
                handles = [retrieval, download, extractor]
                if debate is not None:
                    handles.append(debate)
                for h in handles:
                    await manager.shutdown(h, blocking=True)

    async def _answer(self, question: str, retrieval, download, extractor, debate=None) -> None:
        if self.cfg.workflow_mode == "agentic":
            if debate is None:
                print("[stop] agentic workflow requires EvidenceDebateAgent.")
                return
            await self._answer_agentic(question, retrieval, download, extractor, debate)
            return

        print(f"\n=== Question: {question} ===")
        for round_no in range(1, self.cfg.max_rounds + 1):
            try:
                verdict = await retrieval.query(question)
            except Exception as exc:
                logger.exception("Retrieval agent failed")
                print(f"[stop] retrieval failed: {exc}")
                return
            if str(verdict.get("status", "")).endswith("_error"):
                print(f"[stop] retrieval failed: {verdict.get('error') or verdict.get('status')}")
                return
            if verdict.get("graph_source_used") and verdict.get("graph_source_used") != verdict.get("graph_source_requested"):
                print(
                    f"[info] graph source fallback: requested={verdict.get('graph_source_requested')} "
                    f"used={verdict.get('graph_source_used')}"
                )
            if verdict.get("sufficient"):
                print(f"\n[Answer]\n{verdict.get('answer')}")
                return

            missing = verdict.get("missing_topics") or [question]
            print(f"[round {round_no}] insufficient evidence; missing: {missing}")

            try:
                dl = await download.find_and_download(
                    question,
                    missing_topics=missing,
                    target_dir=str(self.pdf_dir),
                    max_papers=self.cfg.max_papers,
                    candidate_pool=self.cfg.candidate_pool,
                )
            except Exception as exc:
                logger.exception("Download agent failed")
                print(f"[stop] download failed: {exc}")
                return
            print(f"[round {round_no}] downloaded {dl.get('count', 0)} paper(s) "
                  f"(from {dl.get('candidates', 0)} candidates; "
                  f"failed={dl.get('failed', 0)}, "
                  f"semantic_rejected={dl.get('semantic_rejected', 0)})")
            if dl.get("count", 0) == 0:
                print("[stop] no new relevant papers found; cannot gather more evidence.")
                return

            round_pdf_dir, pending_pdfs = self._stage_unprocessed_pdfs(round_no)
            if not pending_pdfs:
                print("[stop] no unprocessed PDFs available after download.")
                return
            print(f"[round {round_no}] extracting {len(pending_pdfs)} new PDF(s)")
            try:
                ext = await extractor.extract(str(round_pdf_dir), str(self.session_terms))
            except Exception as exc:
                logger.exception("Extractor agent failed")
                print(f"[stop] extraction failed: {exc}")
                return
            if ext.get("status") == "error":
                print(f"[stop] extraction failed: {ext.get('message') or ext}")
                return
            self._mark_processed_pdfs(pending_pdfs)
            print(f"[round {round_no}] extracted -> {ext.get('unique_terms', '?')} unique terms")

            try:
                kg = kg_update.rebuild_kg(str(self.session_terms), str(self.session_kg))
            except Exception as exc:
                logger.exception("KG rebuild failed")
                print(f"[stop] KG rebuild failed: {exc}")
                return
            print(f"[round {round_no}] KG rebuilt: {kg.get('nodes')} nodes, {kg.get('edges')} edges")
            if self.cfg.kg_mode == "splash":
                try:
                    res = kg_update.splash_reimport(
                        str(self.session_kg),
                        splash_repo=self.cfg.splash_repo,
                        allow_wipe=self.cfg.allow_splash_wipe,
                    )
                except Exception as exc:
                    logger.exception("Splash reimport failed")
                    print(f"[stop] splash reimport failed: {exc}")
                    return
                db_note = f" db={res.get('db_path')}" if res.get("db_path") else ""
                print(f"[round {round_no}] splash reimport: {res.get('status')}{db_note}")
                if res.get("status") == "error":
                    print(f"[stop] splash reimport failed: {res.get('message') or res}")
                    return

            try:
                reload_res = await retrieval.reload_kg(str(self.session_kg))
            except Exception as exc:
                logger.exception("Retrieval KG reload failed")
                print(f"[stop] KG reload failed: {exc}")
                return
            if reload_res.get("graph_source_used") and reload_res.get("graph_source_used") != reload_res.get("graph_source_requested"):
                print(
                    f"[info] graph source fallback after reload: requested={reload_res.get('graph_source_requested')} "
                    f"used={reload_res.get('graph_source_used')}"
                )

        print(f"[stop] reached max rounds ({self.cfg.max_rounds}) without sufficient evidence.")

    async def _answer_agentic(self, question: str, retrieval, download, extractor, debate) -> None:
        print(f"\n=== Question: {question} ===")
        for round_no in range(1, self.cfg.max_rounds + 1):
            try:
                verdict = await retrieval.query(question)
            except Exception as exc:
                logger.exception("Retrieval agent failed")
                print(f"[stop] retrieval failed: {exc}")
                return
            if str(verdict.get("status", "")).endswith("_error"):
                print(f"[stop] retrieval failed: {verdict.get('error') or verdict.get('status')}")
                return
            if verdict.get("graph_source_used") and verdict.get("graph_source_used") != verdict.get("graph_source_requested"):
                print(
                    f"[info] graph source fallback: requested={verdict.get('graph_source_requested')} "
                    f"used={verdict.get('graph_source_used')}"
                )

            missing = verdict.get("missing_topics") or [question]
            print(f"[round {round_no}] evidence probe: selected={len(verdict.get('selected') or [])} "
                  f"sufficient={bool(verdict.get('sufficient'))}")

            if verdict.get("sufficient"):
                debate_summary = await debate.decide(question, verdict, [], round_no)
                print(f"[round {round_no}] debate: {debate_summary.get('selected_action')} - "
                      f"{debate_summary.get('reason')}")
                print(f"\n[Answer]\n{verdict.get('answer')}")
                return

            try:
                search = await download.search_candidates(
                    question,
                    missing_topics=missing,
                    candidate_pool=self.cfg.candidate_pool,
                )
            except Exception as exc:
                logger.exception("Candidate search failed")
                print(f"[stop] candidate search failed: {exc}")
                return
            candidates = search.get("candidates") or []
            titles = [str(c.get("title") or c.get("doi") or c.get("id") or "Untitled") for c in candidates[:3]]
            print(f"[round {round_no}] candidate search: {len(candidates)} candidate(s)"
                  f"{' - ' + '; '.join(titles) if titles else ''}")

            try:
                debate_summary = await debate.decide(question, verdict, candidates, round_no)
            except Exception as exc:
                logger.exception("Evidence debate failed")
                print(f"[stop] evidence debate failed: {exc}")
                return

            if (
                debate_summary.get("selected_action") == "refine_search"
                and debate_summary.get("refined_query")
            ):
                refined_query = str(debate_summary["refined_query"])
                print(f"[round {round_no}] debate requested refined search: {refined_query}")
                try:
                    search = await download.search_candidates(
                        refined_query,
                        missing_topics=missing,
                        candidate_pool=self.cfg.candidate_pool,
                    )
                    candidates = search.get("candidates") or []
                    debate_summary = await debate.decide(question, verdict, candidates, round_no)
                except Exception as exc:
                    logger.exception("Refined candidate search failed")
                    print(f"[stop] refined candidate search failed: {exc}")
                    return

            print(f"[round {round_no}] debate: {debate_summary.get('selected_action')} - "
                  f"{debate_summary.get('reason')}")

            action_name = debate_summary.get("selected_action")
            if action_name == "answer_from_kg" and verdict.get("sufficient"):
                print(f"\n[Answer]\n{verdict.get('answer')}")
                return
            if action_name != "download_selected":
                print(f"[stop] insufficient evidence: {debate_summary.get('reason')}")
                return

            selected_indices = debate_summary.get("candidate_indices") or []
            selected_candidates = [
                candidates[int(i)] for i in selected_indices
                if isinstance(i, int) or str(i).isdigit()
                if 0 <= int(i) < len(candidates)
            ]
            if not selected_candidates and candidates:
                selected_candidates = candidates[:1]
            if not selected_candidates:
                print("[stop] debate approved download but no candidate was selected.")
                return

            try:
                dl = await download.download_selected(
                    question,
                    missing_topics=missing,
                    target_dir=str(self.pdf_dir),
                    candidates=selected_candidates,
                    max_papers=min(self.cfg.max_papers, len(selected_candidates)),
                )
            except Exception as exc:
                logger.exception("Download agent failed")
                print(f"[stop] download failed: {exc}")
                return
            print(f"[round {round_no}] approved download {dl.get('count', 0)} paper(s) "
                  f"(failed={dl.get('failed', 0)}, "
                  f"semantic_rejected={dl.get('semantic_rejected', 0)})")
            if dl.get("count", 0) == 0:
                print("[stop] approved paper could not be downloaded; cannot gather more evidence.")
                return

            round_pdf_dir, pending_pdfs = self._stage_unprocessed_pdfs(round_no)
            if not pending_pdfs:
                print("[stop] no unprocessed PDFs available after download.")
                return
            print(f"[round {round_no}] extracting {len(pending_pdfs)} approved PDF(s)")
            try:
                ext = await extractor.extract(str(round_pdf_dir), str(self.session_terms))
            except Exception as exc:
                logger.exception("Extractor agent failed")
                print(f"[stop] extraction failed: {exc}")
                return
            if ext.get("status") == "error":
                print(f"[stop] extraction failed: {ext.get('message') or ext}")
                return
            self._mark_processed_pdfs(pending_pdfs)
            print(f"[round {round_no}] extracted -> {ext.get('unique_terms', '?')} unique terms")

            try:
                kg = kg_update.rebuild_kg(str(self.session_terms), str(self.session_kg))
            except Exception as exc:
                logger.exception("KG rebuild failed")
                print(f"[stop] KG rebuild failed: {exc}")
                return
            print(f"[round {round_no}] KG rebuilt: {kg.get('nodes')} nodes, {kg.get('edges')} edges")
            if self.cfg.kg_mode == "splash":
                try:
                    res = kg_update.splash_reimport(
                        str(self.session_kg),
                        splash_repo=self.cfg.splash_repo,
                        allow_wipe=self.cfg.allow_splash_wipe,
                    )
                except Exception as exc:
                    logger.exception("Splash reimport failed")
                    print(f"[stop] splash reimport failed: {exc}")
                    return
                db_note = f" db={res.get('db_path')}" if res.get("db_path") else ""
                print(f"[round {round_no}] splash reimport: {res.get('status')}{db_note}")
                if res.get("status") == "error":
                    print(f"[stop] splash reimport failed: {res.get('message') or res}")
                    return

            try:
                reload_res = await retrieval.reload_kg(str(self.session_kg))
            except Exception as exc:
                logger.exception("Retrieval KG reload failed")
                print(f"[stop] KG reload failed: {exc}")
                return
            if reload_res.get("graph_source_used") and reload_res.get("graph_source_used") != reload_res.get("graph_source_requested"):
                print(
                    f"[info] graph source fallback after reload: requested={reload_res.get('graph_source_requested')} "
                    f"used={reload_res.get('graph_source_used')}"
                )

        print(f"[stop] reached max rounds ({self.cfg.max_rounds}) without sufficient evidence.")

    def _stage_unprocessed_pdfs(self, round_no: int) -> tuple[Path, List[Path]]:
        processed = _load_processed_pdfs(self.processed_pdfs_manifest)
        pending = sorted(
            p for p in self.pdf_dir.glob("*.pdf")
            if p.is_file() and p.name not in processed
        )
        round_dir = self.extract_rounds_dir / f"round_{round_no}"
        if round_dir.exists():
            shutil.rmtree(round_dir)
        round_dir.mkdir(parents=True, exist_ok=True)
        for pdf in pending:
            shutil.copy2(pdf, round_dir / pdf.name)
        return round_dir, pending

    def _mark_processed_pdfs(self, pdfs: List[Path]) -> None:
        processed = _load_processed_pdfs(self.processed_pdfs_manifest)
        processed.update(pdf.name for pdf in pdfs)
        _save_processed_pdfs(self.processed_pdfs_manifest, processed)
