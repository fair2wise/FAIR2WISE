# Plan: General X-ray fundamentals KG

Status: **schema + harvester ready — do not run harvest or extract until asked**

A third knowledge graph, separate from RSoXS literature (`matkg_rsoxs_v*`) and
beamline ops (`matkg_bl1101_v*`). It is a **principles graph**: photon
kinematics, production, propagation, interaction channels, secondary
processes, wave/optics limits, dose physics, and the same laws in
astrophysical plasmas. Techniques (SAXS, XAS, RSoXS) appear only as
*applications of those principles*, not as the backbone of the graph.
Not polymer/device application papers, not ALS 11.0.1.2 hardware.

---

## Goal

Answer questions like:

- What is an X-ray photon, and how are energy, wavelength, and frequency related?
- What is bremsstrahlung vs characteristic radiation?
- How does an undulator (or a cosmic synchrotron plasma) produce X-rays?
- What is photoelectric absorption vs Compton vs Rayleigh vs pair production?
- What is an absorption edge, fluorescence yield, or an Auger electron?
- How do X-rays interact with low-Z vs high-Z materials?
- What is the refractive index at X-ray energies (δ, β)? What is the critical angle?
- What is q-space? What is Bragg’s law? What are f′ and f″?
- What is coherent vs incoherent scattering? What is dynamical vs kinematical diffraction?
- What is Beer–Lambert / optical depth / beam hardening?
- What is ALARA, HVL, buildup, LET, or a dose quantity?
- Why do accretion disks and X-ray binaries emit X-rays?
- What is inverse Compton / Comptonization / the cosmic X-ray background?
- What is a fluorescence yield, and how does it compete with Auger decay?
- What is the photoelectric equation (XPS kinematics)?
- Why is the Thomson cross-section polarized?
- What is dynamical diffraction vs kinematical?

Leave “what polymers do people measure with RSoXS?” to `rsoxs_v3` and “what
comes after the exit slits?” to `bl1101_v9`.

| Graph | Role |
|---|---|
| `xray` (this plan) | Physics / fundamentals |
| `rsoxs_v3` | Soft-matter / RSoXS literature |
| `bl1101_v9` | ALS 11.0.1.2 operations |

Do **not** merge nodes into existing RSoXS or ops snapshots.

---

## Physics principles (inclusive inventory)

These are **instances** (and relations), not each a new LinkML class. Prefer
one `XRayPhenomenon` / `XRayBeamProperty` / `MaterialXRayInteraction` node
per principle. The harvest classifier should keep a paper if it teaches
*any* of these, even if it never names SAXS or a synchrotron.

### Photons and kinematics

- Photon as quantum of the EM field; `E = hν = hc/λ`
- Soft / tender / hard bands as energy decades, not facility brands
- Momentum `p = E/c`, wavelength, wavevector **k**
- Polarization (linear, circular, elliptical; Stokes / Jones)
- Helicity / spin where it matters (magnetic and dichroic scattering)
- Units and conversions (eV ↔ Å ↔ nm; barn; photons/s/mrad²/0.1%BW)
- Photon statistics: Poisson arrival, shot noise, bunching (chaotic vs FEL)

### Production (lab and cosmos — same radiation physics)

- Bremsstrahlung (electron–ion, thick/thin target; Kramers spectrum)
- Characteristic line radiation; Moseley’s law; satellite lines
- Synchrotron / undulator / wiggler (ρ, K, harmonics, opening angle, brilliance)
- Inverse Compton and Comptonization (laser–electron and AGN/XRB)
- Thermal / blackbody and thermal bremsstrahlung continua
- Transition radiation, channeling radiation, betatron radiation, PXR
  (principle-level; rare but same Maxwell + trajectory physics)
- Free-electron laser SASE / seeded FEL (gain, bandwidth — not a machine SOP)
- X-ray tubes and plasma sources as instances of the same production laws
- Time structure: pulse length, bunch length, peak vs average brilliance

### Atomic structure and spectroscopy (the atom, not the sample)

- Bound–bound, bound–free, free–free
- Dipole approximation; selection rules; multipoles (E1, E2, M1)
- Natural linewidth, core-hole lifetime, Lorentzian vs Gaussian
- Chemical shift of edges as a *principle* (oxidation/local potential)
- White lines; density of empty states (qualitative)
- Shake-up / shake-off; multi-electron excitations
- XPS / photoelectric equation as kinematics (`E_k = hν − E_b − φ`)
- EXAFS / NEXAFS oscillations as interference of outgoing/backscattered
  photoelectron waves — physics first, “XAS experiment” second
- Resonant inelastic (intermediate-state) scattering as energy conservation
  plus core excitation, not as a beamline product

### Propagation and wave optics

- Vacuum vs air / window / filter attenuation
- Complex refractive index `n = 1 − δ − iβ`; dispersion; Kramers–Kronig
- Total external reflection; critical angle; evanescent field; X-ray standing waves
- Fresnel reflectivity / transmission; multilayer interference (Bragg in 1D)
- Snell’s law at X-ray n≈1; Goos–Hänchen as optional depth
- Coherence: spatial, temporal, lengths/times, degeneracy parameter
- Fresnel vs Fraunhofer; phase contrast; Talbot–Lau
- Speckle as a coherence signature
- Beam hardening, filtration, and spectral shaping as transport physics
- Zone-plate / CRL / KB focusing as *diffraction or reflection optics
  principles*, not as a shopping list of ALS optics

### Interaction channels with matter

- Photoelectric absorption (edge structure, jump ratio, Z⁴–Z⁵ scaling)
- Fluorescence and Auger / Coster–Kronig competition; fluorescence yield ω
- Elastic: Thomson, Rayleigh, atomic form factor; polarization factor
- Anomalous scattering factors f′, f″; resonant elastic scattering
- Magnetic scattering (non-resonant spin/orbital; resonant XMCD/XMLD as
  polarization × spin–orbit — physics, not a magnetometry product)
- Inelastic: Compton kinematics, Klein–Nishina, Compton wavelength, Compton profile
- Raman / Compton–Raman; resonant Raman near an edge
- Pair production (1.022 MeV) and triplet production
- Photonuclear reactions at high energy (principle-level)
- Multiple scattering; optical theorem
- Mean free path, μ/ρ, μ_en/ρ
- Beer–Lambert; optical depth; transmission; albedo
- Photoelectrons, secondaries, cascades; thermal spikes
- Radiation damage (radiolysis, knock-on, heating, charging, Coulomb explosion)
- Plasma opacity: bound–free, free–free, line blanketing (lab and cosmic)

### Reciprocal space and scattering principles

- Bragg’s law; Laue conditions; Bragg vs Laue *geometry*
- Momentum transfer **q**; Ewald sphere
- Form factor vs structure factor; Patterson idea (principle)
- Kinematical vs dynamical diffraction; Pendellösung; extinction
- Coherent vs incoherent; elastic vs inelastic
- Small-angle vs wide-angle as *q-range*, not as brand names
- Thermal diffuse scattering; Debye–Waller
- Powder vs single-crystal vs amorphous (how disorder enters the pattern)

### Detection and counting (physics, not a detector inventory)

- Photoelectric conversion in a sensor as the same PE channel
- Charge cloud, escape peaks, pile-up as interaction + statistics
- Quantum efficiency / DQE as definitions
- Energy-resolving vs integrating as information limits
- No catalog of AXIS-SXR-40, Pilatus models, or beamline serial numbers

### Transport, dose, and safety physics

- Fluence, flux, intensity, brilliance (definitions and units)
- Inverse-square and extended-source geometry
- Kerma, absorbed dose, equivalent / effective dose; quality factor; LET; RBE
- Charged-particle equilibrium; Bragg–Gray idea (principle)
- Buildup factor; half-value / tenth-value layer
- Shielding (PE- vs Compton-dominated; K-fluorescence from high-Z shields)
- Skyshine / room scatter as transport, not a floor-plan
- ALARA; time / distance / shielding
- Activation (photonuclear) as a high-energy limit
- Biological effect only at the interaction/dose-physics level

### Cosmic X-ray physics (mechanisms, not catalogs)

- Synchrotron cooling and cooling breaks in relativistic plasmas
- Inverse Compton / Comptonization of seed photons; Kompaneets idea
- Thermal bremsstrahlung in hot gas (clusters, SNR interiors)
- Photoionization and fluorescent lines in AGN / ISM (Fe K, etc. as atomic physics)
- Accretion-disk thermal X-rays; corona; boundary layer
- Relativistic beaming and Doppler boosting
- Compton-thick vs Compton-thin as optical-depth regimes
- Cosmic X-ray background as an integrated process
- Redshift of X-ray lines as atomic physics plus cosmology

### What is *not* a principle (still out)

Techniques (XRD, SAXS, XAS, RIXS, RSoXS, radiography) are linked *from*
these principles. A paper that only reports a materials result, a source
catalog, a CT protocol, or a motor list does not belong here even if it
“used X-rays.”

### What a good node looks like

A node is a **named principle or quantity** with a one-sentence definition,
typical energy/Z regime if any, and edges to the channels it involves.
Examples: `Klein–Nishina cross section`, `critical angle`, `fluorescence
yield`, `Debye–Waller factor`, `SASE`. Bad nodes: `P3HT:PCBM 2020 paper`,
`ESAF-2026-00041`, `the Pilatus on 11.0.1.2`.

---

## Phase 0 — LinkML schema (do this first)

Schema before harvest. Extraction quality depends on the class/slot list.

### Files

Follow the RSoXS split:

| File | Purpose |
|---|---|
| `storage/schema/xray_schema.yaml` | Overlay on `matkg_schema.yaml` (viewer / KG classes) |
| `storage/schema/xray_schema_extract.yaml` | Thin extract overlay (term extractor only) |
| `storage/schema/prov_vocabulary.yaml` | Shared PROV-O reference — **do not** re-embed `prov_vocabulary:` in the schema (LinkML rejects unknown top-level keys) |

IDs:

```yaml
id: https://w3id.org/matkg/xray
name: xray
title: General X-ray fundamentals overlay
version: xray_v1
imports:
  - matkg_schema
```

Reuse `matkg:` prefix. New class names must not collide with `RSoXSMeasurement`
or bl1101 ops types (`Motor`, `ProcessVariable`, `ESAF`, …).

### Design rules

1. Import `matkg_schema`; do not fork it.
2. Prefer `is_a` on existing classes: `ExperimentalTechnique`, `Phenomenon`,
   `Process`, `Property`, `Parameter`, `Measurement`.
3. Slots that already exist on `RSoXSMeasurement` (`photon_energy_eV`,
   `absorption_edge`, `polarization`, `scattering_technique`) stay **general**
   here. RSoXS remains a *specialization*, not the owner of “photon energy.”
4. No sample/device classes (`ConjugatedPolymer`, `PhotovoltaicCell`, `OPV`).
   Those belong in the literature KG. Generic material-interaction concepts
   (Z-dependence, attenuation length, mass energy-absorption) **are** in scope.
5. No hardware inventory (`Motor`, `OphydDevice`, `ESAF`). Those belong in ops.
   Safety *controls* (interlock, shutter, shielding) are concepts, not a
   beamline asset list.
6. **Principles are instances.** Do not add a class per law (no
   `BraggsLaw` class). Add a class only when slots/relations truly differ.
   Target ~16–20 classes and ~30–45 slots; the inventory above is the
   *content* of the graph, not the class list.
7. Inclusive of X-ray physics wherever it shows up (lab, safety, cosmos).
   Exclusive of *application results* and *facility inventories*.
8. Validate with LinkML (`SchemaDefinition` load) in CI / `tests/test_xray_schema.py`
   so unknown top-level keys fail fast.

### Proposed classes

| Class | `is_a` | What it captures |
|---|---|---|
| `XRayPhenomenon` | `Phenomenon` | Photoelectric, Compton, Rayleigh/Thomson, pair production, fluorescence, Auger, refraction, resonance |
| `XRaySource` | `Process` | Tube, BM, undulator, wiggler, FEL, plasma; cosmic synchrotron / accretion / IC |
| `XRayBeamProperty` | `Property` | E, λ, k, brilliance, coherence, polarization, bandwidth, fluence, flux |
| `XRayOpticalConstant` | `Property` | δ, β, critical angle, HVL, attenuation / energy-absorption coefficients |
| `AbsorptionEdge` | `Parameter` | Named edges and shells (K, L2, L3, M) — not a paper’s measured eV |
| `SecondaryProcess` | `Phenomenon` | Fluorescence yield, Auger, photoelectrons, cascades, thermal spikes |
| `MaterialXRayInteraction` | `Phenomenon` | Z-dependence, mean free path, damage mechanisms, optical depth |
| `XRaySafetyConcept` | `Phenomenon` | Dose quantities, LET/RBE, ALARA, shielding, buildup, scatter |
| `CosmicXRayProcess` | `Phenomenon` | Accretion, IC, synchrotron cooling, CXB, cluster bremsstrahlung |
| `ScatteringGeometry` | `ExperimentalTechnique` | Transmission, GI, reflection; small- vs wide-angle as q-range |
| `XRayTechniqueFamily` | `ExperimentalTechnique` | XRD, SAXS, XAS, XANES, EXAFS, RIXS, RSoXS — linked *to* principles |
| `InteractionCrossSection` | `Property` | Channel cross-sections; Klein–Nishina; form factor; f′, f″ |
| `ReciprocalSpaceConcept` | `Parameter` | q, Bragg, Laue, Ewald, form/structure factor, dynamical vs kinematical |
| `WaveOpticsConcept` | `Parameter` | Fresnel/Fraunhofer, coherence, phase contrast, Talbot, standing waves |
| `AtomicTransition` | `Parameter` | Selection rules, linewidth, chemical shift, shake-up, XPS kinematics |
| `DetectionPrinciple` | `Phenomenon` | PE conversion, Poisson statistics, QE/DQE, escape/pile-up (no asset list) |

`RSoXSMeasurement` stays in `rsoxs_schema.yaml`. The xray KG may have one
`XRayTechniqueFamily` instance named “RSoXS” that *points at* the idea, not
at 200 OPV measurements.

### Proposed slots (xray-only)

Examples — refine during schema drafting. Prefer definitional / typical
values, not a single experiment’s datum.

- `typical_energy_range_eV`; `energy_band` (soft / tender / hard)
- `wavelength_nm` / `wavelength_angstrom`; `wavevector`
- `dispersion_delta` / `absorption_beta` (δ, β)
- `critical_angle`; `refractive_index_form`
- `brilliance`; `fluence`; `flux`
- `coherence_type`; `coherence_length`
- `polarization_state` (alias-safe with existing `polarization`)
- `edge_name`, `element_symbol`, `shell` (K, L2, L3, M)
- `fluorescence_yield`; `auger_branching`
- `interaction_channel` (photoelectric, Compton, Rayleigh, pair, photonuclear)
- `atomic_number_dependence` (e.g. `Z^n`)
- `cross_section_form` (Klein–Nishina, Thomson, …)
- `anomalous_correction` (f′, f″)
- `attenuation_length`; `mass_attenuation_coefficient`; `optical_depth`
- `mean_free_path`; `damage_mechanism`
- `dose_quantity`; `let`; `quality_factor`; `hvl`
- `shielding_material`; `safety_control`
- `cosmic_mechanism`
- `geometry`; `q_definition`; `diffraction_limit` (kinematical / dynamical)
- `selection_rule`; `linewidth`; `chemical_shift_principle`
- `polarization_factor`; `magnetic_channel` (spin / orbital / XMCD)
- `photon_statistic` (Poisson, bunching)
- `technique_family` (how a method uses the principle — secondary)

Avoid numeric “this paper measured 283.5 eV on P3HT.” That is literature KG.

### Relations

Reuse matkg associations where possible. Add only if needed:

- `XRaySource` —produces→ `XRayBeamProperty` / `XRayPhenomenon`
- `XRayPhenomenon` —described_by→ `InteractionCrossSection` / `XRayOpticalConstant`
- `MaterialXRayInteraction` —involves→ `XRayPhenomenon` / `SecondaryProcess`
- `AbsorptionEdge` —governs→ photoelectric / resonant `XRayPhenomenon`
- `AtomicTransition` —governs→ `AbsorptionEdge` / `SecondaryProcess`
- `WaveOpticsConcept` —governs→ propagation / `ScatteringGeometry`
- `DetectionPrinciple` —uses→ `XRayPhenomenon` (same PE channel)
- `ReciprocalSpaceConcept` —governs→ `XRayTechniqueFamily` (not the reverse)
- `XRayTechniqueFamily` —uses→ `XRayPhenomenon` (technique is downstream)
- `XRaySafetyConcept` —follows_from→ `MaterialXRayInteraction` (dose from energy deposition)
- `CosmicXRayProcess` —same_physics_as→ lab `XRayPhenomenon` (one Compton node)

No `hasDetector` / `hasMotor` edges into ops hardware.

### Schema tests (phase 0 exit)

- [x] Both YAML files load with LinkML (`SchemaView` / `SchemaDefinition`)
- [x] No `prov_vocabulary:` top-level key
- [x] `RSoXSMeasurement` is **not** redefined here
- [x] Extract schema imports the overlay and stays extract-safe (no extra
      top-level keys the extractor does not expect)
- [x] Short unit test listing required classes/slots (same style as
      `tests/test_prov.py` after the vocabulary split)
- [x] Schema includes `MaterialXRayInteraction`, `XRaySafetyConcept`,
      `CosmicXRayProcess`, `SecondaryProcess`, `XRayOpticalConstant`,
      `WaveOpticsConcept`, `AtomicTransition`, and `DetectionPrinciple`
      (or equivalent names)
- [x] Extractor seed / example instances cover production, atomic
      spectroscopy, channels, optics, q-space, dose, detection statistics,
      and cosmic mechanisms — not only technique families

---

## Phase 1 — Harvest design (no download until schema is accepted)

### Layout

```
papers/xray/{year}/{doi_or_arxiv}.pdf
papers/xray/manifest.json
```

Never write into `papers/rsoxs/`. Dedup by DOI/arXiv against the RSoXS
manifest so the same PDF is not ingested twice.

### Classifier (tier A/B only)

**Keep** when the paper *teaches a principle* from the inventory above
(even if the venue is astronomy, health physics, or a methods review):

- Photons / kinematics / polarization / photon statistics
- Production: bremsstrahlung, lines, synchrotron/undulator, inverse Compton,
  thermal continua, FEL, transition/channeling/betatron (if taught as physics)
- Atomic spectroscopy: selection rules, linewidth, chemical shift, XPS
  kinematics, EXAFS-as-interference
- Propagation: δ/β, critical angle, standing waves, coherence, Fresnel, beam
  hardening
- Channels: PE, Compton/Klein–Nishina/profile, Rayleigh/Thomson, pair,
  fluorescence/Auger, f′/f″, magnetic/dichroic, damage, plasma opacity
- Reciprocal space: Bragg/Laue, q, form/structure factor, dynamical vs
  kinematical, Debye–Waller
- Detection *physics* (QE, Poisson, escape) — not detector shopping
- Dose / safety physics: kerma, HVL, buildup, LET, ALARA, shielding regimes
- Cosmic mechanisms that *are* those same processes
- Technique reviews **only if** they explain the underlying physics (a SAXS
  tutorial that derives q is in; a SAXS polymer result is out)

**Drop** (tier C):

- Application papers whose abstract is a material result (“P3HT:PCBM
  morphology…”)
- Diagnostic imaging *practice* (CT protocols, radiography workflows, patient
  positioning) — keep only if it is really radiation-physics / dose physics
- Astronomy *catalogs* or source surveys with no mechanism discussion
- Single-sample lab XRD (“we measured this steel”)
- ALS 11.0.1.2 ops notes (ops KG)

### Seed queries (quoted; no unquoted hyphens)

Bias toward textbooks, reviews, and methods-physics papers. Include
health-physics and high-energy-astro *mechanism* papers. Do not stop at
synchrotron-technique reviews.

**Kinematics / production**

- `"bremsstrahlung" X-ray`
- `"characteristic radiation" X-ray` OR `"Moseley's law"`
- `"undulator radiation"` OR `"undulator parameter"`
- `"synchrotron radiation" brilliance`
- `"inverse Compton" X-ray`
- `"X-ray free-electron laser" SASE` (principle reviews)
- `"transition radiation" X-ray` OR `"channeling radiation"`

**Atomic / spectroscopy**

- `"dipole approximation" X-ray` OR `"selection rule" X-ray`
- `"core-hole lifetime"` OR `"natural linewidth" X-ray`
- `"photoelectric equation"` OR `"Einstein photoelectric" X-ray`
- `"EXAFS" interference` OR `"photoelectron wave" X-ray`
- `"anomalous scattering factor"` already under channels

**Channels / matter**

- `"photoelectric absorption" X-ray` OR `"photoelectric effect" X-ray`
- `"X-ray absorption edge"` OR `"fluorescence yield"`
- `"Auger" X-ray`
- `"Compton scattering" X-ray` OR `"Klein-Nishina"`
- `"Thomson scattering" X-ray` OR `"Rayleigh scattering" X-ray`
- `"pair production" X-ray`
- `"anomalous scattering factor"` OR `"dispersive correction" X-ray`
- `"mass attenuation coefficient" X-ray`
- `"radiation damage" X-ray` mechanism OR radiolysis

**Optics / q-space**

- `"X-ray refractive index"` OR `"critical angle" X-ray`
- `"Kramers-Kronig" X-ray`
- `"coherence" "X-ray" review`
- `"Bragg's law"` X-ray
- `"reciprocal space" X-ray scattering`
- `"dynamical diffraction" X-ray` OR `"Pendellösung"`
- `"Debye-Waller" X-ray`
- `"X-ray standing wave"`
- `"grazing-incidence" X-ray scattering review`
- `"Thomson polarization factor"` OR `"X-ray magnetic scattering"`

**Detection physics**

- `"detective quantum efficiency" X-ray` OR `"photon counting statistics"`
- `"escape peak" X-ray` (as PE + fluorescence, not a detector manual)

**Dose / safety physics**

- `"radiation protection" X-ray` OR `"ALARA" X-ray`
- `"half-value layer" X-ray`
- `"X-ray shielding"` OR `"buildup factor" X-ray`
- `"linear energy transfer" X-ray` OR `"quality factor" radiation`

**Cosmic mechanisms**

- `"cosmic X-ray background"`
- `"Comptonization"` OR `"inverse Compton" accretion`
- `"thermal bremsstrahlung" cluster` X-ray
- `"Compton thick"` OR `"photoionization" "Fe K"` AGN
- `"relativistic beaming" X-ray`
- `"X-ray binary" accretion` (physics, not a source catalog)

Facility names (ALS, APS, ESRF) are **metadata**, not a relevance boost.

### Script

New `scripts/harvest_xray.py` (do not clone all of `harvest_rsoxs.py` blindly).
Share download helpers from `download_pdfs.py`. Own queries + `classify_xray()`.

First live run, when approved: `--max-pdfs 40` and inspect the manifest
before any extract.

---

## Phase 2 — Extract and KG snapshot

- `scripts/run.py --pdf-dir papers/xray --schema storage/schema/xray_schema_extract.yaml`
  `--output storage/terminology/extracted_terms_xray_v1.json`
- Promote to `storage/kg/matkg_xray_v1.json` (`nodes`/`edges`, plus
  `things`/`associations` compatibility already in the loader)
- Graph id: `xray_v1` via `graph_id_for_path` (same versioned pattern as
  `rsoxs_vN`, **not** collapsed to a single alias)
- `is_science_graph_id()` should treat `xray` / `xray_v*` as science
- Default chat selection stays rsoxs + ops; xray is **opt-in** until quality
  is reviewed (same caution as the x-ray demo KG)

---

## Phase 3 — Retrieval / chat

- Intent: conceptual “what is / how does / explain / derive / why” plus
  principle keywords (photon, bremsstrahlung, Compton, photoelectric,
  refractive index, Bragg, q-space, coherence, attenuation, ALARA, HVL,
  inverse Compton, accretion, fluorescence yield, f-prime, Debye–Waller,
  Klein–Nishina, XPS, Pendellösung, …)
  → prioritize `xray_v*`
- Technique-only questions (“how do I run GIWAXS at 11.0.1.2”) stay ops /
  literature. “Why does GIWAXS need a critical angle?” is this KG.
- Do **not** steal RSoXS materials questions or ops layout questions
- Citations: `[KG: xray_v1: absorption edge]` so the UI can zoom that graph
- Materials Project stays for crystalline formulas; this KG does not replace it

---

## Phase 4 — Docs and ops

- Document corpus root, schema files, and “do not merge with rsoxs/ops”
- MkDocs page under harvest / KG sources
- Extraction is a **separate** process from the agent (same isolation note as
  RSoXS `scripts/run.py`)

---

## Out of scope

- Starting harvest or extract in this draft
- Merging into `matkg_rsoxs_v3` or `matkg_bl1101_v9`
- Tiled / ESAF identities
- Medical *practice* corpora (CT protocols) and astronomy *catalogs*
  (safety physics and cosmic X-ray *mechanisms* are in scope)
- Splitting `api.py` or other unrelated refactors

---

## Suggested order of work

1. Draft `xray_schema.yaml` + `xray_schema_extract.yaml` and LinkML tests **(done)**
2. Review the principles inventory against a handful of canonical texts
   (e.g. Als-Nielsen & McMorrow, Attwood, Jackson excerpts, ICRP/NCRP
   primers, Rybicki & Lightman / Longair high-energy astro) — titles only,
   no harvest yet. Add missing *principles*, not missing *techniques*.
3. Write `classify_xray()` + harvest script **(done; do not run until asked)**
4. `--max-pdfs 40` OA harvest into `papers/xray/`
5. Extract → `matkg_xray_v1.json`
6. Opt-in in Settings; then intent routing
7. Only then consider making it a default selected science KG

---

## Open decisions

- Prefix instances `xray:` vs reuse `matkg:` (recommend `xray:` for new ids)
- Soft vs tender vs hard as enum vs free string
- Whether one `XRayTechniqueFamily` node for RSoXS is enough, or a
  `sameAs` / see-also link to the literature KG technique node
- How aggressive the harvest gate is (reviews-only vs include primary
  methods, safety handbooks, and astro-physics reviews)
- Whether safety instances cite regulatory names (NCRP, ICRP) as
  `Publication` nodes or stay purely conceptual
- How tightly cosmic X-ray nodes link to lab `XRayPhenomenon` (same Compton
  node vs parallel “astro Compton” instance)
- Whether Mössbauer / nuclear resonant scattering sits here (γ-adjacent)
  or is left out as a different spectral band
- How much plasma opacity / atomic codes (e.g. principle of bound–free
  tables) to instantiate vs cite as `Publication`
