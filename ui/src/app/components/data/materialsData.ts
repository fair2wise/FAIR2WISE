export type NodeType = 'material' | 'property' | 'application' | 'process' | 'compound';

export interface GraphNode {
  id: string;
  label: string;
  type: NodeType;
  description: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
}

export const NODE_COLORS: Record<NodeType, string> = {
  material: '#3b82f6',
  property: '#10b981',
  application: '#f59e0b',
  process: '#8b5cf6',
  compound: '#ef4444',
};

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  material: 'Material',
  property: 'Property',
  application: 'Application',
  process: 'Process',
  compound: 'Compound',
};

export const GRAPH_NODES: GraphNode[] = [
  // Materials
  { id: 'steel', label: 'Steel', type: 'material', description: 'Iron-carbon alloy (0.02–2.14 wt% C) with excellent strength, ductility, and toughness. Most widely used structural metal.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'aluminum', label: 'Aluminum', type: 'material', description: 'Lightweight FCC metal (2.7 g/cm³) with good corrosion resistance and high thermal conductivity (205 W/m·K).', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'titanium', label: 'Titanium', type: 'material', description: 'HCP→BCC allotropic metal with exceptional specific strength and biocompatibility. Density: 4.5 g/cm³.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'carbon_fiber', label: 'Carbon Fiber', type: 'material', description: 'Continuous carbon filaments with tensile strength up to 7 GPa and modulus up to 900 GPa. Density ~1.6–1.9 g/cm³.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'silicon', label: 'Silicon', type: 'material', description: 'Diamond-cubic semiconductor (Eg = 1.12 eV). Intrinsic carrier density 1.5×10¹⁰ cm⁻³ at 300 K.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'graphene', label: 'Graphene', type: 'material', description: '2D hexagonal carbon lattice. Carrier mobility ~200,000 cm²/V·s. Intrinsic tensile strength ~130 GPa.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'nickel', label: 'Nickel Alloy', type: 'material', description: 'FCC superalloy with outstanding high-temperature creep resistance. Turbine blades operate up to 1100°C.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'copper', label: 'Copper', type: 'material', description: 'FCC metal with highest electrical conductivity (5.96×10⁷ S/m) among common metals. Thermal conductivity: 401 W/m·K.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'tungsten', label: 'Tungsten', type: 'material', description: 'BCC refractory metal with highest melting point (3422°C) of all metals. Vickers hardness ~3430 MPa.', x: 0, y: 0, vx: 0, vy: 0 },
  // Properties
  { id: 'tensile_strength', label: 'Tensile Strength', type: 'property', description: 'Maximum engineering stress before fracture (MPa or GPa). Critical parameter for structural design and safety factors.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'hardness', label: 'Hardness', type: 'property', description: 'Resistance to permanent deformation. Scales: Vickers (HV), Rockwell (HRC), Brinell (HB). Correlates to tensile strength.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'thermal_conductivity', label: 'Thermal Conductivity', type: 'property', description: 'Heat flux per unit temperature gradient (W/m·K). Governed by phonon and electron transport mechanisms.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'electrical_conductivity', label: 'Electrical Conductivity', type: 'property', description: 'Current density per electric field (S/m). Inversely related to resistivity ρ. Strongly temperature-dependent.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'corrosion_resistance', label: 'Corrosion Resistance', type: 'property', description: 'Ability to maintain integrity in oxidizing/reducing environments. Quantified by corrosion rate (mm/year).', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'density', label: 'Density', type: 'property', description: 'Mass per unit volume (g/cm³). Critical for specific strength (σ/ρ) calculations. Ranges 1.6 (CF) to 19.3 (W) g/cm³.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'elastic_modulus', label: 'Elastic Modulus', type: 'property', description: "Young's modulus (GPa): stress-strain ratio in elastic regime. Ranges from ~0.1 GPa (rubber) to ~1000 GPa (diamond).", x: 0, y: 0, vx: 0, vy: 0 },
  // Applications
  { id: 'aerospace', label: 'Aerospace', type: 'application', description: 'Airframes, fuselages, turbine engines, rocket structures. Requires high specific strength, fatigue life, and thermal performance.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'automotive', label: 'Automotive', type: 'application', description: 'Body panels, chassis, powertrains, EV battery enclosures. Industry trend toward lightweighting with AHSS and composites.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'electronics', label: 'Electronics', type: 'application', description: 'Integrated circuits, PCBs, MEMS, photovoltaics. Requires precise electrical, thermal, and dimensional property control.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'biomedical', label: 'Biomedical', type: 'application', description: 'Orthopedic implants, dental prosthetics, cardiovascular stents. Requires biocompatibility, corrosion resistance, osseointegration.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'construction', label: 'Construction', type: 'application', description: 'Structural steel frames, rebar, aluminum curtain walls, bridges. Driven by strength, cost, and long-term durability.', x: 0, y: 0, vx: 0, vy: 0 },
  // Processes
  { id: 'heat_treatment', label: 'Heat Treatment', type: 'process', description: 'Annealing, quenching, tempering, age-hardening. Controls microstructure phases, grain size, and precipitate distribution.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'sintering', label: 'Sintering', type: 'process', description: 'Powder consolidation below melting point via atomic diffusion. Used for ceramics, WC-Co cermets, and PM Ti/W components.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'cvd', label: 'CVD', type: 'process', description: 'Chemical Vapor Deposition: gas-phase precursor decomposition on substrates. Enables epitaxial Si, graphene synthesis, DLC coatings.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'forging', label: 'Forging', type: 'process', description: 'Compressive hot/cold working to refine grain structure and eliminate porosity. Improves fatigue and impact properties significantly.', x: 0, y: 0, vx: 0, vy: 0 },
  // Compounds
  { id: 'al2o3', label: 'Al₂O₃', type: 'compound', description: 'Corundum-structure alumina. Hardness 9 Mohs, Tm = 2072°C. Used as abrasive, ceramic substrate, and thermal barrier coating.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'sic', label: 'SiC', type: 'compound', description: 'Silicon carbide: covalent ceramic. Hardness ~9.5 Mohs, Eg = 3.3 eV (4H-SiC). Thermal conductivity 120–490 W/m·K.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'fe3c', label: 'Fe₃C', type: 'compound', description: 'Cementite: orthorhombic iron carbide, ~800 HV, brittle. Controls steel hardness through pearlite and martensite microstructures.', x: 0, y: 0, vx: 0, vy: 0 },
  { id: 'tio2', label: 'TiO₂', type: 'compound', description: 'Titanium dioxide: rutile/anatase polymorphs. Eg ~3.0–3.2 eV. Photocatalyst, pigment, and passive corrosion barrier on Ti surfaces.', x: 0, y: 0, vx: 0, vy: 0 },
];

export const GRAPH_EDGES: GraphEdge[] = [
  // Steel
  { source: 'steel', target: 'tensile_strength', label: 'has' },
  { source: 'steel', target: 'hardness', label: 'has' },
  { source: 'steel', target: 'elastic_modulus', label: 'has' },
  { source: 'steel', target: 'construction', label: 'used_in' },
  { source: 'steel', target: 'automotive', label: 'used_in' },
  { source: 'steel', target: 'heat_treatment', label: 'processed_by' },
  { source: 'steel', target: 'fe3c', label: 'contains' },
  { source: 'steel', target: 'forging', label: 'processed_by' },
  // Aluminum
  { source: 'aluminum', target: 'density', label: 'has' },
  { source: 'aluminum', target: 'corrosion_resistance', label: 'has' },
  { source: 'aluminum', target: 'thermal_conductivity', label: 'has' },
  { source: 'aluminum', target: 'aerospace', label: 'used_in' },
  { source: 'aluminum', target: 'automotive', label: 'used_in' },
  { source: 'aluminum', target: 'al2o3', label: 'forms_oxide' },
  { source: 'aluminum', target: 'forging', label: 'processed_by' },
  // Titanium
  { source: 'titanium', target: 'tensile_strength', label: 'has' },
  { source: 'titanium', target: 'corrosion_resistance', label: 'has' },
  { source: 'titanium', target: 'density', label: 'has' },
  { source: 'titanium', target: 'aerospace', label: 'used_in' },
  { source: 'titanium', target: 'biomedical', label: 'used_in' },
  { source: 'titanium', target: 'tio2', label: 'forms_oxide' },
  { source: 'titanium', target: 'sintering', label: 'processed_by' },
  // Carbon Fiber
  { source: 'carbon_fiber', target: 'tensile_strength', label: 'has' },
  { source: 'carbon_fiber', target: 'density', label: 'has' },
  { source: 'carbon_fiber', target: 'elastic_modulus', label: 'has' },
  { source: 'carbon_fiber', target: 'aerospace', label: 'used_in' },
  { source: 'carbon_fiber', target: 'automotive', label: 'used_in' },
  { source: 'carbon_fiber', target: 'cvd', label: 'processed_by' },
  // Silicon
  { source: 'silicon', target: 'electrical_conductivity', label: 'has' },
  { source: 'silicon', target: 'electronics', label: 'used_in' },
  { source: 'silicon', target: 'cvd', label: 'processed_by' },
  { source: 'silicon', target: 'sic', label: 'reacts_to_form' },
  // Graphene
  { source: 'graphene', target: 'electrical_conductivity', label: 'has' },
  { source: 'graphene', target: 'tensile_strength', label: 'has' },
  { source: 'graphene', target: 'thermal_conductivity', label: 'has' },
  { source: 'graphene', target: 'electronics', label: 'used_in' },
  { source: 'graphene', target: 'cvd', label: 'processed_by' },
  // Nickel
  { source: 'nickel', target: 'tensile_strength', label: 'has' },
  { source: 'nickel', target: 'corrosion_resistance', label: 'has' },
  { source: 'nickel', target: 'thermal_conductivity', label: 'has' },
  { source: 'nickel', target: 'aerospace', label: 'used_in' },
  { source: 'nickel', target: 'heat_treatment', label: 'processed_by' },
  // Copper
  { source: 'copper', target: 'electrical_conductivity', label: 'has' },
  { source: 'copper', target: 'thermal_conductivity', label: 'has' },
  { source: 'copper', target: 'electronics', label: 'used_in' },
  { source: 'copper', target: 'corrosion_resistance', label: 'has' },
  // Tungsten
  { source: 'tungsten', target: 'hardness', label: 'has' },
  { source: 'tungsten', target: 'density', label: 'has' },
  { source: 'tungsten', target: 'tensile_strength', label: 'has' },
  { source: 'tungsten', target: 'thermal_conductivity', label: 'has' },
  { source: 'tungsten', target: 'sintering', label: 'processed_by' },
  // Compounds
  { source: 'al2o3', target: 'hardness', label: 'has' },
  { source: 'al2o3', target: 'corrosion_resistance', label: 'has' },
  { source: 'sic', target: 'hardness', label: 'has' },
  { source: 'sic', target: 'thermal_conductivity', label: 'has' },
  { source: 'sic', target: 'aerospace', label: 'used_in' },
  { source: 'sic', target: 'electronics', label: 'used_in' },
  { source: 'fe3c', target: 'hardness', label: 'has' },
  { source: 'tio2', target: 'corrosion_resistance', label: 'has' },
  { source: 'tio2', target: 'electrical_conductivity', label: 'has' },
  // Process effects
  { source: 'heat_treatment', target: 'hardness', label: 'modifies' },
  { source: 'heat_treatment', target: 'tensile_strength', label: 'modifies' },
  { source: 'sintering', target: 'density', label: 'affects' },
  { source: 'forging', target: 'tensile_strength', label: 'improves' },
  { source: 'cvd', target: 'electrical_conductivity', label: 'enables' },
];
