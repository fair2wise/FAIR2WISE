"""Default strategy: the existing F2W term extractor, wrapped without behaviour change.

The wrapped object is an already-constructed ``extract_terms.LLMTermExtractor`` (or
anything with the same ``process_pdf`` / ``process_page`` / ``terms_dict`` /
``code_snippets`` / ``metadata`` surface). Its constructor, client, schema helper,
ChEBI lookup, formula validation, property normalizer, merge index, retries and
output-file writes are exactly as configured by the caller; this module only calls
its public processing methods and *reads* its state afterwards.

Two input modes, both existing extractor entry points:

- ``pdf_path`` -> ``process_pdf(path)`` (the CLI/directory path, unchanged);
- ``page_texts`` -> ``process_page`` per page, in page order, over an in-memory
  page view that offers the ``load_page(i).get_text()`` calls the method makes.

The normalized result keeps the extractor's own entries verbatim in
``side_outputs`` (terms, code snippets, metadata) and lists mentions with their
schema category, page and context snippet. Chemical/formula/ChEBI fields and
code-snippet provenance stay attached as mention attributes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..contract import ChunkCoverage, ExtractedMention, ExtractionResult, ExtractionSource, frozen_copy

STRATEGY_NAME = "legacy_f2w"
STRATEGY_VERSION = "extract_terms-2.1"

#: Term fields carried onto mentions as attributes (F2W-specific side data).
_CHEMICAL_FIELDS = ("formula", "formula_validation", "chebi", "smiles", "charge", "inchi", "inchikey", "mass")


class _Page:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, *_args: object, **_kwargs: object) -> str:
        return self._text


class TextPages:
    """Minimal page container for ``process_page`` (``load_page``/``page_count``/``metadata``)."""

    def __init__(self, page_texts: Sequence[str]) -> None:
        self._pages = [str(t) for t in page_texts]
        self.page_count = len(self._pages)
        self.metadata: dict[str, Any] = {}

    def load_page(self, number: int) -> _Page:
        """Return page ``number`` (0-based)."""
        return _Page(self._pages[number])


class LegacyF2WStrategy:
    """Wrap an existing ``LLMTermExtractor`` as the default extraction strategy.

    Args:
        extractor: A configured extractor instance (owned by the caller).
    """

    name = STRATEGY_NAME
    version = STRATEGY_VERSION

    def __init__(self, extractor: Any) -> None:  # noqa: ANN401  # Any: the legacy term extractor (untyped donor code)
        self.extractor = extractor

    def _inference(self) -> dict[str, Any]:
        client = getattr(self.extractor, "chat_client", None)
        return {
            "path": "extract_terms.LLMTermExtractor.call_llm",
            "client_class": f"{type(client).__module__}.{type(client).__qualname__}" if client else None,
            "model": getattr(client, "model", None) or getattr(self.extractor, "model_name", None),
            "temperature": getattr(self.extractor, "temperature", None),
        }

    def run(self, source: ExtractionSource) -> tuple[str, list[ChunkCoverage]]:
        """Run the extractor on ``source``; return the ``source_paper`` filename and coverage."""
        coverage: list[ChunkCoverage] = []
        if source.pdf_path:
            filename = Path(source.pdf_path).name
            changed_pages = self.extractor.process_pdf(source.pdf_path)
            coverage.append(ChunkCoverage(
                chunk_id=f"pdf:{filename}", section=None, start=None, end=None, ok=None,
                extracted_by="legacy_pdf", reason=f"pages_with_terms={changed_pages}",
                settings=self._inference(),
            ))
            return filename, coverage
        if not source.page_texts:
            raise ValueError("legacy_f2w needs pdf_path or page_texts")
        filename = source.filename or f"{source.work_id}.pdf"
        pages = TextPages(source.page_texts)
        pub_meta = dict(source.pub_meta or {})
        pub_year = pub_meta.get("publication_year")
        for number in range(pages.page_count):
            changed = self.extractor.process_page(pages, filename, number, pub_year, pub_meta or None)
            coverage.append(ChunkCoverage(
                chunk_id=f"page:{number + 1}", section=None, start=None, end=None, ok=None,
                extracted_by="legacy_page", reason="changed" if changed else "unchanged",
                settings=self._inference(),
            ))
        return filename, coverage

    def extract(self, source: ExtractionSource) -> ExtractionResult:
        """Run the wrapped extractor and normalize what it produced for this source."""
        filename, coverage = self.run(source)
        terms = [t for t in self.extractor.terms_dict.values() if filename in (t.get("source_papers") or [])]
        snippets = [s for s in self.extractor.code_snippets if s.get("source_paper") == filename]
        mentions = [m for term in terms for m in mentions_for_term(term, filename)]
        document = source.document
        return ExtractionResult(
            strategy=self.name, strategy_version=self.version, work_id=source.work_id,
            source_version=document.source_ref if document else None,
            parser_revision=document.parser_key if document else None,
            # The extractor does not report page failures, so completeness is unknown.
            complete=False,
            mentions=tuple(mentions),
            coverage=tuple(coverage),
            side_outputs=frozen_copy({
                "source_paper": filename, "terms": terms, "code_snippets": snippets,
                "metadata": dict(self.extractor.metadata),
            }),
            inference=self._inference(),
        )


def mentions_for_term(term: dict[str, Any], filename: str, *, extracted_by: str = STRATEGY_NAME
                      ) -> list[ExtractedMention]:
    """One mention per (page, snippet) of this file for one terms-JSON entry (shared by the agent strategy)."""
    pages = list(term.get("pages") or [])
    snippets = [s for s in term.get("context_snippets") or [] if s.get("source_paper") == filename]
    attributes = {k: frozen_copy(term[k]) for k in _CHEMICAL_FIELDS if term.get(k) is not None}
    if term.get("relations"):
        attributes["relations"] = frozen_copy(term["relations"])
    if term.get("properties"):
        attributes["properties"] = frozen_copy(term["properties"])
    base: dict[str, Any] = dict(surface=str(term.get("term", "")), category=str(term.get("category") or "Thing"),
                canonical=False, slug=str(term.get("term", "")).strip().lower(), extracted_by=extracted_by,
                attributes=attributes)
    if not snippets:
        page: int | None = pages[0] if pages else None
        return [ExtractedMention(page=page, **base)]
    return [
        ExtractedMention(page=s.get("page"), chunk_id=f"page:{s.get('page')}", quote=str(s.get("text") or ""),
                         **base)
        for s in snippets
    ]


_mentions_for = mentions_for_term

__all__ = ["STRATEGY_NAME", "STRATEGY_VERSION", "LegacyF2WStrategy", "TextPages", "mentions_for_term"]
