export type NodeType = 'material' | 'property' | 'application' | 'process' | 'compound';

export interface GraphNode {
  id: string;
  label: string;
  type: NodeType;
  description: string;
  x: number;
  y: number;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export const NODE_COLORS: Record<NodeType, string> = {
  material: '#60a5fa',
  property: '#34d399',
  application: '#fbbf24',
  process: '#a78bfa',
  compound: '#f87171',
};

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  material: 'Material',
  property: 'Property',
  application: 'Application',
  process: 'Process',
  compound: 'Compound',
};

// Pre-positioned for a clean visual layout (900×640 canvas space)
export const GRAPH_NODES: GraphNode[] = [
  // Materials — left arc
  { id: 'steel',        label: 'Steel',        type: 'material',    description: 'Iron-carbon alloy (0.02–2.14 wt% C). Most widely used structural metal. UTS 400–2500 MPa depending on alloy and heat treatment.', x: 180, y: 200 },
  { id: 'aluminum',     label: 'Aluminum',     type: 'material',    description: 'Lightweight FCC metal, density 2.7 g/cm³. Excellent thermal conductivity (205 W/m·K) and corrosion resistance via Al₂O₃ passive layer.', x: 120, y: 320 },
  { id: 'titanium',     label: 'Titanium',     type: 'material',    description: 'HCP→BCC allotropic metal, density 4.5 g/cm³. Exceptional specific strength and biocompatibility. Passive TiO₂ layer resists corrosion.', x: 165, y: 445 },
  { id: 'carbon_fiber', label: 'Carbon Fiber', type: 'material',    description: 'Continuous carbon filaments, tensile strength up to 7 GPa, modulus up to 900 GPa. Density only 1.6–1.9 g/cm³. Produced via CVD/pyrolysis.', x: 260, y: 540 },
  { id: 'graphene',     label: 'Graphene',     type: 'material',    description: '2D hexagonal carbon lattice. Carrier mobility ~200,000 cm²/V·s. Intrinsic tensile strength ~130 GPa. Grown via CVD on Cu or Ni foils.', x: 370, y: 590 },
  { id: 'silicon',      label: 'Silicon',      type: 'material',    description: 'Diamond-cubic semiconductor, bandgap 1.12 eV. Intrinsic carrier density 1.5×10¹⁰ cm⁻³ at 300 K. Foundation of modern microelectronics.', x: 490, y: 600 },
  { id: 'nickel',       label: 'Nickel Alloy', type: 'material',    description: 'FCC superalloy with outstanding high-temperature creep resistance. γ′ precipitate strengthening enables turbine blade use above 1000°C.', x: 195, y: 140 },
  { id: 'copper',       label: 'Copper',       type: 'material',    description: 'FCC metal. Highest electrical conductivity of common metals at 5.96×10⁷ S/m. Thermal conductivity 401 W/m·K. Used in 65% of all wiring.', x: 610, y: 590 },
  { id: 'tungsten',     label: 'Tungsten',     type: 'material',    description: 'BCC refractory metal. Highest melting point of all metals (3422°C). Vickers hardness ~3430 MPa. Density 19.3 g/cm³.', x: 100, y: 200 },
  // Properties — center
  { id: 'tensile',      label: 'Tensile Strength',     type: 'property',    description: 'Maximum engineering stress a material sustains before fracture (MPa or GPa). Determined by universal testing machine at controlled strain rate.', x: 430, y: 200 },
  { id: 'hardness',     label: 'Hardness',             type: 'property',    description: 'Resistance to permanent surface deformation. Scales: Vickers (HV), Rockwell (HRC), Brinell (HB). Empirically correlated to UTS for metals.', x: 340, y: 300 },
  { id: 'thermal_cond', label: 'Thermal Conductivity', type: 'property',    description: 'Heat flux per unit temperature gradient (W/m·K). Mediated by phonons in ceramics/polymers and free electrons in metals.', x: 530, y: 310 },
  { id: 'elec_cond',    label: 'Elec. Conductivity',   type: 'property',    description: 'Current density per electric field (S/m). Reciprocal of resistivity ρ. Strongly temperature-dependent; decreases with temperature in metals.', x: 630, y: 410 },
  { id: 'corrosion',    label: 'Corrosion Resistance', type: 'property',    description: 'Ability to maintain chemical integrity in oxidizing/reducing environments. Quantified by corrosion rate (mm/year) or polarization curves.', x: 310, y: 430 },
  { id: 'density',      label: 'Density',              type: 'property',    description: 'Mass per unit volume (g/cm³). Critical for specific strength (σ/ρ) and specific stiffness (E/ρ) calculations in weight-critical design.', x: 430, y: 470 },
  { id: 'modulus',      label: 'Elastic Modulus',      type: 'property',    description: "Young's modulus (GPa): ratio of stress to strain in the elastic regime. Materials-specific constant independent of geometry. Steel: 200 GPa, CF: 230–900 GPa.", x: 540, y: 200 },
  // Applications — right arc
  { id: 'aerospace',    label: 'Aerospace',    type: 'application', description: 'Airframes, fuselages, turbine engines, rocket structures. Requires high specific strength, fatigue resistance, and extreme temperature performance.', x: 750, y: 170 },
  { id: 'automotive',   label: 'Automotive',   type: 'application', description: 'Body-in-white, chassis, powertrains, EV battery enclosures. Industry trend toward lightweighting with AHSS, aluminum, and CFRP composites.', x: 820, y: 290 },
  { id: 'electronics',  label: 'Electronics',  type: 'application', description: 'ICs, PCBs, MEMS, photovoltaics, power devices. Requires precise electrical, thermal, and dimensional control at nanometer scale.', x: 800, y: 420 },
  { id: 'biomedical',   label: 'Biomedical',   type: 'application', description: 'Orthopedic implants, dental prosthetics, cardiovascular stents. Requires biocompatibility, corrosion resistance, and osseointegration capability.', x: 740, y: 530 },
  { id: 'construction', label: 'Construction', type: 'application', description: 'Structural frames, rebar-reinforced concrete, bridges, curtain walls. Driven by strength, long-term durability, and lifecycle cost.', x: 650, y: 200 },
  // Processes — top
  { id: 'heat_treat',   label: 'Heat Treatment', type: 'process',   description: 'Controlled heating/cooling cycles (annealing, quenching, tempering, aging) to manipulate microstructure, phase distribution, and grain size.', x: 390, y: 105 },
  { id: 'sintering',    label: 'Sintering',      type: 'process',   description: 'Consolidation of powder compacts below melting point via solid-state atomic diffusion. Used for ceramics, WC-Co cermets, and PM Ti/W parts.', x: 540, y: 105 },
  { id: 'cvd',          label: 'CVD',            type: 'process',   description: 'Chemical Vapor Deposition: gas-phase precursor decomposition onto a heated substrate. Enables epitaxial Si, graphene synthesis, SiC, and DLC films.', x: 670, y: 105 },
  { id: 'forging',      label: 'Forging',        type: 'process',   description: 'Compressive hot/cold working to refine grain structure and close porosity. Significantly improves fatigue life and impact toughness over castings.', x: 270, y: 115 },
  // Compounds — bottom
  { id: 'al2o3',        label: 'Al₂O₃',          type: 'compound',  description: 'Corundum-structure alumina. Hardness 9 Mohs, Tm = 2072°C. Used as abrasive, ceramic substrate, TBC bond coat, and cutting tool insert.', x: 200, y: 590 },
  { id: 'sic',          label: 'SiC',            type: 'compound',  description: 'Silicon carbide: covalent ceramic, hardness ~9.5 Mohs. Wide bandgap (3.3 eV, 4H-SiC) and exceptional thermal conductivity (120–490 W/m·K).', x: 730, y: 590 },
  { id: 'fe3c',         label: 'Fe₃C',           type: 'compound',  description: 'Cementite: orthorhombic iron carbide, ~800 HV, inherently brittle. Morphology (lamellar pearlite vs. spheroidized) governs steel hardness and toughness.', x: 310, y: 185 },
  { id: 'tio2',         label: 'TiO₂',           type: 'compound',  description: 'Titanium dioxide: rutile or anatase polymorph. Bandgap ~3.0–3.2 eV. Functions as photocatalyst, white pigment, and passive corrosion barrier on Ti.', x: 290, y: 595 },
];

export const GRAPH_EDGES: GraphEdge[] = [
  { source: 'steel',        target: 'tensile' },
  { source: 'steel',        target: 'hardness' },
  { source: 'steel',        target: 'modulus' },
  { source: 'steel',        target: 'construction' },
  { source: 'steel',        target: 'automotive' },
  { source: 'steel',        target: 'heat_treat' },
  { source: 'steel',        target: 'fe3c' },
  { source: 'steel',        target: 'forging' },
  { source: 'aluminum',     target: 'density' },
  { source: 'aluminum',     target: 'corrosion' },
  { source: 'aluminum',     target: 'thermal_cond' },
  { source: 'aluminum',     target: 'aerospace' },
  { source: 'aluminum',     target: 'automotive' },
  { source: 'aluminum',     target: 'al2o3' },
  { source: 'aluminum',     target: 'forging' },
  { source: 'titanium',     target: 'tensile' },
  { source: 'titanium',     target: 'corrosion' },
  { source: 'titanium',     target: 'density' },
  { source: 'titanium',     target: 'aerospace' },
  { source: 'titanium',     target: 'biomedical' },
  { source: 'titanium',     target: 'tio2' },
  { source: 'titanium',     target: 'sintering' },
  { source: 'carbon_fiber', target: 'tensile' },
  { source: 'carbon_fiber', target: 'density' },
  { source: 'carbon_fiber', target: 'modulus' },
  { source: 'carbon_fiber', target: 'aerospace' },
  { source: 'carbon_fiber', target: 'automotive' },
  { source: 'carbon_fiber', target: 'cvd' },
  { source: 'silicon',      target: 'elec_cond' },
  { source: 'silicon',      target: 'electronics' },
  { source: 'silicon',      target: 'cvd' },
  { source: 'silicon',      target: 'sic' },
  { source: 'graphene',     target: 'elec_cond' },
  { source: 'graphene',     target: 'tensile' },
  { source: 'graphene',     target: 'thermal_cond' },
  { source: 'graphene',     target: 'electronics' },
  { source: 'graphene',     target: 'cvd' },
  { source: 'nickel',       target: 'tensile' },
  { source: 'nickel',       target: 'corrosion' },
  { source: 'nickel',       target: 'thermal_cond' },
  { source: 'nickel',       target: 'aerospace' },
  { source: 'nickel',       target: 'heat_treat' },
  { source: 'copper',       target: 'elec_cond' },
  { source: 'copper',       target: 'thermal_cond' },
  { source: 'copper',       target: 'electronics' },
  { source: 'tungsten',     target: 'hardness' },
  { source: 'tungsten',     target: 'density' },
  { source: 'tungsten',     target: 'tensile' },
  { source: 'tungsten',     target: 'sintering' },
  { source: 'al2o3',        target: 'hardness' },
  { source: 'al2o3',        target: 'corrosion' },
  { source: 'sic',          target: 'hardness' },
  { source: 'sic',          target: 'thermal_cond' },
  { source: 'sic',          target: 'electronics' },
  { source: 'fe3c',         target: 'hardness' },
  { source: 'tio2',         target: 'corrosion' },
  { source: 'heat_treat',   target: 'hardness' },
  { source: 'heat_treat',   target: 'tensile' },
  { source: 'sintering',    target: 'density' },
  { source: 'forging',      target: 'tensile' },
  { source: 'cvd',          target: 'elec_cond' },
];

export interface ExampleQuery {
  id: string;
  question: string;
  answer: string;
  nodeIds: string[];
  confidence: number;
}

export const EXAMPLE_QUERIES: ExampleQuery[] = [
  {
    id: 'q1',
    question: 'What are the best lightweight materials for aerospace?',
    answer: '**Carbon Fiber** composites lead aerospace applications with the highest specific strength (tensile up to 7 GPa at 1.8 g/cm³). **Titanium** alloys balance strength, corrosion resistance, and moderate density (4.5 g/cm³) for airframes and compressor stages. **Aluminum** remains dominant in secondary structures for its machinability and cost. **Nickel superalloys** are essential for turbine hot-section blades, operating above 1000°C where other materials fail.',
    nodeIds: ['carbon_fiber', 'titanium', 'aluminum', 'nickel', 'aerospace', 'density', 'tensile', 'corrosion', 'cvd', 'heat_treat', 'thermal_cond'],
    confidence: 0.94,
  },
  {
    id: 'q2',
    question: 'How does heat treatment affect steel hardness?',
    answer: '**Heat treatment** controls steel hardness by manipulating the iron-carbon phase diagram. Rapid quenching traps carbon in a supersaturated body-centered tetragonal lattice, forming **martensite** — the hardest steel microstructure (up to ~900 HV). Subsequent tempering reduces brittleness by allowing controlled carbide precipitation. The **Fe₃C** (cementite) phase distribution dictates the hardness ceiling: higher carbon content enables greater hardness but reduces toughness.',
    nodeIds: ['steel', 'heat_treat', 'hardness', 'fe3c', 'tensile', 'forging'],
    confidence: 0.97,
  },
  {
    id: 'q3',
    question: 'Compare electrical conductivity: copper vs graphene',
    answer: '**Copper** is the industry standard at 5.96×10⁷ S/m, used in virtually all electrical wiring. **Graphene** has intrinsic carrier mobility of ~200,000 cm²/V·s — theoretically exceeding copper — but scalable deposition via **CVD** still yields polycrystalline films with grain boundaries that limit conductivity to ~10⁶ S/m in practice. **Silicon** is a semiconductor (not conductor), its conductivity tunable via doping from 10⁻³ to 10³ S/m, enabling transistors.',
    nodeIds: ['copper', 'graphene', 'silicon', 'elec_cond', 'electronics', 'cvd'],
    confidence: 0.91,
  },
];
