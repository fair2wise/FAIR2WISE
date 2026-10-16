# H3 B4: pilot paper candidates

This answers your 2026-10-16 instruction for A1/B4: "find 1-3 papers". It is a
shortlist for you to choose from. Nothing has been labelled yet, and nothing
is approved.

## How the shortlist was made

- Every PDF under `papers/` was scanned locally with PyMuPDF. That is 534
  files: 403 in `papers/rsoxs/`, 90 in `papers/` itself, plus `tomo/` and
  `staging/`. Nothing was uploaded or fetched.
- Each file was scored on:
  - RSoXS terms;
  - q-axis units (nm^-1, Å^-1);
  - values in nm;
  - tables;
  - numbers next to citation markers;
  - "assum", "literature" and "optical constants";
  - `2π/q`;
  - photon energies;
  - page count.
- The top candidates were then read page by page, and their key figures were
  rendered and checked by eye.
- Graph membership was checked against the source lists of the tracked graphs.
  - `storage/kg/matkg_bl1101_v8.json` lists no papers. Its sources are the
    BL11.0.1.2 web page, the blueprint, repos and the simulator. No paper can
    be checked against it.
  - `matkg_rsoxs_v1.json`, `_v2.json` and `_v3.json` list 86, 135 and 246
    source papers.
- **Page numbers below are PDF page numbers**, the ones a viewer shows, unless
  marked "journal p.".

### Cases these papers must exercise

- The brief names 12 cases: EXT-01 to EXT-04, FIG-01 to FIG-03, QA-01 to QA-04
  and OPS-04.
- The H3 decision speaks of "13 cases needing real-paper fixtures". The 13th is
  **LOCAL-02** (end-to-end local workflows from intake to publication and graph
  editing). It needs an isolated run on one real paper, not labelled answers,
  so any paper chosen here serves it. **OPS-02** (repeated Work references
  deduplicated) is a separate case still waiting on an approved counting
  definition; candidate C helps it where noted below.

### EXT-04 needs a preprint and a published version. What the corpus holds

- 49 groups of byte-identical files, most of them the same DOI stored twice
  (for example `papers/10.1002adma...` and `papers/rsoxs/<year>/10.1002_adma...`).
- Four "openalex" files that are byte-identical copies of arXiv preprints:
  2607.20066, 2606.09527, 1703.08007 and 2402.07280 (stored twice).
- Two files named after a published DOI that actually contain the preprint:
  - `rsoxs/2019/10.1073_pnas.1821372116.pdf` is identical to arXiv 1710.00922v2;
  - `rsoxs/2008/10.1063_1.3246788.pdf` is identical to arXiv 0810.1926.

  Both are useful warnings for EXT-04 and OPS-02, but neither is a true pair.
- **Exactly one true preprint/published pair**: CyRSoXS.
  - preprint: `rsoxs/2022/10.48550_arXiv.2209.13121v1.pdf`, 42 pages;
  - published: `rsoxs/2023/10.1107_s1600576723002790.pdf`, J. Appl. Cryst.
    2023, 16 pages.

  It is Candidate C.

---

## Candidate A: porous block copolymer membranes (Macromolecules 2015)

- **File:** `papers/rsoxs/2015/10.1021_acs.macromol.5b00950.pdf`
- **DOI:** 10.1021/acs.macromol.5b00950
- **Title:** Water Uptake and Proton Conductivity in Porous Block Copolymer
  Electrolyte Membranes. Chen, Kortright and Balsara (LBNL), Macromolecules 48,
  5648-5655, 2015.
- **Length:** 9 PDF pages: 1 eScholarship cover page and 8 article pages,
  journal pp. 5648-5655. PDF page N = journal page 5646+N. The references are
  on page 9.
- **Tracked graph:** yes, `matkg_rsoxs_v3.json`: 88 nodes cite it, including
  a shared RSoXSMeasurement node. It is not in v1 or v2.

**Why chosen.**
- It is the best single paper:
  - an RSoXS study done at BL11.0.1.2 (page 3);
  - short, with a clean main text;
  - two tables;
  - one q-axis figure in nm^-1 with three panels;
  - electron-microscopy images with scale bars;
  - one figure that mixes the paper's own data with cited data on two y-axes;
  - all four value tags, each in a single sentence that is easy to locate.
- It is an LBNL paper, which suits the facility.

**Cases it can exercise**

| case | where | what to label |
| --- | --- | --- |
| EXT-01 schema fields | Table 1 (p.3), Table 2 (p.4), RSoXS methods (p.3) | Sample records SES(0/10/40) and S-SES(0/10/40); a MeasurementRecord for RSoXS at BL11.0.1.2, 280.0 and 284.6 eV, transmission, analysed with Nika |
| EXT-02 quantity and unit | p.6, p.7, Table 2 | q* = 0.18 / 0.17 / 0.15 nm^-1; d = 34.3 / 37.0 nm; IEC 2.25 mmol/g; SL 67.3 %; thickness "40 ± 5 μm" (p.3) |
| EXT-03 no fabrication | p.3, p.7-8 | Not stated: detector distance, polarization, exposure time, any d-spacing for S-SES (only q* = 0.12 nm^-1 in the Fig. 8 caption, p.7), and the pore size in S-SES ("outside the resolution", p.8). Some values are only in the Supporting Information (Figs. S1-S4), which is not in the corpus. **Trap:** the shared graph node "RSoXS" carries `photon_energy_eV: 290.0`, which this paper never uses. |
| EXT-04 Work/version/locator | p.1 cover, journal page footers | Partial. There is one version only: an eScholarship repository copy of the published article. The cover page shifts everything by one: PDF p.2 is journal p.5648. That makes a good locator check. No preprint exists in the corpus. |
| FIG-01 panel and coordinates | Fig. 6a-c (p.6); Fig. 9a-c (p.8) | Read the peak positions in Fig. 6a and the conductivity peaks in Fig. 9b (about 0.10 S/cm at φv = 0.1 for SL = 55%). That conductivity value appears **only** in the figure, never in the text. |
| FIG-02 q-axis or scale | Fig. 6 (p.6), Fig. 5 (p.6), Fig. 2 (p.4), Fig. 4 (p.5) | Fig. 6: axis "q [nm^-1]", log scale. Fig. 5: hν in eV. Fig. 2: "100 nm" scale bars printed in the images. Fig. 4: "Scale bar represents 0.5 mm", stated in the caption. |
| FIG-03 ambiguity withheld | Fig. 6b, Fig. 6 caption, Fig. 9c | Fig. 6b: the text calls the 284.6 eV curve "a convolution of two peaks" (0.19 and 0.31 nm^-1), so those readings are uncertain. Fig. 6: "vertically shifted" and in arbitrary units, so absolute intensities must be withheld. Fig. 9c: two y-axes, one for the membranes and one for the cited HCl data, each tied to its curves only by arrows. |
| QA-01 / QA-03 numeric claims | p.6 | "What is the lamellar spacing of SES(0)?" The answer is 34.3 nm, with a quote from p.6 (journal p.5652). |
| QA-02 active-paper context | whole paper | With this paper active, ask "what photon energy was used?". The answer must be 280.0 and 284.6 eV from this paper, not the merged graph value of 290 eV. |
| QA-04 conceptual leeway | p.6 | "Why 284.6 eV rather than 285.0 eV?" The paper gives the reason, a better signal-to-noise ratio, as a concept with no number. |
| OPS-04 totals | Table 2; graph | Table 2 has 6 samples × 3 values = 18 cells. The paper has 88 nodes in rsoxs_v3, so a truncated list can be checked against a known total. |

**Examples of each value tag**

- Reported:
  - "q*SES0 = 0.18 nm−1" (p.6);
  - pore fractions "0.076 for SES(10) and 0.33 for SES(40)" (p.5);
  - water uptake "from 95% … to 240%" (p.7);
  - Table 2 values (p.4).
- Cited:
  - HCl conductivity in Fig. 9c, "data are taken from ref 35" (p.8);
  - σ maximized "at λ = 10" for HCl and "λ = 13" for H2SO4, ref. 36 (p.7);
  - phase-regime limits α = 0.22 and 0.90, from ref. 18 (p.5).
- Derived:
  - "dSES0 = 2π/q*SES0 … 34.3 nm" (p.6);
  - dSES10 = 37.0 nm (p.6);
  - the ratio "4.3 = 0.33/0.076" (p.5);
  - a "40.9% volume increase" and 16.8 % from the group-contribution method
    (p.4).
  - **Note for the labeller:** 2π/0.18 nm^-1 = 34.9 nm, not 34.3 nm. The
    printed q* is rounded, so the derived-value check needs a stated tolerance.
- Assumed:
  - refractive indices "calculated using the method described in ref 18" (p.5)
    are literature optical constants;
  - the carbon K edge "(284.2 eV)" is a literature constant (p.5);
  - "MWH2O = 18.02 g/mol" (p.4);
  - "we treat PE as a single component" (p.5).

**Cases it cannot exercise well**
- EXT-04 needs two versions of the same Work; this paper has one.
- It has no Å^-1 axis, which leaves the nm^-1/Å^-1 normalization untested.
- It has no explicit "this region is unreliable" statement in a figure.

**Labelling effort:** low, about 3-4 hours. That is 8 article pages, 2 short
tables, about 25 values, 3 figures worth coordinate reading (5, 6, 9) and
about 6 questions.

---

## Candidate B: cellulose microfibril spacing (Scientific Reports 2018)

- **File:** `papers/rsoxs/2018/10.1038_s41598-018-31024-1.pdf`
- **DOI:** 10.1038/s41598-018-31024-1
- **Title:** Resonant soft X-ray scattering reveals cellulose microfibril
  spacing in plant primary cell walls. Sci. Rep. 8:12449, 2018, open access
  (CC BY 4.0).
- **Length:** 13 PDF pages: main text pp.1-9, methods pp.9-10 and references
  pp.10-13.
- **Tracked graph:** **no**. It is not in rsoxs v1, v2 or v3, so graph-based QA
  checks cannot use it.

**Why chosen.**
- It is the strongest complement to A:
  - the q-axis is in **Å^-1**, where A uses nm^-1;
  - it states its instrument values (BL11.0.1.2, two detector distances, q
    range);
  - the AFM images carry scale bars;
  - the four-panel RSoXS figure has a deliberately weak feature;
  - the paper says outright where its data are not accurate.
- That makes it the best paper in the corpus for FIG-02 and FIG-03.
- All four value tags are again plentiful.

**Cases it can exercise**

| case | where | what to label |
| --- | --- | --- |
| EXT-01 schema fields | methods, p.10 | Samples: unextracted, Ca-treated, pectate lyase-treated and Driselase-treated onion epidermis. Measurements: RSoXS (transmission, CCD), NEXAFS (transmission and TEY), AFM. |
| EXT-02 quantity and unit | p.2, p.5, p.10 | q = 0.04 / 0.15 / 0.03 Å^-1; d = 16 / 4 / 20 nm; detector distances "50 mm and 150 mm"; q range "0.0013 Å−1 to 0.08 Å−1"; thickness 1.1 µm (p.4) |
| EXT-03 no fabrication | p.7, p.10 | No absolute intensity: "we lack an absolute intensity calibration" (p.7). No polarization or exposure values. Many values live only in the Supplementary Information (Figs. S1-S11), which is not in the corpus. |
| EXT-04 | p.1 | Weak. One version only, and its first page reads "Published: xx xx xxxx", a placeholder date. That makes it a small test that a missing version date is not invented. |
| FIG-01 panel and coordinates | Fig. 5a-d (p.6), Fig. 2 (p.3) | Four labelled panels, four energies each, log-log axes. Fig. 2 is a single FFT curve with an arrow at the RSoXS spacing. |
| FIG-02 q-axis or scale | Fig. 5 and Fig. 2 (q in Å^-1); Fig. 1b (p.3) | Fig. 1b: AFM scale bars "400.0 nm" and "50 nm" (inset), plus a ±1.0 nN colour bar |
| FIG-03 ambiguity withheld | Fig. 5a, Fig. 2, Fig. 8 (p.8) | Fig. 5a: a "weak peak near q = 0.03 Å−1" that is barely visible. Fig. 2: a broad, flat shoulder. Fig. 8: TSI values between 288 and 300 eV "are not accurate" (p.8). The withheld reading is backed by the source itself. |
| QA-01 / QA-03 numeric claims | p.5, p.7 | "What is the average spacing between microfibrils?" The answer is about 20 nm, from q = 0.03 Å^-1 (p.5). |
| QA-02 active-paper context | p.2 | "How thick is a microfibril?" This paper says 6.6 nm, and cites 3.5 nm from other work. The answer must keep the two apart. |
| QA-04 conceptual leeway | p.4-6 | "Why measure near the calcium L-edge?" The answer is contrast between pectin and cellulose, a concept. |
| OPS-04 totals | Fig. 5 | 4 panels × 4 energies = 16 curves. The SAXS/AFM/RSoXS length scales give a small, closed list. |

**Examples of each value tag**

- Reported:
  - "a diameter of 6.6 nm" (p.2);
  - "a weak peak near q = 0.03 Å−1" (p.5);
  - a Ca/C NEXAFS ratio that falls "from 1.2 … to 0.31" (p.7).
- Cited:
  - "previously reported values … of 3.5 nm" (p.2, ref. 2);
  - a spacing "within bundles is 4 nm" (p.7, ref. 18);
  - cellulose NEXAFS "previously reported" (p.5, Fig. 4a, ref. 64).
- Derived:
  - "length scales of about 16 nm and 4 nm … (d = 2π/q)" (p.2);
  - "20 nm (q = 2π/d)" (p.5);
  - δ from β through Kramers-Kronig (p.5).
- Assumed:
  - "assuming pectin makes up 40% of the cell wall by volume" (p.5);
  - "we assume each layer has a similar density" (p.6);
  - tabulated Henke constants and KKcalc (p.5, refs. 61-62).
- Setup values, which the labeller tags as reported or assumed:
  - sample-to-detector "50 mm and 150 mm" (p.10).

**Cases it cannot exercise well**
- It is not in a tracked graph, so graph-based QA (QA-02 against the graph,
  OPS-04 on graph totals) cannot use it.
- It has no table, which leaves EXT-01 table extraction to A.
- It has only one version, so EXT-04 is weak.

**Labelling effort:** moderate, about 4-5 hours. There are about 9 dense pages
of main text and many energy-specific values. Five figures matter (1, 2, 5, 6
and 8).

---

## Candidate C: CyRSoXS, preprint and published (J. Appl. Cryst. 2023)

- **Files:**
  - published: `papers/rsoxs/2023/10.1107_s1600576723002790.pdf`, 16 pages,
    J. Appl. Cryst. 56, 868-883;
  - preprint: `papers/rsoxs/2022/10.48550_arXiv.2209.13121v1.pdf`, 42 pages,
    single column, 27 Sep 2022.
- **Title:** CyRSoXS: a GPU-accelerated virtual instrument for polarized
  resonant soft X-ray scattering.
- **Tracked graph:** yes, the published version only. It is in rsoxs v1, v2
  and v3 (52 nodes in v3). The preprint is in none.

**Why chosen.**
- It is the **only true pair of a preprint and its published version** in the
  corpus, so it is the only way to test EXT-04 properly.
- The two versions differ in ways a version-aware extractor must handle.
  - **Different locators.** Table 1 is on p.8 of the published version and
    p.21 of the preprint. The figure numbers shift by one: the hexagonal
    lattice pattern is Fig. 11 published and Fig. 10 preprint.
  - **Different wording.** The published abstract says "acceleration of over
    three orders of magnitude"; the preprint says "speedup of over three orders".
  - **A value only in the published version.** The comparison "around 31 min"
    against "only 1.05 s (>1000 acceleration)" (published p.15) does not appear
    in the preprint.
  - **A unit trap.** In the published PDF, μ sits in a symbol font. Plain-text
    extraction turns "0.5 to 5 μm" into **"0.5 to 5 mm"** (p.5), while the
    preprint reads "(0.5 to 5) µm" (p.14). Likewise "nm^-1" comes out as "nm1"
    and "1 × 10^-2" as "1  102" (p.10). This is a real EXT-02/FIG-02 hazard,
    not a constructed one.
- It also makes a natural OPS-02 test: two Work versions that must resolve to
  one Work.

**Cases it can exercise**

| case | where | what to label |
| --- | --- | --- |
| EXT-04 Work/version/locator | both files | About 6 values located in both versions, plus 1 value found only in the published version |
| EXT-02 quantity and unit | published p.5, p.9-10 | voxel "5 × 5 × 5 nm"; sphere radius 50 nm; 285 eV; the μm/mm extraction trap |
| EXT-03 no fabrication | preprint | Ask for the 31 min / 1.05 s timing from the preprint. It must come back as absent. |
| FIG-01 / FIG-02 | published Fig. 11a-b, Fig. 12 (p.12) | Panels with qx/qy axes in nm^-1; Bragg peaks q*, √3q*, √4q* and so on, readable on Fig. 12 |
| FIG-03 | published Fig. 14 (p.12) | The axis is labelled "q_y" with **no unit**, so a unit reading must be withheld. |
| OPS-02 dedup | both files | Two files, one Work |
| OPS-04 totals | Tables 1-2 (pp.8-9) | Memory-requirement rows |

**Examples of each value tag**

- Reported (simulation output and benchmarks rather than measurements):
  - "CyRSoXS took only 1.05 s" (published p.15);
  - the "48 GB memory limit" (p.8).
- Cited:
  - the earlier simulator "developed by Gann et al. (2016)" (p.3);
  - the IgorPro run "around 31 min" (p.15), a figure for the reference code.
- Derived:
  - Bragg peaks "at the expected locations [q*, (3^1/2)q* …]" (p.12).
- Assumed:
  - "tabulated optical constants of polyethylene (Gann, 2022)" (p.9);
  - "If a component is oriented, we assume … uniaxial" (p.5).

**Cases it cannot exercise well**
- This is a software paper. It has no physical samples and no experimental
  measurements, so EXT-01 SampleRecord and MeasurementRecord labelling, and
  "reported measurement" QA, are weak.
- QA-04 is possible but less natural here.

**Labelling effort:** moderate, about 4 hours. The published version is
16 dense two-column pages, but only the few values used for EXT-04 need
labelling. The 42-page preprint needs locators only for those values, about
1 hour of the total.

---

## Coverage at a glance

| case | A (Macromol 2015) | B (Sci Rep 2018) | C (CyRSoXS pair) |
| --- | --- | --- | --- |
| EXT-01 | strong (2 tables) | good | weak |
| EXT-02 | strong (nm^-1) | strong (Å^-1, mm) | strong (μm→mm trap) |
| EXT-03 | strong (graph 290 eV trap) | good | good |
| EXT-04 | partial (one version) | weak | **only real pair** |
| FIG-01 | strong | strong | good |
| FIG-02 | strong (nm^-1, 100 nm bars) | strong (Å^-1, 400 nm bars) | good |
| FIG-03 | good (shifted, two axes) | **strong (source says "not accurate")** | good (unitless axis) |
| QA-01/03 | strong | strong | fair |
| QA-02 | strong (graph present) | text only (no graph) | fair |
| QA-04 | good | good | weak |
| OPS-04 | good | fair | fair |
| OPS-02 (probable 13th) | no | no | **yes** |
| in tracked graph | v3 | none | v1, v2, v3 (published only) |
| value tags | all four | all four | all four (simulation) |

Runners-up that were read and set aside:
- `rsoxs/2024/10.1021_acs.jpcb.4c05774.pdf`: in all three graphs, Å^-1, 15
  pages, but heavy with simulation and denser to label.
- `rsoxs/2017/10.1126_sciadv.1700434.pdf`: 13 pages, a table and nm^-1, but in
  no graph.
- `rsoxs/2021/10.1038_s41467-021-25176-4.pdf`: 10 pages, no table, in no graph.
- `rsoxs/2019/10.1021_acs.macromol.8b02198.pdf`: in v3, but almost no cited or
  derived numbers.

## Recommendation

- **If only one paper: Candidate A**, `10.1021_acs.macromol.5b00950`.
  - It covers 11 of the 12 named cases well, with EXT-04 only partially.
  - It is in a tracked graph.
  - It has all four value tags in short, locatable sentences.
  - It has the lowest labelling burden, at about 3-4 hours.
- **For full coverage: A + C**, about 7-8 hours in total.
  - C adds the only real preprint/published pair, which is needed for EXT-04
    and for OPS-02 if that is the 13th case.
  - C also adds the μm→mm extraction trap.
  - Both are in tracked graphs.
- **For full coverage with stronger figure tests: A + B + C**, about 11-13
  hours.
  - B adds Å^-1 axes, explicit instrument distances and a source-stated "not
    accurate" region for FIG-03.
  - It costs one more paper and has no graph coverage.

A labeller still has to be named. It cannot be the implementing agent.
