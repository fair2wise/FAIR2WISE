"""Coordinator helpers and CLI adapter for the orchestrated KG-RAG workflow.

The public flow is owned by ``WorkflowOrchestratorAgent``. Candidate metadata
search can run automatically; PDF download and extraction require explicit
approval unless the CLI configuration enables ``auto_approve``.
"""
from __future__ import annotations

import json
import logging
import shutil
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """Workflow mode (``F2W_WORKFLOW_MODE`` / config / agentic fallback)."""
    from ..project_config import config_value

    value = str(config_value("f2w_agent.workflow_mode", fallback="agentic") or "").strip().lower()
    return value if value in {"deterministic", "agentic"} else "agentic"


def default_extraction_mode() -> str:
    """Extraction mode (``F2W_EXTRACTION_MODE`` / config / full fallback)."""
    from ..project_config import config_value

    value = str(config_value("f2w_agent.extraction_mode", fallback="targeted") or "").strip().lower()
    return value if value in {"full", "targeted"} else "targeted"


def default_targeted_max_pages() -> int:
    """Maximum pages per PDF for targeted partial extraction."""
    from ..project_config import config_value

    value = config_value("f2w_agent.targeted_max_pages", fallback=6, cast=int)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 6
    return parsed if parsed > 0 else 6


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
    extraction_mode: str = field(default_factory=default_extraction_mode)
    targeted_max_pages: int = field(default_factory=default_targeted_max_pages)
    max_orchestration_steps: int = 12
    auto_approve: bool = False


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_extraction_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"papers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed extraction manifest: %s", path)
        return {"papers": {}}
    if not isinstance(data, dict):
        return {"papers": {}}
    papers = data.get("papers")
    if not isinstance(papers, dict):
        data["papers"] = {}
    return data


def _save_extraction_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


class Coordinator:
    """Owns session files; public runs delegate to AgentPipelineService."""

    def __init__(self, cfg: CoordinatorConfig) -> None:
        self.cfg = cfg
        self.cfg.workflow_mode = str(self.cfg.workflow_mode or "agentic").strip().lower()
        if self.cfg.workflow_mode not in {"deterministic", "agentic"}:
            self.cfg.workflow_mode = "agentic"
        self.cfg.extraction_mode = str(self.cfg.extraction_mode or "targeted").strip().lower()
        if self.cfg.extraction_mode not in {"full", "targeted"}:
            self.cfg.extraction_mode = "targeted"
        try:
            self.cfg.targeted_max_pages = int(self.cfg.targeted_max_pages)
        except (TypeError, ValueError):
            self.cfg.targeted_max_pages = 6
        if self.cfg.targeted_max_pages <= 0:
            self.cfg.targeted_max_pages = 6
        self.workdir = Path(cfg.workdir)
        self.pdf_dir = self.workdir / "pdfs"
        self.extract_rounds_dir = self.workdir / "extract_rounds"
        self.processed_pdfs_manifest = self.workdir / "processed_pdfs.json"
        self.extraction_manifest = self.workdir / "extraction_manifest.json"
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
        """Answer questions through the same persistent orchestrator used by HTTP."""
        # Lazy import avoids an import cycle: the service reuses Coordinator's
        # staging and extraction-manifest helpers.
        from .api import AgentPipelineService

        service = AgentPipelineService(self.cfg)
        for question in questions:
            response = await service.ask(question, auto_approve=self.cfg.auto_approve)
            print(f"\n=== Question: {question} ===")
            if response.answer:
                print(f"\n[Answer]\n{response.answer}")
            if response.pending:
                kind = response.pending.get("kind")
                if kind == "download":
                    print("[approval required] Choose a candidate in a later ask/chat turn.")
                else:
                    print("[approval required] Approve extraction in a later ask/chat turn.")

    async def _answer(self, question: str, retrieval, download, extractor, debate=None) -> None:
        # Retained only as a low-level legacy test/helper. Public CLI/API entry
        # points use ``run`` and therefore the canonical orchestrator service.
        if self.cfg.workflow_mode == "agentic" and debate is not None:
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
            self._record_full_extraction(pending_pdfs, ext)
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
            if self.cfg.extraction_mode == "targeted":
                print(
                    f"[round {round_no}] targeted extraction from {len(pending_pdfs)} approved PDF(s) "
                    f"(max_pages={self.cfg.targeted_max_pages})"
                )
                try:
                    ext = await extractor.extract_targeted(
                        str(round_pdf_dir),
                        str(self.session_terms),
                        question,
                        missing,
                        self.cfg.targeted_max_pages,
                    )
                except Exception as exc:
                    logger.exception("Extractor agent failed")
                    print(f"[stop] targeted extraction failed: {exc}")
                    return
            else:
                print(f"[round {round_no}] extracting {len(pending_pdfs)} approved PDF(s)")
                try:
                    ext = await extractor.extract(str(round_pdf_dir), str(self.session_terms))
                except Exception as exc:
                    logger.exception("Extractor agent failed")
                    print(f"[stop] extraction failed: {exc}")
                    return
            if not isinstance(ext, dict):
                print(f"[stop] extraction failed: {ext}")
                return
            if ext.get("status") == "error":
                print(f"[stop] extraction failed: {ext.get('message') or ext}")
                return
            if self.cfg.extraction_mode == "targeted":
                self._record_partial_extraction(
                    query=question,
                    missing_topics=list(missing),
                    pdfs=pending_pdfs,
                    result=ext,
                )
            else:
                self._record_full_extraction(pending_pdfs, ext)
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

    def _stage_unprocessed_pdfs(
        self,
        round_no: int,
        source_pdfs: Optional[List[Path]] = None,
    ) -> tuple[Path, List[Path]]:
        processed = _load_processed_pdfs(self.processed_pdfs_manifest)
        candidates = (
            [Path(pdf) for pdf in source_pdfs]
            if source_pdfs is not None
            else list(self.pdf_dir.glob("*.pdf"))
        )
        by_name = {
            pdf.name: pdf
            for pdf in candidates
            if pdf.is_file() and pdf.suffix.lower() == ".pdf"
        }
        pending = sorted(
            (pdf for name, pdf in by_name.items() if name not in processed),
            key=lambda pdf: pdf.name,
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

    def _record_full_extraction(self, pdfs: List[Path], result: Dict[str, Any]) -> None:
        manifest = _load_extraction_manifest(self.extraction_manifest)
        papers = manifest.setdefault("papers", {})
        now = datetime.now(timezone.utc).isoformat()
        for pdf in pdfs:
            entry = dict(papers.get(pdf.name) or {})
            entry.update(
                {
                    "filename": pdf.name,
                    "pdf_sha256": _sha256_file(pdf),
                    "extraction_state": "full",
                    "timestamp": now,
                    "terms_json": str(self.session_terms),
                    "processed_pages_total": result.get("processed_pages_total"),
                    "processed_pages_with_terms": result.get("processed_pages_with_terms"),
                }
            )
            entry["full"] = {
                "timestamp": now,
                "terms_json": str(self.session_terms),
                "processed_pages_total": result.get("processed_pages_total"),
                "processed_pages_with_terms": result.get("processed_pages_with_terms"),
            }
            papers[pdf.name] = entry
        _save_extraction_manifest(self.extraction_manifest, manifest)

    def _record_partial_extraction(
        self,
        *,
        query: str,
        missing_topics: List[str],
        pdfs: List[Path],
        result: Dict[str, Any],
    ) -> None:
        manifest = _load_extraction_manifest(self.extraction_manifest)
        papers = manifest.setdefault("papers", {})
        now = datetime.now(timezone.utc).isoformat()
        by_name = {
            str(item.get("filename") or ""): item
            for item in result.get("pdf_results") or []
            if isinstance(item, dict)
        }
        for pdf in pdfs:
            pdf_result = by_name.get(pdf.name, {})
            entry = dict(papers.get(pdf.name) or {})
            partials = entry.get("partials")
            if not isinstance(partials, list):
                partials = []
            partial_record = {
                "timestamp": now,
                "query": query,
                "missing_topics": missing_topics,
                "selected_pages": pdf_result.get("selected_pages") or [],
                "processed_pages_total": pdf_result.get("processed_pages_total"),
                "processed_pages_with_terms": pdf_result.get("processed_pages_with_terms"),
                "source_pages_total": pdf_result.get("source_pages_total"),
                "terms_json": str(self.session_terms),
            }
            partials.append(partial_record)
            entry.update(
                {
                    "filename": pdf.name,
                    "pdf_sha256": _sha256_file(pdf),
                    "extraction_state": "partial" if entry.get("extraction_state") != "full" else "full",
                    "timestamp": now,
                    "query": query,
                    "missing_topics": missing_topics,
                    "selected_pages": partial_record["selected_pages"],
                    "processed_pages_total": partial_record["processed_pages_total"],
                    "processed_pages_with_terms": partial_record["processed_pages_with_terms"],
                    "source_pages_total": partial_record["source_pages_total"],
                    "terms_json": str(self.session_terms),
                    "partials": partials,
                }
            )
            papers[pdf.name] = entry
        _save_extraction_manifest(self.extraction_manifest, manifest)
