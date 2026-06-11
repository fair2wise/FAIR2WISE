import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool


@dataclass
class ToolState:
    terms_dict: Dict[str, Any]
    bk_terms: Dict[str, str]
    state_lock: object
    schema_helper: object
    formula_checker: object
    chebi_lookup: object
    mark_updated: Callable[[], None]


def build_tools(state: ToolState) -> list:
    @tool
    def check_existing_term(name: str) -> str:
        """Check whether a term already exists in the knowledge base.
        Returns 'exact_match:<key>', 'possible_matches:<csv>', or 'not_found'."""
        key = name.strip().lower()
        with state.state_lock:
            if key in state.terms_dict:
                return f"exact_match:{key}"
            name_lower = name.lower()
            candidates = []
            for display in state.bk_terms:
                dl = display.lower()
                if name_lower in dl or dl in name_lower:
                    candidates.append(display)
        if candidates:
            return "possible_matches:" + ",".join(candidates[:5])
        return "not_found"

    @tool
    def validate_formula(formula: str) -> str:
        """Validate a chemical formula string against the Materials Project database."""
        try:
            result = state.formula_checker.validate(formula)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @tool
    def lookup_chebi(name: str) -> str:
        """Look up a chemical entity by name in the ChEBI ontology.
        Returns formula, mass, charge, SMILES, InChI, InChIKey when found."""
        if not state.chebi_lookup:
            return "ChEBI not available"
        try:
            info = state.chebi_lookup.lookup(name)
            return json.dumps(info) if info else "not_found"
        except Exception as e:
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
        raw = {
            "term": term,
            "definition": definition,
            "category": category,
            "formula": formula,
            "relations": relations or [],
        }
        fixed = state.schema_helper.validate_and_fix_term(raw)
        key = fixed["term"].strip().lower()

        with state.state_lock:
            if key in state.terms_dict:
                entry = state.terms_dict[key]
                updated = False
                if page and page not in entry.get("pages", []):
                    entry.setdefault("pages", []).append(page)
                    updated = True
                if source_paper and source_paper not in entry.get("source_papers", []):
                    entry.setdefault("source_papers", []).append(source_paper)
                    updated = True
                new_def = fixed.get("definition", "")
                if len(new_def) > len(entry.get("definition", "")):
                    entry["definition"] = new_def
                    updated = True
                existing_rel_tups = {(r["relation"], r["related_term"]) for r in entry.get("relations", [])}
                for rel in fixed.get("relations", []):
                    tup = (rel["relation"], rel["related_term"])
                    if tup not in existing_rel_tups:
                        entry.setdefault("relations", []).append(rel)
                        existing_rel_tups.add(tup)
                        updated = True
                if updated:
                    state.mark_updated()
                return f"updated:{key}"

            entry: Dict[str, Any] = {
                "term": fixed["term"],
                "definition": fixed.get("definition", ""),
                "category": fixed.get("category", "Thing"),
                "formula": fixed.get("formula"),
                "formula_validation": fixed.get("formula_validation"),
                "relations": fixed.get("relations", []),
                "pages": [page] if page else [],
                "source_papers": [source_paper] if source_paper else [],
                "context_snippets": [],
                "properties": [],
            }
            state.terms_dict[key] = entry
            state.bk_terms[fixed["term"]] = key
            state.mark_updated()
            return f"registered:{key}"

    return [check_existing_term, validate_formula, lookup_chebi, register_term]
