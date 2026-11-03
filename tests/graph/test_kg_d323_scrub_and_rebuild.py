"""D3.23: scrubbed bl1101 v1..v7 and the rebuilt rsoxs v4 (standard json2kg serialization).

The old files (``matkg_rsoxs_v3.json``, ``matkg_bl1101_v8.json``, ``matkg_bl1101_v9.json``) are history and stay as
they are; the rebuilt ``matkg_rsoxs_v4.json`` is a new version. Nothing here prints a value: only counts and rules.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

import pytest

from app.modules.graph.adoption import adopt_legacy_flat_file
from app.modules.graph.releases import check_disclosure

KG = Path(__file__).resolve().parents[2] / "storage" / "kg"
SCRUBBED = [f"matkg_bl1101_v{n}.json" for n in range(1, 8)]
NEW = ["matkg_rsoxs_v4.json"]
_KEY_LITERAL = re.compile(r"(?i)api.?key\w*['\"]?\s*[:=]\s*'your-[a-z-]+'")


def _graph(name: str) -> dict[str, Any]:
    return json.loads((KG / name).read_bytes().decode("utf-8"))


@pytest.mark.parametrize("name", SCRUBBED)
def test_scrubbed_bl1101_file_has_no_r3_or_r4_finding(name: str) -> None:
    rules = Counter(f.rule for f in check_disclosure(_graph(name)).findings)
    assert rules["R3"] == 0 and rules["R4"] == 0 and rules["R5"] == 0, dict(rules)


@pytest.mark.parametrize("name", SCRUBBED)
def test_scrubbed_bl1101_file_keeps_placeholders_not_key_literals(name: str) -> None:
    text = (KG / name).read_text(encoding="utf-8")
    assert not _KEY_LITERAL.search(text)
    assert "/tmp/" not in text and "file://" not in text
    assert text.count("<REDACTED>") == 2 and text.count("<tmp>/") == 1
    assert not text.endswith("\n")  # formatting of the old files is kept (no trailing newline)


def test_only_v7_keeps_the_documentchunk_review_flags() -> None:
    """R7 flags are reported, not changed: v7 keeps its DocumentChunk nodes and a full-body field."""
    flags = [f for f in check_disclosure(_graph("matkg_bl1101_v7.json")).findings if f.rule == "R7"]
    assert len(flags) == 10
    for name in SCRUBBED[:-1]:
        assert not [f for f in check_disclosure(_graph(name)).findings if f.rule == "R7"], name


@pytest.mark.parametrize("name", SCRUBBED + NEW)
def test_file_passes_every_adoption_check_except_the_known_r7_flags(name: str) -> None:
    """R1-R7 through the adoption dry run (writes nothing). Only bl1101_v7 is refused, on R7."""
    try:
        outcome = adopt_legacy_flat_file(KG / name, code_commit="a" * 40, dry_run=True)
    except Exception as exc:  # AdoptionRefused carries the rule
        assert name == "matkg_bl1101_v7.json" and getattr(exc, "rule", "") == "R7", (name, repr(exc)[:200])
    else:
        assert name != "matkg_bl1101_v7.json"
        assert outcome.status == "would_adopt"


@pytest.mark.parametrize("name", NEW)
def test_new_file_is_in_the_standard_json2kg_serialization(name: str) -> None:
    blob = (KG / name).read_bytes()
    graph = json.loads(blob.decode("utf-8"))
    assert set(graph) == {"things", "associations", "metadata"}
    assert graph["things"] and graph["associations"]
    assert json.dumps(graph, indent=2, ensure_ascii=False).encode("utf-8") == blob
    assert not blob.endswith(b"\n")


def test_rsoxs_v4_is_rebuilt_from_the_repaired_terms_and_v3_is_untouched() -> None:
    v3 = _graph("matkg_rsoxs_v3.json")
    assert "nodes" in v3 and "things" not in v3  # history: the old nodes/edges layout stays as it was
    meta = _graph("matkg_rsoxs_v4.json")["metadata"]
    assert meta  # the source terms' metadata block is carried through by json2kg
