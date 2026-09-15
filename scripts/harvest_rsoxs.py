#!/usr/bin/env python3
"""Harvest OA RSoXS PDFs into papers/rsoxs/{year}/ and update manifest.json.

arXiv (SubmittedDate, newest first) then OpenAlex. Queries are quoted so
hyphens are literal — unquoted R-SoXS is Lucene ``R AND NOT SoXS`` and
dumps unrelated newest arXiv.

Keep a paper only when RSoXS / resonant soft X-ray scattering is used,
developed, simulated, interpreted, or materially discussed (tier A or B).
The corpus is general RSXS. Ranking uses three independent
0–1 dimensions (technique / soft-matter / methods) plus
facility/beamline metadata. ALS provenance is not a relevance boost.
Near-duplicate preprint/journal pairs collapse to one Work.
Tier C is never downloaded. Does not write matkg_rsoxs_v1.json.

Usage (from repo root):
  python3 scripts/harvest_rsoxs.py
  python3 scripts/harvest_rsoxs.py --max-pdfs 120
  python3 scripts/harvest_rsoxs.py --rescore
  python3 scripts/harvest_rsoxs.py --import-pubtemp papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf
  python3 scripts/harvest_rsoxs.py --harvest-pending
  python3 scripts/harvest_rsoxs.py --resolve-oa
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import download_pdfs as dl  # noqa: E402

# Same allowlist as download_pdfs / DownloadAgent — keep harvest free of Academy imports.
RELIABLE_PDF_HOSTS = dl.RELIABLE_PDF_HOSTS

LOGGER = logging.getLogger("harvest_rsoxs")

# OpenAlex phrase search. Do not pass unquoted hyphens to arXiv — Lucene
# treats R-SoXS as "R AND NOT SoXS", which returns newest papers in bulk.
OPENALEX_QUERIES = (
    "RSoXS",
    '"R-SoXS"',
    '"P-RSoXS"',
    '"resonant soft x-ray scattering"',
    '"resonant soft X-ray scattering"',
    '"tender resonant X-ray scattering"',
    '"11.0.1.2"',
    '"Advanced Light Source" RSoXS',
    'RSoXS polymer',
    'RSoXS morphology',
    '"organic solar" RSoXS',
    'GIWAXS RSoXS',
    '"block copolymer" RSoXS',
    '"liquid crystal" "resonant soft x-ray"',
    'NRSS RSoXS',
    '"vapor-deposited" RSoXS',
    'PffBT RSoXS',
    '"non-fullerene" RSoXS',
    '"vapor-deposited" glass RSoXS',
    'RSoXS "phase separation"',
)

# Quoted all: fields so hyphens are literal (arXiv API user manual).
ARXIV_QUERIES = (
    'all:"RSoXS"',
    'all:"R-SoXS"',
    'all:"P-RSoXS"',
    'all:"resonant soft x-ray scattering"',
    'all:"resonant soft X-ray scattering"',
    'all:"tender resonant X-ray scattering"',
    'all:"11.0.1.2"',
    'all:"Advanced Light Source" AND all:"RSoXS"',
    'all:"RSoXS" AND all:polymer',
    'all:"RSoXS" AND all:morphology',
    'all:"resonant soft x-ray scattering" AND all:polymer',
    'all:"P-RSoXS" AND all:polymer',
    'all:"RSoXS" AND all:"organic solar"',
    'all:"RSoXS" AND all:"vapor-deposited"',
)

# Author / exact-title queries. Still gated by classify_rsoxs A/B.
# Do not require is_oa — record skipped/failed when no reliable PDF.
OPENALEX_AUTHOR_QUERIES = (
    '"Cheng Wang" RSoXS',
    '"Cheng Wang" "resonant soft x-ray scattering"',
    '"Cheng Wang" "resonant soft X-ray scattering"',
    '"11.0.1.2" Wang',
    '"Cheng Wang" "chemical sensitivity"',
    '"Thomas Ferron" RSoXS',
    '"Ferron" "resonant soft X-ray"',
    '"Ferron" "resonant soft x-ray scattering"',
    '"Spectral Analysis for Resonant Soft X-Ray Scattering Enables Measurement of Interfacial Width"',
    '"Absolute intensity calibration for carbon-edge soft X-ray scattering"',
    'Ferron "interfacial width" RSoXS',
    'Ferron "intensity calibration"',
)

ARXIV_AUTHOR_QUERIES = (
    'au:"Cheng Wang" AND (all:"RSoXS" OR all:"resonant soft x-ray scattering")',
    'au:"Cheng Wang" AND all:"11.0.1.2"',
    'au:"Thomas Ferron" AND (all:"RSoXS" OR all:"resonant soft x-ray scattering")',
    'au:"Ferron" AND all:"interfacial width"',
    'all:"Absolute intensity calibration for carbon-edge soft X-ray scattering"',
    'all:"Spectral Analysis for Resonant Soft X-Ray Scattering Enables Measurement of Interfacial Width"',
)

AUTHOR_NAMES = (
    "Cheng Wang",
    "Thomas Ferron",
)

_ASTRONOMY_SOXS = re.compile(
    r"\b(eso|ntt|tigeriss|eso/ntt)\b|"
    r"soxs\s+(spectrograph|instrument|nir|common path|end-to-end)|"
    r"transient event.{0,40}soxs|"
    r"\b(ntt telescope|eso/ntt)\b",
    re.I,
)

# Do not match bare "ALS" / "also". Require facility language.
_ALS = re.compile(
    r"advanced light source|"
    r"lawrence berkeley(?: national laboratory)?|"
    r"\blbnl\b|"
    r"11\.0\.1\.2|"
    r"beamline 11\.0\.1|"
    r"(?:at the |at )\bALS\b|"
    r"\bALS\b.{0,40}beamline|"
    r"beamline.{0,40}\bALS\b",
    re.I,
)

# APS before SSRL: TES/29-ID papers often cite SSRL in references.
_APS = re.compile(
    r"advanced photon source|"
    r"\b29-?ID\b|"
    r"(?:at the |at )\bAPS\b|"
    r"\bAPS\b.{0,40}(beamline|29)|"
    r"beamline.{0,20}\bAPS\b",
    re.I,
)

# Word-boundary SSRL only — do not match the substring inside other tokens.
_SSRL = re.compile(
    r"(?<![A-Za-z0-9])SSRL(?![A-Za-z0-9])|"
    r"stanford synchrotron(?: radiation(?: lightsource)?)?",
    re.I,
)

_HARD_QUANTUM = re.compile(
    r"cuprate|nickelate|manganite|helimagnet|holmium|"
    r"superconduct|charge density wave|\bcdw\b|"
    r"antiferromagnet|ferromagnet|spin stripe|orbital order|"
    r"quantum (?:phase|material|melting)|magnon|topological insulator|"
    r"spin orientation|exchange interaction|"
    r"magnetic (?:phase|order|domain|structure|spiral)|"
    r"spin slip|helical magnetic|stripe-ordered|"
    r"infinite layer|perovskite|"
    r"\bHo\b.{0,20}(m-edge|magnetic)|"
    r"helimagnetism|skyrmion",
    re.I,
)

_EXPLICIT_SOFT = re.compile(
    r"\bpolymer|\bbiomaterial|\bopv\b|organic solar|organic semiconductor|"
    r"organic glass|organic electronic|vapor-?deposited|"
    r"block copolymer|liquid crystal|\bmesogen\b|twist-bend|"
    r"conjugated polymer|\bp3ht\b|\bpffbt|\bnon-fullerene|"
    r"fullerene-free|polymer solar|\bnfa\b",
    re.I,
)

# Extract-first (S) then extract-next (A). Rank is the user seed order.
_KG_SEEDS = (
    ("S", 1, ("controlled component segregation",)),
    ("S", 2, ("distribution of dopants",)),
    ("S", 3, ("hierarchical structure",)),
    ("S", 4, ("cyrsoxs",)),
    ("S", 5, ("kinetically-arrested phase separation", "kinetically arrested phase separation")),
    ("S", 6, ("pffbt4t",)),
    ("S", 7, ("liquid resonant soft x ray", "upper critical solution temperature polymer")),
    ("A", 8, ("interstitial pores of dense nanoparticle", "nanoparticle packings")),
    ("A", 9, ("twist-bend nematic", "nanoscale-pitch helical")),
    ("A", 10, ("fullerene-free polymer solar", "itcptc")),
    ("A", 11, ("transition edge sensor array",)),
    ("A", 12, ("capability in ssrl",)),
    ("A", 13, ("transition edge sensor spectrometer",)),
    ("S", 14, ("how to rsoxs",)),
    ("S", 15, ("resonant soft x ray scattering in polymer science",)),
    ("S", 16, ("understanding morphology and chemical heterogeneity", "characterizing morphology in organic systems")),
    ("S", 17, ("resonant elastic soft x ray scattering",)),
    ("A", 18, ("characterizing patterned block copolymer",)),
)

_ROLE_RANK = {
    "core_soft_matter": 4,
    "core_technique": 3,
    "general": 1,
    "peripheral": 0,
}

_TIER_RANK = {
    "S": 5,
    "A": 4,
}

DEFAULT_MANIFEST = {
    "corpus": "rsoxs",
    "kg_version": "v1",
    "beamline": "11.0.1.2",
    "schema": "storage/schema/rsoxs_schema.yaml",
    "terms_path": "storage/terminology/extracted_terms_rsoxs_v1.json",
    "kg_path": "storage/kg/matkg_rsoxs_v1.json",
    "tiled_graphql": None,
    "papers": [],
}


def _normalize_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _normalize_rsoxs_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", t)
    t = re.sub(r"x\s*-\s*ray", "x ray", t)
    t = re.sub(r"p\s*-\s*r\s*-\s*soxs", " prsoxs ", t)
    t = re.sub(r"p\s*-\s*rsoxs", " prsoxs ", t)
    t = re.sub(r"r\s*-\s*soxs", " rsoxs ", t)
    t = re.sub(r"\bcyrsoxs\b", " cyrsoxs ", t)
    return t


def classify_rsoxs(title: str, abstract: str = "") -> str:
    """Tier A (experiment/interpretation), B (methods/simulation), or C (reject).

    Keep only when RSoXS / resonant soft X-ray scattering is used, developed,
    simulated, or materially discussed — not acronym collisions like astronomy SOXS.
    """
    blob = f"{title or ''}\n{abstract or ''}"
    norm = _normalize_rsoxs_text(blob)
    has_phrase = bool(
        re.search(r"resonant\s+(?:elastic\s+)?soft\s+x\s*-?\s*ray\s+scatter", norm)
    )
    has_rsoxs = bool(re.search(r"\b(?:p)?rsoxs\b", norm)) or "cyrsoxs" in norm
    astronomy = bool(_ASTRONOMY_SOXS.search(blob))
    bare_soxs = bool(re.search(r"\bsoxs\b", norm)) and not has_rsoxs and not has_phrase
    if (astronomy or bare_soxs) and not has_rsoxs and not has_phrase:
        return "C"
    if not has_rsoxs and not has_phrase:
        return "C"
    methods = bool(
        re.search(
            r"\bcyrsoxs\b|virtual instrument|"
            r"gpu-accelerated.{0,40}(rsoxs|cyrsoxs)",
            norm,
        )
    )
    experimental = bool(
        re.search(
            r"rsoxs measurements|using rsoxs|used rsoxs|"
            r"we (use|used|performed|measured).{0,40}rsoxs|"
            r"resonant soft x-ray scattering reveals|complementary rsoxs",
            norm,
        )
    )
    if methods and not experimental:
        return "B"
    return "A"


def _clip01(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 2)


def _weight_hits(text: str, patterns: Iterable[Tuple[str, float]]) -> float:
    score = 0.0
    for pattern, weight in patterns:
        if re.search(pattern, text, re.I):
            score += weight
    return score


def detect_facility(blob: str) -> Dict[str, Optional[str]]:
    """Facility/beamline metadata only — not a relevance feature.

    APS is checked before SSRL so TES/29-ID papers that cite SSRL in
    references are not labelled Stanford.
    """
    text = blob or ""
    facility: Optional[str] = None
    beamline: Optional[str] = None
    if _ALS.search(text) or re.search(r"11\.0\.1\.2", text):
        facility = "ALS"
        if re.search(r"11\.0\.1\.2", text):
            beamline = "11.0.1.2"
        elif re.search(r"beamline 11\.0\.1", text, re.I):
            beamline = "11.0.1.2"
    elif _APS.search(text):
        facility = "APS"
        if re.search(r"29-?ID", text, re.I):
            beamline = "29-ID"
    elif _SSRL.search(text) or re.search(r"\b13-3\b", text):
        facility = "SSRL"
        if re.search(r"\b13-3\b|beamline 13-3", text, re.I):
            beamline = "13-3"
    elif re.search(r"pohang accelerator|pal-xfel|\bpal\b.{0,20}xfel", text, re.I):
        facility = "PAL"
    elif re.search(r"nsls-ii|nsls ii|national synchrotron light source", text, re.I):
        facility = "NSLS-II"
    elif re.search(r"diamond light source", text, re.I):
        facility = "Diamond"
    return {"facility": facility, "beamline": beamline}


def _kg_seed(title: str) -> Tuple[Optional[str], Optional[int]]:
    t = _normalize_rsoxs_text(title or "")
    # Fink 2013 title is exactly this phrase; do not seed the YBCO REXS paper.
    if t.strip() == "resonant elastic soft x ray scattering":
        return "S", 17
    for tier, rank, needles in _KG_SEEDS:
        if rank == 17:
            continue
        if any(needle in t for needle in needles):
            return tier, rank
    return None, None


def score_rsoxs(title: str, abstract: str = "", extra: str = "") -> Dict[str, Any]:
    """Three relevance dimensions; facility is metadata, not a boost.

    technique_relevance: how central RSoXS/P-RSoXS is to the paper.
    soft_matter_relevance: polymers / OPV / organic glass / LC / morphology.
    method_relevance: analysis, simulation, instrumentation, workflow.
    """
    tier = classify_rsoxs(title, abstract)
    front = f"{title or ''}\n{abstract or ''}"
    norm = _normalize_rsoxs_text(front)
    title_norm = _normalize_rsoxs_text(title or "")
    fac = detect_facility(f"{front}\n{extra or ''}")
    seed_tier, seed_rank = _kg_seed(title)

    empty = {
        "rsoxs_tier": tier,
        "technique_relevance": 0.0,
        "soft_matter_relevance": 0.0,
        "method_relevance": 0.0,
        "kg_priority": 0.0,
        "corpus_role": "peripheral",
        "kg_primary": False,
        "kg_primary_tier": None,
        "kg_seed_rank": None,
        "facility": fac["facility"],
        "beamline": fac["beamline"],
        "als": fac["facility"] == "ALS",
    }
    if tier == "C":
        return empty

    technique = 0.2
    if re.search(
        r"\b(?:p)?rsoxs\b|\brsxs\b|cyrsoxs|"
        r"resonant\s+(?:elastic\s+)?soft\s+x\s*-?\s*ray\s+scatter",
        norm,
    ):
        technique += 0.5
    if re.search(
        r"\b(?:p)?rsoxs\b|\brsxs\b|cyrsoxs|"
        r"resonant\s+(?:elastic\s+)?soft\s+x\s*-?\s*ray\s+scatter",
        title_norm,
    ):
        technique += 0.2
    technique += _weight_hits(
        norm,
        (
            (r"\bp-rsoxs\b|\bprsoxs\b|polarized resonant", 0.15),
            (r"resonant soft x ray scattering reveals", 0.1),
            (r"rsoxs measurements|using rsoxs|used rsoxs|complementary rsoxs|"
             r"using resonant soft", 0.1),
            (r"various measurements|among (other|many) techniques", -0.2),
        ),
    )
    if re.search(
        r"tes spectrometer|transition edge sensor|scattering capability",
        title_norm,
    ):
        technique += 0.15
    is_review = bool(
        re.search(r"\breview\b|recent progress|how to rsoxs", title_norm)
        or re.search(r"this review presents|this review ", norm)
    )
    if is_review and not re.search(r"how to rsoxs", title_norm):
        technique -= 0.1
    if re.search(r"how to rsoxs", title_norm):
        technique += 0.15

    soft = _weight_hits(
        norm,
        (
            (r"\bp3ht\b|poly 3 hexylthiophene", 0.4),
            (r"\bpffbt|pffbt4t", 0.35),
            (r"conjugated polymer|organic semiconductor|organic electronic|"
             r"organic thin film|organic system", 0.3),
            (r"organic glass|vapor-?deposited glass|molecular glass", 0.3),
            (r"\bopv\b|organic solar|polymer solar|non-fullerene|"
             r"fullerene-free|\bnfa\b", 0.3),
            (r"block copolymer|vapor-?deposited|phase separat|"
             r"kinetically.arrested", 0.25),
            (r"liquid crystal|\bmesogen\b|twist-bend|blue phase|cholesteric|"
             r"helical pitch|nematic phase", 0.2),
            (r"morpholog|domain connectivity|domain size|domain structure|"
             r"hierarchical", 0.25),
            (r"aggregation|immiscib|polymer/polymer", 0.2),
            (r"\bpolymer|biomaterial|soft material", 0.2),
            (r"rsoxs length scale|characteristic length scale|"
             r"multiple length scales|through(?:out)? the film thickness", 0.15),
            (r"giwaxs|gisaxs", 0.1),
            (r"dopant|molecular orientation|chain orientation|"
             r"scattering anisotropy|segregation", 0.15),
            (r"semicrystalline|crystalline and amorphous", 0.15),
            (r"liquid resonant|solution phase|polymer assembl", 0.15),
        ),
    )
    if _HARD_QUANTUM.search(front) and not _EXPLICIT_SOFT.search(front):
        soft = 0.0

    method = _weight_hits(
        norm,
        (
            (r"\bcyrsoxs\b|\bnrss\b|simulation suite|virtual instrument", 0.55),
            (r"forward simulat|inverse design|gpu", 0.2),
            (r"optical constant|dielectric tensor|polarization|"
             r"p-rsoxs|\bprsoxs\b", 0.25),
            (r"energy-dependent|absorption edge|contrast mechanism", 0.2),
            (r"fitting|reconstruct|orientation analysis|pattern fitting", 0.2),
            (r"endstation|instrumentation|tes spectrometer|transition edge", 0.3),
            (r"spectral analysis|intensity calibration|interfacial width|"
             r"absolute intensity", 0.3),
            (r"through(?:out)? the film thickness|characteristic length scale", 0.3),
            (r"geometry|reciprocal.space|angular degrees", 0.15),
            (r"machine learning|parametric morphology", 0.1),
            (r"this review presents|measurement best practices|"
             r"theoretical underpinnings|how to rsoxs", 0.4),
        ),
    )
    if re.search(
        r"new resonant soft x ray scattering capability|endstation|"
        r"tes spectrometer|transition edge|how to rsoxs|"
        r"in polymer science",
        title_norm,
    ):
        method += 0.5

    technique_r = _clip01(technique)
    soft_r = _clip01(soft)
    method_r = _clip01(method)
    kg_priority = round(0.45 * soft_r + 0.35 * method_r + 0.20 * technique_r, 3)

    instrument_subject = bool(
        re.search(
            r"\bcyrsoxs\b|virtual instrument|tes spectrometer|transition edge|"
            r"scattering capability|endstation",
            title_norm,
        )
    )
    if instrument_subject and method_r >= 0.45:
        role = "core_technique"
    elif seed_rank == 16:
        role = "core_soft_matter"
    elif seed_rank == 17 or (
        title_norm.strip() == "resonant elastic soft x ray scattering"
    ):
        role = "core_technique"
        if method_r < 0.45:
            method_r = 0.7
            kg_priority = round(0.45 * soft_r + 0.35 * method_r + 0.20 * technique_r, 3)
    elif is_review and technique_r >= 0.4:
        role = "core_soft_matter" if soft_r >= 0.35 else "core_technique"
    elif soft_r >= 0.35 and technique_r >= 0.5:
        role = "core_soft_matter"
    elif technique_r < 0.45:
        role = "peripheral"
    else:
        role = "general"

    kg_tier = seed_tier
    if kg_tier is None and role in {"core_soft_matter", "core_technique"}:
        kg_tier = "A"

    return {
        "rsoxs_tier": tier,
        "technique_relevance": technique_r,
        "soft_matter_relevance": soft_r,
        "method_relevance": method_r,
        "kg_priority": kg_priority,
        "corpus_role": role,
        "kg_primary": kg_tier in {"S", "A"},
        "kg_primary_tier": kg_tier,
        "kg_seed_rank": seed_rank,
        "facility": fac["facility"],
        "beamline": fac["beamline"],
        "als": fac["facility"] == "ALS",
    }


def pdf_preview_text(path: Path, head_pages: int = 2, tail_pages: int = 2) -> str:
    """First and last pages — ALS is often only in the acknowledgements."""
    if not path or not Path(path).exists():
        return ""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    try:
        import fitz

        doc = fitz.open(path)
        idxs = list(range(min(head_pages, doc.page_count)))
        if doc.page_count > head_pages:
            start = max(head_pages, doc.page_count - tail_pages)
            idxs.extend(range(start, doc.page_count))
        parts = [(doc.load_page(i).get_text() or "") for i in idxs]
        doc.close()
        return " ".join(" ".join(parts).split())
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        n = len(reader.pages)
        idxs = list(range(min(head_pages, n)))
        if n > head_pages:
            start = max(head_pages, n - tail_pages)
            idxs.extend(range(start, n))
        parts = []
        for i in idxs:
            try:
                parts.append(reader.pages[i].extract_text() or "")
            except Exception:
                continue
        return " ".join(" ".join(parts).split())
    except Exception:
        return ""


def _annotate_work(work: Dict[str, Any]) -> Dict[str, Any]:
    meta = score_rsoxs(str(work.get("title") or ""), str(work.get("abstract") or ""))
    work.update(meta)
    return work


def _reconstruct_abstract(inverted_index: Any) -> str:
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted_index.items():
        for i in idxs or []:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def _dedupe_key(work: Dict[str, Any]) -> str:
    doi = (work.get("doi") or "").strip().lower()
    if doi:
        return doi
    return (work.get("id") or "").strip().lower() or json.dumps(work, sort_keys=True, default=str)[:200]


def _is_reliable_pdf_url(url: str) -> bool:
    return dl.is_reliable_pdf_url(url)


def _pdf_urls(work: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    explicit = work.get("pdf_urls")
    if isinstance(explicit, list):
        urls.extend(str(url) for url in explicit if str(url).strip())
    for loc_key in ("best_oa_location", "primary_location"):
        loc = work.get(loc_key) or {}
        if isinstance(loc, dict) and loc.get("pdf_url"):
            urls.append(str(loc["pdf_url"]))
    for loc in work.get("locations") or []:
        if isinstance(loc, dict) and loc.get("pdf_url"):
            urls.append(str(loc["pdf_url"]))
    oa = work.get("open_access") or {}
    if isinstance(oa, dict) and oa.get("oa_url"):
        urls.append(str(oa["oa_url"]))
    seen: set[str] = set()
    out: List[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _normalize_pdf_urls(urls: Iterable[str]) -> List[str]:
    rewritten: List[str] = []
    seen: set[str] = set()
    for url in urls:
        mapped = dl.listed_oa_pdf_url(str(url))
        if mapped and mapped not in seen:
            seen.add(mapped)
            rewritten.append(mapped)
    return dl.prefer_repository_pdfs(rewritten)


def _reliable_pdf_urls(work: Dict[str, Any]) -> List[str]:
    urls, _, _ = dl.select_openalex_oa_pdfs(work)
    if urls:
        return urls
    return _normalize_pdf_urls(_pdf_urls(work))


def _europepmc_fallback_urls(urls: Iterable[str]) -> List[str]:
    """PMC often returns HTML to unauthenticated clients; Europe PMC still serves PDFs."""
    extra: List[str] = []
    seen: set[str] = set()
    for url in urls:
        pmcid = dl._pmcid_from_text(str(url))
        if not pmcid or pmcid in seen:
            continue
        seen.add(pmcid)
        extra.append(dl.europepmc_pdf_url(pmcid))
    return extra


def _safe_name(work: Dict[str, Any]) -> str:
    doi = work.get("doi")
    if doi:
        return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi)).replace("/", "_")
    wid = (work.get("id") or "").rstrip("/").split("/")[-1]
    return wid or "openalex_work"


def doi_stem(doi: str) -> str:
    """Filename-safe DOI (no slashes)."""
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", (doi or "").strip())
    return raw.replace("/", "_") or "unknown"


def _canonical_doi(doi: Optional[str]) -> Optional[str]:
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", (doi or "").strip(), flags=re.I)
    if not raw or not raw.lower().startswith("10."):
        return None
    return f"https://doi.org/{raw}"


def _doi_key(doi: Optional[str]) -> str:
    canon = _canonical_doi(doi)
    return (canon or "").lower()


def year_from_work(work: Dict[str, Any]) -> str:
    year = work.get("publication_year")
    if year:
        return str(int(year))
    date = str(work.get("publication_date") or "")[:4]
    if date.isdigit():
        return date
    return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paper_sort_tuple(paper: Dict[str, Any]) -> Tuple:
    tier = str(paper.get("kg_primary_tier") or "")
    seed = paper.get("kg_seed_rank")
    seed_n = int(seed) if seed not in (None, "", False) else 99
    role = str(paper.get("corpus_role") or "general")
    kg = float(paper.get("kg_priority") or 0)
    return (
        _TIER_RANK.get(tier, 0),
        100 - seed_n if seed_n < 99 else 0,
        _ROLE_RANK.get(role, 0),
        kg,
        str(paper.get("publication_date") or ""),
        str(paper.get("publication_year") or ""),
        str(paper.get("title") or ""),
    )


def _sort_key(work: Dict[str, Any]) -> Tuple:
    meta = work if "kg_priority" in work else score_rsoxs(
        str(work.get("title") or ""), str(work.get("abstract") or "")
    )
    merged = {**work, **meta}
    return _paper_sort_tuple(merged)


def _load_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_MANIFEST))
    data = json.loads(path.read_text())
    merged = json.loads(json.dumps(DEFAULT_MANIFEST))
    merged.update({k: data.get(k, merged[k]) for k in merged if k != "papers"})
    for key, value in data.items():
        if key not in merged:
            merged[key] = value
    merged["papers"] = list(data.get("papers") or [])
    return merged


def _save_manifest(path: Path, data: Dict[str, Any]) -> None:
    papers = sorted(
        data.get("papers") or [],
        key=_paper_sort_tuple,
        reverse=True,
    )
    data = dict(data)
    data["papers"] = papers
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, path)


def _upsert_paper(manifest: Dict[str, Any], row: Dict[str, Any]) -> None:
    if row.get("doi"):
        row["doi"] = _canonical_doi(str(row["doi"])) or row["doi"]
    key = _doi_key(row.get("doi")) or (
        row.get("arxiv_id") or row.get("pdf_path") or ""
    ).lower()
    papers = manifest.setdefault("papers", [])
    for i, existing in enumerate(papers):
        existing_key = _doi_key(existing.get("doi")) or (
            existing.get("arxiv_id") or existing.get("pdf_path") or ""
        ).lower()
        if existing_key and existing_key == key:
            merged = {**existing, **row}
            if existing.get("pdf_path") and not row.get("pdf_path"):
                merged["pdf_path"] = existing["pdf_path"]
                if existing.get("pdf_sha256"):
                    merged["pdf_sha256"] = existing["pdf_sha256"]
                if existing.get("downloaded_at"):
                    merged["downloaded_at"] = existing["downloaded_at"]
                ing = dict(merged.get("ingestion") or {})
                if ing.get("status") in {"failed", "skipped"} and existing.get("pdf_path"):
                    ing["status"] = (existing.get("ingestion") or {}).get("status") or "pending"
                    ing["error"] = None
                    merged["ingestion"] = ing
            papers[i] = merged
            return
    papers.append(row)


def _title_tokens(title: str) -> set:
    stop = {"a", "an", "the", "and", "for", "of", "in", "on", "with", "by"}
    return set(_normalize_title(title).split()) - stop


def titles_near_duplicate(a: str, b: str, threshold: float = 0.82) -> bool:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def _is_preprint(paper: Dict[str, Any]) -> bool:
    doi = (paper.get("doi") or "").lower()
    return "arxiv" in doi or "10.48550" in doi


def _canonical_preference(paper: Dict[str, Any]) -> Tuple[int, str]:
    """Journal version outranks arXiv preprint for the same Work."""
    return (0 if _is_preprint(paper) else 1, str(paper.get("publication_date") or ""))


def _version_record(paper: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "preprint" if _is_preprint(paper) else "journal",
        "doi": paper.get("doi"),
        "arxiv_id": paper.get("arxiv_id"),
        "pdf_path": paper.get("pdf_path"),
        "publication_date": paper.get("publication_date"),
        "source": paper.get("source"),
    }


def collapse_near_duplicates(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One canonical Work per near-duplicate title; preprint becomes a version."""
    kept: List[Dict[str, Any]] = []
    for paper in papers:
        match_i = None
        for i, existing in enumerate(kept):
            if titles_near_duplicate(str(paper.get("title") or ""), str(existing.get("title") or "")):
                match_i = i
                break
        if match_i is None:
            row = dict(paper)
            row.setdefault("versions", [_version_record(paper)])
            kept.append(row)
            continue
        current = kept[match_i]
        if _canonical_preference(paper) > _canonical_preference(current):
            winner, loser = dict(paper), current
        else:
            winner, loser = current, paper
        versions: List[Dict[str, Any]] = []
        for src in (winner, loser):
            versions.append(_version_record(src))
            versions.extend(src.get("versions") or [])
        seen_v: set[str] = set()
        uniq: List[Dict[str, Any]] = []
        for ver in versions:
            key = str(ver.get("doi") or ver.get("arxiv_id") or ver.get("pdf_path") or "")
            if key in seen_v:
                continue
            seen_v.add(key)
            uniq.append(ver)
        winner["versions"] = uniq
        if not winner.get("arxiv_id"):
            for ver in uniq:
                if ver.get("arxiv_id"):
                    winner["arxiv_id"] = ver["arxiv_id"]
                    break
            winner["arxiv_id"] = winner.get("arxiv_id") or loser.get("arxiv_id")
        kept[match_i] = winner
    return kept


def _arxiv_id_from_work(work: Dict[str, Any]) -> Optional[str]:
    loc_urls: List[str] = []
    for loc in [work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])]:
        if isinstance(loc, dict):
            loc_urls.extend([str(loc.get("pdf_url") or ""), str(loc.get("landing_page_url") or "")])
    return dl.arxiv_id_from_text(
        work.get("id"),
        work.get("doi"),
        work.get("arxiv_id"),
        *loc_urls,
        *(work.get("pdf_urls") or []),
    )


def search_arxiv(queries: Iterable[str], per_query: int) -> List[Dict[str, Any]]:
    import arxiv

    client = arxiv.Client(page_size=min(per_query, 50), delay_seconds=3, num_retries=2)
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        LOGGER.info("arXiv search %r (SubmittedDate, %d)", query, per_query)
        search = arxiv.Search(
            query=query,
            max_results=per_query,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            results = client.results(search)
            for result in results:
                short_id = str(result.get_short_id()).strip()
                pdf_url = str(result.pdf_url or f"https://arxiv.org/pdf/{short_id}").strip()
                if not short_id or not _is_reliable_pdf_url(pdf_url):
                    continue
                published = getattr(result, "published", None)
                doi = str(getattr(result, "doi", "") or "").strip()
                if doi and not doi.lower().startswith("http"):
                    doi = f"https://doi.org/{doi}"
                work = {
                    "id": f"https://arxiv.org/abs/{short_id}",
                    "doi": doi or f"https://doi.org/10.48550/arXiv.{short_id}",
                    "title": " ".join(str(result.title or "").split()),
                    "abstract": " ".join(str(result.summary or "").split()),
                    "publication_year": getattr(published, "year", None),
                    "publication_date": published.date().isoformat() if published else None,
                    "pdf_urls": [pdf_url],
                    "source": "arxiv",
                }
                if classify_rsoxs(work["title"], work["abstract"]) == "C":
                    continue
                key = _dedupe_key(work)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(work)
        except Exception as exc:
            LOGGER.warning("arXiv search failed for %r (%s)", query, exc)
            continue
    return merged


def _normalize_openalex_work(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    work = dict(raw)
    title = str(work.get("display_name") or work.get("title") or "")
    work["title"] = " ".join(title.split())
    work["abstract"] = work.get("abstract") or _reconstruct_abstract(
        work.get("abstract_inverted_index")
    )
    if classify_rsoxs(work["title"], str(work.get("abstract") or "")) == "C":
        return None
    work["pdf_urls"] = _reliable_pdf_urls(work)
    work["source"] = "openalex"
    return work


def _paginate_works(query_obj: Any, per_query: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        if hasattr(query_obj, "paginate"):
            for page in query_obj.paginate(per_page=min(50, max(per_query, 1))):
                results.extend(page or [])
                if len(results) >= per_query:
                    break
        else:
            results = list(query_obj.get(per_page=min(per_query, 50)) or [])
    except Exception as exc:
        LOGGER.warning("OpenAlex paginate failed (%s)", exc)
        try:
            results = list(query_obj.get(per_page=min(per_query, 50)) or [])
        except Exception as exc2:
            LOGGER.warning("OpenAlex get failed (%s)", exc2)
            return []
    return (results or [])[:per_query]


def search_openalex(
    queries: Iterable[str],
    per_query: int,
    mailto: Optional[str],
    *,
    oa_only: bool = True,
    require_pdf: bool = True,
) -> List[Dict[str, Any]]:
    from pyalex import Works, config as pyalex_config

    if mailto:
        pyalex_config.email = mailto
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    seen_titles: set[str] = set()
    for query in queries:
        LOGGER.info(
            "OpenAlex search %r (%s, %d)",
            query,
            "is_oa" if oa_only else "any",
            per_query,
        )
        try:
            query_obj = Works().search(query)
            if oa_only:
                query_obj = query_obj.filter(open_access={"is_oa": True})
            results = _paginate_works(query_obj, per_query)
        except Exception as exc:
            LOGGER.warning("OpenAlex search failed for %r (%s)", query, exc)
            try:
                results = Works().search(query).get(per_page=min(per_query, 50))
            except Exception as exc2:
                LOGGER.warning("OpenAlex fallback failed for %r (%s)", query, exc2)
                continue
        for raw in results or []:
            work = _normalize_openalex_work(dict(raw))
            if not work:
                continue
            if require_pdf and not work.get("pdf_urls"):
                continue
            key = _dedupe_key(work)
            title_key = _normalize_title(work["title"])
            if key in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.add(title_key)
            merged.append(work)
    return merged


def _author_is_target(name: str, rec: Dict[str, Any]) -> bool:
    display = str(rec.get("display_name") or "")
    if name.lower() not in display.lower():
        return False
    lowered = name.lower()
    if lowered == "thomas ferron":
        return True
    if lowered == "cheng wang":
        blob = json.dumps(rec, default=str).lower()
        return any(
            token in blob
            for token in (
                "lawrence berkeley",
                "advanced light source",
                "lbnl",
                "berkeley lab",
            )
        )
    return True


def search_openalex_authors(
    names: Iterable[str],
    per_query: int,
    mailto: Optional[str],
) -> List[Dict[str, Any]]:
    """OpenAlex author IDs → RSoXS works. Keep hits even without a PDF."""
    from pyalex import Authors, Works, config as pyalex_config

    if mailto:
        pyalex_config.email = mailto
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    phrases = ("RSoXS", '"resonant soft x-ray scattering"', '"11.0.1.2"')
    for name in names:
        LOGGER.info("OpenAlex author search %r", name)
        try:
            authors = Authors().search(name).get(per_page=25)
        except Exception as exc:
            LOGGER.warning("OpenAlex Authors search failed for %r (%s)", name, exc)
            continue
        author_ids = [
            rec.get("id") for rec in (authors or []) if rec.get("id") and _author_is_target(name, rec)
        ]
        if not author_ids and name.lower() == "cheng wang":
            LOGGER.info("No ALS-affiliated Cheng Wang ID; relying on quoted title queries")
        for aid in author_ids[:6]:
            for phrase in phrases:
                try:
                    query_obj = (
                        Works()
                        .filter(authorships={"author": {"id": aid}})
                        .search(phrase)
                    )
                    results = _paginate_works(query_obj, per_query)
                except Exception as exc:
                    LOGGER.warning("OpenAlex author-works failed for %s %r (%s)", aid, phrase, exc)
                    continue
                for raw in results or []:
                    work = _normalize_openalex_work(dict(raw))
                    if not work:
                        continue
                    key = _dedupe_key(work)
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(work)
    return merged


def merge_candidates(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    seen_titles: set[str] = set()
    out: List[Dict[str, Any]] = []
    for work in groups:
        for item in work:
            key = _dedupe_key(item)
            title_key = _normalize_title(str(item.get("title") or ""))
            if key in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.add(title_key)
            out.append(_annotate_work(dict(item)))
    out = collapse_near_duplicates(out)
    out.sort(key=_sort_key, reverse=True)
    return out


_SCORE_KEYS = (
    "rsoxs_tier",
    "technique_relevance",
    "soft_matter_relevance",
    "method_relevance",
    "kg_priority",
    "corpus_role",
    "kg_primary",
    "kg_primary_tier",
    "kg_seed_rank",
    "facility",
    "beamline",
    "als",
)


def _retry_urls(paper: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    explicit = paper.get("pdf_urls")
    if isinstance(explicit, list):
        urls.extend(str(u) for u in explicit if str(u).strip())
    err = str((paper.get("ingestion") or {}).get("error") or "")
    for match in re.findall(r"https://[^\s\"'>]+", err):
        urls.append(match.rstrip(").,;"))
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    if arxiv_id:
        urls.append(f"https://arxiv.org/pdf/{arxiv_id}")
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(paper.get("doi") or ""), flags=re.I)
    if doi.lower().startswith("10.48550/arxiv."):
        ident = doi.split("10.48550/arXiv.", 1)[-1]
        urls.append(f"https://arxiv.org/pdf/{ident}")
    seen: set[str] = set()
    out: List[str] = []
    for url in urls:
        mapped = dl.rewrite_oa_pdf_url(url)
        if not mapped or mapped in seen:
            continue
        seen.add(mapped)
        out.append(mapped)
    for fallback in _europepmc_fallback_urls(out):
        if fallback not in seen:
            seen.add(fallback)
            out.append(fallback)
    return out


def retry_failed_pdfs(manifest: Dict[str, Any], dest_root: Path, delay: float) -> int:
    """Retry skipped/failed rows that still have no PDF on disk."""
    recovered = 0
    for paper in list(manifest.get("papers") or []):
        status = str((paper.get("ingestion") or {}).get("status") or "")
        rel = paper.get("pdf_path")
        if rel and (REPO_ROOT / str(rel)).exists():
            continue
        if status not in {"failed", "skipped"}:
            continue
        urls = _retry_urls(paper)
        if not urls:
            continue
        year = str(paper.get("publication_year") or year_from_work(paper))
        stem = _safe_name(paper)
        year_dir = dest_root / year
        dest = year_dir / f"{stem}.pdf"
        year_dir.mkdir(parents=True, exist_ok=True)
        ok = False
        last_url = ""
        for url in urls:
            last_url = url
            ok = dl.download_pdf(url, str(dest))
            if ok:
                break
        if not ok:
            paper["pdf_path"] = None
            paper["ingestion"] = {
                **(paper.get("ingestion") or {}),
                "status": "failed",
                "error": f"download failed: {last_url}",
            }
            continue
        paper["pdf_path"] = str(dest.relative_to(REPO_ROOT))
        paper["pdf_sha256"] = _sha256(dest)
        paper["downloaded_at"] = _now()
        paper["ingestion"] = {
            **(paper.get("ingestion") or {}),
            "status": "pending",
            "error": None,
        }
        recovered += 1
        LOGGER.info("Recovered PDF: %s", paper["pdf_path"])
        if delay > 0:
            time.sleep(delay)
    return recovered


def harvest(
    *,
    dest_root: Path,
    max_pdfs: int,
    per_query: int,
    mailto: Optional[str],
    delay: float,
) -> Dict[str, Any]:
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    recovered = retry_failed_pdfs(manifest, dest_root, delay)
    if recovered:
        LOGGER.info("Recovered %d previously failed/skipped PDFs", recovered)
        _save_manifest(manifest_path, manifest)

    author_works = search_openalex_authors(AUTHOR_NAMES, per_query, mailto)
    author_arxiv = search_arxiv(ARXIV_AUTHOR_QUERIES, per_query)
    author_oa = search_openalex(
        OPENALEX_AUTHOR_QUERIES,
        per_query,
        mailto,
        oa_only=False,
        require_pdf=False,
    )
    arxiv_hits = search_arxiv(ARXIV_QUERIES, per_query)
    openalex_hits = search_openalex(OPENALEX_QUERIES, per_query, mailto)
    candidates = merge_candidates(
        author_works,
        author_arxiv,
        author_oa,
        arxiv_hits,
        openalex_hits,
    )
    LOGGER.info(
        "Merged %d candidates (author %d/%d/%d, arXiv %d, OpenAlex %d); downloading up to %d",
        len(candidates),
        len(author_works),
        len(author_arxiv),
        len(author_oa),
        len(arxiv_hits),
        len(openalex_hits),
        max_pdfs,
    )

    downloaded = 0
    skipped_existing = 0
    failed = 0
    pdf_n = sum(
        1
        for p in (manifest.get("papers") or [])
        if p.get("pdf_path") and (REPO_ROOT / str(p["pdf_path"])).exists()
    )
    for work in candidates:
        abstract = str(work.get("abstract") or "")
        title = str(work.get("title") or "")
        meta = score_rsoxs(title, abstract)
        tier = meta["rsoxs_tier"]
        if tier == "C":
            continue
        urls = _normalize_pdf_urls(work.get("pdf_urls") or [])
        for fallback in _europepmc_fallback_urls(urls):
            if fallback not in urls:
                urls.append(fallback)
        doi = str(work.get("doi") or "")
        year = year_from_work(work)
        stem = _safe_name(work) if doi else doi_stem(doi or str(work.get("id") or "work"))
        year_dir = dest_root / year
        dest = year_dir / f"{stem}.pdf"
        rel = str(dest.relative_to(REPO_ROOT))
        arxiv_id = _arxiv_id_from_work(work)
        relevance = {k: meta[k] for k in _SCORE_KEYS}
        base_row = {
            "doi": doi or None,
            "arxiv_id": arxiv_id,
            "title": work.get("title"),
            "abstract": abstract[:4000] if abstract else None,
            "publication_year": work.get("publication_year"),
            "publication_date": work.get("publication_date"),
            "source": work.get("source") or "openalex",
            "pdf_path": rel,
            "pdf_urls": urls,
            "tiled_uri": None,
            **relevance,
            "ingestion": {
                "status": "pending",
                "kg_version": "v1",
                "extracted_at": None,
                "in_kg_at": None,
                "in_tiled_graph_at": None,
                "pages_total": None,
                "error": None,
            },
        }

        if dest.exists() and dest.stat().st_size > 0:
            LOGGER.info("Already on disk: %s", rel)
            base_row["pdf_sha256"] = _sha256(dest)
            base_row["downloaded_at"] = base_row.get("downloaded_at")
            if not base_row.get("downloaded_at"):
                base_row["downloaded_at"] = _now()
            _upsert_paper(manifest, base_row)
            skipped_existing += 1
            downloaded += 1
            continue

        if not urls:
            base_row["pdf_path"] = None
            base_row["ingestion"] = {
                **base_row["ingestion"],
                "status": "skipped",
                "error": "no reliable OA PDF URL",
            }
            _upsert_paper(manifest, base_row)
            continue

        if pdf_n >= max_pdfs:
            break

        year_dir.mkdir(parents=True, exist_ok=True)

        ok = False
        last_url = ""
        for url in urls:
            last_url = url
            ok = dl.download_pdf(url, str(dest))
            if ok:
                break
        if not ok:
            failed += 1
            base_row["pdf_path"] = None
            base_row["ingestion"] = {
                **base_row["ingestion"],
                "status": "failed",
                "error": f"download failed: {last_url}",
            }
            _upsert_paper(manifest, base_row)
            _save_manifest(manifest_path, manifest)
            continue

        base_row["pdf_sha256"] = _sha256(dest)
        base_row["downloaded_at"] = _now()
        _upsert_paper(manifest, base_row)
        downloaded += 1
        pdf_n += 1
        LOGGER.info("Harvested %d PDFs (cap %d): %s", pdf_n, max_pdfs, rel)
        _save_manifest(manifest_path, manifest)
        if delay > 0:
            time.sleep(delay)

    _save_manifest(manifest_path, manifest)
    rescore = rescore_manifest(dest_root)
    summary = {
        "candidates": len(candidates),
        "downloaded_or_existing": downloaded,
        "already_on_disk": skipped_existing,
        "failed": failed,
        "manifest": str(manifest_path),
        "papers_in_manifest": len(manifest.get("papers") or []),
        "roles": rescore.get("roles"),
        "als": rescore.get("als"),
    }
    LOGGER.info("Done: %s", summary)
    return summary


def rescore_manifest(dest_root: Path) -> Dict[str, Any]:
    """Re-rank papers already in the manifest; does not download or delete."""
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    for paper in manifest.get("papers") or []:
        extra = ""
        rel = paper.get("pdf_path")
        if rel:
            extra = pdf_preview_text(REPO_ROOT / rel)
        meta = score_rsoxs(str(paper.get("title") or ""), str(paper.get("abstract") or ""), extra)
        for key in _SCORE_KEYS:
            paper[key] = meta[key]
        paper.pop("rsoxs_emphasis", None)
        paper.pop("rsoxs_score", None)
    manifest["papers"] = collapse_near_duplicates(list(manifest.get("papers") or []))
    for paper in manifest.get("papers") or []:
        if not paper.get("arxiv_id"):
            for ver in paper.get("versions") or []:
                if ver.get("arxiv_id"):
                    paper["arxiv_id"] = ver["arxiv_id"]
                    break
    counts = {"A": 0, "B": 0, "C": 0, "als": 0, "kg_primary": 0, "kg_S": 0, "kg_A": 0}
    roles = {}
    for paper in manifest.get("papers") or []:
        counts[paper.get("rsoxs_tier") or "C"] = counts.get(paper.get("rsoxs_tier") or "C", 0) + 1
        role = str(paper.get("corpus_role") or "general")
        roles[role] = roles.get(role, 0) + 1
        if paper.get("als"):
            counts["als"] += 1
        if paper.get("kg_primary"):
            counts["kg_primary"] += 1
        if paper.get("kg_primary_tier") == "S":
            counts["kg_S"] += 1
        elif paper.get("kg_primary_tier") == "A":
            counts["kg_A"] += 1
    _save_manifest(manifest_path, manifest)
    summary = {
        "manifest": str(manifest_path),
        "papers": len(manifest.get("papers") or []),
        "roles": roles,
        **counts,
    }
    LOGGER.info("Rescored: %s", summary)
    return summary


def _arxiv_pdf_urls_for_title(title: str) -> List[str]:
    if not title or len(title) < 12:
        return []
    try:
        import arxiv

        query = f'ti:"{title.split(":")[0].strip()}"'
        client = arxiv.Client(page_size=5, delay_seconds=3, num_retries=1)
        search = arxiv.Search(query=query, max_results=5, sort_by=arxiv.SortCriterion.Relevance)
        urls: List[str] = []
        for result in client.results(search):
            if titles_near_duplicate(title, str(result.title or ""), threshold=0.72):
                pdf = str(result.pdf_url or "").strip()
                if pdf and _is_reliable_pdf_url(pdf):
                    urls.append(pdf)
        return urls
    except Exception as exc:
        LOGGER.warning("arXiv title lookup failed for %r (%s)", title[:80], exc)
        return []


def _strip_jats(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def _crossref_by_doi_or_title(query: str) -> Optional[Dict[str, Any]]:
    import urllib.parse
    import urllib.request

    q = (query or "").strip()
    headers = {"User-Agent": "FAIRtoWISE-harvest/1.0 (mailto:dev@localhost)"}
    doi = _canonical_doi(q)
    url = (
        f"https://api.crossref.org/works/{urllib.parse.quote(doi.replace('https://doi.org/', ''))}"
        if doi
        else "https://api.crossref.org/works?query.bibliographic="
        + urllib.parse.quote(q)
        + "&rows=5"
    )
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        LOGGER.warning("Crossref lookup failed for %r (%s)", query, exc)
        return None
    msg = payload.get("message") or {}
    items = [msg] if doi and msg.get("DOI") else list(msg.get("items") or [])
    picked = None
    for item in items:
        title = " ".join(item.get("title") or [])
        if doi or titles_near_duplicate(q, title, threshold=0.5) or q.lower() in title.lower():
            picked = item
            break
    if picked is None and items:
        picked = items[0]
    if not picked:
        return None
    tdm_urls, _tdm_sources = dl.select_crossref_tdm_pdfs(picked)
    issued = (picked.get("published-print") or picked.get("published-online") or {}).get("date-parts") or [[]]
    parts = issued[0] if issued else []
    year = parts[0] if parts else None
    date = "-".join(f"{n:02d}" if i else str(n) for i, n in enumerate(parts)) if parts else None
    if date and len(parts) == 1:
        date = f"{parts[0]}-01-01"
    work = {
        "title": " ".join(picked.get("title") or []),
        "abstract": _strip_jats(str(picked.get("abstract") or "")),
        "doi": _canonical_doi(str(picked.get("DOI") or "")),
        "publication_year": year,
        "publication_date": date,
        "pdf_urls": tdm_urls,
        "source": "crossref",
    }
    # Keep explicit DOI lookups even if the title omits the RSoXS phrase;
    # harvest still records skipped when there is no OA PDF.
    if not doi and classify_rsoxs(work["title"], work.get("abstract") or "") == "C":
        if not re.search(r"rsoxs|resonant (elastic )?soft x", work["title"], re.I):
            return None
    return work


def _openalex_by_doi_or_title(query: str, mailto: Optional[str]) -> Optional[Dict[str, Any]]:
    work = _crossref_by_doi_or_title(query)
    if work:
        return work
    from pyalex import Works, config as pyalex_config

    if mailto:
        pyalex_config.email = mailto
    q = (query or "").strip()
    raw = None
    try:
        doi = _canonical_doi(q)
        if doi:
            raw = Works()[doi]
        if raw is None:
            hits = Works().search(q).get(per_page=5) or []
            for cand in hits:
                title = str(cand.get("display_name") or cand.get("title") or "")
                if titles_near_duplicate(q, title, threshold=0.55) or q.lower() in title.lower():
                    raw = cand
                    break
            if raw is None and hits:
                raw = hits[0]
    except Exception as exc:
        LOGGER.warning("OpenAlex lookup failed for %r (%s)", query, exc)
        return None
    if not raw:
        return None
    return _normalize_openalex_work(dict(raw))


def ingest_known_works(
    dest_root: Path,
    queries: Iterable[str],
    mailto: Optional[str] = None,
    delay: float = 1.0,
) -> List[Dict[str, Any]]:
    """Add specific Works by DOI or title. Keep a row even without an OA PDF."""
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    added: List[Dict[str, Any]] = []
    for query in queries:
        work = _openalex_by_doi_or_title(query, mailto)
        if not work:
            LOGGER.warning("No Crossref/OpenAlex hit for %r — skipped", query)
            continue
        if _canonical_doi(str(work.get("title") or "")):
            LOGGER.warning("Lookup returned DOI as title for %r — skipped", query)
            continue
        title = str(work.get("title") or query)
        abstract = str(work.get("abstract") or "")
        meta = score_rsoxs(title, abstract)
        urls = _normalize_pdf_urls(work.get("pdf_urls") or [])
        for extra in _arxiv_pdf_urls_for_title(str(work.get("title") or "")):
            if extra not in urls:
                urls.append(extra)
        for fallback in _europepmc_fallback_urls(urls):
            if fallback not in urls:
                urls.append(fallback)
        doi = str(work.get("doi") or "")
        year = year_from_work(work)
        stem = _safe_name(work) if doi else doi_stem(doi or str(work.get("id") or "work"))
        dest = dest_root / year / f"{stem}.pdf"
        rel = str(dest.relative_to(REPO_ROOT))
        row = {
            "doi": doi or None,
            "arxiv_id": _arxiv_id_from_work(work),
            "title": title,
            "abstract": abstract[:4000] if abstract else None,
            "publication_year": work.get("publication_year"),
            "publication_date": work.get("publication_date"),
            "source": work.get("source") or "openalex",
            "pdf_path": rel,
            "pdf_urls": urls,
            "tiled_uri": None,
            **{k: meta[k] for k in _SCORE_KEYS},
            "ingestion": {
                "status": "pending",
                "kg_version": "v1",
                "extracted_at": None,
                "in_kg_at": None,
                "in_tiled_graph_at": None,
                "pages_total": None,
                "error": None,
            },
        }
        if dest.exists() and dest.stat().st_size > 0:
            row["pdf_sha256"] = _sha256(dest)
            row["downloaded_at"] = _now()
        elif not urls:
            row["pdf_path"] = None
            row["ingestion"]["status"] = "skipped"
            row["ingestion"]["error"] = "no reliable OA PDF URL"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok = False
            last_url = ""
            for url in urls:
                last_url = url
                ok = dl.download_pdf(url, str(dest))
                if ok:
                    break
            if ok:
                row["pdf_sha256"] = _sha256(dest)
                row["downloaded_at"] = _now()
            else:
                row["pdf_path"] = None
                row["ingestion"]["status"] = "failed"
                row["ingestion"]["error"] = f"download failed: {last_url}"
            if delay > 0:
                time.sleep(delay)
        _upsert_paper(manifest, row)
        added.append(row)
        LOGGER.info(
            "Ingested %s (%s, pdf=%s)",
            title,
            row.get("doi"),
            row.get("pdf_path"),
        )
    _save_manifest(manifest_path, manifest)
    rescore_manifest(dest_root)
    return added


PUBTEMP_DEFAULT = (
    REPO_ROOT / "papers" / "rsoxs" / "beamline_blueprint" / "PubTempPubListExport9_11_26_14_05_24.rtf"
)
PUBTEMP_EXPORT_DATE = "2026-09-11"
PUBTEMP_RELPATH = "papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf"

_RTF_SPECIAL = {
    "ldblquote": '"',
    "rdblquote": '"',
    "lquote": "'",
    "rquote": "'",
    "emdash": "\u2014",
    "endash": "\u2013",
    "tab": " ",
    "line": "\n",
    "par": "\n",
    "bullet": "\u2022",
}

_PUBTEMP_KIND = (
    ("refereed journal", "journal"),
    ("refereed conference", "conference"),
    ("theses", "thesis"),
    ("non-refereed", "non_refereed"),
    ("patents", "patent"),
    ("awards", "award"),
    ("invited lectures", "invited_lecture"),
)

_DOI_INLINE_RE = re.compile(r"\(doi:\s*([^)]+)\)", re.I)
_PMID_RE = re.compile(r"\bPMID[:\s]+(\d+)\b", re.I)
_ARXIV_RE = re.compile(r"\barXiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
_URL_RE = re.compile(r"https?://[^\s\]>'\"\\]+", re.I)
_YEAR_PAREN_RE = re.compile(r"\((19|20)\d{2}\)")
_YEAR_BARE_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_TITLE_RE = re.compile(r'"([^"]+)"')
_HEADER_RE = re.compile(r"\\f43([^\\{}]+?)\\b0\\par")


def _rtf_unescape(rtf: str) -> str:
    def uni(match: re.Match) -> str:
        n = int(match.group(1))
        if n < 0:
            n += 65536
        return chr(n)

    text = re.sub(r"\\u(-?\d+)[^0-9]", uni, rtf)
    text = text.replace("\\~", " ")
    text = re.sub(
        r"\\'([0-9a-fA-F]{2})",
        lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="replace"),
        text,
    )

    def ctrl(match: re.Match) -> str:
        word = match.group(1)
        return _RTF_SPECIAL.get(word, " ")

    text = re.sub(r"\\([a-zA-Z]+)(-?\d+)?[ ]?", ctrl, text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()


def _pubtemp_kind(section: Optional[str]) -> str:
    lowered = (section or "").lower()
    for prefix, kind in _PUBTEMP_KIND:
        if prefix in lowered:
            return kind
    return "unknown"


def _split_authors(raw: str) -> List[str]:
    text = re.sub(r"\s+", " ", (raw or "").strip().rstrip(","))
    text = re.sub(r"\band\b", ",", text, flags=re.I)
    parts = [p.strip(" ,") for p in text.split(",") if p.strip(" ,")]
    return parts


def _first_author_key(paper: Dict[str, Any]) -> str:
    authors = paper.get("authors")
    if isinstance(authors, list) and authors:
        name = str(authors[0] or "")
    else:
        name = str(authors or "")
    if not name:
        return ""
    surname = name.split(",")[0] if "," in name else name.split()[-1]
    return _normalize_title(surname)


def _arxiv_key(arxiv_id: Optional[str]) -> str:
    if not arxiv_id:
        return ""
    return re.sub(r"v\d+$", "", str(arxiv_id).strip().lower())


def parse_pubtemp_plain(text: str, section: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse one unescaped ALS/PubTemp citation into a Work dict. Never invents DOIs."""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return None
    title_m = _TITLE_RE.search(blob)
    if not title_m:
        return None
    title = title_m.group(1).strip().rstrip(",")
    authors_raw = blob[: title_m.start()].strip(" ,")
    doi_m = _DOI_INLINE_RE.search(blob)
    doi = None
    if doi_m:
        doi = _canonical_doi(doi_m.group(1).strip())
    pmid_m = _PMID_RE.search(blob)
    arxiv_m = _ARXIV_RE.search(blob)
    year = None
    year_m = _YEAR_PAREN_RE.search(blob)
    if year_m:
        year = int(year_m.group(0)[1:-1])
    else:
        years = _YEAR_BARE_RE.findall(blob)
        if years:
            year = int(years[-1])
    urls = []
    seen_u: set[str] = set()
    for url in _URL_RE.findall(blob):
        cleaned = url.rstrip(").,;]")
        if cleaned in seen_u:
            continue
        seen_u.add(cleaned)
        urls.append(cleaned)
    return {
        "title": title,
        "authors": _split_authors(authors_raw),
        "authors_raw": authors_raw,
        "doi": doi,
        "pmid": pmid_m.group(1) if pmid_m else None,
        "arxiv_id": arxiv_m.group(1) if arxiv_m else None,
        "publication_year": year,
        "publication_date": f"{year}-01-01" if year else None,
        "urls": urls,
        "pubtemp_section": section,
        "pubtemp_kind": _pubtemp_kind(section),
        "citation": blob,
    }


def parse_pubtemp_rtf(path: Path) -> List[Dict[str, Any]]:
    """Read an ALS PubTemp RTF export. One record per ``\\cr`` citation."""
    raw = path.read_text(encoding="latin-1")
    section: Optional[str] = None
    records: List[Dict[str, Any]] = []
    for part in re.split(r"(?=\\cr )", raw):
        cr_at = part.find("\\cr ")
        prefix = part if cr_at < 0 else part[:cr_at]
        for header in _HEADER_RE.findall(prefix):
            section = header.strip()
        if cr_at < 0:
            continue
        chunk = part[cr_at + 4 :]
        next_headers = list(_HEADER_RE.finditer(chunk))
        record_rtf = chunk[: next_headers[0].start()] if next_headers else chunk
        parsed = parse_pubtemp_plain(_rtf_unescape(record_rtf), section)
        if parsed:
            records.append(parsed)
        for header in next_headers:
            section = header.group(1).strip()
    return records


def works_same_identity(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """DOI / arXiv / title-authors IdentityResolver (plan lock)."""
    doi_l, doi_r = _doi_key(left.get("doi")), _doi_key(right.get("doi"))
    if doi_l and doi_r:
        if doi_l == doi_r:
            return True
        if not (_is_preprint(left) or _is_preprint(right)):
            return False
    arxiv_l, arxiv_r = _arxiv_key(left.get("arxiv_id")), _arxiv_key(right.get("arxiv_id"))
    if arxiv_l and arxiv_r and arxiv_l == arxiv_r:
        return True
    if doi_l:
        for ver in left.get("versions") or []:
            if _doi_key(ver.get("doi")) == doi_r and doi_r:
                return True
            if _arxiv_key(ver.get("arxiv_id")) == arxiv_r and arxiv_r:
                return True
    if doi_r:
        for ver in right.get("versions") or []:
            if _doi_key(ver.get("doi")) == doi_l and doi_l:
                return True
            if _arxiv_key(ver.get("arxiv_id")) == arxiv_l and arxiv_l:
                return True
    if not titles_near_duplicate(str(left.get("title") or ""), str(right.get("title") or "")):
        return False
    key_l, key_r = _first_author_key(left), _first_author_key(right)
    if key_l and key_r and key_l != key_r:
        return False
    if doi_l and doi_r and doi_l != doi_r and not (_is_preprint(left) or _is_preprint(right)):
        return False
    return True


def _find_existing_work(papers: List[Dict[str, Any]], row: Dict[str, Any]) -> Optional[int]:
    for i, existing in enumerate(papers):
        if works_same_identity(existing, row):
            return i
    return None


def _pubtemp_provenance(record: Dict[str, Any], rtf_rel: str, export_date: str) -> Dict[str, Any]:
    return {
        "source": "als_pubtemp",
        "file": rtf_rel,
        "export_date": export_date,
        "section": record.get("pubtemp_section"),
        "kind": record.get("pubtemp_kind"),
    }


def _mark_included(paper: Dict[str, Any], record: Dict[str, Any], rtf_rel: str, export_date: str) -> None:
    paper["selection"] = {"included": True}
    provenance = list(paper.get("provenance") or [])
    stamp = _pubtemp_provenance(record, rtf_rel, export_date)
    if stamp not in provenance:
        provenance.append(stamp)
    paper["provenance"] = provenance
    paper["pubtemp_kind"] = record.get("pubtemp_kind") or paper.get("pubtemp_kind")
    if record.get("authors") and not paper.get("authors"):
        paper["authors"] = record["authors"]
    if record.get("pmid") and not paper.get("pmid"):
        paper["pmid"] = record["pmid"]
    incoming_urls = _normalize_pdf_urls(record.get("urls") or [])
    if incoming_urls:
        merged_urls = list(paper.get("pdf_urls") or [])
        for url in incoming_urls:
            if url not in merged_urls:
                merged_urls.append(url)
        paper["pdf_urls"] = merged_urls


def _pubtemp_row(record: Dict[str, Any], rtf_rel: str, export_date: str) -> Dict[str, Any]:
    title = str(record.get("title") or "")
    meta = score_rsoxs(title, "")
    doi = record.get("doi")
    urls = _normalize_pdf_urls(record.get("urls") or [])
    row = {
        "doi": doi,
        "arxiv_id": record.get("arxiv_id"),
        "title": title,
        "authors": record.get("authors") or [],
        "abstract": None,
        "publication_year": record.get("publication_year"),
        "publication_date": record.get("publication_date"),
        "source": "pubtemp",
        "pdf_path": None,
        "pdf_urls": urls,
        "tiled_uri": None,
        "pmid": record.get("pmid"),
        **{k: meta[k] for k in _SCORE_KEYS},
        "ingestion": {
            "status": "pending",
            "kg_version": "v1",
            "extracted_at": None,
            "in_kg_at": None,
            "in_tiled_graph_at": None,
            "pages_total": None,
            "error": None,
        },
        "versions": [
            {
                "kind": "preprint" if _is_preprint({"doi": doi}) else "journal",
                "doi": doi,
                "arxiv_id": record.get("arxiv_id"),
                "pdf_path": None,
                "publication_date": record.get("publication_date"),
                "source": "pubtemp",
            }
        ],
    }
    _mark_included(row, record, rtf_rel, export_date)
    return row


def _pdf_on_disk(paper: Dict[str, Any]) -> bool:
    rel = paper.get("pdf_path")
    return bool(rel) and (REPO_ROOT / str(rel)).exists() and (REPO_ROOT / str(rel)).stat().st_size > 0


def import_pubtemp(
    dest_root: Path,
    rtf_path: Path,
    export_date: str = PUBTEMP_EXPORT_DATE,
) -> Dict[str, Any]:
    """Merge PubTemp records into the harvest manifest. Does not download PDFs."""
    records = parse_pubtemp_rtf(rtf_path)
    try:
        rtf_rel = str(rtf_path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        rtf_rel = str(rtf_path)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    papers = list(manifest.get("papers") or [])
    matched_existing = 0
    new_works = 0
    already_had_pdf = 0
    for record in records:
        row = _pubtemp_row(record, rtf_rel, export_date)
        idx = _find_existing_work(papers, row)
        if idx is None:
            papers.append(row)
            new_works += 1
            continue
        existing = papers[idx]
        _mark_included(existing, record, rtf_rel, export_date)
        if not existing.get("doi") and row.get("doi"):
            existing["doi"] = row["doi"]
        elif (
            existing.get("doi")
            and row.get("doi")
            and _doi_key(existing.get("doi")) != _doi_key(row.get("doi"))
            and _is_preprint(existing)
            and not _is_preprint(row)
        ):
            existing["doi"] = row["doi"]
        if not existing.get("arxiv_id") and row.get("arxiv_id"):
            existing["arxiv_id"] = row["arxiv_id"]
        if not existing.get("publication_year") and row.get("publication_year"):
            existing["publication_year"] = row["publication_year"]
            existing["publication_date"] = existing.get("publication_date") or row.get("publication_date")
        versions = list(existing.get("versions") or [])
        incoming_ver = (row.get("versions") or [None])[0]
        if incoming_ver:
            ver_key = str(incoming_ver.get("doi") or incoming_ver.get("arxiv_id") or "")
            seen_keys = {str(v.get("doi") or v.get("arxiv_id") or "") for v in versions}
            if ver_key and ver_key not in seen_keys:
                versions.append(incoming_ver)
                existing["versions"] = versions
        papers[idx] = existing
        matched_existing += 1
        if _pdf_on_disk(existing):
            already_had_pdf += 1
    manifest["papers"] = papers
    included = sum(1 for p in papers if (p.get("selection") or {}).get("included"))
    queued = sum(
        1
        for p in papers
        if (p.get("selection") or {}).get("included") and not _pdf_on_disk(p)
    )
    had_pdf = sum(1 for p in papers if (p.get("selection") or {}).get("included") and _pdf_on_disk(p))
    manifest["pubtemp_import"] = {
        "file": rtf_rel,
        "export_date": export_date,
        "parsed_count": len(records),
        "matched_existing": matched_existing,
        "new_works": new_works,
        "included": included,
        "already_had_pdf": had_pdf,
        "newly_queued": queued,
    }
    _save_manifest(manifest_path, manifest)
    LOGGER.info(
        "PubTemp import: parsed=%d matched=%d new=%d included=%d had_pdf=%d queued=%d",
        len(records),
        matched_existing,
        new_works,
        included,
        had_pdf,
        queued,
    )
    return {
        "manifest": str(manifest_path),
        "parsed_count": len(records),
        "matched_existing": matched_existing,
        "new_works": new_works,
        "included": included,
        "already_had_pdf": had_pdf,
        "newly_queued": queued,
        "kind_counts": {
            kind: sum(1 for r in records if r.get("pubtemp_kind") == kind)
            for _, kind in _PUBTEMP_KIND
        },
    }


def _openalex_oa_urls_for_doi(
    doi: str, mailto: Optional[str]
) -> Tuple[List[str], Optional[str], List[str]]:
    """Allowlisted OA PDF URLs for a known DOI. Does not invent identifiers."""
    canon = _canonical_doi(doi)
    if not canon:
        return [], None, []
    bare_doi = canon.removeprefix("https://doi.org/")
    endpoint = (
        "https://api.openalex.org/works/doi:"
        + urllib.parse.quote(bare_doi, safe="")
    )
    if dl.is_usable_contact_email(mailto):
        endpoint += "?mailto=" + urllib.parse.quote(str(mailto))
    user_agent = "FAIRtoWISE-harvest/1.0 (legal OA PDF resolver"
    if dl.is_usable_contact_email(mailto):
        user_agent += f"; mailto:{mailto}"
    user_agent += ")"
    try:
        request = urllib.request.Request(
            endpoint,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        LOGGER.warning("OpenAlex DOI lookup failed for %r (%s)", canon, exc)
        return [], None, []
    if not isinstance(raw, dict):
        return [], None, []
    urls, arxiv_id, sources = dl.select_openalex_oa_pdfs(raw)
    if not arxiv_id:
        arxiv_id = _arxiv_id_from_work(raw)
    return urls, arxiv_id, sources


def _record_oa_resolution(
    paper: Dict[str, Any],
    urls: List[str],
    sources: List[str],
) -> None:
    paper["pdf_urls"] = urls
    if sources:
        paper["oa_source"] = sources[0] if len(sources) == 1 else sources
        stamp = {
            "kind": "oa_resolution",
            "source": sources[0] if len(sources) == 1 else sources,
            "urls": urls,
            "resolved_at": _now(),
        }
        provenance = list(paper.get("provenance") or [])
        if stamp not in provenance:
            provenance.append(stamp)
        paper["provenance"] = provenance


def _s2_from_cache(
    doi: Optional[str],
    cache: Optional[Dict[str, Tuple[List[str], List[str]]]],
) -> Tuple[List[str], List[str]]:
    if not doi or not cache:
        return [], []
    candidates = [str(doi)]
    bare = dl.bare_doi(doi)
    if bare:
        candidates.extend([bare, bare.lower(), f"https://doi.org/{bare}"])
    seen: set[str] = set()
    for key in candidates:
        if not key or key in seen:
            continue
        seen.add(key)
        hit = cache.get(key) or cache.get(key.lower())
        if hit:
            urls, sources = hit
            return list(urls or []), list(sources or [])
    return [], []


def _append_trusted_urls(
    urls: List[str],
    sources: List[str],
    extra_urls: Iterable[str],
    extra_sources: Iterable[str],
) -> None:
    added = False
    for extra in extra_urls:
        mapped = dl.rewrite_oa_pdf_url(str(extra))
        if mapped and mapped not in urls:
            urls.append(mapped)
            added = True
    if added:
        sources.extend(extra_sources)


def _missing_pdf_dois(papers: Iterable[Dict[str, Any]]) -> List[str]:
    """Bare DOIs for included Works that still lack a PDF file. Does not invent DOIs."""
    dois: List[str] = []
    seen: set[str] = set()
    for paper in papers:
        if not (paper.get("selection") or {}).get("included"):
            continue
        if _pdf_on_disk(paper):
            continue
        bare = dl.bare_doi(paper.get("doi"))
        if not bare:
            continue
        key = bare.lower()
        if key in seen:
            continue
        seen.add(key)
        dois.append(bare)
    return dois


def _resolve_oa_pdfs(
    paper: Dict[str, Any],
    mailto: Optional[str],
    s2_cache: Optional[Dict[str, Tuple[List[str], List[str]]]] = None,
) -> Tuple[List[str], List[str]]:
    """Merge existing URLs with OpenAlex / Unpaywall / extra OA PDFs. Repositories first."""
    sources: List[str] = []
    urls = _normalize_pdf_urls(paper.get("pdf_urls") or [])
    doi = paper.get("doi")
    arxiv_id = str(paper.get("arxiv_id") or "").strip() or None
    if doi and s2_cache:
        s2_urls, s2_sources = _s2_from_cache(str(doi), s2_cache)
        _append_trusted_urls(urls, sources, s2_urls, s2_sources)
    if doi:
        oa_urls, oa_arxiv, oa_sources = _openalex_oa_urls_for_doi(str(doi), mailto)
        for extra in oa_urls:
            if extra not in urls:
                urls.append(extra)
        sources.extend(oa_sources)
        if not arxiv_id and oa_arxiv:
            paper["arxiv_id"] = oa_arxiv
            arxiv_id = oa_arxiv
    if not urls and doi and mailto:
        upw_urls, upw_sources = dl.fetch_unpaywall_oa_pdfs(str(doi), mailto)
        for extra in upw_urls:
            if extra not in urls:
                urls.append(extra)
        sources.extend(upw_sources)
    if not urls and doi:
        osti_urls, osti_sources = dl.fetch_osti_oa_pdfs(str(doi))
        _append_trusted_urls(urls, sources, osti_urls, osti_sources)
    if not urls and doi:
        core_urls, core_sources = dl.fetch_core_oa_pdfs(
            str(doi), os.environ.get("CORE_API_KEY")
        )
        _append_trusted_urls(urls, sources, core_urls, core_sources)
    if not urls and doi:
        epmc_urls, epmc_sources = dl.fetch_europepmc_search_oa_pdfs(str(doi))
        _append_trusted_urls(urls, sources, epmc_urls, epmc_sources)
    if not urls and doi:
        cr_urls, cr_sources = dl.fetch_crossref_tdm_pdfs(str(doi))
        _append_trusted_urls(urls, sources, cr_urls, cr_sources)
    if arxiv_id:
        pdf = f"https://arxiv.org/pdf/{arxiv_id}"
        mapped = dl.rewrite_oa_pdf_url(pdf)
        if mapped and mapped not in urls:
            urls.append(mapped)
    for fallback in _europepmc_fallback_urls(urls):
        if fallback not in urls:
            urls.append(fallback)
    _record_oa_resolution(paper, urls, _dedupe_sources(sources))
    return urls, _dedupe_sources(sources)


def _dedupe_sources(sources: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for source in sources:
        name = str(source or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def harvest_pending_pdfs(
    dest_root: Path,
    mailto: Optional[str] = None,
    delay: float = 1.0,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Download allowlisted OA PDFs for included Works that still lack a file."""
    dl.set_delay_floor(delay)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    attempted = 0
    downloaded = 0
    skipped = 0
    failed = 0
    pending_dois = _missing_pdf_dois(manifest.get("papers") or [])
    s2_cache: Dict[str, Tuple[List[str], List[str]]] = {}
    if pending_dois:
        LOGGER.info(
            "Semantic Scholar batch lookup for %d DOIs still missing a PDF",
            len(pending_dois),
        )
        s2_cache = dl.fetch_semantic_scholar_batch_oa_pdfs(pending_dois)
        hits = sum(1 for urls, _sources in s2_cache.values() if urls)
        LOGGER.info(
            "Semantic Scholar allowlisted PDFs for %d/%d DOIs",
            hits,
            len(pending_dois),
        )
    if not str(os.environ.get("CORE_API_KEY") or "").strip():
        LOGGER.info("CORE OA resolver skipped: CORE_API_KEY not set")
    for paper in list(manifest.get("papers") or []):
        if not (paper.get("selection") or {}).get("included"):
            continue
        if _pdf_on_disk(paper):
            continue
        if limit is not None and attempted >= limit:
            break
        doi = paper.get("doi")
        urls, sources = _resolve_oa_pdfs(paper, mailto, s2_cache=s2_cache)
        attempted += 1
        LOGGER.info(
            "Pending %s doi=%s urls=%d source=%s title=%s",
            attempted,
            doi or "-",
            len(urls),
            ",".join(sources) or "-",
            (paper.get("title") or "")[:80],
        )
        if not urls:
            paper["ingestion"] = {
                **(paper.get("ingestion") or {}),
                "status": "skipped",
                "error": "no reliable OA PDF URL",
            }
            skipped += 1
            if delay > 0:
                time.sleep(delay)
            continue
        year = str(paper.get("publication_year") or year_from_work(paper) or "unknown")
        stem = _safe_name(paper)
        dest = dest_root / year / f"{stem}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = False
        last_url = ""
        for url in urls:
            last_url = url
            ok = dl.download_pdf(url, str(dest))
            if ok:
                break
        if ok:
            paper["pdf_path"] = str(dest.relative_to(REPO_ROOT))
            paper["pdf_sha256"] = _sha256(dest)
            paper["downloaded_at"] = _now()
            paper["ingestion"] = {
                **(paper.get("ingestion") or {}),
                "status": "pending",
                "error": None,
            }
            downloaded += 1
            LOGGER.info("Harvested OA PDF %s", paper["pdf_path"])
        else:
            paper["pdf_path"] = None
            paper["ingestion"] = {
                **(paper.get("ingestion") or {}),
                "status": "failed",
                "error": f"download failed: {last_url}",
            }
            failed += 1
        pause = delay
        if last_url:
            pause = max(delay, dl.host_delay(dl._host(last_url)))
        if pause > 0:
            time.sleep(pause)
        if attempted % 25 == 0:
            _save_manifest(manifest_path, manifest)
    _save_manifest(manifest_path, manifest)
    return {
        "manifest": str(manifest_path),
        "attempted": attempted,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dest",
        type=Path,
        default=REPO_ROOT / "papers" / "rsoxs",
        help="Corpus root (year folders + manifest.json)",
    )
    p.add_argument("--max-pdfs", type=int, default=200, help="Target OA PDFs (existing kept; only add)")
    p.add_argument("--per-query", type=int, default=50, help="Hits per query per source")
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Minimum seconds between OA lookups/downloads (per-host floors may be higher)",
    )
    p.add_argument("--mailto", default=os.environ.get("OPENALEX_EMAIL"), help="OpenAlex polite pool email")
    p.add_argument(
        "--rescore",
        action="store_true",
        help="Re-rank existing manifest (subdomain + facility metadata) and exit",
    )
    p.add_argument(
        "--add-work",
        action="append",
        default=[],
        help="DOI or title to add (repeatable). Records skipped if no OA PDF.",
    )
    p.add_argument(
        "--import-pubtemp",
        nargs="?",
        const=str(PUBTEMP_DEFAULT),
        default=None,
        help="Parse an ALS/PubTemp RTF and merge Works into the manifest (no download).",
    )
    p.add_argument(
        "--harvest-pending",
        action="store_true",
        help="Download allowlisted OA PDFs for selection.included Works missing a file.",
    )
    p.add_argument(
        "--resolve-oa",
        action="store_true",
        dest="harvest_pending",
        help="Retry skipped/failed included Works via OpenAlex/Unpaywall/extra OA PDFs (alias of --harvest-pending).",
    )
    p.add_argument(
        "--harvest-limit",
        type=int,
        default=None,
        help="Max pending Works to attempt when --harvest-pending is set.",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except Exception:
        pass
    args = parse_args()
    dl.set_delay_floor(args.delay)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if args.import_pubtemp:
        summary = import_pubtemp(args.dest, Path(args.import_pubtemp))
        LOGGER.info("import-pubtemp %s", json.dumps(summary, sort_keys=True))
        if not args.harvest_pending:
            return
    if args.harvest_pending:
        summary = harvest_pending_pdfs(
            args.dest,
            mailto=args.mailto,
            delay=args.delay,
            limit=args.harvest_limit,
        )
        LOGGER.info("harvest-pending %s", json.dumps(summary, sort_keys=True))
        return
    if args.add_work:
        ingest_known_works(args.dest, args.add_work, mailto=args.mailto, delay=args.delay)
        return
    if args.rescore:
        rescore_manifest(args.dest)
        return
    rescore_manifest(args.dest)
    harvest(
        dest_root=args.dest,
        max_pdfs=args.max_pdfs,
        per_query=args.per_query,
        mailto=args.mailto,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
