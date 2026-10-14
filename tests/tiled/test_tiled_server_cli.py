"""T20B: scripts/tiled_server.py operator facade."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tiled_server import tiled_python

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "scripts" / "tiled_server.py"


def _cli(*args, env):
    base = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    out = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env={**base, **env},
                         timeout=180, cwd=str(REPO))
    return out.returncode, out.stdout, out.stderr


def test_cli_rejects_lifecycle_on_external_profile():
    code, out, err = _cli("start", env={"F2W_DEPLOYMENT_PROFILE": "als-prod"})
    assert code == 2 and json.loads(err.splitlines()[0])["error"] == "not_managed"


def test_cli_reports_config_errors_as_json():
    code, _, err = _cli("status", env={})
    assert code == 2 and json.loads(err.splitlines()[0])["error"] == "config_invalid"
    code, _, err = _cli("check", "--profile", "test", "--mode", "external", "--url", "https://tiled.als.lbl.gov",
                        env={})
    assert code == 2 and "must not target an ALS" in json.loads(err.splitlines()[0])["message"]


def test_cli_status_absent(tmp_path):
    code, out, _ = _cli("status", "--profile", "test", "--data-dir", str(tmp_path), "--python", "/x", env={})
    assert code == 0 and json.loads(out)["state"] == "absent"


@pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
def test_cli_start_check_stop_keeps_data(tiled_base):
    root = tiled_base / "cli-instance"
    root.mkdir()
    env = {"F2W_DEPLOYMENT_PROFILE": "test", "F2W_TILED_DATA_DIR": str(root),
           "F2W_TILED_PYTHON": str(tiled_python()), "F2W_TILED_PORT": "0"}
    code, out, err = _cli("start", env=env)
    assert code == 0, err
    started = json.loads(out)
    try:
        assert started["capabilities"]["library_version"] == "0.2.18"
        assert json.loads(_cli("status", env=env)[1])["state"] == "running"
        code, out, err = _cli("check", env=env)
        assert code == 0, err
        assert json.loads(out)["capabilities"]["writer_principal"] == "single-user-api-key"
        code, out, _ = _cli("start", env=env)
        assert code == 2
    finally:
        code, out, err = _cli("stop", env=env)
    assert code == 0, err
    assert json.loads(out)["stopped"] is True and json.loads(out)["data_retained"] is True
    assert (root / "catalog.db").exists()
    assert json.loads(_cli("status", env=env)[1])["state"] == "stopped"
