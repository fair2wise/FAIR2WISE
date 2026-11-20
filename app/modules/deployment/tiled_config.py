"""Tiled deployment configuration: managed (local, app-owned) versus external (central, connect-only).

The trusted server-startup profile picks one mode (``docs/adr/tiled-deployment.md`` section 4).
No request, browser setting or model argument can pick or change it, so
:func:`load_tiled_settings` takes the startup environment as an explicit mapping and is
meant to run once at process start.

* ``local`` -> ``managed``: F2W owns a Tiled server on ``127.0.0.1`` with persistent data
  in ``F2W_TILED_DATA_DIR``; the server runs in its own Python environment
  (``F2W_TILED_PYTHON``), never in the app's site-packages (pydantic-settings conflict,
  ``docs/adr/optional-dependencies.md``).
* ``als-dev`` / ``als-prod`` -> ``external``: connect to the profile's one fixed URL. No
  server runtime, data dir or Tiled interpreter may be configured.
* ``test`` -> ``managed`` (disposable fixture) or ``external`` (connect-only tests against
  a fixture's loopback URL).

Secrets never appear in this module's outputs: the writer credential is read at runtime
from a file (``F2W_TILED_WRITER_SECRET_FILE``) or generated into the managed data dir, and
the generated server configuration (:func:`render_server_config`) contains no secret.

Version pin: owner decision O-T1 (2026-10-12) pins ``tiled==0.2.18`` as an interim
exception to D22 (see ``docs/migration/reviews/H1/owner_decisions.md``). Changing it
invalidates H1, so it is a constant here, not a setting.

Legacy ``TILED_URI``, ``TILED_API_KEY`` and ``F2W_LIVE_TILED`` are deliberately not read.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import secrets
import stat
from typing import Any
from urllib.parse import urlsplit, urlunsplit

#: Exact Tiled version F2W supports (O-T1). Compared as an exact string with
#: ``about.library_version``.
PINNED_TILED_VERSION = "0.2.18"
#: The pinned artifacts (PyPI, published 2026-09-02).
PINNED_TILED_WHEEL = "tiled-0.2.18-py3-none-any.whl"
PINNED_TILED_WHEEL_SHA256 = "416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06"
PINNED_TILED_SDIST_SHA256 = "2f3943260c46824b162796fcb8a43c3d02f41205e2ccefc64e365b18b6ec1e27"

PROFILES = ("local", "als-dev", "als-prod", "test")
MODES = ("managed", "external")
#: Modes each profile allows; the first one is the default.
PROFILE_MODES: dict[str, tuple[str, ...]] = {
    "local": ("managed",),
    "als-dev": ("external",),
    "als-prod": ("external",),
    "test": ("managed", "external"),
}
#: The one URL each ALS profile may connect to (never contacted by this module).
ALS_PROFILE_URLS: dict[str, str] = {
    "als-dev": "https://tiled-staging.als.lbl.gov",
    "als-prod": "https://tiled.als.lbl.gov",
}

DEFAULT_MANAGED_HOST = "127.0.0.1"
DEFAULT_MANAGED_PORT = 8765
DEFAULT_LOCAL_DATA_DIR = "/var/lib/fair2wise/tiled"
DEFAULT_LOCAL_TILED_PYTHON = "/opt/tiled/venv/bin/python"

#: Owner labels written into ownership manifests.
OWNER_LOCAL = "f2w-local"
OWNER_TEST = "f2w-test"

#: Startup variables that would carry a secret value instead of a file path.
_SECRET_VALUE_VARS = ("F2W_TILED_WRITER_SECRET", "F2W_TILED_API_KEY", "F2W_TILED_WRITER_KEY")
#: Variables that configure a managed server and must be absent on ALS profiles.
_MANAGED_ONLY_VARS = ("F2W_TILED_DATA_DIR", "F2W_TILED_PYTHON", "F2W_TILED_PORT")


class TiledDeploymentError(RuntimeError):
    """Base error for Tiled deployment problems; ``code`` is a stable machine-readable reason."""

    code = "tiled_error"

    def __init__(self, message: str, *, code: str | None = None, detail: dict[str, Any] | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.detail = dict(detail or {})


class TiledConfigError(TiledDeploymentError):
    """The startup settings are invalid or inconsistent with the deployment profile."""

    code = "config_invalid"


@dataclass(frozen=True)
class AuthnSpec:
    """Multi-user authentication for a managed server (test fixture / ALS-like variant).

    Uses Tiled's ``DictionaryAuthenticator`` (``tiled/authenticators.py:46``, "for test and
    demo purposes only"). Passwords and token-signing keys are not part of this spec: they
    live in a runtime secrets file (:attr:`ManagedTiledConfig.authn_secrets_file`).

    Attributes:
        provider: Tiled provider name.
        users: User names the authenticator accepts.
        admins: Subset of ``users`` granted Tiled's ``admin`` role via ``tiled_admins``.
        access_tags: Optional tag definitions (``roles``/``tags``/``tag_owners``, the
            ``AccessTagsCompiler`` format, ``tiled/access_control/access_tags.py:308``).
            When set, the server enforces ``TagBasedAccessPolicy`` from a tags database
            compiled at start. Without it, any signed-in user (Tiled role ``user``) can write.
    """

    provider: str = "f2wtest"
    users: tuple[str, ...] = ("admin", "reader")
    admins: tuple[str, ...] = ("admin",)
    access_tags: dict[str, Any] | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        missing = [name for name in self.admins if name not in self.users]
        if missing:
            raise TiledConfigError(f"Tiled admins are not configured users: {missing}")
        if not self.provider.isidentifier():
            raise TiledConfigError(f"Tiled provider name must be an identifier: {self.provider!r}")


@dataclass(frozen=True)
class ManagedTiledConfig:
    """An application-managed Tiled server and the storage it owns.

    Layout under ``data_dir`` (all owned by this instance)::

        catalog.db           catalog + graph tables (SQLite)
        data/                writable_storage
        authn.db             authentication database (only with ``authn``)
        secrets/             writer.key, authn.json (mode 0600)
        run/                 server.yml (generated, no secrets), manifest.json
        logs/                server logs

    Attributes:
        profile: ``local`` or ``test``.
        data_dir: Persistent storage root; must exist (a mount on ``:local``).
        tiled_python: Interpreter of the isolated Tiled environment.
        port: TCP port on ``host``; ``0`` picks a free ephemeral port at start.
        host: Always loopback.
        expected_version: Must equal :data:`PINNED_TILED_VERSION`.
        writer_secret_file: Writer API key file; ``None`` uses ``secrets/writer.key``,
            generated at first provisioning.
        owner: ``f2w-local`` or ``f2w-test``; only ``f2w-test`` instances may be destroyed.
        authn: Multi-user authentication; ``None`` means single-user API key mode.
    """

    profile: str
    data_dir: Path
    tiled_python: Path
    port: int = DEFAULT_MANAGED_PORT
    host: str = DEFAULT_MANAGED_HOST
    expected_version: str = PINNED_TILED_VERSION
    writer_secret_file: Path | None = None
    owner: str = OWNER_LOCAL
    authn: AuthnSpec | None = None

    def __post_init__(self) -> None:
        if self.profile not in ("local", "test"):
            raise TiledConfigError(f"Profile {self.profile!r} cannot run a managed Tiled server")
        if not _is_loopback_host(self.host):
            raise TiledConfigError(f"A managed Tiled server binds loopback only, not {self.host!r}")
        if not 0 <= int(self.port) <= 65535:
            raise TiledConfigError(f"Invalid Tiled port {self.port!r}")
        if self.owner not in (OWNER_LOCAL, OWNER_TEST):
            raise TiledConfigError(f"Unknown owner label {self.owner!r}")
        if self.owner == OWNER_TEST and self.profile != "test":
            raise TiledConfigError("Only the test profile may create disposable (f2w-test) instances")
        _require_pinned(self.expected_version)

    @property
    def catalog_path(self) -> Path:
        """SQLite catalog (also holds the graph tables)."""
        return self.data_dir / "catalog.db"

    @property
    def storage_dir(self) -> Path:
        """Tiled ``writable_storage`` directory."""
        return self.data_dir / "data"

    @property
    def authn_db_path(self) -> Path:
        """SQLite authentication database (multi-user mode only)."""
        return self.data_dir / "authn.db"

    @property
    def secrets_dir(self) -> Path:
        """Runtime secrets owned by this instance (mode 0700)."""
        return self.data_dir / "secrets"

    @property
    def run_dir(self) -> Path:
        """Generated server config and the ownership manifest."""
        return self.data_dir / "run"

    @property
    def log_dir(self) -> Path:
        """Server logs."""
        return self.data_dir / "logs"

    @property
    def server_config_path(self) -> Path:
        """Generated Tiled server configuration (no secrets)."""
        return self.run_dir / "server.yml"

    @property
    def manifest_path(self) -> Path:
        """Ownership manifest of the running (or last) server process."""
        return self.run_dir / "manifest.json"

    @property
    def effective_writer_secret_file(self) -> Path:
        """Writer key file: the configured one, or ``secrets/writer.key``."""
        return self.writer_secret_file or (self.secrets_dir / "writer.key")

    @property
    def authn_secrets_file(self) -> Path:
        """Passwords and token-signing keys for :attr:`authn` (generated, mode 0600)."""
        return self.secrets_dir / "authn.json"

    @property
    def access_tags_source(self) -> Path:
        """Tag definitions written at start (no secrets)."""
        return self.run_dir / "access_tags.json"

    @property
    def access_tags_db_path(self) -> Path:
        """Compiled tags database read by ``TagBasedAccessPolicy``."""
        return self.data_dir / "access_tags.db"


@dataclass(frozen=True)
class ExternalTiledConfig:
    """A Tiled server F2W only connects to; it never starts, stops or upgrades it.

    Attributes:
        profile: ``als-dev``, ``als-prod`` or ``test``.
        url: Normalized base URL (no trailing slash).
        expected_version: Must equal :data:`PINNED_TILED_VERSION`.
        writer_secret_file: File holding the writer credential (runtime secret).
        writer_scheme: ``Apikey`` or ``Bearer``.
        ca_bundle: Optional CA bundle for TLS verification.
        require_writer_role: Tiled role the writer principal must hold (``admin`` on ALS, D27).
        timeout: Per-request timeout in seconds.
    """

    profile: str
    url: str
    expected_version: str = PINNED_TILED_VERSION
    writer_secret_file: Path | None = None
    writer_scheme: str = "Apikey"
    ca_bundle: Path | None = None
    require_writer_role: str | None = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        validate_external_url(self.profile, self.url)
        _require_pinned(self.expected_version)
        if self.writer_scheme not in ("Apikey", "Bearer"):
            raise TiledConfigError(f"Unsupported Tiled credential scheme {self.writer_scheme!r}")


@dataclass(frozen=True)
class TiledSettings:
    """Resolved startup choice: exactly one of ``managed`` or ``external`` is set."""

    profile: str
    mode: str
    managed: ManagedTiledConfig | None = None
    external: ExternalTiledConfig | None = None


def normalize_base_url(url: str) -> str:
    """Return ``scheme://host[:port][/path]`` without trailing slash, query or fragment.

    Args:
        url: An absolute http(s) URL. A trailing ``/api/graphql`` or ``/api/v1`` is removed.

    Raises:
        TiledConfigError: If ``url`` is not an absolute http(s) URL.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise TiledConfigError(f"Tiled URL must be an absolute http(s) URL: {url!r}")
    path = parts.path.rstrip("/")
    for suffix in ("/api/graphql", "/api/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = {"http": 80, "https": 443}[parts.scheme.lower()]
    netloc = host if parts.port in (None, default_port) else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, path.rstrip("/"), "", ""))


def is_als_host(host: str | None) -> bool:
    """True for ``als.lbl.gov`` and its subdomains."""
    name = (host or "").strip().lower().rstrip(".")
    return name == "als.lbl.gov" or name.endswith(".als.lbl.gov")


def _is_loopback_host(host: str | None) -> bool:
    name = (host or "").strip().lower().strip("[]")
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:  # a hostname is not a loopback IP
        return False


def validate_external_url(profile: str, url: str) -> str:
    """Check that ``url`` is the one target ``profile`` may connect to.

    ALS profiles accept only their fixed URL (a profile allowlist of one, replacing the
    hostname coercion in ``graph.backends.tiled_lookup.coerce_local_tiled_uri``). Local and test profiles
    accept only loopback http(s) and reject every ``*.als.lbl.gov`` host.

    Returns:
        The normalized URL.

    Raises:
        TiledConfigError: If the URL is not allowed for the profile.
    """
    normalized = normalize_base_url(url)
    host = urlsplit(normalized).hostname
    if profile in ALS_PROFILE_URLS:
        if normalized != ALS_PROFILE_URLS[profile]:
            raise TiledConfigError(
                f"Profile {profile} connects only to {ALS_PROFILE_URLS[profile]}, not {normalized}"
            )
        return normalized
    if profile not in PROFILES:
        raise TiledConfigError(f"Unknown deployment profile {profile!r}")
    if is_als_host(host):
        raise TiledConfigError(f"Profile {profile} must not target an ALS Tiled host ({host})")
    if not _is_loopback_host(host):
        raise TiledConfigError(f"Profile {profile} targets loopback Tiled only, not {host}")
    return normalized


def _require_pinned(version: str) -> None:
    if version != PINNED_TILED_VERSION:
        raise TiledConfigError(
            f"Tiled version is pinned to {PINNED_TILED_VERSION} (O-T1); {version!r} is not supported. "
            "Changing the pin needs a new H1 record."
        )


def load_tiled_settings(environ: Mapping[str, str]) -> TiledSettings:
    """Resolve the Tiled mode from trusted startup settings.

    Args:
        environ: The process start environment (pass ``os.environ`` once at startup).

    Variables:
        ``F2W_DEPLOYMENT_PROFILE`` (required), ``F2W_TILED_MODE``, ``F2W_TILED_URL``,
        ``F2W_TILED_EXPECTED_VERSION``, ``F2W_TILED_WRITER_SECRET_FILE``,
        ``F2W_TILED_CA_BUNDLE``, and for managed mode ``F2W_TILED_DATA_DIR``,
        ``F2W_TILED_PYTHON``, ``F2W_TILED_PORT``.

    Returns:
        The validated settings.

    Raises:
        TiledConfigError: On any missing, unknown or inconsistent value.
    """

    def get(name: str) -> str:
        return str(environ.get(name, "") or "").strip()

    profile = get("F2W_DEPLOYMENT_PROFILE")
    if profile not in PROFILES:
        raise TiledConfigError(
            f"F2W_DEPLOYMENT_PROFILE must be one of {', '.join(PROFILES)} (got {profile!r})"
        )
    for name in _SECRET_VALUE_VARS:
        if get(name):
            raise TiledConfigError(f"{name} would carry a secret value; use F2W_TILED_WRITER_SECRET_FILE")
    allowed = PROFILE_MODES[profile]
    mode = get("F2W_TILED_MODE") or allowed[0]
    if mode not in MODES:
        raise TiledConfigError(f"F2W_TILED_MODE must be managed or external (got {mode!r})")
    if mode not in allowed:
        raise TiledConfigError(f"Profile {profile} does not allow Tiled mode {mode}")
    expected = get("F2W_TILED_EXPECTED_VERSION") or PINNED_TILED_VERSION
    secret_file = Path(get("F2W_TILED_WRITER_SECRET_FILE")) if get("F2W_TILED_WRITER_SECRET_FILE") else None

    if mode == "external":
        present = [name for name in _MANAGED_ONLY_VARS if get(name)]
        if present:
            raise TiledConfigError(f"External Tiled mode must not configure a managed server: {present}")
        url = get("F2W_TILED_URL") or ALS_PROFILE_URLS.get(profile, "")
        if not url:
            raise TiledConfigError("F2W_TILED_URL is required for external mode on this profile")
        ca_bundle = Path(get("F2W_TILED_CA_BUNDLE")) if get("F2W_TILED_CA_BUNDLE") else None
        external = ExternalTiledConfig(
            profile=profile,
            url=validate_external_url(profile, url),
            expected_version=expected,
            writer_secret_file=secret_file,
            ca_bundle=ca_bundle,
            require_writer_role="admin" if profile in ALS_PROFILE_URLS else None,
        )
        return TiledSettings(profile=profile, mode=mode, external=external)

    if get("F2W_TILED_URL"):
        raise TiledConfigError("F2W_TILED_URL is for external mode; a managed server is always loopback")
    data_dir = get("F2W_TILED_DATA_DIR") or (DEFAULT_LOCAL_DATA_DIR if profile == "local" else "")
    python = get("F2W_TILED_PYTHON") or (DEFAULT_LOCAL_TILED_PYTHON if profile == "local" else "")
    if not data_dir or not python:
        raise TiledConfigError("Managed test servers need F2W_TILED_DATA_DIR and F2W_TILED_PYTHON")
    port_text = get("F2W_TILED_PORT") or (str(DEFAULT_MANAGED_PORT) if profile == "local" else "0")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise TiledConfigError(f"F2W_TILED_PORT must be an integer (got {port_text!r})") from exc
    managed = ManagedTiledConfig(
        profile=profile,
        data_dir=Path(data_dir),
        tiled_python=Path(python),
        port=port,
        expected_version=expected,
        writer_secret_file=secret_file,
        owner=OWNER_LOCAL if profile == "local" else OWNER_TEST,
    )
    return TiledSettings(profile=profile, mode=mode, managed=managed)


def check_private_file(path: Path) -> None:
    """Refuse a secret file other users could read or another uid owns (AUD-15).

    Rule (``docs/adr/tiled-deployment.md`` section 4.1): the writer secret file is a regular
    file, mode ``0600`` or ``0400`` (no group or other bits), owned by the uid running F2W.
    Symlinks are followed (``/run/secrets`` mounts may be links); the target is what is
    checked. Generated ``:local`` keys (:func:`write_private_file`) satisfy the rule.

    Raises:
        TiledConfigError: Missing, not a regular file, group/world-accessible or foreign-owned.
            The message names the path and the mode, never the content.
    """
    try:
        st = path.stat()
    except OSError as exc:
        raise TiledConfigError(f"Cannot read Tiled secret file {path}: {exc.strerror}") from None
    if not stat.S_ISREG(st.st_mode):
        raise TiledConfigError(f"Tiled secret file {path} is not a regular file")
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        raise TiledConfigError(f"Tiled secret file {path} is group- or world-accessible (mode {mode:04o}); "
                               "it must be 0600 or 0400")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and st.st_uid != getuid():
        raise TiledConfigError(f"Tiled secret file {path} is owned by uid {st.st_uid}, not the running uid "
                               f"{getuid()}")


def read_secret_file(path: Path, *, alphanumeric: bool = False) -> str:
    """Read a credential from a runtime secret file.

    The file must pass :func:`check_private_file` (mode ``0600``/``0400``, owned by the
    running uid) before a byte of it is read.

    Args:
        path: Secret file path.
        alphanumeric: Require ``[A-Za-z0-9]+`` (Tiled single-user API keys, app.py:581).

    Returns:
        The stripped secret.

    Raises:
        TiledConfigError: If the file is missing, empty, a symlink to nowhere, malformed,
            readable by others or owned by another uid. The secret itself is never included
            in the message.
    """
    check_private_file(Path(path))
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TiledConfigError(f"Cannot read Tiled secret file {path}: {exc.strerror}") from None
    if not value:
        raise TiledConfigError(f"Tiled secret file {path} is empty")
    if alphanumeric and not value.isalnum():
        raise TiledConfigError(f"Tiled API key in {path} must be alphanumeric")
    return value


def write_private_file(path: Path, content: str, *, exclusive: bool = False) -> None:
    """Create ``path`` with mode 0600, whatever the umask or a setgid parent directory.

    The file is opened without following a symlink, ``fchmod``-ed to 0600 on the open
    descriptor before any content is written, and chmod-ed again by path afterwards.

    Args:
        path: File to write.
        content: Text to store.
        exclusive: Create atomically (``O_EXCL``); an existing file raises ``FileExistsError``
            instead of being truncated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except AttributeError:  # pragma: no cover - platforms without fchmod
        pass
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def generate_api_key() -> str:
    """A random 64-character alphanumeric (hex) API key."""
    return secrets.token_hex(32)


def render_server_config(cfg: ManagedTiledConfig, *, port: int, init_catalog: bool) -> dict[str, Any]:
    """Build the Tiled server configuration for a managed instance.

    The result has no secret: the single-user API key, passwords and token-signing keys
    are injected at process start by the launcher (``_tiled_entry.py``) from files.

    Args:
        cfg: The managed instance.
        port: The concrete port to bind.
        init_catalog: ``True`` only when no catalog exists yet (first provisioning).
            An existing catalog is never migrated implicitly.

    Returns:
        A mapping that serializes to a Tiled ``config.yml``.
    """
    config: dict[str, Any] = {
        "catalog": {
            "uri": f"sqlite:///{cfg.catalog_path}",
            "writable_storage": [str(cfg.storage_dir)],
            "init_if_not_exists": bool(init_catalog),
        },
        "authentication": {"allow_anonymous_access": False},
        "uvicorn": {"host": cfg.host, "port": int(port)},
    }
    if cfg.authn is not None:
        config["authentication"].update(
            {
                "providers": [
                    {
                        "provider": cfg.authn.provider,
                        "authenticator": "tiled.authenticators:DictionaryAuthenticator",
                        "args": {},
                    }
                ],
                "tiled_admins": [{"provider": cfg.authn.provider, "id": name} for name in cfg.authn.admins],
            }
        )
        config["database"] = {"uri": f"sqlite:///{cfg.authn_db_path}", "init_if_not_exists": True}
        if cfg.authn.access_tags is not None:
            config["access_control"] = {
                "access_policy": "tiled.access_control.access_policies:TagBasedAccessPolicy",
                "args": {
                    "provider": cfg.authn.provider,
                    "tags_db": {"uri": f"sqlite:///{cfg.access_tags_db_path}"},
                    "access_tags_parser": "tiled.access_control.access_tags:AccessTagsParser",
                },
            }
    return config
