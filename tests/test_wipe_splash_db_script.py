import os
import socket
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wipe_splash_db.sh"


def _port_8081_is_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", 8081)) != 0


def _database_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "splash_links"
    repo.mkdir()
    db = repo / "links.sqlite"
    db.write_bytes(b"database contents")
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{db}{suffix}").write_bytes(b"sidecar")
    env = os.environ.copy()
    env["SPLASH_LINKS_REPO"] = str(repo)
    env["SPLASH_LINKS_DB"] = "links.sqlite"
    return db, env


@pytest.mark.skipif(not _port_8081_is_free(), reason="port 8081 is already active")
def test_wipe_splash_db_requires_exact_confirmation(tmp_path):
    db, env = _database_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="no\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "Cancelled" in result.stdout
    assert db.read_bytes() == b"database contents"


@pytest.mark.skipif(not _port_8081_is_free(), reason="port 8081 is already active")
def test_wipe_splash_db_deletes_database_and_sidecars(tmp_path):
    db, env = _database_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="WIPE splash_links\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Splash database wiped" in result.stdout
    assert not db.exists()
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{db}{suffix}").exists()


def test_wipe_splash_db_refuses_database_outside_workspace(tmp_path):
    repo = tmp_path / "splash_links"
    repo.mkdir()
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"keep me")
    env = os.environ.copy()
    env["SPLASH_LINKS_REPO"] = str(repo)
    env["SPLASH_LINKS_DB"] = str(outside)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="WIPE splash_links\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "outside" in result.stderr
    assert outside.read_bytes() == b"keep me"


def test_wipe_splash_db_refuses_running_managed_service(tmp_path):
    db, env = _database_fixture(tmp_path)
    pid_file = tmp_path / "splash.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    env["SPLASH_LINKS_PID_FILE"] = str(pid_file)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="WIPE splash_links\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "is running" in result.stderr
    assert db.read_bytes() == b"database contents"
