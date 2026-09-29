#!/usr/bin/env python3
"""Harvest OA X-ray *fundamentals* PDFs into papers/xray/{year}/.

Principles corpus (photons, production, spectroscopy, optics, channels,
q-space, detection physics, dose, cosmic mechanisms). Not RSoXS literature
and not ALS 11.0.1.2 ops. Never writes into papers/rsoxs/. Dedups by
DOI/arXiv against the RSoXS manifest.

Own queries + classify_xray(). Search/download helpers come from
harvest_rsoxs.py and download_pdfs.py.

Does not extract terms or write matkg_xray_v1.json.

Usage (from repo root):
  python3 scripts/harvest_xray.py --max-pdfs 40
  python3 scripts/harvest_xray.py --rescore
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import download_pdfs as dl  # noqa: E402
import harvest_rsoxs as hr  # noqa: E402

LOGGER = logging.getLogger("harvest_xray")

RSOXS_DEST = REPO_ROOT / "papers" / "rsoxs"
XRAY_DEST = REPO_ROOT / "papers" / "xray"

# Quoted phrases so hyphens stay literal (arXiv Lucene).
OPENALEX_QUERIES = (
    '"bremsstrahlung" X-ray',
    '"characteristic radiation" X-ray',
    '"Moseley\'s law"',
    '"undulator radiation"',
    '"undulator parameter"',
    '"synchrotron radiation" brilliance',
    '"inverse Compton" X-ray',
    '"X-ray free-electron laser" SASE',
    '"transition radiation" X-ray',
    '"channeling radiation"',
    '"dipole approximation" X-ray',
    '"selection rule" X-ray',
    '"core-hole lifetime"',
    '"natural linewidth" X-ray',
    '"photoelectric equation"',
    '"Einstein photoelectric" X-ray',
    '"EXAFS" interference',
    '"photoelectron wave" X-ray',
    '"photoelectric absorption" X-ray',
    '"photoelectric effect" X-ray',
    '"X-ray absorption edge"',
    '"fluorescence yield"',
    '"Auger" X-ray',
    '"Compton scattering" X-ray',
    '"Klein-Nishina"',
    '"Thomson scattering" X-ray',
    '"Rayleigh scattering" X-ray',
    '"pair production" X-ray',
    '"anomalous scattering factor"',
    '"dispersive correction" X-ray',
    '"mass attenuation coefficient" X-ray',
    '"radiation damage" X-ray radiolysis',
    '"X-ray refractive index"',
    '"critical angle" X-ray',
    '"Kramers-Kronig" X-ray',
    '"coherence" "X-ray" review',
    '"Bragg\'s law" X-ray',
    '"reciprocal space" X-ray scattering',
    '"dynamical diffraction" X-ray',
    '"Pendellösung"',
    '"Debye-Waller" X-ray',
    '"X-ray standing wave"',
    '"grazing-incidence" X-ray scattering review',
    '"Thomson polarization factor"',
    '"X-ray magnetic scattering"',
    '"detective quantum efficiency" X-ray',
    '"photon counting statistics"',
    '"escape peak" X-ray',
    '"radiation protection" X-ray',
    '"ALARA" X-ray',
    '"half-value layer" X-ray',
    '"X-ray shielding"',
    '"buildup factor" X-ray',
    '"linear energy transfer" X-ray',
    '"quality factor" radiation',
    '"cosmic X-ray background"',
    '"Comptonization"',
    '"inverse Compton" accretion',
    '"thermal bremsstrahlung" cluster X-ray',
    '"Compton thick"',
    '"photoionization" "Fe K" AGN',
    '"relativistic beaming" X-ray',
    '"X-ray binary" accretion',
)

ARXIV_QUERIES = (
    'all:"bremsstrahlung" AND all:"X-ray"',
    'all:"characteristic radiation" AND all:"X-ray"',
    'all:"undulator radiation"',
    'all:"inverse Compton" AND all:"X-ray"',
    'all:"Klein-Nishina"',
    'all:"fluorescence yield"',
    'all:"photoelectric absorption" AND all:"X-ray"',
    'all:"X-ray refractive index"',
    'all:"critical angle" AND all:"X-ray"',
    'all:"Bragg\'s law" AND all:"X-ray"',
    'all:"dynamical diffraction" AND all:"X-ray"',
    'all:"Debye-Waller" AND all:"X-ray"',
    'all:"X-ray standing wave"',
    'all:"ALARA" AND all:"X-ray"',
    'all:"half-value layer" AND all:"X-ray"',
    'all:"cosmic X-ray background"',
    'all:"Comptonization"',
    'all:"core-hole lifetime"',
    'all:"anomalous scattering factor"',
    'all:"detective quantum efficiency" AND all:"X-ray"',
)

_PRINCIPLE = re.compile(
    r"bremsstrahlung|characteristic radiation|moseley|"
    r"undulator (?:radiation|parameter)|synchrotron radiation|"
    r"inverse compton|comptonization|kompaneets|"
    r"thermal bremsstrahlung|transition radiation|channeling radiation|"
    r"betatron radiation|parametric x-?ray|\bsase\b|"
    r"dipole approximation|selection rule|core-?hole lifetime|"
    r"natural linewidth|photoelectric equation|einstein photoelectric|"
    r"photoelectron wave|shake-?up|shake-?off|"
    r"fluorescence yield|coster-?kronig|"
    r"photoelectric absorption|photoelectric effect|absorption edge|"
    r"klein[-\s]?nishina|compton (?:scatter|profile|wavelength)|"
    r"thomson scatter|rayleigh scatter|pair production|"
    r"anomalous scattering factor|dispersive correction|"
    r"mass attenuation|optical depth|mean free path|"
    r"plasma opacity|resonant (?:elastic |inelastic )?x-?ray|"
    r"x-?ray refractive index|critical angle|kramers[-\s]?kronig|"
    r"x-?ray standing wave|beam hardening|"
    r"bragg(?:'s)? law|laue (?:condition|geometry)|ewald sphere|"
    r"dynamical diffraction|pendell[öo]sung|debye[-\s]?waller|"
    r"kinematical diffraction|reciprocal space|"
    r"form factor|structure factor|"
    r"detective quantum efficiency|\bdqe\b|photon counting|"
    r"escape peak|pile-?up|"
    r"\balara\b|half[-\s]?value layer|\bhvl\b|tenth[-\s]?value|"
    r"buildup factor|linear energy transfer|\blet\b|quality factor|"
    r"\bkerma\b|charged[-\s]?particle equilibrium|"
    r"cosmic x-?ray background|compton[-\s]?thick|compton[-\s]?thin|"
    r"relativistic beaming|accretion disk|"
    r"x-?ray photon|photon statistic",
    re.I,
)

_EXPLAIN = re.compile(
    r"\breview\b|primer|tutorial|textbook|we (?:review|derive|explain)|"
    r"this review|introduction to|"
    r"theoretical (?:underpin|framework|description)|"
    r"first principles|from first|derivation of|what is a",
    re.I,
)

_TECHNIQUE_PHYSICS = re.compile(
    r"\b(?:saxs|waxs|giwaxs|gisaxs|xrd|xas|xanes|exafs|rixs|rsoxs)\b.*"
    r"(q[-\s]?space|momentum transfer|form factor|bragg|critical angle|"
    r"absorption edge|interference)|"
    r"(q[-\s]?space|momentum transfer|form factor|bragg|critical angle).*"
    r"\b(?:saxs|waxs|giwaxs|gisaxs|xrd|xas|xanes|exafs|rixs|rsoxs)\b",
    re.I,
)

_DROP = re.compile(
    r"\bp3ht\b|\bpcbm\b|conjugated polymer|organic solar|"
    r"block copolymer morpholog|polymer solar cell|"
    r"ct protocol|patient position|radiograph(?:y|ic) workflow|"
    r"computed tomography (?:protocol|workflow|reconstruction)|"
    r"(?:chandra|xmm(?:-newton)?|erosita) (?:source )?catalog|"
    r"catalog of .{0,40}x-?ray (?:sources|binaries)|"
    r"powder diffraction of|crystal structure of|"
    r"11\.0\.1\.2|ophyd|process variable|\besaf\b|"
    r"beamline blueprint|motor record",
    re.I,
)

_AUGER_XRAY = re.compile(r"\bauger\b.{0,40}x-?ray|x-?ray.{0,40}\bauger\b", re.I)

_ROLE_RANK = {
    "core_principle": 4,
    "methods_physics": 3,
    "technique_downstream": 2,
    "peripheral": 0,
}

DEFAULT_MANIFEST = {
    "corpus": "xray",
    "kg_version": "v1",
    "beamline": None,
    "schema": "storage/schema/xray_schema.yaml",
    "extract_schema": "storage/schema/xray_schema_extract.yaml",
    "terms_path": "storage/terminology/extracted_terms_xray_v1.json",
    "kg_path": "storage/kg/matkg_xray_v1.json",
    "tiled_graphql": None,
    "papers": [],
}

_SCORE_KEYS = (
    "xray_tier",
    "principle_relevance",
    "review_relevance",
    "application_penalty",
    "kg_priority",
    "corpus_role",
)


def _normalize_xray_text(text: str) -> str:
    """Lowercase text and collapse unicode/hyphen variants of ``x-ray``."""
    t = (text or "").lower()
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", t)
    t = re.sub(r"x\s*-\s*ray", "x-ray", t)
    return t


def classify_xray(title: str, abstract: str = "") -> str:
    """Tier A (principle review/primer), B (methods physics), or C (reject)."""
    blob = f"{title or ''}\n{abstract or ''}"
    norm = _normalize_xray_text(blob)
    principle = bool(_PRINCIPLE.search(norm)) or bool(_AUGER_XRAY.search(norm))
    technique_physics = bool(_TECHNIQUE_PHYSICS.search(norm))
    drop = bool(_DROP.search(norm))
    explain = bool(_EXPLAIN.search(norm))
    hits = len(_PRINCIPLE.findall(norm))
    if drop and hits < 2 and not (principle and explain):
        return "C"
    if not principle and not technique_physics:
        return "C"
    if explain or (principle and re.search(r"\breview\b", title or "", re.I)):
        return "A"
    return "B"


def score_xray(title: str, abstract: str = "", extra: str = "") -> Dict[str, Any]:
    """Principle / review dimensions. Facility names are not a boost."""
    tier = classify_xray(title, abstract)
    front = _normalize_xray_text(f"{title or ''}\n{abstract or ''}\n{extra or ''}")
    title_norm = _normalize_xray_text(title or "")
    empty = {
        "xray_tier": tier,
        "principle_relevance": 0.0,
        "review_relevance": 0.0,
        "application_penalty": 0.0,
        "kg_priority": 0.0,
        "corpus_role": "peripheral",
    }
    if tier == "C":
        return empty

    principle = 0.25
    principle += min(0.45, 0.12 * len(_PRINCIPLE.findall(front)))
    if _PRINCIPLE.search(title_norm):
        principle += 0.2
    if _TECHNIQUE_PHYSICS.search(front):
        principle += 0.1

    review = 0.0
    if _EXPLAIN.search(front):
        review += 0.5
    if re.search(r"\breview\b|primer|tutorial", title_norm):
        review += 0.3

    penalty = 0.0
    if _DROP.search(front):
        penalty += 0.6

    principle_r = hr._clip01(principle)
    review_r = hr._clip01(review)
    penalty_r = hr._clip01(penalty)
    kg_priority = round(max(0.0, 0.6 * principle_r + 0.4 * review_r - 0.35 * penalty_r), 3)

    if review_r >= 0.4 and principle_r >= 0.4:
        role = "core_principle"
    elif _TECHNIQUE_PHYSICS.search(front) and principle_r < 0.5:
        role = "technique_downstream"
    elif principle_r >= 0.45:
        role = "methods_physics"
    else:
        role = "peripheral"

    return {
        "xray_tier": tier,
        "principle_relevance": principle_r,
        "review_relevance": review_r,
        "application_penalty": penalty_r,
        "kg_priority": kg_priority,
        "corpus_role": role,
    }


def _annotate_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """Attach xray score fields onto a search hit in place."""
    work.update(score_xray(str(work.get("title") or ""), str(work.get("abstract") or "")))
    return work


def _paper_sort_tuple(paper: Dict[str, Any]) -> Tuple:
    """Sort key: A-tier first, then corpus role, kg_priority, date, and title."""
    role = str(paper.get("corpus_role") or "peripheral")
    kg = float(paper.get("kg_priority") or 0)
    return (
        1 if paper.get("xray_tier") == "A" else 0,
        _ROLE_RANK.get(role, 0),
        kg,
        str(paper.get("publication_date") or ""),
        str(paper.get("title") or ""),
    )


def _load_manifest(path: Path) -> Dict[str, Any]:
    """Load papers/xray/manifest.json, filling DEFAULT_MANIFEST keys."""
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
    """Sort papers and atomically write the xray corpus manifest."""
    papers = sorted(data.get("papers") or [], key=_paper_sort_tuple, reverse=True)
    data = dict(data)
    data["papers"] = papers
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, path)


def _rsoxs_identity_keys() -> set[str]:
    """DOI / arXiv keys already in the RSoXS corpus — do not ingest twice."""
    keys: set[str] = set()
    manifest_path = RSOXS_DEST / "manifest.json"
    if not manifest_path.exists():
        return keys
    data = json.loads(manifest_path.read_text())
    for paper in data.get("papers") or []:
        doi_key = hr._doi_key(paper.get("doi"))
        if doi_key:
            keys.add(doi_key)
        arxiv_id = str(paper.get("arxiv_id") or "").strip().lower()
        if arxiv_id:
            keys.add(f"arxiv:{arxiv_id}")
        for ver in paper.get("versions") or []:
            vdoi = hr._doi_key(ver.get("doi"))
            if vdoi:
                keys.add(vdoi)
            vaid = str(ver.get("arxiv_id") or "").strip().lower()
            if vaid:
                keys.add(f"arxiv:{vaid}")
    return keys


def _work_identity_keys(work: Dict[str, Any]) -> set[str]:
    """DOI and arXiv identity keys for a search hit."""
    keys: set[str] = set()
    doi_key = hr._doi_key(work.get("doi"))
    if doi_key:
        keys.add(doi_key)
    arxiv_id = str(work.get("arxiv_id") or hr._arxiv_id_from_work(work) or "").strip().lower()
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    return keys


def in_rsoxs_corpus(work: Dict[str, Any], rsoxs_keys: Optional[set[str]] = None) -> bool:
    """True if this work's DOI or arXiv id is already in the RSoXS manifest."""
    known = rsoxs_keys if rsoxs_keys is not None else _rsoxs_identity_keys()
    return bool(_work_identity_keys(work) & known)


def merge_candidates(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe, score, and rank hits from multiple search sources."""
    seen: set[str] = set()
    seen_titles: set[str] = set()
    out: List[Dict[str, Any]] = []
    for group in groups:
        for item in group:
            key = hr._dedupe_key(item)
            title_key = hr._normalize_title(str(item.get("title") or ""))
            if key in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.add(title_key)
            out.append(_annotate_work(dict(item)))
    out = hr.collapse_near_duplicates(out)
    out.sort(key=_paper_sort_tuple, reverse=True)
    return out


def _refuse_rsoxs_dest(dest_root: Path) -> None:
    """Exit if dest_root is papers/rsoxs/."""
    if dest_root.resolve() == RSOXS_DEST.resolve():
        raise SystemExit("harvest_xray refuses to write into papers/rsoxs/")


def harvest(
    *,
    dest_root: Path,
    max_pdfs: int,
    per_query: int,
    mailto: Optional[str],
    delay: float,
) -> Dict[str, Any]:
    """Search, classify, and download OA X-ray fundamentals PDFs into dest_root."""
    _refuse_rsoxs_dest(dest_root)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    rsoxs_keys = _rsoxs_identity_keys()
    recovered = hr.retry_failed_pdfs(manifest, dest_root, delay)
    if recovered:
        LOGGER.info("Recovered %d previously failed/skipped PDFs", recovered)
        _save_manifest(manifest_path, manifest)

    arxiv_hits = hr.search_arxiv(ARXIV_QUERIES, per_query, classifier=classify_xray)
    openalex_hits = hr.search_openalex(
        OPENALEX_QUERIES,
        per_query,
        mailto,
        classifier=classify_xray,
    )
    candidates = merge_candidates(arxiv_hits, openalex_hits)
    LOGGER.info(
        "Merged %d candidates (arXiv %d, OpenAlex %d); downloading up to %d",
        len(candidates),
        len(arxiv_hits),
        len(openalex_hits),
        max_pdfs,
    )

    downloaded = 0
    skipped_existing = 0
    skipped_rsoxs = 0
    failed = 0
    pdf_n = sum(
        1
        for p in (manifest.get("papers") or [])
        if p.get("pdf_path") and (REPO_ROOT / str(p["pdf_path"])).exists()
    )
    for work in candidates:
        if in_rsoxs_corpus(work, rsoxs_keys):
            skipped_rsoxs += 1
            LOGGER.info("Skip (already in RSoXS corpus): %s", work.get("title"))
            continue
        abstract = str(work.get("abstract") or "")
        title = str(work.get("title") or "")
        meta = score_xray(title, abstract)
        if meta["xray_tier"] == "C":
            continue
        urls = hr._normalize_pdf_urls(work.get("pdf_urls") or [])
        for fallback in hr._europepmc_fallback_urls(urls):
            if fallback not in urls:
                urls.append(fallback)
        doi = str(work.get("doi") or "")
        year = hr.year_from_work(work)
        stem = hr._safe_name(work) if doi else hr.doi_stem(doi or str(work.get("id") or "work"))
        year_dir = dest_root / year
        dest = year_dir / f"{stem}.pdf"
        rel = str(dest.relative_to(REPO_ROOT))
        if rel.startswith("papers/rsoxs/"):
            raise RuntimeError("refusing to write an xray PDF under papers/rsoxs/")
        arxiv_id = hr._arxiv_id_from_work(work)
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
            LOGGER.info("Already on disk: %s", rel)
            base_row["pdf_sha256"] = hr._sha256(dest)
            base_row["downloaded_at"] = hr._now()
            hr._upsert_paper(manifest, base_row)
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
            hr._upsert_paper(manifest, base_row)
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
            hr._upsert_paper(manifest, base_row)
            _save_manifest(manifest_path, manifest)
            continue

        base_row["pdf_sha256"] = hr._sha256(dest)
        base_row["downloaded_at"] = hr._now()
        hr._upsert_paper(manifest, base_row)
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
        "skipped_rsoxs": skipped_rsoxs,
        "failed": failed,
        "manifest": str(manifest_path),
        "papers_in_manifest": len(manifest.get("papers") or []),
        "roles": rescore.get("roles"),
    }
    LOGGER.info("Done: %s", summary)
    return summary


def rescore_manifest(dest_root: Path) -> Dict[str, Any]:
    """Re-score every paper in the xray manifest using title, abstract, and PDF preview."""
    _refuse_rsoxs_dest(dest_root)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    for paper in manifest.get("papers") or []:
        extra = ""
        rel = paper.get("pdf_path")
        if rel:
            extra = hr.pdf_preview_text(REPO_ROOT / rel)
        meta = score_xray(str(paper.get("title") or ""), str(paper.get("abstract") or ""), extra)
        for key in _SCORE_KEYS:
            paper[key] = meta[key]
    manifest["papers"] = hr.collapse_near_duplicates(list(manifest.get("papers") or []))
    counts = {"A": 0, "B": 0, "C": 0}
    roles: Dict[str, int] = {}
    for paper in manifest.get("papers") or []:
        counts[paper.get("xray_tier") or "C"] = counts.get(paper.get("xray_tier") or "C", 0) + 1
        role = str(paper.get("corpus_role") or "peripheral")
        roles[role] = roles.get(role, 0) + 1
    _save_manifest(manifest_path, manifest)
    summary = {
        "manifest": str(manifest_path),
        "papers": len(manifest.get("papers") or []),
        "roles": roles,
        **counts,
    }
    LOGGER.info("Rescored: %s", summary)
    return summary


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """CLI for harvest_xray (dest, max-pdfs, rescore, and related flags)."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dest",
        type=Path,
        default=XRAY_DEST,
        help="Corpus root (year folders + manifest.json). Must not be papers/rsoxs/.",
    )
    p.add_argument(
        "--max-pdfs",
        type=int,
        default=40,
        help="Target OA PDFs (existing kept; only add). First live run: 40.",
    )
    p.add_argument("--per-query", type=int, default=25, help="Hits per query per source")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between downloads")
    p.add_argument("--mailto", default=os.environ.get("OPENALEX_EMAIL"), help="OpenAlex polite pool email")
    p.add_argument(
        "--rescore",
        action="store_true",
        help="Re-rank existing manifest and exit (no download)",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main() -> None:
    """Load env, then rescore or harvest into papers/xray/."""
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
    _refuse_rsoxs_dest(args.dest)
    if args.rescore:
        rescore_manifest(args.dest)
        return
    harvest(
        dest_root=args.dest,
        max_pdfs=args.max_pdfs,
        per_query=args.per_query,
        mailto=args.mailto,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
