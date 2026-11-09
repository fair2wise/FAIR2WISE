"""Parser for the public ALS beamline 11.0.1.2 page (fact table, techniques, contacts)."""

from __future__ import annotations

import re
from typing import Any

from lxml import html as lhtml

from .common import _texts, _xp_elems, _xp_strs
from .specs import ALS_PAGE_URL, EMAIL_RE, PERSON_NAME_RE, PERSON_NAME_STOPWORDS, SKIP_PERSON_NAMES


def parse_als_page(html_text: str) -> dict[str, Any]:
    """Extract fact table + technique list from the public ALS beamline page."""
    tree = lhtml.fromstring(html_text)
    facts: dict[str, str] = {}
    for row in _xp_elems(tree, "//tr"):
        cells = [" ".join(_texts(cell)).strip() for cell in _xp_elems(row, "./th|./td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        if len(cells) >= 2:
            facts[cells[0]] = " | ".join(cells[1:])
    title = " ".join(_xp_strs(tree, "//h1//text()")).strip() or "Beamline 11.0.1.2"
    paragraphs = [
        re.sub(r"\s+", " ", " ".join(_texts(p)).strip())
        for p in _xp_elems(tree, "//p")
        if " ".join(_texts(p)).strip()
    ]
    techniques: list[str] = []
    tech_blob = facts.get("Technique category") or facts.get("Techniques") or ""
    for token in re.split(r"[|\n;/]+", tech_blob):
        token = token.strip(" -")
        if len(token) > 2:
            techniques.append(token)
    full_text = re.sub(r"\s+", " ", " ".join(_texts(tree)))
    people = parse_als_contact_people(facts, full_text, source=ALS_PAGE_URL)
    return {
        "title": title,
        "facts": facts,
        "techniques": techniques,
        "paragraphs": paragraphs[:8],
        "text": full_text[:8000],
        "people": people,
    }


def _clean_person_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip(" ,;:()"))
    if not cleaned or cleaned.casefold() in SKIP_PERSON_NAMES:
        return ""
    parts = cleaned.split()
    if len(parts) < 2:
        return ""
    last = parts[-1].rstrip(".").casefold()
    if last in PERSON_NAME_STOPWORDS or len(last) < 3:
        return ""
    return cleaned


def _email_near(text: str, index: int, name: str) -> str:
    window = (text or "")[index : index + max(len(name) + 80, 120)]
    match = EMAIL_RE.search(window)
    return match.group(0) if match else ""


def parse_als_contact_people(
    facts: dict[str, str],
    text: str = "",
    *,
    source: str = ALS_PAGE_URL,
) -> list[dict[str, str]]:
    """Parse documented ALS 11.0.1.2 contacts. Does not invent names."""
    blob = facts.get("Primary contact(s)") or facts.get("Primary contacts") or facts.get("Contacts") or ""
    people: list[dict[str, str]] = []
    seen = set()
    chunks = re.split(r"[|;]+", blob) if blob else [text]
    for chunk in chunks:
        for match in PERSON_NAME_RE.finditer(chunk or ""):
            name = _clean_person_name(match.group(1))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            people.append(
                {
                    "name": name,
                    "email": _email_near(chunk, match.start(), name),
                    "role": "BeamlineScientist",
                    "source": source,
                    "evidence": f"ALS primary contact: {chunk.strip()[:240]}",
                }
            )
    return people
