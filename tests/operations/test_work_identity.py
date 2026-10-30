"""T19C / H3 K1: distinct-Work identity (work-identity/2; the /1 rule plus the D1.9 content-hash step) and its parity with the harvest identity rule."""

from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

import pytest

from app.modules.operations.corpus import (
    PaperRecord,
    WorkIndex,
    _title_candidates,
    identifiers_from_key,
    paper_key,
    record_for,
    same_work,
    titles_near_duplicate,
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import harvest_rsoxs  # noqa: E402

from app.modules.literature.intake import filename_doi_candidates  # noqa: E402

TITLE = "Resonant soft X-ray scattering of synthetic polymer blends"


def _pairs():
    """(left, right) records covering every branch of works_same_identity."""
    journal = PaperRecord("10.1021_x.1.pdf", doi="10.1021/x.1", title=TITLE, first_author="doe")
    return [
        (journal, PaperRecord("dup.pdf", doi="10.1021/x.1", title="unrelated", first_author="roe")),
        (journal, PaperRecord("10.1021_x.2.pdf", doi="10.1021/x.2", title=TITLE, first_author="doe")),
        (journal, PaperRecord("2306.07295v1.pdf", doi="10.48550/arxiv.2306.07295", title=TITLE + ".",
                              first_author="doe")),
        (journal, PaperRecord("2306.07295v2.pdf", arxiv_id="2306.07295", title=TITLE, first_author="roe")),
        (PaperRecord("a.pdf", arxiv_id="2306.07295"), PaperRecord("b.pdf", arxiv_id="2306.07295")),
        (PaperRecord("a.pdf", title=TITLE), PaperRecord("b.pdf", title=TITLE.upper())),
        (PaperRecord("a.pdf", title=TITLE, first_author="doe"), PaperRecord("b.pdf", title=TITLE, first_author="roe")),
        (PaperRecord("a.pdf", title=TITLE), PaperRecord("b.pdf", title="Completely different subject matter here")),
        (PaperRecord("a.pdf"), PaperRecord("b.pdf")),
        (PaperRecord("a.pdf", doi="10.48550/arxiv.1", title=TITLE),
         PaperRecord("b.pdf", doi="10.1/j", title=TITLE)),
    ]


def _as_harvest(rec):
    return {"doi": f"https://doi.org/{rec.doi}" if rec.doi else None, "arxiv_id": rec.arxiv_id or None,
            "title": rec.title, "authors": [rec.first_author.title()] if rec.first_author else []}


@pytest.mark.parametrize("left,right", _pairs())
def test_pairwise_rule_matches_harvest_works_same_identity(left, right):
    expected = harvest_rsoxs.works_same_identity(_as_harvest(left), _as_harvest(right))
    assert same_work(left, right) is expected
    assert same_work(right, left) is harvest_rsoxs.works_same_identity(_as_harvest(right), _as_harvest(left))


def test_title_rule_matches_harvest():
    for a, b in [(TITLE, TITLE + " revisited"), (TITLE, "The " + TITLE), ("", ""), ("of the", "of the")]:
        assert titles_near_duplicate(a, b) is harvest_rsoxs.titles_near_duplicate(a, b)


@pytest.mark.parametrize("key", ["10.1021_acs.macromol.0c01782.pdf", "10.48550_arXiv.2306.07295.pdf",
                                 "2306.07295v1.pdf", "arXiv_2306.07295.pdf", "XRAY1.pdf", "cond-mat_0510522v1.pdf"])
def test_identifiers_from_key_agree_with_the_literature_filename_rule(key):
    doi, arxiv = identifiers_from_key(key)
    candidates = [c.lower() for c in filename_doi_candidates(key)]
    if doi:
        assert doi in candidates
    if arxiv and "." in arxiv:
        assert f"10.48550/arxiv.{arxiv}" in candidates
    if key == "XRAY1.pdf":
        assert (doi, arxiv) == ("", "")
    if key.startswith("cond-mat"):
        assert arxiv == "cond-mat/0510522"


def test_repeated_work_references_are_one_work():
    """OPS-02: one Work cited as a journal PDF, an arXiv preprint and a second copy counts once."""
    records = [
        record_for("10.1021_x.1.pdf", [{"paper_title": TITLE, "authors": ["Doe, J."], "publication_year": 2021}]),
        record_for("2306.07295v1.pdf", [{"paper_title": TITLE, "authors": ["J. Doe"], "publication_year": 2020}]),
        record_for("copy/10.1021_x.1.pdf"),
        record_for("unrelated.pdf", [{"paper_title": "A different synthetic subject", "publication_year": 2019}]),
        record_for("bare.pdf"),
    ]
    index = WorkIndex.build(records)
    assert len(index.records) == 4  # the copy has the same key as the journal PDF
    assert len(index) == 3
    work = index.work_of["10.1021_x.1.pdf"]
    assert work == "doi:10.1021/x.1" and index.work_of["2306.07295v1.pdf"] == work
    assert index.members[work] == ["10.1021_x.1.pdf", "2306.07295v1.pdf"]
    assert index.year_of(work) == (2020, True)  # tie between 2020 and 2021 -> earliest, flagged as conflicting
    assert index.multi_version_works() == 1 and index.key_only_works() == 1
    assert index.resolve("10.1021/X.1") == work and index.resolve("arXiv:2306.07295") == work
    assert index.resolve("https://doi.org/10.1021/x.1") == work and index.resolve("nope") is None


def test_two_journal_dois_are_never_merged_even_with_equal_titles():
    index = WorkIndex.build([PaperRecord("a.pdf", doi="10.1/a", title=TITLE),
                             PaperRecord("b.pdf", doi="10.1/b", title=TITLE)])
    assert len(index) == 2


def test_prefix_filter_finds_every_pair_brute_force_finds():
    rng = random.Random(7)
    vocab = [f"w{i}" for i in range(40)]
    recs = {}
    for i in range(120):
        words = rng.sample(vocab, rng.randint(3, 9))
        if i % 3 == 0 and recs:  # near-copies of an earlier title
            base = rng.choice(list(recs.values())).title.split()
            words = base + ([rng.choice(vocab)] if rng.random() < 0.5 else [])
        recs[f"k{i:03d}.pdf"] = PaperRecord(f"k{i:03d}.pdf", title=" ".join(words))
    found = set(_title_candidates(recs, 0.82))
    for a, b in itertools.combinations(sorted(recs), 2):
        if titles_near_duplicate(recs[a].title, recs[b].title):
            assert (a, b) in found


def test_paper_key_is_the_basename():
    assert paper_key("/tmp/x/paper.pdf") == "paper.pdf" and paper_key("C:\\d\\p.pdf") == "p.pdf"
    assert paper_key(None) == "" and paper_key("p1") == "p1"
