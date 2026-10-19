"""Run one Knowledge Operation over a written population in a fresh process and report its peak RSS (T19D).

``python -I tests/operations/scale_rss.py <operation> <population file> [<manifest> <filename> | <reference>]``
prints one JSON line: ``{"operation", "seconds", "baseline_mib", "peak_mib", "total"}``.
``baseline_mib`` is the resident size after imports and before the operation;
``peak_mib`` is the process's maximum resident size afterwards (``getrusage``), so
``peak_mib - baseline_mib`` bounds what the operation itself held. No model, network or
.env is touched: the process imports only the operation modules.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # -I: the repository root is not on sys.path


def _mib(value: int) -> float:
    # ru_maxrss is bytes on macOS and KiB on Linux.
    return round(value / 2**20 if sys.platform == "darwin" else value / 2**10, 1)


def main(argv: list) -> int:
    from app.modules.contracts import AuthorizationMode, DeploymentProfile, Principal, RequestContext
    from app.modules.graph.backends.json import JsonGraphBackend
    from app.modules.operations.corpus import CorpusSource, audit_corpus
    from app.modules.operations.graph import compute_statistics, paper_subgraph
    from app.modules.operations.papers import PaperScope, summarize_paper_terms
    from app.modules.operations.reports import ScanControl

    op, path = argv[0], argv[1]
    ctx = RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                         principal=Principal(subject="local-operator"), request_id="t19d-rss",
                         capabilities=frozenset({"chat.ask"}))
    unbounded = ScanControl(10_000_000)
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.perf_counter()
    if op == "kg_compute_statistics":
        total = compute_statistics(JsonGraphBackend({"g": path}), ctx, "g", unbounded).metrics["entities"]
    elif op == "kg_paper_subgraph":
        total = paper_subgraph(JsonGraphBackend({"g": path}), ctx, "g", argv[2], control=unbounded).metrics["entities"]
    elif op == "curate_audit_corpus":
        total = audit_corpus(CorpusSource("c", path), control=unbounded).metrics["summary"]["distinct_works"]
    elif op == "lit_summarize_extracted_terms":
        total = summarize_paper_terms(PaperScope(path, argv[2], argv[3])).summary.term_count
    else:
        raise SystemExit(f"unknown operation {op}")
    seconds = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"operation": op, "seconds": round(seconds, 3), "baseline_mib": _mib(baseline),
                      "peak_mib": _mib(peak), "total": total}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
