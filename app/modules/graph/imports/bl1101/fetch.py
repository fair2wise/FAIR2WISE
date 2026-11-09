"""Network and git access for the BL11.0.1.2 ingest: page download and repo clone/sync."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .common import LOGGER, sha256_bytes, utc_now
from .paths import REPO_ROOT
from .specs import USER_AGENT, RepoSpec


def fetch_url(url: str, dest: Path, timeout: int = 45) -> dict[str, Any]:
    """Download ``url`` to ``dest`` and describe the result.

    Args:
        url: Page to fetch.
        dest: File to write. Parent directories are created.
        timeout: Request timeout in seconds.

    Returns:
        On success, a snapshot record with ``status`` ``"ok"``, ``fetched_at``,
        repo-relative ``path``, ``sha256``, ``http_status`` and ``bytes``. On an
        HTTP, URL, timeout or OS error, ``{"url", "status": "failed", "reason"}``;
        the error is logged and not raised and ``dest`` is not written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = getattr(response, "status", 200)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Failed to fetch %s: %s", url, exc)
        return {"url": url, "status": "failed", "reason": str(exc)}
    dest.write_bytes(body)
    return {
        "url": url,
        "status": "ok",
        "fetched_at": utc_now(),
        "path": str(dest.relative_to(REPO_ROOT)),
        "sha256": sha256_bytes(body),
        "http_status": status,
        "bytes": len(body),
    }


def git_sha(path: Path) -> str:
    """Return the HEAD commit SHA of the git checkout at ``path``.

    Raises:
        subprocess.CalledProcessError: If ``path`` is not a git checkout or git fails.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_git(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def sync_repo(spec: RepoSpec, dest: Path) -> dict[str, Any]:
    """Clone or update ``spec`` into ``dest`` and report the outcome.

    An existing clone is fetched and hard-reset to ``FETCH_HEAD``. A new clone is
    shallow, and sparse (README only) when ``spec.readme_only`` is set.

    Args:
        spec: Repo to sync.
        dest: Clone directory. Its parent is created.

    Returns:
        On success, a record with ``status`` ``"ok"``, ``sha``, repo-relative
        ``path`` and ``mode``. If git fails, times out or hits an OS error (for
        example a private repo), ``status`` ``"skipped"`` with a ``reason``; nothing
        is raised.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if (dest / ".git").exists():
            _run_git(["git", "fetch", "--depth", "1", "origin"], cwd=dest)
            _run_git(["git", "reset", "--hard", "FETCH_HEAD"], cwd=dest)
        elif spec.readme_only:
            _run_git(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    spec.url,
                    str(dest),
                ]
            )
            try:
                _run_git(["git", "sparse-checkout", "set", "--skip-checks", "README.md"], cwd=dest)
            except subprocess.CalledProcessError:
                LOGGER.info("%s sparse-checkout README skipped; using clone as-is", spec.name)
        else:
            _run_git(["git", "clone", "--depth", "1", spec.url, str(dest)])
        sha = git_sha(dest)
        LOGGER.info("Repo %s @ %s", spec.name, sha[:12])
        return {
            "name": spec.name,
            "url": spec.url,
            "status": "ok",
            "sha": sha,
            "path": str(dest.relative_to(REPO_ROOT)),
            "mode": spec.mode,
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        LOGGER.warning("Skipping private/unavailable repo %s: %s", spec.name, detail)
        return {
            "name": spec.name,
            "url": spec.url,
            "status": "skipped",
            "reason": detail,
            "mode": spec.mode,
        }
