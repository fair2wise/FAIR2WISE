import { describe, expect, it } from 'vitest';
import { nodeCorpus, sourceForChatNode } from './nodeDetailSource';
import { parseGraphSourceListing } from './graphSourcesApi';

const row = (id: string, graph_id: string, kind = id.startsWith('tiled:') ? 'tiled' : 'json', available = true) =>
  ({ id, kind, label: id, read_only: true, available, graph_id });

const listing = parseGraphSourceListing({
  sources: [
    row('json:rsoxs_v4', 'rsoxs_v4'),
    row('json:rsoxs/v3', 'rsoxs'),
    row('json:rsoxs/v4', 'rsoxs'),
    row('tiled:rsoxs', 'rsoxs'),
    row('json:qwen3_235b_580papers/v1', 'qwen3_235b_580papers'),
    row('json:gone/v1', 'gone', 'json', false),
  ],
  tiled: { status: 'ok' },
});

describe('sourceForChatNode', () => {
  it('names the corpus from `corpus`, else `graph_id`', () => {
    expect(nodeCorpus({ corpus: 'a', graph_id: 'b' })).toBe('a');
    expect(nodeCorpus({ graph_id: ' b ' })).toBe('b');
    expect(nodeCorpus({})).toBeNull();
  });

  it('prefers the live Tiled record, then the newest release, then a flat file', () => {
    expect(sourceForChatNode({ graph_id: 'rsoxs' }, null, listing)).toBe('tiled:rsoxs');
    expect(sourceForChatNode({ graph_id: 'qwen3_235b_580papers' }, null, listing)).toBe('json:qwen3_235b_580papers/v1');
    expect(sourceForChatNode({ graph_id: 'rsoxs_v4' }, null, listing)).toBe('json:rsoxs_v4');
    const noTiled = parseGraphSourceListing({ sources: listing.sources.filter(s => s.kind === 'json'), tiled: { status: 'not_offered' } });
    expect(sourceForChatNode({ graph_id: 'rsoxs' }, null, noTiled)).toBe('json:rsoxs/v4');
  });

  it('keeps the source the viewer shows when it holds that corpus', () => {
    expect(sourceForChatNode({ graph_id: 'rsoxs' }, 'json:rsoxs/v3', listing)).toBe('json:rsoxs/v3');
  });

  it('returns null without a corpus, a listing, a listed match or an available source', () => {
    expect(sourceForChatNode({}, null, listing)).toBeNull();
    expect(sourceForChatNode({ graph_id: 'rsoxs' }, null, undefined)).toBeNull();
    expect(sourceForChatNode({ graph_id: 'unlisted' }, null, listing)).toBeNull();
    expect(sourceForChatNode({ graph_id: 'gone' }, null, listing)).toBeNull();
  });
});
