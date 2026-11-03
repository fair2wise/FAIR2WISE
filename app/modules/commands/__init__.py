"""Operator command-line tools promoted from ``scripts/`` (T17B step 4, owner approval 2026-11-03).

Each module has a ``main`` (or ``cli``) function and is registered as a console script in ``pyproject.toml``
(``f2w-run``, ``f2w-tiled-server``, ``f2w-harvest``, ``f2w-paper-finder``, ``f2w-corpus-qa``,
``f2w-repair-corpus``, ``f2w-kg-snapshot``, ``f2w-kg-release``, ``f2w-kg-adopt``; ``f2w-download-pdfs`` points at
``literature.download_pdfs``). The files under ``scripts/`` with the same names remain as launch facades so
``python scripts/<name>.py`` keeps working in a checkout.

Secrets never travel in argv: API keys and tokens are read from the environment or a file named by it.
"""
