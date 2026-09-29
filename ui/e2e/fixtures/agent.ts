import type { Page, Route } from '@playwright/test';

export const AGENT_ORIGIN = 'http://127.0.0.1:8090';

export const RSOXS_PATH = 'storage/kg/matkg_rsoxs_v3.json';
export const OPS_PATH = 'storage/kg/matkg_bl1101_v9.json';
export const XRAY_DEMO_PATH = 'storage/kg/matkg_xray_papers_cborg_chat.json';

export const P3HT_NODE = {
  id: 'matkg:p3ht',
  label: 'P3HT',
  type: 'Material',
  description: 'Poly(3-hexylthiophene), a conjugated polymer used in organic photovoltaics.',
  graph_id: 'rsoxs_v3',
  graph_label: 'RSoXS literature',
};

export const SCAN_NODE = {
  id: 'tiled:scan-301',
  label: 'scan 301',
  type: 'BlueskyPlan',
  description: 'Azimuth scan on 11.0.1.2.',
  graph_id: 'bl1101',
  graph_label: 'Beamline ops',
  extra_fields: { esaf: 'P202600045-01', proposal: 'S01', sample: 'S01', scan: '301' },
};

export const SI_NODE = {
  id: 'mp:mp-149',
  label: 'mp-149 Si',
  type: 'Material',
  description: 'Silicon from the Materials Project.',
  graph_id: 'mp',
  formula: 'Si',
};

export const PAPER_PUB = {
  paper_title: 'Resonant soft X-ray scattering of P3HT blends',
  authors: ['Ada Lovelace', 'Alan Turing'],
  publication_year: 2020,
  journal: 'Advanced Energy Materials',
  doi: '10.1002/aenm.202001203',
  source_paper: 'XRAY1.pdf',
};

export const CATALOG_GRAPH = {
  nodes: [P3HT_NODE, SCAN_NODE],
  edges: [
    { source: P3HT_NODE.id, target: SCAN_NODE.id, predicate: 'rel:used_in' },
  ],
  source_path: RSOXS_PATH,
};

export const QUERY_GRAPH = {
  nodes: [P3HT_NODE, SI_NODE],
  edges: [],
  source_path: 'query:rsoxs_v3',
};

export const CITATION_ANSWER =
  'P3HT [KG: P3HT] is used for resonant soft X-ray scattering. '
  + 'See [PDF: XRAY1.pdf] and beamline notes [OPS: blueprint.html §Motors]. '
  + 'Silicon from Materials Project [MP: mp-149 Si].';

export function defaultSettings() {
  return {
    backend: 'cborg',
    model: 'lbl/cborg-chat',
    graph_source: 'json',
    workflow_mode: 'agentic',
    extraction_mode: 'targeted',
    targeted_max_pages: 6,
    json_graph_path: RSOXS_PATH,
    json_graph_paths: [RSOXS_PATH, OPS_PATH],
    kg_query_max_nodes: 1000,
    kg_query_hops: 20,
    source_rag: false,
    use_live_tiled: true,
    tiled_uri: 'http://127.0.0.1:8001',
    tiled_api_key_set: true,
    tiled_status: 'ok',
    tiled_error: null,
    available_json_graphs: [RSOXS_PATH, OPS_PATH, XRAY_DEMO_PATH],
    available_graphs: [
      { path: RSOXS_PATH, graph_id: 'rsoxs_v3', label: 'RSoXS literature v3' },
      { path: OPS_PATH, graph_id: 'bl1101', label: '11.0.1.2 ops v9' },
      { path: XRAY_DEMO_PATH, graph_id: 'xray_demo', label: 'X-ray demo' },
    ],
    selected_graph_ids: ['rsoxs_v3', 'bl1101'],
    available_cborg_models: ['lbl/cborg-chat', 'lbl/llama'],
    default_ollama_model: 'deepseek-r1:70b',
  };
}

export function citationChatResponse() {
  return {
    status: 'ok',
    answer: CITATION_ANSWER,
    sufficient: true,
    node_ids: [P3HT_NODE.id, SI_NODE.id],
    publications: [PAPER_PUB],
    confidence: 0.91,
    rounds: [],
    graph: QUERY_GRAPH,
    graph_source_requested: 'json',
    graph_source_used: 'json',
    workdir: '/tmp/f2w-e2e',
    pending: null,
  };
}

export function downloadPendingResponse() {
  return {
    status: 'pending_download',
    answer: '',
    sufficient: false,
    node_ids: [],
    publications: [],
    confidence: 0.4,
    rounds: [],
    graph: { nodes: [], edges: [], source_path: '' },
    workdir: '/tmp/f2w-e2e',
    pending: {
      kind: 'download',
      prompt: 'Which paper should be downloaded?',
      papers: [
        {
          index: 0,
          recommended: true,
          title: 'Resonant soft X-ray scattering of P3HT blends',
          doi: '10.1002/aenm.202001203',
          publication_year: 2020,
          abstract: 'A study of P3HT morphology with RSoXS.',
        },
        {
          index: 1,
          title: 'Alternative OPV morphology paper',
          doi: '10.1000/example.doi',
          publication_year: 2019,
        },
      ],
    },
  };
}

export function extractionPendingResponse() {
  return {
    status: 'pending_extraction',
    answer: '',
    sufficient: false,
    node_ids: [],
    publications: [],
    confidence: 0.5,
    rounds: [],
    graph: { nodes: [], edges: [], source_path: '' },
    workdir: '/tmp/f2w-e2e',
    pending: {
      kind: 'extraction',
      candidate: {
        index: 0,
        title: 'Resonant soft X-ray scattering of P3HT blends',
        doi: '10.1002/aenm.202001203',
        publication_year: 2020,
      },
    },
  };
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function json(data: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

export interface MockAgentOptions {
  settings?: Record<string, unknown>;
  graph?: typeof CATALOG_GRAPH;
  chatQueue?: Array<Record<string, unknown>>;
  hangChatMs?: number;
  failChat?: 'network' | 500;
  failSettings?: boolean;
  publications?: Array<Record<string, unknown>>;
}

export async function mockAgent(page: Page, options: MockAgentOptions = {}) {
  const settings = { ...defaultSettings(), ...(options.settings ?? {}) };
  const graph = options.graph ?? CATALOG_GRAPH;
  const chatQueue = [...(options.chatQueue ?? [citationChatResponse()])];
  const publications = options.publications ?? [PAPER_PUB];

  const handle = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;

    if (path === '/settings' && method === 'GET') {
      if (options.failSettings) {
        await route.abort('failed');
        return;
      }
      await route.fulfill(json(settings));
      return;
    }

    if (path === '/settings' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>;
      Object.assign(settings, body);
      await route.fulfill(json(settings));
      return;
    }

    if (path === '/graph' && method === 'GET') {
      await route.fulfill(json(graph));
      return;
    }

    if (path.startsWith('/graph/node/') && method === 'GET') {
      const id = decodeURIComponent(path.slice('/graph/node/'.length));
      const node = graph.nodes.find(item => item.id === id)
        || QUERY_GRAPH.nodes.find(item => item.id === id)
        || { ...P3HT_NODE, id };
      await route.fulfill(json(node));
      return;
    }

    if (path.startsWith('/graph/neighborhood/') && method === 'GET') {
      await route.fulfill(json(graph));
      return;
    }

    if (path === '/graph/nodes/search' && method === 'POST') {
      const body = request.postDataJSON() as { query?: string };
      const q = (body.query || '').toLowerCase();
      const results = graph.nodes
        .filter(node => node.label.toLowerCase().includes(q) || node.id.toLowerCase().includes(q))
        .map(node => ({ node, score: 0.92 }));
      await route.fulfill(json({
        query: body.query || '',
        results,
        retrieval_backend: 'lexical',
      }));
      return;
    }

    if (path === '/publications/search' && method === 'POST') {
      const body = request.postDataJSON() as { query?: string; include_external?: boolean };
      await route.fulfill(json({
        status: 'ok',
        query: body.query || '',
        publications,
        matched_node_ids: [P3HT_NODE.id],
        source: body.include_external ? 'kg+openalex' : 'kg',
      }));
      return;
    }

    if (path === '/session/reset' && method === 'POST') {
      await route.fulfill(json({
        status: 'reset',
        session_memory: '',
        session_memory_has_context: false,
      }));
      return;
    }

    if (path.startsWith('/session/') && method === 'DELETE') {
      await route.fulfill(json({ status: 'deleted' }));
      return;
    }

    if (
      (path === '/chat/stream' || path === '/chat/action/stream' || path === '/chat' || path === '/chat/action')
      && method === 'POST'
      && options.failChat
    ) {
      if (options.failChat === 'network') {
        await route.abort('failed');
        return;
      }
      await route.fulfill({
        status: 500,
        contentType: 'text/plain',
        body: 'upstream failed',
      });
      return;
    }

    if (
      (path === '/chat/stream' || path === '/chat/action/stream')
      && method === 'POST'
    ) {
      if (options.hangChatMs) {
        await new Promise(resolve => setTimeout(resolve, options.hangChatMs));
      }
      const payload = chatQueue.length > 1 ? chatQueue.shift()! : (chatQueue[0] ?? citationChatResponse());
      const body = sse('progress', {
        phase: 'retrieve',
        message: 'Retrieving from selected knowledge graphs',
      }) + sse('complete', payload);
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body,
      });
      return;
    }

    if ((path === '/chat' || path === '/chat/action') && method === 'POST') {
      const payload = chatQueue[0] ?? citationChatResponse();
      await route.fulfill(json(payload));
      return;
    }

    await route.fulfill({ status: 404, body: 'not mocked' });
  };

  await page.route(`${AGENT_ORIGIN}/**`, handle);
  await page.route('http://localhost:8090/**', handle);
}

export async function askQuestion(page: Page, text: string) {
  const input = page.getByPlaceholder('Ask a domain-specific question...');
  await input.fill(text);
  await input.press('Enter');
}

export async function waitForTypedAnswer(page: Page) {
  await page.getByRole('button', { name: 'View Knowledge Graph' }).waitFor({ timeout: 20_000 });
}

export async function openSettings(page: Page) {
  await page.getByRole('button', { name: 'Settings' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('heading', { name: 'Settings' }).waitFor();
  await dialog.getByRole('radio', { name: /JSON/ }).waitFor();
}
