"""FAIR2WISE application package: ``f2w`` when installed, ``app`` in a checkout.

One process, one name (ADR package-and-imports, decision 3): the same files
must not be loaded as both ``app.*`` and ``f2w.*``, because each name gets its
own copy of every stateful module. Importing this package under one name
raises ``ImportError`` when F2W is already loaded under the other. A foreign
top-level ``app`` package (for example FORGE's) carries no F2W marker and is
ignored. No alias finder and no ``sys.modules`` rewriting.

Importing this package has no other effect.
"""
import sys as _sys

_F2W_DISTRIBUTION = "fair2wise"


def _check_single_name() -> None:
    other_name = {"f2w": "app", "app": "f2w"}.get(__name__)
    other = _sys.modules.get(other_name) if other_name else None
    if other is not None and getattr(other, "_F2W_DISTRIBUTION", None) == _F2W_DISTRIBUTION:
        raise ImportError(
            f"F2W is already imported as {other_name!r} in this process; importing it again as "
            f"{__name__!r} would load every module twice. Use 'f2w' (installed) or 'app' (checkout), "
            "not both."
        )


_check_single_name()
