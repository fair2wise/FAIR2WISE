import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from langchain_core.tools import tool

from .models import ContextSnippet, RelationRecord, TermRecord
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
        Returns 'exact_match:<key>', 'possible_matches:<csv>', or 'not_found'.
        If 'not_found', call fuzzy_merge_term before registering."""
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
        """Ask the LLM whether a newly extracted term is semantically equivalent to any
        already-registered term, handling punctuation variants and acronym↔expansion pairs.
        Call this when check_existing_term returns 'not_found'.
        Returns 'match:<key>' if matched to an existing term, or 'no_match'."""
        if not state.llm_invoke:
            return "no_match"
        existing = state.store.all_display_names()
        if not existing:
            return "no_match"

        bullets = "\n".join(f"- {d}" for d in existing)
        prompt = f"""
We have just extracted a new term:    "{name}"
Below is the list of all already-registered terms (one per line; the first time we saw each term):
{bullets}

You must decide whether "{name}" refers to exactly the same concept as one of these,
or if it is a distinct new concept. Follow these rules:

  1. **Ignore only trivial punctuation (spaces, hyphens, slashes, brackets, parentheses, capitalization)**
     when comparing.  For example, "GIWAXS" and "GI-WAXS" are the *same* technique and should be merged
     (choose the variant already in the list).  Likewise, "XRD" and "X-RD" (if it appeared) are identical.
     Anything beyond punctuation differences (letters, numbers, or added qualifiers) is not trivial.

  2. **Do NOT merge distinct instrument or method acronyms**.  Even if two acronyms share letters, if they are
     known to be different techniques or materials, keep them separate.
     Examples you must treat as always distinct:
       - "SEM" (scanning electron microscopy) vs. "TEM" (transmission electron microscopy)
       - "AFM" (atomic force microscopy)
       - "XPS" vs. "UPS"
       - "MoTe2" vs. "WTe2" (different compounds)
       - "Al2O3[0001]" (specific surface) vs. "Al2O3" (generic material)
     In other words, if two strings differ by more than punctuation—by letters, numbers
     or explicit qualifiers—they should not be merged.

  3. **Do NOT merge general vs. specific variants**.
     If one term is a broader concept (e.g. "band structure") and another is a specialized version
     (e.g. "Dirac-like band structure"), treat them as distinct.
     Similarly, if a term includes an added qualifier or context
     (e.g. surface orientation "[0001]" vs. generic material), do not merge into a more general term.

  4. **If the newly extracted term is an exact punctuation-agnostic match** to one of the existing
     terms—i.e., removing or changing only punctuation/brackets/spaces/case makes them identical—then respond
     with exactly that already-registered term (preserve its original casing/spelling).
     Otherwise, respond `"None"`.

  5. **DO merge terms if one is the acronym for the other term**, vice versa,
      or one term includes the acronym and the other doesn't
      For example, "angle-resolved photoelectric spectroscopy" and "ARPES" should merge to become:
      "angle-resolved photoemission spectroscopy (ARPES)".
      Another example: "resonant soft xray scattering" or "R-SoXS" should merge to become:
      "Resonant soft xray scattering (RSoXS)"

  6. **Your response must be exactly one line**: either the exact existing term (matching punctuation
     and case as it appears above) or the single word `None`. Don't output anything else—no quotes,
     no extra commentary.

Here are additional examples to illustrate:

  • If the new term is `"GI-WAXS"` and the list already contains `"GIWAXS"`, respond exactly `"GIWAXS"`.
  • If the new term is `"RSoXS"` and the list already contains `"R-SoXS"`,
    respond exactly `"RSoXS" as the correct term`.
  • If the new term is `"SEM"` and the list contains `"SEM"`, respond `"SEM"`, but if the list contains
    only `"TEM"`, respond `"None"` (distinct acronyms).
  • If the new term is `"MoTe2"` and the list has `"WTe2"`, respond `"None"` (different compound).
  • If the new term is `"Band-structure"` and the list has `"Dirac-like band structure"`, respond `"None"`
    (general vs. specific).
  • If the new term is `"Al2O3[0001]"` and the list has `"Al2O3"`, respond `"None"` (surface-specific vs. generic).
  • If the new term is `"photoemission"` and the list has `"angle-resolved photoemission spectroscopy (ARPES)"`,
    respond `"None"` (general process vs. specific technique).
  • If the new term is `"X-RD"` and the list has `"XRD"`, respond `"XRD"`
    (consistent acronym once punctuation is removed).
  • "organic solar cells" and "OSCs" should merge to become "Organic solar cells (OSCs)"

Now, having read the rules, please answer: which of the above existing terms is exactly the same concept
as "{name}"?  If none match, respond with `None`.
"""
        try:
            response = (state.llm_invoke(prompt) or "").strip()
        except Exception as e:
            logger.warning("fuzzy_merge_term: LLM call failed for '%s': %s", name, e)
            return "no_match"

        existing_set = set(existing)
        if response in existing_set:
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
        formula: Optional[str] = None,
        relations: Optional[List[Dict[str, str]]] = None,
        source_paper: Optional[str] = None,
        page: Optional[int] = None,
        context: Optional[str] = None,
    ) -> str:
        """Register an extracted materials-science term into the knowledge base.
        Call check_existing_term then fuzzy_merge_term first to avoid duplicates.
        Relations must use exact predicate names from the schema.
        Pass a short context sentence (the sentence where the term appears) as 'context'."""
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
            formula=fixed.get("formula"),
            relations=[RelationRecord.from_dict(r) for r in fixed.get("relations", [])],
            pages=[page] if page else [],
            source_papers=[source_paper] if source_paper else [],
            context_snippets=[snippet] if snippet else [],
        )

        final_key, _ = state.store.upsert(record)
        action = "registered" if is_new else "updated"
        logger.info("register_term: %s '%s' (key=%s)", action, term, final_key)
        return action + f":{final_key}"

    return [check_existing_term, fuzzy_merge_term, validate_formula, repair_formula, lookup_chebi, register_term]
