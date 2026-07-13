/** MatKG LinkML entity classes that may appear as node `category` values. */
export const SCHEMA_NODE_CLASSES = [
  'Thing',
  'Material',
  'Component',
  'Condition',
  'Interface',
  'Measurement',
  'Method',
  'Parameter',
  'Phenomenon',
  'Process',
  'Property',
  'Structure',
  'Publication',
  'ConjugatedPolymer',
  'ChemicalEntity',
  'Compound',
  'Element',
  'Device',
  'PhotovoltaicCell',
  'OFET',
  'MaterialProperty',
  'ElectronicProperty',
  'ProcessingMethod',
  'ExperimentalTechnique',
  'CodeSnippet',
  'DomainFeature',
] as const;

const RAINBOW = [
  '#ef4444',
  '#f97316',
  '#f59e0b',
  '#eab308',
  '#84cc16',
  '#22c55e',
  '#10b981',
  '#14b8a6',
  '#06b6d4',
  '#3b82f6',
  '#6366f1',
  '#8b5cf6',
  '#a855f7',
  '#d946ef',
  '#ec4899',
  '#f43f5e',
] as const;

/** Sky-400 — matches AsciiOrb accent for LLM-invented categories. */
export const LLM_INVENTED_NODE_COLOR = '#38bdf8';

/** Slate-400 — only for Unknown stub nodes. */
export const UNKNOWN_NODE_COLOR = '#94a3b8';

const SCHEMA_CLASS_COLORS: Record<string, string> = Object.fromEntries(
  SCHEMA_NODE_CLASSES.map((className, index) => [
    className.toLowerCase(),
    RAINBOW[index % RAINBOW.length],
  ]),
);

const SCHEMA_CLASS_SET = new Set(
  SCHEMA_NODE_CLASSES.map(className => className.toLowerCase()),
);

export function normalizeNodeCategory(type: string): string {
  const trimmed = type.trim();
  if (!trimmed) return '';
  const withoutPrefix = trimmed.replace(/^matkg:/i, '');
  const withoutRel = withoutPrefix.replace(/^rel:/i, '');
  return withoutRel.replace(/\s+/g, '').toLowerCase();
}

export function isSchemaNodeCategory(type: string): boolean {
  const normalized = normalizeNodeCategory(type);
  return normalized !== '' && SCHEMA_CLASS_SET.has(normalized);
}

/** Stub / unresolved nodes — excluded from KG UI rendering. */
export function isUnknownNodeCategory(type: string): boolean {
  const normalized = normalizeNodeCategory(type);
  return !normalized || normalized === 'unknown';
}

export function getNodeColor(type: string): string {
  if (isUnknownNodeCategory(type)) {
    return UNKNOWN_NODE_COLOR;
  }
  const normalized = normalizeNodeCategory(type);
  return SCHEMA_CLASS_COLORS[normalized] ?? LLM_INVENTED_NODE_COLOR;
}
