#!/usr/bin/env python3
"""FAIR2WISE image supervisor (task T24B): one per container, started by ``tini``.

It reads the image's baked profile (``/etc/fair2wise/profile.json``, a copy of
``docker/profiles/<profile>.json``), fixes the trusted startup environment, runs the
preflight checks, then starts the profile's services one at a time, in order, each only
after the previous one passed its health check:

=========  ============================================  =========  ==========================
service    command                                       required   health
=========  ============================================  =========  ==========================
tiled      ``tiled_service.py`` (``:local`` only)        no         ``GET :8765/healthz``
api        ``api_service.py`` on ``127.0.0.1:8090``      yes        ``GET :8090/health``
worker     ``python -m app.modules.workers``             yes        alive for a grace period
gateway    nginx with ``docker/nginx.conf``              yes        ``GET :8080/fair2wise/healthz``
=========  ============================================  =========  ==========================

Rules (``DEPLOYMENT_STRATEGY.md`` section 2, ``docker_profile_contract.md`` sections 5-6):

* **Profile fixed per image.** ``F2W_DEPLOYMENT_PROFILE`` and the other ``locked_env``
  values may be unset or equal to the baked value; anything else stops the container
  before a process starts. ``forbidden_env`` must be unset (for example managed-Tiled
  settings on an ALS image). ``stripped_env`` (legacy ``TILED_URI``/``TILED_API_KEY``/
  ``F2W_LIVE_TILED``...) is removed from every child, so an ambient dotenv cannot
  redirect a deployed image.
* **No implicit dotenv.** ``F2W_DOTENV`` naming a file loads exactly that file once,
  here, without overriding the environment, before the profile rules are applied; every
  child then gets ``F2W_DOTENV=off``. Unset or ``off``: nothing is loaded.
* **Supervision.** Each child runs in its own session/process group. A *required* child
  that exits (or fails its startup health check) stops everything and the supervisor
  exits non-zero. A non-required child (managed Tiled) is restarted with exponential
  backoff up to ``restart.max`` times, never for an exit code in
  ``no_restart_exit_codes``; after that it is marked ``failed`` in the status file and
  the application keeps serving JSON workflows (graph publication not ready).
* **Termination.** SIGTERM/SIGINT stop services in reverse order (gateway first, Tiled
  last). Each process group gets its ``stop_signal``, then SIGKILL after
  ``stop_timeout`` seconds. The worker re-queues running jobs on SIGTERM (T10).

Optional profile keys (used by the ``cluster`` profile, absent from the others, whose behaviour is
unchanged): ``deployment_profile`` (the ``F2W_DEPLOYMENT_PROFILE`` value when it differs from the
image profile name), ``enum_env`` (names restricted to listed values, refused with 78 otherwise),
``when_env`` on a service or on a preflight step given as ``{"argv": [...], "when_env": {...}}``
(it is skipped unless every named variable has one of the listed values), and
``no_proxy_append`` (entries added to ``NO_PROXY``/``no_proxy`` so internal traffic is never proxied).

OpenTelemetry (any profile, only when tracing is on): with ``F2W_OTEL_ENABLED`` (or the alias ``OTEL_ENABLED``)
truthy and an OTLP endpoint whose host is not a public name (a cluster Service such as
``jaeger-collector.observability.svc``, a single-label hub name such as ``otel-collector``, a private or
loopback address), that host is added to ``NO_PROXY`` so spans reach the collector directly and not through
an egress proxy that would refuse it. Nothing changes while tracing is off.

Exit codes: 0 clean stop; 1 stop needed SIGKILL or a child reported an unclean stop;
70 a required child died; 71 a required child failed its startup health check;
78 configuration or preflight refused (no service was started).

Standard library only (``python-dotenv`` is imported lazily, only for ``F2W_DOTENV``).
Importing this module has no side effects.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

PROFILE_SCHEMA = "f2w.image.profile/v1"
DEFAULT_PROFILE_FILE = "/etc/fair2wise/profile.json"
STATUS_ENV = "F2W_SUPERVISOR_STATUS"
DEFAULT_STATUS_FILE = "/run/fair2wise/supervisor.json"
DOTENV_VAR = "F2W_DOTENV"
DOTENV_DISABLED = frozenset({"", "0", "none", "off", "false"})
#: Set by the supervisor for children (preflight reads the profile it was started with).
PROFILE_FILE_VAR = "F2W_PROFILE_FILE"
PYTHON_TOKEN = "@python"

EXIT_OK = 0
EXIT_UNCLEAN_STOP = 1
EXIT_CHILD_FAILED = 70
EXIT_STARTUP_FAILED = 71
EXIT_CONFIG = 78

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SIGNALS = {"SIGTERM": signal.SIGTERM, "SIGINT": signal.SIGINT, "SIGQUIT": signal.SIGQUIT}


class ProfileError(ValueError):
    """The profile file or the startup environment is refused; nothing may start."""


def log(event: str, **fields: Any) -> None:
    """One structured line on stderr (no secret values are ever passed in)."""
    parts = " ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}" for k, v in fields.items())
    print(f"f2w-supervisor: {event} {parts}".rstrip(), file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- profile


@dataclass(frozen=True)
class Health:
    """How a service proves it is up: ``http`` (2xx from a URL) or ``alive_for`` seconds."""

    http: Optional[str] = None
    alive_for: float = 0.0
    timeout: float = 60.0
    interval: float = 0.5


@dataclass(frozen=True)
class Service:
    name: str
    argv: Tuple[str, ...]
    required: bool = True
    health: Health = field(default_factory=Health)
    restart_max: int = 0
    restart_backoff: float = 2.0
    no_restart_exit_codes: Tuple[int, ...] = ()
    stop_signal: int = signal.SIGTERM
    stop_timeout: float = 15.0
    cwd: Optional[str] = None
    when_env: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    """A validated image profile (``docker/profiles/<name>.json``)."""

    profile: str
    tiled_mode: str
    services: Tuple[Service, ...]
    preflight: Tuple[Tuple[str, ...], ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    locked_env: Mapping[str, str] = field(default_factory=dict)
    forbidden_env: Tuple[str, ...] = ()
    stripped_env: Tuple[str, ...] = ()
    cwd: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)
    deployment_profile: str = ""
    enum_env: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    no_proxy_append: Tuple[str, ...] = ()
    preflight_when: Tuple[Mapping[str, Tuple[str, ...]], ...] = ()


def _strs(value: Any, what: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ProfileError(f"{what} must be a list of non-empty strings")
    return tuple(value)


def _str_map(value: Any, what: str) -> Dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ProfileError(f"{what} must map names to strings")
    return dict(value)


def _when(value: Any, what: str) -> Dict[str, Tuple[str, ...]]:
    """Parse a ``{NAME: [allowed values]}`` condition (values compared lower-case, stripped)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProfileError(f"{what} must map names to lists of values")
    return {str(k): tuple(v.strip().lower() for v in _strs(vals, f"{what}[{k}]")) for k, vals in value.items()}


def condition_holds(when: Mapping[str, Tuple[str, ...]], env: Mapping[str, str]) -> bool:
    """Whether every ``when_env`` entry matches ``env`` (an empty condition always holds)."""
    return all((env.get(name) or "").strip().lower() in allowed for name, allowed in when.items())


def parse_profile(data: Mapping[str, Any]) -> Profile:
    """Validate a profile document.

    Raises:
        ProfileError: On an unknown schema, a missing field or an inconsistent service list.
    """
    if not isinstance(data, dict) or data.get("schema") != PROFILE_SCHEMA:
        raise ProfileError(f"profile schema must be {PROFILE_SCHEMA!r}")
    name = data.get("profile")
    if not isinstance(name, str) or not name:
        raise ProfileError("profile name is required")
    services: List[Service] = []
    for raw in data.get("services") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ProfileError("each service needs a name")
        health_raw = raw.get("health") or {}
        health = Health(http=health_raw.get("http"), alive_for=float(health_raw.get("alive_for", 0.0)),
                        timeout=float(health_raw.get("timeout", 60.0)),
                        interval=float(health_raw.get("interval", 0.5)))
        if not health.http and health.alive_for <= 0:
            raise ProfileError(f"service {raw['name']}: health needs http or alive_for")
        restart = raw.get("restart") or {}
        stop_signal = raw.get("stop_signal", "SIGTERM")
        if stop_signal not in _SIGNALS:
            raise ProfileError(f"service {raw['name']}: unsupported stop_signal {stop_signal!r}")
        required = bool(raw.get("required", True))
        restart_max = int(restart.get("max", 0))
        if required and restart_max:
            raise ProfileError(f"service {raw['name']}: a required service is not restarted in-container "
                               "(the container exits and the host restart policy applies)")
        services.append(Service(
            name=raw["name"], argv=_strs(raw.get("argv"), f"service {raw['name']} argv"), required=required,
            health=health, restart_max=restart_max, restart_backoff=float(restart.get("backoff", 2.0)),
            no_restart_exit_codes=tuple(int(c) for c in raw.get("no_restart_exit_codes", [])),
            stop_signal=_SIGNALS[stop_signal], stop_timeout=float(raw.get("stop_timeout", 15.0)),
            cwd=raw.get("cwd"), when_env=_when(raw.get("when_env"), f"service {raw['name']} when_env")))
    if not services:
        raise ProfileError("profile has no services")
    names = [s.name for s in services]
    if len(set(names)) != len(names) and not all(s.when_env for s in services if names.count(s.name) > 1):
        raise ProfileError(f"duplicate service names: {names}")
    locked = _str_map(data.get("locked_env", {}), "locked_env")
    deployment = str(data.get("deployment_profile") or name)
    if locked.get("F2W_DEPLOYMENT_PROFILE") != deployment:
        raise ProfileError("locked_env must pin F2W_DEPLOYMENT_PROFILE to the profile name")
    steps: List[Tuple[str, ...]] = []
    step_when: List[Dict[str, Tuple[str, ...]]] = []
    for step in data.get("preflight", []):
        if isinstance(step, dict):
            steps.append(_strs(step.get("argv"), "preflight step argv"))
            step_when.append(_when(step.get("when_env"), "preflight step when_env"))
        else:
            steps.append(_strs(step, "preflight step"))
            step_when.append({})
    enum_env = _when(data.get("enum_env"), "enum_env")
    defaults = _str_map(data.get("env", {}), "env")
    forbidden = _strs(data.get("forbidden_env", []), "forbidden_env")
    overlap = (set(defaults) | set(locked)) & set(forbidden)
    if overlap:
        raise ProfileError(f"variables both set and forbidden: {sorted(overlap)}")
    return Profile(
        profile=name, tiled_mode=str(data.get("tiled_mode") or ""), services=tuple(services),
        preflight=tuple(steps),
        env=defaults, locked_env=locked, forbidden_env=forbidden,
        stripped_env=_strs(data.get("stripped_env", []), "stripped_env"),
        cwd=data.get("cwd"), raw=dict(data), deployment_profile=deployment, enum_env=enum_env,
        no_proxy_append=_strs(data.get("no_proxy_append", []), "no_proxy_append"),
        preflight_when=tuple(step_when))


def load_profile(path: Path) -> Profile:
    """Read and validate a profile file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from None
    return parse_profile(data)


# --------------------------------------------------------------------------- environment


def _same(a: str, b: str) -> bool:
    return a.strip().rstrip("/") == b.strip().rstrip("/")


def _load_dotenv_file(path: Path) -> Dict[str, str]:
    from dotenv import dotenv_values  # app dependency; only needed when F2W_DOTENV names a file

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def resolve_environment(
    environ: Mapping[str, str],
    profile: Profile,
    *,
    dotenv_loader: Callable[[Path], Dict[str, str]] = _load_dotenv_file,
) -> Tuple[Dict[str, str], List[str]]:
    """Return the environment every child gets, and notes for the log (names only).

    Order: explicit ``F2W_DOTENV`` file (never overriding), strip legacy names, refuse
    forbidden names, check and pin locked values, fill defaults, disable dotenv in children.

    Raises:
        ProfileError: A locked value differs, a forbidden value is set, or ``F2W_DOTENV``
            names a missing file.
    """
    env = {k: v for k, v in environ.items()}
    notes: List[str] = []
    dotenv = env.get(DOTENV_VAR)
    if dotenv is not None and dotenv.strip().lower() not in DOTENV_DISABLED:
        path = Path(dotenv).expanduser()
        if not path.is_file():
            raise ProfileError(f"{DOTENV_VAR}={dotenv!r} is not a file")
        loaded = dotenv_loader(path)
        added = sorted(k for k in loaded if k not in env)
        for key in added:
            env[key] = loaded[key]
        notes.append(f"dotenv loaded explicitly from {path} ({len(added)} new names)")
    for name in profile.stripped_env:
        if env.pop(name, None) is not None:
            notes.append(f"ignored legacy variable {name} (not read by image services)")
    present = sorted(n for n in profile.forbidden_env if (env.get(n) or "").strip())
    if present:
        raise ProfileError(f"profile {profile.profile} refuses these settings: {', '.join(present)}")
    for name, value in profile.locked_env.items():
        given = env.get(name)
        if given is not None and given.strip() and not _same(given, value):
            raise ProfileError(f"{name}={given.strip()!r} is not allowed on the {profile.profile} image "
                               f"(fixed to {value!r}; the profile cannot be switched at runtime)")
        env[name] = value
    for name, value in profile.env.items():
        if not (env.get(name) or "").strip():
            env[name] = value
    for name, allowed in profile.enum_env.items():
        value = (env.get(name) or "").strip().lower()
        if value not in allowed:
            raise ProfileError(f"{name}={env.get(name)!r} is not one of {', '.join(allowed)} on the "
                               f"{profile.profile} image")
        env[name] = value
    if profile.no_proxy_append:
        notes.append(merge_no_proxy(env, profile.no_proxy_append))
    collector = otel_no_proxy_host(env)
    if collector:
        notes.append("OTLP collector host is not public: " + merge_no_proxy(env, [collector]))
    env[DOTENV_VAR] = "off"
    return env, notes


_OTEL_TRUE = frozenset({"1", "true", "yes", "on"})
_INTERNAL_SUFFIXES = (".svc", ".local", ".localhost", ".internal", ".lan", ".home.arpa", ".cluster.local")


def otel_no_proxy_host(env: Mapping[str, str]) -> Optional[str]:
    """Collector host to add to ``NO_PROXY``: tracing is on and the OTLP endpoint host is not a public name.

    Public means a dotted DNS name outside the internal suffixes (``.svc``, ``.local``, ``.internal``...) or a
    global IP address; such a collector is reached through the egress proxy like any other host, and the
    proxy's allowlist must name it. Returns ``None`` when tracing is off or the host is public.
    """
    flag = (env.get("F2W_OTEL_ENABLED") or "").strip() or (env.get("OTEL_ENABLED") or "")
    if flag.strip().lower() not in _OTEL_TRUE:
        return None
    endpoint = ((env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "")
                .strip() or "http://localhost:4318")
    try:
        host = (urllib.parse.urlsplit(endpoint).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    try:
        return None if ipaddress.ip_address(host).is_global else host
    except ValueError:
        pass
    return host if "." not in host or host.endswith(_INTERNAL_SUFFIXES) else None


def merge_no_proxy(env: Dict[str, str], extra: Sequence[str]) -> str:
    """Add ``extra`` to ``NO_PROXY`` and ``no_proxy`` (both spellings carry the same merged list).

    Internal traffic (loopback, the gateway upstream, the bundled Tiled) must never reach an egress
    proxy configured through ``HTTP_PROXY``/``HTTPS_PROXY``. Existing entries keep their order and
    are never removed. Returns a log note (entries only, never a proxy URL).
    """
    merged: List[str] = []
    for key in ("no_proxy", "NO_PROXY"):
        for entry in re.split(r"[,\s]+", env.get(key) or ""):
            if entry and entry.lower() not in (m.lower() for m in merged):
                merged.append(entry)
    added = [e for e in extra if e.lower() not in (m.lower() for m in merged)]
    merged.extend(added)
    env["NO_PROXY"] = env["no_proxy"] = ",".join(merged)
    return f"NO_PROXY gained {len(added)} internal entries ({len(merged)} in total)"


def substitute(argv: Sequence[str], env: Mapping[str, str]) -> List[str]:
    """Expand ``${NAME}`` from ``env`` and the ``@python`` token; unknown names are refused."""
    out: List[str] = []
    for arg in argv:
        if arg == PYTHON_TOKEN:
            out.append(sys.executable)
            continue

        def repl(match: "re.Match[str]") -> str:
            name = match.group(1)
            if name not in env:
                raise ProfileError(f"argument {arg!r} uses unset variable {name}")
            return env[name]

        out.append(_VAR.sub(repl, arg))
    return out


# --------------------------------------------------------------------------- processes


def http_ok(url: str, timeout: float = 2.0) -> bool:
    """True when ``GET url`` answers 2xx (no proxy, no redirects followed beyond urllib's)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class Running:
    """State of one service: its process, restart count and status."""

    def __init__(self, service: Service, argv: List[str], health_url: Optional[str]):
        self.service = service
        self.argv = argv
        self.health_url = health_url
        self.process: Optional[subprocess.Popen] = None
        self.state = "pending"  # pending|starting|running|backoff|failed|stopped
        self.started_at = 0.0
        self.deadline = 0.0
        self.next_check = 0.0
        self.restart_at = 0.0
        self.restarts = 0
        self.attempts = 0
        self.last_exit: Optional[int] = None
        self.killed = False

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "pid": self.process.pid if self.alive else None,
                "required": self.service.required, "restarts": self.restarts, "last_exit": self.last_exit}


def _killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class Supervisor:
    """Start, watch and stop the profile's services (see the module docstring)."""

    def __init__(self, profile: Profile, env: Dict[str, str], *, status_file: Optional[Path] = None,
                 tick: float = 0.1):
        self.profile = profile
        self.env = env
        self.status_file = status_file
        self.tick = tick
        self.stop_requested = threading.Event()
        self.stop_signal_name: Optional[str] = None
        self.ready_logged = False
        self.units = [Running(s, substitute(s.argv, env), substitute([s.health.http], env)[0]
                              if s.health.http else None)
                      for s in profile.services if condition_holds(s.when_env, env)]

    # -- signals ----------------------------------------------------------------
    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            if not self.stop_requested.is_set():
                self.stop_signal_name = signal.Signals(signum).name
            self.stop_requested.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, handler)

    # -- status -----------------------------------------------------------------
    def write_status(self, phase: str) -> None:
        if self.status_file is None:
            return
        data = {"profile": self.profile.profile, "phase": phase, "updated": time.time(),
                "services": {u.service.name: u.to_dict() for u in self.units}}
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.status_file)
        except OSError as exc:  # status is diagnostics only
            log("status_write_failed", error=type(exc).__name__)

    # -- preflight --------------------------------------------------------------
    def run_preflight(self) -> bool:
        whens = self.profile.preflight_when or ({},) * len(self.profile.preflight)
        for step, when in zip(self.profile.preflight, whens):
            if not condition_holds(when, self.env):
                continue
            argv = substitute(step, self.env)
            log("preflight", argv=argv)
            try:
                done = subprocess.run(argv, env=self.env, cwd=self.profile.cwd, stdin=subprocess.DEVNULL,
                                      timeout=120, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log("preflight_failed", argv=argv[:2], error=type(exc).__name__)
                return False
            if done.returncode != 0:
                log("preflight_failed", argv=argv[:2], exit=done.returncode)
                return False
        return True

    # -- lifecycle --------------------------------------------------------------
    def spawn(self, unit: Running) -> None:
        unit.attempts += 1
        cwd = unit.service.cwd or self.profile.cwd
        unit.process = subprocess.Popen(unit.argv, env=self.env, cwd=cwd, stdin=subprocess.DEVNULL,
                                        start_new_session=True, close_fds=True)
        now = time.monotonic()
        unit.state, unit.started_at, unit.killed = "starting", now, False
        unit.deadline = now + unit.service.health.timeout
        unit.next_check = now
        log("start", service=unit.service.name, pid=unit.process.pid, attempt=unit.attempts)
        self.write_status("starting")

    def healthy(self, unit: Running) -> bool:
        if unit.health_url:
            return http_ok(unit.health_url, timeout=min(2.0, unit.service.health.interval * 4))
        return time.monotonic() - unit.started_at >= unit.service.health.alive_for

    def _settled(self, unit: Running) -> bool:
        """An earlier service no longer blocks the next one from starting."""
        if unit.state == "running":
            return True
        return not unit.service.required and unit.attempts > 0 and unit.state in ("backoff", "failed")

    def _reap(self, unit: Running) -> None:
        """After a group leader exited, make sure no member of its group is left behind."""
        if unit.process is not None:
            _killpg(unit.process.pid, signal.SIGKILL)

    def _child_exited(self, unit: Running, code: int, phase: str) -> Optional[int]:
        """Handle an unexpected exit; return a supervisor exit code when it is fatal."""
        unit.last_exit = code
        self._reap(unit)
        name = unit.service.name
        if unit.service.required:
            log("required_child_exited", service=name, exit=code, phase=phase)
            unit.state = "failed"
            return EXIT_CHILD_FAILED
        if code in unit.service.no_restart_exit_codes or unit.restarts >= unit.service.restart_max:
            unit.state = "failed"
            log("service_failed", service=name, exit=code, restarts=unit.restarts,
                note="not restarted; dependent capability is not ready")
        else:
            delay = unit.service.restart_backoff * (2 ** unit.restarts)
            unit.restarts += 1
            unit.state, unit.restart_at = "backoff", time.monotonic() + delay
            log("restart_scheduled", service=name, exit=code, in_seconds=delay, restart=unit.restarts)
        self.write_status("degraded")
        return None

    def step(self, phase: str) -> Optional[int]:
        """One supervision pass; returns an exit code when the container must stop."""
        now = time.monotonic()
        for unit in self.units:
            if unit.process is not None and unit.state in ("starting", "running"):
                code = unit.process.poll()
                if code is not None:
                    fatal = self._child_exited(unit, code, "running" if unit.state == "running" else "starting")
                    if fatal is not None:
                        return fatal
                    continue
            if unit.state == "starting" and now >= unit.next_check:
                unit.next_check = now + unit.service.health.interval
                if self.healthy(unit):
                    unit.state = "running"
                    log("healthy", service=unit.service.name, seconds=round(now - unit.started_at, 2))
                    self.write_status(phase)
                elif now >= unit.deadline:
                    log("health_timeout", service=unit.service.name, timeout=unit.service.health.timeout)
                    self.stop_unit(unit)
                    if unit.service.required:
                        unit.state = "failed"
                        return EXIT_STARTUP_FAILED
                    self._child_exited(unit, unit.last_exit if unit.last_exit is not None else -1, "starting")
            elif unit.state == "backoff" and now >= unit.restart_at:
                self.spawn(unit)
        if phase == "startup":
            for index, unit in enumerate(self.units):
                if unit.state == "pending" and all(self._settled(u) for u in self.units[:index]):
                    self.spawn(unit)
                    break
        if not self.ready_logged and all(u.state in ("running", "failed", "backoff") for u in self.units) \
                and all(u.state == "running" for u in self.units if u.service.required):
            self.ready_logged = True
            log("ready", services={u.service.name: u.state for u in self.units})
            self.write_status("ready")
        return None

    def stop_unit(self, unit: Running) -> None:
        """Signal one process group, wait ``stop_timeout``, then SIGKILL."""
        proc = unit.process
        if proc is None:
            return
        if proc.poll() is None:
            _killpg(proc.pid, unit.service.stop_signal)
            try:
                proc.wait(timeout=unit.service.stop_timeout)
            except subprocess.TimeoutExpired:
                log("stop_timeout_kill", service=unit.service.name, timeout=unit.service.stop_timeout)
                unit.killed = True
                _killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        unit.last_exit = proc.returncode
        self._reap(unit)
        log("stopped", service=unit.service.name, exit=proc.returncode, killed=unit.killed)

    def shutdown(self) -> bool:
        """Stop every live service in reverse start order; True when all stopped cleanly."""
        clean = True
        for unit in reversed(self.units):
            if unit.process is None or unit.state in ("pending",):
                continue
            was_alive = unit.alive
            self.stop_unit(unit)
            if was_alive:
                ok_codes = {0, -unit.service.stop_signal, 128 + unit.service.stop_signal}
                if unit.killed or unit.last_exit not in ok_codes:
                    clean = False
            unit.state = "stopped"
        self.write_status("stopped")
        return clean

    def run(self) -> int:
        """Preflight, start in order, supervise until a signal or a fatal child exit."""
        self.write_status("preflight")
        if not self.run_preflight():
            self.write_status("refused")
            return EXIT_CONFIG
        phase = "startup"
        result: Optional[int] = None
        while not self.stop_requested.is_set():
            result = self.step(phase)
            if result is not None:
                break
            if phase == "startup" and all(u.state != "pending" for u in self.units) and self.ready_logged:
                phase = "running"
            self.stop_requested.wait(self.tick)
        if result is None:
            log("stop_requested", signal=self.stop_signal_name)
            return EXIT_OK if self.shutdown() else EXIT_UNCLEAN_STOP
        log("fatal", exit=result)
        self.shutdown()
        return result


# --------------------------------------------------------------------------- CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="f2w-supervisor", description=__doc__.splitlines()[0])
    parser.add_argument("--profile-file", type=Path, default=Path(DEFAULT_PROFILE_FILE))
    parser.add_argument("--check", action="store_true",
                        help="resolve the environment and commands, print them (values of locked and "
                             "defaulted names only), and exit without starting anything")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile_file)
        env, notes = resolve_environment(os.environ, profile)
        env[PROFILE_FILE_VAR] = str(args.profile_file)
        supervisor = Supervisor(profile, env, status_file=Path(env.get(STATUS_ENV) or DEFAULT_STATUS_FILE))
    except ProfileError as exc:
        log("refused", reason=str(exc))
        return EXIT_CONFIG
    for note in notes:
        log("note", detail=note)
    if args.check:
        shown = sorted(set(profile.locked_env) | set(profile.env) | {DOTENV_VAR}
                       | ({"NO_PROXY"} if profile.no_proxy_append or otel_no_proxy_host(env) else set()))
        print(json.dumps({"profile": profile.profile, "tiled_mode": profile.tiled_mode,
                          "env": {k: env[k] for k in shown},
                          "preflight": [substitute(s, env) for s, w in zip(
                              profile.preflight, profile.preflight_when or ({},) * len(profile.preflight))
                              if condition_holds(w, env)],
                          "services": [{"name": u.service.name, "argv": u.argv, "required": u.service.required,
                                        "health": u.health_url or f"alive_for={u.service.health.alive_for}"}
                                       for u in supervisor.units]}, indent=2))
        return EXIT_OK
    supervisor.install_signal_handlers()
    log("starting", profile=profile.profile, services=[u.service.name for u in supervisor.units])
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
