"""
Conftest for tests/deepeval/.

Sets environment variables required by kg_rag_api before any import,
and configures pytest-asyncio for async test support.
"""
import os

# Force lexical backend and CPU mode — no GPU or semantic index needed for unit tests
os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib-codex")

# Prevent deepeval from trying to phone home during tests
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("CONFIDENT_AI_AUTO_SEND_TEST_RUN", "NO")
