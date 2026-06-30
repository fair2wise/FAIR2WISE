"""Provenance enrichment for the term-extractor: PDF-derived publication
metadata and code-snippet extraction.

Ported from the local ``app/modules/extract_terms.py`` provenance fixes so the
copied branch extractor emits source-scoped ``source_metadata`` and top-level
``code_snippets`` that ``app/modules/json2kg.py`` understands.

Key rule (HANDOFF fixes #2/#3): publication/authorship metadata comes ONLY from
PDF-derived text/metadata. The term LLM never stamps it; missing fields stay
blank rather than being copied from another source or hallucinated.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import fitz

logger = logging.getLogger(__name__)


# ───────────────────────── source metadata helpers ─────────────────────────
_PUB_FIELDS = (
    "publication_year",
    "paper_title",
    "authors",
    "institutions",
    "doi",
    "journal",
    "volume",
    "issue",
    "pages_range",
    "abstract_text",
    "keywords",
)


def clean_pub_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop empty metadata fields before storing source-scoped provenance."""
    cleaned: Dict[str, Any] = {}
    for key in _PUB_FIELDS:
        value = (meta or {}).get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = [v for v in value if v]
            if not value:
                continue
        cleaned[key] = value
    return cleaned


def merge_into_source_metadata(
    source_metadata: Dict[str, Any],
    source_paper: str,
    meta: Optional[Dict[str, Any]],
) -> bool:
    """Merge cleaned ``meta`` into ``source_metadata[source_paper]`` in place.

    Never overwrites other sources. Returns True if anything changed.
    """
    cleaned = clean_pub_meta(meta)
    if not source_paper or not cleaned:
        return False
    before = dict(source_metadata.get(source_paper) or {})
    merged = {**before, **{k: v for k, v in cleaned.items() if v not in (None, "", [])}}
    if merged == before:
        return False
    source_metadata[source_paper] = merged
    return True


def publications_from_source_metadata(source_metadata: Any) -> List[Dict[str, Any]]:
    """Convert source-scoped metadata map into embedded publication records."""
    if not isinstance(source_metadata, dict):
        return []
    publications: List[Dict[str, Any]] = []
    for source_paper in sorted(str(k) for k in source_metadata if str(k).strip()):
        meta = source_metadata.get(source_paper)
        if not isinstance(meta, dict):
            continue
        cleaned = clean_pub_meta(meta)
        publication = {"source_paper": source_paper, **cleaned}
        publications.append(publication)
    return publications


# ───────────────────────── code snippet helpers ─────────────────────────
def snippet_key(snip: Dict[str, Any]) -> Tuple[str, str, str]:
    """Dedup key for a code snippet (source_paper, page, code body)."""
    return (
        str(snip.get("source_paper", "")),
        str(snip.get("page", "")),
        (snip.get("code_snippet") or "").strip(),
    )


def extract_snippets_json_from_text(text: str) -> Dict[str, Any]:
    """Extract the largest JSON object containing ``snippets`` from LLM output."""
    pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
    matches = list(re.finditer(pattern, text or ""))
    matches.sort(key=lambda m: -len(m.group(0)))
    for m in matches:
        snippet = m.group(0)
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict) and "snippets" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return {"snippets": []}


def normalize_domain_features(features: Any, schema_helper: Any) -> List[Dict[str, Optional[str]]]:
    """Keep only schema-allowed CodeSnippet domain features."""
    try:
        ctx = schema_helper.get_code_domain_feature_context()
    except Exception:
        ctx = ""
    allowed = {
        line.split(":", 1)[0].lstrip("- ").strip()
        for line in ctx.splitlines()
        if line.startswith("- ") and line != "- None"
    }
    normalized: List[Dict[str, Optional[str]]] = []
    feature_items = features if isinstance(features, list) else [features]
    for feature in feature_items:
        if not isinstance(feature, dict):
            continue
        name = str(feature.get("feature_name") or "").strip()
        value = feature.get("feature_value")
        if name not in allowed or value in (None, "", []):
            continue
        normalized.append({
            "feature_name": name,
            "feature_value": str(value),
            "feature_units": feature.get("feature_units") or None,
            "feature_source_text": feature.get("feature_source_text") or None,
        })
    return normalized


def _prepare_code_snippet_prompt(page_text: str, schema_helper: Any) -> str:
    """Prompt for scientific CONTEXT around code snippets (no code bodies)."""
    max_len = 6000
    text = page_text[-max_len:] if len(page_text) > max_len else page_text
    try:
        domain_feature_context = schema_helper.get_code_domain_feature_context()
    except Exception:
        domain_feature_context = "- None"

    return f"""=== CODE SNIPPET CONTEXT EXTRACTION ===
This page may contain scientific analysis code (e.g. scattering, spectroscopy, simulation).
Extract domain metadata for any code functions mentioned or used on this page.
Do NOT extract code bodies — only the surrounding scientific context.

CONTENT:
{text}

For each function name or code block referenced on this page, extract:
- "function_name": the Python/MATLAB function name, or null
- "authors": authors of the library/code, or []
- "code_description": one-sentence plain-English description of what the function does
- "domain_features": schema-driven scientific metadata found near the code, or []

Allowed domain_features feature_name values from the schema:
{domain_feature_context}

Each domain_features item must include:
- "feature_name": exactly one allowed schema feature name
- "feature_value": extracted value as a string
- "feature_units": units as a string, or null
- "feature_source_text": short source text supporting the value

Return {{"snippets": []}} if page has no scientific analysis or code references.

Output JSON:
{{
  "snippets": [
    {{
      "function_name": "function name or null",
      "authors": [],
      "code_description": "what this function does",
      "domain_features": [
        {{
          "feature_name": "schema feature name",
          "feature_value": "extracted value",
          "feature_units": null,
          "feature_source_text": "supporting text"
        }}
      ]
    }}
  ]
}}"""


def extract_code_snippets(
    page_text: str,
    chat_client: Any,
    schema_helper: Any,
    *,
    source_paper: str = "",
    page: int = 0,
    temperature: float = 0.0,
) -> List[Dict[str, Any]]:
    """Extract code snippets from a page.

    Regex deterministically extracts named code blocks (code bodies); the LLM
    adds schema-driven domain context/authors keyed by function name.
    """
    if not page_text or len(page_text.split()) < 20:
        return []

    named_block_re = re.compile(
        r"((?:(?:import|from|library|require|using)\s+\S[^\n]*\n)*"
        r"(?:def|class|function|func)\s+(\w+)\s*[\(\[{]"
        r"[^\n]*\n"
        r"(?:[ \t]+[^\n]+\n){1,})",
        re.MULTILINE,
    )

    regex_results: List[Dict[str, Any]] = []
    seen_fn_names: set = set()
    seen_bodies: set = set()

    for dm in named_block_re.finditer(page_text):
        fn_name = dm.group(2)
        code_body = dm.group(1).rstrip()
        if fn_name.lower() in seen_fn_names or code_body.strip() in seen_bodies:
            continue
        seen_fn_names.add(fn_name.lower())
        seen_bodies.add(code_body.strip())

        lang = "python"
        if re.search(r"\bfunction\b", code_body) and not re.search(r"\bdef\b", code_body):
            lang = "matlab" if re.search(r"\bend\b", code_body) else "r"

        regex_results.append({
            "domain_features": [],
            "function_name": fn_name,
            "authors": [],
            "code_snippet": code_body,
            "code_language": lang,
            "code_description": f"{fn_name}: extracted from {source_paper} p.{page}",
            "page": page,
            "source_paper": source_paper,
        })

    if not regex_results:
        return []

    llm_context: Dict[str, Dict[str, Any]] = {}
    try:
        prompt = _prepare_code_snippet_prompt(page_text, schema_helper)
        response = chat_client.chat(prompt, temperature=temperature, timeout=120)
        data = extract_snippets_json_from_text(response)
        for snip in data.get("snippets", []):
            if not isinstance(snip, dict):
                continue
            fn = (snip.get("function_name") or "").strip().lower()
            llm_context[fn or "__anonymous__"] = snip
    except Exception as e:
        logger.warning("LLM code-context extraction failed (%s p.%s): %s — regex only", source_paper, page, e)

    results: List[Dict[str, Any]] = []
    for r in regex_results:
        fn_key = (r["function_name"] or "").lower()
        ctx = llm_context.get(fn_key) or llm_context.get("__anonymous__") or {}
        r["domain_features"] = normalize_domain_features(ctx.get("domain_features") or [], schema_helper)
        r["authors"] = ctx.get("authors") or []
        if ctx.get("code_description"):
            r["code_description"] = ctx["code_description"]
        results.append(r)

    if results:
        logger.info("Extracted %d snippet(s) from %s page %s", len(results), source_paper, page)
    return results


# ───────────────────────── publication metadata ─────────────────────────
def extract_pub_metadata(doc: "fitz.Document", pdf_path: str) -> Dict[str, Any]:
    """Extract publication metadata from PDF metadata fields and first-page text.

    All values are ``None`` or ``[]`` if not found. Never invents fields.
    """
    pdf_meta = doc.metadata or {}
    filename = os.path.basename(pdf_path)
    current_year = datetime.datetime.now(datetime.timezone.utc).year

    _MONTHS = (
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    )

    first_text = doc.load_page(0).get_text() if doc.page_count > 0 else ""

    publication_year: Optional[int] = None
    explicit_patterns = [
        rf"(?i)(?:published|accepted|received|revised|available\s+online)[^\n]{{0,40}}"
        rf"(?:{_MONTHS}\s+\d{{1,2}},?\s*((?:19|20)\d{{2}})"
        rf"|\d{{1,2}}\s+{_MONTHS}\s*((?:19|20)\d{{2}})"
        rf"|((?:19|20)\d{{2}})\s*[-–]\s*\d{{1,2}}\s*[-–]\s*\d{{1,2}})",
        r"(?i)(?:published|accepted|received|revised)[^\n]{0,30}((?:19|20)\d{2})",
        rf"(?i)(?:published|accepted|received|revised)[^\n]{{0,20}}"
        rf"((?:19|20)\d{{2}})-\d{{2}}-\d{{2}}",
    ]
    for pat in explicit_patterns:
        m = re.search(pat, first_text)
        if m:
            yr_str = next((g for g in m.groups() if g and re.match(r"(19|20)\d{2}", g)), None)
            if yr_str:
                yr = int(yr_str[:4])
                if 1990 <= yr <= current_year:
                    publication_year = yr
                    break

    if not publication_year:
        for key in ("creationDate", "modDate"):
            val = (pdf_meta.get(key) or "").strip()
            m = re.search(r"((?:19|20)\d{2})", val)
            if m:
                yr = int(m.group(1))
                if 1990 <= yr <= current_year:
                    publication_year = yr
                    break

    if not publication_year:
        month_year_m = re.findall(
            rf"(?:{_MONTHS})\s+(?:\d{{1,2}},?\s*)?((?:19|20)\d{{2}})"
            rf"|((?:19|20)\d{{2}})\s+{_MONTHS}",
            first_text,
        )
        candidates = []
        for grp in month_year_m:
            for g in grp:
                if g and re.match(r"(19|20)\d{2}", g):
                    yr = int(g)
                    if 1990 <= yr <= current_year:
                        candidates.append(yr)
        if candidates:
            publication_year = max(set(candidates), key=candidates.count)

    if not publication_year:
        all_years = [
            int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", first_text)
            if 1990 <= int(y) <= current_year
        ]
        if all_years:
            publication_year = max(set(all_years), key=all_years.count)

    paper_title: Optional[str] = (pdf_meta.get("title") or "").strip() or None
    if not paper_title and doc.page_count > 0:
        first_lines = [ln.strip() for ln in first_text.splitlines() if ln.strip()]
        _skip = re.compile(r"(?i)^(https?://|10\.\d{4}|doi|vol|pp\.|©|received|accepted|published|edited|keywords)")
        for i, ln in enumerate(first_lines[:10]):
            if 10 <= len(ln) <= 200 and not _skip.search(ln):
                title_parts = [ln]
                for nxt in first_lines[i + 1:i + 4]:
                    if (5 <= len(nxt) <= 120
                            and not _skip.search(nxt)
                            and not re.search(r"[@,;]", nxt)
                            and not re.search(r"\.$", nxt)):
                        title_parts.append(nxt)
                    else:
                        break
                paper_title = " ".join(title_parts)
                break

    authors: List[str] = []
    raw_author = (pdf_meta.get("author") or "").strip()
    if raw_author:
        parts = re.split(r";| and ", raw_author)
        authors = [p.strip() for p in parts if p.strip()]

    doi: Optional[str] = None
    for key in ("subject", "keywords", "identifier"):
        val = pdf_meta.get(key, "") or ""
        m = re.search(r"10\.\d{4,}/\S+", val)
        if m:
            doi = m.group(0).rstrip(".,)")
            break
    if not doi:
        for pn in range(min(2, doc.page_count)):
            text = doc.load_page(pn).get_text()
            m = re.search(r"10\.\d{4,}/\S+", text)
            if m:
                doi = m.group(0).rstrip(".,)")
                break

    keywords: List[str] = []
    raw_kw = (pdf_meta.get("keywords") or "").strip()
    if raw_kw:
        kw_parts = re.split(r"[;,]", raw_kw)
        keywords = [k.strip() for k in kw_parts if k.strip()]

    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages_range: Optional[str] = None
    abstract_text: Optional[str] = None

    if first_text:
        jrnl_m = re.search(
            r"(?i)((?:journal|letters|review|advanced|nature|science|ACS|RSC|wiley|elsevier)[^\n]{0,80})",
            first_text,
        )
        if jrnl_m:
            journal = jrnl_m.group(1).strip()

        vi_m = re.search(
            r"(?i)vol(?:ume)?\.?\s*(\d+)[,\s]+(?:no|issue|iss)\.?\s*(\d+)",
            first_text,
        )
        if vi_m:
            volume = vi_m.group(1)
            issue = vi_m.group(2)

        pg_m = re.search(r"(?i)pp?\.?\s*(\d+\s*[-–]\s*\d+)", first_text)
        if pg_m:
            pages_range = pg_m.group(1).replace(" ", "")

        abs_m = re.search(
            r"(?i)abstract\s*\n([\s\S]{50,1500}?)(?:\n(?:introduction|keywords|1\.|©))",
            first_text,
        )
        if abs_m:
            abstract_text = " ".join(abs_m.group(1).split())

    result = {
        "publication_year": publication_year,
        "paper_title": paper_title,
        "authors": authors,
        "institutions": [],
        "doi": doi,
        "journal": journal,
        "volume": volume,
        "issue": issue,
        "pages_range": pages_range,
        "abstract_text": abstract_text,
        "keywords": keywords,
    }
    logger.debug("Pub metadata for %s: year=%s title=%r doi=%r", filename, publication_year, paper_title, doi)
    return result
