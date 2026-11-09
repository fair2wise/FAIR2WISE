"""Discover literature PDFs and ops docs as two separate source lists."""
from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger(__name__)

GOLD_TITLE_NEEDLES = (
    "p3ht",
    "cyrsoxs",
    "tpd",
    "do37",
    "how to rsoxs",
    "vapor-deposited",
    "vapor deposited",
    "pffbt",
    "dopant",
)

DEFAULT_GOLD_LIMIT = 12


def repo_root() -> Path:
    """Resource root for source indexes (the repo root in a checkout; ``F2W_RESOURCE_ROOT`` when set)."""
    from ..resources import resource_root

    root = resource_root()
    return root if root is not None else Path(__file__).resolve().parents[3]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 64 KiB blocks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def work_id_for_paper(paper: dict[str, Any]) -> str:
    """Derive a stable work id from a harvest record.

    Preference order: ``doi:<doi>`` (top-level, then any version), ``arxiv:<id>``
    (version suffix dropped), then ``title:<slug>`` (``title:unknown`` if no title).
    """
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    for version in paper.get("versions") or []:
        if not isinstance(version, dict):
            continue
        ver_doi = str(version.get("doi") or "").strip().lower()
        if ver_doi:
            return f"doi:{ver_doi}"
    arxiv = str(paper.get("arxiv_id") or "").strip()
    if arxiv:
        return f"arxiv:{arxiv.split('v')[0]}"
    title = re.sub(r"[^a-z0-9]+", "-", str(paper.get("title") or "").lower()).strip("-")
    return f"title:{title or 'unknown'}"


def resolve_pdf_path(paper: dict[str, Any], root: Path) -> Path | None:
    """Return the paper's ``pdf_path`` (relative paths resolved against ``root``) if the file exists, else None."""
    rel = str(paper.get("pdf_path") or "").strip()
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    return path if path.is_file() else None


def load_harvest_papers(manifest_path: Path) -> list[dict[str, Any]]:
    """Load paper records from a harvest manifest.

    Accepts a list or a dict under ``papers`` (list or id-keyed dict). Returns an
    empty list if the file is missing or unparseable; non-dict entries are skipped.
    """
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("papers manifest unreadable or not JSON: %s", type(exc).__name__)
        return []
    papers = data.get("papers") if isinstance(data, dict) else data
    if isinstance(papers, dict):
        return [v for v in papers.values() if isinstance(v, dict)]
    if isinstance(papers, list):
        return [p for p in papers if isinstance(p, dict)]
    return []


def _gold_score(paper: dict[str, Any]) -> tuple[int, int, int, float, str]:
    title = str(paper.get("title") or "").lower()
    seed: Any = paper.get("kg_seed_rank")  # Any: raw JSON value; int() failures are handled below
    try:
        seed_i = int(seed)
    except (TypeError, ValueError):
        seed_i = 10_000
    needle_hit = any(n in title for n in GOLD_TITLE_NEEDLES)
    primary = 0 if paper.get("kg_primary") else 1
    needle = 0 if needle_hit else 1
    priority = -float(paper.get("kg_priority") or 0.0)
    return (needle, primary, seed_i, priority, title)


def select_literature_docs(
    *,
    root: Path | None = None,
    manifest_path: Path | None = None,
    mode: str = "gold",
    limit: int = DEFAULT_GOLD_LIMIT,
) -> list[dict[str, Any]]:
    """Return one PDF per canonical Work. ``gold`` is a small sample; ``all`` is every on-disk PDF."""
    base = Path(root or repo_root())
    manifest = Path(manifest_path or base / "papers/rsoxs/manifest.json")
    papers = load_harvest_papers(manifest)
    seen_works: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for paper in papers:
        pdf = resolve_pdf_path(paper, base)
        if pdf is None:
            continue
        wid = work_id_for_paper(paper)
        if wid in seen_works:
            continue
        seen_works.add(wid)
        candidates.append(
            {
                "work_id": wid,
                "doc_id": pdf.name,
                "path": pdf,
                "title": paper.get("title") or pdf.name,
                "doi": paper.get("doi") or "",
                "year": paper.get("publication_year"),
                "kg_primary": bool(paper.get("kg_primary")),
                "kg_seed_rank": paper.get("kg_seed_rank"),
                "corpus_role": paper.get("corpus_role") or "",
            }
        )
    mode_l = (mode or "gold").lower()
    if mode_l == "gold":
        def _key(doc: dict[str, Any]) -> tuple[int, int, int, float, str]:
            paperish = {
                "title": doc.get("title"),
                "kg_seed_rank": doc.get("kg_seed_rank"),
                "kg_primary": doc.get("kg_primary"),
                "kg_priority": 1.0 if doc.get("kg_primary") else 0.0,
            }
            return _gold_score(paperish)

        candidates.sort(key=_key)
        if limit > 0:
            candidates = candidates[:limit]
    else:
        candidates.sort(key=lambda d: str(d.get("title") or ""))
        if limit > 0:
            candidates = candidates[:limit]
    return candidates


def _glob_existing(root: Path, patterns: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(p for p in root.glob(pattern) if p.is_file())
    return found


def collect_ops_docs(*, root: Path | None = None) -> list[dict[str, Any]]:
    """HTML/md/README already on disk. Never mixes literature PDFs. Never reads h5."""
    base = Path(root or repo_root())
    docs: list[dict[str, Any]] = []

    als = base / ".cache/bl1101/pages/als_11-0-1-2.html"
    if als.is_file():
        docs.append(
            {
                "doc_id": "als_11-0-1-2",
                "path": als,
                "url": "https://als.lbl.gov/beamlines/11-0-1-2/",
                "title": "ALS Beamline 11.0.1.2",
                "access": "public",
            }
        )
    blueprint = base / "papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html"
    if blueprint.is_file():
        docs.append(
            {
                "doc_id": "blueprint_bl11012_version09092026",
                "path": blueprint,
                "url": "",
                "title": "Gabe blueprint BL 11.0.1.2 revision E",
                "access": "gated",
            }
        )

    try:
        from ..graph.imports.bl1101.specs import REPO_SPECS
    except Exception:
        logger.debug("bl1101 repo specs unavailable", exc_info=True)
        REPO_SPECS = ()

    skip_suffixes = {".h5", ".hdf5", ".env", ".pem", ".npy", ".npz"}
    skip_names = {".env", "credentials.json", "secrets.yaml", "id_rsa"}
    repos_dir = base / ".cache/bl1101/repos"
    readmes_dir = base / ".cache/bl1101/readmes"

    for spec in REPO_SPECS:
        repo_root_dir = repos_dir / spec.name
        patterns = tuple(spec.md_globs or ("README.md",))
        if spec.readme_only:
            continue
        if not repo_root_dir.is_dir():
            continue
        for path in _glob_existing(repo_root_dir, patterns):
            if path.suffix.lower() in skip_suffixes or path.name in skip_names:
                continue
            docs.append(
                {
                    "doc_id": f"{spec.name}:{path.relative_to(repo_root_dir).as_posix()}",
                    "path": path,
                    "url": f"{spec.url}/blob/HEAD/{path.relative_to(repo_root_dir).as_posix()}",
                    "title": f"{spec.name} {path.name}",
                    "access": "gated",
                }
            )

    if readmes_dir.is_dir():
        for path in sorted(readmes_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.upper() not in {"README", "README.MD", "README.TXT"}:
                continue
            if path.suffix.lower() in skip_suffixes or path.name in skip_names:
                continue
            rel = path.relative_to(readmes_dir).as_posix()
            docs.append(
                {
                    "doc_id": f"readme:{rel}",
                    "path": path,
                    "url": "",
                    "title": f"README {rel}",
                    "access": "gated",
                }
            )

    # De-dupe by resolved path so literature PDFs cannot sneak in.
    uniq: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in docs:
        resolved = str(Path(doc["path"]).resolve())
        if resolved in seen:
            continue
        if Path(doc["path"]).suffix.lower() == ".pdf":
            continue
        seen.add(resolved)
        uniq.append(doc)
    return uniq
