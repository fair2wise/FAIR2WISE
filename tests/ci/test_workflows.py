"""Static rules for the image workflows (T24D). CI runs this in build-app.yml.

build-app.yml builds and checks the three variants of one commit and records them in a
release manifest; publish-image.yml publishes and promotes them. These tests keep the
security properties from drifting:

* no pull_request_target or workflow_run, no secrets (not even ``secrets: inherit``),
  read-only pull requests, every action a reviewed full-SHA pin (no placeholders);
* publishing only by manual dispatch, from a protected default branch or vX.Y.Z tag, inside
  the literal GitHub environments ghcr-publish / ghcr-als-prod, after an O06_APPROVED check
  that fails closed and cannot be bypassed with always()/failure()/continue-on-error, with
  only the job's GITHUB_TOKEN and ``packages: write``;
* promotion is a re-tag, never a rebuild; nothing deploys to a host;
* every built variant gets the tests/images and docker/profiles/README.md checks and its
  identity goes into the release manifest.

Only the YAML is parsed; nothing here talks to GitHub or Docker.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
BUILD = WORKFLOWS / "build-app.yml"
PUBLISH = WORKFLOWS / "publish-image.yml"
OWNED = (BUILD, PUBLISH)
VARIANTS = ("local", "als-dev", "als-prod")
PROPOSAL = REPO / "docs" / "migration" / "reviews" / "H6" / "o06_tag_rules_proposal.md"

SHA_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
UNTRUSTED_EXPR = re.compile(
    r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref|github\.ref_name|github\.actor|vars\.|"
    r"steps\.[^}]*outputs|needs\.[^}]*outputs)"
)
# Every action any workflow uses (all GitHub-owned actions/*; no third-party action), with
# the commit its tag pointed to when the pin was taken (T01, 2026-10-12). A new action or a
# moved pin must be added here after review; there are no placeholder pins. Docker build and
# push use the runner's docker CLI, so no docker/* action is used.
REVIEWED_ACTIONS = {
    "actions/checkout": ("11d5960a326750d5838078e36cf38b85af677262", "v4"),
    "actions/setup-node": ("49933ea5288caeca8642d1e84afbd3f7d6820020", "v4"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4"),
}
PLACEHOLDER_SHA = re.compile(r"^(.)\1{39}$|^(0123456789abcdef){2}01234567$|^deadbeef", re.IGNORECASE)
GATE_BYPASS = re.compile(r"\b(always|failure|cancelled)\s*\(")
DEPLOY_WORDS = re.compile(r"\b(ansible|ansible-playbook|ssh|scp|rsync|kubectl|helm|docker\s+compose|"
                          r"docker-compose|systemctl)\b", re.IGNORECASE)


def load(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML (YAML 1.1) reads the bare key ``on`` as boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def triggers(wf: Dict[str, Any]) -> Dict[str, Any]:
    on = wf["on"]
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {name: None for name in on}
    return dict(on)


def steps(wf: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for job_id, job in wf["jobs"].items():
        for step in job.get("steps", []):
            yield job_id, step


def write_scopes(perms: Any) -> List[str]:
    if perms in (None, {}):
        return []
    if isinstance(perms, str):
        return ["*"] if perms == "write-all" else []
    return [scope for scope, level in perms.items() if level == "write"]


@pytest.fixture(scope="module")
def build() -> Dict[str, Any]:
    return load(BUILD)


@pytest.fixture(scope="module")
def publish() -> Dict[str, Any]:
    return load(PUBLISH)


# ---------------------------------------------------------------- every workflow ---------
@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name)
def test_workflow_parses_and_never_uses_pull_request_target(path: Path) -> None:
    wf = load(path)
    assert wf.get("jobs"), path
    assert "pull_request_target" not in triggers(wf)
    assert "workflow_run" not in triggers(wf), "workflow_run would hand PR artifacts to a privileged run"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_commit_sha(path: Path) -> None:
    wf = load(path)
    refs = [step["uses"] for _, step in steps(wf) if "uses" in step]
    refs += [job["uses"] for job in wf["jobs"].values() if "uses" in job]
    unpinned = [r for r in refs if not r.startswith("./") and not SHA_PIN.match(r)]
    assert not unpinned, f"{path.name}: actions not pinned to a 40-hex SHA: {unpinned}"
    assert not [r for r in refs if r.startswith("docker://")], "docker:// actions are not digest-reviewed"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name)
def test_every_action_is_a_reviewed_pin_not_a_placeholder(path: Path) -> None:
    wf = load(path)
    refs = [step["uses"] for _, step in steps(wf) if "uses" in step]
    refs += [job["uses"] for job in wf["jobs"].values() if "uses" in job]
    for ref in refs:
        if ref.startswith("./"):
            continue
        name, _, sha = ref.partition("@")
        assert not PLACEHOLDER_SHA.search(sha), f"{path.name}: placeholder pin {ref}"
        assert name in REVIEWED_ACTIONS, f"{path.name}: {name} is not a reviewed action"
        assert sha == REVIEWED_ACTIONS[name][0], f"{path.name}: {ref} is not the reviewed pin"
    text = path.read_text(encoding="utf-8")
    for name, (sha, tag) in REVIEWED_ACTIONS.items():
        for line in text.splitlines():
            if f"uses: {name}@{sha}" in line:
                assert line.rstrip().endswith(f"# {tag}"), f"{path.name}: the pin comment names the tag: {line}"


# ---------------------------------------------------------------- owned workflows --------
@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_job_inherits_or_passes_secrets(path: Path) -> None:
    for job_id, job in load(path)["jobs"].items():
        assert "secrets" not in job, f"{path.name}:{job_id} passes secrets"
        for step in job.get("steps", []):
            assert "secrets" not in str(step.get("with") or {}), f"{path.name}:{job_id}"


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_secret_is_referenced(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "secrets." not in text and "secrets[" not in text
    text = yaml.safe_dump(load(path))  # comments may name what is kept out; the workflow itself may not
    for name in ("CBORG", "TILED_API_KEY", "TILED_WRITER", "OPENAI", "ANTHROPIC", "HF_TOKEN"):
        assert name not in text, f"{path.name} mentions runtime credential {name}"


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_run_scripts_take_untrusted_values_only_through_env(path: Path) -> None:
    for job_id, step in steps(load(path)):
        script = step.get("run", "")
        assert not UNTRUSTED_EXPR.search(script), f"{path.name}:{job_id} interpolates into a shell: {step.get('name')}"


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_host_deployment(path: Path) -> None:
    wf = load(path)
    for job_id, step in steps(wf):
        assert not DEPLOY_WORDS.search(step.get("run", "")), f"{path.name}:{job_id} deploys: {step.get('name')}"
    for job in wf["jobs"].values():
        env = job.get("environment")
        assert not (isinstance(env, dict) and env.get("url")), "no environment points at a host"
    assert "docker run" not in PUBLISH.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_checkouts_do_not_persist_credentials_where_not_needed(path: Path) -> None:
    for job_id, step in steps(load(path)):
        if str(step.get("uses", "")).startswith("actions/checkout@") and job_id != "docs-deploy":
            assert (step.get("with") or {}).get("persist-credentials") is False, f"{path.name}:{job_id}"


# ---------------------------------------------------------------- build-app.yml ----------
def test_build_runs_for_prs_with_read_only_permissions(build: Dict[str, Any]) -> None:
    on = triggers(build)
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}
    assert set(on["pull_request"]["branches"]) == {"main", "forge_merge"}
    assert set(on["push"]["branches"]) == {"main", "forge_merge"}
    assert build["permissions"] == {"contents": "read"}
    for job_id, job in build["jobs"].items():
        scopes = write_scopes(job.get("permissions"))
        if scopes:
            assert scopes == ["contents"], f"{job_id}: unexpected write scopes {scopes}"
            cond = job.get("if", "")
            assert "github.event_name == 'push'" in cond and "refs/heads/main" in cond, job_id
        assert "environment" not in job, f"{job_id}: build jobs need no environment"
        assert "id-token" not in (job.get("permissions") or {})


def test_build_never_logs_in_pushes_or_publishes(build: Dict[str, Any]) -> None:
    text = BUILD.read_text(encoding="utf-8")
    for word in ("docker login", "docker push", "imagetools create", "packages:", "--push", "ghcr.io/"):
        if word == "ghcr.io/":
            # Only the manifest's intended repository name, never a push target.
            assert text.count("ghcr.io/") == 1 and 'echo "ghcr.io/$OWNER/fair2wise"' in text
            continue
        assert word not in text, word
    assert "github.token" not in text and "GITHUB_TOKEN" not in text


def test_build_builds_all_three_variants_from_one_commit(build: Dict[str, Any]) -> None:
    job = build["jobs"]["build-candidates"]
    assert job["needs"] == "image-static-tests"
    scripts = {s.get("name"): s.get("run", "") for s in job["steps"]}
    build_script = scripts["Build the three variants"]
    assert "for variant in local als-dev als-prod" in build_script
    assert '--target "$variant"' in build_script
    assert "--label \"org.opencontainers.image.revision=$GITHUB_SHA\"" in build_script
    assert "--provenance=false" in build_script and "--sbom=false" in build_script
    assert "--secret" not in build_script and "--ssh" not in build_script
    assert re.findall(r"--build-arg\s+\"?([A-Z_]+)=", build_script) == ["PYTHON_IMAGE"]
    # The single checkout is the commit under test; nothing else is checked out.
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1 and "ref" not in (checkouts[0].get("with") or {})


def test_static_image_tests_run_before_any_build(build: Dict[str, Any]) -> None:
    job = build["jobs"]["image-static-tests"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "pytest tests/images tests/ci" in run
    assert "env -i" in run and "test ! -e .env" in run


def test_every_variant_gets_the_post_build_checks(build: Dict[str, Any]) -> None:
    job = build["jobs"]["build-candidates"]
    run = next(s["run"] for s in job["steps"] if s.get("name", "").startswith("Post-build checks"))
    assert "for variant in local als-dev als-prod" in run
    assert "scripts/ci/image_checks.py --variant \"$variant\"" in run
    assert "--commit \"$GITHUB_SHA\"" in run and "--tiled-version" in run
    assert "exit \"$status\"" in run, "a failed variant fails the job"


def test_release_manifest_artifact_and_trusted_only_images(build: Dict[str, Any]) -> None:
    job = build["jobs"]["build-candidates"]
    manifest = next(s for s in job["steps"] if s.get("name") == "Release manifest")
    for variant in VARIANTS:
        assert f"checks-{variant}.json" in manifest["run"]
    uploads = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    by_name = {s["with"]["name"]: s for s in uploads}
    assert set(by_name) == {"release-manifest", "release-images"}
    assert by_name["release-images"]["if"] == "github.event_name != 'pull_request'"
    save = next(s for s in job["steps"] if s.get("name", "").startswith("Save the tested images"))
    assert save["if"] == "github.event_name != 'pull_request'"
    assert all(f'"$CANDIDATE:{v}"' in save["run"] for v in VARIANTS)


def test_main_side_effects_stay_main_only(build: Dict[str, Any]) -> None:
    deploy = build["jobs"]["docs-deploy"]
    assert deploy["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    test_job = build["jobs"]["test"]
    assert "gh-deploy" not in "\n".join(s.get("run", "") for s in test_job["steps"])


# ---------------------------------------------------------------- publish-image.yml ------
def test_publish_is_manual_dispatch_only(publish: Dict[str, Any]) -> None:
    assert set(triggers(publish)) == {"workflow_dispatch"}
    assert publish["permissions"] == {}
    assert publish["concurrency"]["cancel-in-progress"] is False


def test_gate_fails_closed_on_o06_and_protected_ref(publish: Dict[str, Any]) -> None:
    gate = publish["jobs"]["gate"]
    assert write_scopes(gate.get("permissions")) == []
    approval = gate["steps"][0]
    assert approval["env"] == {"O06_APPROVED": "${{ vars.O06_APPROVED }}"}
    assert '[ "$O06_APPROVED" != "true" ]' in approval["run"] and "exit 1" in approval["run"]
    ref = next(s for s in gate["steps"] if s.get("id") == "ref")
    assert ref["env"]["REF_PROTECTED"] == "${{ github.ref_protected }}"
    assert ref["env"]["DEFAULT_BRANCH"] == "${{ github.event.repository.default_branch }}"
    assert '"refs/heads/$DEFAULT_BRANCH"' in ref["run"] and "^v[0-9]+" in ref["run"]


def test_every_writer_job_is_gated_and_in_an_environment(publish: Dict[str, Any]) -> None:
    writers = {k: j for k, j in publish["jobs"].items() if k != "gate"}
    assert set(writers) == {"publish", "promote", "promote-als-prod"}
    for job_id, job in writers.items():
        assert job["needs"] == "gate", job_id
        assert job["environment"] in ("ghcr-publish", "ghcr-als-prod"), job_id
        assert write_scopes(job["permissions"]) == ["packages"], job_id
        assert set(job["permissions"]) <= {"contents", "actions", "packages"}, job_id
        assert job["permissions"]["contents"] == "read"
        assert any(s.get("env", {}).get("O06_APPROVED") == "${{ vars.O06_APPROVED }}"
                   and s.get("run") == 'test "$O06_APPROVED" = "true"' for s in job["steps"]), job_id
    assert writers["promote-als-prod"]["environment"] == "ghcr-als-prod"
    assert "inputs.variant == 'als-prod'" in writers["promote-als-prod"]["if"]
    assert writers["promote"]["environment"] == "ghcr-publish"
    assert "als-prod" not in writers["promote"]["if"].replace("inputs.variant == 'als-dev'", "")


def test_the_gate_cannot_be_bypassed(publish: Dict[str, Any]) -> None:
    gate = publish["jobs"]["gate"]
    assert "if" not in gate and "continue-on-error" not in gate
    assert not [s for s in gate["steps"] if "if" in s or "continue-on-error" in s]
    for job_id, job in publish["jobs"].items():
        if job_id == "gate":
            continue
        # A writer that ran on always()/failure() would start although the gate failed.
        assert not GATE_BYPASS.search(str(job.get("if", ""))), job_id
        assert "continue-on-error" not in job, job_id
        for step in job["steps"]:
            if step.get("run") == 'test "$O06_APPROVED" = "true"':
                assert "if" not in step and "continue-on-error" not in step, job_id
        # The O06 re-check comes before the registry login.
        names = [s.get("run", "") for s in job["steps"]]
        o06 = names.index('test "$O06_APPROVED" = "true"')
        login = next(i for i, r in enumerate(names) if "docker login" in r)
        assert o06 < login, job_id


def test_environments_are_the_two_named_approval_boundaries(publish: Dict[str, Any]) -> None:
    envs = {job_id: job.get("environment") for job_id, job in publish["jobs"].items()}
    assert envs == {"gate": None, "publish": "ghcr-publish", "promote": "ghcr-publish",
                    "promote-als-prod": "ghcr-als-prod"}
    # Literal names: an expression could pick an unprotected environment at run time.
    assert all(e is None or "${{" not in e for e in envs.values())


def test_only_the_job_token_logs_in_to_ghcr(publish: Dict[str, Any]) -> None:
    logins = [(j, s) for j, s in steps(publish) if "docker login" in s.get("run", "")]
    assert {j for j, _ in logins} == {"publish", "promote", "promote-als-prod"}
    for _, step in logins:
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert step["run"] == 'printf \'%s\' "$GH_TOKEN" | docker login ghcr.io -u "$ACTOR" --password-stdin'
    tokens = [(j, s.get("name")) for j, s in steps(publish) if "github.token" in str(s.get("env", {}))]
    assert all(j != "gate" for j, _ in tokens)


def test_publish_never_rebuilds_and_verifies_the_tested_bytes(publish: Dict[str, Any]) -> None:
    text = PUBLISH.read_text(encoding="utf-8")
    for word in ("docker build", "buildx build", "build-push-action", "docker/setup-buildx"):
        assert word not in text, word
    job = publish["jobs"]["publish"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert '"path": ".github/workflows/build-app.yml"' in run
    assert '"head_sha": os.environ["GITHUB_SHA"]' in run
    assert '("push", "workflow_dispatch")' in run, "pull_request builds are never published"
    assert run.index("release_manifest.py verify") < run.index("docker load") < run.index("--inspect")
    assert run.index("--inspect") < run.index("publish_candidate.py")


def test_promotion_is_a_retag_of_a_published_digest(publish: Dict[str, Any]) -> None:
    for job_id in ("promote", "promote-als-prod"):
        job = publish["jobs"][job_id]
        run = "\n".join(s.get("run", "") for s in job["steps"])
        assert "scripts/ci/promote_channel.py" in run and '--digest "$DIGEST"' in run
        checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
        assert checkout["with"]["fetch-depth"] == 0, "ancestry checks need history"


def test_inputs_are_validated_before_any_writer(publish: Dict[str, Any]) -> None:
    validate = next(s for s in publish["jobs"]["gate"]["steps"] if s.get("name") == "Validate the inputs")
    assert "^sha256:[0-9a-f]{64}$" in validate["run"]
    assert "^[0-9]{1,20}$" in validate["run"]
    assert "a rollback needs a reason" in validate["run"]


# ---------------------------------------------------------------- O06 proposal ----------
def test_o06_rules_are_a_proposal_and_publishing_waits_for_them() -> None:
    text = PROPOSAL.read_text(encoding="utf-8")
    assert "PROPOSAL" in text and "not approved" in text.lower()
    assert "O06_APPROVED" in text
    for topic in ("sha-<commit>-<variant>", "v<MAJOR>.<MINOR>.<PATCH>-<variant>", ":als-prod", "Rollback",
                  "imagetools create", "T24E"):
        assert topic in text, topic
    assert "NOT ACTIVE UNTIL THE OWNER APPROVES O06" in PUBLISH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- forge-merge-ci.yml ------
# T28B (audit AUD-09): the validation workflow runs the Tiled-backed suites against the
# owner-pinned 0.2.18 wheel in an isolated venv, and the full suite is gated by
# compare_known_failures instead of hiding failures behind ``|| true``.
FORGE_CI = WORKFLOWS / "forge-merge-ci.yml"
TILED_SUITES = ("tests/tiled", "tests/graph", "tests/graph_import", "tests/security_live", "tests/api_v1_live",
                "tests/images", "tests/contracts/legacy_ui/closure")


@pytest.fixture(scope="module")
def forge_ci() -> Dict[str, Any]:
    return load(FORGE_CI)


def _job_script(job: Dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_tiled_job_installs_the_owner_pinned_wheel_into_its_own_venv(forge_ci: Dict[str, Any]) -> None:
    from app.modules.deployment.tiled_config import PINNED_TILED_VERSION, PINNED_TILED_WHEEL, PINNED_TILED_WHEEL_SHA256

    job = forge_ci["jobs"]["python-tiled-suites"]
    assert job["needs"] == "guard" and "environment" not in job and "secrets" not in job
    install = next(s["run"] for s in job["steps"] if s.get("name", "").startswith("Isolated Tiled"))
    assert f'"tiled=={PINNED_TILED_VERSION}"' in install and "--no-deps" in install and "--only-binary=:all:" in install
    assert f"{PINNED_TILED_WHEEL_SHA256}  $RUNNER_TEMP/tiled-wheel/{PINNED_TILED_WHEEL}" in install
    assert "sha256sum -c -" in install, "the downloaded wheel is checked before anything is installed"
    assert install.index("sha256sum -c") < install.index("python -m venv"), "check first, install second"
    assert "--constraint docker/profiles/tiled-server/constraints.txt" in install
    assert f"{PINNED_TILED_WHEEL}[server]" in install
    assert "pip check" in install and f"tiled.__version__ != '{PINNED_TILED_VERSION}'" in install
    # The server goes into $RUNNER_TEMP/venv-tiled only; the app environment never gets tiled.
    assert "venv-tiled" in install
    for step in job["steps"]:
        run = step.get("run", "")
        if "requirements.txt" in run:
            assert "tiled" not in run.lower()


def test_tiled_job_runs_every_tiled_backed_suite_with_the_isolated_interpreter(forge_ci: Dict[str, Any]) -> None:
    job = forge_ci["jobs"]["python-tiled-suites"]
    run = next(s["run"] for s in job["steps"] if s.get("name") == "Tiled-backed suites")
    assert "env -i" in run, "no ambient environment reaches the servers"
    assert 'F2W_TILED_PYTHON="$RUNNER_TEMP/venv-tiled/bin/python"' in run
    for suite in TILED_SUITES:
        assert f" {suite}" in run, suite
    assert "|| true" not in run and "continue-on-error" not in job
    assert "--junitxml=tiled.xml" in run
    upload = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"] == {"name": "tiled-junit", "path": "tiled.xml"} and upload["if"] == "always()"


def test_forge_ci_never_names_a_central_tiled_or_a_credential(forge_ci: Dict[str, Any]) -> None:
    text = yaml.safe_dump(forge_ci)
    for word in ("TILED_API_KEY", "TILED_WRITER", "CBORG", "tiled.als", "als.lbl.gov", "8765", "8011", "secrets."):
        assert word not in text, word
    for job_id, job in forge_ci["jobs"].items():
        for step in job.get("steps", []):
            assert not DEPLOY_WORDS.search(step.get("run", "")), f"{job_id}: {step.get('name')}"


def test_full_suite_is_gated_by_compare_known_failures_not_hidden(forge_ci: Dict[str, Any]) -> None:
    job = forge_ci["jobs"]["python-baseline-suite"]
    assert "|| true" not in _job_script(job)
    assert "continue-on-error" not in job and not [s for s in job["steps"] if "continue-on-error" in s]
    names = [s.get("name", "") for s in job["steps"]]
    full = next(s for s in job["steps"] if s.get("name", "").startswith("Full suite"))
    gate = next(s for s in job["steps"] if s.get("name", "").startswith("Compare with the known-failure set"))
    assert names.index(full["name"]) < names.index(gate["name"])
    assert "--junitxml=full.xml" in full["run"]
    assert 'test -s full.xml || exit "$status"' in full["run"], "a run that produced no report fails outright"
    assert gate["run"] == (
        "python scripts/ci/compare_known_failures.py full.xml docs/migration/reviews/H0/known_failures.json"
    )
    assert "if" not in gate


def test_known_failure_set_is_down_to_the_splash_wipe_entry() -> None:
    known = yaml.safe_load((REPO / "docs/migration/reviews/H0/known_failures.json").read_text(encoding="utf-8"))
    ids = [k["id"] for k in known["known_failures"]]
    assert ids == ["tests.test_kg_update::test_splash_reimport_refuses_wipe_without_explicit_allow"]
    assert all(k["reason"] for k in known["known_failures"])
