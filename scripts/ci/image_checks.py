#!/usr/bin/env python3
"""Post-build checks for one built FAIR2WISE image variant (T24D).

These are the docker/profiles/README.md "Static checks on the built images", run by
``.github/workflows/build-app.yml`` against every candidate before its identity is written
into the release manifest:

* image metadata: user 10001, profile/base-path/Tiled labels, gateway port 8080, the commit
  label, and no credential-like names in the baked environment;
* ``--check``: the supervisor's resolved plan for the right profile, Tiled mode and services;
* private-file ``find``: no PDF, source index, Tiled catalog, dotenv, SQLite or ``/app/papers``;
* ``id``: uid 10001;
* Tiled presence (``:local``: ``/opt/tiled/venv`` imports the pinned version) or absence (ALS);
* exit-78 refusals: a switched profile, a forbidden or foreign ``F2W_TILED_URL``, and for the
  ALS images unmounted state and a missing ``F2W_CORS_ORIGINS``.

Every container runs with ``--network none`` and only the ``-e`` values named here, so no
runner environment, credential or central Tiled server is reachable from a candidate. The
script needs only the ``docker`` CLI on ``PATH`` and the standard library.

Usage:
    python scripts/ci/image_checks.py --variant als-dev --image f2w-candidate:als-dev \
        --commit "$GITHUB_SHA" --tiled-version 0.2.18 --out candidate/checks-als-dev.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

VARIANTS = ("local", "als-dev", "als-prod")
TILED_MODE = {"local": "managed", "als-dev": "external", "als-prod": "external"}
TILED_URL = {"als-dev": "https://tiled-staging.als.lbl.gov", "als-prod": "https://tiled.als.lbl.gov"}
BASE_PATH = "/fair2wise"
GATEWAY_PORT = "8080"
RUNTIME_UID = "10001"
EXIT_CONFIG = 78
CI_ORIGIN = "https://f2w-ci.invalid"

# docker/profiles/README.md, "Static checks on the built images".
PRIVATE_FIND = (
    'find / -xdev \\( -iname "*.pdf" -o -path "*source_index*" -o -name "tiled_catalog*" -o -name ".env" '
    '-o -name "*.sqlite*" -o -path "/app/papers*" \\) 2>/dev/null'
)
SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.IGNORECASE)
# Baked names that match SECRET_NAME but carry no credential (file paths, switches).
SECRET_NAME_ALLOWED = frozenset()


@dataclass
class Result:
    """One named check and its outcome."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    """All checks for one variant plus the identity of the image they ran against."""

    variant: str
    image: str
    results: List[Result] = field(default_factory=list)
    identity: Dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(Result(name, bool(ok), detail))

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "variant": self.variant,
            "image": self.image,
            "passed": self.passed,
            "identity": self.identity,
            "checks": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in self.results],
        }


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def run_docker(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """Run ``docker <args>``; a timeout returns code 124 instead of raising."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(["docker", *args], 124, out, err)


class Checker:
    """Runs the checks for one variant through a docker runner (replaceable in tests)."""

    def __init__(self, variant: str, image: str, *, commit: str, tiled_version: str,
                 runner: Runner = run_docker, timeout: float = 300.0) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}")
        self.variant = variant
        self.image = image
        self.commit = commit
        self.tiled_version = tiled_version
        self.runner = runner
        self.timeout = timeout
        self.report = Report(variant, image)

    # -- helpers -------------------------------------------------------------------------
    def docker(self, *args: str) -> subprocess.CompletedProcess:
        return self.runner(list(args), self.timeout)

    def container(self, *args: str, env: Optional[Dict[str, str]] = None, entrypoint: Optional[str] = None,
                  extra: Sequence[str] = ()) -> subprocess.CompletedProcess:
        """``docker run`` a throwaway, network-less container of the candidate."""
        name = f"f2w-ci-{self.variant}-{uuid.uuid4().hex[:8]}"
        cmd = ["run", "--rm", "--name", name, "--network", "none"]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        cmd += list(extra)
        if entrypoint is not None:
            cmd += ["--entrypoint", entrypoint]
        cmd += [self.image, *args]
        proc = self.docker(*cmd)
        if proc.returncode == 124:  # a refusal that did not happen: the supervisor kept running
            self.docker("rm", "-f", name)
        return proc

    def refusal(self, name: str, *, env: Dict[str, str], extra: Sequence[str] = (),
                expect_text: Optional[str] = None) -> None:
        proc = self.container(env=env, extra=extra)
        output = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == EXIT_CONFIG and (expect_text is None or expect_text in output)
        self.report.add(name, ok, f"exit {proc.returncode}; {output.strip()[-300:]}")

    # -- checks --------------------------------------------------------------------------
    def check_inspect(self) -> None:
        proc = self.docker("image", "inspect", self.image)
        try:
            data = json.loads(proc.stdout)[0] if proc.returncode == 0 else None
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            self.report.add("inspect", False, (proc.stderr or "").strip()[-300:])
            return
        config = data.get("Config") or {}
        labels = config.get("Labels") or {}
        env_names = [item.split("=", 1)[0] for item in config.get("Env") or []]
        self.report.identity = {
            "image_id": data.get("Id"),
            "rootfs_layers": list((data.get("RootFS") or {}).get("Layers") or []),
            "platform": f"{data.get('Os')}/{data.get('Architecture')}",
            "size": data.get("Size"),
            "labels": labels,
        }
        self.report.add("inspect", bool(data.get("Id")), str(data.get("Id")))
        self.report.add("user is 10001", config.get("User") in (f"{RUNTIME_UID}:{RUNTIME_UID}", RUNTIME_UID),
                        str(config.get("User")))
        self.report.add("label f2w.profile", labels.get("f2w.profile") == self.variant, str(labels.get("f2w.profile")))
        self.report.add("label f2w.base_path", labels.get("f2w.base_path") == BASE_PATH, str(labels.get("f2w.base_path")))
        self.report.add("label f2w.tiled_mode", labels.get("f2w.tiled_mode") == TILED_MODE[self.variant],
                        str(labels.get("f2w.tiled_mode")))
        self.report.add("label revision is the build commit",
                        labels.get("org.opencontainers.image.revision") == self.commit,
                        str(labels.get("org.opencontainers.image.revision")))
        self.report.add("gateway port 8080 exposed", f"{GATEWAY_PORT}/tcp" in (config.get("ExposedPorts") or {}),
                        ",".join(sorted(config.get("ExposedPorts") or {})))
        leaked = [n for n in env_names if SECRET_NAME.search(n) and n not in SECRET_NAME_ALLOWED]
        self.report.add("no credential names in the image environment", not leaked, ",".join(leaked))

    def check_plan(self) -> None:
        proc = self.container("--check")
        try:
            plan = json.loads(proc.stdout)
        except ValueError:
            self.report.add("--check plan", False, f"exit {proc.returncode}; {(proc.stderr or '').strip()[-300:]}")
            return
        services = [s.get("name") for s in plan.get("services", [])]
        env = plan.get("env") or {}
        problems = []
        if proc.returncode != 0:
            problems.append(f"exit {proc.returncode}")
        if plan.get("profile") != self.variant:
            problems.append(f"profile {plan.get('profile')!r}")
        if plan.get("tiled_mode") != TILED_MODE[self.variant]:
            problems.append(f"tiled_mode {plan.get('tiled_mode')!r}")
        if env.get("F2W_GATEWAY_PORT") != GATEWAY_PORT:
            problems.append(f"F2W_GATEWAY_PORT {env.get('F2W_GATEWAY_PORT')!r}")
        if env.get("F2W_DOTENV") != "off":
            problems.append("children do not get F2W_DOTENV=off")
        for required in ("api", "worker", "gateway"):
            if required not in services:
                problems.append(f"service {required} missing")
        if (self.variant == "local") != ("tiled" in services):
            problems.append(f"tiled service {'missing' if self.variant == 'local' else 'present'}")
        self.report.add("--check plan", not problems, "; ".join(problems) or ",".join(map(str, services)))

    def check_private_files(self) -> None:
        proc = self.container("-c", PRIVATE_FIND, entrypoint="sh")
        found = (proc.stdout or "").strip()
        self.report.add("no private files (find)", proc.returncode in (0, 1) and not found, found[:500])

    def check_uid(self) -> None:
        proc = self.container(entrypoint="id")
        out = (proc.stdout or "").strip()
        self.report.add("uid 10001", proc.returncode == 0 and out.startswith(f"uid={RUNTIME_UID}("), out)

    def check_tiled(self) -> None:
        if self.variant == "local":
            proc = self.container("-I", "-c", "import tiled; print(tiled.__version__)",
                                  entrypoint="/opt/tiled/venv/bin/python")
            out = (proc.stdout or "").strip()
            self.report.add(f"tiled {self.tiled_version} present", proc.returncode == 0 and out == self.tiled_version,
                            out or (proc.stderr or "").strip()[-300:])
        else:
            proc = self.container("-c", "test ! -e /opt/tiled", entrypoint="sh")
            self.report.add("no tiled server (/opt/tiled absent)", proc.returncode == 0, f"exit {proc.returncode}")

    def check_refusals(self) -> None:
        other = next(v for v in VARIANTS if v != self.variant)
        self.refusal(f"exit 78: F2W_DEPLOYMENT_PROFILE={other}", env={"F2W_DEPLOYMENT_PROFILE": other})
        if self.variant == "local":
            self.refusal("exit 78: forbidden F2W_TILED_URL", env={"F2W_TILED_URL": TILED_URL["als-prod"]})
            return
        foreign = TILED_URL["als-prod" if self.variant == "als-dev" else "als-dev"]
        self.refusal("exit 78: other central F2W_TILED_URL", env={"F2W_TILED_URL": foreign})
        self.refusal("exit 78: state not mounted, no F2W_CORS_ORIGINS", env={})
        self.refusal("exit 78: F2W_CORS_ORIGINS required", env={},
                     extra=["--tmpfs", f"/var/lib/fair2wise/state:uid={RUNTIME_UID},gid={RUNTIME_UID},mode=0700"],
                     expect_text="F2W_CORS_ORIGINS")
        self.refusal("exit 78: malformed F2W_CORS_ORIGINS", env={"F2W_CORS_ORIGINS": "not-an-origin"},
                     extra=["--tmpfs", f"/var/lib/fair2wise/state:uid={RUNTIME_UID},gid={RUNTIME_UID},mode=0700"],
                     expect_text="F2W_CORS_ORIGINS")

    def run_all(self) -> Report:
        self.check_inspect()
        if not self.report.identity.get("image_id"):
            return self.report
        self.check_plan()
        self.check_private_files()
        self.check_uid()
        self.check_tiled()
        self.check_refusals()
        return self.report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", required=True, choices=VARIANTS)
    parser.add_argument("--image", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tiled-version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    report = Checker(args.variant, args.image, commit=args.commit, tiled_version=args.tiled_version,
                     timeout=args.timeout).run_all()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for r in report.results:
        print(f"{'ok  ' if r.ok else 'FAIL'} {args.variant}: {r.name}" + ("" if r.ok else f" ({r.detail})"))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
