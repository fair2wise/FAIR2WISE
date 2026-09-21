import { describe, expect, it } from 'vitest';
import type { ChatMessage } from './chatSessions';
import {
  publicationSectionHeading,
  publicationsBlockText,
  queryGraphFromResult,
  raiseViewerLimit,
} from './ChatSidebar';
import type { AgentChatResponse } from './data/liveAgent';

describe('post-extraction publication labels', () => {
  const message: ChatMessage = {
    id: 'post-extraction',
    role: 'assistant',
    content: 'More evidence is needed.',
    status: 'insufficient_evidence',
  };

  it('labels insufficient post-extraction sources as relevant but incomplete', () => {
    expect(publicationSectionHeading(message)).toBe(
      'Relevant Publications and Sources — More Evidence Needed:',
    );
  });

  it('uses the same label in copied publication text', () => {
    const text = publicationsBlockText(
      [{ paper_title: 'Relevant paper', source_paper: 'paper.pdf' }],
      false,
      false,
      true,
    );

    expect(text).toContain('Relevant Publications and Sources — More Evidence Needed:');
    expect(text).toContain('Relevant paper');
  });
});

describe('query viewer binding', () => {
  it('raises the render cap to All when the queried set exceeds the current limit', () => {
    expect(raiseViewerLimit(100, 150)).toBe('all');
    expect(raiseViewerLimit(100, 80)).toBe(100);
    expect(raiseViewerLimit('all', 400)).toBe('all');
  });

  it('uses the retrieve subgraph instead of a truncated full-KG dump', () => {
    const result: AgentChatResponse = {
      status: 'answered',
      answer: 'ok',
      sufficient: true,
      node_ids: ['a', 'b'],
      confidence: 1,
      rounds: [],
      graph: {
        source_path: 'query:dual',
        nodes: [
          { id: 'a', label: 'A', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
          { id: 'b', label: 'B', type: 'Beamline', description: '', graph_id: 'bl1101' },
          { id: 'c', label: 'C', type: 'Material', description: '' },
        ],
        edges: [
          { source: 'a', predicate: 'rel:related_to', target: 'b' },
          { source: 'a', predicate: 'rel:related_to', target: 'c' },
        ],
      },
      workdir: 'runs/session',
    };

    const queried = queryGraphFromResult(result);
    expect(queried?.nodes.map(node => node.id)).toEqual(['a', 'b']);
    expect(queried?.edges).toEqual([
      { source: 'a', predicate: 'rel:related_to', target: 'b' },
    ]);
  });

  it('keeps Tiled identity neighbors instead of inducing only hit ids', () => {
    const result: AgentChatResponse = {
      status: 'answered',
      answer: 'ok',
      sufficient: true,
      node_ids: ['beamline:ESAF-2026-00043'],
      confidence: 1,
      rounds: [],
      graph: {
        source_path: 'tiled://graphql',
        nodes: [
          { id: 'beamline:ESAF-2026-00043', label: 'ESAF 2026-00043', type: 'ESAF', description: '', graph_id: 'tiled' },
          { id: 'beamline:Proposal-P202600043-01', label: 'Proposal P202600043-01', type: 'Proposal', description: '', graph_id: 'tiled' },
          { id: 'matkg:P3HT', label: 'P3HT', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        ],
        edges: [
          { source: 'beamline:ESAF-2026-00043', predicate: 'rel:hasProposal', target: 'beamline:Proposal-P202600043-01' },
        ],
      },
      workdir: 'runs/session',
    };
    const queried = queryGraphFromResult(result);
    expect(queried?.nodes.map(node => node.id).sort()).toEqual([
      'beamline:ESAF-2026-00043',
      'beamline:Proposal-P202600043-01',
    ]);
    expect(queried?.edges).toEqual([
      { source: 'beamline:ESAF-2026-00043', predicate: 'rel:hasProposal', target: 'beamline:Proposal-P202600043-01' },
    ]);
  });
});
