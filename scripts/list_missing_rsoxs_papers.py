#!/usr/bin/env python3
"""List PubTemp publications that are in the harvest manifest but have no PDF.

Citations are copied from the ALS/PubTemp RTF after stripping RTF markup.
Nothing is invented: a line is written only when the RTF record matches an
included manifest Work and that Work has no PDF on disk.

Publication kinds: journal articles, refereed conference proceedings, theses,
and patents. Awards, invited lectures, SPIE "Conference Presentation" talks,
and other non-refereed covers/talks are counted but not written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from harvest_rsoxs import (  # noqa: E402
    PUBTEMP_DEFAULT,
    REPO_ROOT as HARVEST_ROOT,
    _doi_key,
    _first_author_key,
    _load_manifest,
    _normalize_title,
    _pdf_on_disk,
    parse_pubtemp_rtf,
)

PUBLICATION_KINDS = frozenset({"journal", "conference", "thesis", "patent"})
EXCLUDED_KINDS = frozenset({"award", "invited_lecture", "non_refereed", "unknown"})
TALK_TITLE_RE = re.compile(
    r"\(conference presentation\)|invited lecture",
    re.I,
)

DEFAULT_OUT = REPO_ROOT / "missing_rsoxs_papers.txt"
DEFAULT_AUDIT = REPO_ROOT / "logs" / "missing_rsoxs_papers_audit.json"


def _is_talk_title(record: Dict[str, Any]) -> bool:
    blob = f"{record.get('title') or ''} {record.get('citation') or ''}"
    return bool(TALK_TITLE_RE.search(blob))


def _is_publication(record: Dict[str, Any]) -> bool:
    if record.get("pubtemp_kind") not in PUBLICATION_KINDS:
        return False
    return not _is_talk_title(record)


def index_included_papers(
    papers: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    by_doi: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for paper in papers:
        if not (paper.get("selection") or {}).get("included"):
            continue
        doi_key = _doi_key(paper.get("doi"))
        if doi_key and doi_key not in by_doi:
            by_doi[doi_key] = paper
        title_key = _normalize_title(str(paper.get("title") or ""))
        if title_key:
            by_title[title_key].append(paper)
    return by_doi, by_title


def match_rtf_to_included(
    record: Dict[str, Any],
    by_doi: Dict[str, Dict[str, Any]],
    by_title: Dict[str, List[Dict[str, Any]]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Match one RTF record to an included manifest Work. DOI first, then title."""
    doi_key = _doi_key(record.get("doi"))
    if doi_key and doi_key in by_doi:
        return by_doi[doi_key], "doi"
    title_key = _normalize_title(str(record.get("title") or ""))
    hits = list(by_title.get(title_key) or [])
    if not hits:
        return None, None
    if len(hits) == 1:
        return hits[0], "title"
    author = _first_author_key(record)
    authored = [h for h in hits if _first_author_key(h) == author] if author else []
    if len(authored) == 1:
        return authored[0], "title+author"
    return hits[0], "title-ambiguous"


def collect_missing_citations(
    records: List[Dict[str, Any]],
    papers: List[Dict[str, Any]],
    pdf_on_disk: Callable[[Dict[str, Any]], bool] = _pdf_on_disk,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return missing publication rows plus an audit dict. RTF-first."""
    by_doi, by_title = index_included_papers(papers)
    kind_counts = Counter(r.get("pubtemp_kind") for r in records)
    section_counts = Counter(r.get("pubtemp_section") for r in records)

    written: List[Dict[str, Any]] = []
    excluded_awards = 0
    excluded_invited = 0
    excluded_talks = 0
    excluded_non_refereed = 0
    excluded_other = 0
    rtf_pubs = 0
    rtf_pubs_missing_pdf = 0
    rtf_journal_conference = 0
    rtf_journal_conference_missing_pdf = 0
    rtf_journal_conference_not_in_manifest = 0
    rtf_pubs_not_in_manifest = 0
    match_how: Counter[str] = Counter()
    skipped_has_pdf = 0

    for record in records:
        kind = record.get("pubtemp_kind")
        is_jc = kind in {"journal", "conference"}
        if is_jc:
            rtf_journal_conference += 1
        publication = _is_publication(record)
        if publication:
            rtf_pubs += 1

        paper, how = match_rtf_to_included(record, by_doi, by_title)
        in_manifest = paper is not None
        missing_pdf = bool(paper) and not pdf_on_disk(paper)

        if kind == "award":
            excluded_awards += 1
        elif kind == "invited_lecture":
            excluded_invited += 1
        elif _is_talk_title(record):
            excluded_talks += 1
        elif kind == "non_refereed":
            excluded_non_refereed += 1
        elif kind in EXCLUDED_KINDS:
            excluded_other += 1

        if is_jc and missing_pdf:
            rtf_journal_conference_missing_pdf += 1
        if is_jc and not in_manifest:
            rtf_journal_conference_not_in_manifest += 1
        if publication and not in_manifest:
            rtf_pubs_not_in_manifest += 1
        if publication and missing_pdf:
            rtf_pubs_missing_pdf += 1

        if not publication:
            continue
        if not in_manifest:
            continue
        if not missing_pdf:
            skipped_has_pdf += 1
            continue

        citation = (record.get("citation") or "").strip()
        if not citation:
            continue
        match_how[how or "unknown"] += 1
        written.append(
            {
                "citation": citation,
                "title": record.get("title"),
                "doi": record.get("doi"),
                "kind": kind,
                "section": record.get("pubtemp_section"),
                "match": how,
            }
        )

    audit = {
        "rtf_records": len(records),
        "rtf_kind_counts": dict(kind_counts),
        "rtf_section_counts": dict(section_counts),
        "rtf_publication_kinds": rtf_pubs,
        "rtf_publication_kinds_missing_pdf": rtf_pubs_missing_pdf,
        "rtf_journal_conference": rtf_journal_conference,
        "rtf_journal_conference_missing_pdf": rtf_journal_conference_missing_pdf,
        "rtf_journal_conference_not_in_manifest": rtf_journal_conference_not_in_manifest,
        "rtf_pubs_not_in_manifest": rtf_pubs_not_in_manifest,
        "excluded_awards": excluded_awards,
        "excluded_invited_lectures": excluded_invited,
        "excluded_conference_presentation_talks": excluded_talks,
        "excluded_non_refereed": excluded_non_refereed,
        "excluded_other": excluded_other,
        "skipped_has_pdf": skipped_has_pdf,
        "written_lines": len(written),
        "written_kind_counts": dict(Counter(row["kind"] for row in written)),
        "match_how": dict(match_how),
        "manifest_papers": len(papers),
        "manifest_included": sum(
            1 for p in papers if (p.get("selection") or {}).get("included")
        ),
    }
    return written, audit


def _rtf_doi_keys(records: Iterable[Dict[str, Any]]) -> set[str]:
    return {k for k in (_doi_key(r.get("doi")) for r in records) if k}


def _rtf_title_keys(records: Iterable[Dict[str, Any]]) -> set[str]:
    return {k for k in (_normalize_title(str(r.get("title") or "")) for r in records) if k}


def _rtf_citations(records: Iterable[Dict[str, Any]]) -> set[str]:
    return {str(r.get("citation") or "").strip() for r in records if r.get("citation")}


def verify_written_against_rtf(
    written: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    raw_rtf: Optional[str] = None,
    papers: Optional[List[Dict[str, Any]]] = None,
    pdf_on_disk: Callable[[Dict[str, Any]], bool] = _pdf_on_disk,
) -> List[str]:
    """Return a list of assertion failures. Empty means the list is clean."""
    errors: List[str] = []
    doi_keys = _rtf_doi_keys(records)
    title_keys = _rtf_title_keys(records)
    citations = _rtf_citations(records)
    pub_records = [r for r in records if _is_publication(r)]
    pub_citations = _rtf_citations(pub_records)

    for i, row in enumerate(written, start=1):
        citation = row["citation"]
        if citation not in citations:
            errors.append(f"line {i}: citation not in RTF parse set")
        if citation not in pub_citations:
            errors.append(f"line {i}: citation is not a publication-kind RTF record")
        doi_key = _doi_key(row.get("doi"))
        title_key = _normalize_title(str(row.get("title") or ""))
        if doi_key:
            if doi_key not in doi_keys:
                errors.append(f"line {i}: DOI {doi_key} not in RTF")
            if raw_rtf is not None:
                bare = doi_key.split("doi.org/", 1)[-1]
                if bare.lower() not in raw_rtf.lower():
                    errors.append(f"line {i}: DOI {bare} not in raw RTF text")
        elif title_key:
            if title_key not in title_keys:
                errors.append(f"line {i}: title not in RTF")
        else:
            errors.append(f"line {i}: no DOI and no title")
        kind = row.get("kind")
        if kind not in PUBLICATION_KINDS:
            errors.append(f"line {i}: kind {kind!r} is not a publication")
        if _is_talk_title(row):
            errors.append(f"line {i}: talk/presentation slipped through")
        if kind in {"award", "invited_lecture"}:
            errors.append(f"line {i}: award/talk kind slipped through")

    if papers is not None:
        by_doi, by_title = index_included_papers(papers)
        written_citations = {row["citation"] for row in written}
        for rec in records:
            if rec.get("pubtemp_kind") not in {"journal", "conference"}:
                continue
            paper, _how = match_rtf_to_included(rec, by_doi, by_title)
            if paper is None or pdf_on_disk(paper):
                continue
            cite = (rec.get("citation") or "").strip()
            if cite and cite not in written_citations:
                errors.append(
                    "RTF journal/conference without PDF missing from output: "
                    + str(rec.get("title") or cite)[:120]
                )
    return errors


def format_output(written: List[Dict[str, Any]], audit: Dict[str, Any]) -> str:
    header = [
        "# Missing RSoXS harvest PDFs. Each line is copied from the PubTemp RTF",
        "# papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf",
        "# (RTF markup stripped; author/title/journal/year kept as in the export).",
        (
            "# rtf_records={rtf_records} rtf_pubs={rtf_publication_kinds} "
            "missing_pdfs={rtf_publication_kinds_missing_pdf} written={written_lines} "
            "excluded_awards={excluded_awards} excluded_invited_lectures={excluded_invited_lectures} "
            "excluded_conference_presentation_talks={excluded_conference_presentation_talks} "
            "rtf_journal_conference_not_in_manifest={rtf_journal_conference_not_in_manifest}"
        ).format(**audit),
        "# OpenAlex-only harvest rows are omitted unless they also appear in the RTF.",
    ]
    lines = header + [row["citation"] for row in written]
    return "\n".join(lines) + "\n"


def write_outputs(
    written: List[Dict[str, Any]],
    audit: Dict[str, Any],
    out_path: Path,
    audit_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_output(written, audit), encoding="utf-8")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(audit)
    payload["output"] = str(out_path)
    payload["matching"] = (
        "RTF-first: included manifest Works with no PDF on disk, matched by "
        "normalized DOI then exact normalized title (first-author tie-break). "
        "Citation text is the unescaped PubTemp record. No OpenAlex-only rows."
    )
    audit_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtf", type=Path, default=PUBTEMP_DEFAULT)
    parser.add_argument("--manifest", type=Path, default=HARVEST_ROOT / "papers" / "rsoxs" / "manifest.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    records = parse_pubtemp_rtf(args.rtf)
    raw_rtf = args.rtf.read_text(encoding="latin-1")
    papers = _load_manifest(args.manifest).get("papers") or []
    written, audit = collect_missing_citations(records, papers)
    errors = verify_written_against_rtf(
        written, records, raw_rtf=raw_rtf, papers=papers
    )
    if errors:
        raise SystemExit("verify failed:\n" + "\n".join(errors[:20]))
    write_outputs(written, audit, args.out, args.audit)
    print(json.dumps({k: audit[k] for k in (
        "written_lines",
        "rtf_publication_kinds",
        "rtf_publication_kinds_missing_pdf",
        "excluded_awards",
        "excluded_invited_lectures",
        "excluded_conference_presentation_talks",
        "rtf_journal_conference_not_in_manifest",
        "written_kind_counts",
        "match_how",
    )}, sort_keys=True))
    print(f"wrote {args.out}")
    print(f"audit {args.audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
