#!/usr/bin/env python3
"""Classify each module's docstring style as Google, Sphinx, mixed or none (T27, F2.4).

The repository rule (docs/migration/reviews/H7/sniff_findings.md, F2.4): Google style for
new code, Sphinx field lists for code that already used them, and never both in one module.
ruff's pydocstyle ``convention = "google"`` does not detect the mix, so this script does.

A module is scanned with ``ast``; every docstring (module, class, function, nested
functions included) is searched for section markers:

* Google: a line that is only a section header, ``Args:``, ``Returns:``, ``Raises:``,
  ``Yields:``, ``Attributes:`` (and the ``Arguments:``/``Return:``/``Keyword Args:`` aliases);
* Sphinx: a field list entry at the start of a line, ``:param``, ``:type``, ``:returns:``,
  ``:return:``, ``:rtype:``, ``:raises``, ``:yields:``, ``:ivar``, ``:cvar``, ``:vartype``.

Plain prose docstrings have no marker and count as ``none``. ``:class:`` and other Sphinx
roles are cross-references, not style, and are ignored.

Usage:
    python scripts/ci/docstring_style.py                 # table and counts
    python scripts/ci/docstring_style.py --check         # exit 1 when any module is mixed
    python scripts/ci/docstring_style.py --json out.json # machine-readable per-module result
    python scripts/ci/docstring_style.py --only mixed    # list only the mixed modules, with the markers

Defaults scan ``app/`` of the repository that holds this script, excluding
``app/modules/legacy``, ``graphify-out`` and ``__pycache__``; pass paths to scan other trees.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO / "app"
EXCLUDED_PARTS = ("legacy", "graphify-out", "__pycache__")

GOOGLE_HEADER = re.compile(
    r"^[ \t]*(Args|Arguments|Keyword Args|Keyword Arguments|Returns?|Yields?|Raises|Attributes):[ \t]*$",
    re.MULTILINE,
)
SPHINX_FIELD = re.compile(
    r"^[ \t]*:(param|parameter|arg|argument|key|keyword|type|returns?|rtype|raises?|except|exception"
    r"|yields?|ivar|cvar|var|vartype)\b[^:\n]*:",
    re.MULTILINE,
)

STYLES = ("google", "sphinx", "mixed", "none")


@dataclass
class Marker:
    """One style marker found in a docstring."""

    line: int
    owner: str
    style: str
    text: str


@dataclass
class ModuleStyle:
    """Classification of one module."""

    path: str
    style: str
    google: int = 0
    sphinx: int = 0
    markers: list[Marker] = field(default_factory=list)


def iter_modules(paths: Sequence[Path]) -> Iterator[Path]:
    """Yield the ``.py`` files under ``paths``, skipping the excluded directory names."""
    for base in paths:
        if base.is_file():
            yield base
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in EXCLUDED_PARTS for part in path.relative_to(base).parts):
                continue
            yield path


def _docstring_nodes(tree: ast.Module) -> Iterable[tuple[str, ast.AST, str]]:
    yield "module", tree, ast.get_docstring(tree, clean=False) or ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text = ast.get_docstring(node, clean=False) or ""
            if text:
                yield node.name, node, text


def classify_source(source: str, path: str = "<string>") -> ModuleStyle:
    """Classify one module's source text."""
    tree = ast.parse(source, filename=path)
    result = ModuleStyle(path=path, style="none")
    for owner, node, text in _docstring_nodes(tree):
        if not text:
            continue
        base_line = _docstring_line(node)
        for match in GOOGLE_HEADER.finditer(text):
            result.google += 1
            result.markers.append(
                Marker(base_line + text.count("\n", 0, match.start()), owner, "google", match.group(0).strip())
            )
        for match in SPHINX_FIELD.finditer(text):
            result.sphinx += 1
            result.markers.append(
                Marker(base_line + text.count("\n", 0, match.start()), owner, "sphinx", match.group(0).strip())
            )
    if result.google and result.sphinx:
        result.style = "mixed"
    elif result.google:
        result.style = "google"
    elif result.sphinx:
        result.style = "sphinx"
    result.markers.sort(key=lambda m: m.line)
    return result


def _docstring_line(node: ast.AST) -> int:
    body = node.body if isinstance(node, ast.Module) else getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        return int(body[0].lineno)
    return int(getattr(node, "lineno", 1))


def classify_tree(paths: Sequence[Path], root: Path | None = None) -> list[ModuleStyle]:
    """Classify every module under ``paths``; ``path`` fields are relative to ``root`` when given."""
    results: list[ModuleStyle] = []
    for module in iter_modules(paths):
        shown = str(module.relative_to(root)) if root and module.is_relative_to(root) else str(module)
        try:
            source = module.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = module.read_text(encoding="utf-8", errors="replace")
        results.append(classify_source(source, shown))
    return results


def summarize(results: Sequence[ModuleStyle]) -> dict[str, int]:
    """Count modules per style."""
    counts = {style: 0 for style in STYLES}
    for item in results:
        counts[item.style] += 1
    counts["total"] = len(results)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    """Run the classifier; return 1 under ``--check`` when a module mixes styles."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories (default: app/)")
    parser.add_argument("--check", action="store_true", help="exit 1 when any module is mixed")
    parser.add_argument("--json", type=Path, help="write the per-module result to this file")
    parser.add_argument("--only", choices=STYLES, help="print only modules of this style")
    args = parser.parse_args(argv)

    paths = args.paths or [DEFAULT_ROOT]
    results = classify_tree(paths, root=REPO)
    counts = summarize(results)

    shown = [r for r in results if args.only is None or r.style == args.only]
    for item in shown:
        line = f"{item.style:<6} google={item.google:<3} sphinx={item.sphinx:<3} {item.path}"
        print(line)  # noqa: T201 - CLI output
        if item.style == "mixed" or args.only == "mixed":
            for marker in item.markers:
                print(f"       {item.path}:{marker.line} [{marker.style}] {marker.owner}: {marker.text}")  # noqa: T201
    print(  # noqa: T201 - CLI output
        f"modules={counts['total']} google={counts['google']} sphinx={counts['sphinx']} "
        f"mixed={counts['mixed']} none={counts['none']}"
    )
    if args.json:
        args.json.write_text(
            json.dumps({"counts": counts, "modules": [asdict(r) for r in results]}, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.check and counts["mixed"]:
        print(f"docstring style: {counts['mixed']} module(s) mix Google sections and Sphinx fields")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
