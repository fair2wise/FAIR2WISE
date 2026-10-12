"""T02: importing the runtime has no side effects and pulls in no FORGE/experimental code."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = [REPO / "app" / "modules" / "agent_runtime", REPO / "app" / "modules" / "tools"]
HEAVY = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langchain_openai", "tiled", "dotenv",
         "academy", "requests", "httpx", "aiosqlite"}

_PROBE = """
import gc, json, os, sys
sys.path.insert(0, {repo!r})
env = dict(os.environ); cwd = sorted(os.listdir('.')); before = set(sys.modules)
import app.modules.agent_runtime as rt
import app.modules.tools, app.modules.tools.bootstrap
bare = sorted(set(sys.modules) - before)
from app.modules.agent_runtime.registry import ToolRegistry
from app.modules.agent_runtime.skills import SkillRegistry
live = [type(o).__name__ for o in gc.get_objects() if isinstance(o, (ToolRegistry, SkillRegistry))]
rt.build_literature_supervisor  # lazy attribute: loads langgraph, must still load no provider
full = sorted(set(sys.modules) - before)
print(json.dumps({{"bare": bare, "full": full, "live": live, "env_changed": dict(os.environ) != env,
                  "cwd_changed": sorted(os.listdir('.')) != cwd}}))
"""


def _probe():
    out = subprocess.run([sys.executable, "-I", "-c", _PROBE.format(repo=str(REPO))], capture_output=True,
                         text=True, check=True, cwd="/", env={"PATH": "/usr/bin:/bin"})
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_import_has_no_side_effects():
    result = _probe()
    roots = {m.split(".")[0] for m in result["bare"]}
    assert not HEAVY & roots, HEAVY & roots
    assert "langgraph" not in roots and "langchain_core" not in roots  # bare import stays light
    app_mods = [m for m in result["bare"] if m.startswith("app")]
    allowed = ("app.modules.contracts", "app.modules.agent_runtime", "app.modules.tools")
    assert all(m in {"app", "app.modules"} or m.startswith(allowed) for m in app_mods), app_mods
    assert result["live"] == []  # importing registers nothing and builds no registry
    assert result["env_changed"] is False and result["cwd_changed"] is False
    full_roots = {m.split(".")[0] for m in result["full"]}
    assert not HEAVY & full_roots, HEAVY & full_roots
    assert not any(m.startswith("app.modules.") and not m.startswith(allowed) for m in result["full"])


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def test_no_forge_experimental_or_provider_imports():
    banned_roots = {"forge", "als_knowledge_agent", "reduction_api", "tiled_agentic", "openai", "langchain_openai",
                    "dotenv"}
    offenders = []
    for base in RUNTIME_DIRS:
        for path in base.rglob("*.py"):
            for name in _imports(path):
                root = name.split(".")[0]
                experimental = any(part in {"simulation", "analysis", "calibration", "catalog"}
                                   for part in name.split("."))
                legacy = name.startswith("app.modules.") and not name.startswith(
                    ("app.modules.contracts", "app.modules.agent_runtime", "app.modules.tools"))
                if root in banned_roots or experimental or legacy:
                    offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert offenders == []


def test_runtime_reads_no_environment():
    offenders = []
    for base in RUNTIME_DIRS:
        for path in base.rglob("*.py"):
            text = path.read_text()
            for needle in ("os.environ", "getenv(", "load_dotenv", "config.yml"):
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO)}: {needle}")
    assert offenders == []


def test_donor_adapted_files_carry_attribution():
    adapted = ["registry.py", "skills.py", "executor.py", "supervisor.py", "state.py", "message_trimming.py",
               "checkpointer.py", "profiles.py"]
    for name in adapted:
        text = (REPO / "app" / "modules" / "agent_runtime" / name).read_text()
        assert "Adapted from als-computing/forge_rsoxs_analysis" in text and "(BSD-3-Clause)" in text, name
    assert "forge_rsoxs_analysis" in (REPO / "app" / "modules" / "tools" / "bootstrap.py").read_text()
