"""Private label directory: where provisional labels, review sheets, gold and the parse cache live.

Labels hold verbatim quotes and the parse cache holds full parsed page text, so the
directory is private and outside the repository by default:

* ``--out DIR``, else ``F2W_PILOT_LABELS_DIR``, else ``~/.f2w/pilot_labels``;
* a directory inside the repository is refused, except ``<repo>/.pilot_labels/``, which
  ``.gitignore`` excludes;
* it is created with mode 0700.

Layout::

    <root>/parsed/<source sha256>.json        parse cache (private page text)
    <root>/<pilot id>/<version>.label.json    provisional model labels
    <root>/<pilot id>/review_sheet.csv        reviewer decisions (edit this)
    <root>/<pilot id>/review_sheet.md         read-only view of the same items
    <root>/<pilot id>/gold.json               written only by ``promote``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.binary.model import REPO_ROOT

ENV_DIR = "F2W_PILOT_LABELS_DIR"
DEFAULT_DIR = Path("~/.f2w/pilot_labels")
IN_REPO_ALLOWED = ".pilot_labels"


class LabelStoreError(ValueError):
    """The label directory is unsafe or a file is malformed."""


def resolve_root(explicit: Optional[Path] = None, *, environ: Optional[Dict[str, str]] = None,
                 repo_root: Path = REPO_ROOT) -> Path:
    """Pick the label root and refuse one that would put private text into the repository."""
    env = os.environ if environ is None else environ
    raw = explicit if explicit is not None else (env.get(ENV_DIR) or None)
    root = Path(raw).expanduser() if raw else DEFAULT_DIR.expanduser()
    root = root.resolve()
    repo = repo_root.resolve()
    if root == repo or repo in root.parents:
        allowed = (repo / IN_REPO_ALLOWED).resolve()
        if not (root == allowed or allowed in root.parents):
            raise LabelStoreError(
                f"refusing a label directory inside the repository; use a path outside it or {IN_REPO_ALLOWED}/"
                " (gitignored)")
    return root


class LabelStore:
    """Files under one label root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        """Create the root (0700) and return it."""
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        return self.root

    def paper_dir(self, pilot_id: str) -> Path:
        return self.root / pilot_id

    def label_path(self, pilot_id: str, version: str) -> Path:
        return self.paper_dir(pilot_id) / f"{version}.label.json"

    def sheet_csv(self, pilot_id: str) -> Path:
        return self.paper_dir(pilot_id) / "review_sheet.csv"

    def sheet_md(self, pilot_id: str) -> Path:
        return self.paper_dir(pilot_id) / "review_sheet.md"

    def gold_path(self, pilot_id: str) -> Path:
        return self.paper_dir(pilot_id) / "gold.json"

    def parse_cache(self, sha256: str) -> Path:
        return self.root / "parsed" / f"{sha256}.json"

    def write_json(self, path: Path, data: Any) -> Path:
        """Atomic JSON write (creates parents)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    @staticmethod
    def read_json(path: Path) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LabelStoreError(f"cannot read {Path(path).name}: {type(exc).__name__}") from exc

    def labels_for(self, pilot_id: str) -> List[Dict[str, Any]]:
        """Every provisional label document of one paper, by version name."""
        folder = self.paper_dir(pilot_id)
        if not folder.is_dir():
            return []
        return [self.read_json(p) for p in sorted(folder.glob("*.label.json"))]


__all__ = ("DEFAULT_DIR", "ENV_DIR", "IN_REPO_ALLOWED", "LabelStore", "LabelStoreError", "resolve_root")
