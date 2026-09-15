"""Unit tests for RSoXS harvest helpers (no network)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harvest_rsoxs import (
    classify_rsoxs,
    collapse_near_duplicates,
    detect_facility,
    doi_stem,
    import_pubtemp,
    merge_candidates,
    parse_args,
    parse_pubtemp_plain,
    parse_pubtemp_rtf,
    score_rsoxs,
    works_same_identity,
    year_from_work,
    _missing_pdf_dois,
    _openalex_oa_urls_for_doi,
    _resolve_oa_pdfs,
)


def test_doi_stem_strips_url_and_slash():
    assert doi_stem("https://doi.org/10.1002/adma.202000001") == "10.1002_adma.202000001"
    assert doi_stem("10.48550/arXiv.2401.00001") == "10.48550_arXiv.2401.00001"


def test_year_from_work_prefers_publication_year():
    assert year_from_work({"publication_year": 2024, "publication_date": "2021-01-01"}) == "2024"
    assert year_from_work({"publication_date": "2019-06-02"}) == "2019"
    assert year_from_work({}) == "unknown"


def test_merge_candidates_prefers_soft_matter_over_cuprate_recency():
    cuprate = {
        "doi": "https://doi.org/10.1/b",
        "title": "Resonant soft x-ray scattering of a cuprate",
        "abstract": "We use resonant soft x-ray scattering to probe charge density wave order.",
        "publication_date": "2024-01-01",
        "publication_year": 2024,
    }
    polymer = {
        "doi": "https://doi.org/10.1/a",
        "title": "Resonant Soft X-ray Scattering Reveals the Distribution of Dopants "
        "in Semicrystalline Conjugated Polymers",
        "abstract": "Polarized resonant soft X-ray scattering (P-RSoXS) of P3HT films.",
        "publication_date": "2018-01-01",
        "publication_year": 2018,
    }
    merged = merge_candidates([cuprate], [polymer])
    assert merged[0]["doi"] == polymer["doi"]
    assert merged[0]["corpus_role"] == "core_soft_matter"


def test_classify_keeps_experimental_rsoxs_even_when_title_omits_it():
    assert (
        classify_rsoxs(
            "Resonant Soft X-ray Scattering Reveals the Distribution of Dopants "
            "in Semicrystalline Conjugated Polymers"
        )
        == "A"
    )
    assert (
        classify_rsoxs(
            "Kinetically-Arrested Phase Separation leads to Tunable Domain "
            "Structures in Vapor-Deposited Glasses",
            "We report complementary RSoXS measurements to determine whether "
            "phase separation extends through the film thickness.",
        )
        == "A"
    )
    assert (
        classify_rsoxs(
            "Controlled component segregation in vapor-deposited organic "
            "semiconductor glass mixtures",
            "Resonant soft X-ray scattering (RSoXS) and the NIST RSoXS "
            "Simulation Suite are used to quantify nanoscale segregation.",
        )
        == "A"
    )


def test_classify_methods_papers_are_tier_b():
    assert (
        classify_rsoxs(
            "CyRSoXS: a GPU-accelerated virtual instrument for polarized "
            "resonant soft X-ray scattering"
        )
        == "B"
    )


def test_classify_rejects_acronym_collisions_and_off_topic():
    assert classify_rsoxs("SOXS Spectrograph Instrument Control Software") == "C"
    assert classify_rsoxs("TIGERISS silicon detector") == "C"
    assert classify_rsoxs("Circumbinary planets around post-AGB binaries") == "C"
    assert classify_rsoxs("ECG differential diagnosis with graph neural nets") == "C"
    assert classify_rsoxs("Tree-based RAG compression for Graph anomaly detection") == "C"
    assert classify_rsoxs("Scrum Master gender dynamics in agile teams") == "C"
    assert (
        classify_rsoxs(
            "Customized spin spirals in ferromagnetic thin films",
            "Polarized neutron reflectometry and nuclear resonant scattering.",
        )
        == "C"
    )
    title_only_glass = classify_rsoxs(
        "Kinetically-Arrested Phase Separation leads to Tunable Domain "
        "Structures in Vapor-Deposited Glasses"
    )
    assert title_only_glass == "C"


def test_score_three_dimensions_prioritize_soft_matter_not_als():
    p3ht = score_rsoxs(
        "Resonant Soft X-ray Scattering Reveals the Distribution of Dopants "
        "in Semicrystalline Conjugated Polymers",
        "The distribution of counterions in doped films of P3HT was examined "
        "using polarized resonant soft X-ray scattering (P-RSoXS). Scattering "
        "anisotropy varies with composition of crystalline and amorphous domains.",
    )
    cyrsoxs = score_rsoxs(
        "CyRSoXS: a GPU-accelerated virtual instrument for polarized "
        "resonant soft X-ray scattering",
        "P-RSoXS provides unique sensitivity to molecular orientation in soft "
        "materials such as polymers. CyRSoXS uses GPUs to simulate P-RSoXS "
        "from tensor morphologies, enabling fitting and inverse design.",
    )
    nickelate = score_rsoxs(
        "Absence of 3a0 Charge Density Wave Order in the Infinite Layer Nickelates",
        "Using resonant soft x-ray scattering we search for CDW order in nickelates.",
    )
    magnet = score_rsoxs(
        "Antiferromagnetic order in a layered magnetic topological insulator "
        "probed by resonant soft x-ray scattering",
        "Resonant soft x-ray scattering probes spin orientation and exchange.",
    )
    assert p3ht["corpus_role"] == "core_soft_matter"
    assert cyrsoxs["corpus_role"] == "core_technique"
    glasses = score_rsoxs(
        "Controlled component segregation in vapor-deposited organic "
        "semiconductor glass mixtures",
        "Resonant soft X-ray scattering (RSoXS) and the NIST RSoXS "
        "Simulation Suite are used to quantify nanoscale segregation.",
    )
    assert glasses["corpus_role"] == "core_soft_matter"
    assert nickelate["corpus_role"] == "general"
    assert magnet["soft_matter_relevance"] <= 0.05
    assert p3ht["soft_matter_relevance"] >= 0.85
    assert p3ht["kg_priority"] > nickelate["kg_priority"]
    assert cyrsoxs["method_relevance"] >= 0.9
    nickelate_als = score_rsoxs(
        nickelate_title := "Absence of 3a0 Charge Density Wave Order in the Infinite Layer Nickelates",
        "Using resonant soft x-ray scattering we search for CDW order in nickelates.",
        extra="This research used beamline 11.0.1.2 of the Advanced Light Source.",
    )
    assert nickelate_als["facility"] == "ALS"
    assert nickelate_als["beamline"] == "11.0.1.2"
    assert nickelate_als["kg_priority"] == nickelate["kg_priority"]
    assert p3ht["kg_priority"] > nickelate_als["kg_priority"]
    assert score_rsoxs("Scrum Master gender dynamics")["kg_priority"] == 0.0


def test_collapse_cyrsoxs_preprint_into_journal():
    journal = {
        "doi": "https://doi.org/10.1107/s1600576723002790",
        "title": "CyRSoXS : a GPU-accelerated virtual instrument for polarized "
        "resonant soft X-ray scattering",
        "publication_date": "2023-04-19",
        "pdf_path": "papers/rsoxs/2023/journal.pdf",
    }
    preprint = {
        "doi": "https://doi.org/10.48550/arXiv.2209.13121v1",
        "arxiv_id": "2209.13121v1",
        "title": "CyRSoXS: A GPU-accelerated virtual instrument for Polarized "
        "Resonant Soft X-ray Scattering (P-RSoXS)",
        "publication_date": "2022-09-27",
        "pdf_path": "papers/rsoxs/2022/preprint.pdf",
    }
    collapsed = collapse_near_duplicates([preprint, journal])
    assert len(collapsed) == 1
    assert collapsed[0]["doi"] == journal["doi"]
    assert collapsed[0]["arxiv_id"] == "2209.13121v1"
    kinds = {v["kind"] for v in collapsed[0]["versions"]}
    assert kinds == {"journal", "preprint"}


def test_aps_tes_facility_not_ssrl_and_instrument_scores():
    tes = score_rsoxs(
        "A high-speed, high-resolution Transition Edge Sensor spectrometer "
        "for soft X-rays at the Advanced Photon Source",
        "This project explores the design and development of a transition edge "
        "sensor (TES) spectrometer for resonant soft X- ray scattering (RSXS) "
        "measurements. TES spectrometers for RSXS at the Advanced Photon Source "
        "(APS) 29-ID lead to improved understanding of advanced materials.",
        extra="References: Introducing new resonant soft x-ray scattering "
        "capability in SSRL, beamline 13-3, Stanford Synchrotron Radiation "
        "Lightsource.",
    )
    assert tes["facility"] == "APS"
    assert tes["beamline"] == "29-ID"
    assert tes["corpus_role"] == "core_technique"
    assert tes["soft_matter_relevance"] == 0.0
    assert tes["technique_relevance"] >= 0.75
    assert tes["method_relevance"] >= 0.75
    assert detect_facility("ssrlite spectrometer calibration")["facility"] != "SSRL"


def test_holmium_magnetic_soft_matter_is_zero():
    ho = score_rsoxs(
        "Satellite Holmium M-Edge Spectra from the Magnetic Phase via "
        "Resonant Soft X-Ray Scattering",
        "We analyze the RXS spectra from the magnetic phases of Ho near the "
        "M4,5 absorption edges. At the M5 edge in the uniform helical phase, "
        "the azimuthal angle dependence is lacking. The spin slip phase "
        "indicates a change of magnetic structures.",
    )
    assert ho["soft_matter_relevance"] == 0.0
    assert ho["technique_relevance"] >= 0.7
    assert ho["method_relevance"] <= 0.45
    assert ho["corpus_role"] == "general"
    assert ho["kg_primary"] is False


def test_opv_outranks_helical_lc_and_glass_band():
    pffbt = score_rsoxs(
        "Influence of Polymer Aggregation and Liquid Immiscibility on Morphology "
        "Tuning by Varying Composition in PffBT4T-2DT/Nonfullerene Organic Solar Cells",
        "The temperature dependent aggregation behavior of PffBT4T polymers used "
        "in organic solar cells plays a critical role in morphology. Non-fullerene "
        "acceptors (NFAs). A hierarchical morphology is observed from resonant soft "
        "X-ray scattering (R-SoXS). Polymer crystallite size and domain connectivity "
        "are balanced.",
    )
    helical = score_rsoxs(
        "Structure of nanoscale-pitch helical phases: blue phase and twist-bend "
        "nematic phase resolved by resonant soft X-ray scattering",
        "Periodic structures of a short pitch cholesteric, blue phase and "
        "twist-bend nematic phase were probed by a resonant soft x-ray scattering "
        "(RSoXS) at the carbon K-edge.",
    )
    arrested = score_rsoxs(
        "Kinetically-Arrested Phase Separation leads to Tunable Domain "
        "Structures in Vapor-Deposited Glasses",
        "The characteristic length scale of phase-separated organic thin film "
        "blends is a critical structural parameter. Complementary Resonant Soft "
        "X-ray Scattering (RSoXS) measurements indicate that phase separation "
        "extends throughout the film thickness. Multiple length scales before "
        "kinetically arresting.",
    )
    p3ht = score_rsoxs(
        "Resonant Soft X-ray Scattering Reveals the Distribution of Dopants "
        "in Semicrystalline Conjugated Polymers",
        "The distribution of counterions in doped films of P3HT was examined "
        "using polarized resonant soft X-ray scattering (P-RSoXS).",
    )
    glasses = score_rsoxs(
        "Controlled component segregation in vapor-deposited organic "
        "semiconductor glass mixtures",
        "Resonant soft X-ray scattering (RSoXS) and the NIST RSoXS "
        "Simulation Suite are used to quantify nanoscale segregation.",
    )
    cyrsoxs = score_rsoxs(
        "CyRSoXS: a GPU-accelerated virtual instrument for polarized "
        "resonant soft X-ray scattering",
        "P-RSoXS provides unique sensitivity to molecular orientation in soft "
        "materials such as polymers. CyRSoXS uses GPUs to simulate P-RSoXS "
        "from tensor morphologies, enabling fitting and inverse design.",
    )
    assert pffbt["corpus_role"] == "core_soft_matter"
    assert pffbt["soft_matter_relevance"] >= 0.85
    assert pffbt["kg_priority"] > helical["kg_priority"]
    assert 0.65 <= arrested["kg_priority"] <= 0.75
    assert arrested["kg_priority"] > helical["kg_priority"]
    assert arrested["corpus_role"] == "core_soft_matter"
    assert p3ht["corpus_role"] == "core_soft_matter"
    assert glasses["corpus_role"] == "core_soft_matter"
    assert cyrsoxs["corpus_role"] == "core_technique"
    assert pffbt["kg_primary_tier"] == "S"
    assert helical["kg_primary_tier"] == "A"
    assert cyrsoxs["kg_primary_tier"] == "S"


def test_required_reviews_and_sunday_are_kg_primary():
    how = score_rsoxs(
        "How to RSoXS",
        "This tutorial reviews resonant soft X-ray scattering principles "
        "and practical considerations for measurement and analysis.",
    )
    collins = score_rsoxs(
        "Resonant soft X-ray scattering in polymer science",
        "This review presents a full synopsis of the technique, including "
        "background on the theoretical underpinnings, measurement best practices, "
        "and examples of recent RSoXS applications in polymer science.",
    )
    sunday = score_rsoxs(
        "Characterizing Patterned Block Copolymer Thin Films with Soft X-Rays",
        "Resonant soft X-ray scattering (RSoXS) is used to characterize "
        "patterned block copolymer thin films and domain morphology.",
    )
    assert how["rsoxs_tier"] == "A"
    assert how["kg_primary"] is True
    assert how["kg_primary_tier"] == "S"
    assert how["corpus_role"] in {"core_technique", "core_soft_matter"}
    assert how["method_relevance"] >= 0.4
    assert collins["kg_primary"] is True
    assert collins["corpus_role"] in {"core_soft_matter", "core_technique"}
    assert collins["method_relevance"] >= 0.4
    assert sunday["kg_primary"] is True
    assert sunday["corpus_role"] == "core_soft_matter"
    assert sunday["soft_matter_relevance"] >= 0.35
    fink = score_rsoxs(
        "Resonant elastic soft x-ray scattering",
        "This review covers the theory and instrumentation of resonant elastic "
        "soft x-ray scattering (RSXS/REXS) for correlated-electron materials "
        "and interfaces.",
    )
    ybco = score_rsoxs(
        "Resonant elastic soft x-ray scattering in oxygen-ordered YBa2Cu3O6+delta",
        "Using resonant elastic soft x-ray scattering we study charge order "
        "in a cuprate superconductor.",
    )
    ade = score_rsoxs(
        "Understanding morphology and chemical heterogeneity in soft materials "
        "by resonant soft X-ray scattering",
        "Resonant soft X-ray scattering (RSoXS) probes chemical heterogeneity "
        "and morphology in polymers and organic thin films.",
    )
    assert fink["corpus_role"] == "core_technique"
    assert fink["kg_primary"] is True
    assert fink["kg_primary_tier"] == "S"
    assert fink["soft_matter_relevance"] <= 0.15
    assert ybco["kg_primary_tier"] != "S" or ybco["kg_seed_rank"] != 17
    assert ade["kg_primary"] is True
    assert ade["kg_primary_tier"] == "S"
    assert ade["corpus_role"] in {"core_soft_matter", "core_technique"}
    assert ade["soft_matter_relevance"] >= 0.35


def test_europepmc_fallback_from_pmc_html_gate():
    from harvest_rsoxs import _europepmc_fallback_urls

    urls = _europepmc_fallback_urls(
        [
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC11664917/pdf/jp4c05774.pdf",
            "https://www.ncbi.nlm.nih.gov/pmc/articles/7560964",
        ]
    )
    assert urls == [
        "https://europepmc.org/articles/PMC11664917?pdf=render",
        "https://europepmc.org/articles/PMC7560964?pdf=render",
    ]


_MINI_PUBTEMP_RTF = r"""{\rtf1\ansi
\pard\plain \b \qc \s18\sa180\widctlpar \f43Refereed Journal Articles (2)\b0\par
 \cr Ferron, T.J., M. Pope, and B.A. Collins, \ldblquote Spectral Analysis for Resonant Soft X-Ray Scattering Enables Measurement of Interfacial Width in 3D Organic Nanostructures,\rdblquote {\i  Phys. Rev. Lett.}\fs20 (2017).  (doi:10.1103/PhysRevLett.119.167801) 11.0.1.2\par
 \cr Sch\u252#tz, G., \ldblquote A brand new RSoXS paper without a DOI yet,\rdblquote {\i  Fake J.}\fs20 (2026). 11.0.1.2\par
\pard\plain \b \qc \s18\sa180\widctlpar \f43Awards (1)\b0\par
 \cr Ritchie, R.O., \ldblquote Elected Member of the National Academy of Sciences,\rdblquote  2025. 11.0.1.2\par
}"""


def test_parse_pubtemp_plain_extracts_doi_authors_year_not_invented():
    rec = parse_pubtemp_plain(
        'Ferron, T.J., and B.A. Collins, "Spectral Analysis for Resonant Soft '
        'X-Ray Scattering," Phys. Rev. Lett. (2017). (doi:10.1103/PhysRevLett.119.167801) 11.0.1.2',
        "Refereed Journal Articles (1)",
    )
    assert rec["doi"] == "https://doi.org/10.1103/PhysRevLett.119.167801"
    assert rec["publication_year"] == 2017
    assert rec["authors"][0].startswith("Ferron")
    assert rec["pubtemp_kind"] == "journal"
    no_doi = parse_pubtemp_plain(
        'Nobody, A., "A brand new RSoXS paper without a DOI yet," Fake J. (2026). 11.0.1.2',
        "Refereed Journal Articles (1)",
    )
    assert no_doi["doi"] is None
    assert no_doi["title"].startswith("A brand new")


def test_parse_pubtemp_rtf_counts_sections_and_unicode(tmp_path):
    path = tmp_path / "pubtemp.rtf"
    path.write_text(_MINI_PUBTEMP_RTF, encoding="latin-1")
    records = parse_pubtemp_rtf(path)
    assert len(records) == 3
    kinds = [r["pubtemp_kind"] for r in records]
    assert kinds == ["journal", "journal", "award"]
    assert records[1]["authors"][0].startswith("Schütz")
    assert records[1]["doi"] is None
    assert records[2]["publication_year"] == 2025


def test_works_same_identity_doi_arxiv_title_authors():
    journal = {
        "doi": "https://doi.org/10.1103/PhysRevLett.119.167801",
        "title": "Spectral Analysis for Resonant Soft X-Ray Scattering Enables "
        "Measurement of Interfacial Width in 3D Organic Nanostructures",
        "authors": ["Ferron, T.J."],
    }
    same_doi = {
        "doi": "10.1103/PhysRevLett.119.167801",
        "title": "unrelated leftover title",
        "authors": ["Someone Else"],
    }
    preprint = {
        "doi": "https://doi.org/10.48550/arXiv.2209.13121v1",
        "arxiv_id": "2209.13121v1",
        "title": "CyRSoXS: A GPU-accelerated virtual instrument for Polarized "
        "Resonant Soft X-ray Scattering (P-RSoXS)",
        "authors": ["Kumar, P."],
    }
    journal_cyr = {
        "doi": "https://doi.org/10.1107/s1600576723002790",
        "title": "CyRSoXS : a GPU-accelerated virtual instrument for polarized "
        "resonant soft X-ray scattering",
        "authors": ["Kumar, P."],
    }
    hawker = {
        "title": "Member, National Academy of Engineering",
        "authors": ["Hawker, C.J."],
        "publication_year": 2021,
    }
    balsara = {
        "title": "Member, National Academy of Engineering",
        "authors": ["Balsara, N.P."],
        "publication_year": 2026,
    }
    assert works_same_identity(journal, same_doi)
    assert works_same_identity(preprint, journal_cyr)
    assert not works_same_identity(hawker, balsara)


def test_import_pubtemp_merges_existing_and_does_not_duplicate(tmp_path):
    dest = tmp_path / "rsoxs"
    dest.mkdir()
    existing = {
        "corpus": "rsoxs",
        "kg_version": "v1",
        "beamline": "11.0.1.2",
        "schema": "storage/schema/rsoxs_schema.yaml",
        "terms_path": "storage/terminology/extracted_terms_rsoxs_v1.json",
        "kg_path": "storage/kg/matkg_rsoxs_v1.json",
        "tiled_graphql": None,
        "papers": [
            {
                "doi": "https://doi.org/10.1103/PhysRevLett.119.167801",
                "title": "Spectral Analysis for Resonant Soft X-Ray Scattering Enables "
                "Measurement of Interfacial Width in 3D Organic Nanostructures",
                "pdf_path": "papers/rsoxs/2017/existing.pdf",
                "ingestion": {"status": "pending", "kg_version": "v1"},
            }
        ],
    }
    (dest / "manifest.json").write_text(json.dumps(existing), encoding="utf-8")
    rtf = tmp_path / "export.rtf"
    rtf.write_text(_MINI_PUBTEMP_RTF, encoding="latin-1")
    summary = import_pubtemp(dest, rtf, export_date="2026-09-11")
    assert summary["parsed_count"] == 3
    assert summary["matched_existing"] == 1
    assert summary["new_works"] == 2
    data = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    dois = [p.get("doi") for p in data["papers"]]
    assert dois.count("https://doi.org/10.1103/PhysRevLett.119.167801") == 1
    ferron = next(p for p in data["papers"] if p.get("doi") and "167801" in p["doi"])
    assert ferron["selection"]["included"] is True
    assert ferron["pdf_path"] == "papers/rsoxs/2017/existing.pdf"
    assert ferron["provenance"][0]["export_date"] == "2026-09-11"
    untitled = next(p for p in data["papers"] if p["title"].startswith("A brand new"))
    assert untitled["doi"] is None
    assert untitled["selection"]["included"] is True
    assert untitled["source"] == "pubtemp"


def test_resolve_oa_flag_is_harvest_pending_alias():
    assert parse_args(["--resolve-oa"]).harvest_pending is True
    assert parse_args(["--harvest-pending"]).harvest_pending is True


def test_openalex_doi_resolution_prefers_repositories_then_listed_publisher_pdfs(monkeypatch):
    payload = {
        "best_oa_location": {
            "is_oa": True,
            "pdf_url": "https://www.osti.gov/servlets/purl/1656507",
        },
        "open_access": {
            "is_oa": True,
            "oa_url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/example",
        },
        "locations": [
            {
                "is_oa": True,
                "pdf_url": "https://arxiv.org/pdf/2401.00001",
            },
            {
                "is_oa": True,
                "pdf_url": "https://journals.aps.org/prl/pdf/10.1103/example",
            },
        ],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(request, timeout):
        assert request.full_url.startswith(
            "https://api.openalex.org/works/doi:10.1021%2Facs.macromol.8b02198"
        )
        assert request.get_header("User-agent")
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr("harvest_rsoxs.urllib.request.urlopen", fake_urlopen)
    urls, arxiv_id, sources = _openalex_oa_urls_for_doi(
        "https://doi.org/10.1021/acs.macromol.8b02198",
        "researcher@example.edu",
    )
    assert urls[:2] == [
        "https://www.osti.gov/servlets/purl/1656507",
        "https://arxiv.org/pdf/2401.00001",
    ]
    assert "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/example" in urls
    assert "https://journals.aps.org/prl/pdf/10.1103/example" in urls
    assert arxiv_id == "2401.00001"
    assert sources == ["openalex"]


def test_resolve_oa_pdfs_records_openalex_provenance(monkeypatch):
    paper = {
        "doi": "https://doi.org/10.1021/acs.macromol.8b02198",
        "title": "Polarized Soft X-ray Scattering",
        "pdf_urls": [],
    }

    def fake_openalex(doi, mailto):
        return (
            ["https://www.osti.gov/servlets/purl/1656507"],
            None,
            ["openalex"],
        )

    monkeypatch.setattr("harvest_rsoxs._openalex_oa_urls_for_doi", fake_openalex)
    urls, sources = _resolve_oa_pdfs(paper, mailto="dev@localhost")
    assert urls == ["https://www.osti.gov/servlets/purl/1656507"]
    assert sources == ["openalex"]
    assert paper["oa_source"] == "openalex"
    assert paper["provenance"][-1]["kind"] == "oa_resolution"
    assert paper["provenance"][-1]["source"] == "openalex"


def _mute_extra_oa(monkeypatch):
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_osti_oa_pdfs", lambda doi: ([], []))
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_core_oa_pdfs", lambda doi, api_key=None: ([], [])
    )
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_europepmc_search_oa_pdfs", lambda doi: ([], [])
    )
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_crossref_tdm_pdfs", lambda doi: ([], []))
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_unpaywall_oa_pdfs", lambda *a, **k: ([], []))


def test_missing_pdf_dois_skips_existing_files_and_does_not_invent(monkeypatch):
    monkeypatch.setattr(
        "harvest_rsoxs._pdf_on_disk",
        lambda paper: paper.get("doi") == "https://doi.org/10.1234/has-file",
    )
    papers = [
        {
            "doi": "https://doi.org/10.1234/missing",
            "selection": {"included": True},
            "pdf_path": None,
        },
        {
            "doi": "not-a-doi",
            "selection": {"included": True},
            "pdf_path": None,
        },
        {
            "doi": "https://doi.org/10.1234/has-file",
            "selection": {"included": True},
            "pdf_path": "papers/rsoxs/2017/existing.pdf",
        },
        {
            "doi": "https://doi.org/10.1234/excluded",
            "selection": {"included": False},
            "pdf_path": None,
        },
    ]
    assert _missing_pdf_dois(papers) == ["10.1234/missing"]


def test_resolve_oa_uses_s2_cache_before_per_paper_network(monkeypatch):
    paper = {
        "doi": "https://doi.org/10.1234/s2-hit",
        "title": "Cached Semantic Scholar hit",
        "pdf_urls": [],
    }
    cache = {
        "10.1234/s2-hit": (
            ["https://arxiv.org/pdf/2401.00001"],
            ["semantic-scholar"],
        )
    }
    called = {"openalex": 0, "osti": 0}

    def fake_openalex(doi, mailto):
        called["openalex"] += 1
        return [], None, []

    def fake_osti(doi):
        called["osti"] += 1
        return [], []

    monkeypatch.setattr("harvest_rsoxs._openalex_oa_urls_for_doi", fake_openalex)
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_osti_oa_pdfs", fake_osti)
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_core_oa_pdfs", lambda doi, api_key=None: ([], [])
    )
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_europepmc_search_oa_pdfs", lambda doi: ([], [])
    )
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_crossref_tdm_pdfs", lambda doi: ([], []))
    urls, sources = _resolve_oa_pdfs(
        paper, mailto="dev@localhost", s2_cache=cache
    )
    assert urls == ["https://arxiv.org/pdf/2401.00001"]
    assert sources == ["semantic-scholar"]
    assert called["openalex"] == 1
    assert called["osti"] == 0


def test_resolve_oa_drops_publisher_hosts_from_s2_cache(monkeypatch):
    paper = {
        "doi": "https://doi.org/10.1002/pol.20210414",
        "pdf_urls": [],
    }
    cache = {
        "10.1002/pol.20210414": (
            ["https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414"],
            ["semantic-scholar"],
        )
    }
    monkeypatch.setattr(
        "harvest_rsoxs._openalex_oa_urls_for_doi", lambda *a, **k: ([], None, [])
    )
    _mute_extra_oa(monkeypatch)
    urls, sources = _resolve_oa_pdfs(paper, mailto=None, s2_cache=cache)
    assert urls == []
    assert "wiley" not in "".join(urls).lower()
    assert "semantic-scholar" not in sources


def test_resolve_oa_falls_back_to_osti_when_empty(monkeypatch):
    paper = {"doi": "https://doi.org/10.1234/osti-only", "pdf_urls": []}
    monkeypatch.setattr(
        "harvest_rsoxs._openalex_oa_urls_for_doi", lambda *a, **k: ([], None, [])
    )
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_osti_oa_pdfs",
        lambda doi: (["https://www.osti.gov/servlets/purl/1656507"], ["osti"]),
    )
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_core_oa_pdfs", lambda doi, api_key=None: ([], [])
    )
    monkeypatch.setattr(
        "harvest_rsoxs.dl.fetch_europepmc_search_oa_pdfs", lambda doi: ([], [])
    )
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_crossref_tdm_pdfs", lambda doi: ([], []))
    monkeypatch.setattr("harvest_rsoxs.dl.fetch_unpaywall_oa_pdfs", lambda *a, **k: ([], []))
    urls, sources = _resolve_oa_pdfs(paper, mailto="dev@localhost")
    assert urls == ["https://www.osti.gov/servlets/purl/1656507"]
    assert sources == ["osti"]
