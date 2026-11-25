"""LangChain tools the term-extraction agent calls to look up, validate and register terms."""

from collections.abc import Callable
import contextvars
from dataclasses import dataclass, field
import json
import logging
import re

from langchain_core.tools import BaseTool, tool

from . import passages
from .merge_index import TermMergeIndex, build_merge_prompt, clean_term
from .models import ContextSnippet, PropertyRecord, RelationRecord, TermRecord, union_evidence
from .schema import SchemaHelper, judge_relations, mark_invalid_relations
from .services import Services
from .store import TermStore

logger = logging.getLogger(__name__)

# Max candidate display names returned by check_existing_term's "possible_matches:" reply.
POSSIBLE_MATCHES_LIMIT = 5


@dataclass(frozen=True)
class _PageContext:
    """The page a tool call belongs to: which ``ToolState`` set it, the paper, the page number and the page text."""

    owner: int
    source_paper: str | None
    page: int | None
    text: str | None = None


#: The page being extracted, per execution context (see :meth:`ToolState.set_page_context`).
_PAGE: contextvars.ContextVar[_PageContext | None] = contextvars.ContextVar("f2w_extract_page", default=None)


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
    #: ``(paper file name, every page text of that paper)``, set once per paper by the Orchestrator (the PDF is read
    #: once); lets grounding find a sentence or a mention on a page other than the one being extracted.
    paper_pages: tuple[str, list[str]] | None = field(default=None, repr=False)

    def set_page_context(self, source_paper: str, page: int, text: str | None = None) -> None:
        """Record the PDF page being extracted.

        ``current_source_paper`` is shared (all workers of one PDF). The page and its text live in a
        :class:`contextvars.ContextVar`: each page is processed on its own thread, and the tool node of the agent graph
        runs the tools on *other* threads that receive a **copy of the calling context** (LangGraph's
        ``ContextThreadPoolExecutor``), so the page reaches the tools. A plain ``threading.local`` did not: the tools
        saw no page, ``register_term`` dropped the page and the supporting-text snippet, and every extracted node
        came out without passages (pilot 2026-11-24).
        """
        self.current_source_paper = source_paper
        _PAGE.set(_PageContext(id(self), source_paper, page, text))

    def clear_page_context(self) -> None:
        """Reset the page of this context (the shared paper name is kept)."""
        current = _PAGE.get()
        if current is not None and current.owner == id(self):
            _PAGE.set(None)

    def page_text(self) -> str | None:
        """Text of the page being extracted in this context (``None`` when the Orchestrator did not pass it)."""
        current = _PAGE.get()
        return current.text if current is not None and current.owner == id(self) else None

    def ground(self, term_key: str, term: str, context: str | None, source_paper: str | None, page: int | None) -> ContextSnippet | None:
        """Ground a term's supporting sentence against the page texts and count the outcome (design b).

        Args:
            term_key: Normalised term key (for the per-paper coverage counters).
            term: Display name of the term.
            context: The model's sentence, if any.
            source_paper: Paper the term is registered for.
            page: 1-based page, if known.

        Returns:
            The passage to store, or ``None`` (a rejected sentence is counted, never stored).
        """
        if not source_paper:
            return None
        pages = self.paper_pages[1] if self.paper_pages and self.paper_pages[0] == source_paper else None
        result = passages.ground_context(
            term=term, sentence=context, source_paper=source_paper, page=page, page_text=self.page_text(), paper_pages=pages,
        )
        snippet = result.snippet
        self.store.note_passage(source_paper, term_key, kind=snippet.kind if snippet else None, rejected=int(result.rejected))
        if result.rejected:
            logger.info("ground: sentence for '%s' not found in %s; rejected", term, source_paper)
        return snippet

    def attach_relation_evidence(self, term: str, relations: list[RelationRecord], source_paper: str | None,
                                 page: int | None) -> None:
        """Locate, for each relation, the sentences of the paper that name both endpoints and set them as its evidence.

        Located only, no model: a sentence naming just one endpoint is never evidence, and the term's own context sentence
        is used only when it names both (design: relationship evidence). The outcome is counted per paper.
        """
        if not source_paper or not relations:
            return
        pages = self.paper_pages[1] if self.paper_pages and self.paper_pages[0] == source_paper else None
        subject_key = TermStore.normalize(term)
        for relation in relations:
            entries = passages.relation_evidence_for(
                subject=term, obj=relation.related_term, source_paper=source_paper, page=page,
                page_text=self.page_text(), paper_pages=pages)
            union_evidence(relation.evidence, entries)
            self.store.note_relation(
                source_paper, f"{subject_key}|{relation.relation}|{TermStore.normalize(relation.related_term)}", len(entries))

    def page_context(self) -> tuple[str | None, int | None]:
        """Return ``(source_paper, page)`` for the current context.

        The paper falls back to the shared ``current_source_paper`` when this context has none; the page is ``None``
        if it was never set in a context this one descends from.
        """
        current = _PAGE.get()
        if current is not None and current.owner == id(self):
            return current.source_paper or self.current_source_paper, current.page
        return self.current_source_paper, None


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
            logger.debug("check_existing_term: '%s' → possible_matches %s", name,
                         candidates[:POSSIBLE_MATCHES_LIMIT])
            return "possible_matches:" + ",".join(candidates[:POSSIBLE_MATCHES_LIMIT])
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
        Pass a short context sentence as 'context', copied from the page. source_paper and
        page default to the PDF page currently being processed. The sentence is kept only if it is
        found in the paper's text (the stored page is where it is found); otherwise the passage is
        located from the term's mention, and a term with no findable mention gets no passage.
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

        def category_of(name: str) -> str:
            record = state.store.get(TermStore.normalize(name))
            return record.category if record else ""

        # A user or model-written schema: a literal-range slot is an attribute of the term, and a relation that is valid
        # only with subject and object exchanged is stored the right way round (shared with the schema preview).
        judged = judge_relations(state.schema, fixed, category_of)
        for item in judged.attributes:
            state.store.note_relation_check("attribute", item["property"], subj_cat, "")
        for subject_class, pred, object_class in [*judged.invalid, *mark_invalid_relations(state.schema, fixed, category_of)]:
            state.store.note_relation_check("invalid", pred, subject_class, object_class)

        key = TermStore.normalize(fixed["term"])
        is_new = state.store.get(key) is None

        # Grounding (design b): the model's sentence is kept only if found in the paper's page text, on the page where
        # it is found; otherwise the term's mention is located; nothing found stores nothing and counts as uncovered.
        snippet = state.ground(key, fixed["term"], context, source_paper, page)

        relation_records = [RelationRecord.from_dict(r) for r in fixed.get("relations", [])]
        state.attach_relation_evidence(fixed["term"], relation_records, source_paper, page)

        record = TermRecord(
            term=fixed["term"],
            definition=fixed.get("definition", ""),
            category=fixed.get("category", "Thing"),
            raw_category=fixed.get("raw_category"),
            formula=fixed.get("formula"),
            relations=relation_records,
            pages=[page] if page is not None else [],
            source_papers=[source_paper] if source_paper else [],
            context_snippets=[snippet] if snippet else [],
        )

        record.properties.extend(
            PropertyRecord(property=item["property"], value=item["value"], unit="", context=(context or "")[:500])
            for item in judged.attributes)
        final_key, _ = state.store.upsert(record)
        for item in judged.swapped:   # the real subject is the other term: the relation is stored on it, verified
            owner = state.store.get(TermStore.normalize(item["subject"]))
            if owner is None:
                continue
            swapped = RelationRecord(relation=item["relation"], related_term=fixed["term"], verified=True)
            state.attach_relation_evidence(owner.term, [swapped], source_paper, page)
            state.store.add_relation(owner.term, swapped)
            state.store.note_relation_check("swapped", item["relation"], item["subject_class"], item["object_class"])
        if is_new and state.merge_index is not None:
            state.merge_index.add(fixed["term"])
        action = "registered" if is_new else "updated"
        logger.info("register_term: %s '%s' (key=%s)", action, term, final_key)
        return action + f":{final_key}"

    return [check_existing_term, fuzzy_merge_term, validate_formula, repair_formula, lookup_chebi, register_term]
