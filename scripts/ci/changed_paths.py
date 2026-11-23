#!/usr/bin/env python3
"""Decide which CI job groups a push or pull request needs, from the files it changed.

Used by the ``guard`` job of ``.github/workflows/forge-merge-ci.yml``. It writes three
``name=true|false`` lines to ``$GITHUB_OUTPUT`` (or stdout):

* ``python``: anything outside ``ui/`` and outside the documentation set changed, so the
  lint, type and unit-test jobs run;
* ``frontend``: ``ui/`` (or the workflow file itself) changed, so the Vitest and build jobs run;
* ``docs``: anything that feeds the MkDocs site changed (also true when Python changed,
  because the site documents the code).

The frozen-UI guard is not decided here: it runs on every event. When the base commit is
unknown (first push, force-push, dispatch, schedule) or the diff cannot be computed, every
output is ``true``: the filter only ever skips work, it never invents a green result.

Usage:
    python scripts/ci/changed_paths.py --base <sha> --head <sha> [--force-all]
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import os
from pathlib import Path
import subprocess
import sys

ZERO_SHA = "0" * 40
WORKFLOW = ".github/workflows/forge-merge-ci.yml"


def is_docs_only(path: str) -> bool:
    """Return True for a file that cannot change Python behavior: Markdown and the MkDocs tree."""
    return path.endswith(".md") or path.startswith("mkdocs/")


def classify(paths: Iterable[str]) -> dict[str, bool]:
    """Map a list of changed paths to the three job-group flags."""
    changed = [p for p in paths if p]
    python = any(not p.startswith("ui/") and not is_docs_only(p) for p in changed)
    frontend = any(p.startswith("ui/") or p == WORKFLOW for p in changed)
    docs = python or any(is_docs_only(p) for p in changed)
    return {"python": python, "frontend": frontend, "docs": docs}


def changed_files(base: str, head: str) -> list[str] | None:
    """Return the files changed between two commits, or None when the diff is unavailable."""
    if not base or base == ZERO_SHA:
        return None
    for spec in (f"{base}...{head}", f"{base}..{head}"):
        result = subprocess.run(["git", "diff", "--name-only", spec], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout.splitlines()
    return None


def main(argv: list[str] | None = None) -> int:
    """Print the flags (and append them to ``$GITHUB_OUTPUT`` when set)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--force-all", action="store_true", help="run everything (dispatch, schedule)")
    args = parser.parse_args(argv)
    files = None if args.force_all else changed_files(args.base, args.head)
    flags = {"python": True, "frontend": True, "docs": True} if files is None else classify(files)
    lines = [f"{name}={str(value).lower()}" for name, value in flags.items()]
    target = os.environ.get("GITHUB_OUTPUT")
    print("\n".join(lines), file=sys.stderr if target else sys.stdout)
    if target:
        with Path(target).open("a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
