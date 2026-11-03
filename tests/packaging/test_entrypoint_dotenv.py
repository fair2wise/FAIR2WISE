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
    "app.modules.legacy.extract_terms_cli",
    "app.modules.f2w_agent.cli",
    "app.modules.f2w_agent.api",
    "app.modules.f2w_agent.coordinator",
    "app.modules.launchers.f2w_agent",
]

# Legacy entry points that still called load_dotenv() at import after T04 (AUD-02). They are
# imported by file path below: app/run_pipeline_cborg.py needs app/ on sys.path for its
# ``modules.*`` imports, and scripts/ is not a package.
LEGACY_ENTRY_POINTS = ["app/run_pipeline_cborg.py", "scripts/run.py"]


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


@pytest.mark.parametrize("script", LEGACY_ENTRY_POINTS)
def test_legacy_entry_point_import_loads_no_dotenv(tmp_path, script):
    """AUD-02: importing the two legacy entry points reads no .env, even one next to the cwd,
    next to the script, or named by F2W_DOTENV; only their __main__ block loads it."""
    (tmp_path / ".env").write_text("T28C_SENTINEL=leaked\nCBORG_BASE_URL=https://leaked.invalid\n")
    code = (
        "import importlib.util, json, os, sys\n"
        f"sys.path.insert(0, {str(REPO)!r}); sys.path.insert(0, {str(REPO / 'app')!r})\n"
        "import dotenv, dotenv.main\n"
        "calls = []\n"
        "def rec(*a, **k):\n"
        "    calls.append(sys._getframe(1).f_code.co_filename)\n"
        "dotenv.load_dotenv = dotenv.main.load_dotenv = rec\n"
        "dotenv.find_dotenv = dotenv.main.find_dotenv = rec\n"
        f"spec = importlib.util.spec_from_file_location('legacy_entry', {str(REPO / script)!r})\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "print(json.dumps({'calls': calls, 'sentinel': os.environ.get('T28C_SENTINEL'),\n"
        "                  'base': getattr(mod, 'DEFAULT_CBORG_BASE', None)}))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=tmp_path,
                         env=_clean_env(F2W_DOTENV=str(tmp_path / ".env")), timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen["calls"] == []
    assert seen["sentinel"] is None
    assert seen["base"] in (None, "https://api.cborg.lbl.gov")


def _run_legacy(script, tmp_path, args, **env):
    # As documented (README): plain ``python <script>``, so the script directory is sys.path[0].
    return subprocess.run([sys.executable, str(REPO / script), *args], capture_output=True, text=True,
                          cwd=tmp_path, env=_clean_env(**env), timeout=180)


def test_scripts_run_main_reads_dotenv_under_f2w_dotenv(tmp_path):
    """``python scripts/run.py --dry-run`` needs CBORG_API_KEY: it comes from the F2W_DOTENV file,
    and F2W_DOTENV=none leaves it unset (the former import-time load_dotenv() found the repo .env)."""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "one.pdf").write_bytes(b"%PDF-1.4\n")
    env_file = tmp_path / "custom.env"
    env_file.write_text("CBORG_API_KEY=from-dotenv\n")
    args = ["--pdf-dir", str(pdfs), "--dry-run", "--log-file", str(tmp_path / "run.log")]
    ok = _run_legacy("scripts/run.py", tmp_path, args, F2W_DOTENV=str(env_file))
    assert ok.returncode == 0, ok.stderr[-2000:]
    assert "Dry-run complete" in ok.stderr + ok.stdout
    assert "from-dotenv" not in ok.stderr + ok.stdout  # the key is never logged
    off = _run_legacy("scripts/run.py", tmp_path, args, F2W_DOTENV="none")
    assert off.returncode == 1
    assert "requires CBORG_API_KEY" in off.stderr + off.stdout


def test_run_pipeline_cborg_main_reads_dotenv_under_f2w_dotenv(tmp_path):
    (tmp_path / "paper_25").mkdir()
    env_file = tmp_path / "custom.env"
    env_file.write_text("CBORG_API_KEY=from-dotenv\nCBORG_BASE_URL=https://cborg.example.invalid\n")
    args = ["--pdf-root", str(tmp_path), "--dry-run"]
    ok = _run_legacy("app/run_pipeline_cborg.py", tmp_path, args, F2W_DOTENV=str(env_file))
    assert ok.returncode == 0, ok.stderr[-2000:]
    assert "[DRY-RUN] Checkpoint=25" in ok.stdout
    assert "Using CBORG_BASE_URL = https://cborg.example.invalid" in ok.stdout  # read after the dotenv load
    off = _run_legacy("app/run_pipeline_cborg.py", tmp_path, args, F2W_DOTENV="none")
    assert off.returncode != 0
    assert "CBORG_API_KEY is not set" in off.stderr


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
