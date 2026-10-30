"""LangChain tools the term-extraction agent calls to look up, validate and register terms."""

from dataclasses import dataclass, field
import json
import logging
import re
import threading
from typing import Callable

from langchain_core.tools import BaseTool, tool

from .merge_index import TermMergeIndex, build_merge_prompt, clean_term
from .models import ContextSnippet, RelationRecord, TermRecord
from .schema import SchemaHelper
from .services import Services
from .store import TermStore

logger = logging.getLogger(__name__)


@dataclass
class ToolState:
    """Shared mutable state handed to every tool built by ``build_tools``.

    Holds the term store, schema helper, services, optional LLM callable and
    merge index, plus the PDF/page context of the extraction in progress.
    """

    store: TermStore
    schema: SchemaHelper
    services: Services
    llm_invoke: Callable[[str], str] | None = field(default=None, repr=False)
    merge_index: TermMergeIndex | None = field(default=None, repr=False)
    current_source_paper: str | None = None
    _tls: threading.local = field(default_factory=threading.local, repr=False)

    def set_page_context(self, source_paper: str, page: int) -> None:
        """Record the PDF being extracted.

        ``current_source_paper`` is shared (all workers of one PDF). Page is
        thread-local because LangGraph may run tools on another thread than
        ``process_page``.
        """
        self.current_source_paper = source_paper
        self._tls.source_paper = source_paper
        self._tls.page = page

    def clear_page_context(self) -> None:
        """Reset the thread-local source paper and page (the shared paper name is kept)."""
        self._tls.source_paper = None
        self._tls.page = None

    def page_context(self) -> tuple[str | None, int | None]:
        """Return ``(source_paper, page)`` for the current thread.

        The paper falls back to the shared ``current_source_paper`` when this
        thread has none; the page is ``None`` if it was never set on this thread.
        """
        paper = getattr(self._tls, "source_paper", None) or self.current_source_paper
        return paper, getattr(self._tls, "page", None)


def build_tools(state: ToolState) -> list[BaseTool]:
    """Build the agent tool list as closures over ``state``.

    Args:
        state: Shared tool state used by every returned tool.

    Returns:
        LangChain tools: check_existing_term, fuzzy_merge_term, validate_formula,
        repair_formula, lookup_chebi and register_term.
    """
    @tool
    def check_existing_term(name: str) -> str:
        """Check whether a term already exists in the knowledge base.

        Returns 'exact_match:<key>', 'possible_matches:<csv>', or 'not_found'.
        If 'not_found', call fuzzy_merge_term before registering.
        """
        key = TermStore.normalize(name)
        logger.debug("check_existing_term: '%s'", name)
        if state.store.get(key) is not None:
            return f"exact_match:{key}"
        name_lower = name.lower()
        candidates = [
            display for display in state.store.all_display_names()
            if name_lower in display.lower() or display.lower() in name_lower
        ]
        if candidates:
            logger.debug("check_existing_term: '%s' → possible_matches %s", name, candidates[:5])
            return "possible_matches:" + ",".join(candidates[:5])
        return "not_found"

    @tool
    def fuzzy_merge_term(name: str) -> str:
        """Ask the LLM whether a new term matches an already-registered term.

        Compares against the nearest registered terms (punctuation variants and
        acronym↔expansion pairs).
        Call this when check_existing_term returns 'not_found'.
        Returns 'match:<key>' if matched to an existing term, or 'no_match'.
        """
        if not state.llm_invoke:
            return "no_match"
        cleaned = clean_term(name)
        if not cleaned:
            return "no_match"
        merge_index = state.merge_index
        if merge_index is None:
            logger.warning("fuzzy_merge_term: no merge index; skipping LLM merge for '%s'", name)
            return "no_match"
        merge_index.ensure(state.store.all_display_names())
        candidates = merge_index.query(cleaned)
        if not candidates:
            return "no_match"
        prompt = build_merge_prompt(cleaned, candidates)
        try:
            response = (state.llm_invoke(prompt) or "").strip()
        except Exception as e:
            logger.warning("fuzzy_merge_term: LLM call failed for '%s': %s", name, e)
            return "no_match"

        allowed = set(candidates)
        if response in allowed:
            key = TermStore.normalize(response)
            logger.info("fuzzy_merge_term: '%s' → matched '%s' (key=%s)", name, response, key)
            return f"match:{key}"

        return "no_match"

    @tool
    def validate_formula(formula: str) -> str:
        """Validate a chemical formula string against the Materials Project database."""
        logger.info("validate_formula: '%s'", formula)
        try:
            result = state.services.formula_checker.validate(formula)
            logger.info("validate_formula: '%s' → %s", formula, result.get("status"))
            return json.dumps(result)
        except Exception as e:
            logger.warning("validate_formula: error for '%s': %s", formula, e)
            return json.dumps({"status": "error", "error": str(e)})

    @tool
    def repair_formula(formula: str, context: str) -> str:
        """Validate a chemical formula and repair it via LLM if invalid.

        Pass the surrounding sentence as context to help guess the correct formula.
        Returns JSON with keys: status ('valid'|'corrected'|'invalid'|'missing'|'error'),
        formula (possibly corrected), and original (only when corrected).
        """
        if not formula or not re.search(r"[A-Z][a-z]?[\d]", formula):
            return json.dumps({"status": "missing", "formula": None})
        try:
            validation = state.services.formula_checker.validate(formula)
        except Exception as e:
            logger.warning("repair_formula: validation error for '%s': %s", formula, e)
            return json.dumps({"status": "error", "details": {"error": str(e)}})

        if validation.get("status") != "invalid" or not state.llm_invoke:
            return json.dumps({**validation, "formula": formula})

        repair_prompt = (
            f"The extracted string '{formula}' is not a valid chemical formula.\n"
            "Based on the context below, guess the correct formula and return ONLY the formula string.\n\n"
            f"CONTEXT:\n{context}"
        )
        try:
            candidate = (state.llm_invoke(repair_prompt) or "").strip().split()[0]
        except Exception as e:
            logger.warning("repair_formula: LLM repair failed for '%s': %s", formula, e)
            return json.dumps({**validation, "formula": formula})

        if not candidate or candidate == formula:
            return json.dumps({**validation, "formula": formula})

        try:
            new_validation = state.services.formula_checker.validate(candidate)
            if new_validation.get("status") != "invalid":
                logger.info("repair_formula: corrected '%s' → '%s'", formula, candidate)
                new_validation["status"] = "corrected"
                return json.dumps({**new_validation, "formula": candidate, "original": formula})
        except Exception as e:
            logger.warning("repair_formula: re-validation failed for candidate '%s': %s", candidate, e)

        return json.dumps({**validation, "formula": formula})

    @tool
    def lookup_chebi(name: str) -> str:
        """Look up a chemical entity by name in the ChEBI ontology.

        Returns formula, mass, charge, SMILES, InChI, InChIKey when found.
        """
        logger.info("lookup_chebi: '%s'", name)
        if not state.services.chebi_lookup:
            return "ChEBI not available"
        try:
            info = state.services.chebi_lookup.lookup(name)
            logger.info("lookup_chebi: '%s' → %s", name, "found" if info else "not_found")
            return json.dumps(info) if info else "not_found"
        except Exception as e:
            logger.warning("lookup_chebi: error for '%s': %s", name, e)
            return f"error:{e}"

    @tool
    def register_term(
        term: str,
        definition: str,
        category: str,
        formula: str | None = None,
        relations: list[dict[str, str]] | None = None,
        source_paper: str | None = None,
        page: int | None = None,
        context: str | None = None,
    ) -> str:
        """Register or update an extracted materials-science term.

        Call even when check_existing_term or fuzzy_merge_term found a match so
        relations, context, and paper provenance can be merged onto the existing term.
        Relations must use exact predicate names from the schema.
        Pass a short context sentence as 'context'. source_paper and page default
        to the PDF page currently being processed.
        """
        ctx_paper, ctx_page = state.page_context()
        source_paper = source_paper or ctx_paper
        if page is None:
            page = ctx_page
        logger.info("register_term: '%s' category=%s", term, category)
        raw = {
            "term": term,
            "definition": definition,
            "category": category,
            "formula": formula,
            "relations": relations or [],
        }
        fixed = state.schema.validate_and_fix_term(raw)

        # Domain/range validation: downgrade verified=True relations that violate schema constraints.
        subj_cat = fixed.get("category", "")
        for rel in fixed.get("relations", []):
            if not rel.get("verified", False):
                continue
            obj_key = TermStore.normalize(rel.get("related_term", ""))
            obj_record = state.store.get(obj_key)
            obj_cat = obj_record.category if obj_record else ""
            if subj_cat and obj_cat and not state.schema.check_relation_validity(
                subj_cat, rel["relation"], obj_cat
            ):
                logger.warning(
                    "register_term: relation '%s' invalid for %s→%s, marking verified=False",
                    rel["relation"], subj_cat, obj_cat,
                )
                rel["verified"] = False

        key = TermStore.normalize(fixed["term"])
        is_new = state.store.get(key) is None

        snippet = (
            ContextSnippet(text=context, source_paper=source_paper, page=page)
            if context and source_paper and page is not None
            else None
        )

        record = TermRecord(
            term=fixed["term"],
            definition=fixed.get("definition", ""),
            category=fixed.get("category", "Thing"),
            raw_category=fixed.get("raw_category"),
            formula=fixed.get("formula"),
            relations=[RelationRecord.from_dict(r) for r in fixed.get("relations", [])],
            pages=[page] if page is not None else [],
            source_papers=[source_paper] if source_paper else [],
            context_snippets=[snippet] if snippet else [],
        )

        final_key, _ = state.store.upsert(record)
        if is_new and state.merge_index is not None:
            state.merge_index.add(fixed["term"])
        action = "registered" if is_new else "updated"
        logger.info("register_term: %s '%s' (key=%s)", action, term, final_key)
        return action + f":{final_key}"

    return [check_existing_term, fuzzy_merge_term, validate_formula, repair_formula, lookup_chebi, register_term]
