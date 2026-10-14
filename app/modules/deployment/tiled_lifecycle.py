"""Explicit lifecycle of an application-managed Tiled server (``:local`` and the test fixture).

Operations (``docs/adr/tiled-deployment.md`` sections 4.3 and 7; spec section 2):

* :func:`start_managed` launches ``<tiled-python> -I _tiled_entry.py`` in its own Python
  environment, its own process group and a scrubbed environment (no inherited ``TILED_*``,
  ``F2W_*`` or ``.env``), after recording an ownership manifest. It then runs
  :func:`wait_until_ready` (health plus C1-C4/C6).
* :func:`managed_status` reports ``running``/``stopped``/``stale``/``unhealthy`` from the
  manifest without changing anything.
* :func:`stop_managed` sends SIGTERM to the owned process group, waits, then SIGKILL, and
  confirms the PID is gone. Data is always kept.
* :func:`destroy_owned` stops and then deletes the storage of a disposable ``f2w-test``
  instance, only under its fixture base and only when the manifest nonce matches.
* :func:`upgrade_catalog` is the explicit operator command for an older catalog. Nothing
  migrates a catalog implicitly.

Nothing here runs at import time. External mode never imports this module.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .tiled_capabilities import CapabilityReport, check_capabilities, check_health
from .tiled_config import (
    OWNER_TEST,
    PINNED_TILED_VERSION,
    ManagedTiledConfig,
    TiledConfigError,
    TiledDeploymentError,
    generate_api_key,
    read_secret_file,
    write_private_file,
)

ENTRY_SCRIPT = Path(__file__).with_name("_tiled_entry.py")
MANIFEST_SCHEMA = "f2w.tiled.ownership/v1"
DEFAULT_READY_TIMEOUT = 60.0
DEFAULT_STOP_TIMEOUT = 10.0


class TiledStartupError(TiledDeploymentError):
    """The managed server did not become ready; ``detail['log_tail']`` has the log end."""

    code = "startup_failed"


class TiledAlreadyRunning(TiledDeploymentError):
    """A live server already owns this data directory."""

    code = "already_running"


class TiledPortInUse(TiledDeploymentError):
    """Something else listens on the configured port; it is never touched."""

    code = "port_in_use"


class TiledNotOwned(TiledDeploymentError):
    """A process or path is not provably owned by this instance; nothing was done to it."""

    code = "not_owned"


class TiledEnvironmentError(TiledDeploymentError):
    """The Tiled interpreter is missing, is the app's own environment, or has the wrong tiled."""

    code = "wrong_environment"


class TiledCatalogUpgradeNeeded(TiledDeploymentError):
    """The catalog was created by an older Tiled; run the explicit upgrade command."""

    code = "catalog_upgrade_needed"


@dataclass
class ManagedServer:
    """A running managed server started by this process (or found via its manifest)."""

    config: ManagedTiledConfig
    port: int
    pid: int
    nonce: str
    log_path: Path
    process: Optional[subprocess.Popen] = None
    capabilities: Optional[CapabilityReport] = None
    started_utc: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def url(self) -> str:
        """Base URL (loopback)."""
        return f"http://{self.config.host}:{self.port}"

    @property
    def pgid(self) -> int:
        """The server runs as its own session/process-group leader."""
        return self.pid


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == MANIFEST_SCHEMA else None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline(pid: int) -> str:
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        try:
            return proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return ""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5, env={"PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _owns(pid: int, nonce: str) -> bool:
    """True only if ``pid`` is alive and its command line carries this instance's nonce."""
    return bool(nonce) and _pid_alive(pid) and f"--instance {nonce}" in _cmdline(pid)


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def probe_environment(tiled_python: Path, expected_version: str = PINNED_TILED_VERSION) -> Dict[str, str]:
    """Check the isolated Tiled interpreter before launching anything.

    Returns:
        ``{"prefix", "version", "python"}`` of the Tiled environment.

    Raises:
        TiledEnvironmentError: Missing interpreter, the app's own environment, or tiled
            missing / not ``expected_version``.
    """
    import sys

    path = Path(tiled_python)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise TiledEnvironmentError(f"Tiled interpreter {path} does not exist or is not executable")
    code = "import sys, tiled; print(sys.prefix); print(tiled.__version__); print(sys.version.split()[0])"
    try:
        out = subprocess.run([str(path), "-I", "-c", code], capture_output=True, text=True, timeout=60,
                             env=_child_env(Path(tempfile.gettempdir())), cwd="/")
    except (OSError, subprocess.SubprocessError) as exc:
        raise TiledEnvironmentError(f"Cannot run Tiled interpreter {path}: {type(exc).__name__}") from None
    if out.returncode != 0:
        raise TiledEnvironmentError(f"tiled is not importable with {path}", detail={"stderr": out.stderr[-2000:]})
    prefix, version, pyver = (out.stdout.strip().splitlines() + ["", "", ""])[:3]
    if os.path.realpath(prefix) == os.path.realpath(sys.prefix):
        raise TiledEnvironmentError(
            f"Tiled must run in its own environment, not the app's ({prefix}); see optional-dependencies.md"
        )
    if version != expected_version:
        raise TiledEnvironmentError(
            f"{path} has tiled {version}; F2W requires exactly {expected_version} (O-T1)",
            code="unsupported_version",
            detail={"found": version, "expected": expected_version},
        )
    return {"prefix": prefix, "version": version, "python": pyver}


def _child_env(home: Path) -> Dict[str, str]:
    """A minimal explicit environment: nothing inherited (no TILED_*, F2W_*, proxies, .env)."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home), "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8"}


def _prepare_secrets(cfg: ManagedTiledConfig) -> None:
    cfg.secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.secrets_dir, 0o700)
    if cfg.authn is not None:
        if not cfg.authn_secrets_file.exists():
            data = {
                "provider": cfg.authn.provider,
                "users_to_passwords": {name: generate_api_key() for name in cfg.authn.users},
                "secret_keys": [generate_api_key()],
            }
            write_private_file(cfg.authn_secrets_file, json.dumps(data))
        return
    key_file = cfg.effective_writer_secret_file
    if not key_file.exists():
        if cfg.writer_secret_file is not None:
            raise TiledConfigError(f"Writer secret file {key_file} does not exist")
        # First provisioning: generate the local writer key inside the data mount.
        write_private_file(key_file, generate_api_key() + "\n")
    read_secret_file(key_file, alphanumeric=True)


def writer_credential(cfg: ManagedTiledConfig) -> Optional[str]:
    """The single-user writer API key of a managed instance (``None`` in multi-user mode)."""
    if cfg.authn is not None:
        return None
    return read_secret_file(cfg.effective_writer_secret_file, alphanumeric=True)


def authn_passwords(cfg: ManagedTiledConfig) -> Dict[str, str]:
    """Generated user passwords of a multi-user managed instance (test fixtures only)."""
    if cfg.authn is None:
        return {}
    return dict(json.loads(cfg.authn_secrets_file.read_text(encoding="utf-8"))["users_to_passwords"])


def _log_tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _classify_exit(server: ManagedServer, tail: str) -> TiledDeploymentError:
    code = server.process.returncode if server.process else None
    if code == 3 or "F2W_TILED_ERROR unsupported_version" in tail:
        return TiledEnvironmentError("The Tiled interpreter has an unsupported tiled version",
                                     code="unsupported_version", detail={"log_tail": tail})
    if "DatabaseUpgradeNeeded" in tail or "upgrade-database" in tail:
        return TiledCatalogUpgradeNeeded(
            f"Catalog {server.config.catalog_path} needs an explicit upgrade: "
            f"scripts/tiled_server.py upgrade-catalog --data-dir {server.config.data_dir}",
            detail={"log_tail": tail},
        )
    if "address already in use" in tail.lower() or "Errno 48" in tail or "Errno 98" in tail:
        return TiledPortInUse(f"Port {server.port} became busy before Tiled could bind it",
                              detail={"log_tail": tail})
    return TiledStartupError(f"Tiled exited during startup (exit code {code})", detail={"log_tail": tail})


def start_managed(
    cfg: ManagedTiledConfig,
    *,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
    port_retries: int = 2,
) -> ManagedServer:
    """Start the managed server for ``cfg`` and wait until C1-C4/C6 pass.

    Args:
        cfg: Managed instance configuration; ``cfg.data_dir`` must already exist.
        ready_timeout: Seconds to wait for readiness.
        port_retries: Extra attempts with a new ephemeral port when ``cfg.port == 0`` and
            the chosen port was taken in the meantime.

    Returns:
        The ready server (its process is a child of this process).

    Raises:
        TiledConfigError, TiledEnvironmentError, TiledAlreadyRunning, TiledPortInUse,
        TiledCatalogUpgradeNeeded, TiledStartupError, and the capability errors. On any
        failure the process this call started is stopped; data is kept.
    """
    if not cfg.data_dir.is_dir():
        raise TiledConfigError(
            f"Tiled data directory {cfg.data_dir} does not exist; mount persistent storage there "
            "(it is never created inside the image)"
        )
    env_info = probe_environment(cfg.tiled_python, cfg.expected_version)
    previous = _read_manifest(cfg.manifest_path)
    if previous and previous.get("state") in ("starting", "running"):
        if _owns(int(previous.get("pid") or 0), str(previous.get("nonce") or "")):
            raise TiledAlreadyRunning(
                f"Tiled for {cfg.data_dir} is already running "
                f"(pid {previous.get('pid')}, port {previous.get('port')})",
                detail={"pid": previous.get("pid"), "port": previous.get("port")},
            )
        # Killed or crashed earlier: keep the old manifest for diagnosis and recover.
        _write_json(cfg.run_dir / f"stale-{int(time.time() * 1000)}.json", previous)
    for sub in (cfg.storage_dir, cfg.run_dir, cfg.log_dir):
        sub.mkdir(parents=True, exist_ok=True)
    _prepare_secrets(cfg)

    attempts = 1 + (max(0, port_retries) if cfg.port == 0 else 0)
    last_error: Optional[TiledDeploymentError] = None
    for _ in range(attempts):
        port = _free_port(cfg.host) if cfg.port == 0 else cfg.port
        if _port_listening(cfg.host, port):
            raise TiledPortInUse(f"{cfg.host}:{port} is already in use by another process; not touching it")
        server = _spawn(cfg, port, env_info)
        try:
            wait_until_ready(server, timeout=ready_timeout)
            return server
        except TiledPortInUse as exc:
            last_error = exc
            stop_managed(server)
            if cfg.port != 0:
                raise
        except BaseException:
            stop_managed(server)
            raise
    assert last_error is not None
    raise last_error


def _spawn(cfg: ManagedTiledConfig, port: int, env_info: Dict[str, str]) -> ManagedServer:
    init_catalog = not cfg.catalog_path.exists()
    server_config = _render_yaml(cfg, port, init_catalog)
    cfg.server_config_path.write_text(server_config, encoding="utf-8")
    nonce = uuid.uuid4().hex
    log_path = cfg.log_dir / f"tiled-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.log"
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "run_id": uuid.uuid4().hex,
        "nonce": nonce,
        "owner": cfg.owner,
        "profile": cfg.profile,
        "root": str(cfg.data_dir),
        "host": cfg.host,
        "port": port,
        "pid": None,
        "pgid": None,
        "state": "starting",
        "created_utc": _utc(),
        "tiled_version": env_info["version"],
        "tiled_prefix": env_info["prefix"],
        "catalog_initialized_now": init_catalog,
        "log": str(log_path),
    }
    _write_json(cfg.manifest_path, manifest)  # recorded before the process exists
    argv = [str(cfg.tiled_python), "-I", str(ENTRY_SCRIPT), "--config", str(cfg.server_config_path),
            "--expected-version", cfg.expected_version, "--instance", nonce]
    if cfg.authn is None:
        argv += ["--api-key-file", str(cfg.effective_writer_secret_file)]
    else:
        argv += ["--authn-secrets-file", str(cfg.authn_secrets_file)]
    home = cfg.run_dir
    with open(log_path, "ab") as log:
        process = subprocess.Popen(argv, cwd=str(home), env=_child_env(home), stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   close_fds=True)
    manifest.update({"pid": process.pid, "pgid": process.pid})
    _write_json(cfg.manifest_path, manifest)
    return ManagedServer(config=cfg, port=port, pid=process.pid, nonce=nonce, log_path=log_path,
                         process=process, started_utc=manifest["created_utc"])


def _render_yaml(cfg: ManagedTiledConfig, port: int, init_catalog: bool) -> str:
    from .tiled_config import render_server_config

    header = "# Generated by f2w tiled_lifecycle; contains no secrets (keys are injected at start).\n"
    return header + yaml.safe_dump(render_server_config(cfg, port=port, init_catalog=init_catalog),
                                   sort_keys=False)


def wait_until_ready(server: ManagedServer, *, timeout: float = DEFAULT_READY_TIMEOUT) -> CapabilityReport:
    """Poll ``/healthz`` then run the capability checks; mark the manifest ``running``.

    Raises:
        TiledStartupError (with log tail) on timeout or early exit; the capability errors
        if the server answers but is not usable.
    """
    deadline = time.monotonic() + timeout
    while True:
        if server.process is not None and server.process.poll() is not None:
            raise _classify_exit(server, _log_tail(server.log_path))
        if server.process is None and not _owns(server.pid, server.nonce):
            raise TiledStartupError(f"Tiled process {server.pid} is gone",
                                    detail={"log_tail": _log_tail(server.log_path)})
        if check_health(server.url, timeout=1.0):
            break
        if time.monotonic() > deadline:
            raise TiledStartupError(f"Tiled at {server.url} not healthy after {timeout:.0f}s",
                                    detail={"log_tail": _log_tail(server.log_path)})
        time.sleep(0.25)
    report = check_capabilities(server.url, expected_version=server.config.expected_version,
                                credential=writer_credential(server.config), timeout=10.0)
    server.capabilities = report
    manifest = _read_manifest(server.config.manifest_path) or {}
    if manifest.get("nonce") == server.nonce:
        manifest.update({"state": "running", "ready_utc": _utc(), "capability_hash": report.capability_hash})
        _write_json(server.config.manifest_path, manifest)
    return report


def managed_status(cfg: ManagedTiledConfig) -> Dict[str, Any]:
    """Describe the managed instance without changing anything.

    Returns:
        ``{"state": "running"|"unhealthy"|"stale"|"stopped"|"absent", "pid", "port", "url", ...}``.
    """
    manifest = _read_manifest(cfg.manifest_path)
    if manifest is None:
        return {"state": "absent", "data_dir": str(cfg.data_dir), "catalog_exists": cfg.catalog_path.exists()}
    pid, nonce, port = int(manifest.get("pid") or 0), str(manifest.get("nonce") or ""), manifest.get("port")
    url = f"http://{cfg.host}:{port}" if port else None
    base = {"pid": pid or None, "port": port, "url": url, "owner": manifest.get("owner"),
            "data_dir": str(cfg.data_dir), "catalog_exists": cfg.catalog_path.exists()}
    if manifest.get("state") == "stopped":
        return {"state": "stopped", **base}
    if not _owns(pid, nonce):
        return {"state": "stale", **base}
    return {"state": "running" if url and check_health(url) else "unhealthy", **base}


def find_managed(cfg: ManagedTiledConfig) -> Optional[ManagedServer]:
    """Return the live server recorded in ``cfg``'s manifest, if this instance owns one."""
    manifest = _read_manifest(cfg.manifest_path)
    if not manifest:
        return None
    pid, nonce = int(manifest.get("pid") or 0), str(manifest.get("nonce") or "")
    if manifest.get("state") == "stopped" or not _owns(pid, nonce):
        return None
    return ManagedServer(config=cfg, port=int(manifest["port"]), pid=pid, nonce=nonce,
                         log_path=Path(manifest.get("log") or cfg.log_dir / "unknown.log"),
                         started_utc=str(manifest.get("created_utc") or ""))


def _wait_gone(server: ManagedServer, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.process is not None:
            if server.process.poll() is not None:
                return True
        elif not _pid_alive(server.pid):
            return True
        time.sleep(0.1)
    return False


def stop_managed(server: ManagedServer, *, timeout: float = DEFAULT_STOP_TIMEOUT) -> Dict[str, Any]:
    """Stop an owned server: SIGTERM its process group, wait, SIGKILL if needed. Keeps data.

    Returns:
        A stop receipt ``{"stopped", "signal", "pid", "port", "data_dir", "data_retained"}``.

    Raises:
        TiledNotOwned: If the PID is alive but not provably this instance's process.
    """
    receipt: Dict[str, Any] = {"pid": server.pid, "port": server.port, "data_dir": str(server.config.data_dir),
                               "data_retained": True, "signal": None, "stopped_utc": None}
    running = (server.process.poll() is None) if server.process is not None else _pid_alive(server.pid)
    if running:
        if server.process is None and not _owns(server.pid, server.nonce):
            raise TiledNotOwned(f"PID {server.pid} is not the Tiled server this instance started; "
                                "not signalling it")
        for sig, wait in ((signal.SIGTERM, timeout), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(server.pgid, sig)
            except ProcessLookupError:
                break
            receipt["signal"] = sig.name
            if _wait_gone(server, wait):
                break
    if server.process is not None and server.process.poll() is None:
        server.process.wait(timeout=5)
    gone = (server.process.poll() is not None) if server.process is not None else not _pid_alive(server.pid)
    receipt["stopped"] = gone
    receipt["stopped_utc"] = _utc()
    manifest = _read_manifest(server.config.manifest_path)
    if gone and manifest and manifest.get("nonce") == server.nonce:
        manifest.update({"state": "stopped", "stopped_utc": receipt["stopped_utc"],
                         "stop_signal": receipt["signal"]})
        _write_json(server.config.manifest_path, manifest)
    if not gone:
        raise TiledStartupError(f"Tiled process {server.pid} did not exit after SIGKILL", detail=receipt)
    return receipt


def destroy_owned(server: ManagedServer, *, base: Path) -> Dict[str, Any]:
    """Stop a disposable test instance and delete its storage, if and only if it is owned.

    Deletes ``server.config.data_dir`` only when all hold: owner ``f2w-test``; the manifest
    on disk carries this server's nonce; the directory is a real directory (not a symlink)
    strictly inside ``base`` after resolving symlinks; and no symlink inside points out.

    Returns:
        A teardown receipt (also safe to write outside the deleted root).
    """
    cfg = server.config
    receipt: Dict[str, Any] = {"root": str(cfg.data_dir), "nonce": server.nonce, "removed": False}
    if cfg.owner != OWNER_TEST:
        raise TiledNotOwned(f"{cfg.data_dir} is not a disposable test instance (owner {cfg.owner})")
    receipt["stop"] = stop_managed(server)
    manifest = _read_manifest(cfg.manifest_path)
    if not manifest or manifest.get("nonce") != server.nonce or manifest.get("owner") != OWNER_TEST:
        raise TiledNotOwned(f"Ownership manifest of {cfg.data_dir} does not match this instance")
    root = Path(os.path.realpath(cfg.data_dir))
    base_real = Path(os.path.realpath(base))
    if cfg.data_dir.is_symlink() or root == base_real or base_real not in root.parents:
        raise TiledNotOwned(f"{cfg.data_dir} is not inside the fixture base {base}")
    escapes: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                target = Path(os.path.realpath(full))
                if root not in target.parents and target != root:
                    escapes.append(str(full))
    if escapes:
        raise TiledNotOwned(f"{cfg.data_dir} contains symlinks leaving the instance root: {escapes[:3]}")
    shutil.rmtree(root)
    receipt.update({"removed": True, "removed_utc": _utc()})
    return receipt


def upgrade_catalog(cfg: ManagedTiledConfig, *, timeout: float = 300.0) -> Dict[str, Any]:
    """Explicit operator command: upgrade an older catalog with the pinned Tiled.

    Refuses while a server owns the data dir. Back up ``catalog.db`` first.
    """
    if find_managed(cfg) is not None:
        raise TiledAlreadyRunning(f"Stop Tiled for {cfg.data_dir} before upgrading its catalog")
    if not cfg.catalog_path.exists():
        raise TiledConfigError(f"No catalog at {cfg.catalog_path}")
    probe_environment(cfg.tiled_python, cfg.expected_version)
    argv = [str(cfg.tiled_python), "-I", "-m", "tiled", "catalog", "upgrade-database",
            f"sqlite+aiosqlite:///{cfg.catalog_path}"]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=str(cfg.data_dir),
                         env=_child_env(cfg.run_dir if cfg.run_dir.exists() else cfg.data_dir))
    if out.returncode != 0:
        raise TiledDeploymentError("Catalog upgrade failed", code="catalog_upgrade_failed",
                                   detail={"stderr": out.stderr[-4000:]})
    return {"catalog": str(cfg.catalog_path), "upgraded_utc": _utc(), "output": out.stdout[-2000:]}
