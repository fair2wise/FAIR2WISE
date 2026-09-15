#!/usr/bin/env python3
"""Match skipped RSoXS harvest Works to ALS zip PDFs and copy hits into the repo.

Zip members are slash-stripped DOIs (``ALS/10.1103PhysRevB.69.115425.pdf``).
Harvest dest names use underscores (``10.1103_PhysRevB.69.115425.pdf``).
Matching is exact after DOI compact-key normalization — no fuzzy titles.

Does not start term extract, bind 5174, or stop a live harvest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

PDF_MAGIC = b"%PDF"
_DOI_URL = re.compile(r"^https?://(dx\.)?doi\.org/", re.I)


def bare_doi(doi: str) -> str:
    """Strip a doi.org prefix; keep internal slashes."""
    return _DOI_URL.sub("", (doi or "").strip())


def doi_compact_key(doi: Optional[str]) -> str:
    """Lowercase DOI with slashes removed — matches ALS zip stems."""
    raw = bare_doi(doi or "")
    if not raw:
        return ""
    return raw.replace("/", "").lower()


def zip_member_compact_key(member: str) -> str:
    """Basename of a zip member, without ``.pdf``, lowercased."""
    name = Path(member).name
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name.lower()


def harvest_pdf_stem(doi: Optional[str]) -> str:
    """Filename-safe DOI used by harvest (``/`` → ``_``)."""
    raw = bare_doi(doi or "")
    return raw.replace("/", "_") or "unknown"


def keys_match(doi: Optional[str], zip_member: str) -> bool:
    key = doi_compact_key(doi)
    return bool(key) and key == zip_member_compact_key(zip_member)


def is_pdf_bytes(header: bytes) -> bool:
    return header.startswith(PDF_MAGIC)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_pdf_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    with path.open("rb") as fh:
        return is_pdf_bytes(fh.read(5))


def skipped_included(papers: Iterable[Dict[str, Any]], repo_root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for paper in papers:
        if not (paper.get("selection") or {}).get("included"):
            continue
        rel = paper.get("pdf_path")
        if rel:
            dest = repo_root / str(rel)
            if dest.exists() and dest.stat().st_size > 0:
                continue
        out.append(paper)
    return out


def index_als_zips(zip_paths: Iterable[Path]) -> Dict[str, Tuple[str, str, Path]]:
    """compact_key → (zip filename, member name, zip path). First hit wins."""
    index: Dict[str, Tuple[str, str, Path]] = {}
    for zpath in zip_paths:
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                key = zip_member_compact_key(member)
                if not key or key in index:
                    continue
                index[key] = (zpath.name, member, zpath)
    return index


def match_skipped(
    skipped: List[Dict[str, Any]],
    zip_index: Dict[str, Tuple[str, str, Path]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    matches: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for paper in skipped:
        doi = paper.get("doi")
        key = doi_compact_key(doi)
        hit = zip_index.get(key) if key else None
        if not hit:
            unmatched.append(
                {
                    "doi": doi,
                    "arxiv_id": paper.get("arxiv_id"),
                    "title": paper.get("title"),
                    "publication_year": paper.get("publication_year"),
                    "ingestion_status": (paper.get("ingestion") or {}).get("status"),
                }
            )
            continue
        zip_name, member, zpath = hit
        matches.append(
            {
                "doi": doi,
                "title": paper.get("title"),
                "publication_year": paper.get("publication_year"),
                "zip": zip_name,
                "member": member,
                "zip_path": str(zpath),
                "harvest_stem": harvest_pdf_stem(doi),
            }
        )
    return matches, unmatched


def dest_for_paper(paper: Dict[str, Any], dest_root: Path) -> Path:
    year = str(paper.get("publication_year") or "").strip()
    if not year.isdigit():
        date = str(paper.get("publication_date") or "")[:4]
        year = date if date.isdigit() else "unknown"
    return dest_root / year / f"{harvest_pdf_stem(paper.get('doi'))}.pdf"


def extract_pdf_from_zip(zf: zipfile.ZipFile, member: str, dest: Path) -> str:
    """Copy one open-zip member to dest. Returns 'copied', 'exists', or 'not_pdf'."""
    if existing_pdf_ok(dest):
        return "exists"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".als.part")
    try:
        with zf.open(member) as src, tmp.open("wb") as out:
            header = src.read(8)
            if not is_pdf_bytes(header):
                return "not_pdf"
            out.write(header)
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, dest)
        return "copied"
    finally:
        if tmp.exists():
            tmp.unlink()


def extract_pdf_member(zpath: Path, member: str, dest: Path) -> str:
    """Copy one zip member to dest. Returns 'copied', 'exists', or 'not_pdf'."""
    if existing_pdf_ok(dest):
        return "exists"
    with zipfile.ZipFile(zpath) as zf:
        return extract_pdf_from_zip(zf, member, dest)


def als_provenance(zip_name: str, member: str) -> Dict[str, Any]:
    return {
        "source": "als_zip",
        "zip": zip_name,
        "member": member,
        "copied_at": _now(),
    }


def apply_match_to_paper(paper: Dict[str, Any], dest: Path, zip_name: str, member: str) -> bool:
    """Update one manifest row the way harvest does when a PDF lands. False if already on disk."""
    rel_existing = paper.get("pdf_path")
    if rel_existing:
        existing = REPO_ROOT / str(rel_existing)
        if existing_pdf_ok(existing):
            return False
    if not existing_pdf_ok(dest):
        return False
    paper["pdf_path"] = str(dest.relative_to(REPO_ROOT))
    paper["pdf_sha256"] = _sha256(dest)
    paper["downloaded_at"] = _now()
    paper["ingestion"] = {
        **(paper.get("ingestion") or {}),
        "status": "pending",
        "error": None,
    }
    stamp = als_provenance(zip_name, member)
    provenance = list(paper.get("provenance") or [])
    if stamp not in provenance:
        # Identity without timestamp so re-merge does not stack stamps.
        already = any(
            isinstance(item, dict)
            and item.get("source") == "als_zip"
            and item.get("zip") == zip_name
            and item.get("member") == member
            for item in provenance
        )
        if not already:
            provenance.append(stamp)
    paper["provenance"] = provenance
    return True


def wait_for_manifest_idle(manifest_path: Path, timeout: float = 15.0) -> None:
    tmp = manifest_path.with_suffix(".json.tmp")
    deadline = time.time() + timeout
    while tmp.exists() and time.time() < deadline:
        time.sleep(0.2)


def merge_sidecar_into_manifest(
    manifest_path: Path,
    dest_root: Path,
    copied: List[Dict[str, Any]],
) -> int:
    """Read-modify-write only rows that still lack a PDF on disk.

    Retries if harvest rewrites the manifest between load and save.
    """
    from harvest_rsoxs import _load_manifest, _save_manifest

    updated = 0
    for _ in range(6):
        wait_for_manifest_idle(manifest_path)
        mtime = manifest_path.stat().st_mtime if manifest_path.exists() else 0
        manifest = _load_manifest(manifest_path)
        by_key: Dict[str, Dict[str, Any]] = {}
        for paper in manifest.get("papers") or []:
            key = doi_compact_key(paper.get("doi"))
            if key:
                by_key[key] = paper
        updated = 0
        for row in copied:
            paper = by_key.get(doi_compact_key(row.get("doi")))
            if paper is None:
                continue
            dest = Path(row["dest"])
            if not dest.is_absolute():
                dest = REPO_ROOT / dest
            if apply_match_to_paper(paper, dest, row["zip"], row["member"]):
                updated += 1
        wait_for_manifest_idle(manifest_path)
        if manifest_path.exists() and manifest_path.stat().st_mtime != mtime:
            continue
        if updated:
            _save_manifest(manifest_path, manifest)
        return updated
    return updated


def zip_inventory(zip_paths: List[Path]) -> List[Dict[str, Any]]:
    rows = []
    for zpath in zip_paths:
        with zipfile.ZipFile(zpath) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
        rows.append(
            {
                "name": zpath.name,
                "path": str(zpath),
                "bytes": zpath.stat().st_size,
                "members": len(members),
            }
        )
    return rows


def run(
    *,
    zip_dir: Path,
    dest_root: Path,
    sidecar_path: Path,
    copy: bool,
    merge: bool,
) -> Dict[str, Any]:
    zip_paths = sorted(zip_dir.glob("ALS-*.zip"))
    inventory = zip_inventory(zip_paths)
    zip_index = index_als_zips(zip_paths)

    from harvest_rsoxs import _load_manifest

    manifest_path = dest_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    skipped = skipped_included(manifest.get("papers") or [], REPO_ROOT)
    matches, unmatched = match_skipped(skipped, zip_index)

    copied: List[Dict[str, Any]] = []
    existed: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    if copy:
        papers_by_key = {
            doi_compact_key(p.get("doi")): p
            for p in skipped
            if doi_compact_key(p.get("doi"))
        }
        by_zip: Dict[str, List[Dict[str, Any]]] = {}
        for match in matches:
            by_zip.setdefault(match["zip_path"], []).append(match)
        for zip_path, group in by_zip.items():
            with zipfile.ZipFile(zip_path) as zf:
                for match in group:
                    paper = papers_by_key.get(doi_compact_key(match["doi"]))
                    if paper is None:
                        continue
                    dest = dest_for_paper(paper, dest_root)
                    rel = str(dest.relative_to(REPO_ROOT))
                    status = extract_pdf_from_zip(zf, match["member"], dest)
                    record = {
                        **{k: match[k] for k in ("doi", "title", "publication_year", "zip", "member", "harvest_stem")},
                        "dest": rel,
                        "status": status,
                    }
                    if status == "copied":
                        record["sha256"] = _sha256(dest)
                        copied.append(record)
                    elif status == "exists":
                        existed.append(record)
                    else:
                        rejected.append(record)

    sidecar = {
        "created_at": _now(),
        "zip_files": inventory,
        "zip_members_total": sum(row["members"] for row in inventory),
        "zip_index_size": len(zip_index),
        "skipped_at_start": len(skipped),
        "matches_found": len(matches),
        "copied": copied,
        "already_on_disk": existed,
        "rejected_not_pdf": rejected,
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
    }
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")

    merged = 0
    if merge:
        merge_rows = copied + [
            {**row, "dest": row["dest"]}
            for row in existed
        ]
        # Also include dests we just verified exist.
        merged = merge_sidecar_into_manifest(manifest_path, dest_root, merge_rows)

    sidecar["manifest_rows_updated"] = merged
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    return sidecar


def merge_from_sidecar_file(sidecar_path: Path, dest_root: Path) -> Dict[str, Any]:
    """Re-apply sidecar dests onto manifest without re-copying or rewriting matches."""
    sidecar = json.loads(sidecar_path.read_text())
    merge_rows = list(sidecar.get("copied") or []) + list(sidecar.get("already_on_disk") or [])
    updated = merge_sidecar_into_manifest(dest_root / "manifest.json", dest_root, merge_rows)
    sidecar["manifest_rows_updated"] = updated
    sidecar["manifest_merged_at"] = _now()
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    return sidecar


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch_harvest_and_merge(sidecar_path: Path, dest_root: Path, pid: int) -> Dict[str, Any]:
    """Re-merge sidecar after each harvest manifest save until *pid* exits."""
    manifest_path = dest_root / "manifest.json"
    last = manifest_path.stat().st_mtime if manifest_path.exists() else 0
    summary = merge_from_sidecar_file(sidecar_path, dest_root)
    last = manifest_path.stat().st_mtime
    while pid_alive(pid):
        time.sleep(1.0)
        mtime = manifest_path.stat().st_mtime if manifest_path.exists() else 0
        if mtime != last:
            time.sleep(0.3)
            summary = merge_from_sidecar_file(sidecar_path, dest_root)
            last = manifest_path.stat().st_mtime
            print(
                json.dumps(
                    {
                        "event": "remerged_after_harvest_save",
                        "manifest_rows_updated": summary.get("manifest_rows_updated"),
                        "at": _now(),
                    }
                ),
                flush=True,
            )
    summary = merge_from_sidecar_file(sidecar_path, dest_root)
    print(
        json.dumps(
            {
                "event": "harvest_exited_final_merge",
                "pid": pid,
                "manifest_rows_updated": summary.get("manifest_rows_updated"),
                "at": _now(),
            }
        ),
        flush=True,
    )
    return summary


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--zip-dir",
        type=Path,
        default=Path("/Users/david/Documents/data/fair2wise"),
    )
    p.add_argument(
        "--dest",
        type=Path,
        default=REPO_ROOT / "papers" / "rsoxs",
    )
    p.add_argument(
        "--sidecar",
        type=Path,
        default=REPO_ROOT / "logs" / "als_zip_matches.json",
    )
    p.add_argument("--copy", action="store_true", help="Extract matching PDFs into papers/rsoxs/")
    p.add_argument("--merge", action="store_true", help="Update manifest.json for copied rows")
    p.add_argument(
        "--watch-pid",
        type=int,
        default=None,
        help="With --merge, re-apply sidecar after each harvest save until this PID exits",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.watch_pid and args.merge:
        summary = watch_harvest_and_merge(args.sidecar, args.dest, args.watch_pid)
    elif args.merge and not args.copy:
        summary = merge_from_sidecar_file(args.sidecar, args.dest)
    else:
        summary = run(
            zip_dir=args.zip_dir,
            dest_root=args.dest,
            sidecar_path=args.sidecar,
            copy=args.copy,
            merge=args.merge,
        )
    print(
        json.dumps(
            {
                "zip_files": len(summary.get("zip_files") or []),
                "zip_members_total": summary.get("zip_members_total"),
                "skipped_at_start": summary.get("skipped_at_start"),
                "matches_found": summary.get("matches_found"),
                "copied": len(summary.get("copied") or []),
                "already_on_disk": len(summary.get("already_on_disk") or []),
                "rejected_not_pdf": len(summary.get("rejected_not_pdf") or []),
                "unmatched_count": summary.get("unmatched_count"),
                "manifest_rows_updated": summary.get("manifest_rows_updated"),
                "sidecar": str(args.sidecar),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
