"""The committed case manifest: completeness, honesty and consistency with its generator."""

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals.binary import import_companion, proposals  # noqa: E402
from evals.binary.model import REPO_ROOT, load_manifest  # noqa: E402
from evals.binary.reports import load_rubrics  # noqa: E402

COMPANION = REPO_ROOT / "docs/migration/companion/binary_case_inventory.json"
INVENTORY = json.loads(COMPANION.read_text(encoding="utf-8"))
MANIFEST = load_manifest()


def test_all_108_companion_cases_are_present_and_none_is_dropped_or_added():
    assert len(INVENTORY["cases"]) == 108
    assert sorted(c["case_id"] for c in INVENTORY["cases"]) == sorted(MANIFEST.by_id)
    assert len(MANIFEST.cases) == 108


def test_every_family_of_the_companion_is_represented():
    families = {c["case_id"].split("-")[0] for c in INVENTORY["cases"]}
    assert families == {
        "DEP",
        "AUTH",
        "JSON",
        "LOCAL",
        "REL",
        "LLM",
        "TIL",
        "EXT",
        "QA",
        "OPS",
        "SEC",
        "SCH",
        "FIG",
        "QUOTE",
    }
    assert {c["family"] for c in MANIFEST.cases} == families


def test_manifest_is_structurally_valid_and_hashes_match():
    assert MANIFEST.validate() == []


def test_generated_files_are_up_to_date_with_the_generator():
    assert import_companion.check_outputs() == []


def test_the_companion_file_is_the_one_the_manifest_was_generated_from():
    source = MANIFEST.header["companion_source"]
    assert source["sha256"] == hashlib.sha256(COMPANION.read_bytes()).hexdigest()
    assert source["case_count"] == 108


def test_nothing_is_approved_by_an_agent():
    assert MANIFEST.header["human_gates"] == {"H3": "not_approved"}
    for case in MANIFEST.cases:
        assert case["owner_approval"] == {"status": "proposed", "ref": None, "approved_by": None}, case["case_id"]
    for rubric in load_rubrics().values():
        assert rubric["approval_status"] == "draft" and rubric["approval_ref"] is None


def test_required_membership_is_frozen_and_every_companion_proposal_is_required():
    assert MANIFEST.required_ids == sorted(c["case_id"] for c in INVENTORY["cases"] if c["required_proposal"])
    assert len(MANIFEST.header["required_case_ids"]) == 108


def test_unchanged_cases_keep_the_companion_expectation_verbatim():
    for companion in INVENTORY["cases"]:
        case = MANIFEST.by_id[companion["case_id"]]
        assert case["companion"] == companion  # the original record is preserved untouched
        assert case["companion_title"] == companion["title"]
        if case["case_revision"] == 1:
            assert case["title"] == companion["title"]
            if companion.get("expected_rule"):
                assert case["expected_rule"] == companion["expected_rule"], case["case_id"]
            assert case["supersedes"] is None and case["change_reason"] is None


def test_changed_cases_cite_a_recorded_decision_keep_the_old_text_and_are_listed_for_the_owner():
    changed = [c for c in MANIFEST.cases if c["case_revision"] > 1]
    assert {c["case_id"] for c in changed} == set(proposals.CHANGES)
    doc = (REPO_ROOT / "docs/migration/reviews/H3/case_changes.md").read_text(encoding="utf-8")
    for case in changed:
        assert case["supersedes"] and case["change_reason"]
        decisions = proposals.CHANGES[case["case_id"]]["decisions"]
        assert decisions and all(d in case["change_reason"] for d in decisions)
        original = case["companion_expected_rule"] or case["expected_rule"].split(" Owner decision update:")[0]
        assert case["expected_rule"].startswith(original[:60])  # companion text is kept, the decision is appended
        assert "Owner decision update" in case["expected_rule"]
        assert case["case_id"] in doc
        assert case["case_revision"] == 2


def test_every_case_has_a_clear_readiness_and_unfinished_cases_say_why():
    for case in MANIFEST.cases:
        if case["readiness"] == "blocked":
            assert case["blocked_by"], case["case_id"]
            assert case["coverage"] == "none" and case["oracle"]["kind"] is None
        elif case["readiness"] == "needs_h3_rubric":
            assert case["oracle"]["rubric_ref"] in load_rubrics(), case["case_id"]
            assert case["blocked_by"]
        else:
            assert (
                case["readiness"] == "executable"
                and case["oracle"]["selectors"]
                and case["coverage"] in ("full", "partial")
            )
            assert case["oracle"]["covers"]
            if case["coverage"] == "partial":
                assert case["oracle"]["uncovered"], case["case_id"]


def test_rubric_drafts_are_binary_and_list_open_questions():
    for rubric in load_rubrics().values():
        assert rubric["verdict_rule"].startswith("PASS iff every criterion")
        assert len(rubric["criteria"]) >= 4 and rubric["open_questions_for_h3"]
        for cid in rubric["applies_to"]:
            assert MANIFEST.by_id[cid]["oracle"]["rubric_ref"] == rubric["rubric_id"]


def test_repeat_policy_is_declared_in_the_manifest_with_no_retry():
    policy = MANIFEST.header["repeat_policy"]
    assert policy["retry_on_failure"] is False and policy["declared_before_run"] is True and policy["runs"] == 1


def _test_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


@pytest.mark.parametrize("case", MANIFEST.cases, ids=lambda c: c["case_id"])
def test_every_oracle_and_supporting_selector_names_an_existing_test(case):
    for selector in case["oracle"]["selectors"] + case["oracle"]["supporting_selectors"]:
        file, _, rest = selector.partition("::")
        path = REPO_ROOT / file
        assert path.is_file(), selector
        if rest:
            names = _test_names(path)
            assert all(part in names for part in rest.split("::")), selector


def test_blocked_by_names_a_task_or_owner_item():
    for case in MANIFEST.cases:
        for item in case["blocked_by"]:
            assert item.split(" ")[0] in {
                "T08B",
                "T23B",
                "T10",
                "T22B",
                "T24B",
                "T24C",
                "T24D",
                "T24E",
                "T26B",
                "T26C",
                "T25C",
                "T13",
                "T14",
                "T15",
                "T18C",
                "O03",
                "H3",
                "T11B",
            }, (case["case_id"], item)


def test_deployment_profiles_and_policy_notes_survive_from_the_companion():
    assert MANIFEST.by_id["LOCAL-01"]["deployment_profiles"] == ["local"]
    assert MANIFEST.by_id["AUTH-01"]["deployment_profiles"] == ["als-dev", "als-prod"]
    assert "unresolved policy cannot be counted" in MANIFEST.by_id["TIL-05"]["companion"]["policy_note"]
