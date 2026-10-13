"""Synthetic extracted-terms fixtures for the T19B paper-summary tests.

Every term, definition and filename is invented; no real paper text is used.
Each case writes ``terms.json`` and ``extraction_manifest.json`` into a
directory and returns the arguments the legacy callers pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

FILENAME = "synthetic_paper.pdf"
OTHER = "other_paper.pdf"


def term(name: str, pages: List[int], *, definition: str = "", category: Optional[str] = "Method",
         papers: Optional[List[str]] = None, snippets: Optional[List[Dict[str, Any]]] = None,
         raw_category: Optional[str] = None) -> Dict[str, Any]:
    """One extracted-term record in the session terms file format."""
    record: Dict[str, Any] = {"term": name, "definition": definition or f"Synthetic definition of {name}.",
                              "pages": pages, "source_papers": papers if papers is not None else [FILENAME]}
    if category is not None:
        record["category"] = category
    if raw_category is not None:
        record["raw_category"] = raw_category
    if snippets is not None:
        record["context_snippets"] = snippets
    return record


def write_case(root: Path, entry: Dict[str, Any], terms: Any, *, filename: str = FILENAME) -> Dict[str, str]:
    """Write the two files and return ``{terms_path, manifest_path, filename}``."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "extraction_manifest.json"
    terms_path = root / "terms.json"
    manifest.write_text(json.dumps({"papers": {filename: entry}}), encoding="utf-8")
    body = terms if not isinstance(terms, list) else {"terms": terms}
    terms_path.write_text(json.dumps(body), encoding="utf-8")
    return {"terms_path": str(terms_path), "manifest_path": str(manifest), "filename": filename}


def many_terms(count: int, pages: List[int]) -> List[Dict[str, Any]]:
    """``count`` distinct synthetic terms spread round-robin over ``pages``."""
    return [term(f"Synthetic concept {i:02d}", [pages[i % len(pages)]], category="Property")
            for i in range(count)]


#: Named synthetic cases: (manifest entry, term records). Built by ``CASES[name]()``.
def _cases() -> Dict[str, Any]:
    snip = [{"source_paper": f"/tmp/x/{FILENAME}", "page": 3, "text": "synthetic"},
            {"source_paper": OTHER, "page": 1, "text": "other file"}]
    return {
        # top-level selected_pages; page 1 term is ineligible
        "targeted": ({"extraction_state": "partial", "selected_pages": [2, 3]},
                     [term("Eligible alpha", [2]), term("Hidden beta", [1]),
                      term("Snippet gamma", [1, 3], snippets=snip), term("Other paper delta", [2], papers=[OTHER]),
                      term("No category eps", [3], category=None, raw_category="RawKind"),
                      term("Whitespace  zeta", [2], definition="  spread\n over   lines  ")]),
        # no top-level list: latest partial's selected_pages
        "latest_partial": ({"extraction_state": "partial",
                            "partials": [{"selected_pages": [1]}, {"selected_pages": [4]}]},
                           [term("Early term", [1]), term("Late term", [4])]),
        # D-10: full extraction, no top-level list -> eligibility from the terms' own pages,
        # and full.selected_pages is ignored (unlike _eligible_pages)
        "full_term_pages": ({"extraction_state": "full", "full": {"selected_pages": [1]}},
                            [term("Full page five", [5]), term("Full page two", [2]),
                             term("Other file page nine", [9], papers=[OTHER])]),
        # duplicate labels count separately
        "duplicates": ({"extraction_state": "partial", "selected_pages": [1, 2]},
                       [term("Repeated label", [1]), term("Repeated label", [2]), term("Single label", [2])]),
        # more records than the display cap
        "capped": ({"extraction_state": "partial", "selected_pages": [1, 2, 3, 4]}, many_terms(23, [1, 2, 3, 4])),
        # nothing on eligible pages
        "insufficient": ({"extraction_state": "partial", "selected_pages": [7]}, [term("Off page", [1])]),
    }


CASES = _cases()

QUERY_TERMS = [
    term("Ionic conductivity", [2], definition="Synthetic ion transport property.", category="Property"),
    term("Electrolyte additive", [2], definition="Synthetic battery electrolyte component.", category="Material"),
    term("Sample holder", [3], definition="Synthetic hardware.", category="Equipment"),
    term("Beam alignment", [3], definition="Synthetic setup step.", category="Procedure"),
]
