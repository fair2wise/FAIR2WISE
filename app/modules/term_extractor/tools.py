import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from langchain_core.tools import tool

from .models import RelationRecord, TermRecord
from .schema import SchemaHelper
from .services import Services
from .store import TermStore

logger = logging.getLogger(__name__)


@dataclass
class ToolState:
    store: TermStore
    schema: SchemaHelper
    services: Services
    llm_invoke: Optional[Callable[[str], str]] = field(default=None, repr=False)


def build_tools(state: ToolState) -> list:
    @tool
    def check_existing_term(name: str) -> str:
        """Check whether a term already exists in the knowledge base.
        Returns 'exact_match:<key>', 'possible_matches:<csv>', or 'not_found'."""
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
    def validate_formula(formula: str) -> str:
        """Validate a chemical formula string against the Materials Project database."""
        logger.debug("validate_formula: '%s'", formula)
        try:
            result = state.services.formula_checker.validate(formula)
            logger.debug("validate_formula: '%s' → %s", formula, result.get("status"))
            return json.dumps(result)
        except Exception as e:
            logger.warning("validate_formula: error for '%s': %s", formula, e)
            return json.dumps({"status": "error", "error": str(e)})

    @tool
    def repair_formula(formula: str, context: str) -> str:
        """Validate a chemical formula and repair it via LLM if invalid.
        Pass the surrounding sentence as context to help guess the correct formula.
        Returns JSON with keys: status ('valid'|'corrected'|'invalid'|'missing'|'error'),
        formula (possibly corrected), and original (only when corrected)."""
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
        Returns formula, mass, charge, SMILES, InChI, InChIKey when found."""
        logger.debug("lookup_chebi: '%s'", name)
        if not state.services.chebi_lookup:
            return "ChEBI not available"
        try:
            info = state.services.chebi_lookup.lookup(name)
            logger.debug("lookup_chebi: '%s' → %s", name, "found" if info else "not_found")
            return json.dumps(info) if info else "not_found"
        except Exception as e:
            logger.warning("lookup_chebi: error for '%s': %s", name, e)
            return f"error:{e}"

    @tool
    def register_term(
        term: str,
        definition: str,
        category: str,
        formula: Optional[str] = None,
        relations: Optional[List[Dict[str, str]]] = None,
        source_paper: Optional[str] = None,
        page: Optional[int] = None,
    ) -> str:
        """Register an extracted materials-science term into the knowledge base.
        Call check_existing_term first to avoid duplicates. Relations must use exact
        predicate names from the schema."""
        logger.debug("register_term: '%s' category=%s", term, category)
        raw = {
            "term": term,
            "definition": definition,
            "category": category,
            "formula": formula,
            "relations": relations or [],
        }
        fixed = state.schema.validate_and_fix_term(raw)

        key = TermStore.normalize(fixed["term"])
        is_new = state.store.get(key) is None

        record = TermRecord(
            term=fixed["term"],
            definition=fixed.get("definition", ""),
            category=fixed.get("category", "Thing"),
            formula=fixed.get("formula"),
            relations=[RelationRecord.from_dict(r) for r in fixed.get("relations", [])],
            pages=[page] if page else [],
            source_papers=[source_paper] if source_paper else [],
        )

        final_key, _ = state.store.upsert(record)
        action = "registered" if is_new else "updated"
        logger.info("register_term: %s '%s' (key=%s)", action, term, final_key)
        return action + f":{final_key}"

    return [check_existing_term, validate_formula, repair_formula, lookup_chebi, register_term]
