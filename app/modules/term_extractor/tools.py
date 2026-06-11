import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from langchain_core.tools import tool

from .models import RelationRecord, TermRecord
from .schema import SchemaHelper
from .services import Services
from .store import TermStore


@dataclass
class ToolState:
    store: TermStore
    schema: SchemaHelper
    services: Services


def build_tools(state: ToolState) -> list:
    @tool
    def check_existing_term(name: str) -> str:
        """Check whether a term already exists in the knowledge base.
        Returns 'exact_match:<key>', 'possible_matches:<csv>', or 'not_found'."""
        key = TermStore.normalize(name)
        if state.store.get(key) is not None:
            return f"exact_match:{key}"
        name_lower = name.lower()
        candidates = [
            display for display in state.store.all_display_names()
            if name_lower in display.lower() or display.lower() in name_lower
        ]
        if candidates:
            return "possible_matches:" + ",".join(candidates[:5])
        return "not_found"

    @tool
    def validate_formula(formula: str) -> str:
        """Validate a chemical formula string against the Materials Project database."""
        try:
            result = state.services.formula_checker.validate(formula)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @tool
    def lookup_chebi(name: str) -> str:
        """Look up a chemical entity by name in the ChEBI ontology.
        Returns formula, mass, charge, SMILES, InChI, InChIKey when found."""
        if not state.services.chebi_lookup:
            return "ChEBI not available"
        try:
            info = state.services.chebi_lookup.lookup(name)
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
        return ("registered" if is_new else "updated") + f":{final_key}"

    return [check_existing_term, validate_formula, lookup_chebi, register_term]
