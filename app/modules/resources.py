"""Resource-root discovery for schemas, vocabularies and ``config.yml`` (T04).

Schema/vocabulary assets (``storage/schema/*.yaml``), ``config.yml`` and the
data trees under ``storage/`` live outside the ``app/`` package and are not
shipped as package data: copying them into the wheel would create a second,
unreviewed copy of owner-governed scientific schemas (T22A/H3). They resolve
from one explicit resource root instead:

1. ``F2W_RESOURCE_ROOT`` when set (required for an installed wheel that needs
   schemas or ``config.yml``);
2. otherwise, in a checkout (``app/`` tree, including an editable install),
   the repository root, i.e. exactly the paths used before T04;
3. otherwise (installed wheel, no root configured) ``None``: config lookups
   fall back to their defaults, and resolving a missing asset raises
   :class:`ResourceRootError` naming the variable.

The root is read at call time, never at import.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

RESOURCE_ROOT_VAR = "F2W_RESOURCE_ROOT"
_PACKAGE_DIR = Path(__file__).resolve().parents[1]  # app/ in a checkout, site-packages/f2w when installed


class ResourceRootError(FileNotFoundError):
    """A resource is needed but no resource root is configured (installed wheel)."""


def is_checkout() -> bool:
    """True when this code runs from the ``app/`` source tree rather than an installed ``f2w`` wheel."""
    return _PACKAGE_DIR.name == "app"


def resource_root() -> Path | None:
    """Return the configured resource root, the checkout root, or ``None``."""
    explicit = os.environ.get(RESOURCE_ROOT_VAR, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if is_checkout():
        return _PACKAGE_DIR.parent
    return None


def resolve_resource(path: Union[str, Path]) -> Path:
    """Resolve a schema/vocabulary path without depending on the working directory.

    Absolute paths and relative paths that exist from the current directory are
    returned unchanged (explicit CLI arguments keep their meaning). Any other
    relative path, such as the built-in ``storage/schema/...`` defaults, is
    resolved against :func:`resource_root`.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute() or candidate.exists():
        return candidate
    root = resource_root()
    if root is None:
        raise ResourceRootError(
            f"{path!s} not found and no resource root is configured; set {RESOURCE_ROOT_VAR} "
            "to the directory that contains storage/ and config.yml"
        )
    return root / candidate
