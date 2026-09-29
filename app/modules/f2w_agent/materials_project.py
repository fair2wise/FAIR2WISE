"""Materials Project lookup for specific-material chat questions.

Only fires when the question names a formula or ``mp-`` id. General RSoXS /
beamline questions stay on the KGs. Requires ``MP_API_KEY``; never logs the key.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

MP_ID_RE = re.compile(r"\bmp-\d+\b", re.IGNORECASE)
PROPERTY_RE = re.compile(
    r"\b(band\s*gaps?|space\s*groups?|crystal\s*structure|lattice(?:\s+parameters?)?|"
    r"formation\s+energy|energy\s+above\s+hull|bulk\s+modulus|density|"
    r"materials?\s+project|mp[- ]?id|is\s+it\s+stable|stable\s+phase)\b",
    re.IGNORECASE,
)
# Token that looks like a Hill/formula string (TiO2, MoS2, CH3NH3PbI3).
FORMULA_TOKEN_RE = re.compile(r"\b((?:[A-Z][a-z]?\d*){1,10})\b")
_STOP_TOKENS = {
    "RSoXS", "ALS", "ESAF", "PDF", "KG", "OPS", "MP", "API", "UI", "HTTP",
    "DOI", "PV", "EPU", "BHJ", "OPV", "NRSS", "Nika", "In", "As", "At",
    "He", "Be", "I", "K", "V", "W", "U", "P", "S", "C", "N", "O", "H",
    "Si",  # too short alone; allow SiO2 / SiC via digit or 2nd element
}

_SUMMARY_FIELDS = [
    "material_id",
    "formula_pretty",
    "band_gap",
    "energy_above_hull",
    "formation_energy_per_atom",
    "symmetry",
    "density",
    "is_stable",
    "is_metal",
]


def extract_mp_queries(question: str) -> List[str]:
    """Return formula / mp-id strings worth looking up. Empty = do not call MP."""
    text = str(question or "").strip()
    if not text:
        return []
    found: List[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        """Append *value* to *found* if it is a new query token."""
        key = value.strip()
        if not key:
            return
        lowered = key.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        found.append(key)

    for match in MP_ID_RE.finditer(text):
        _add(match.group(0).lower())

    for match in FORMULA_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token in _STOP_TOKENS:
            continue
        if not _looks_like_formula(token):
            continue
        _add(token)

    wants_property = bool(PROPERTY_RE.search(text))
    if not found:
        return []
    # Bare short formulas without a property cue stay off — avoids "what kinds
    # of materials…" matching nothing useful, and "I" / "In" false positives.
    if not wants_property and all(item.startswith("mp-") is False and not re.search(r"\d", item) for item in found):
        if not any(_element_count(item) >= 2 for item in found):
            return []
    return found


def should_query_materials_project(question: str) -> bool:
    """True when *question* names a formula or mp-id worth looking up."""
    return bool(extract_mp_queries(question))


def lookup_materials_project(
    question: str,
    *,
    limit: int = 3,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Search MP for formulas/ids in *question* and return a context pack."""
    queries = extract_mp_queries(question)
    empty = {
        "triggered": bool(queries),
        "queries": queries,
        "records": [],
        "context": "",
        "skipped": None,
    }
    if not queries:
        return empty
    key = (api_key if api_key is not None else os.environ.get("MP_API_KEY") or "").strip()
    if not key:
        empty["skipped"] = "no_api_key"
        logger.info("Materials Project skipped: MP_API_KEY is not set")
        return empty
    records: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for query in queries[:3]:
        try:
            hits = search_mp_summary(query, api_key=key, limit=limit)
        except Exception as exc:
            logger.warning("Materials Project search failed for %r: %s", query, exc)
            empty["skipped"] = "search_error"
            continue
        for hit in hits:
            mid = str(hit.get("material_id") or "")
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            records.append(hit)
            if len(records) >= limit:
                break
        if len(records) >= limit:
            break
    empty["records"] = records
    empty["context"] = format_mp_context(records)
    if not records and empty["skipped"] is None:
        empty["skipped"] = "no_hits"
    return empty


def search_mp_summary(query: str, *, api_key: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Live MP summary search. Isolated so tests can monkeypatch it."""
    from mp_api.client import MPRester

    kwargs: Dict[str, Any] = {
        "fields": list(_SUMMARY_FIELDS),
        "num_chunks": 1,
        "chunk_size": max(1, limit),
    }
    if MP_ID_RE.fullmatch(query):
        kwargs["material_ids"] = [query.lower()]
    else:
        kwargs["formula"] = query
    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(**kwargs)
    return [_normalize_doc(doc) for doc in docs[:limit]]


def format_mp_context(records: Sequence[Dict[str, Any]]) -> str:
    """Render MP summary records as a cite-token context block for the judge."""
    if not records:
        return ""
    tokens = [cite_token(rec) for rec in records if cite_token(rec)]
    lines = [
        "### Materials Project",
        "Allowed citations (copy these tokens exactly):",
        *[f"- {token}" for token in tokens],
        "",
    ]
    for rec in records:
        token = cite_token(rec)
        mid = rec.get("material_id") or ""
        formula = rec.get("formula_pretty") or ""
        bits = [token]
        if formula:
            bits.append(f"formula={formula}")
        if rec.get("band_gap") is not None:
            bits.append(f"band_gap_eV={rec['band_gap']}")
        if rec.get("is_metal") is not None:
            bits.append(f"is_metal={rec['is_metal']}")
        if rec.get("is_stable") is not None:
            bits.append(f"is_stable={rec['is_stable']}")
        if rec.get("energy_above_hull") is not None:
            bits.append(f"energy_above_hull_eV={rec['energy_above_hull']}")
        if rec.get("formation_energy_per_atom") is not None:
            bits.append(f"formation_energy_per_atom_eV={rec['formation_energy_per_atom']}")
        if rec.get("density") is not None:
            bits.append(f"density_g_cm3={rec['density']}")
        if rec.get("spacegroup"):
            bits.append(f"spacegroup={rec['spacegroup']}")
        if mid:
            bits.append(f"url=https://next-gen.materialsproject.org/materials/{mid}")
        lines.append(" ".join(str(b) for b in bits if b))
    return "\n".join(lines).strip()


def cite_token(record: Dict[str, Any]) -> str:
    """``[MP: mp-id formula]`` token, or empty when material_id is missing."""
    mid = str(record.get("material_id") or "").strip()
    formula = str(record.get("formula_pretty") or "").strip()
    if not mid:
        return ""
    label = f"{mid} {formula}".strip()
    return f"[MP: {label}]"


def _looks_like_formula(token: str) -> bool:
    """True if *token* parses as a composition and is not a stop word."""
    if token in _STOP_TOKENS:
        return False
    if not re.search(r"[A-Z][a-z]?", token):
        return False
    if re.search(r"\d", token):
        return _parse_ok(token)
    return _element_count(token) >= 2 and _parse_ok(token)


def _element_count(token: str) -> int:
    """Count Hill-notation element symbols in *token*."""
    return len(re.findall(r"[A-Z][a-z]?", token))


def _parse_ok(token: str) -> bool:
    """True if pymatgen accepts *token* as a Composition."""
    try:
        from pymatgen.core import Composition

        Composition(token)
        return True
    except Exception:
        return False


def _normalize_doc(doc: Any) -> Dict[str, Any]:
    """Flatten an MP summary document or dict into citation-ready fields."""
    data = doc if isinstance(doc, dict) else getattr(doc, "model_dump", lambda: {})()
    if not data and hasattr(doc, "material_id"):
        data = {field: getattr(doc, field, None) for field in _SUMMARY_FIELDS}
    symmetry = data.get("symmetry") or {}
    spacegroup = ""
    if isinstance(symmetry, dict):
        spacegroup = str(symmetry.get("symbol") or symmetry.get("crystal_system") or "")
    elif symmetry:
        spacegroup = str(getattr(symmetry, "symbol", "") or "")
    return {
        "material_id": str(data.get("material_id") or ""),
        "formula_pretty": str(data.get("formula_pretty") or ""),
        "band_gap": data.get("band_gap"),
        "energy_above_hull": data.get("energy_above_hull"),
        "formation_energy_per_atom": data.get("formation_energy_per_atom"),
        "density": data.get("density"),
        "is_stable": data.get("is_stable"),
        "is_metal": data.get("is_metal"),
        "spacegroup": spacegroup,
    }


def mp_context_for_question(question: str) -> str:
    """Convenience wrapper used by the retrieval agent."""
    return str(lookup_materials_project(question).get("context") or "")
