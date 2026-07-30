import { describe, expect, it } from 'vitest';
import type { ChatMessage } from './chatSessions';
import {
  publicationSectionHeading,
  publicationsBlockText,
} from './ChatSidebar';

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
