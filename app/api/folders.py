"""Folder browse and index over fixed roots, ``:local`` only (task T32, owner decision B4 / O-P4).

The donor's ``ingest_folders_routes.py`` let an operator browse paper folders and start an
index run. Here the browsable roots are **startup configuration**: the trusted setting
``F2W_FOLDER_ROOTS`` (``os.pathsep``-separated ``label=/absolute/path`` entries, or bare
absolute paths labelled by their directory name), or a :class:`FolderRoots` passed to
``create_api_app`` by trusted Python wiring. No request names a root path: a request names a
root *label* and a relative path under it.

Containment is the literature intake policy
(:class:`~app.modules.literature.safe_fetch.LocalIntakePolicy`): the lexical path is checked
first (no absolute path, no ``..``, no backslash or control character, no empty segment),
then resolved and required to stay inside the named root, and no symlink at or below a root is
followed. A refused path is ``NOT_FOUND`` (404) with a fixed message, so a probe cannot tell a
missing folder from one outside the roots, and nothing in a DTO is an absolute path: entries
carry the root label and a relative path only.

The routes are mounted by ``create_api_app`` only when the profile is ``:local``; on an ALS
profile they are not registered (a plain 404) and the ALS image profiles refuse the setting
(``forbidden_env``). Nothing here reads ``os.environ`` or a ``.env`` file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.literature.safe_fetch import LocalIntakePolicy
from ..modules.security.authorization import StartupConfigError

#: Trusted startup setting naming the browsable roots.
FOLDER_ROOTS_ENV = "F2W_FOLDER_ROOTS"
#: Entries a single listing returns at most (the rest is reported as truncated).
MAX_LIST_ENTRIES = 1000
#: Documents a single index returns at most.
MAX_INDEX_DOCUMENTS = 5000
#: Directories an index walks at most, whatever the document count.
MAX_INDEX_FOLDERS = 20_000
#: File suffixes the index counts as documents.
DOCUMENT_SUFFIXES = frozenset({".pdf"})
#: Fixed refusal: never says whether the folder exists, is a file, is a link or lies outside the roots.
NOT_FOUND_MESSAGE = "folder not found under the named root"

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SEGMENT_RE = re.compile(r"^[^\x00-\x1f\x7f\\/]{1,255}$")


def _label_for(path: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.name).strip("-.") or "root"
    return name[:64]


def check_relative(path: str | None) -> PurePosixPath:
    """Validate a request-supplied relative folder path lexically; return it as a pure POSIX path.

    Empty or ``None`` is the root itself. Refused (``INVALID_INPUT``): an absolute path or a
    drive letter, ``~``, ``.`` or ``..`` or any other dot-prefixed (hidden) segment, empty
    segments (``a//b``), backslashes, control characters and over-long segments. Percent-encoding is already decoded by the time
    a route sees the value, so ``%2e%2e`` and ``%2f`` are caught here as ``..`` and ``/``.
    """
    if path is None or path == "":
        return PurePosixPath()
    if not isinstance(path, str) or len(path) > 4096:
        raise ContractError(ErrorCode.INVALID_INPUT, "path must be a relative folder path")
    if path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", path):
        raise ContractError(ErrorCode.INVALID_INPUT, "path must be relative to the root")
    segments = path.rstrip("/").split("/")
    for segment in segments:
        if segment == "" or segment.startswith(".") or not _SEGMENT_RE.match(segment):
            raise ContractError(ErrorCode.INVALID_INPUT,
                                "path must be a relative folder path without empty, dot or parent segments")
    return PurePosixPath(*segments)


def _entry(path: Path, relative: PurePosixPath) -> dict[str, Any] | None:
    """One listing entry; links are named but never followed; unreadable entries are skipped."""
    try:
        if path.is_symlink():
            return {"name": path.name, "relative_path": str(relative), "kind": "link", "size": None,
                    "modified": None, "document": False}
        stat = path.stat()
    except OSError:  # entry vanished or is unreadable: left out of the listing
        return None
    if path.is_dir():
        kind, size, document = "directory", None, False
    elif path.is_file():
        kind, size, document = "file", stat.st_size, path.suffix.lower() in DOCUMENT_SUFFIXES
    else:
        kind, size, document = "other", None, False
    return {"name": path.name, "relative_path": str(relative), "kind": kind, "size": size,
            "modified": round(stat.st_mtime, 3), "document": document}


@dataclass(frozen=True)
class FolderRoots:
    """The browsable roots (label -> resolved directory) and their containment policy.

    Attributes:
        roots: Label to absolute, resolved root directory; built once at startup.
    """

    roots: Mapping[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label, root in self.roots.items():
            if not _LABEL_RE.match(label):
                raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} label {label!r} is not a plain name")
            if not Path(root).is_absolute():
                raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} root for {label!r} must be absolute")
        object.__setattr__(self, "roots", dict(self.roots))

    @classmethod
    def from_paths(cls, paths: Mapping[str, Any] | list[Any] | tuple[Any, ...]) -> FolderRoots:
        """Build from ``{label: path}`` or a sequence of paths (labelled by directory name)."""
        items: list[tuple[str, Path]] = []
        if isinstance(paths, Mapping):
            items = [(str(label), Path(p).expanduser()) for label, p in paths.items()]
        else:
            for raw in paths:
                path = Path(raw).expanduser()
                items.append((_label_for(path), path))
        roots: dict[str, Path] = {}
        for label, path in items:
            if label in roots:
                raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} names the label {label!r} twice")
            if not path.is_absolute():
                raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} root {label!r} must be absolute")
            roots[label] = path.resolve()
        return cls(roots)

    @classmethod
    def from_startup(cls, values: Mapping[str, str] | None) -> FolderRoots:
        """Parse ``F2W_FOLDER_ROOTS`` from a trusted startup mapping (``{}`` -> no roots).

        Entries are separated by ``os.pathsep``; each is ``label=/abs/path`` or ``/abs/path``.
        """
        raw = (values or {}).get(FOLDER_ROOTS_ENV, "") or ""
        labelled: dict[str, Any] = {}
        bare: list[str] = []
        for entry in (e.strip() for e in raw.split(os.pathsep)):
            if not entry:
                continue
            if "=" in entry and not entry.startswith("/"):
                label, _, path = entry.partition("=")
                if label.strip() in labelled:
                    raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} names the label {label!r} twice")
                labelled[label.strip()] = path.strip()
            else:
                bare.append(entry)
        merged: dict[str, Any] = dict(cls.from_paths(bare).roots) if bare else {}
        for label, path in labelled.items():
            if label in merged:
                raise StartupConfigError(f"config_rejected: {FOLDER_ROOTS_ENV} names the label {label!r} twice")
            merged[label] = Path(path).expanduser()
        return cls.from_paths(merged)

    @property
    def configured(self) -> bool:
        """Whether any root is configured."""
        return bool(self.roots)

    def policy(self, label: str) -> LocalIntakePolicy:
        """The containment policy of one root (symlinks never followed)."""
        return LocalIntakePolicy.from_paths([self.roots[label]], allow_symlinks=False)

    def describe(self) -> list[dict[str, Any]]:
        """The roots as DTOs: label and whether the directory exists right now (never its path)."""
        return [{"root": label, "exists": root.is_dir()} for label, root in sorted(self.roots.items())]

    def resolve(self, label: str, path: str | None) -> tuple[Path, PurePosixPath]:
        """Return the resolved directory for ``label``/``path`` or refuse with a fixed ``NOT_FOUND``.

        Raises:
            ContractError: ``INVALID_INPUT`` for a lexically bad path (``check_relative``);
                ``NOT_FOUND`` for an unknown root, a missing folder, a file, a link or anything
                resolving outside the root.
        """
        relative = check_relative(path)
        root = self.roots.get(label) if isinstance(label, str) and _LABEL_RE.match(label or "") else None
        if root is None:
            raise ContractError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE)
        try:
            resolved = self.policy(label).require_allowed_folder(root.joinpath(*relative.parts))
        except ValueError:
            raise ContractError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE) from None
        if resolved != root and root not in resolved.parents:  # belt and braces over the policy
            raise ContractError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE)
        return resolved, relative

    def list_folder(self, label: str, path: str | None = None, *, limit: int = MAX_LIST_ENTRIES) -> dict[str, Any]:
        """Entries of one folder (directories first, then names), hidden entries skipped, links not followed."""
        resolved, relative = self.resolve(label, path)
        bound = max(1, min(int(limit), MAX_LIST_ENTRIES))
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir() or p.is_symlink(), p.name))
        except OSError:
            raise ContractError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE) from None
        total = 0
        for child in children:
            if child.name.startswith("."):
                continue
            total += 1
            if len(entries) < bound:
                entry = _entry(child, relative / child.name)
                if entry is not None:
                    entries.append(entry)
        return {"root": label, "relative_path": str(relative) if relative.parts else "", "entries": entries,
                "count": total, "truncated": total > len(entries)}

    def index_folder(self, label: str, path: str | None = None, *,
                     limit: int = MAX_INDEX_DOCUMENTS) -> dict[str, Any]:
        """Bounded inventory of the documents (PDFs) under one folder, recursively, without following links.

        This is the read-only part of the donor's index run: what would be indexed. Starting an
        ingest over it is a job (``POST /v1/jobs``), not a side effect of this read.
        """
        resolved, relative = self.resolve(label, path)
        bound = max(1, min(int(limit), MAX_INDEX_DOCUMENTS))
        documents: list[dict[str, Any]] = []
        total = folders = 0
        truncated = False
        for current, dirnames, filenames in os.walk(resolved, followlinks=False):
            folders += 1
            if folders > MAX_INDEX_FOLDERS:
                truncated = True
                break
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                 and not Path(current, d).is_symlink())
            here = Path(current)
            for name in sorted(filenames):
                file = here / name
                if name.startswith(".") or Path(name).suffix.lower() not in DOCUMENT_SUFFIXES or file.is_symlink():
                    continue
                total += 1
                if len(documents) >= bound:
                    truncated = True
                    continue
                try:
                    stat = file.stat()
                except OSError:  # entry vanished or is unreadable: left out of the listing
                    continue
                documents.append({"relative_path": str(relative / file.relative_to(resolved).as_posix()),
                                  "size": stat.st_size, "modified": round(stat.st_mtime, 3)})
        return {"root": label, "relative_path": str(relative) if relative.parts else "", "documents": documents,
                "count": total, "folders_scanned": folders, "truncated": truncated}


__all__ = ("DOCUMENT_SUFFIXES", "FOLDER_ROOTS_ENV", "MAX_INDEX_DOCUMENTS", "MAX_INDEX_FOLDERS", "MAX_LIST_ENTRIES",
           "NOT_FOUND_MESSAGE", "FolderRoots", "check_relative")
