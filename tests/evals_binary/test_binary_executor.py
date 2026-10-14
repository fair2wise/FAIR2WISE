"""The pytest-node oracle executor: JUnit attribution, states and sanitising."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals.binary.assertions.pytest_nodes import (  # noqa: E402
    PytestNodeExecutor,
    base_node,
    clean_env,
    selector_matches,
)
from evals.binary.sanitize import sanitize  # noqa: E402

F = "tests/evals/test_term_substring.py"  # a real, quick, existing file (selectors are checked for existence)


def junit(rows):
    body = []
    for name, kind, msg in rows:
        inner = "" if kind == "passed" else f'<{kind} message="{msg}"/>'
        body.append(
            f'<testcase classname="tests.evals.test_term_substring" name="{name}" file="{F}">{inner}</testcase>'
        )
    return "<testsuites><testsuite>" + "".join(body) + "</testsuite></testsuites>"


class FakePytest:
    """Fake subprocess: answers --collect-only with ids and writes JUnit for real runs."""

    def __init__(self, tests, rows):
        self.tests, self.rows, self.calls = tests, rows, []

    def __call__(self, args, env, timeout):
        self.calls.append(list(args))
        if "--collect-only" in args:
            return 0, "\n".join(f"{F}::{t}" for t in self.tests) + "\n\n2 tests collected"
        path = next(a for a in args if a.startswith("--junitxml=")).split("=", 1)[1]
        Path(path).write_text(junit(self.rows), encoding="utf-8")
        return 0, ""


def run(selectors, tests, rows):
    fake = FakePytest(tests, rows)
    out = PytestNodeExecutor(runner=fake, env={}).run_selectors({"k": selectors})
    return out["k"], fake


def test_all_passed_is_completed_pass():
    out, fake = run([f"{F}::test_a"], ["test_a", "test_b"], [("test_a", "passed", "")])
    assert (out.execution_state, out.verdict) == ("completed", "PASS")
    assert out.evidence_refs == [f"pytest:{F}::test_a:passed"]


def test_failure_is_a_completed_fail_with_sanitised_message():
    out, _ = run([F], ["test_a"], [("test_a", "failure", "boom /Users/me/secret.py API_KEY=abc")])
    assert (out.execution_state, out.verdict) == ("completed", "FAIL")
    assert "/Users/me" not in out.actual and "abc" not in out.actual


def test_error_has_no_verdict():
    out, _ = run([F], ["test_a"], [("test_a", "error", "fixture failed")])
    assert (out.execution_state, out.verdict) == ("error", None)


def test_any_skip_means_skipped_even_when_others_pass():
    out, _ = run(
        [F], ["test_a", "test_b"], [("test_a", "passed", ""), ("test_b", "skipped", "F2W_TILED_PYTHON is not set")]
    )
    assert (out.execution_state, out.verdict) == ("skipped", None)
    assert "F2W_TILED_PYTHON is not set" in out.actual


def test_a_failure_wins_over_a_skip():
    out, _ = run([F], ["test_a", "test_b"], [("test_a", "failure", "x"), ("test_b", "skipped", "y")])
    assert (out.execution_state, out.verdict) == ("completed", "FAIL")


def test_selector_matching_nothing_is_missing_fixture_and_nothing_runs_for_it():
    out, fake = run([f"{F}::test_renamed"], ["test_a"], [])
    assert (out.execution_state, out.verdict) == ("missing_fixture", None)
    assert not any("--junitxml" in " ".join(c) for c in fake.calls)


def test_missing_file_is_missing_fixture():
    out = PytestNodeExecutor(runner=FakePytest([], []), env={}).run_selectors(
        {"k": ["tests/does_not_exist.py::t"]}
    )["k"]
    assert out.execution_state == "missing_fixture"


def test_a_partly_missing_selector_set_is_not_a_pass():
    out, _ = run([f"{F}::test_a", f"{F}::test_gone"], ["test_a"], [("test_a", "passed", "")])
    assert out.execution_state == "missing_fixture" and out.verdict is None


def test_timeout_in_the_batch_is_timeout_not_a_verdict():
    fake = FakePytest(["test_a"], [])
    original = fake.__call__

    def slow(args, env, timeout):
        if "--collect-only" in args:
            return original(args, env, timeout)
        raise subprocess.TimeoutExpired("pytest", timeout)

    result = PytestNodeExecutor(runner=slow, env={}).run_selectors({"k": [F]})["k"]
    assert (result.execution_state, result.verdict) == ("timeout", None)


def test_collection_failure_is_error():
    def broken(args, env, timeout):
        return 2, "ERROR collecting tests/evals/test_term_substring.py\nImportError"

    out = PytestNodeExecutor(runner=broken, env={}).run_selectors({"k": [F]})["k"]
    assert (out.execution_state, out.verdict) == ("error", None)


def test_one_batch_serves_many_cases_and_results_attribute_per_case():
    fake = FakePytest(["test_a", "test_b"], [("test_a", "passed", ""), ("test_b", "failure", "x")])
    out = PytestNodeExecutor(runner=fake, env={}).run_selectors({"one": [f"{F}::test_a"], "two": [f"{F}::test_b"]})
    assert out["one"].verdict == "PASS" and out["two"].verdict == "FAIL"
    assert sum("--junitxml" in " ".join(c) for c in fake.calls) == 1


def test_selector_matching_rules_and_base_node():
    assert selector_matches("a.py", "a.py::t") and not selector_matches("a.py", "b.py::t")
    assert selector_matches("a.py::C", "a.py::C::t") and selector_matches("a.py::t", "a.py::t")
    assert not selector_matches("a.py::t", "a.py::t2")
    assert (
        base_node("tests/x/test_y.py", "tests.x.test_y.TestC", "test_z[p1]") == "tests/x/test_y.py::TestC::test_z"
    )
    assert base_node("tests/x/test_y.py", "tests.x.test_y", "test_z") == "tests/x/test_y.py::test_z"


def test_environment_is_allow_listed():
    env = clean_env(
        {"PATH": "/bin", "CBORG_API_KEY": "sk-secret", "HOME": "/h", "F2W_TILED_PYTHON": "/t/python", "EXTRA": "1"}
    )
    assert env == {"PATH": "/bin", "HOME": "/h", "F2W_TILED_PYTHON": "/t/python"}


def test_real_subprocess_runs_a_real_oracle_test():
    out = PytestNodeExecutor(timeout=300).run_selectors({"k": [f"{F}::test_exact_semiconductor_matches"]})["k"]
    assert (out.execution_state, out.verdict) == ("completed", "PASS"), out.actual


def test_sanitize_redacts_secrets_and_paths_and_bounds_length():
    text = sanitize("Bearer abcdefghijkl CBORG_API_KEY=sk-zzzzzzzzzz at /Users/x/y.py " + "z" * 1000)
    assert "abcdefghijkl" not in text and "sk-z" not in text and "/Users" not in text and len(text) <= 400
    assert sanitize("tests/a.py::test_wrong_credential_is_denied: F2W_TILED_PYTHON is not set").endswith(
        "is not set"
    )
