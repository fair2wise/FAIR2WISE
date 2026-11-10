import os
from pathlib import Path


os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ["KG_RAG_GRAPH_SOURCE"] = "json"
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib-codex")
# Isolated missing indexes so unit tests do not pick up a local gold/ops index.
os.environ.setdefault("HYBRID_RAG_LITERATURE_INDEX", "/tmp/fair2wise-missing-lit-index")
os.environ.setdefault("HYBRID_RAG_OPS_INDEX", "/tmp/fair2wise-missing-ops-index")
os.environ.setdefault("F2W_SOURCE_RAG", "0")
os.environ.setdefault("F2W_LIVE_TILED", "0")
# Free-model policy (owner decision 2026-11-09): the suite's allowlist is the real one plus the fake model ids
# the fakes use ("m", "test", "fake-judge", ...), so no test needs a real id; tests/test_model_policy.py reads
# the real file explicitly. The helper agents' model calls are opt-in (F2W_HELPER_AGENT_LLM); the legacy-parity
# suites (routing/debate goldens, frozen UI contract) fake the model, so they run with the flag on, and
# tests/runtime/test_helper_llm_opt_in.py covers the default-off path.
os.environ.setdefault("F2W_MODEL_POLICY_FILE",
                      str(Path(__file__).resolve().parent / "fixtures" / "model_policy_test_allowlist.json"))
os.environ.setdefault("F2W_HELPER_AGENT_LLM", "1")
