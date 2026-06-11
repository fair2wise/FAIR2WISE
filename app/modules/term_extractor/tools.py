from typing import Dict, List, Optional
from langchain_core.tools import tool


@tool
def register_term(
    term: str,
    definition: str,
    category: str,
    formula: Optional[str] = None,
    relations: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Register an extracted materials-science term into the knowledge base."""
    pass


@tool
def lookup_chebi(name: str) -> str:
    """Look up a chemical entity by name in the ChEBI ontology."""
    pass


@tool
def validate_formula(formula: str) -> str:
    """Validate a chemical formula string against the Materials Project."""
    pass


@tool
def check_existing_term(name: str) -> str:
    """Check whether a term already exists in the knowledge base. Returns matched key or null."""
    pass


TOOLS = [register_term, lookup_chebi, validate_formula, check_existing_term]
