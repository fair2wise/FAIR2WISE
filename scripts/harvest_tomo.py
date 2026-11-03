#!/usr/bin/env python3
"""Harvest OA synchrotron X-ray tomography PDFs into papers/tomo/{year}/.

Technique corpus (TXM, nano-CT, holotomography, laminography, ptychographic
tomography, synchrotron µCT). Not RSoXS literature, not X-ray *fundamentals*,
and not clinical CT protocols. Never writes into papers/rsoxs/ or papers/xray/.
Dedups by DOI/arXiv against both of those manifests.

Own queries + classify_tomo(). Search/download helpers come from
harvest_rsoxs.py and download_pdfs.py.

Does not extract terms or write a KG snapshot.

Usage (from repo root):
  python3 scripts/harvest_tomo.py --max-pdfs 40
  python3 scripts/harvest_tomo.py --rescore
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

from app.modules.commands import harvest_rsoxs as hr  # noqa: E402
from app.modules.literature import download_pdfs as dl  # noqa: E402

LOGGER = logging.getLogger("harvest_tomo")

RSOXS_DEST = REPO_ROOT / "papers" / "rsoxs"
XRAY_DEST = REPO_ROOT / "papers" / "xray"
TOMO_DEST = REPO_ROOT / "papers" / "tomo"

# Quoted phrases so hyphens stay literal (arXiv Lucene).
OPENALEX_QUERIES = (
    '"synchrotron tomography"',
    '"synchrotron radiation" tomography',
    '"X-ray microtomography" synchrotron',
    '"X-ray nanotomography"',
    '"nano-CT" synchrotron',
    '"nanotomography" synchrotron',
    '"transmission X-ray microscopy" tomography',
    '"TXM" tomography synchrotron',
    '"ptychographic tomography"',
    '"holotomography" synchrotron',
    '"X-ray laminography"',
    '"phase-contrast tomography" synchrotron',
    '"X-ray tomographic microscopy"',
    '"synchrotron µCT"',
    '"synchrotron micro-CT"',
    '"synchrotron microtomography"',
    '"tomographic microscopy" synchrotron',
    '"X-ray computed tomography" synchrotron materials',
    '"hard X-ray tomography" beamline',
    '"soft X-ray tomography" synchrotron',
)

ARXIV_QUERIES = (
    'all:"synchrotron tomography"',
    'all:"X-ray nanotomography"',
    'all:"ptychographic tomography"',
    'all:"holotomography" AND all:"synchrotron"',
    'all:"X-ray laminography"',
    'all:"transmission X-ray microscopy" AND all:"tomography"',
    'all:"phase-contrast tomography" AND all:"synchrotron"',
    'all:"X-ray tomographic microscopy"',
    'all:"nano-CT" AND all:"synchrotron"',
    'all:"microtomography" AND all:"synchrotron"',
    'all:"synchrotron" AND all:"micro-CT"',
)

_TOMO = re.compile(
    r"synchrotron tomograph|"
    r"x-?ray (?:micro|nano)?tomograph|"
    r"x-?ray tomographic microscop|"
    r"tomographic microscop|"
    r"\b(?:micro|nano)[-\s]?ct\b|"
    r"nanotomograph|microtomograph|"
    r"holotomograph|laminograph|"
    r"ptychograph(?:ic)? tomograph|"
    r"\btxm\b|transmission x-?ray microscop|"
    r"phase[-\s]?contrast tomograph|"
    r"computed tomograph|"
    r"x-?ray computed tomograph|"
    r"hard x-?ray tomograph|soft x-?ray tomograph",
    re.I,
)

_SYNCH = re.compile(
    r"\bsynchrotron\b|storage ring|undulator|wiggler|"
    r"light source|\bbeamline\b|"
    r"\besrf\b|\baps\b|spring-?8|\bnsls\b|\bals\b|"
    r"diamond light|max[\s-]?iv|\bpetra\b|\bsls\b|"
    r"\bsoleil\b|\bbessy\b|\btomcat\b|"
    r"2-?bm|32-?id|\bcls\b|australian synchrotron|"
    r"taiwan photon|pal-?xfel|\bspring8\b",
    re.I,
)

_EXPLAIN = re.compile(
    r"\breview\b|primer|tutorial|we (?:review|survey)|"
    r"this review|introduction to|overview of",
    re.I,
)

_DROP = re.compile(
    r"\bp3ht\b|\bpcbm\b|conjugated polymer|organic solar|"
    r"block copolymer morpholog|polymer solar cell|"
    r"\brsoxs\b|resonant soft x-?ray scatter|"
    r"ct protocol|patient position|radiograph(?:y|ic) workflow|"
    r"computed tomography (?:protocol|workflow)|"
    r"diagnostic radiology|hospital ct|clinical ct|"
    r"cone[-\s]?beam ct of (?:the )?(?:jaw|dental|sinus)|"
    r"klein[-\s]?nishina|moseley's law|half[-\s]?value layer|"
    r"(?:chandra|xmm(?:-newton)?|erosita) (?:source )?catalog|"
    r"11\.0\.1\.2|ophyd|process variable|\besaf\b|"
    r"beamline blueprint|motor record",
    re.I,
)

_ROLE_RANK = {
    "core_method": 4,
    "beamline_tomo": 3,
    "application": 2,
    "peripheral": 0,
}

DEFAULT_MANIFEST = {
    "corpus": "tomo",
    "kg_version": "v1",
    "beamline": None,
    "schema": "storage/schema/matkg_schema.yaml",
    "extract_schema": "storage/schema/matkg_schema.yaml",
    "terms_path": "storage/terminology/extracted_terms_tomo_v1.json",
    "kg_path": "storage/kg/matkg_tomo_v1.json",
    "tiled_graphql": None,
    "papers": [],
}

_SCORE_KEYS = (
    "tomo_tier",
    "tomo_relevance",
    "synchrotron_relevance",
    "review_relevance",
    "application_penalty",
    "kg_priority",
    "corpus_role",
)


def _normalize_text(text: str) -> str:
    """Lowercase and collapse unicode dashes / x-ray / µCT spellings."""
    t = (text or "").lower()
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", t)
    t = re.sub(r"x\s*-\s*ray", "x-ray", t)
    t = t.replace("µct", "micro-ct").replace("μct", "micro-ct")
    t = re.sub(r"\bmicroct\b", "micro-ct", t)
    t = re.sub(r"\bnanoct\b", "nano-ct", t)
    return t


def classify_tomo(title: str, abstract: str = "") -> str:
    """Tier A (tomo review/primer at a light source), B (synchrotron tomo), or C."""
    blob = f"{title or ''}\n{abstract or ''}"
    norm = _normalize_text(blob)
    tomo = bool(_TOMO.search(norm))
    synch = bool(_SYNCH.search(norm))
    drop = bool(_DROP.search(norm))
    explain = bool(_EXPLAIN.search(norm))
    tomo_hits = len(_TOMO.findall(norm))
    if drop and tomo_hits < 2:
        return "C"
    if not tomo:
        return "C"
    if not synch:
        return "C"
    if explain or (tomo and re.search(r"\breview\b", title or "", re.I)):
        return "A"
    return "B"


def score_tomo(title: str, abstract: str = "", extra: str = "") -> Dict[str, Any]:
    """Synchrotron-tomography dimensions. Clinical CT and RSoXS papers score 0."""
    tier = classify_tomo(title, abstract)
    front = _normalize_text(f"{title or ''}\n{abstract or ''}\n{extra or ''}")
    title_norm = _normalize_text(title or "")
    empty = {
        "tomo_tier": tier,
        "tomo_relevance": 0.0,
        "synchrotron_relevance": 0.0,
        "review_relevance": 0.0,
        "application_penalty": 0.0,
        "kg_priority": 0.0,
        "corpus_role": "peripheral",
    }
    if tier == "C":
        return empty

    tomo_r = 0.25 + min(0.5, 0.12 * len(_TOMO.findall(front)))
    if _TOMO.search(title_norm):
        tomo_r += 0.2
    synch_r = 0.35 if _SYNCH.search(front) else 0.0
    if _SYNCH.search(title_norm):
        synch_r += 0.25

    review = 0.0
    if _EXPLAIN.search(front):
        review += 0.5
    if re.search(r"\breview\b|primer|tutorial", title_norm):
        review += 0.3

    penalty = 0.0
    if _DROP.search(front):
        penalty += 0.6

    tomo_r = hr._clip01(tomo_r)
    synch_r = hr._clip01(synch_r)
    review_r = hr._clip01(review)
    penalty_r = hr._clip01(penalty)
    kg_priority = round(
        max(0.0, 0.45 * tomo_r + 0.35 * synch_r + 0.2 * review_r - 0.35 * penalty_r),
        3,
    )

    if review_r >= 0.4 and tomo_r >= 0.4:
        role = "core_method"
    elif synch_r >= 0.5 and tomo_r >= 0.4:
        role = "beamline_tomo"
    elif tomo_r >= 0.45:
        role = "application"
    else:
        role = "peripheral"

    return {
        "tomo_tier": tier,
        "tomo_relevance": tomo_r,
        "synchrotron_relevance": synch_r,
        "review_relevance": review_r,
        "application_penalty": penalty_r,
        "kg_priority": kg_priority,
        "corpus_role": role,
    }


def _annotate_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """Attach tomo score fields onto a search hit in place."""
    work.update(score_tomo(str(work.get("title") or ""), str(work.get("abstract") or "")))
    return work


def _paper_sort_tuple(paper: Dict[str, Any]) -> Tuple:
    """Sort key: A-tier first, then corpus role, kg_priority, date, and title."""
    role = str(paper.get("corpus_role") or "peripheral")
    kg = float(paper.get("kg_priority") or 0)
    return (
        1 if paper.get("tomo_tier") == "A" else 0,
        _ROLE_RANK.get(role, 0),
        kg,
        str(paper.get("publication_date") or ""),
        str(paper.get("title") or ""),
    )


def _load_manifest(path: Path) -> Dict[str, Any]:
    """Load papers/tomo/manifest.json, filling DEFAULT_MANIFEST keys."""
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
    """Sort papers and atomically write the tomo corpus manifest."""
    papers = sorted(data.get("papers") or [], key=_paper_sort_tuple, reverse=True)
    data = dict(data)
    data["papers"] = papers
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, path)


def _manifest_identity_keys(manifest_path: Path) -> set[str]:
    """DOI / arXiv keys already in another corpus manifest."""
    keys: set[str] = set()
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


def _foreign_identity_keys() -> set[str]:
    """Union of RSoXS and X-ray-fundamentals identity keys."""
    return _manifest_identity_keys(RSOXS_DEST / "manifest.json") | _manifest_identity_keys(
        XRAY_DEST / "manifest.json"
    )


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


def in_other_corpus(work: Dict[str, Any], known: Optional[set[str]] = None) -> bool:
    """True if this work's DOI or arXiv id is already in RSoXS or X-ray fundamentals."""
    keys = known if known is not None else _foreign_identity_keys()
    return bool(_work_identity_keys(work) & keys)


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


def _refuse_foreign_dest(dest_root: Path) -> None:
    """Exit if dest_root is papers/rsoxs/ or papers/xray/."""
    resolved = dest_root.resolve()
    if resolved == RSOXS_DEST.resolve():
        raise SystemExit("harvest_tomo refuses to write into papers/rsoxs/")
    if resolved == XRAY_DEST.resolve():
        raise SystemExit("harvest_tomo refuses to write into papers/xray/")


def harvest(
    *,
    dest_root: Path,
    max_pdfs: int,
    per_query: int,
    mailto: Optional[str],
    delay: float,
) -> Dict[str, Any]:
    """Search, classify, and download OA synchrotron tomography PDFs into dest_root."""
    _refuse_foreign_dest(dest_root)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    foreign_keys = _foreign_identity_keys()
    recovered = hr.retry_failed_pdfs(manifest, dest_root, delay)
    if recovered:
        LOGGER.info("Recovered %d previously failed/skipped PDFs", recovered)
        _save_manifest(manifest_path, manifest)

    arxiv_hits = hr.search_arxiv(ARXIV_QUERIES, per_query, classifier=classify_tomo)
    openalex_hits = hr.search_openalex(
        OPENALEX_QUERIES,
        per_query,
        mailto,
        classifier=classify_tomo,
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
    skipped_foreign = 0
    failed = 0
    pdf_n = sum(
        1
        for p in (manifest.get("papers") or [])
        if p.get("pdf_path") and (REPO_ROOT / str(p["pdf_path"])).exists()
    )
    for work in candidates:
        if in_other_corpus(work, foreign_keys):
            skipped_foreign += 1
            LOGGER.info("Skip (already in RSoXS or X-ray fundamentals): %s", work.get("title"))
            continue
        abstract = str(work.get("abstract") or "")
        title = str(work.get("title") or "")
        meta = score_tomo(title, abstract)
        if meta["tomo_tier"] == "C":
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
        if rel.startswith("papers/rsoxs/") or rel.startswith("papers/xray/"):
            raise RuntimeError("refusing to write a tomo PDF under papers/rsoxs/ or papers/xray/")
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
            ok = hr.download_pdf(url, str(dest))
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
        "skipped_foreign": skipped_foreign,
        "failed": failed,
        "manifest": str(manifest_path),
        "papers_in_manifest": len(manifest.get("papers") or []),
        "roles": rescore.get("roles"),
    }
    LOGGER.info("Done: %s", summary)
    return summary


def rescore_manifest(dest_root: Path) -> Dict[str, Any]:
    """Re-score every paper in the tomo manifest using title, abstract, and PDF preview."""
    _refuse_foreign_dest(dest_root)
    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    for paper in manifest.get("papers") or []:
        extra = ""
        rel = paper.get("pdf_path")
        if rel:
            extra = hr.pdf_preview_text(REPO_ROOT / rel)
        meta = score_tomo(str(paper.get("title") or ""), str(paper.get("abstract") or ""), extra)
        for key in _SCORE_KEYS:
            paper[key] = meta[key]
    manifest["papers"] = hr.collapse_near_duplicates(list(manifest.get("papers") or []))
    counts = {"A": 0, "B": 0, "C": 0}
    roles: Dict[str, int] = {}
    for paper in manifest.get("papers") or []:
        counts[paper.get("tomo_tier") or "C"] = counts.get(paper.get("tomo_tier") or "C", 0) + 1
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
    """CLI for harvest_tomo (dest, max-pdfs, rescore, and related flags)."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dest",
        type=Path,
        default=TOMO_DEST,
        help="Corpus root (year folders + manifest.json). Must not be papers/rsoxs/ or papers/xray/.",
    )
    p.add_argument(
        "--max-pdfs",
        type=int,
        default=40,
        help="Target OA PDFs (existing kept; only add).",
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
    """Load env, then rescore or harvest into papers/tomo/."""
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
    _refuse_foreign_dest(args.dest)
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
