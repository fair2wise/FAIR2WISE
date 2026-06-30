"""GitHub source repository helpers for code snippet extraction.

The extractor keeps papers as the source of truth: repositories are considered
only when a GitHub URL appears in the PDF text. Source files are fetched through
GitHub's HTTP APIs, parsed locally, and converted into the same ``code_snippets``
records used by the existing PDF-embedded code path.
"""
from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
MAX_SOURCE_FILE_BYTES = 250_000
MAX_SOURCE_FILES_PER_REPO = 30
MAX_SNIPPETS_PER_REPO = 12
MAX_BLOCK_LINES = 260
MIN_BLOCK_CHARS = 80

_GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?P<tail>(?:/[^\s<>\]\[)('\"},]+)?)",
    re.IGNORECASE,
)
_IGNORED_REPO_PATHS = {"issues", "pull", "pulls", "releases", "wiki", "discussions"}
_SKIP_PATH_PARTS = {
    ".github",
    "benchmarks",
    "build",
    "dist",
    "doc",
    "docs",
    "examples",
    "tests",
    "test",
}
_PY_EXTENSIONS = {".py"}
_STOPWORDS = {
    "about", "after", "also", "analysis", "and", "are", "based", "between",
    "code", "data", "does", "from", "github", "have", "into", "paper",
    "python", "repo", "repository", "results", "source", "that", "this",
    "using", "with", "were", "which",
}


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def api_url(self) -> str:
        return f"{GITHUB_API}/repos/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class SourceFile:
    path: str
    raw_url: str
    html_url: str
    size: int


@dataclass(frozen=True)
class SourceBlock:
    name: str
    kind: str
    source: str
    start_line: int
    end_line: int


def extract_github_repositories(text: str) -> List[GitHubRepoRef]:
    """Return unique GitHub repositories explicitly linked in text."""
    refs: List[GitHubRepoRef] = []
    seen: set[tuple[str, str]] = set()
    for match in _GITHUB_RE.finditer(text or ""):
        owner = match.group("owner").strip().rstrip(".,;:")
        repo = match.group("repo").strip().rstrip(".,;:").removesuffix(".git")
        tail = (match.group("tail") or "").strip("/")
        first_path_part = tail.split("/", 1)[0].lower() if tail else ""
        if not owner or not repo or first_path_part in _IGNORED_REPO_PATHS:
            continue
        key = (owner.lower(), repo.lower())
        if key in seen:
            continue
        seen.add(key)
        refs.append(GitHubRepoRef(owner=owner, repo=repo))
    return refs


def _tokens(text: str) -> set[str]:
    toks = {
        t.lower()
        for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or "")
        if t.lower() not in _STOPWORDS
    }
    return toks


def extract_identifier_hints(text: str, *, limit: int = 80) -> List[str]:
    """Find function-like identifiers and dotted API names mentioned in text."""
    candidates: List[str] = []
    seen: set[str] = set()
    patterns = [
        r"`([A-Za-z_][A-Za-z0-9_\.]{2,})`",
        r"\b([A-Za-z_][A-Za-z0-9_\.]{2,})\s*\(",
    ]
    for pat in patterns:
        for raw in re.findall(pat, text or ""):
            name = raw.rsplit(".", 1)[-1]
            key = name.lower()
            if key in seen or key in _STOPWORDS:
                continue
            seen.add(key)
            candidates.append(name)
            if len(candidates) >= limit:
                return candidates
    return candidates


def _path_supported(path: str) -> bool:
    return any(path.endswith(ext) for ext in _PY_EXTENSIONS)


def _path_is_noise(path: str) -> bool:
    parts = {p.lower() for p in path.split("/")}
    return bool(parts & _SKIP_PATH_PARTS)


def _path_score(path: str, hints: Sequence[str], paper_tokens: set[str]) -> float:
    low = path.lower()
    score = 0.0
    for hint in hints:
        h = hint.lower()
        if h and h in low:
            score += 5.0
        for part in h.split("_"):
            if len(part) >= 4 and part in low:
                score += 1.0
    path_tokens = _tokens(path.replace("/", " ").replace("_", " "))
    score += min(8, len(path_tokens & paper_tokens)) * 0.5
    if _path_is_noise(path):
        score -= 3.0
    return score


def extract_python_blocks(source: str) -> List[SourceBlock]:
    """Extract exact function/class source blocks from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    blocks: List[SourceBlock] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end = getattr(node, "end_lineno", None)
        if not node.lineno or not end:
            continue
        start = node.lineno
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            start = min(getattr(dec, "lineno", start) for dec in decorators)
        if end - start + 1 > MAX_BLOCK_LINES:
            continue
        block = "\n".join(lines[start - 1:end]).rstrip()
        if len(block) < MIN_BLOCK_CHARS:
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        blocks.append(SourceBlock(node.name, kind, block, start, end))
    return blocks


def _block_score(block: SourceBlock, path: str, hints: Sequence[str], paper_tokens: set[str]) -> float:
    score = _path_score(path, hints, paper_tokens)
    low_name = block.name.lower()
    low_source = block.source.lower()
    for hint in hints:
        h = hint.lower()
        if h == low_name:
            score += 12.0
        elif h in low_name:
            score += 6.0
        elif h in low_source:
            score += 2.0
    name_tokens = _tokens(block.name.replace("_", " "))
    source_tokens = _tokens(block.source[:4000])
    score += min(8, len(name_tokens & paper_tokens)) * 1.5
    score += min(12, len(source_tokens & paper_tokens)) * 0.2
    if block.kind == "class":
        score *= 0.9
    return score


class GitHubSourceClient:
    """Small GitHub API/raw client with optional token support."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        session: Any = None,
        timeout: int = 30,
    ) -> None:
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_json(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=self.timeout)
            if getattr(resp, "status_code", 200) == 403:
                remaining = getattr(resp, "headers", {}).get("X-RateLimit-Remaining")
                if remaining == "0":
                    logger.warning("GitHub API rate limit exceeded for %s", url)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("GitHub API request failed for %s: %s", url, exc)
            return None

    def _get_text(self, url: str) -> Optional[str]:
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.warning("GitHub raw request failed for %s: %s", url, exc)
            return None

    def repo_metadata(self, ref: GitHubRepoRef) -> Optional[Dict[str, Any]]:
        repo = self._get_json(ref.api_url)
        if not repo:
            return None
        branch = repo.get("default_branch") or "main"
        commit = self._get_json(f"{ref.api_url}/commits/{branch}") or {}
        license_info = repo.get("license") or {}
        return {
            "default_branch": branch,
            "commit_sha": commit.get("sha"),
            "license": license_info.get("spdx_id") or license_info.get("key") or license_info.get("name"),
        }

    def source_files(
        self,
        ref: GitHubRepoRef,
        *,
        default_branch: str,
        commit_sha: Optional[str],
        hints: Sequence[str],
        paper_tokens: set[str],
    ) -> List[SourceFile]:
        tree_ref = commit_sha or default_branch
        tree = self._get_json(f"{ref.api_url}/git/trees/{tree_ref}?recursive=1") or {}
        if tree.get("truncated"):
            logger.warning("GitHub tree truncated for %s; using scored subset", ref.url)
        files: List[tuple[float, SourceFile]] = []
        for item in tree.get("tree") or []:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            path = str(item.get("path") or "")
            size = int(item.get("size") or 0)
            if not _path_supported(path) or size <= 0 or size > MAX_SOURCE_FILE_BYTES:
                continue
            if _path_is_noise(path):
                continue
            quoted_path = quote(path, safe="/")
            raw_url = (
                f"https://raw.githubusercontent.com/{ref.owner}/{ref.repo}/"
                f"{tree_ref}/{quoted_path}"
            )
            html_url = f"{ref.url}/blob/{tree_ref}/{quoted_path}"
            score = _path_score(path, hints, paper_tokens)
            if score <= 0 and len(tree.get("tree") or []) > 100:
                continue
            files.append((score, SourceFile(path=path, raw_url=raw_url, html_url=html_url, size=size)))
        files.sort(key=lambda item: item[0], reverse=True)
        return [f for _, f in files[:MAX_SOURCE_FILES_PER_REPO]]

    def fetch_file(self, source_file: SourceFile) -> Optional[str]:
        return self._get_text(source_file.raw_url)


def build_code_snippet_records(
    *,
    ref: GitHubRepoRef,
    source_file: SourceFile,
    metadata: Dict[str, Any],
    blocks: Iterable[SourceBlock],
    source_paper: str,
    paper_text: str,
    hints: Sequence[str],
) -> List[Dict[str, Any]]:
    paper_tokens = _tokens(paper_text)
    scored = [
        (_block_score(block, source_file.path, hints, paper_tokens), block)
        for block in blocks
    ]
    scored = [(score, block) for score, block in scored if score > 0]
    scored.sort(key=lambda item: item[0], reverse=True)

    license_name = metadata.get("license")
    records: List[Dict[str, Any]] = []
    for score, block in scored[:MAX_SNIPPETS_PER_REPO]:
        records.append({
            "source_type": "github",
            "source_paper": source_paper,
            "page": 0,
            "function_name": block.name,
            "code_language": "python",
            "code_snippet": block.source,
            "code_description": (
                f"{block.name}: {block.kind} source from {ref.url} "
                f"({source_file.path}:{block.start_line}-{block.end_line})"
            ),
            "code_domain": None,
            "domain_features": [],
            "authors": [],
            "repo_url": ref.url,
            "repo_owner": ref.owner,
            "repo_name": ref.repo,
            "repo_default_branch": metadata.get("default_branch"),
            "repo_commit_sha": metadata.get("commit_sha"),
            "source_file_path": source_file.path,
            "source_file_url": source_file.html_url,
            "source_start_line": block.start_line,
            "source_end_line": block.end_line,
            "repository_license": license_name,
            "license_warning": None if license_name else "Repository license not reported by GitHub API.",
            "source_score": round(score, 3),
        })
    return records


def extract_github_code_snippets(
    paper_text: str,
    *,
    source_paper: str,
    client: Optional[GitHubSourceClient] = None,
) -> List[Dict[str, Any]]:
    """Fetch function/class snippets from GitHub repos linked in a paper."""
    refs = extract_github_repositories(paper_text)
    if not refs:
        return []

    client = client or GitHubSourceClient()
    hints = extract_identifier_hints(paper_text)
    paper_tokens = _tokens(paper_text)
    all_records: List[Dict[str, Any]] = []
    for ref in refs:
        metadata = client.repo_metadata(ref)
        if not metadata:
            continue
        files = client.source_files(
            ref,
            default_branch=metadata["default_branch"],
            commit_sha=metadata.get("commit_sha"),
            hints=hints,
            paper_tokens=paper_tokens,
        )
        repo_records: List[Dict[str, Any]] = []
        for source_file in files:
            source = client.fetch_file(source_file)
            if not source:
                continue
            blocks = extract_python_blocks(source)
            repo_records.extend(
                build_code_snippet_records(
                    ref=ref,
                    source_file=source_file,
                    metadata=metadata,
                    blocks=blocks,
                    source_paper=source_paper,
                    paper_text=paper_text,
                    hints=hints,
                )
            )
        repo_records.sort(key=lambda item: item.get("source_score", 0), reverse=True)
        all_records.extend(repo_records[:MAX_SNIPPETS_PER_REPO])
    all_records.sort(key=lambda item: item.get("source_score", 0), reverse=True)
    logger.info("Extracted %d GitHub snippet(s) from %s", len(all_records), source_paper)
    return all_records
