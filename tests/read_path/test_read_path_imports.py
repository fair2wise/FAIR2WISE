"""T03: importing the application service and runtime bridge has no side effects."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NEW_FILES = [REPO / "app" / "modules" / "application.py",
             *sorted((REPO / "app" / "modules" / "runtime_bridge").glob("*.py"))]
HEAVY = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langchain_openai", "tiled", "dotenv",
         "academy", "langgraph", "langchain_core", "fastapi", "requests", "httpx", "aiohttp"}

_PROBE = """
import gc, json, os, sys
sys.path.insert(0, {repo!r})
env = dict(os.environ); cwd = sorted(os.listdir('.')); before = set(sys.modules)
import app.modules.runtime_bridge.selection
selection_only = sorted(set(sys.modules) - before)
import app.modules.application, app.modules.runtime_bridge as rb
for name in ("mode", "retrieval", "qa_tool", "bootstrap", "legacy_adapter", "selection"):
    __import__("app.modules.runtime_bridge." + name)
new = sorted(set(sys.modules) - before)
from app.modules.agent_runtime.registry import ToolRegistry
from app.modules.agent_runtime.skills import SkillRegistry
live = [type(o).__name__ for o in gc.get_objects() if isinstance(o, (ToolRegistry, SkillRegistry))]
print(json.dumps({{"selection_only": selection_only, "new": new, "live": live,
                  "env_changed": dict(os.environ) != env, "cwd_changed": sorted(os.listdir('.')) != cwd}}))
"""


def test_import_has_no_side_effects():
    out = subprocess.run([sys.executable, "-I", "-c", _PROBE.format(repo=str(REPO))], capture_output=True,
                         text=True, check=True, cwd="/", env={"PATH": "/usr/bin:/bin"})
    result = json.loads(out.stdout.strip().splitlines()[-1])
    roots = {m.split(".")[0] for m in result["new"]}
    assert not HEAVY & roots, HEAVY & roots
    # The startup hook api.py imports pulls in only the flag parser.
    assert [m for m in result["selection_only"] if m.startswith("app")] == [
        "app", "app.modules", "app.modules.runtime_bridge", "app.modules.runtime_bridge.mode",
        "app.modules.runtime_bridge.selection"]
    legacy = [m for m in result["new"] if m.startswith("app.modules.") and m.split(".")[2] in {
        "f2w_agent", "kg_rag_api", "hybrid_rag", "term_extractor", "tiled_graph", "extract_terms"}]
    assert legacy == []  # the retrieval agent loads only when an application builds it
    assert result["live"] == []
    assert result["env_changed"] is False and result["cwd_changed"] is False


def test_new_modules_import_no_forge_experimental_or_provider_sdk():
    banned = {"forge", "als_knowledge_agent", "reduction_api", "tiled_agentic", "openai", "langchain_openai",
              "dotenv", "fastapi"}
    offenders = []
    for path in NEW_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in banned]
    assert offenders == []
