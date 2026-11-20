"""Startup re-securing of generated Tiled secrets (kubelet ``fsGroup`` widens existing files on every pod start).

The key is created 0600; on a later start the volume driver makes it group-writable and AUD-15 would refuse it.
``_prepare_secrets`` tightens only a regular file owned by the running uid and never loosens the check.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat

import pytest

from app.modules.deployment import tiled_config as tc
from app.modules.deployment import tiled_lifecycle as lc


def _cfg(tmp_path: Path, **extra) -> tc.ManagedTiledConfig:
    return tc.ManagedTiledConfig(profile="local", data_dir=tmp_path, tiled_python=Path("/nonexistent/python"),
                                 **extra)


def _widened_key(tmp_path: Path, mode: int = 0o660) -> Path:
    key = tmp_path / "secrets" / "writer.key"
    tc.write_private_file(key, "abc123\n", exclusive=True)
    key.parent.chmod(0o2777)  # the setgid hostPath root, as kubelet leaves it
    key.chmod(mode)
    return key


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("mode", [0o660, 0o664, 0o640, 0o606])
def test_widened_key_is_tightened_and_start_proceeds(tmp_path, caplog, mode):
    key = _widened_key(tmp_path, mode)
    with caplog.at_level(logging.WARNING, logger=lc.logger.name):
        lc._prepare_secrets(_cfg(tmp_path))
    assert _mode(key) == 0o600
    assert _mode(key.parent) == 0o700  # the supervisor's own directory tightening is unchanged
    assert tc.read_secret_file(key, alphanumeric=True) == "abc123"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert str(key) in text and f"{mode:04o}" in text and "abc123" not in text


def test_a_correct_key_is_left_alone_and_silent(tmp_path, caplog):
    key = _widened_key(tmp_path, 0o400)
    with caplog.at_level(logging.WARNING, logger=lc.logger.name):
        lc._prepare_secrets(_cfg(tmp_path))
    assert _mode(key) == 0o400 and not caplog.records


def test_widened_authn_secrets_are_tightened(tmp_path):
    authn = tc.AuthnSpec(provider="f2wtest", users=("admin",))
    cfg = _cfg(tmp_path, authn=authn)
    lc._prepare_secrets(cfg)
    cfg.authn_secrets_file.chmod(0o660)
    lc._prepare_secrets(cfg)
    assert _mode(cfg.authn_secrets_file) == 0o600


def test_symlinked_key_is_still_refused_and_its_target_untouched(tmp_path):
    real = tmp_path / "elsewhere.key"
    tc.write_private_file(real, "abc123\n")
    real.chmod(0o660)
    key = tmp_path / "secrets" / "writer.key"
    key.parent.mkdir()
    key.symlink_to(real)
    with pytest.raises(tc.TiledConfigError, match="group- or world-accessible"):
        lc._prepare_secrets(_cfg(tmp_path))
    assert _mode(real) == 0o660


def test_foreign_owned_key_is_still_refused(tmp_path, monkeypatch):
    key = _widened_key(tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: key.stat().st_uid + 1)
    with pytest.raises(tc.TiledConfigError):
        lc._prepare_secrets(_cfg(tmp_path))
    assert _mode(key) == 0o660


def test_a_failing_chmod_keeps_the_refusal(tmp_path, monkeypatch):
    key = _widened_key(tmp_path)
    real_chmod = Path.chmod

    def deny(self, mode, **kw):
        if self == key:
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(self, mode, **kw)

    monkeypatch.setattr(Path, "chmod", deny)
    with pytest.raises(tc.TiledConfigError, match="group- or world-accessible") as err:
        lc._prepare_secrets(_cfg(tmp_path))
    assert "fsGroup" in str(err.value) and "abc123" not in str(err.value)


def test_a_configured_secret_mount_is_never_touched(tmp_path):
    mount = tmp_path / "mount" / "tiled_writer"
    tc.write_private_file(mount, "abc123\n")
    mount.chmod(0o660)
    with pytest.raises(tc.TiledConfigError, match="group- or world-accessible"):
        lc._prepare_secrets(_cfg(tmp_path, writer_secret_file=mount))
    assert _mode(mount) == 0o660
