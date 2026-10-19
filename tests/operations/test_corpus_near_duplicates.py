"""T19D: the corpus_qa §1 arXiv x journal near-duplicate pair loop, pruned but unchanged.

``corpus._near_duplicate_pairs`` skips the edit distance for pairs that two exact lower
bounds already put at >= 10 edits, and computes the rest with a banded distance. These
tests prove the result (pairs, order and scores) is the legacy loop's on populations built
to sit on every boundary: overlap 0.80, edit distances 8-11, titles of 30/31 characters,
prefix cut at 100 characters and empty or missing titles.
"""

from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import pytest

from app.modules.operations.corpus import (
    _levenshtein,
    _levenshtein_within,
    _near_duplicate_pairs,
    _title_overlap,
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import corpus_qa  # noqa: E402

_ALPHABET = string.ascii_lowercase + "  -:"


def _legacy_pairs(arxiv, journal):
    """The loop body of ``corpus_qa.audit_duplicate_works`` (lines 241-254), verbatim in effect."""
    near = []
    for adoi, atitle in arxiv.items():
        if not atitle:
            continue
        for jdoi, jtitle in journal.items():
            if not jtitle:
                continue
            overlap = corpus_qa._title_overlap(atitle, jtitle)
            if overlap >= 0.80:
                near.append((adoi, jdoi, overlap))
            elif len(atitle) > 30 and len(jtitle) > 30:
                lev = corpus_qa._levenshtein(atitle[:100], jtitle[:100])
                if lev < 10:
                    near.append((adoi, jdoi, 1.0 - lev / 100))
    return near


def _mutate(rng, text, edits):
    chars = list(text)
    for _ in range(edits):
        op = rng.randrange(3)
        pos = rng.randrange(len(chars) + 1)
        if op == 0 and chars:
            chars[min(pos, len(chars) - 1)] = rng.choice(_ALPHABET)
        elif op == 1:
            chars.insert(pos, rng.choice(_ALPHABET))
        elif chars:
            del chars[min(pos, len(chars) - 1)]
    return "".join(chars)


def _population(seed, n_arxiv=40, n_journal=60):
    rng = random.Random(seed)
    base = ["".join(rng.choice(_ALPHABET) for _ in range(rng.choice([20, 30, 31, 45, 99, 100, 101, 140])))
            for _ in range(25)]
    arxiv = {}
    for i in range(n_arxiv):
        arxiv[f"10.48550_arXiv.2306.{i:05d}"] = rng.choice(base + ["", None])
    journal = {}
    for i in range(n_journal):
        src = rng.choice(base)
        journal[f"10.1000_j.{i}"] = rng.choice([src, _mutate(rng, src, rng.randrange(0, 14)), "", src.upper(),
                                               src + " extra words", "".join(reversed(src))])
    return arxiv, journal


@pytest.mark.parametrize("seed", range(12))
def test_pruned_pair_loop_equals_corpus_qa(seed):
    arxiv, journal = _population(seed)
    expected = _legacy_pairs(arxiv, journal)
    assert _near_duplicate_pairs(arxiv, journal) == expected
    assert any(score < 1.0 for _a, _j, score in expected)  # the edit-distance branch is exercised


@pytest.mark.parametrize("seed", range(8))
def test_banded_distance_is_exact_below_the_limit(seed):
    rng = random.Random(1000 + seed)
    for _ in range(150):
        a = "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(0, 110)))
        b = _mutate(rng, a, rng.randrange(0, 16)) if rng.random() < 0.8 else "".join(
            rng.choice(_ALPHABET) for _ in range(rng.randrange(0, 110)))
        full = _levenshtein(a, b)
        assert full == corpus_qa._levenshtein(a, b)
        assert _levenshtein_within(a, b, 9) == (full if full <= 9 else None)


def test_overlap_helper_is_unchanged():
    for a, b in [("A b c", "a B c"), ("", "x"), ("x y z w", "x y z q")]:
        assert _title_overlap(a, b) == corpus_qa._title_overlap(a, b)
