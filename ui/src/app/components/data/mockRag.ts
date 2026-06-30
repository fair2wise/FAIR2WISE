import { GRAPH_NODES, GRAPH_EDGES } from './materialsData';

export interface RagResult {
  response: string;
  usedNodeIds: string[];
  confidence: number;
}

const keywordMap: Record<string, string[]> = {
  // Materials
  'steel': ['steel', 'fe3c', 'heat_treatment', 'forging', 'tensile_strength'],
  'stainless': ['steel', 'corrosion_resistance', 'fe3c'],
  'iron': ['steel', 'fe3c', 'heat_treatment'],
  'aluminum': ['aluminum', 'al2o3', 'aerospace', 'automotive', 'density'],
  'aluminium': ['aluminum', 'al2o3', 'aerospace', 'automotive', 'density'],
  'titanium': ['titanium', 'tio2', 'biomedical', 'aerospace', 'corrosion_resistance'],
  'carbon fiber': ['carbon_fiber', 'aerospace', 'automotive', 'cvd', 'tensile_strength'],
  'carbon fibre': ['carbon_fiber', 'aerospace', 'automotive', 'cvd'],
  'composite': ['carbon_fiber', 'sic', 'tensile_strength', 'density'],
  'graphene': ['graphene', 'electronics', 'cvd', 'electrical_conductivity', 'thermal_conductivity'],
  'silicon': ['silicon', 'sic', 'electronics', 'cvd', 'electrical_conductivity'],
  'nickel': ['nickel', 'aerospace', 'heat_treatment', 'corrosion_resistance'],
  'copper': ['copper', 'electronics', 'electrical_conductivity', 'thermal_conductivity'],
  'tungsten': ['tungsten', 'hardness', 'sintering', 'density'],
  // Properties
  'strength': ['tensile_strength', 'steel', 'carbon_fiber', 'titanium', 'forging'],
  'tensile': ['tensile_strength', 'steel', 'carbon_fiber', 'titanium'],
  'yield': ['tensile_strength', 'steel', 'aluminum'],
  'hard': ['hardness', 'steel', 'tungsten', 'al2o3', 'sic', 'fe3c'],
  'hardness': ['hardness', 'steel', 'tungsten', 'al2o3', 'sic', 'fe3c', 'heat_treatment'],
  'conduct': ['electrical_conductivity', 'thermal_conductivity', 'copper', 'graphene'],
  'thermal': ['thermal_conductivity', 'copper', 'graphene', 'sic', 'nickel', 'aluminum'],
  'heat conduction': ['thermal_conductivity', 'copper', 'graphene', 'sic'],
  'electric': ['electrical_conductivity', 'copper', 'graphene', 'silicon', 'cvd'],
  'semiconductor': ['silicon', 'electronics', 'electrical_conductivity', 'cvd'],
  'corrosion': ['corrosion_resistance', 'titanium', 'aluminum', 'nickel', 'tio2', 'al2o3'],
  'rust': ['corrosion_resistance', 'steel', 'aluminum', 'titanium'],
  'oxidation': ['corrosion_resistance', 'tio2', 'al2o3', 'titanium', 'aluminum'],
  'light': ['density', 'aluminum', 'carbon_fiber', 'titanium'],
  'lightweight': ['density', 'aluminum', 'carbon_fiber', 'titanium'],
  'weight': ['density', 'aluminum', 'carbon_fiber', 'titanium'],
  'density': ['density', 'aluminum', 'carbon_fiber', 'titanium', 'tungsten'],
  'stiff': ['elastic_modulus', 'carbon_fiber', 'steel', 'sic'],
  'stiffness': ['elastic_modulus', 'carbon_fiber', 'steel', 'sic'],
  'modulus': ['elastic_modulus', 'carbon_fiber', 'steel'],
  // Applications
  'aerospace': ['aerospace', 'titanium', 'aluminum', 'carbon_fiber', 'nickel', 'sic'],
  'aircraft': ['aerospace', 'titanium', 'aluminum', 'carbon_fiber', 'nickel'],
  'rocket': ['aerospace', 'titanium', 'nickel', 'carbon_fiber'],
  'turbine': ['aerospace', 'nickel', 'heat_treatment', 'tensile_strength'],
  'jet': ['aerospace', 'nickel', 'titanium', 'tensile_strength'],
  'automotive': ['automotive', 'steel', 'aluminum', 'carbon_fiber'],
  'car': ['automotive', 'steel', 'aluminum', 'forging'],
  'vehicle': ['automotive', 'steel', 'aluminum', 'carbon_fiber'],
  'electronics': ['electronics', 'silicon', 'graphene', 'copper', 'cvd'],
  'chip': ['electronics', 'silicon', 'electrical_conductivity', 'cvd'],
  'transistor': ['electronics', 'silicon', 'electrical_conductivity'],
  'solar': ['electronics', 'silicon', 'electrical_conductivity'],
  'biomedical': ['biomedical', 'titanium', 'corrosion_resistance', 'tio2'],
  'implant': ['biomedical', 'titanium', 'corrosion_resistance', 'sintering'],
  'bone': ['biomedical', 'titanium', 'elastic_modulus', 'corrosion_resistance'],
  'medical': ['biomedical', 'titanium', 'corrosion_resistance'],
  'construction': ['construction', 'steel', 'elastic_modulus', 'tensile_strength'],
  'building': ['construction', 'steel', 'tensile_strength'],
  'bridge': ['construction', 'steel', 'tensile_strength', 'elastic_modulus'],
  'structural': ['construction', 'steel', 'tensile_strength', 'elastic_modulus'],
  // Processes
  'heat treat': ['heat_treatment', 'steel', 'nickel', 'hardness'],
  'anneal': ['heat_treatment', 'steel', 'aluminum'],
  'quench': ['heat_treatment', 'steel', 'hardness'],
  'temper': ['heat_treatment', 'steel', 'hardness', 'tensile_strength'],
  'sintering': ['sintering', 'titanium', 'tungsten', 'al2o3', 'density'],
  'sinter': ['sintering', 'titanium', 'tungsten', 'al2o3'],
  'powder': ['sintering', 'tungsten', 'titanium', 'al2o3'],
  'cvd': ['cvd', 'silicon', 'graphene', 'carbon_fiber', 'electrical_conductivity'],
  'vapor deposition': ['cvd', 'silicon', 'graphene', 'carbon_fiber'],
  'forging': ['forging', 'steel', 'aluminum', 'titanium', 'tensile_strength'],
  'forge': ['forging', 'steel', 'aluminum', 'titanium'],
  'processing': ['heat_treatment', 'forging', 'sintering', 'cvd'],
  // Compounds
  'alumina': ['al2o3', 'hardness', 'corrosion_resistance', 'aluminum'],
  'silicon carbide': ['sic', 'hardness', 'thermal_conductivity', 'aerospace'],
  'cementite': ['fe3c', 'hardness', 'steel'],
  'carbide': ['sic', 'fe3c', 'hardness'],
  'oxide': ['tio2', 'al2o3', 'corrosion_resistance'],
};

function buildResponse(query: string, usedNodes: string[]): string {
  const q = query.toLowerCase();
  const labels = usedNodes.map(id => GRAPH_NODES.find(n => n.id === id)?.label ?? id);
  const top4 = labels.slice(0, 4).join(', ');

  if ((q.includes('strong') || q.includes('tensile')) && (q.includes('material') || q.includes('which') || q.includes('strongest'))) {
    return `The knowledge graph retrieved ${usedNodes.length} nodes (${top4}, ...) to answer this.\n\n**Carbon Fiber** has the highest tensile strength-to-weight ratio among structural materials — up to 7 GPa tensile strength with a density of only 1.6 g/cm³. In absolute terms, **Tungsten** reaches ~3100 MPa UTS but with high density (19.3 g/cm³). **Steel** offers a practical balance at 400–2500 MPa depending on alloy and heat treatment. **Titanium** alloys achieve ~1400 MPa with density 4.5 g/cm³.\n\nThe graph shows that forging and heat treatment both improve tensile strength by refining grain structure and controlling precipitate phases.`;
  }
  if (q.includes('aerospace') || q.includes('aircraft') || q.includes('turbine') || q.includes('jet')) {
    return `Retrieving from ${usedNodes.length} graph nodes (${top4}, ...) for aerospace materials:\n\n**Nickel superalloys** (Inconel, Waspaloy) dominate turbine hot-section blades — they maintain tensile strength above 1000°C. **Titanium** is preferred for compressor stages and airframes due to its specific strength. **Carbon fiber composites** have largely replaced aluminum in fuselage primary structure (e.g., B787 is 50% CF by weight). **Aluminum** alloys remain dominant in secondary structure for cost efficiency.\n\nThe graph edges reveal these are connected via the 'used_in: aerospace' relationship, with heat treatment and CVD as the key enabling processes.`;
  }
  if (q.includes('conduct') || q.includes('electric') || q.includes('resistiv')) {
    return `Graph retrieval found ${usedNodes.length} relevant nodes (${top4}, ...) for electrical conductivity:\n\n**Copper** is the reference conductor at 5.96×10⁷ S/m and remains the dominant choice for wiring. **Graphene** has intrinsic conductivity exceeding copper (~10⁸ S/m) but scalable deposition via CVD is still maturing. **Silicon** is a semiconductor (not a conductor) — its conductivity is tunable 10⁻³ to 10³ S/m through doping, making it foundational for transistors. **TiO₂** is a wide-bandgap semiconductor (Eg ~3.2 eV) used in photovoltaics and electrochemistry.`;
  }
  if (q.includes('thermal') && (q.includes('conduct') || q.includes('heat') || q.includes('transfer'))) {
    return `The graph retrieved ${usedNodes.length} nodes (${top4}, ...) related to thermal conductivity:\n\n**Graphene** leads at ~5000 W/m·K (suspended monolayer). **Copper** follows at 401 W/m·K, making it the industrial benchmark. **SiC** achieves 120–490 W/m·K depending on polytype — excellent for power electronics heat spreaders. **Aluminum** (205 W/m·K) offers a good balance of thermal performance, weight, and machinability for heat sinks. **Steel** has relatively low thermal conductivity (~50 W/m·K).`;
  }
  if (q.includes('corrosion') || q.includes('rust') || q.includes('oxidat')) {
    return `Retrieved ${usedNodes.length} graph nodes (${top4}, ...) for corrosion resistance:\n\n**Titanium** forms a stable TiO₂ passive layer spontaneously in air/aqueous environments — it resists seawater, chlorides, and acids that attack steel. **Aluminum** forms Al₂O₃ rapidly, providing good protection except in strong alkalis. **Nickel alloys** resist high-temperature oxidizing gases (essential for turbine environments). **Steel** requires protective coatings or alloying (chromium > 10.5% for stainless) to resist corrosion.`;
  }
  if (q.includes('light') || q.includes('density') || q.includes('weight') || q.includes('specific strength')) {
    return `Graph retrieval found ${usedNodes.length} nodes (${top4}, ...) related to density and specific properties:\n\n**Carbon fiber** composites dominate at 1.6–1.9 g/cm³ with the highest specific strength (σ/ρ). **Aluminum** at 2.7 g/cm³ offers excellent machinability and cost. **Titanium** (4.5 g/cm³) has higher density than aluminum but much greater strength — its specific strength rivals carbon fiber. **Steel** (7.8 g/cm³) is the heaviest common structural metal but remains dominant due to low cost and well-understood processing.`;
  }
  if (q.includes('biomedical') || q.includes('implant') || q.includes('bone') || q.includes('biomed')) {
    return `The graph retrieved ${usedNodes.length} nodes (${top4}, ...) for biomedical applications:\n\n**Titanium** is the gold standard for orthopedic and dental implants. Its TiO₂ passive layer provides corrosion resistance in physiological saline. Its elastic modulus (~110 GPa) is closer to cortical bone (~20–30 GPa) than steel (200 GPa), reducing stress shielding. Ti alloys (Ti-6Al-4V) are processed via sintering and machining for implant geometries. No other structural metal matches this combination of biocompatibility and mechanical performance.`;
  }
  if (q.includes('hard') && !q.includes('hard to')) {
    return `Retrieved ${usedNodes.length} graph nodes (${top4}, ...) for hardness-related materials:\n\n**SiC** (~2500 HV) and **Al₂O₃** (~1500–1800 HV) are among the hardest engineering ceramics. **Tungsten** leads metals at ~3430 MPa Vickers. In steels, hardness is controlled by **Fe₃C** (cementite) content and morphology — martensite formation via quenching creates the hardest steel microstructures. Heat treatment (quench + temper) is the primary process used to tune steel hardness between ~150–900 HV.`;
  }
  if (q.includes('process') || q.includes('sintering') || q.includes('forging') || q.includes('cvd') || q.includes('heat treat')) {
    return `The graph retrieved ${usedNodes.length} process-related nodes (${top4}, ...) for manufacturing context:\n\n**Heat Treatment** controls steel and nickel alloy microstructure — quenching traps carbon in martensite, tempering relieves brittleness. **Sintering** consolidates powder compacts (WC-Co tools, Ti implants, Al₂O₃ ceramics) via solid-state diffusion. **Forging** refines grain structure and eliminates casting porosity, significantly improving fatigue life. **CVD** deposits thin films for silicon epitaxy, graphene synthesis, and SiC/DLC hard coatings.`;
  }
  if (q.includes('compare') || q.includes('vs') || q.includes('versus') || q.includes('difference')) {
    return `Cross-material comparison — retrieved ${usedNodes.length} graph nodes (${top4}, ...):\n\nThe graph structure reveals that **Aluminum** and **Titanium** are both connected to aerospace applications but differ fundamentally: Al is cheaper and more machinable; Ti offers 2× specific strength. **Steel** and **Carbon Fiber** share high tensile strength but differ in density (7.8 vs 1.8 g/cm³) and cost. **Graphene** and **Copper** both connect to electrical conductivity but graphene's intrinsic mobility is 100× higher — the bottleneck is scalable deposition via CVD.`;
  }

  return `Graph RAG retrieved ${usedNodes.length} relevant nodes (${top4}, ...) for your query.\n\nThe retrieved subgraph spans materials, properties, processes, and applications. Key relationships identified: materials connect to their characteristic properties via 'has' edges, and to manufacturing processes via 'processed_by' edges. This multi-hop traversal grounds the answer in structured knowledge rather than parametric recall alone, improving reliability for materials science queries where precision matters.`;
}

export function queryRag(query: string): RagResult {
  const q = query.toLowerCase();
  const matchedNodeIds = new Set<string>();

  for (const [keyword, nodeIds] of Object.entries(keywordMap)) {
    if (q.includes(keyword)) {
      nodeIds.forEach(id => matchedNodeIds.add(id));
    }
  }

  // Fallback to broad nodes if no match
  if (matchedNodeIds.size === 0) {
    ['steel', 'aluminum', 'titanium', 'tensile_strength', 'aerospace', 'construction'].forEach(id =>
      matchedNodeIds.add(id)
    );
  }

  // 1-hop expansion to fill context window (cap at 14 nodes)
  const direct = Array.from(matchedNodeIds);
  GRAPH_EDGES.forEach(edge => {
    if (matchedNodeIds.size >= 14) return;
    if (direct.includes(edge.source)) matchedNodeIds.add(edge.target);
    else if (direct.includes(edge.target)) matchedNodeIds.add(edge.source);
  });

  const usedNodeIds = Array.from(matchedNodeIds);
  const confidence = Math.min(0.97, 0.58 + direct.length * 0.04);

  return {
    response: buildResponse(query, usedNodeIds),
    usedNodeIds,
    confidence,
  };
}
