"""Deterministic oracle: a named set of existing pytest tests run in one batch.

Selectors are ``path.py`` or ``path.py::name`` (optionally ``::Class::name``); a
function selector covers every parametrised variant. The executor collects first so
that a renamed or deleted oracle test is reported as ``missing_fixture`` instead of
aborting the whole batch, then runs the existing selectors once and attributes the
JUnit rows back to cases. Rules:

* any failing selected test -> completed FAIL (a scientific/contract failure);
* collection/setup errors -> ``error`` (infrastructure, no verdict);
* any skipped test -> ``skipped`` with the skip reason (never PASS, even if others pass);
* selector matching no test -> ``missing_fixture``;
* subprocess timeout -> ``timeout`` for every case in the batch.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from evals.binary.assertions.base import Outcome
from evals.binary.model import REPO_ROOT
from evals.binary.sanitize import sanitize

ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "F2W_TILED_PYTHON")

# A runner: (args, env, timeout) -> (returncode, stdout). Injected by tests.
PytestRunner = Callable[[List[str], Dict[str, str], float], Tuple[int, str]]


def clean_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Allow-listed environment for the pytest subprocess (no ambient API keys or .env)."""
    source = os.environ if environ is None else environ
    return {k: source[k] for k in ENV_ALLOWLIST if k in source}


def default_runner(args: List[str], env: Dict[str, str], timeout: float) -> Tuple[int, str]:
    """Run ``python -m pytest`` in the repository root; raises ``subprocess.TimeoutExpired``."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def selector_file(selector: str) -> str:
    """The test file part of a selector."""
    return selector.split("::", 1)[0]


def base_node(file: str, classname: str, name: str) -> str:
    """Parametrisation-free node id of a JUnit testcase."""
    module = file[:-3].replace("/", ".")
    prefix = len(module) + 1
    rest = classname[prefix:] if classname.startswith(module + ".") else ""
    parts = ([rest] if rest else []) + [name.split("[", 1)[0]]
    return file + "::" + "::".join(parts)


def selector_matches(selector: str, node: str) -> bool:
    """True if ``node`` (a base node id) is covered by ``selector``."""
    if "::" not in selector:
        return node.split("::", 1)[0] == selector
    return node == selector or node.startswith(selector + "::")


def parse_collection(output: str, files: Iterable[str]) -> Tuple[Set[str], Set[str]]:
    """Return (base node ids, files that failed to collect) from ``--collect-only -q`` output."""
    nodes: Set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if "::" in line and line.split("::", 1)[0].endswith(".py"):
            nodes.add(line.split("[", 1)[0])
    collected_files = {n.split("::", 1)[0] for n in nodes}
    return nodes, {f for f in files if f not in collected_files}


class PytestNodeExecutor:
    """Run the union of selectors once and attribute results per selector set."""

    def __init__(
        self,
        runner: PytestRunner = default_runner,
        timeout: float = 1800.0,
        env: Optional[Dict[str, str]] = None,
        workdir: Optional[Path] = None,
    ):
        self.runner = runner
        self.timeout = timeout
        self.env = clean_env() if env is None else env
        self.workdir = workdir

    def run_selectors(self, selector_sets: Dict[str, Sequence[str]]) -> Dict[str, Outcome]:
        """Execute ``{key: selectors}`` and return ``{key: Outcome}``.

        Selectors are batched per test directory: several suites use ``from conftest import``
        and cannot be collected together, so each directory is its own pytest invocation.
        """
        all_selectors = sorted({s for sels in selector_sets.values() for s in sels})
        if not all_selectors:
            return {}
        files = sorted({selector_file(s) for s in all_selectors})
        missing_files = [f for f in files if not (REPO_ROOT / f).is_file()]
        groups: Dict[str, List[str]] = {}
        for f in files:
            if f not in missing_files:
                groups.setdefault(f.rsplit("/", 1)[0] if "/" in f else "", []).append(f)
        nodes: Set[str] = set()
        uncollected: Set[str] = set()
        results: Dict[str, Tuple[str, str]] = {}
        crashes: Dict[str, Outcome] = {}
        for group, group_files in sorted(groups.items()):
            try:
                _, out = self.runner(["--collect-only", "-q", *group_files], self.env, self.timeout)
            except subprocess.TimeoutExpired:
                for f in group_files:
                    crashes[f] = Outcome(
                        "timeout", reason_codes=["collect_timeout"], actual="collection timed out"
                    )
                continue
            group_nodes, group_bad = parse_collection(out, group_files)
            nodes |= group_nodes
            uncollected |= group_bad
            runnable = sorted(
                {
                    s
                    for s in all_selectors
                    if selector_file(s) in group_files and any(selector_matches(s, n) for n in group_nodes)
                }
            )
            if runnable:
                crash = self._run(runnable, results)
                if crash is not None:
                    for f in group_files:
                        crashes[f] = crash
        return {
            key: self._attribute(sels, nodes, uncollected, missing_files, results, crashes)
            for key, sels in selector_sets.items()
        }

    def _run(self, runnable: List[str], results: Dict[str, Tuple[str, str]]) -> Optional[Outcome]:
        with tempfile.TemporaryDirectory(dir=self.workdir) as tmp:
            junit = Path(tmp) / "junit.xml"
            args = ["-q", "-o", "junit_family=xunit1", f"--junitxml={junit}", *runnable]
            try:
                rc, out = self.runner(args, self.env, self.timeout)
            except subprocess.TimeoutExpired:
                return Outcome("timeout", reason_codes=["batch_timeout"], actual="pytest batch timed out")
            if not junit.is_file():
                return Outcome(
                    "error", reason_codes=["no_junit_report"], actual=sanitize(f"pytest exit {rc}: {out[-300:]}")
                )
            for case in ET.parse(junit).getroot().iter("testcase"):
                classname, name = case.get("classname") or "", case.get("name") or ""
                node = base_node(case.get("file") or _guess_file(case), classname, name)
                outcome, message = "passed", ""
                for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
                    child = case.find(tag)
                    if child is not None:
                        outcome, message = label, child.get("message") or ""
                        break
                results[f"{node}|{name}"] = (outcome, message)
        return None

    def _attribute(
        self,
        selectors: Sequence[str],
        nodes: Set[str],
        uncollected: Set[str],
        missing_files: List[str],
        results: Dict[str, Tuple[str, str]],
        crashes: Dict[str, Outcome],
    ) -> Outcome:
        refs: List[str] = []
        failed: List[str] = []
        errors: List[str] = []
        skipped: List[str] = []
        missing: List[str] = []
        passed = 0
        for sel in selectors:
            if selector_file(sel) in missing_files:
                missing.append(sel)
                continue
            if selector_file(sel) in crashes and crashes[selector_file(sel)].reason_codes == ["collect_timeout"]:
                return Outcome(
                    "timeout",
                    reason_codes=["collect_timeout"],
                    actual="collection timed out",
                    evidence_refs=[f"pytest:{x}" for x in selectors],
                )
            if selector_file(sel) in uncollected:
                errors.append(f"{sel}: collection error")
                continue
            if not any(selector_matches(sel, n) for n in nodes):
                missing.append(sel)
                continue
            crash = crashes.get(selector_file(sel))
            if crash is not None:
                return Outcome(
                    crash.execution_state,
                    reason_codes=list(crash.reason_codes),
                    actual=crash.actual,
                    evidence_refs=[f"pytest:{s}" for s in selectors],
                )
            for key, (outcome, message) in sorted(results.items()):
                node = key.split("|", 1)[0]
                if not selector_matches(sel, node):
                    continue
                ref = f"pytest:{_shown(key)}:{outcome}"
                refs.append(ref)
                if outcome == "failed":
                    failed.append(f"{_shown(key)}: {sanitize(message, 160)}")
                elif outcome == "error":
                    errors.append(f"{_shown(key)}: {sanitize(message, 160)}")
                elif outcome == "skipped":
                    skipped.append(f"{_shown(key)}: {sanitize(message, 160)}")
                else:
                    passed += 1
        refs = sorted(set(refs))
        if missing:
            return Outcome(
                "missing_fixture",
                reason_codes=["oracle_selector_not_found"],
                evidence_refs=refs,
                actual="oracle selectors matched no test: " + ", ".join(sorted(missing)),
            )
        if failed:
            return Outcome(
                "completed",
                "FAIL",
                reason_codes=["oracle_test_failed"],
                evidence_refs=refs,
                actual=f"{len(failed)} oracle test(s) failed, {passed} passed: " + "; ".join(failed[:5]),
            )
        if errors:
            return Outcome(
                "error", reason_codes=["oracle_test_error"], evidence_refs=refs, actual="; ".join(errors[:5])
            )
        if skipped:
            return Outcome(
                "skipped",
                reason_codes=["oracle_test_skipped"],
                evidence_refs=refs,
                actual=f"{len(skipped)} oracle test(s) skipped, {passed} passed: " + "; ".join(skipped[:3]),
            )
        if passed == 0:
            return Outcome(
                "missing_fixture",
                reason_codes=["no_test_executed"],
                evidence_refs=refs,
                actual="no oracle test executed",
            )
        return Outcome("completed", "PASS", evidence_refs=refs, actual=f"{passed} oracle test(s) passed")


def _guess_file(case: ET.Element) -> str:
    """Fallback when a JUnit row lacks ``file``: derive it from the module part of classname."""
    parts = (case.get("classname") or "").split(".")
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i]) + ".py"
        if (REPO_ROOT / candidate).is_file():
            return candidate
    return "/".join(parts) + ".py"


def _shown(key: str) -> str:
    """Display id of a result key ``node|name``: the node plus any parametrisation suffix."""
    node, name = key.split("|", 1)
    return node + ("[" + name.split("[", 1)[1] if "[" in name else "")
