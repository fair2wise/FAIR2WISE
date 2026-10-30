#!/usr/bin/env python3
"""Push the three tested candidates of one commit under their immutable tags (T24D).

Used only by ``.github/workflows/publish-image.yml`` (job ``publish``) after
``release_manifest.py verify`` accepted the manifest and the loaded images. Per variant it:

1. tags the loaded, verified image ``sha-<commit>-<variant>`` and pushes it, unless that tag
   already exists with the same rootfs layers (a re-run); an existing tag with other bytes is
   refused, never overwritten;
2. reads the registry digest back and checks the pushed config carries exactly the tested
   rootfs layers and the commit label;
3. for a release tag ``vX.Y.Z`` adds ``vX.Y.Z-<variant>`` by a registry-side re-tag of that
   digest (no rebuild, no second push); an existing semver tag must already equal the digest.

Channel tags (``:local``, ``:als-dev``, ``:als-prod``) are never written here; the publish
workflow moves them afterwards through ``promote_channel.py`` (all three on a push to ``main``,
owner decision O-O6 D2.1). Writes the publication record (``release_manifest.py``); its binary
report reference comes from the manifest unless ``--binary-report`` names another (D2.10,
information only).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_manifest as rm  # noqa: E402

SEMVER_RE = re.compile(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class PublishRefused(Exception):
    """The candidate cannot be published as tested."""


def run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False, timeout=1800)


class Publisher:
    def __init__(self, repository: str, runner: Runner = run) -> None:
        self.repository = repository
        self.runner = runner

    def _ok(self, cmd: Sequence[str]) -> str:
        proc = self.runner(cmd)
        if proc.returncode != 0:
            raise PublishRefused(f"{' '.join(cmd[:4])} failed: {(proc.stderr or '').strip()[-300:]}")
        return proc.stdout

    def _inspect(self, ref: str, fmt: str) -> Optional[Any]:
        proc = self.runner(["docker", "buildx", "imagetools", "inspect", ref, "--format", fmt])
        return json.loads(proc.stdout) if proc.returncode == 0 else None

    def digest(self, ref: str) -> Optional[str]:
        desc = self._inspect(ref, "{{json .Manifest}}")
        return desc.get("digest") if isinstance(desc, dict) else None

    def config(self, ref: str) -> Dict[str, Any]:
        image = self._inspect(ref, "{{json .Image}}")
        if not isinstance(image, dict) or "rootfs" not in image:
            raise PublishRefused(f"{ref}: not a single-platform image")
        return image

    def same_bytes(self, ref: str, entry: Dict[str, Any], commit: str) -> bool:
        image = self.config(ref)
        labels = (image.get("config") or {}).get("Labels") or {}
        return (list((image.get("rootfs") or {}).get("diff_ids") or []) == entry["rootfs_layers"]
                and labels.get("org.opencontainers.image.revision") == commit)

    def publish_variant(self, variant: str, entry: Dict[str, Any], *, commit: str, local_ref: str,
                        semver: str) -> Dict[str, Any]:
        immutable = f"{self.repository}:sha-{commit}-{variant}"
        existing = self.digest(immutable)
        if existing:
            if not self.same_bytes(immutable, entry, commit):
                raise PublishRefused(f"{immutable} exists with other bytes; immutable tags are never overwritten")
        else:
            self._ok(["docker", "tag", local_ref, immutable])
            self._ok(["docker", "push", immutable])
        digest = self.digest(immutable)
        if not digest or not rm.DIGEST_RE.match(digest):
            raise PublishRefused(f"{immutable}: no digest after push")
        if not self.same_bytes(f"{self.repository}@{digest}", entry, commit):
            raise PublishRefused(f"{immutable}: pushed layers or revision differ from the tested image")
        tags = [immutable]
        if semver:
            if not SEMVER_RE.match(semver):
                raise PublishRefused(f"not a release tag: {semver!r}")
            release = f"{self.repository}:{semver}-{variant}"
            current = self.digest(release)
            if current is None:
                self._ok(["docker", "buildx", "imagetools", "create", "--tag", release, f"{self.repository}@{digest}"])
                current = self.digest(release)
            if current != digest:
                raise PublishRefused(f"{release} resolves to {current!r}, not {digest}")
            tags.append(release)
        return {"digest": digest, "tags": tags}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--local-prefix", default="f2w-candidate")
    parser.add_argument("--semver", default="")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--binary-report", default="", help="override the manifest's report reference (information only)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        rm.verify_manifest(manifest, commit=args.commit)
        if manifest.get("image_repository") != args.repository:
            raise PublishRefused(f"manifest names {manifest.get('image_repository')!r}, not {args.repository!r}")
        pub = Publisher(args.repository)
        digests: Dict[str, str] = {}
        tags: Dict[str, List[str]] = {}
        for variant in rm.VARIANTS:
            out = pub.publish_variant(variant, manifest["variants"][variant], commit=args.commit,
                                      local_ref=f"{args.local_prefix}:{variant}", semver=args.semver)
            digests[variant], tags[variant] = out["digest"], out["tags"]
            print(f"published {variant}: {args.repository}@{out['digest']} {' '.join(out['tags'])}")
        binary = rm.binary_reference(args.root, args.binary_report) if args.binary_report else None
        record = rm.build_publication(manifest, digests=digests, tags=tags, run_url=args.run_url,
                                      actor=args.actor, binary=binary)
    except (PublishRefused, rm.ManifestError, OSError, ValueError) as exc:
        print(f"publish refused: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
