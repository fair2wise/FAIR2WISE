"""Source-tree readers for the BL11.0.1.2 ingest: glob expansion, Python/TypeScript/compose/Markdown extraction."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
import re
from typing import Any

import yaml

from .common import LOGGER, beam_id, should_skip_file
from .specs import DEVICE_BASE_HINTS, MAX_SNIPPET_LINES, NAMESPACE, PUBLIC_FUNCTIONS


def expand_globs(root: Path, patterns: Sequence[str]) -> list[Path]:
    """Expand ``patterns`` under ``root`` into a de-duplicated list of ingestable files.

    Directories, secret files and skipped paths are dropped. Order follows the
    pattern order, sorted within each pattern.
    """
    found: list[Path] = []
    for pattern in patterns:
        found.extend(sorted(root.glob(pattern)))
    unique: list[Path] = []
    seen = set()
    for path in found:
        if not path.is_file() or should_skip_file(path):
            continue
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        unique.append(path)
    return unique


def _base_names(node: ast.ClassDef) -> str:
    chunks = []
    for base in node.bases:
        try:
            chunks.append(ast.unparse(base))
        except Exception:
            chunks.append(getattr(base, "id", "") or "")
    return " ".join(chunks)


def _is_device_class(node: ast.ClassDef) -> bool:
    if node.name.endswith(("Error", "Exception")):
        return False
    bases = _base_names(node)
    if any(hint in bases for hint in DEVICE_BASE_HINTS):
        return True
    return bool(
        re.match(r"^(Sim|Epics|AD|Replay)", node.name)
        or node.name.endswith(("Device", "Motor", "Detector", "Endstation", "CCD", "Plan"))
    )


def _is_plan_function(node: ast.FunctionDef) -> bool:
    if node.name.startswith("_"):
        return False
    if node.name in PUBLIC_FUNCTIONS:
        return True
    deco = " ".join(
        ast.unparse(d) if not isinstance(d, ast.Name) else d.id for d in node.decorator_list
    )
    if "parameter_annotation" in deco or "plan" in deco.lower():
        return True
    try:
        src = ast.unparse(node)
    except Exception:
        src = node.name
    if "yield" in src and any(tok in node.name.lower() for tok in ("scan", "count", "replay")):
        return True
    return node.name in {"count", "scan", "rel_scan", "grid_scan"} or "scan" in node.name.lower()


def extract_python_symbols(path: Path, repo_name: str, commit: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (terms-like symbol records, curated code snippets) from one Python file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        LOGGER.warning("Skipping Python %s: %s", path, exc)
        return [], []
    lines = source.splitlines()
    rel = path.name
    snippets: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []

    def snippet_for(node: ast.AST, kind: str, function_name: str) -> dict[str, Any] | None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        chunk = lines[start - 1 : end]
        body = "\n".join(chunk[:MAX_SNIPPET_LINES])
        if not body.strip():
            return None
        paper = f"{repo_name}:{path.name}:{function_name}"
        return {
            "id": beam_id("snippet", repo_name, function_name, str(start)),
            "id_prefix": NAMESPACE,
            "curated": True,
            "function_name": function_name,
            "code_snippet": body,
            "code_language": "python",
            "code_description": f"{kind} `{function_name}` from {repo_name} ({path.name}:{start}-{end})",
            "source_paper": paper,
            "page": start,
            "source_type": "github",
            "repo_name": repo_name,
            "repo_url": f"https://github.com/als-computing/{repo_name}",
            "repo_commit_sha": commit,
            "source_file_path": rel,
            "source_start_line": start,
            "source_end_line": end,
            "source_file_url": (
                f"https://github.com/als-computing/{repo_name}/blob/{commit}/{rel}#L{start}-L{end}"
                if commit
                else ""
            ),
        }

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_device_class(node):
            snip = snippet_for(node, "class", node.name)
            if not snip:
                continue
            snippets.append(snip)
            category = "Detector" if "detect" in node.name.lower() or "ccd" in node.name.lower() else "OphydDevice"
            if node.name.lower().endswith("plan") or "scan" in node.name.lower():
                category = "BlueskyPlan"
            if node.name in {"Endstation"}:
                category = "Endstation"
            symbols.append(
                {
                    "id": beam_id("ophyd", node.name),
                    "name": node.name,
                    "category": category,
                    "file": rel,
                    "snippet_paper": snip["source_paper"],
                    "start": snip["source_start_line"],
                    "end": snip["source_end_line"],
                }
            )
        elif isinstance(node, ast.FunctionDef) and _is_plan_function(node):
            snip = snippet_for(node, "function", node.name)
            if not snip:
                continue
            snippets.append(snip)
            is_loader = node.name in PUBLIC_FUNCTIONS
            symbols.append(
                {
                    "id": beam_id("plan" if not is_loader else "fn", node.name),
                    "name": node.name,
                    "category": "Method" if is_loader else "BlueskyPlan",
                    "file": rel,
                    "snippet_paper": snip["source_paper"],
                    "start": snip["source_start_line"],
                    "end": snip["source_end_line"],
                }
            )
    return symbols, snippets


def parse_compose_services(path: Path) -> list[dict[str, str]]:
    """List the service names defined in a docker-compose file.

    Uses the YAML ``services`` mapping. If the file is not valid YAML or has no
    such mapping, falls back to a regex over two-space-indented keys, excluding
    top-level section names such as ``networks`` and ``volumes``.

    Returns:
        ``{"name", "file"}`` dicts, with ``file`` the compose file's basename.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        LOGGER.warning("YAML parse failed for %s: %s", path, exc)
        data = {}
    services = data.get("services") if isinstance(data, dict) else None
    names: list[str] = []
    if isinstance(services, dict):
        names = [str(k) for k in services.keys()]
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        names = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", text)
        skip = {"networks", "volumes", "configs", "secrets", "x-anchors"}
        names = [n for n in names if n not in skip]
    return [{"name": n, "file": path.name} for n in names]


def extract_markdown_software(path: Path, repo_name: str, limit: int = 400) -> str:
    """Return a short plain-text summary of a Markdown file.

    Takes the first heading and the lines after it from the first 80 lines, drops
    code fences, and collapses whitespace. Whole READMEs are deliberately never
    copied into nodes.

    Args:
        path: Markdown file to read.
        repo_name: Repo the file belongs to. Unused.
        limit: Maximum characters returned.

    Returns:
        The summary, or ``""`` if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # First heading + following paragraph; never dump whole READMEs into nodes.
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines[:80]:
        if line.startswith("#") or kept:
            kept.append(line)
        if len(" ".join(kept)) > limit and line.strip() == "":
            break
    blob = " ".join(x.strip() for x in kept if x.strip() and not x.startswith("```"))
    return re.sub(r"\s+", " ", blob)[:limit]


def extract_ts_fences(path: Path, repo_name: str, commit: str = "") -> list[dict[str, Any]]:
    """Extract the first Finch-config TypeScript fence from a Markdown file.

    Only a ```` ```ts ````, ```` ```tsx ```` or ```` ```typescript ```` fence that mentions
    ``FinchConfig`` or ``ophyd`` qualifies.

    Args:
        path: Markdown file to read.
        repo_name: Repo name stamped on the snippet.
        commit: Commit SHA stamped on the snippet.

    Returns:
        A list with at most one snippet record (body truncated to 4000 characters),
        or an empty list if the file is unreadable or nothing matches.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    snippets = []
    for match in re.finditer(r"```(?:tsx|ts|typescript)\n(.*?)```", text, re.S):
        body = match.group(1).strip()
        if "FinchConfig" not in body and "ophyd" not in body.lower():
            continue
        start = text[: match.start()].count("\n") + 1
        snippets.append(
            {
                "id": beam_id("snippet", repo_name, "FinchConfigProvider", str(start)),
                "id_prefix": NAMESPACE,
                "curated": True,
                "function_name": "FinchConfigProvider",
                "code_snippet": body[:4000],
                "code_language": "typescript",
                "code_description": f"Finch config snippet from {repo_name} README",
                "source_paper": f"{repo_name}:README.md:FinchConfigProvider",
                "page": start,
                "source_type": "github",
                "repo_name": repo_name,
                "repo_commit_sha": commit,
                "source_file_path": path.name,
                "source_start_line": start,
            }
        )
        break
    return snippets
