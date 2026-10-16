"""Entry point: ``python -m evals.binary.label`` (see :mod:`evals.binary.labeling.cli`)."""

from __future__ import annotations

import sys

from evals.binary.labeling.cli import main

if __name__ == "__main__":
    sys.exit(main())
