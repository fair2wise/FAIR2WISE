"""Behaviour of the T24D CI scripts with a fake docker/registry (no Docker, no network).

* scripts/ci/image_checks.py   - post-build checks per variant
* scripts/ci/release_manifest.py - release manifest build and verification
* scripts/ci/publish_candidate.py - immutable-tag publication of the tested bytes
* scripts/ci/promote_channel.py  - channel re-tag rules (forward, rollback, prod after dev)
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

REPO = Path(__file__).resolve().parents[2]
CI = REPO / "scripts" / "ci"
COMMIT = "a" * 40
OLDER = "b" * 40
NEWER = "c" * 40
REPO_IMG = "ghcr.io/fair2wise/fair2wise"


def _load(name: str):
    if str(CI) not in sys.path:
        sys.path.insert(0, str(CI))
    spec = importlib.util.spec_from_file_location(name, CI / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ic = _load("image_checks")
rm = _load("release_manifest")
pc = _load("publish_candidate")
pr = _load("promote_channel")


def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


def digest(ch: str) -> str:
    return "sha256:" + ch * 64


# ------------------------------------------------------------------ image_checks ---------
class FakeImage:
    """Answers the docker calls image_checks makes, as a correct image would."""

    def __init__(self, variant: str, **overrides: Any) -> None:
        self.variant = variant
        self.o = overrides
        self.calls: List[List[str]] = []

    def inspect(self) -> Dict[str, Any]:
        labels = {"f2w.profile": self.variant, "f2w.base_path": "/fair2wise",
                  "f2w.tiled_mode": ic.TILED_MODE[self.variant],
                  "org.opencontainers.image.revision": COMMIT}
        labels.update(self.o.get("labels", {}))
        env = ["PATH=/usr/bin", "F2W_DOTENV=off"] + self.o.get("env", [])
        return {"Id": digest("1"), "Os": "linux", "Architecture": "amd64", "Size": 1,
                "RootFS": {"Layers": [digest("2"), digest("3")]},
                "Config": {"User": self.o.get("user", "10001:10001"), "Labels": labels, "Env": env,
                           "ExposedPorts": {"8080/tcp": {}}}}

    def __call__(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return done(out=json.dumps([self.inspect()]))
        if args[0] == "rm":
            return done()
        assert args[0] == "run"
        rest = args[args.index(f"img:{self.variant}") + 1:]
        entry = args[args.index("--entrypoint") + 1] if "--entrypoint" in args else None
        if entry == "id":
            return done(out=self.o.get("id", "uid=10001(fair2wise) gid=10001(fair2wise)\n"))
        if entry == "sh" and "find" in rest[-1]:
            return done(1, out=self.o.get("find", ""))
        if entry == "sh":
            return done(self.o.get("opt_tiled", 0 if self.variant != "local" else 1))
        if entry == "python" and rest[:1] == [ic.PREFLIGHT[0]]:
            return self.preflight(args)
        if entry and entry.endswith("python"):
            return done(out="0.2.18\n")
        if rest == ["--check"]:
            services = [{"name": n} for n in (["tiled"] if self.variant == "local" else []) + ["api", "worker", "gateway"]]
            services += [{"name": "tiled"}] if self.o.get("als_tiled") else []
            return done(out=json.dumps({"profile": self.variant, "tiled_mode": ic.TILED_MODE[self.variant],
                                        "env": {"F2W_GATEWAY_PORT": "8080", "F2W_DOTENV": "off"},
                                        "services": services}))
        # A refusal: the profile refuses everything the checks try.
        code = self.o.get("refusal_code", 78)
        text = "preflight: F2W_CORS_ORIGINS is required" if "--tmpfs" in args else "refused: not a mount point"
        return done(code, err=text)

    def preflight(self, args: List[str]) -> subprocess.CompletedProcess:
        """The image preflight run directly: passes only with every mount and, on ALS, CORS."""
        mounted = {a.split(":", 1)[0] for i, a in enumerate(args) if i and args[i - 1] == "--tmpfs"}
        cors = any(a.startswith("F2W_CORS_ORIGINS=") for a in args)
        problems = [f"{m} is not a mount point" for m in ic.MOUNTS[self.variant] if m not in mounted]
        if self.variant != "local" and not cors:
            problems.append("F2W_CORS_ORIGINS is required")
        facts = {"profile": self.variant, "gateway_port": self.o.get("gateway_port", 8080),
                 "cors_origins": int(cors)}
        result = {"ok": not problems, "problems": problems, "facts": facts}
        if problems:
            return done(78, err=json.dumps(result))
        return done(out=json.dumps(result))


@pytest.mark.parametrize("variant", ic.VARIANTS)
def test_image_checks_pass_for_a_correct_image(variant: str) -> None:
    fake = FakeImage(variant)
    report = ic.Checker(variant, f"img:{variant}", commit=COMMIT, tiled_version="0.2.18", runner=fake).run_all()
    assert report.passed, [r for r in report.results if not r.ok]
    names = {r.name for r in report.results}
    assert {"--check plan", "no private files (find)", "uid 10001", "user is 10001"} <= names
    assert any(n.startswith("exit 78: F2W_DEPLOYMENT_PROFILE") for n in names)
    if variant == "local":
        assert "tiled 0.2.18 present" in names
    else:
        assert {"no tiled server (/opt/tiled absent)", "exit 78: F2W_CORS_ORIGINS required",
                "exit 78: state not mounted, no F2W_CORS_ORIGINS", "exit 78: other central F2W_TILED_URL"} <= names
    assert "exit 78: state not mounted" in names
    assert "preflight passes as deployed" in names
    runs = [c for c in fake.calls if c[0] == "run"]
    assert runs and all(c[c.index("--network") + 1] == "none" for c in runs)
    # The ALS plan and preflight are resolved as deployed: with a browser origin.
    check = next(c for c in runs if c[-1] == "--check")
    assert ("-e", f"F2W_CORS_ORIGINS={ic.CI_ORIGIN}") in zip(check, check[1:]) or variant == "local"
    for call in runs:
        passed = [call[i + 1].split("=", 1)[0] for i, a in enumerate(call) if a == "-e"]
        assert not [n for n in passed if ic.SECRET_NAME.search(n)], passed
    assert report.identity["image_id"] == digest("1")


@pytest.mark.parametrize("override,failed", [
    ({"find": "/app/papers/x.pdf\n"}, "no private files (find)"),
    ({"id": "uid=0(root)\n"}, "uid 10001"),
    ({"user": "root"}, "user is 10001"),
    ({"refusal_code": 0}, "exit 78: F2W_DEPLOYMENT_PROFILE=local"),
    ({"refusal_code": 124}, "exit 78: F2W_DEPLOYMENT_PROFILE=local"),
    ({"opt_tiled": 1}, "no tiled server (/opt/tiled absent)"),
    ({"als_tiled": True}, "--check plan"),
    ({"labels": {"f2w.profile": "als-prod"}}, "label f2w.profile"),
    ({"labels": {"org.opencontainers.image.revision": OLDER}}, "label revision is the build commit"),
    ({"env": ["CBORG_API_KEY=x"]}, "no credential names in the image environment"),
    ({"gateway_port": 8090}, "preflight passes as deployed"),
])
def test_image_checks_fail_on_a_bad_image(override: Dict[str, Any], failed: str) -> None:
    fake = FakeImage("als-dev", **override)
    report = ic.Checker("als-dev", "img:als-dev", commit=COMMIT, tiled_version="0.2.18", runner=fake).run_all()
    assert not report.passed
    assert failed in {r.name for r in report.results if not r.ok}
    if override.get("refusal_code") == 124:
        assert any(c[0] == "rm" for c in fake.calls), "a container that did not refuse is removed"


# ------------------------------------------------------------------ release_manifest -----
def _checks(tmp: Path, *, fail: Optional[str] = None) -> List[Path]:
    paths = []
    for variant in rm.VARIANTS:
        report = {"variant": variant, "passed": variant != fail,
                  "identity": {"image_id": digest(str(rm.VARIANTS.index(variant) + 1)),
                               "rootfs_layers": [digest("e"), digest(str(rm.VARIANTS.index(variant) + 5))],
                               "platform": "linux/amd64", "size": 1,
                               "labels": {"f2w.profile": variant, "org.opencontainers.image.revision": COMMIT}},
                  "checks": [{"name": "uid 10001", "ok": variant != fail, "detail": ""}]}
        path = tmp / f"checks-{variant}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    return paths


def _manifest(tmp: Path, **kw: Any) -> Dict[str, Any]:
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    checks = rm.load_checks(_checks(tmp, fail=kw.pop("fail", None)))
    for entry in checks.values():
        entry["identity"]["labels"]["org.opencontainers.image.revision"] = head
    manifest = rm.build_manifest(root=REPO, commit=head, ref="refs/heads/main", event=kw.pop("event", "push"),
                                 repository="fair2wise/fair2wise", image_repository=REPO_IMG, run_id="42",
                                 run_attempt="1", server_url="https://github.com", checks=checks,
                                 base_images={"python:3.12-slim": "python@" + digest("9")})
    return manifest


def test_manifest_records_commit_digests_locks_tiled_and_ui_tree(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    assert m["schema"] == "f2w.release-manifest/v1"
    assert set(m["variants"]) == set(rm.VARIANTS)
    assert m["variants"]["local"]["bundles_tiled_server"] is True
    assert m["variants"]["als-prod"]["tiled_url"] == "https://tiled.als.lbl.gov"
    assert m["variants"]["als-dev"]["required_runtime_env"] == ["F2W_CORS_ORIGINS"]
    assert {"requirements.txt", "ui/package-lock.json", "docker/profiles/tiled-server/constraints.txt"} <= set(m["locks"])
    assert m["tiled"]["wheel_sha256"] == "416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06"
    assert m["tiled"]["dockerfile_checks_same_hash"] is True
    assert m["frozen_ui_tree"]["pinned"] == rm.frozen_ui_pin(REPO)
    assert m["frozen_ui_tree"]["matches"] is (m["frozen_ui_tree"]["actual"] == m["frozen_ui_tree"]["pinned"])
    assert m["platforms_tested"] == ["linux/amd64"]
    if m["frozen_ui_tree"]["matches"]:
        assert m["candidate_ok"] and not m["problems"]
        rm.verify_manifest(m, commit=m["commit"])


def test_manifest_with_a_failed_variant_is_never_publishable(tmp_path: Path) -> None:
    m = _manifest(tmp_path, fail="als-prod")
    assert not m["candidate_ok"]
    with pytest.raises(rm.ManifestError):
        rm.verify_manifest(m, commit=m["commit"])


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(event="pull_request"),
    lambda m: m.update(commit=OLDER),
    lambda m: m["variants"].pop("als-dev"),
    lambda m: m["variants"]["local"].update(image_id="latest"),
    lambda m: m["frozen_ui_tree"].update(matches=False),
])
def test_verify_manifest_refuses(tmp_path: Path, mutate) -> None:
    m = _manifest(tmp_path)
    m.update(candidate_ok=True, problems=[])
    m["frozen_ui_tree"]["matches"] = True
    commit = m["commit"]
    mutate(m)
    with pytest.raises(rm.ManifestError):
        rm.verify_manifest(m, commit=commit)


def test_verify_images_needs_the_tested_ids_and_layers(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    loaded = [{"Id": e["image_id"], "RootFS": {"Layers": e["rootfs_layers"]}, "RepoTags": [f"f2w-candidate:{v}"],
               "Config": {"Labels": {"f2w.profile": v}}} for v, e in m["variants"].items()]
    refs = {v: f"f2w-candidate:{v}" for v in rm.VARIANTS}
    rm.verify_images(m, loaded, refs)
    loaded[1]["RootFS"]["Layers"] = [digest("f")]
    with pytest.raises(rm.ManifestError, match="rootfs layers"):
        rm.verify_images(m, loaded, refs)


def test_binary_report_is_recorded_but_never_gates(tmp_path: Path) -> None:
    ref = rm.binary_reference(REPO, "docs/migration/reviews/H3/evidence/binary_report_pre_h3.json")
    assert ref["gating"] is False and len(ref["sha256"]) == 64
    with pytest.raises(rm.ManifestError):
        rm.binary_reference(REPO, "../outside.json")


# ------------------------------------------------------------------ registry fakes -------
class FakeRegistry:
    """Tags -> digest, digest -> config, and a linear commit history OLDER < COMMIT < NEWER."""

    HISTORY = [OLDER, COMMIT, NEWER]

    def __init__(self) -> None:
        self.tags: Dict[str, str] = {}
        self.configs: Dict[str, Dict[str, Any]] = {}
        self.media: Dict[str, str] = {}
        self.calls: List[List[str]] = []
        self.local: Dict[str, Dict[str, Any]] = {}
        self.retag_to: Optional[str] = None

    def add(self, dg: str, variant: str, revision: str, layers: Optional[List[str]] = None, *tags: str) -> None:
        self.configs[dg] = {"config": {"Labels": {"f2w.profile": variant, "org.opencontainers.image.revision": revision}},
                            "rootfs": {"diff_ids": layers or [digest("e")]}}
        for tag in tags:
            self.tags[f"{REPO_IMG}:{tag}"] = dg

    def resolve(self, ref: str) -> Optional[str]:
        if "@" in ref:
            dg = ref.split("@", 1)[1]
            return dg if dg in self.configs else None
        return self.tags.get(ref)

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess:
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            dg = self.resolve(cmd[4])
            if dg is None:
                return done(1, err="not found")
            if cmd[6] == "{{json .Manifest}}":
                return done(out=json.dumps({"digest": dg, "mediaType": self.media.get(
                    dg, "application/vnd.oci.image.manifest.v1+json")}))
            return done(out=json.dumps(self.configs[dg]))
        if cmd[:4] == ["docker", "buildx", "imagetools", "create"]:
            source = self.resolve(cmd[6])
            self.tags[cmd[5]] = self.retag_to or source
            return done()
        if cmd[:2] == ["docker", "tag"]:
            self.local[cmd[3]] = self.local[cmd[2]]
            return done()
        if cmd[:2] == ["docker", "push"]:
            image = self.local[cmd[2]]
            self.add(image["digest"], image["variant"], image["revision"], image["layers"])
            self.tags[cmd[2]] = image["digest"]
            return done()
        if cmd[:2] == ["git", "-C"] and cmd[3:5] == ["merge-base", "--is-ancestor"]:
            older, newer = cmd[5], cmd[6]
            return done(0 if self.HISTORY.index(older) <= self.HISTORY.index(newer) else 1)
        raise AssertionError(cmd)


def promote(reg: FakeRegistry, variant: str, dg: str, rollback: bool = False, reason: str = "") -> Dict[str, Any]:
    return pr.promote(pr.Registry(REPO_IMG, runner=reg), variant=variant, digest=dg, rollback=rollback,
                      reason=reason, actor="owner", run_url="https://example.invalid/run")


# ------------------------------------------------------------------ promote_channel ------
def test_first_and_forward_promotion_retags_the_published_digest() -> None:
    reg = FakeRegistry()
    reg.add(digest("1"), "als-dev", OLDER, None, f"sha-{OLDER}-als-dev")
    reg.add(digest("2"), "als-dev", COMMIT, None, f"sha-{COMMIT}-als-dev")
    first = promote(reg, "als-dev", digest("1"))
    assert first["previous"]["digest"] is None and reg.tags[f"{REPO_IMG}:als-dev"] == digest("1")
    second = promote(reg, "als-dev", digest("2"))
    assert second["previous"] == {"digest": digest("1"), "revision": OLDER}
    assert reg.tags[f"{REPO_IMG}:als-dev"] == digest("2")
    assert not [c for c in reg.calls if c[:2] in (["docker", "push"], ["docker", "build"])]


def test_stale_build_cannot_move_a_channel_backwards_but_rollback_can() -> None:
    reg = FakeRegistry()
    reg.add(digest("1"), "local", OLDER, None, f"sha-{OLDER}-local")
    reg.add(digest("2"), "local", COMMIT, None, f"sha-{COMMIT}-local", "local")
    with pytest.raises(pr.PromotionRefused, match="backwards"):
        promote(reg, "local", digest("1"))
    with pytest.raises(pr.PromotionRefused, match="reason"):
        promote(reg, "local", digest("1"), rollback=True)
    record = promote(reg, "local", digest("1"), rollback=True, reason="bad release")
    assert record["rollback"] and reg.tags[f"{REPO_IMG}:local"] == digest("1")
    reg.add(digest("3"), "local", NEWER, None, f"sha-{NEWER}-local")
    with pytest.raises(pr.PromotionRefused, match="not older"):
        promote(reg, "local", digest("3"), rollback=True, reason="wrong way")


def test_als_prod_needs_the_same_commit_staged_on_als_dev() -> None:
    reg = FakeRegistry()
    reg.add(digest("1"), "als-dev", OLDER, None, f"sha-{OLDER}-als-dev", "als-dev")
    reg.add(digest("2"), "als-prod", COMMIT, None, f"sha-{COMMIT}-als-prod")
    with pytest.raises(pr.PromotionRefused, match="als-dev first"):
        promote(reg, "als-prod", digest("2"))
    reg.add(digest("3"), "als-dev", COMMIT, None, f"sha-{COMMIT}-als-dev", "als-dev")
    promote(reg, "als-prod", digest("2"))
    assert reg.tags[f"{REPO_IMG}:als-prod"] == digest("2")


@pytest.mark.parametrize("case", ["unknown", "wrong-profile", "not-published", "index", "retag-drift", "bad-digest"])
def test_promotion_refusals(case: str) -> None:
    reg = FakeRegistry()
    reg.add(digest("1"), "als-dev", COMMIT, None, f"sha-{COMMIT}-als-dev")
    target = digest("1")
    if case == "unknown":
        target = digest("7")
    elif case == "wrong-profile":
        reg.configs[digest("1")]["config"]["Labels"]["f2w.profile"] = "local"
    elif case == "not-published":
        reg.tags[f"{REPO_IMG}:sha-{COMMIT}-als-dev"] = digest("8")
    elif case == "index":
        reg.media[digest("1")] = "application/vnd.oci.image.index.v1+json"
    elif case == "retag-drift":
        reg.retag_to = digest("9")
    elif case == "bad-digest":
        target = "latest"
    with pytest.raises(pr.PromotionRefused):
        promote(reg, "als-dev", target)
    if case != "retag-drift":
        assert f"{REPO_IMG}:als-dev" not in reg.tags


# ------------------------------------------------------------------ publish_candidate ----
def _entry(n: str) -> Dict[str, Any]:
    return {"image_id": digest(n), "rootfs_layers": [digest("e"), digest(n)]}


def test_publish_pushes_immutable_and_release_tags_never_channels() -> None:
    reg = FakeRegistry()
    pub = pc.Publisher(REPO_IMG, runner=reg)
    reg.local["f2w-candidate:als-dev"] = {"digest": digest("4"), "variant": "als-dev", "revision": COMMIT,
                                          "layers": _entry("2")["rootfs_layers"]}
    out = pub.publish_variant("als-dev", _entry("2"), commit=COMMIT, local_ref="f2w-candidate:als-dev",
                              semver="v1.2.3")
    assert out == {"digest": digest("4"),
                   "tags": [f"{REPO_IMG}:sha-{COMMIT}-als-dev", f"{REPO_IMG}:v1.2.3-als-dev"]}
    assert f"{REPO_IMG}:als-dev" not in reg.tags
    # A re-run with the same bytes is idempotent and pushes nothing.
    pushes = len([c for c in reg.calls if c[:2] == ["docker", "push"]])
    pub.publish_variant("als-dev", _entry("2"), commit=COMMIT, local_ref="f2w-candidate:als-dev", semver="v1.2.3")
    assert len([c for c in reg.calls if c[:2] == ["docker", "push"]]) == pushes


def test_publish_refuses_to_overwrite_an_immutable_tag_or_push_other_bytes() -> None:
    reg = FakeRegistry()
    reg.add(digest("5"), "local", COMMIT, [digest("f")], f"sha-{COMMIT}-local")
    pub = pc.Publisher(REPO_IMG, runner=reg)
    with pytest.raises(pc.PublishRefused, match="never overwritten"):
        pub.publish_variant("local", _entry("1"), commit=COMMIT, local_ref="f2w-candidate:local", semver="")
    reg2 = FakeRegistry()
    reg2.local["f2w-candidate:local"] = {"digest": digest("6"), "variant": "local", "revision": COMMIT,
                                         "layers": [digest("0")]}
    with pytest.raises(pc.PublishRefused, match="differ from the tested image"):
        pc.Publisher(REPO_IMG, runner=reg2).publish_variant("local", _entry("1"), commit=COMMIT,
                                                            local_ref="f2w-candidate:local", semver="")
    reg3 = FakeRegistry()
    reg3.add(digest("7"), "local", OLDER, None, "v1.2.3-local")
    reg3.local["f2w-candidate:local"] = {"digest": digest("8"), "variant": "local", "revision": COMMIT,
                                         "layers": _entry("1")["rootfs_layers"]}
    with pytest.raises(pc.PublishRefused, match="v1.2.3-local"):
        pc.Publisher(REPO_IMG, runner=reg3).publish_variant("local", _entry("1"), commit=COMMIT,
                                                            local_ref="f2w-candidate:local", semver="v1.2.3")
