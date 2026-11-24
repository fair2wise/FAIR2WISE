import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { HarvestCandidate } from '../../harvestApi';
import { HarvestResults, type HarvestResultsProps } from './HarvestResults';

const cand = (id: string, over: Partial<HarvestCandidate> = {}): HarvestCandidate => ({
  id, title: `Paper ${id}`, year: 2020, doi: `10.1/${id}`, arxiv_id: null, source: 'openalex', tier: 'A', score: 0.9,
  status: 'downloaded', landing_url: `https://doi.org/10.1/${id}`, pdf_url: null, relpath: null, detail: null, ...over,
});
const pdf = () => new File(['%PDF'], 'saved.pdf', { type: 'application/pdf' });

function setup(candidates: HarvestCandidate[], over: Partial<HarvestResultsProps> = {}) {
  const props: HarvestResultsProps = {
    candidates, busyId: null, onFile: vi.fn(), onDismiss: vi.fn(), onRejected: vi.fn(), ...over,
  };
  render(<HarvestResults {...props} />);
  return props;
}

describe('HarvestResults', () => {
  it('shows an empty state without candidates', () => {
    setup([]);
    expect(screen.getByText('No papers found yet.')).toBeInTheDocument();
  });

  it('puts settled outcomes in the table and every paper that needs a PDF in the queue', () => {
    setup([
      cand('d', { relpath: 'rsoxs/2020/d.pdf' }),
      cand('s', { status: 'skipped' }),
      cand('f', { status: 'failed', detail: 'HTTP 403', year: null, doi: null, arxiv_id: '2101.00001' }),
      cand('p', { status: 'paywalled' }),
    ]);
    const table = screen.getByRole('table', { name: 'Harvest results' });
    expect(within(table).getByText('Paper d')).toHaveAttribute('title', 'rsoxs/2020/d.pdf');
    expect(within(table).getByText('Downloaded')).toBeInTheDocument();
    expect(within(table).queryByText('Paper p')).not.toBeInTheDocument();
    expect(within(table).queryByText('Paper f')).not.toBeInTheDocument();
    const queue = screen.getByRole('list', { name: 'Papers needing a PDF' });
    expect(within(queue).getByText('Paper p')).toBeInTheDocument();
    expect(within(queue).getByText('Paper f')).toBeInTheDocument();
    expect(within(queue).getByText('HTTP 403')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Needs a PDF (2 left)' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Results (2)' })).toBeInTheDocument();
  });

  it('lists a paper blocked by network policy in the queue with the host named, and a failed fetch too', () => {
    setup([
      cand('b', { status: 'blocked', detail: 'open access, blocked by network policy (private_address) at europepmc.org' }),
      cand('p', { status: 'paywalled', detail: 'not open access: no open-access copy is listed' }),
      cand('f', { status: 'failed', detail: 'failed:http_403' }),
    ]);
    const queue = screen.getByRole('list', { name: 'Papers needing a PDF' });
    expect(within(queue).getAllByRole('listitem')).toHaveLength(3);
    expect(within(queue).getByText('Blocked by network policy')).toBeInTheDocument();
    expect(within(queue).getByText(/blocked by network policy \(private_address\) at europepmc\.org/)).toBeInTheDocument();
    expect(within(queue).getByText('Paywalled')).toBeInTheDocument();
    expect(within(queue).getByText('Failed')).toBeInTheDocument();
    for (const id of ['b', 'p', 'f']) expect(screen.getByTestId(`harvest-file-${id}`)).toBeInTheDocument();
    expect(screen.getByText('No other results.')).toBeInTheDocument();
  });

  it('shows the rights note on the queue', () => {
    setup([cand('p', { status: 'paywalled' })]);
    expect(screen.getByTestId('harvest-rights-note')).toHaveTextContent(
      'Text-mining rights for subscription content are the operator\u2019s responsibility to confirm',
    );
  });

  it('badges a filed paper that a person supplied', () => {
    setup([cand('s', { status: 'filed', provenance: 'user_supplied_confirmed', supplied_by: 'local-operator' }), cand('d')]);
    const table = screen.getByRole('table', { name: 'Harvest results' });
    expect(within(table).getAllByTestId('supplied-badge')).toHaveLength(1);
  });

  it('lists found and downloading papers as they arrive', () => {
    setup([cand('a', { status: 'found' }), cand('b', { status: 'downloading' }), cand('c', { status: 'skipped' })]);
    const table = screen.getByRole('table', { name: 'Harvest results' });
    expect(within(table).getByText('Found')).toBeInTheDocument();
    expect(within(table).getByText('Downloading')).toBeInTheDocument();
    expect(within(table).getByText('Skipped')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Results (3)' })).toBeInTheDocument();
  });

  it('says so when the queue or the table is empty', () => {
    setup([cand('d')]);
    expect(screen.getByText('Nothing is waiting for a PDF.')).toBeInTheDocument();
    setup([cand('p', { status: 'paywalled' })]);
    expect(screen.getByText('No other results.')).toBeInTheDocument();
  });

  it.each(['paywalled', 'blocked', 'failed'] as const)('opens a %s paper at the publisher in a new tab without leaking the opener', status => {
    setup([cand('p', { status })]);
    const link = screen.getByRole('link', { name: 'Open at publisher: Paper p' });
    expect(link).toHaveTextContent('Open at publisher');
    expect(link).toHaveAttribute('href', 'https://doi.org/10.1/p');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('prefers the server\'s open_url (the library proxy form) over the landing page', () => {
    setup([cand('p', { status: 'paywalled', open_url: 'https://ezproxy.example.edu/login?url=https://doi.org/10.1/p' })]);
    expect(screen.getByRole('link', { name: 'Open at publisher: Paper p' }))
      .toHaveAttribute('href', 'https://ezproxy.example.edu/login?url=https://doi.org/10.1/p');
  });

  it('falls back to the landing page when open_url is absent or unusable', () => {
    setup([cand('p', { status: 'paywalled', open_url: 'javascript:alert(1)' })]);
    expect(screen.getByRole('link', { name: 'Open at publisher: Paper p' })).toHaveAttribute('href', 'https://doi.org/10.1/p');
  });

  it.each([null, 'javascript:alert(1)'])('shows no link for an unusable landing URL (%s)', landing => {
    setup([cand('p', { status: 'paywalled', landing_url: landing })]);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('No publisher link')).toBeInTheDocument();
  });

  it('files a chosen PDF', async () => {
    const props = setup([cand('p', { status: 'paywalled' })]);
    const file = pdf();
    await userEvent.upload(screen.getByTestId('harvest-file-p'), file);
    expect(props.onFile).toHaveBeenCalledWith(expect.objectContaining({ id: 'p' }), file);
  });

  it('files a dropped PDF and rejects a dropped non-PDF', () => {
    const props = setup([cand('p', { status: 'paywalled' })]);
    const item = screen.getByText('Paper p').closest('li') as HTMLElement;
    fireEvent.drop(item, { dataTransfer: { files: [new File(['x'], 'n.txt', { type: 'text/plain' })] } });
    expect(props.onRejected).toHaveBeenCalledWith(['n.txt']);
    expect(props.onFile).not.toHaveBeenCalled();
    fireEvent.drop(item, { dataTransfer: { files: [pdf()] } });
    expect(props.onFile).toHaveBeenCalledOnce();
  });

  it('dismisses a candidate', async () => {
    const props = setup([cand('p', { status: 'paywalled' })]);
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss Paper p' }));
    expect(props.onDismiss).toHaveBeenCalledWith(expect.objectContaining({ id: 'p' }));
  });

  it('disables the busy candidate\'s controls and ignores a drop on it', () => {
    const props = setup([cand('p', { status: 'paywalled' }), cand('q', { status: 'paywalled' })], { busyId: 'p' });
    expect(screen.getByRole('button', { name: 'Dismiss Paper p' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Choose PDF for Paper p' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dismiss Paper q' })).toBeEnabled();
    fireEvent.drop(screen.getByText('Paper p').closest('li') as HTMLElement, { dataTransfer: { files: [pdf()] } });
    expect(props.onFile).not.toHaveBeenCalled();
  });
});
