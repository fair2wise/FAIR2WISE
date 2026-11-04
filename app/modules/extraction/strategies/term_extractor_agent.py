"""Optional strategy: the F2W ``term_extractor`` agent path, wrapped without behaviour change (H3 F9).

This is the path the extractor agent runs after a UI extraction approval
(``f2w_agent.extractor_agent`` -> ``term_extractor.Orchestrator``). It is wrapped so
it can be compared with ``legacy_f2w`` and the FORGE strategies on identical
fixtures; it is not the default.

The wrapped object is an already-constructed ``Orchestrator`` (or anything with
its ``process_pdf`` / ``process_page`` / ``store`` surface). Its LLM, tool graph,
schema helper, ChEBI services, merge index, CBORG admission and output-file writes
are exactly as the caller configured them. This module calls only its public
processing methods and *reads* ``store`` afterwards; it imports nothing from
``term_extractor`` (that package builds LangChain clients at import).

Two input modes, both existing entry points:

- ``pdf_path`` -> ``process_pdf(path)`` (unchanged agent path, including its
  code-snippet and GitHub passes and source-metadata stamping);
- ``page_texts`` -> ``process_page(text, filename, page_index)`` per page, in
  page order. ``process_pdf`` runs the same per-page call, then its snippet
  passes, which this mode does not (there is no PDF to read them from).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contract import ChunkCoverage, ExtractionResult, ExtractionSource, frozen_copy
from .legacy_f2w import mentions_for_term

if TYPE_CHECKING:
    from ...contracts.json_types import JsonValue

STRATEGY_NAME = "f2w_term_extractor_agent"
STRATEGY_VERSION = "term_extractor-orchestrator-2.1"


def _as_dict(record: JsonValue) -> dict[str, Any]:
    return record.to_dict() if hasattr(record, "to_dict") else dict(record)


class TermExtractorAgentStrategy:
    """Wrap an existing ``term_extractor.Orchestrator`` as an optional strategy.

    Args:
        orchestrator: A configured orchestrator instance (owned by the caller).
    """

    name = STRATEGY_NAME
    version = STRATEGY_VERSION

    def __init__(self, orchestrator: Any) -> None:  # noqa: ANN401  # Any: the term_extractor Orchestrator (untyped donor code)
        self.orchestrator = orchestrator

    def _inference(self) -> dict[str, Any]:
        o = self.orchestrator
        return {"path": "term_extractor.Orchestrator.process_page", "model": getattr(o, "model", None),
                "backend": getattr(o, "backend", None), "temperature": getattr(o, "temperature", None)}

    def run(self, source: ExtractionSource) -> tuple[str, list[ChunkCoverage]]:
        """Run the orchestrator on ``source``; return the ``source_paper`` filename and coverage."""
        coverage: list[ChunkCoverage] = []
        if source.pdf_path:
            filename = Path(source.pdf_path).name
            pages = self.orchestrator.process_pdf(source.pdf_path)
            coverage.append(ChunkCoverage(chunk_id=f"pdf:{filename}", section=None, start=None, end=None, ok=None,
                                          extracted_by="agent_pdf", reason=f"pages_with_terms={pages}",
                                          settings=self._inference()))
            return filename, coverage
        if not source.page_texts:
            raise ValueError("f2w_term_extractor_agent needs pdf_path or page_texts")
        filename = source.filename or f"{source.work_id}.pdf"
        for number, text in enumerate(source.page_texts):
            changed = self.orchestrator.process_page(str(text), filename, number)
            coverage.append(ChunkCoverage(chunk_id=f"page:{number + 1}", section=None, start=None, end=None,
                                          ok=None, extracted_by="agent_page",
                                          reason="changed" if changed else "unchanged", settings=self._inference()))
        return filename, coverage

    def extract(self, source: ExtractionSource) -> ExtractionResult:
        """Run the wrapped orchestrator and normalize what it stored for this source."""
        filename, coverage = self.run(source)
        store = self.orchestrator.store
        terms = [_as_dict(r) for r in store.all_records() if filename in (getattr(r, "source_papers", None) or [])]
        snippets = [s for s in getattr(store, "code_snippets", []) if s.get("source_paper") == filename]
        document = source.document
        return ExtractionResult(
            strategy=self.name, strategy_version=self.version, work_id=source.work_id,
            source_version=document.source_ref if document else None,
            parser_revision=document.parser_key if document else None,
            # Agent failures fall back or are logged, not reported per page: completeness is unknown.
            complete=False,
            mentions=tuple(m for term in terms for m in mentions_for_term(term, filename, extracted_by=self.name)),
            coverage=tuple(coverage),
            side_outputs=frozen_copy({"source_paper": filename, "terms": terms, "code_snippets": snippets,
                                      "metadata": dict(getattr(store, "metadata", {}))}),
            inference=self._inference(),
        )


__all__ = ["STRATEGY_NAME", "STRATEGY_VERSION", "TermExtractorAgentStrategy"]
