"""Unit tests for synchrotron X-ray tomography harvest helpers (no network)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harvest_tomo import (
    TOMO_DEST,
    classify_tomo,
    in_other_corpus,
    parse_args,
    score_tomo,
    _refuse_foreign_dest,
)


def test_classify_keeps_synchrotron_tomo_reviews():
    assert (
        classify_tomo(
            "A review of synchrotron X-ray tomography",
            "We review synchrotron microtomography, holotomography, and nano-CT at light sources.",
        )
        == "A"
    )


def test_classify_keeps_beamline_methods():
    assert (
        classify_tomo(
            "Ptychographic tomography at TOMCAT",
            "Hard X-ray ptychographic tomography of a battery electrode at the Swiss Light Source.",
        )
        == "B"
    )
    assert (
        classify_tomo(
            "Transmission X-ray microscopy of nanoporous gold",
            "TXM tomography at an APS beamline reconstructs 3D morphology.",
        )
        == "B"
    )
    assert classify_tomo("X-ray nanotomography at ESRF ID19") == "B"


def test_classify_drops_clinical_ct_and_lab_only():
    assert (
        classify_tomo(
            "CT protocol for patient positioning",
            "A computed tomography protocol and radiographic workflow.",
        )
        == "C"
    )
    assert (
        classify_tomo(
            "Laboratory micro-CT of a polymer foam",
            "A sealed-tube laboratory scanner images closed cells. Benchtop source only.",
        )
        == "C"
    )


def test_classify_drops_rsoxs_and_fundamentals_and_ops():
    assert (
        classify_tomo(
            "P3HT:PCBM morphology in organic solar cells",
            "RSoXS shows phase separation in a conjugated polymer blend.",
        )
        == "C"
    )
    assert (
        classify_tomo(
            "A review of the Klein-Nishina cross section",
            "We review Compton scattering of X-rays and the Klein-Nishina formula.",
        )
        == "C"
    )
    assert classify_tomo("11.0.1.2 ophyd motor record and ESAF checklist") == "C"


def test_score_prefers_reviews_at_a_light_source():
    review = score_tomo(
        "A review of synchrotron X-ray nanotomography",
        "This review covers TXM and holotomography at ESRF and APS.",
    )
    assert review["tomo_tier"] == "A"
    assert review["corpus_role"] == "core_method"
    assert review["kg_priority"] > 0.4


def test_parse_args_default_dest_is_papers_tomo():
    args = parse_args([])
    assert args.dest == TOMO_DEST
    assert args.max_pdfs == 40


def test_refuses_rsoxs_and_xray_dest():
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(SystemExit, match="papers/rsoxs"):
        _refuse_foreign_dest(root / "papers" / "rsoxs")
    with pytest.raises(SystemExit, match="papers/xray"):
        _refuse_foreign_dest(root / "papers" / "xray")


def test_in_other_corpus_matches_doi_or_arxiv():
    known = {"https://doi.org/10.1000/xyz", "arxiv:2401.00001"}
    assert in_other_corpus({"doi": "https://doi.org/10.1000/xyz"}, known)
    assert in_other_corpus({"arxiv_id": "2401.00001"}, known)
    assert not in_other_corpus({"doi": "https://doi.org/10.1000/other"}, known)
