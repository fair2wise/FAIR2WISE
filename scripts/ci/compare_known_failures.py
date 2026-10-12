#!/usr/bin/env python3
"""Compare a pytest JUnit report with the recorded known-failure set.

Known baseline failures are tracked, not skipped (plan section 5.3). This exits
non-zero when:

* a test fails or errors that is not in the known set;
* a known failure fails with a different message than recorded;
* a known failure no longer fails (update the known set in the same change).

Usage:
    python scripts/ci/compare_known_failures.py full.xml docs/migration/reviews/H0/known_failures.json
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Tuple


def failing_tests(junit_path: Path) -> Dict[str, Tuple[str, str]]:
    """Return ``{test_id: (kind, message)}`` for failed or errored test cases."""
    out: Dict[str, Tuple[str, str]] = {}
    for case in ET.parse(junit_path).getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
                out[test_id] = (kind, node.get("message") or "")
    return out


def main() -> int:
    """Print the comparison and return the process exit status."""
    junit_path, known_path = Path(sys.argv[1]), Path(sys.argv[2])
    known = {k["id"]: k for k in json.loads(known_path.read_text(encoding="utf-8"))["known_failures"]}
    actual = failing_tests(junit_path)
    problems = []
    for test_id, (kind, message) in sorted(actual.items()):
        entry = known.get(test_id)
        if entry is None:
            problems.append(f"NEW {kind}: {test_id}: {message[:200]}")
        elif entry["kind"] != kind or entry["message_contains"] not in message:
            problems.append(f"CHANGED known failure: {test_id}: expected {entry['kind']} containing "
                            f"{entry['message_contains']!r}, got {kind}: {message[:200]}")
    for test_id in sorted(set(known) - set(actual)):
        problems.append(f"NOW PASSING (update {known_path}): {test_id}")
    print(f"{len(actual)} failing tests; {len(known)} known; {len(problems)} problems")
    for line in problems:
        print("  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
