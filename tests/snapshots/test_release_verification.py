"""T23B releases: disclosure/readiness check and Git-pinned (commit, path, sha256) verification.

Uses a temporary Git fixture repository built with the git CLI inside the test; the code under test reads Git
with argument lists only (no shell).
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from app.modules.graph import releases
from app.modules.graph.releases import (
    ReleaseVerificationError,
    allowlist_key,
    check_disclosure,
    check_paths,
    verify_git_artifact,
    verify_git_release,
    verify_snapshot_dir,
)
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from tests.snapshots.snapshot_fakes import COMMIT, EXPECTED, INPUTS

GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org", "LC_ALL": "C",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV,
                          check=check)
    return done.stdout.strip()


@pytest.fixture()
def repo(tmp_path, registry):
    """A Git repo holding one committed snapshot at storage/kg/synthetic/v1 plus its flat copy."""
    root = tmp_path / "repo"
    (root / "storage" / "kg").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    result = create_snapshot(SnapshotRequest(
        corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=root / "storage" / "kg",
        terms_ref="tests/contracts/json_kg/fixtures/inputs/core_dict.terms.json", code_commit=COMMIT,
        registry=registry))
    git(root, "add", "-f", "storage/kg")  # the flat name is gitignored upstream: add -f (integrator action)
    git(root, "commit", "-q", "-m", "data")
    return root, result, git(root, "rev-parse", "HEAD")


def artifacts(root: Path, commit: str):
    """Descriptor-style artifact entries computed from the committed objects."""
    out = []
    for role, rel in (("graph", "storage/kg/synthetic/v1/graph.json"),
                      ("manifest", "storage/kg/synthetic/v1/manifest.json"),
                      ("compat_copy", "storage/kg/matkg_synthetic_v1.json")):
        blob = git(root, "rev-parse", f"{commit}:{rel}")
        data = subprocess.run(["git", "-C", str(root), "cat-file", "blob", blob], capture_output=True, env=GIT_ENV,
                              check=True).stdout
        out.append({"role": role, "path": rel, "sha256": releases.sha256_bytes(data), "git_blob": blob})
    return out


def _by_role(items, role):
    return next(a for a in items if a["role"] == role)


# ---------------------------------------------------------------- Git-pinned verification


def test_a_pinned_release_verifies_and_returns_the_exact_bytes(repo, registry):
    root, result, commit = repo
    items = artifacts(root, commit)
    verified = verify_git_release(root, commit, items)
    expected = (EXPECTED / "core_dict.graph.json").read_bytes()
    assert verified.graph.data == result.graph_path.read_bytes() == expected
    assert verified.manifest.graph.sha256 == _by_role(items, "graph")["sha256"]
    assert verified.data_commit == commit and len(verified.compat) == 1
    assert verified.manifest_artifact.git_blob == _by_role(items, "manifest")["git_blob"]
    from app.modules.schema.loading import closure_fingerprint

    fp = closure_fingerprint(registry.get("matkg_schema"))
    assert verify_git_release(root, commit, items, supported_schema_fingerprints={fp}).manifest.created.code_commit
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_release(root, commit, items, supported_schema_fingerprints={"0" * 64})
    assert err.value.code == "schema_unsupported"


def test_wrong_hashes_are_rejected(repo):
    root, _, commit = repo
    graph = _by_role(artifacts(root, commit), "graph")
    for bad in ("0" * 64, graph["sha256"][:-1] + ("0" if graph["sha256"][-1] != "0" else "1")):
        with pytest.raises(ReleaseVerificationError) as err:
            verify_git_artifact(root, commit, graph["path"], bad)
        assert err.value.code == "wrong_hash"
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_artifact(root, commit, graph["path"], graph["sha256"], git_blob="1" * 40)
    assert err.value.code == "wrong_hash"
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_artifact(root, commit, graph["path"], "not-a-hash")
    assert err.value.code == "wrong_hash"
    ok = verify_git_artifact(root, commit, graph["path"], graph["sha256"], git_blob=graph["git_blob"])
    assert ok.sha256 == graph["sha256"]


@pytest.mark.parametrize("selector", ["main", "HEAD", "v1", "refs/heads/main", "", "ZZ" * 20])
def test_branches_tags_and_abbreviations_never_select_data(repo, selector):
    root, _, commit = repo
    graph = _by_role(artifacts(root, commit), "graph")
    for ref in (selector, commit[:12], commit.upper()):
        with pytest.raises(ReleaseVerificationError) as err:
            verify_git_artifact(root, ref, graph["path"], graph["sha256"])
        assert err.value.code == "bad_commit"


def test_unknown_commit_missing_path_and_unsafe_paths(repo):
    root, _, commit = repo
    graph = _by_role(artifacts(root, commit), "graph")
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_artifact(root, "0" * 40, graph["path"], graph["sha256"])
    assert err.value.code == "git"
    tree = git(root, "rev-parse", f"{commit}^{{tree}}")
    with pytest.raises(ReleaseVerificationError) as err:  # a tree id is not a commit
        verify_git_artifact(root, tree, graph["path"], graph["sha256"])
    assert err.value.code == "bad_commit"
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_artifact(root, commit, "storage/kg/synthetic/v9/graph.json", graph["sha256"])
    assert err.value.code == "missing"
    for path in ("../outside.json", "/etc/passwd", "storage\\kg\\x", "storage/kg/", ""):
        with pytest.raises(ReleaseVerificationError) as err:
            verify_git_artifact(root, commit, path, graph["sha256"])
        assert err.value.code == "bad_path"
    with pytest.raises(ReleaseVerificationError) as err:  # a directory is not an artifact
        verify_git_artifact(root, commit, "storage/kg/synthetic/v1", graph["sha256"])
    assert err.value.code == "not_a_blob"


def test_a_symlink_in_git_is_not_accepted_as_a_graph(repo):
    root, _, commit = repo
    link = root / "storage" / "kg" / "matkg_synthetic_v2.json"
    link.symlink_to("synthetic/v1/graph.json")
    git(root, "add", "-f", "storage/kg/matkg_synthetic_v2.json")
    git(root, "commit", "-q", "-m", "symlink")
    new = git(root, "rev-parse", "HEAD")
    sha = _by_role(artifacts(root, commit), "graph")["sha256"]
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_artifact(root, new, "storage/kg/matkg_synthetic_v2.json", sha)
    assert err.value.code == "not_a_blob"


def test_working_tree_edits_do_not_change_what_a_commit_selects(repo):
    root, result, commit = repo
    items = artifacts(root, commit)
    original = result.graph_path.read_bytes()
    result.graph_path.write_bytes(original + b" ")
    assert verify_git_release(root, commit, items).graph.data == original  # objects, not the working tree
    with pytest.raises(ReleaseVerificationError) as err:  # but the edited tree is detected as tampered
        verify_snapshot_dir(result.directory)
    assert err.value.code == "tampered"


def test_a_later_commit_that_changes_the_bytes_fails_against_the_old_pin(repo):
    root, result, commit = repo
    result.graph_path.write_bytes(result.graph_path.read_bytes().replace(b"Material", b"Materiel", 1))
    git(root, "commit", "-q", "-am", "edit graph after freezing")
    new = git(root, "rev-parse", "HEAD")
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_release(root, new, artifacts(root, commit))  # old hashes, new commit
    assert err.value.code == "wrong_hash"
    # pinning the new bytes passes the object check but the manifest no longer matches the graph
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_release(root, new, artifacts(root, new))
    assert err.value.code == "manifest_mismatch"


def test_manifest_and_compat_disagreements_are_rejected(repo):
    root, result, commit = repo
    manifest_path = result.manifest_path
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["graph"]["sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    git(root, "commit", "-q", "-am", "manifest edited")
    new = git(root, "rev-parse", "HEAD")
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_release(root, new, artifacts(root, new))
    assert err.value.code == "manifest_mismatch"
    git(root, "reset", "-q", "--hard", commit)
    result.flat_path.write_bytes(result.flat_path.read_bytes() + b"\n")
    git(root, "commit", "-q", "-am", "flat copy edited")
    new = git(root, "rev-parse", "HEAD")
    with pytest.raises(ReleaseVerificationError) as err:
        verify_git_release(root, new, artifacts(root, new))
    assert err.value.code == "manifest_mismatch"


def test_artifact_set_must_be_one_graph_one_manifest_in_the_frozen_layout(repo):
    root, _, commit = repo
    items = artifacts(root, commit)
    for bad in (items[:1], items[1:], items + [items[0]], [{**a, "role": "other"} for a in items]):
        with pytest.raises(ReleaseVerificationError) as err:
            verify_git_release(root, commit, bad)
        assert err.value.code == "layout"
    swapped = copy.deepcopy(items)
    swapped[0]["path"], swapped[1]["path"] = items[1]["path"], items[0]["path"]
    with pytest.raises(ReleaseVerificationError):
        verify_git_release(root, commit, swapped)


def test_git_is_invoked_with_argument_lists_and_no_shell(repo, monkeypatch):
    root, _, commit = repo
    seen = []
    real = subprocess.run

    def spy(args, **kwargs):
        seen.append((args, kwargs))
        return real(args, **kwargs)

    monkeypatch.setattr(releases.subprocess, "run", spy)
    verify_git_release(root, commit, artifacts(root, commit))
    assert seen and all(isinstance(a, list) and a[0] == "git" and not k.get("shell") for a, k in seen)
    assert all(k["env"].get("GIT_CONFIG_GLOBAL") == os.devnull and "HOME" not in k["env"] for _, k in seen)


# ---------------------------------------------------------------- disclosure and readiness


def _graph(**node):
    base = {"id": "matkg:A", "name": "A", "category": "Material", "description": "x", "source_papers": ["p.pdf"]}
    base.update(node)
    return {"things": [base], "associations": []}


def _rules(report):
    return sorted({f.rule for f in report.findings})


def test_a_clean_graph_passes_and_the_report_is_deterministic_and_path_free():
    graph = json.loads((EXPECTED / "core_dict.graph.json").read_bytes())
    a, b = check_disclosure(graph), check_disclosure(copy.deepcopy(graph))
    assert a.status == "pass" and a.commit_ready and a.sha256 == b.sha256
    text = a.canonical_bytes().decode()
    assert "/Users/" not in text and "/private/" not in text
    assert graph == json.loads((EXPECTED / "core_dict.graph.json").read_bytes())  # read-only


def test_secrets_local_paths_and_signed_urls_are_rejected_without_echoing_values():
    secret = "sk-" + "A1b2C3d4E5" * 3
    graph = _graph(description="call with api_key=abcd1234efgh5678", name=secret,
                   source_file_path="/Users/someone/code/x.py",
                   source_file_url="https://example.org/f?X-Amz-Signature=abc",
                   formula="C:\\Users\\x", repo_url="file:///etc/passwd")
    report = check_disclosure(graph)
    assert report.status == "fail" and not report.commit_ready
    assert _rules(report) == ["R3", "R4", "R5"]
    text = report.canonical_bytes().decode()
    assert secret not in text and "abcd1234efgh5678" not in text and "someone" not in text
    assert all(f.allowlist_key for f in report.findings if f.rule in ("R3", "R4", "R5"))


def test_ordinary_urls_and_hashes_are_not_flagged():
    graph = _graph(description="see https://example.org/home/page and /usr/lib",
                   doi="10.1103/PhysRevB.80.045204", repo_commit_sha="0123456789abcdef" * 4,
                   source_file_url="https://github.com/org/repo/blob/main/home/tmp/x.py")
    assert check_disclosure(graph).status == "pass"


def test_high_entropy_tokens_are_flagged_but_hex_digests_are_not():
    token = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3gH5jK7mN9"
    assert _rules(check_disclosure(_graph(description=f"token {token}"))) == ["R3"]
    assert check_disclosure(_graph(description="sha " + "ab12" * 16)).status == "pass"


SLUG_IDS = (  # shapes of the 858 false positives found on the flat KG files (D3.22)
    "snippetanalyzevolfractions101021acsjpcb4c05774pdfp0884f62c3",
    "Poly56-difluoro-213-benzothiadiazol-47-diyl-alt-33-di2-octyldodecyl-225252-quaterthiophen-55-diylPff",
    "YXiongLYeAGadisaQZhangJJRechWYouandHAde2019",
    "1396eVforbothD18-ClN3andD18-ClN3Qxp-C7H8Odevices",
    "RSoXSMeasurement-167e2a1d-3f3b-9fef-c18c-425cfb0a648a",
    "https://github.com/als-computing/bcs2sim-ophyd/blob/729686c05f6c39208f4ed034abc3229f63d7ac23/axis_sxr40.py",
)
KEY_LIKE = (  # seeded random-looking tokens; none carries a prefix the token-shape patterns know
    "q7Zr2LmXw9PbT4vNc8KdYs1HgJf6EaUo3RiVt5WnBx0",
    "Xk93mPq0LzR7vTb2NcY8wDsA4HgJf6EuO1iVt5WnQ-_e",
    "N8fW2kQz7XpLr4VbTc9YdMs1HgJa6EuO3iAt5wRnBx0Zq8",
)


@pytest.mark.parametrize("ident", SLUG_IDS)
def test_slug_shaped_identifiers_are_not_flagged_as_entropy(ident):
    graph = _graph(id=ident, description=f"see {ident}")
    assert "R3" not in _rules(check_disclosure(graph))


@pytest.mark.parametrize("token", KEY_LIKE)
def test_key_like_tokens_still_fail_the_entropy_rule(token):
    report = check_disclosure(_graph(description=f"credential {token} here"))
    assert _rules(report) == ["R3"] and not report.commit_ready
    assert token not in report.canonical_bytes().decode()


@pytest.mark.parametrize("text", (
    "sk-" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ",
    "Authorization: Bearer " + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF",
    'api_key = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF"',
))
def test_prefixed_and_assigned_secrets_still_fail(text):
    assert _rules(check_disclosure(_graph(description=text))) == ["R3"]


def test_slug_next_to_a_key_still_reports_the_key():
    token = KEY_LIKE[0]
    graph = _graph(id=SLUG_IDS[0], description=f"{SLUG_IDS[1]} {token}")
    assert _rules(check_disclosure(graph)) == ["R3"]


def test_reviewed_exceptions_by_key_and_owner_waivers():
    graph = _graph(source_file_path="/home/ci/work/x.py")
    report = check_disclosure(graph)
    key = report.findings[0].allowlist_key
    assert key == allowlist_key("graph.json", "matkg:A", "source_file_path", "/home/ci/work/x.py")
    assert check_disclosure(graph, allowlist={key}).status == "pass"
    assert check_disclosure(graph, allowlist={"0" * 64}).status == "fail"
    waived = check_disclosure(graph, waiver_ref="owner:2026-10-14")
    assert waived.status == "owner_waiver" and waived.commit_ready and waived.waiver_ref == "owner:2026-10-14"


def test_full_parsed_bodies_and_document_chunks_are_blocked_but_snippets_are_not():
    assert _rules(check_disclosure(_graph(full_text="the whole parsed paper body"))) == ["R7"]
    chunk = check_disclosure(_graph(category="DocumentChunk"))
    assert [(f.rule, f.severity) for f in chunk.findings] == [("R7", "review")] and not chunk.commit_ready
    assert check_disclosure(_graph(full_text="")).status == "pass"


def test_snippet_volume_is_information_only(registry):
    """O-D1: verbatim context snippets stay in stored KGs; volume is recorded, never a blocker, never truncated."""
    big = "word " * 3000  # 15,000 characters from one paper
    snippets = [{"text": big, "source_paper": "p1.pdf", "page": i} for i in range(3)]
    graph = _graph(context_snippets=snippets, abstract_text="a" * 5000)
    report = check_disclosure(graph)
    assert report.status == "pass" and report.findings == []
    info = report.to_dict()["informational"]
    assert info["max_value_chars"] == 15000 and info["max_chars_for_one_paper"] == 45000
    assert info["snippet_value_count"] == 4 and info["papers"] == 1
    assert graph["things"][0]["context_snippets"][0]["text"] == big  # never truncated


def test_path_allowlist_rejects_pdfs_crops_databases_env_and_private_stores():
    ok = ["storage/kg/rsoxs/v4/graph.json", "storage/kg/rsoxs/v4/manifest.json",
          "storage/kg/rsoxs/v4/validation.json", "storage/kg/matkg_rsoxs_v4.json"]
    bad = ["papers/a.pdf", "storage/kg/rsoxs/v4/crop.png", "storage/source_index/x.json", "workdir/kg.json",
           "storage/tiled_catalog.db", ".env", "storage/kg/rsoxs/v4/extra.json", "storage/kg/rsoxs/graph.json"]
    assert check_paths(ok) == []
    assert [f.location for f in check_paths(bad)] == bad
    assert {f.rule for f in check_paths(bad)} == {"R1"}


def test_snapshot_creation_records_disclosure_status_and_blocks_commit_readiness(tmp_path, registry):
    terms = tmp_path / "t.json"
    data = json.loads((INPUTS / "core_dict.terms.json").read_text(encoding="utf-8"))
    data["terms"][0]["source_file_path"] = "/Users/someone/project/x.py"
    terms.write_text(json.dumps(data), encoding="utf-8")
    kg = tmp_path / "kg"

    def build(**extra):
        return create_snapshot(SnapshotRequest(
            corpus="syn", terms_path=terms, storage_kg=kg, terms_ref="in/t.json", code_commit=COMMIT,
            registry=registry, **extra))

    blocked = build()
    assert blocked.disclosure.status == "fail" and not blocked.commit_ready
    assert blocked.manifest.disclosure.status == "fail"
    assert blocked.manifest.disclosure.report_sha256 == blocked.disclosure.sha256
    waived = build(owner_waiver_ref="owner:ok")
    assert waived.commit_ready and waived.manifest.disclosure.status == "owner_waiver"
    assert waived.manifest.disclosure.waiver_ref == "owner:ok"
    # the frozen graph is the same bytes either way: readiness never edits the payload
    assert blocked.graph_path.read_bytes() == waived.graph_path.read_bytes()


def test_flat_allowlist_admits_legacy_names_but_not_other_flat_files():
    """D3.24: ``matkg_<anything>.json`` flat names are allowed; other flat files and odd names are not."""
    legacy = ["storage/kg/matkg_deepseek-r1_14b_100_20250918_095748.json", "storage/kg/matkg_Qwen3_235b_147papers.json",
              "storage/kg/matkg_with_code.json"]
    bad = ["storage/kg/notmatkg.json", "storage/kg/matkg_.json", "storage/kg/matkg_x.json.bak",
           "storage/kg/matkg_a b.json", "storage/kg/sub/matkg_x.json", "storage/kg/matkg_x.v1.json",
           "storage/kg/matkg_x.pdf", "other/matkg_x.json", "/storage/kg/matkg_x.json"]
    assert check_paths(legacy) == []
    assert [f.location for f in check_paths(bad)] == bad


def test_an_image_baked_flat_copy_is_used_only_when_its_sha256_equals_the_record(repo, tmp_path):
    """E3 / release_contract.md section 7: images carry the flat ``matkg_*.json`` copy and the manifest, not the
    nested ``graph.json``. The copy is accepted only when its bytes hash to the record's sha256; a different
    image can never silently supply data, and the flat copy alone (no nested graph to check against) is not
    enough to verify a snapshot directory."""
    import shutil

    root, result, _ = repo
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    recorded = manifest["graph"]["sha256"]
    flat_bytes = result.flat_path.read_bytes()
    assert releases.sha256_bytes(flat_bytes) == recorded  # a matching baked copy is the same bytes
    # an image-shaped tree: flat copy + manifest/validation, nested graph.json omitted (.dockerignore)
    baked = tmp_path / "baked"
    shutil.copytree(root / "storage" / "kg", baked / "storage" / "kg",
                    ignore=shutil.ignore_patterns("graph.json"))
    with pytest.raises(ReleaseVerificationError) as err:
        verify_snapshot_dir(baked / "storage" / "kg" / "synthetic" / "v1")
    assert err.value.code == "layout"  # fails closed: nothing is verified from the flat copy alone
    # a baked flat copy from another build (different bytes) does not hash to the record
    (baked / "storage" / "kg" / result.flat_path.name).write_bytes(flat_bytes + b"\n")
    other = (baked / "storage" / "kg" / result.flat_path.name).read_bytes()
    assert releases.sha256_bytes(other) != recorded
    # and the full layout rejects a flat copy that differs from the verified graph
    shutil.copytree(root / "storage" / "kg", tmp_path / "full" / "storage" / "kg")
    (tmp_path / "full" / "storage" / "kg" / result.flat_path.name).write_bytes(other)
    with pytest.raises(ReleaseVerificationError) as err:
        verify_snapshot_dir(tmp_path / "full" / "storage" / "kg" / "synthetic" / "v1")
    assert err.value.code == "tampered"
