"""Tests for missing RSoXS paper listing. Never invents RTF citations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from list_missing_rsoxs_papers import (
    collect_missing_citations,
    verify_written_against_rtf,
)
from harvest_rsoxs import parse_pubtemp_rtf

_MINI_RTF = r"""{\rtf1\ansi
\pard\plain \b \qc \s18\sa180\widctlpar \f43Refereed Journal Articles (2)\b0\par
 \cr Ferron, T.J., M. Pope, and B.A. Collins, \ldblquote Spectral Analysis for Resonant Soft X-Ray Scattering Enables Measurement of Interfacial Width in 3D Organic Nanostructures,\rdblquote {\i  Phys. Rev. Lett.}\fs20 (2017).  (doi:10.1103/PhysRevLett.119.167801) 11.0.1.2\par
 \cr Sch\u252#tz, G., \ldblquote A brand new RSoXS paper without a DOI yet,\rdblquote {\i  Fake J.}\fs20 (2026). 11.0.1.2\par
\pard\plain \b \qc \s18\sa180\widctlpar \f43Refereed Conference Proceedings (1)\b0\par
 \cr Freychet, G., \ldblquote Using resonant soft x-ray scattering to image patterns on undeveloped resists,\rdblquote Proceedings of SPIE 10809 (2018). (doi:10.1117/12.2502769) 11.0.1.2\par
\pard\plain \b \qc \s18\sa180\widctlpar \f43Non-refereed Publications (magazine article, book review, etc.) (1)\b0\par
 \cr Cordova, I.A., \ldblquote Leveraging chemical contrast to spotlight the latent profile of novel resists (Conference Presentation),\rdblquote Proc. SPIE 11147 (2019). (doi:10.1117/12.2539040) 11.0.1.2\par
\pard\plain \b \qc \s18\sa180\widctlpar \f43Awards (1)\b0\par
 \cr Ritchie, R.O., \ldblquote Elected Member of the National Academy of Sciences,\rdblquote  2025. 11.0.1.2\par
\pard\plain \b \qc \s18\sa180\widctlpar \f43Invited Lectures (1)\b0\par
 \cr Su, G., \ldblquote Fundamentals and Applications of NEXAFS Spectroscopy,\rdblquote  2023 ALS User Meeting 2023. 11.0.1.2\par
}"""


def _records(tmp_path):
    path = tmp_path / "pubtemp.rtf"
    path.write_text(_MINI_RTF, encoding="latin-1")
    return parse_pubtemp_rtf(path), path.read_text(encoding="latin-1")


def test_missing_list_copies_rtf_and_drops_talks_awards(tmp_path):
    records, raw = _records(tmp_path)
    has_pdf = {"https://doi.org/10.1103/PhysRevLett.119.167801"}

    def pdf_on_disk(paper):
        return paper.get("doi") in has_pdf

    papers = [
        {
            "doi": "https://doi.org/10.1103/PhysRevLett.119.167801",
            "title": "Spectral Analysis for Resonant Soft X-Ray Scattering Enables "
            "Measurement of Interfacial Width in 3D Organic Nanostructures",
            "selection": {"included": True},
        },
        {
            "doi": None,
            "title": "A brand new RSoXS paper without a DOI yet",
            "selection": {"included": True},
        },
        {
            "doi": "https://doi.org/10.1117/12.2502769",
            "title": "Using resonant soft x-ray scattering to image patterns on undeveloped resists",
            "selection": {"included": True},
        },
        {
            "doi": "https://doi.org/10.1117/12.2539040",
            "title": "Leveraging chemical contrast to spotlight the latent profile of novel resists (Conference Presentation)",
            "selection": {"included": True},
        },
        {
            "doi": None,
            "title": "Elected Member of the National Academy of Sciences",
            "selection": {"included": True},
        },
        {
            "doi": None,
            "title": "Fundamentals and Applications of NEXAFS Spectroscopy",
            "selection": {"included": True},
        },
        {
            "doi": "https://doi.org/10.9999/openalex-only",
            "title": "An OpenAlex-only paper that must not appear",
            "selection": {"included": True},
        },
    ]
    written, audit = collect_missing_citations(records, papers, pdf_on_disk=pdf_on_disk)
    citations = [row["citation"] for row in written]
    kinds = {row["kind"] for row in written}

    assert audit["excluded_awards"] == 1
    assert audit["excluded_invited_lectures"] == 1
    assert audit["excluded_conference_presentation_talks"] == 1
    assert audit["written_lines"] == 2
    assert kinds == {"journal", "conference"}
    assert any("brand new RSoXS paper" in c for c in citations)
    assert any("undeveloped resists" in c for c in citations)
    assert all("Elected Member" not in c for c in citations)
    assert all("NEXAFS" not in c for c in citations)
    assert all("Conference Presentation" not in c for c in citations)
    assert all("OpenAlex-only" not in c for c in citations)
    assert all("Spectral Analysis" not in c for c in citations)

    errors = verify_written_against_rtf(
        written, records, raw_rtf=raw, papers=papers, pdf_on_disk=pdf_on_disk
    )
    assert errors == []
    rtf_dois = {r["doi"] for r in records if r.get("doi")}
    for row in written:
        if row.get("doi"):
            assert row["doi"] in rtf_dois


def test_verify_rejects_citation_not_in_rtf(tmp_path):
    records, raw = _records(tmp_path)
    fake = [
        {
            "citation": "Nobody, A., \"Not in the PubTemp export,\" Fake J. (2026).",
            "title": "Not in the PubTemp export",
            "doi": "https://doi.org/10.9999/not-in-rtf",
            "kind": "journal",
        }
    ]
    errors = verify_written_against_rtf(fake, records, raw_rtf=raw)
    assert errors
    assert any("not in RTF" in e or "not in RTF parse set" in e for e in errors)
