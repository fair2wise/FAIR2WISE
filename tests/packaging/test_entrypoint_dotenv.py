"""T04: .env is loaded only by entry points, explicitly, with unchanged precedence."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules import dotenv_entry

REPO = Path(__file__).resolve().parents[2]

# Modules that called load_dotenv() at import (directly or through kg_rag_api) before T04.
FORMER_IMPORT_LOADERS = [
    "app.modules.kg_rag_api",
    "app.modules.extract_terms",
    "app.modules.f2w_agent.cli",
    "app.modules.f2w_agent.api",
    "app.modules.f2w_agent.coordinator",
    "app.modules.launchers.f2w_agent",
]


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KG_RAG_", "F2W_", "CBORG_"))}
    env.update(KG_RAG_RETRIEVAL_BACKEND="lexical", **extra)
    return env


@pytest.mark.parametrize("module", FORMER_IMPORT_LOADERS)
def test_library_import_never_loads_dotenv(module):
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import dotenv, dotenv.main\n"
        "calls = []\n"
        "def rec(*a, **k):\n"
        "    calls.append(sys._getframe(1).f_code.co_filename)\n"
        "dotenv.load_dotenv = dotenv.main.load_dotenv = rec\n"
        "dotenv.find_dotenv = dotenv.main.find_dotenv = rec\n"
        f"import {module}\n"
        "print(json.dumps(calls))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True,
                         cwd="/", env=_clean_env())
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_checkout_lookup_walks_up_from_the_entry_point(tmp_path, monkeypatch):
    monkeypatch.delenv(dotenv_entry.DOTENV_VAR, raising=False)
    (tmp_path / ".env").write_text("X=1\n")
    entry = tmp_path / "a" / "b" / "launcher.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("")
    assert dotenv_entry.is_checkout()
    assert dotenv_entry.find_entrypoint_dotenv(entry) == tmp_path / ".env"
    (tmp_path / "a" / ".env").write_text("X=2\n")
    assert dotenv_entry.find_entrypoint_dotenv(entry) == tmp_path / "a" / ".env"


def test_installed_lookup_uses_cwd_not_package_location(tmp_path, monkeypatch):
    monkeypatch.delenv(dotenv_entry.DOTENV_VAR, raising=False)
    monkeypatch.setattr(dotenv_entry, "is_checkout", lambda: False)
    site = tmp_path / "project" / ".venv" / "site-packages" / "f2w" / "modules"
    site.mkdir(parents=True)
    (tmp_path / "project" / ".env").write_text("FORGE_SECRET=1\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert dotenv_entry.find_entrypoint_dotenv(site / "cli.py") is None
    (work / ".env").write_text("X=1\n")
    assert dotenv_entry.find_entrypoint_dotenv(site / "cli.py") == work / ".env"


def test_explicit_path_and_disable(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    env_file.write_text("X=1\n")
    monkeypatch.setenv(dotenv_entry.DOTENV_VAR, str(env_file))
    assert dotenv_entry.find_entrypoint_dotenv(tmp_path) == env_file
    for off in ("", "0", "none", "OFF"):
        monkeypatch.setenv(dotenv_entry.DOTENV_VAR, off)
        assert dotenv_entry.find_entrypoint_dotenv(tmp_path) is None
    monkeypatch.setenv(dotenv_entry.DOTENV_VAR, str(tmp_path / "missing.env"))
    with pytest.raises(FileNotFoundError):
        dotenv_entry.find_entrypoint_dotenv(tmp_path)


def test_process_environment_wins_over_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("T04_FROM_FILE=file\nT04_BOTH=file\n")
    monkeypatch.setenv(dotenv_entry.DOTENV_VAR, str(env_file))
    monkeypatch.setenv("T04_BOTH", "process")
    monkeypatch.delenv("T04_FROM_FILE", raising=False)
    assert dotenv_entry.load_entrypoint_dotenv(tmp_path) == env_file
    assert os.environ["T04_FROM_FILE"] == "file"
    assert os.environ["T04_BOTH"] == "process"
    monkeypatch.delenv("T04_FROM_FILE")


_LAUNCH = (
    "import json, runpy, sys, contextlib, io\n"
    f"sys.path.insert(0, {str(REPO)!r})\n"
    "sys.argv = ['f2w_agent', 'status']\n"
    "buf = io.StringIO()\n"
    "with contextlib.redirect_stdout(buf):\n"
    "    try:\n"
    "        runpy.run_module('app.modules.launchers.f2w_agent', run_name='__main__', alter_sys=True)\n"
    "    except SystemExit:\n"
    "        pass\n"
    "kg = sys.modules['app.modules.kg_rag_api']\n"
    "status = json.loads(buf.getvalue())\n"
    "print(json.dumps({'workflow_mode': status['workflow_mode'], 'timeout': kg.LLM_TIMEOUT,\n"
    "                  'backend': kg.LLM_BACKEND}))\n"
)


def _launch(tmp_path, **env):
    out = subprocess.run([sys.executable, "-I", "-c", _LAUNCH], capture_output=True, text=True,
                         cwd=tmp_path, env=_clean_env(**env), timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_documented_launcher_reads_dotenv_before_import_time_configuration(tmp_path):
    """`python -m app.modules.launchers.f2w_agent` (start_agent_backend.sh) still sees .env values,
    including the ones kg_rag_api reads at import, and the process environment still wins."""
    env_file = tmp_path / ".env"
    env_file.write_text("F2W_WORKFLOW_MODE=deterministic\nKG_RAG_LLM_TIMEOUT=77\nKG_RAG_BACKEND=cborg\n")
    assert _launch(tmp_path, F2W_DOTENV=str(env_file)) == {
        "workflow_mode": "deterministic", "timeout": 77, "backend": "cborg"}
    assert _launch(tmp_path, F2W_DOTENV=str(env_file), F2W_WORKFLOW_MODE="agentic", KG_RAG_LLM_TIMEOUT="5") == {
        "workflow_mode": "agentic", "timeout": 5, "backend": "cborg"}
    defaults = {"workflow_mode": "agentic", "timeout": 120, "backend": "ollama"}
    assert _launch(tmp_path, F2W_DOTENV="none") == defaults
