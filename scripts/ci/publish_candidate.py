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

Multi-architecture (K3s pilot): when several manifests are given (one per platform, built on native
runners), each platform's image is pushed as ``sha-<commit>-<variant>-<arch>`` under the same rules, then
the multi-arch index ``sha-<commit>-<variant>`` is created from exactly those per-platform digests
(registry-side, no rebuild); an existing index must already list the same per-platform digests. The
index digest is what channels, release tags and the publication record point at. With a single
manifest the behaviour is the previous single-platform one.

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

    def children(self, ref: str) -> Dict[str, str]:
        """``{"linux/arm64": digest}`` of an index; attestation entries (platform unknown) are skipped."""
        desc = self._inspect(ref, "{{json .Manifest}}")
        out: Dict[str, str] = {}
        for item in (desc or {}).get("manifests") or []:
            plat = item.get("platform") or {}
            if plat.get("os") in (None, "unknown") or plat.get("architecture") in (None, "unknown"):
                continue
            out[f"{plat['os']}/{plat['architecture']}"] = item["digest"]
        return out

    def publish_multiarch(self, variant: str, entries: Dict[str, Dict[str, Any]], *, commit: str,
                          local_refs: Dict[str, str], semver: str) -> Dict[str, Any]:
        """Push every platform image, then the immutable multi-arch index over exactly those digests.

        ``entries`` and ``local_refs`` are keyed by platform (``linux/amd64``).
        """
        per_platform: Dict[str, str] = {}
        tags: List[str] = []
        for platform in sorted(entries):
            arch = platform.split("/", 1)[1]
            out = self.publish_variant(variant, entries[platform], commit=commit, local_ref=local_refs[platform],
                                       semver="", suffix=f"-{arch}")
            per_platform[platform] = out["digest"]
            tags.extend(out["tags"])
        immutable = f"{self.repository}:sha-{commit}-{variant}"
        if self.digest(immutable):
            if self.children(immutable) != per_platform:
                raise PublishRefused(f"{immutable} exists with other platform images; immutable tags are never overwritten")
        else:
            self._ok(["docker", "buildx", "imagetools", "create", "--tag", immutable,
                      *[f"{self.repository}@{per_platform[p]}" for p in sorted(per_platform)]])
        index = self.digest(immutable)
        if not index or not rm.DIGEST_RE.match(index):
            raise PublishRefused(f"{immutable}: no digest after creating the index")
        if self.children(f"{self.repository}@{index}") != per_platform:
            raise PublishRefused(f"{immutable}: the index does not list exactly the tested platform images")
        tags.append(immutable)
        if semver:
            if not SEMVER_RE.match(semver):
                raise PublishRefused(f"not a release tag: {semver!r}")
            release = f"{self.repository}:{semver}-{variant}"
            current = self.digest(release)
            if current is None:
                self._ok(["docker", "buildx", "imagetools", "create", "--tag", release, f"{self.repository}@{index}"])
                current = self.digest(release)
            if current != index:
                raise PublishRefused(f"{release} resolves to {current!r}, not {index}")
            tags.append(release)
        return {"digest": index, "tags": tags, "platform_digests": per_platform}

    def publish_variant(self, variant: str, entry: Dict[str, Any], *, commit: str, local_ref: str,
                        semver: str, suffix: str = "") -> Dict[str, Any]:
        immutable = f"{self.repository}:sha-{commit}-{variant}{suffix}"
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


def _arch(platform: str) -> str:
    return platform.split("/", 1)[1]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, nargs="+", required=True,
                        help="one release manifest per platform (several: multi-arch index)")
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
        manifests = [json.loads(path.read_text(encoding="utf-8")) for path in args.manifest]
        by_platform: Dict[str, Dict[str, Any]] = {}
        for manifest in manifests:
            rm.verify_manifest(manifest, commit=args.commit)
            if manifest.get("image_repository") != args.repository:
                raise PublishRefused(f"manifest names {manifest.get('image_repository')!r}, not {args.repository!r}")
            platform = manifest.get("platform")
            if len(manifests) > 1:
                if platform not in rm.PLATFORMS:
                    raise PublishRefused(f"manifest platform {platform!r} is not one of {rm.PLATFORMS}")
                if platform in by_platform:
                    raise PublishRefused(f"two manifests for {platform}")
            by_platform[platform] = manifest
        pub = Publisher(args.repository)
        digests: Dict[str, str] = {}
        tags: Dict[str, List[str]] = {}
        platform_digests: Dict[str, Dict[str, str]] = {}
        for variant in rm.VARIANTS:
            if len(manifests) == 1:
                out = pub.publish_variant(variant, manifests[0]["variants"][variant], commit=args.commit,
                                          local_ref=f"{args.local_prefix}:{variant}", semver=args.semver)
            else:
                out = pub.publish_multiarch(
                    variant, {p: m["variants"][variant] for p, m in by_platform.items()}, commit=args.commit,
                    local_refs={p: f"{args.local_prefix}:{variant}-{_arch(p)}" for p in by_platform},
                    semver=args.semver)
                platform_digests[variant] = out["platform_digests"]
            digests[variant], tags[variant] = out["digest"], out["tags"]
            print(f"published {variant}: {args.repository}@{out['digest']} {' '.join(out['tags'])}")
        binary = rm.binary_reference(args.root, args.binary_report) if args.binary_report else None
        record = rm.build_publication(manifests[0], digests=digests, tags=tags, run_url=args.run_url,
                                      actor=args.actor, binary=binary,
                                      platform_digests=platform_digests or None,
                                      manifests=by_platform if len(manifests) > 1 else None)
    except (PublishRefused, rm.ManifestError, OSError, ValueError) as exc:
        print(f"publish refused: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
