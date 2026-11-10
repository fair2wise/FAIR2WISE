"""``scripts/run_local_container.sh`` mounts ``docker/gai.conf`` so the container prefers IPv6 (found 2026-11-10).

CBORG allowlists the host's global IPv6 address only. Docker on Colima gives containers a ULA ``fd00::`` address
and glibc then sorts the IPv4 destination first, so every CBORG call from the container answered HTTP 403
("IP address ... not recognized"). ``docker/gai.conf`` fixes the ordering; the start script has to mount it.
A stub ``docker`` records the arguments, so nothing here starts a container.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_local_container.sh"


def _stub_docker(bin_dir: Path, log: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n', encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path: Path, **extra_env: str) -> str:
    log = tmp_path / "docker.log"
    _stub_docker(tmp_path / "bin", log)
    (tmp_path / "papers").mkdir(parents=True, exist_ok=True)
    envfile = tmp_path / "dotenv"
    envfile.write_text("CBORG_API_KEY=not-a-real-key\n", encoding="utf-8")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path), "F2W_PAPERS_DIR": str(tmp_path / "papers"),
           **extra_env}
    done = subprocess.run(["bash", str(SCRIPT), "f2w:test", str(envfile)], env=env, capture_output=True, text=True,
                          check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    return next(line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("run "))


def test_the_repo_ships_the_gai_conf_with_the_ula_label_fix() -> None:
    text = (ROOT / "docker" / "gai.conf").read_text(encoding="utf-8")
    assert "label fc00::/7      1" in text and "label ::/0          1" in text


def test_gai_conf_is_mounted_read_only_by_default(tmp_path: Path) -> None:
    run_line = _run(tmp_path)
    assert f"-v {ROOT / 'docker' / 'gai.conf'}:/etc/gai.conf:ro" in run_line


def test_gai_conf_can_be_skipped_or_pointed_elsewhere(tmp_path: Path) -> None:
    assert "/etc/gai.conf" not in _run(tmp_path / "skip", F2W_GAI_CONF="none")
    other = tmp_path / "mine.conf"
    other.write_text("label ::/0 1\n", encoding="utf-8")
    assert f"-v {other}:/etc/gai.conf:ro" in _run(tmp_path / "other", F2W_GAI_CONF=str(other))


def test_a_missing_gai_conf_only_warns(tmp_path: Path) -> None:
    run_line = _run(tmp_path, F2W_GAI_CONF=str(tmp_path / "nope.conf"))
    assert "/etc/gai.conf" not in run_line
