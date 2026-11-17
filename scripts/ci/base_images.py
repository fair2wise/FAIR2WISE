#!/usr/bin/env python3
"""Base images pinned in the Dockerfile, for the release manifest (K3s pilot).

The Dockerfile pins every base image as ``name:tag@sha256:<multi-arch index digest>`` in a global
``ARG``. ``build-app.yml`` calls this script to read those pins, pull each one for the platform
the job runs on (a pull by digest proves the pin still resolves and that the registry returns
the pinned bytes) and print ``--base-image`` arguments for ``release_manifest.py``: ``tag=repo@digest``.

Standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

PIN_RE = re.compile(r"^(?P<name>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")
ARG_RE = re.compile(r"^ARG\s+([A-Z0-9_]+)=(\S+)\s*$", re.MULTILINE)
DEFAULT_NAMES = ("PYTHON_IMAGE", "NODE_IMAGE", "NGINX_IMAGE", "PIXI_IMAGE")


class BaseImageError(Exception):
    """A base image is not pinned by digest or does not resolve to its pin."""


def read_pins(dockerfile_text: str, names: Sequence[str] = DEFAULT_NAMES) -> Dict[str, str]:
    """``{ARG name: 'repo:tag@sha256:...'}`` for the global ARGs before the first FROM."""
    head = dockerfile_text.split("\nFROM ", 1)[0]
    args = dict(ARG_RE.findall(head))
    pins: Dict[str, str] = {}
    for name in names:
        value = args.get(name)
        if value is None:
            raise BaseImageError(f"{name} is not a global ARG of the Dockerfile")
        if not PIN_RE.match(value):
            raise BaseImageError(f"{name}={value} is not pinned as name:tag@sha256:<digest>")
        pins[name] = value
    return pins


def manifest_arguments(pins: Dict[str, str]) -> Dict[str, str]:
    """``{'python:3.12-slim': 'python:3.12-slim@sha256:...'}``: the tag-reference to its resolved reference."""
    out: Dict[str, str] = {}
    for value in pins.values():
        match = PIN_RE.match(value)
        assert match
        out[match.group("name")] = value
    return out


def pull(ref: str, platform: Optional[str]) -> None:
    cmd = ["docker", "pull", "--quiet"] + (["--platform", platform] if platform else []) + [ref]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise BaseImageError(f"cannot pull {ref}: {(proc.stderr or '').strip()[-300:]}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--platform", default=None, help="pull for this platform, e.g. linux/arm64")
    parser.add_argument("--no-pull", action="store_true", help="only read and validate the pins")
    args = parser.parse_args(argv)
    try:
        pins = read_pins(args.dockerfile.read_text(encoding="utf-8"))
        resolved = manifest_arguments(pins)
        if not args.no_pull:
            for ref in resolved.values():
                pull(ref, args.platform)
    except (BaseImageError, OSError) as exc:
        print(f"base images refused: {exc}", file=sys.stderr)
        return 1
    for name, ref in resolved.items():
        print(f"{name}={ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
