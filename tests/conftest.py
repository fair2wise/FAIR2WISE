import os


os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ["KG_RAG_GRAPH_SOURCE"] = "json"
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib-codex")
# Isolated missing indexes so unit tests do not pick up a local gold/ops index.
os.environ.setdefault("HYBRID_RAG_LITERATURE_INDEX", "/tmp/fair2wise-missing-lit-index")
os.environ.setdefault("HYBRID_RAG_OPS_INDEX", "/tmp/fair2wise-missing-ops-index")
os.environ.setdefault("F2W_SOURCE_RAG", "0")
os.environ.setdefault("F2W_LIVE_TILED", "0")
