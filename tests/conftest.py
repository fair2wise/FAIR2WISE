import os
import sys

import pytest

os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ["KG_RAG_GRAPH_SOURCE"] = "json"
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib-codex")
# Isolated missing indexes so unit tests do not pick up a local gold/ops index.
os.environ.setdefault("HYBRID_RAG_LITERATURE_INDEX", "/tmp/fair2wise-missing-lit-index")
os.environ.setdefault("HYBRID_RAG_OPS_INDEX", "/tmp/fair2wise-missing-ops-index")
os.environ.setdefault("F2W_SOURCE_RAG", "0")
os.environ.setdefault("F2W_LIVE_TILED", "0")


@pytest.fixture(autouse=True)
def _arxiv_limiter_never_sleeps_for_real():
    """The process-wide arXiv limiter (``legacy_download.ARXIV_LIMITER``, 3 s between requests) is replaced per test
    by a fresh one on a fake clock, so no test waits for real and no test inherits another's last-request time.
    A test of the spacing itself injects its own ``RateLimiter``. Only acts once the module is imported.
    (No ``monkeypatch`` here: requesting it from an autouse fixture would reorder other fixtures' teardown.)"""
    ld = sys.modules.get("app.modules.literature.legacy_download")
    if ld is None:
        yield
        return
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    original = ld.ARXIV_LIMITER
    ld.ARXIV_LIMITER = ld.RateLimiter(ld.ARXIV_MIN_INTERVAL_SECONDS, clock=lambda: clock[0], sleep=sleep)
    try:
        yield
    finally:
        ld.ARXIV_LIMITER = original
