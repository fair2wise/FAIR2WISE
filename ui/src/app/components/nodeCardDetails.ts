import type { LinkedCodeSnippet, LiveGraphNode, PublicationInfo } from './data/liveAgent';

const GITHUB_REPO_RE = /(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)/i;
const OPENALEX_RE = /openalex\.org[_/:]+(W\d+)/i;
const ALS_BEAMLINE_RE = /als\.lbl\.gov\/beamlines\/11-0-1-2/i;
const BLUEPRINT_RE = /blueprint_bl11012|beamline_blueprint/i;
const DOI_PDF_RE = /^(10\.\d{4,})[_/]/i;
const ARXIV_PDF_RE = /^(?:arxiv[_-]?)?\d{4}\.\d{4,5}(?:v\d+)?\.pdf$/i;
const OPS_ORG = 'als-computing';
const MAX_EXTRA_STRING = 800;

export interface NodeSourceLink {
  kind: 'github' | 'doi' | 'openalex' | 'als' | 'blueprint' | 'pdf' | 'url';
  label: string;
  url: string | null;
}

export interface NodePropertyRow {
  label: string;
  value: string;
}

export interface NodeProvenanceFields {
  source_type?: string | null;
  repo_url?: string | null;
  repo_owner?: string | null;
  repo_name?: string | null;
  repo_commit_sha?: string | null;
  source_file_path?: string | null;
  source_file_url?: string | null;
  source_start_line?: number | null;
  source_end_line?: number | null;
  graph_id?: string | null;
  id?: string | null;
  source_papers?: string[] | null;
  description?: string | null;
}

function trimSlash(value: string): string {
  return value.replace(/\/+$/, '').replace(/\.git$/i, '');
}

function asText(value: unknown): string {
  if (value == null) return '';
  return String(value).trim();
}

function parseGithubRepo(value?: string | null): { owner: string; repo: string } | null {
  const match = asText(value).match(GITHUB_REPO_RE);
  if (!match) return null;
  return { owner: match[1], repo: match[2].replace(/\.git$/i, '') };
}

function looksLikeRepoName(value: string): boolean {
  if (!/^[A-Za-z0-9_.-]+$/.test(value)) return false;
  return !/\.(pdf|html|json)$/i.test(value);
}

function repoNameFromSourcePaper(source?: string | null): string | null {
  const text = asText(source);
  if (!text || text.includes('/') || /\.(pdf|html)$/i.test(text) || ALS_BEAMLINE_RE.test(text) || BLUEPRINT_RE.test(text)) {
    return null;
  }
  const head = text.split(':')[0].trim();
  if (head && looksLikeRepoName(head) && !DOI_PDF_RE.test(head)) return head;
  return null;
}

function isOpsNode(fields: NodeProvenanceFields): boolean {
  const graphId = asText(fields.graph_id).toLowerCase();
  const nodeId = asText(fields.id).toLowerCase();
  return graphId.startsWith('bl1101') || nodeId.startsWith('beamline:');
}

function lineFragment(start?: number | null, end?: number | null): string {
  if (!start || start < 1) return '';
  if (end && end > start) return `#L${start}-L${end}`;
  return `#L${start}`;
}

function withLineFragment(url: string, start?: number | null, end?: number | null): string {
  const fragment = lineFragment(start, end);
  if (!fragment) return url;
  if (/#L\d+/i.test(url)) return url;
  return `${url}${fragment}`;
}

export function githubHomepageUrl(fields: NodeProvenanceFields): string | null {
  const parsed = parseGithubRepo(fields.repo_url)
    || (asText(fields.repo_owner) && asText(fields.repo_name)
      ? { owner: asText(fields.repo_owner), repo: asText(fields.repo_name) }
      : null);
  if (parsed) return `https://github.com/${parsed.owner}/${parsed.repo}`;

  const repoName = asText(fields.repo_name)
    || (fields.source_papers || []).map(repoNameFromSourcePaper).find(Boolean)
    || null;
  if (repoName && isOpsNode(fields)) return `https://github.com/${OPS_ORG}/${repoName}`;
  return null;
}

export function githubBlobUrl(fields: NodeProvenanceFields): string | null {
  const path = asText(fields.source_file_path).replace(/^\/+/, '');
  const sha = asText(fields.repo_commit_sha);
  const existing = asText(fields.source_file_url);
  if (existing && GITHUB_REPO_RE.test(existing)) {
    return withLineFragment(existing, fields.source_start_line, fields.source_end_line);
  }

  const parsed = parseGithubRepo(fields.repo_url)
    || parseGithubRepo(fields.description)
    || (asText(fields.repo_owner) && asText(fields.repo_name)
      ? { owner: asText(fields.repo_owner), repo: asText(fields.repo_name) }
      : null);

  let owner = parsed?.owner || '';
  let repo = parsed?.repo || asText(fields.repo_name);
  if (!repo) {
    repo = (fields.source_papers || []).map(repoNameFromSourcePaper).find(Boolean) || '';
  }
  if (!owner && repo && isOpsNode(fields)) owner = OPS_ORG;
  if (!owner || !repo) return githubHomepageUrl(fields);

  const home = `https://github.com/${owner}/${repo}`;
  if (sha && path) {
    return withLineFragment(`${home}/blob/${sha}/${path}`, fields.source_start_line, fields.source_end_line);
  }
  return home;
}

export function openAlexWorkUrl(source?: string | null): string | null {
  const match = asText(source).match(OPENALEX_RE);
  return match ? `https://openalex.org/${match[1]}` : null;
}

export function alsBeamlineUrl(source?: string | null): string | null {
  const text = asText(source);
  if (!text) return null;
  if (ALS_BEAMLINE_RE.test(text) || text === 'als.lbl.gov/beamlines/11-0-1-2') {
    return 'https://als.lbl.gov/beamlines/11-0-1-2/';
  }
  return null;
}

export function isBlueprintSource(source?: string | null): boolean {
  return BLUEPRINT_RE.test(asText(source));
}

export function isLiteraturePublication(publication: PublicationInfo): boolean {
  if (publication.doi || publication.paper_title || publication.journal) return true;
  const source = asText(publication.source_paper);
  if (!source) return false;
  if (openAlexWorkUrl(source)) return true;
  if (DOI_PDF_RE.test(source) || ARXIV_PDF_RE.test(source) || source.toLowerCase().endsWith('.pdf')) {
    return !ALS_BEAMLINE_RE.test(source) && !isBlueprintSource(source);
  }
  return false;
}

function pushUnique(links: NodeSourceLink[], next: NodeSourceLink) {
  const key = (next.url || next.label).toLowerCase();
  if (links.some(link => (link.url || link.label).toLowerCase() === key)) return;
  links.push(next);
}

export function collectNodeSourceLinks(
  node: Pick<LiveGraphNode, 'publications' | 'source_papers' | 'description' | 'graph_id' | 'id'> & NodeProvenanceFields,
): NodeSourceLink[] {
  const links: NodeSourceLink[] = [];
  const blob = githubBlobUrl(node);
  const home = githubHomepageUrl(node);
  if (blob && /\/blob\//.test(blob)) {
    const path = asText(node.source_file_path);
    pushUnique(links, {
      kind: 'github',
      label: path
        ? `${path}${lineFragment(node.source_start_line, node.source_end_line)}`
        : blob.replace(/^https?:\/\//, ''),
      url: blob,
    });
  } else if (home) {
    pushUnique(links, {
      kind: 'github',
      label: home.replace(/^https?:\/\//, ''),
      url: home,
    });
  } else if (blob) {
    pushUnique(links, {
      kind: 'github',
      label: blob.replace(/^https?:\/\//, ''),
      url: blob,
    });
  }

  const sources = [
    ...(node.source_papers || []),
    ...(node.publications || []).map(pub => pub.source_paper || ''),
    asText(node.repo_url),
  ].filter(Boolean);

  for (const source of sources) {
    const als = alsBeamlineUrl(source);
    if (als) {
      pushUnique(links, { kind: 'als', label: 'ALS beamline 11.0.1.2', url: als });
      continue;
    }
    if (isBlueprintSource(source)) {
      pushUnique(links, {
        kind: 'blueprint',
        label: `Blueprint (${asText(source).split('/').pop() || 'beamline blueprint'})`,
        url: null,
      });
      continue;
    }
    const openalex = openAlexWorkUrl(source);
    if (openalex) {
      pushUnique(links, { kind: 'openalex', label: openalex.replace(/^https?:\/\//, ''), url: openalex });
      continue;
    }
    const github = parseGithubRepo(source);
    if (github) {
      const url = source.startsWith('http') ? source : `https://github.com/${github.owner}/${github.repo}`;
      pushUnique(links, { kind: 'github', label: `github.com/${github.owner}/${github.repo}`, url: trimSlash(url) });
    }
  }

  return links;
}

const PROPERTY_LABELS: Record<string, string> = {
  pv: 'PV',
  ophyd_name: 'Ophyd name',
  ophyd_class: 'Ophyd class',
  ioc_record: 'IOC record',
  mapping_file: 'Mapping file',
  device_kind: 'Device kind',
  photon_energy_eV: 'Photon energy (eV)',
  absorption_edge: 'Absorption edge',
  scattering_technique: 'Scattering technique',
  technique_type: 'Technique type',
  raw_category: 'Raw category',
  code_domain: 'Code domain',
  entityType: 'Entity type',
  entity_type: 'Entity type',
  esaf: 'ESAF',
  esaf_number: 'ESAF',
  esaf_id: 'ESAF',
  proposal: 'Proposal',
  proposal_code: 'Proposal',
  proposal_id: 'Proposal',
  sample: 'Sample',
  sample_code: 'Sample',
  sample_id: 'Sample',
  sample_name: 'Sample',
  scan: 'Scan',
  scan_id: 'Scan',
  scientist: 'Scientist',
  co_scientist: 'Co-scientist',
  plan_name: 'Plan',
  uid: 'UID',
  uri: 'URI',
  graphql_id: 'GraphQL ID',
  nodeId: 'Tiled node ID',
  node_id: 'Tiled node ID',
};

const SKIP_PROPERTY_KEYS = new Set([
  'haystack',
  'outgoinglinks',
  'relations',
  'properties',
  'description',
  'label',
  'name',
  'category',
  'type',
  'id',
  'graph_id',
  'graph_label',
]);

const TILED_IDENTITY_FIELDS: Array<{ label: string; keys: string[] }> = [
  { label: 'Entity type', keys: ['entityType', 'entity_type'] },
  { label: 'ESAF', keys: ['esaf', 'esaf_number', 'esaf_id'] },
  { label: 'Proposal', keys: ['proposal', 'proposal_code', 'proposal_id'] },
  { label: 'Sample', keys: ['sample', 'sample_code', 'sample_id', 'sample_name'] },
  { label: 'Scan', keys: ['scan', 'scan_id'] },
  { label: 'Scientist', keys: ['scientist'] },
  { label: 'Co-scientist', keys: ['co_scientist'] },
  { label: 'Plan', keys: ['plan_name'] },
  { label: 'UID', keys: ['uid'] },
  { label: 'URI', keys: ['uri'] },
  { label: 'GraphQL ID', keys: ['graphql_id'] },
  { label: 'Tiled node ID', keys: ['nodeId', 'node_id'] },
];

function humanizeKey(key: string): string {
  return PROPERTY_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function formatScalar(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'string') {
    const text = value.trim();
    if (!text || text === 'N/A') return null;
    if (text.length > MAX_EXTRA_STRING) return `${text.slice(0, MAX_EXTRA_STRING)}…`;
    return text;
  }
  return null;
}

function formatMatkgProperty(entry: unknown): NodePropertyRow | null {
  if (!entry || typeof entry !== 'object') return null;
  const record = entry as Record<string, unknown>;
  const name = asText(record.property || record.name || record.feature_name);
  const value = formatScalar(record.value ?? record.feature_value);
  if (!name && !value) return null;
  const units = asText(record.units || record.feature_units);
  return {
    label: name || 'Property',
    value: [value, units].filter(Boolean).join(' '),
  };
}

export function graphDisplayName(node: Pick<LiveGraphNode, 'graph_id' | 'graph_label'>): string {
  return asText(node.graph_label) || asText(node.graph_id);
}

export function nodeDefinition(node: Pick<LiveGraphNode, 'description'>): string {
  const text = asText(node.description);
  if (!text || text === 'N/A') return '';
  return text;
}

export function remainingNodeProperties(node: LiveGraphNode): NodePropertyRow[] {
  const rows: NodePropertyRow[] = [];
  const seen = new Set<string>();

  function add(label: string, value: unknown) {
    const formatted = formatScalar(value);
    if (!formatted) return;
    const key = label.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({ label, value: formatted });
  }

  const bag: Record<string, unknown> = { ...(node.extra_fields || {}) };
  const rawProperties = node.properties as unknown;
  if (rawProperties && !Array.isArray(rawProperties) && typeof rawProperties === 'object') {
    Object.assign(bag, rawProperties as Record<string, unknown>);
  }
  if (Array.isArray(node.properties)) {
    for (const entry of node.properties) {
      if (!entry || typeof entry !== 'object') continue;
      const record = entry as Record<string, unknown>;
      const name = asText(record.property || record.name);
      if (name) bag[name] = record.value ?? record.feature_value;
    }
  }

  add('Type', node.type);
  add('ID', node.id);
  add('Graph', graphDisplayName(node));
  add('Formula', node.formula);
  add('Function', node.function_name);
  add('Language', node.code_language);
  add('File', node.source_file_path);
  if (node.source_start_line) {
    add(
      'Lines',
      node.source_end_line && node.source_end_line !== node.source_start_line
        ? `${node.source_start_line}–${node.source_end_line}`
        : String(node.source_start_line),
    );
  }
  add('Commit', node.repo_commit_sha ? String(node.repo_commit_sha).slice(0, 12) : '');
  add('License', node.repository_license);

  for (const field of TILED_IDENTITY_FIELDS) {
    const value = field.keys.map(key => bag[key]).find(item => formatScalar(item));
    add(field.label, value);
  }

  if (Array.isArray(node.properties)) {
    for (const entry of node.properties) {
      const record = entry as Record<string, unknown> | null;
      const propName = asText(record?.property || record?.name).toLowerCase();
      if (TILED_IDENTITY_FIELDS.some(field => field.keys.some(key => key.toLowerCase() === propName))) {
        continue;
      }
      const row = formatMatkgProperty(entry);
      if (!row) continue;
      if (seen.has(row.label.toLowerCase())) continue;
      seen.add(row.label.toLowerCase());
      rows.push(row);
    }
  }

  for (const [key, value] of Object.entries(bag)) {
    if (SKIP_PROPERTY_KEYS.has(key.toLowerCase())) continue;
    if (TILED_IDENTITY_FIELDS.some(field => field.keys.includes(key))) continue;
    if (Array.isArray(value)) {
      if (value.every(item => item && typeof item === 'object')) {
        for (const item of value) {
          const row = formatMatkgProperty(item);
          if (!row) continue;
          if (seen.has(row.label.toLowerCase())) continue;
          seen.add(row.label.toLowerCase());
          rows.push(row);
        }
        continue;
      }
      const joined = value.map(item => formatScalar(item)).filter(Boolean).join(', ');
      add(humanizeKey(key), joined);
      continue;
    }
    if (value && typeof value === 'object') {
      const compact = Object.entries(value as Record<string, unknown>)
        .map(([innerKey, innerValue]) => {
          const formatted = formatScalar(innerValue);
          return formatted ? `${innerKey}: ${formatted}` : null;
        })
        .filter(Boolean)
        .join(', ');
      add(humanizeKey(key), compact);
      continue;
    }
    add(humanizeKey(key), value);
  }

  return rows;
}

export function snippetProvenance(snippet: LinkedCodeSnippet): NodeProvenanceFields {
  return {
    source_type: snippet.source_type,
    repo_url: snippet.repo_url,
    repo_owner: snippet.repo_owner,
    repo_name: snippet.repo_name,
    repo_commit_sha: snippet.repo_commit_sha,
    source_file_path: snippet.source_file_path,
    source_file_url: snippet.source_file_url,
    source_start_line: snippet.source_start_line,
    source_end_line: snippet.source_end_line,
    source_papers: (snippet.publications || []).map(pub => pub.source_paper || '').filter(Boolean),
  };
}
