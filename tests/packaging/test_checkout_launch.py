"""T04: checkout launches keep working without sys.path hacks in library code."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KG_RAG_", "F2W_", "CBORG_"))}
    env.update(KG_RAG_RETRIEVAL_BACKEND="lexical", **extra)
    return env


def test_importing_f2w_agent_does_not_touch_sys_path():
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "before = list(sys.path)\n"
        "import app.modules.f2w_agent, app.modules.extract_terms\n"
        "print(json.dumps({'same': sys.path == before,\n"
        "                  'top': sorted(m for m in sys.modules if m.split('.')[0] in\n"
        "                                {'modules', 'download_pdfs', 'cborg_http', 'project_config'})}))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True, cwd="/", env=_env())
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result == {"same": True, "top": []}


@pytest.mark.parametrize("script", ["app/modules/extract_terms.py", "app/modules/kg_rag_api.py"])
def test_documented_direct_script_launch_still_works(script, tmp_path):
    """README documents `python3 app/modules/<module>.py`; it re-dispatches to the package module."""
    out = subprocess.run([sys.executable, str(REPO / script), "--help"], capture_output=True, text=True,
                         cwd=tmp_path, env=_env(), timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "usage:" in out.stdout
