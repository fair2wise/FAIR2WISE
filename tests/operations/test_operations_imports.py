"""T19B: importing app.modules.operations (and the bootstrap that names it) has no side effects."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_PROBE = """
import json, os, sys
sys.path.insert(0, {repo!r})
env = dict(os.environ); before = set(sys.modules)
import app.modules.operations as ops
import app.modules.operations.papers, app.modules.operations.summary_compat
import app.modules.runtime_bridge.bootstrap
ops.PaperSummaryTool, ops.LegacySummaryPath
new = sorted(set(sys.modules) - before)
print(json.dumps({{"new": new, "env_changed": dict(os.environ) != env}}))
"""


def test_operations_import_loads_no_agent_model_or_provider_code():
    out = subprocess.run([sys.executable, "-I", "-c", _PROBE.format(repo=str(REPO))], capture_output=True,
                         text=True, check=True, cwd="/", env={"PATH": "/usr/bin:/bin"})
    result = json.loads(out.stdout.strip().splitlines()[-1])
    roots = {m.split(".")[0] for m in result["new"]}
    assert not roots & {"academy", "fitz", "dotenv", "openai", "langgraph", "langchain_core", "fastapi", "torch"}
    assert [m for m in result["new"] if m.startswith("app.modules.f2w_agent")] == []
    assert result["env_changed"] is False
