"""Pure paper-figure kernels (V-09..V-14): geometry, panels, q-axis, scale bar, units, types.

Import closure reviewed: stdlib (``re``, ``math``, ``dataclasses``, ``collections.abc``,
``typing``) and ``numpy`` only. No ``forge.*``, simulation, pyFAI or experimental-analysis
imports; the donor's one such import (``forge.analysis.resolution.measure_scale_bar_result``)
is replaced by the local :mod:`.barmeasure`.
"""
