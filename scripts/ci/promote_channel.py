#!/usr/bin/env python3
"""Move one channel tag (``:local``, ``:als-dev``, ``:als-prod``) to an already published digest (T24D).

Used only by ``.github/workflows/publish-image.yml`` inside an approved GitHub environment.
Promotion is a registry-side re-tag of the exact tested bytes; nothing is built or pushed
from the runner. The approved O06 tag rules (docs/migration/reviews/H6/o06_tag_rules_proposal.md,
APPROVED 2026-10-30, owner decisions O-O6 D2.1-D2.10) are enforced here and fail closed:

1. the target ``REPO@sha256:...`` is a single-platform manifest, or a multi-arch index every
   platform image of which carries the same ``f2w.profile`` label (the channel) and the same
   ``org.opencontainers.image.revision`` label (a full commit);
2. the immutable ``sha-<commit>-<variant>`` tag written by the publish job points at that
   same digest (so only published, tested candidates can be promoted);
3. a forward promotion never moves a channel backwards: the channel's current revision must
   be an ancestor of (or equal to) the target revision; a rollback is explicit, needs a
   reason, and must move to an ancestor;
4. after ``docker buildx imagetools create`` the channel must resolve to the target digest.

Any tested, published digest may reach ``:als-prod`` (D2.3): the former "same commit must be
on ``:als-dev`` first" rule was dropped by the owner. On a push to ``main`` the publish
workflow moves all three channels to the new digests (D2.1); the manual dispatch path uses
this same script for rollbacks and for moving a channel to an older published digest.

Prints a JSON promotion record (previous and new digest) for the run summary and rollback.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

VARIANTS = ("local", "als-dev", "als-prod")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class PromotionRefused(Exception):
    """A promotion rule is not met; the channel was not moved."""


def run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False, timeout=300)


def immutable_tag(commit: str, variant: str) -> str:
    return f"sha-{commit}-{variant}"


class Registry:
    """The few ``docker buildx imagetools`` and ``git`` reads promotion needs."""

    def __init__(self, repository: str, runner: Runner = run, git_dir: Path = Path(".")) -> None:
        self.repository = repository
        self.runner = runner
        self.git_dir = git_dir

    def _inspect(self, ref: str, fmt: str) -> Optional[Any]:
        proc = self.runner(["docker", "buildx", "imagetools", "inspect", ref, "--format", fmt])
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)

    def descriptor(self, ref: str) -> Optional[Dict[str, Any]]:
        return self._inspect(ref, "{{json .Manifest}}")

    def digest(self, ref: str) -> Optional[str]:
        desc = self.descriptor(ref)
        return desc.get("digest") if isinstance(desc, dict) else None

    def _image_labels(self, ref: str) -> Dict[str, str]:
        image = self._inspect(ref, "{{json .Image}}")
        if not isinstance(image, dict) or "config" not in image:
            raise PromotionRefused(f"{ref}: not a single-platform image config")
        return (image.get("config") or {}).get("Labels") or {}

    def labels(self, ref: str) -> Dict[str, str]:
        """Labels of a single-platform image, or of a multi-arch index whose platform images agree."""
        desc = self.descriptor(ref)
        if not (isinstance(desc, dict) and desc.get("mediaType") in INDEX_TYPES):
            return self._image_labels(ref)
        repo = ref.split("@", 1)[0]
        kids = [m["digest"] for m in desc.get("manifests") or []
                if (m.get("platform") or {}).get("os") not in (None, "unknown")
                and (m.get("platform") or {}).get("architecture") not in (None, "unknown")]
        if not kids:
            raise PromotionRefused(f"{ref}: index lists no platform image")
        seen = [self._image_labels(f"{repo}@{kid}") for kid in kids]
        for key in ("f2w.profile", "org.opencontainers.image.revision"):
            if len({labels.get(key) for labels in seen}) != 1:
                raise PromotionRefused(f"{ref}: platform images disagree on label {key}")
        return seen[0]

    def retag(self, tag_ref: str, source_ref: str) -> None:
        proc = self.runner(["docker", "buildx", "imagetools", "create", "--tag", tag_ref, source_ref])
        if proc.returncode != 0:
            raise PromotionRefused(f"imagetools create failed: {proc.stderr.strip()[-300:]}")

    def is_ancestor(self, older: str, newer: str) -> bool:
        proc = self.runner(["git", "-C", str(self.git_dir), "merge-base", "--is-ancestor", older, newer])
        if proc.returncode not in (0, 1):
            raise PromotionRefused(f"cannot compare {older} and {newer}: {proc.stderr.strip()[-200:]}")
        return proc.returncode == 0


def promote(reg: Registry, *, variant: str, digest: str, rollback: bool, reason: str, actor: str,
            run_url: str) -> Dict[str, Any]:
    """Apply the rules and move ``:variant`` to ``digest``; return the promotion record."""
    if variant not in VARIANTS:
        raise PromotionRefused(f"unknown channel {variant!r}")
    if not DIGEST_RE.match(digest):
        raise PromotionRefused(f"digest must be sha256:<64 hex>, got {digest!r}")
    if rollback and not reason.strip():
        raise PromotionRefused("a rollback needs a reason")
    repo = reg.repository
    target_ref = f"{repo}@{digest}"
    desc = reg.descriptor(target_ref)
    if not isinstance(desc, dict) or desc.get("digest") != digest:
        raise PromotionRefused(f"{target_ref} is not in the registry")
    labels = reg.labels(target_ref)
    if labels.get("f2w.profile") != variant:
        raise PromotionRefused(f"target is a {labels.get('f2w.profile')!r} image, not {variant}")
    revision = labels.get("org.opencontainers.image.revision", "")
    if not COMMIT_RE.match(revision):
        raise PromotionRefused(f"target has no full commit revision label ({revision!r})")
    pinned = reg.digest(f"{repo}:{immutable_tag(revision, variant)}")
    if pinned != digest:
        raise PromotionRefused(f"{immutable_tag(revision, variant)} is {pinned!r}, not {digest}: "
                               "only digests written by the publish job can be promoted")

    channel_ref = f"{repo}:{variant}"
    previous_digest = reg.digest(channel_ref)
    previous_revision = None
    if previous_digest:
        previous_revision = reg.labels(f"{repo}@{previous_digest}").get("org.opencontainers.image.revision")
    record = {
        "channel": channel_ref,
        "variant": variant,
        "rollback": rollback,
        "reason": reason,
        "actor": actor,
        "run_url": run_url,
        "previous": {"digest": previous_digest, "revision": previous_revision},
        "new": {"digest": digest, "revision": revision, "reference": target_ref},
        "changed": previous_digest != digest,
    }
    if previous_digest == digest:
        return record

    if previous_revision:
        if rollback:
            if previous_revision == revision or not reg.is_ancestor(revision, previous_revision):
                raise PromotionRefused(f"rollback target {revision} is not older than the current {previous_revision}")
        elif not reg.is_ancestor(previous_revision, revision):
            raise PromotionRefused(f"{revision} does not descend from the current {previous_revision}: "
                                   "a stale build must not move the channel backwards (use rollback)")
    elif rollback:
        raise PromotionRefused("nothing to roll back: the channel does not exist yet")

    reg.retag(channel_ref, target_ref)
    moved = reg.digest(channel_ref)
    if moved != digest:
        raise PromotionRefused(f"{channel_ref} resolves to {moved!r} after re-tag, expected {digest}")
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True, help="e.g. ghcr.io/fair2wise/fair2wise")
    parser.add_argument("--variant", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--rollback", default="false")
    parser.add_argument("--reason", default="")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--git-dir", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    reg = Registry(args.repository, git_dir=args.git_dir)
    try:
        record = promote(reg, variant=args.variant, digest=args.digest, rollback=args.rollback == "true",
                         reason=args.reason, actor=args.actor, run_url=args.run_url)
    except (PromotionRefused, ValueError) as exc:
        print(f"promotion refused: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
