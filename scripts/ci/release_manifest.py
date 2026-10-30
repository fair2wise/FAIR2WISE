#!/usr/bin/env python3
"""Release manifest for the three FAIR2WISE image variants (T24D).

``build`` (``.github/workflows/build-app.yml``) writes ``release-manifest.json`` after the
three candidates of one commit were built and checked by ``scripts/ci/image_checks.py``:
the commit, the identity of each variant (image id, rootfs layer digests, platform, labels,
check results), the dependency lock hashes, the pinned Tiled wheel hash, the frozen UI tree
and the base images that were used. Nothing in it is a credential.

``verify`` (``.github/workflows/publish-image.yml``) refuses a manifest that is not a passed
candidate of the expected commit, or images whose identity differs from what was tested.

:func:`build_publication` (used by ``publish_candidate.py``) records what the publish job
pushed: per variant the registry manifest digest and the tags written. It may reference a
binary evaluation report and its verdict for information; a verdict never gates
a build or a publication (owner decision H3 B).

Standard library only; never imports the application (no ``load_dotenv`` side effects).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA = "f2w.release-manifest/v1"
PUBLICATION_SCHEMA = "f2w.release-publication/v1"
VARIANTS = ("local", "als-dev", "als-prod")
TILED_MODE = {"local": "managed", "als-dev": "external", "als-prod": "external"}
TILED_URL = {"local": None, "als-dev": "https://tiled-staging.als.lbl.gov", "als-prod": "https://tiled.als.lbl.gov"}
REQUIRED_RUNTIME_ENV = {"local": [], "als-dev": ["F2W_CORS_ORIGINS"], "als-prod": ["F2W_CORS_ORIGINS"]}
REQUIRED_MOUNTS = {
    "local": ["/var/lib/fair2wise/state", "/var/lib/fair2wise/tiled"],
    "als-dev": ["/var/lib/fair2wise/state"],
    "als-prod": ["/var/lib/fair2wise/state"],
}
BASE_PATH = "/fair2wise"
GATEWAY_PORT = 8080

# Files whose bytes decide what goes into the three images.
LOCK_FILES = (
    "requirements.txt",
    "requirements/runtime.txt",
    "requirements/runtime.lock.txt",
    "requirements/semantic.in",
    "ui/package-lock.json",
    "docker/profiles/tiled-server/constraints.txt",
    "docker/profiles/tiled-server/constraints.lock.txt",
    "docker/profiles/local.json",
    "docker/profiles/als-dev.json",
    "docker/profiles/als-prod.json",
    "Dockerfile",
    ".dockerignore",
)
FROZEN_UI_SOURCE = ".github/workflows/forge-merge-ci.yml"
TILED_CONFIG = "app/modules/deployment/tiled_config.py"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ManifestError(Exception):
    """The manifest or the images do not satisfy the release contract."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def frozen_ui_pin(root: Path) -> str:
    """The ``FROZEN_UI_TREE`` pin from the migration CI workflow (single source of truth)."""
    text = (root / FROZEN_UI_SOURCE).read_text(encoding="utf-8")
    match = re.search(r"^\s*FROZEN_UI_TREE:\s*([0-9a-f]{40})\s*$", text, re.MULTILINE)
    if not match:
        raise ManifestError(f"FROZEN_UI_TREE not found in {FROZEN_UI_SOURCE}")
    return match.group(1)


def tiled_pins(root: Path) -> Dict[str, Any]:
    """Tiled version and wheel hash from tiled_config.py, cross-checked against the Dockerfile."""
    text = (root / TILED_CONFIG).read_text(encoding="utf-8")
    pins = {}
    for name in ("PINNED_TILED_VERSION", "PINNED_TILED_WHEEL", "PINNED_TILED_WHEEL_SHA256"):
        match = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match:
            raise ManifestError(f"{name} not found in {TILED_CONFIG}")
        pins[name] = match.group(1)
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    in_dockerfile = f'{pins["PINNED_TILED_WHEEL_SHA256"]}  /tmp/tiled/wheel/{pins["PINNED_TILED_WHEEL"]}' in dockerfile
    return {
        "version": pins["PINNED_TILED_VERSION"],
        "wheel": pins["PINNED_TILED_WHEEL"],
        "wheel_sha256": pins["PINNED_TILED_WHEEL_SHA256"],
        "dockerfile_checks_same_hash": in_dockerfile,
        "bundled_in": ["local"],
    }


def load_checks(paths: Sequence[Path]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        variant = data.get("variant")
        if variant not in VARIANTS:
            raise ManifestError(f"{path}: unknown variant {variant!r}")
        if variant in out:
            raise ManifestError(f"{path}: duplicate variant {variant}")
        out[variant] = data
    return out


def build_manifest(*, root: Path, commit: str, ref: str, event: str, repository: str, image_repository: str,
                   run_id: str, run_attempt: str, server_url: str, checks: Mapping[str, Mapping[str, Any]],
                   base_images: Mapping[str, str]) -> Dict[str, Any]:
    """Assemble the manifest; ``candidate_ok`` says whether it may ever be published."""
    if not COMMIT_RE.match(commit):
        raise ManifestError(f"commit must be a full 40-hex object id, got {commit!r}")
    problems: List[str] = []
    pinned_ui = frozen_ui_pin(root)
    actual_ui = git(root, "rev-parse", f"{commit}:ui")
    if actual_ui != pinned_ui:
        problems.append(f"ui tree {actual_ui} differs from FROZEN_UI_TREE {pinned_ui}")
    tiled = tiled_pins(root)
    if not tiled["dockerfile_checks_same_hash"]:
        problems.append("Dockerfile does not check the pinned Tiled wheel sha256")
    variants: Dict[str, Any] = {}
    for variant in VARIANTS:
        report = checks.get(variant)
        if report is None:
            problems.append(f"variant {variant} missing")
            continue
        identity = report.get("identity") or {}
        labels = identity.get("labels") or {}
        if not report.get("passed"):
            failed = [c["name"] for c in report.get("checks", []) if not c.get("ok")]
            problems.append(f"{variant}: checks failed: {', '.join(failed) or 'none recorded'}")
        if labels.get("org.opencontainers.image.revision") != commit:
            problems.append(f"{variant}: image revision label is not {commit}")
        variants[variant] = {
            "docker_target": variant,
            "image_id": identity.get("image_id"),
            "rootfs_layers": identity.get("rootfs_layers") or [],
            "platform": identity.get("platform"),
            "size": identity.get("size"),
            "labels": labels,
            "base_path": BASE_PATH,
            "gateway_port": GATEWAY_PORT,
            "tiled_mode": TILED_MODE[variant],
            "tiled_url": TILED_URL[variant],
            "bundles_tiled_server": variant == "local",
            "required_runtime_env": REQUIRED_RUNTIME_ENV[variant],
            "required_mounts": REQUIRED_MOUNTS[variant],
            "checks": report.get("checks", []),
            "checks_passed": bool(report.get("passed")),
        }
    platforms = sorted({v["platform"] for v in variants.values() if v.get("platform")})
    return {
        "schema": SCHEMA,
        "repository": repository,
        "image_repository": image_repository,
        "commit": commit,
        "ref": ref,
        "event": event,
        "workflow_run": {
            "id": run_id,
            "attempt": run_attempt,
            "url": f"{server_url}/{repository}/actions/runs/{run_id}" if run_id else None,
        },
        "frozen_ui_tree": {"pinned": pinned_ui, "actual": actual_ui, "matches": actual_ui == pinned_ui},
        "locks": {name: sha256_file(root / name) for name in LOCK_FILES if (root / name).exists()},
        "tiled": tiled,
        "base_images": dict(sorted(base_images.items())),
        "platforms_tested": platforms,
        "variants": variants,
        "data_release": None,
        "notes": [
            "Image identity only. Graph snapshots and their binary reports are released separately (D31, T26B).",
            "No corpus, credential or Tiled catalog is part of any variant (checked per variant above).",
        ],
        "candidate_ok": not problems,
        "problems": problems,
    }


def verify_manifest(manifest: Mapping[str, Any], *, commit: str) -> None:
    """Refuse anything but a passed three-variant candidate of ``commit``."""
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"unexpected schema {manifest.get('schema')!r}")
    if manifest.get("commit") != commit:
        raise ManifestError(f"manifest commit {manifest.get('commit')!r} is not {commit!r}")
    if manifest.get("event") == "pull_request":
        raise ManifestError("a pull_request candidate is never published")
    if not manifest.get("candidate_ok") or manifest.get("problems"):
        raise ManifestError(f"candidate did not pass: {manifest.get('problems')}")
    if not (manifest.get("frozen_ui_tree") or {}).get("matches"):
        raise ManifestError("ui tree does not match FROZEN_UI_TREE")
    variants = manifest.get("variants") or {}
    if sorted(variants) != sorted(VARIANTS):
        raise ManifestError(f"variants {sorted(variants)} are not {sorted(VARIANTS)}")
    if len({entry.get("image_id") for entry in variants.values()}) != len(VARIANTS):
        raise ManifestError("the three variants must be three different images")
    for name, entry in variants.items():
        if not entry.get("checks_passed"):
            raise ManifestError(f"{name}: checks did not pass")
        if not DIGEST_RE.match(str(entry.get("image_id"))):
            raise ManifestError(f"{name}: image_id {entry.get('image_id')!r} is not a sha256 digest")
        if not entry.get("rootfs_layers"):
            raise ManifestError(f"{name}: no rootfs layers recorded")


def verify_images(manifest: Mapping[str, Any], inspected: Sequence[Mapping[str, Any]],
                  refs: Mapping[str, str]) -> None:
    """The loaded images (``docker image inspect`` output) are byte-for-byte the tested ones."""
    by_id = {item.get("Id"): item for item in inspected}
    for variant, entry in (manifest.get("variants") or {}).items():
        item = by_id.get(entry["image_id"])
        if item is None:
            raise ManifestError(f"{variant}: tested image {entry['image_id']} is not among the loaded images")
        layers = list((item.get("RootFS") or {}).get("Layers") or [])
        if layers != entry["rootfs_layers"]:
            raise ManifestError(f"{variant}: rootfs layers differ from the tested image")
        labels = (item.get("Config") or {}).get("Labels") or {}
        if labels.get("f2w.profile") != variant:
            raise ManifestError(f"{variant}: loaded image has profile label {labels.get('f2w.profile')!r}")
        ref = refs.get(variant)
        if ref and ref not in (item.get("RepoTags") or []):
            raise ManifestError(f"{variant}: loaded image is not tagged {ref}")


def binary_reference(root: Path, rel_path: str) -> Dict[str, Any]:
    """Information-only reference to a binary evaluation report (never a gate)."""
    path = (root / rel_path).resolve()
    if root.resolve() not in path.parents:
        raise ManifestError("binary report must be inside the repository")
    raw = path.read_bytes()
    try:
        report = json.loads(raw)
    except ValueError:
        report = {}
    gate = report.get("gate") if isinstance(report.get("gate"), Mapping) else {}
    return {
        "path": rel_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "report_kind": report.get("report_kind"),
        "verdict": gate.get("status"),
        "provisional": gate.get("provisional"),
        "gating": False,
    }


def build_publication(manifest: Mapping[str, Any], *, digests: Mapping[str, str], tags: Mapping[str, List[str]],
                      run_url: str, actor: str, binary: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    for variant in VARIANTS:
        if not DIGEST_RE.match(str(digests.get(variant))):
            raise ManifestError(f"{variant}: published digest {digests.get(variant)!r} is not a sha256 digest")
    return {
        "schema": PUBLICATION_SCHEMA,
        "manifest_commit": manifest["commit"],
        "image_repository": manifest["image_repository"],
        "build_run": manifest["workflow_run"],
        "publish_run_url": run_url,
        "actor": actor,
        "variants": {
            v: {
                "image_id": manifest["variants"][v]["image_id"],
                "digest": digests[v],
                "reference": f"{manifest['image_repository']}@{digests[v]}",
                "tags": tags.get(v, []),
            }
            for v in VARIANTS
        },
        "binary_report": binary,
        "manifest": manifest,
    }


def _pairs(values: Sequence[str]) -> Dict[str, str]:
    out = {}
    for value in values:
        key, sep, val = value.partition("=")
        if not sep:
            raise ManifestError(f"expected NAME=VALUE, got {value!r}")
        out[key] = val
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build")
    b.add_argument("--root", type=Path, default=Path("."))
    b.add_argument("--commit", required=True)
    b.add_argument("--ref", required=True)
    b.add_argument("--event", required=True)
    b.add_argument("--repository", required=True)
    b.add_argument("--image-repository", required=True)
    b.add_argument("--run-id", default="")
    b.add_argument("--run-attempt", default="")
    b.add_argument("--server-url", default="https://github.com")
    b.add_argument("--checks", type=Path, nargs="+", required=True)
    b.add_argument("--base-image", action="append", default=[], help="NAME=RESOLVED_REFERENCE")
    b.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("verify")
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--commit", required=True)
    v.add_argument("--inspect", type=Path, help="docker image inspect JSON of the loaded images")
    v.add_argument("--ref", action="append", default=[], help="VARIANT=LOCAL_TAG")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest = build_manifest(
                root=args.root, commit=args.commit, ref=args.ref, event=args.event, repository=args.repository,
                image_repository=args.image_repository, run_id=args.run_id, run_attempt=args.run_attempt,
                server_url=args.server_url, checks=load_checks(args.checks), base_images=_pairs(args.base_image))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            for problem in manifest["problems"]:
                print(f"release manifest: {problem}", file=sys.stderr)
            return 0 if manifest["candidate_ok"] else 1
        if args.command == "verify":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            verify_manifest(manifest, commit=args.commit)
            if args.inspect:
                verify_images(manifest, json.loads(args.inspect.read_text(encoding="utf-8")), _pairs(args.ref))
            print(f"release manifest ok: {args.commit}")
            return 0
        raise ManifestError(f"unknown command {args.command}")
    except (ManifestError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"release manifest refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
