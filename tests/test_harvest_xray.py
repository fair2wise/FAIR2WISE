"""Unit tests for X-ray fundamentals harvest helpers (no network)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harvest_xray import (
    XRAY_DEST,
    classify_xray,
    in_rsoxs_corpus,
    parse_args,
    score_xray,
    _refuse_rsoxs_dest,
)


def test_classify_keeps_principle_reviews():
    assert (
        classify_xray(
            "A review of the Klein-Nishina cross section",
            "We review Compton scattering of X-rays and the Klein-Nishina formula.",
        )
        == "A"
    )
    assert (
        classify_xray(
            "Introduction to ALARA and half-value layers",
            "X-ray shielding and the half-value layer as radiation protection principles.",
        )
        == "A"
    )


def test_classify_keeps_methods_physics():
    assert (
        classify_xray(
            "Inverse Compton emission from accretion disks",
            "Seed photons are upscattered by a hot corona via inverse Compton.",
        )
        == "B"
    )
    assert (
        classify_xray(
            "Fluorescence yield and Auger decay at the K-edge",
            "We measure the fluorescence yield competing with Auger X-ray decay.",
        )
        == "B"
    )
    assert classify_xray("Bragg's law and the Ewald sphere") == "B"


def test_classify_keeps_technique_only_when_it_teaches_physics():
    assert (
        classify_xray(
            "SAXS tutorial: deriving momentum transfer q",
            "This tutorial derives q-space and the form factor for SAXS.",
        )
        in {"A", "B"}
    )


def test_classify_drops_application_practice_catalogs_and_ops():
    assert (
        classify_xray(
            "P3HT:PCBM morphology in organic solar cells",
            "RSoXS shows phase separation in a conjugated polymer blend.",
        )
        == "C"
    )
    assert (
        classify_xray(
            "CT protocol for patient positioning",
            "A computed tomography protocol and radiographic workflow.",
        )
        == "C"
    )
    assert (
        classify_xray(
            "Chandra catalog of X-ray sources in the Galactic plane",
            "We present a catalog of X-ray sources.",
        )
        == "C"
    )
    assert classify_xray("Crystal structure of this steel by powder diffraction") == "C"
    assert classify_xray("11.0.1.2 ophyd motor record and ESAF checklist") == "C"
    assert classify_xray("SOXS Spectrograph Instrument Control Software") == "C"


def test_score_prefers_principle_reviews_over_technique_mentions():
    review = score_xray(
        "A review of X-ray refractive index and the critical angle",
        "This review derives n = 1 − δ − iβ and total external reflection.",
    )
    assert review["xray_tier"] == "A"
    assert review["corpus_role"] == "core_principle"
    assert review["kg_priority"] > 0.4


def test_parse_args_default_dest_is_papers_xray():
    args = parse_args([])
    assert args.dest == XRAY_DEST
    assert args.max_pdfs == 40


def test_refuses_rsoxs_dest():
    with pytest.raises(SystemExit, match="papers/rsoxs"):
        _refuse_rsoxs_dest(Path(__file__).resolve().parents[1] / "papers" / "rsoxs")


def test_in_rsoxs_corpus_matches_doi_or_arxiv():
    known = {"https://doi.org/10.1000/xyz", "arxiv:2401.00001"}
    assert in_rsoxs_corpus({"doi": "https://doi.org/10.1000/xyz"}, known)
    assert in_rsoxs_corpus({"arxiv_id": "2401.00001"}, known)
    assert not in_rsoxs_corpus({"doi": "https://doi.org/10.1000/other"}, known)
