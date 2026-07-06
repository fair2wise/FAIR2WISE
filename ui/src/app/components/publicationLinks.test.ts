import { describe, expect, it } from 'vitest';
import {
  buildPublicationSearchQuery,
  getPublicationLinks,
  parseCrossrefDoi,
} from './publicationLinks';

describe('publicationLinks', () => {
  it('parses Wiley-style slash-stripped DOI PDF filenames', () => {
    const publication = { source_paper: '10.1002aenm.201702831.pdf' };
    expect(parseCrossrefDoi(publication)).toBe('10.1002/aenm.201702831');
  });

  it('parses another Wiley-style filename', () => {
    const publication = { source_paper: '10.1002aenm.201800550.pdf' };
    expect(parseCrossrefDoi(publication)).toBe('10.1002/aenm.201800550');
  });

  it('still parses underscore-separated DOI PDF filenames', () => {
    const publication = { source_paper: '10.1002_aenm.201702831.pdf' };
    expect(parseCrossrefDoi(publication)).toBe('10.1002/aenm.201702831');
  });

  it('builds doi.org and Semantic Scholar links for Wiley-style filenames', () => {
    const links = getPublicationLinks({ source_paper: '10.1002aenm.201702831.pdf' });
    expect(links.primaryKind).toBe('doi');
    expect(links.primaryUrl).toBe('https://doi.org/10.1002/aenm.201702831');
    expect(links.directLabel).toBe('doi.org/10.1002/aenm.201702831');
    expect(links.searchUrl).toContain('semanticscholar.org/search?q=10.1002%2Faenm.201702831');
  });

  it('uses parsed DOI as Semantic Scholar query when metadata is sparse', () => {
    expect(buildPublicationSearchQuery({ source_paper: '10.1002aenm.201800550.pdf' }))
      .toBe('10.1002/aenm.201800550');
  });
});
