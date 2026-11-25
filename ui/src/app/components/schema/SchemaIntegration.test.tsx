import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import choice from '../__fixtures__/schema_choice_user.json';
import sizes from '../__fixtures__/schema_prompt_sizes.json';
import condensed from '../__fixtures__/schema_prompt_condensed.json';
import saveFixture from '../__fixtures__/schema_save.json';
import { ExtractionIdleForm } from '../extraction/ExtractionIdleForm';
import { ExtractionRunView } from '../extraction/ExtractionRunView';
import type { JobInfo, JobRunSummary } from '../extractionApi';
import { SchemaDiff, diffLineKind } from './SchemaDiff';
import { SchemaPanelButton } from './SchemaPanelButton';
import { defaultRoutes, paper, stubFetch, wrapper, type Call, type Route } from './testRoutes';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

let routes: Record<string, Route>;
let calls: Call[];
const shipped = { name: 'matkg_schema@0.3.1', label: 'MatKG', description: null, version: '0.3.1' };

beforeEach(() => {
  routes = {
    ...defaultRoutes(),
    'GET /v1/jobs': { status: 200, body: { jobs: [] } },
    'GET /v1/library/kgs': { status: 200, body: { bundles: [], sources: [] } },
    'GET /v1/library/papers': { status: 200, body: { papers: [paper('p1')], total: 1 } },
    'POST /v1/library/estimate': { status: 200, body: { pages: 5, paper_count: 1, model: 'lbl/cborg-chat', estimated_tokens: 10, estimated_cost_usd: 0, confirmation: 'tok', schema_prompt: sizes } },
  };
});
afterEach(() => vi.unstubAllGlobals());

function mountForm() {
  calls = stubFetch(routes);
  const Wrapper = wrapper();
  return render(<MemoryRouter><Wrapper><ExtractionIdleForm onStarted={() => {}} /></Wrapper></MemoryRouter>);
}

describe('capability gating', () => {
  it.each([404, 403])('hides the panel button when the schema routes answer %i (ALS or no capability)', async status => {
    routes['GET /v1/library/schemas/user'] = { status, body: {} };
    mountForm();
    await screen.findByLabelText('Schema');
    await waitFor(() => expect(calls.some(c => c.path === '/v1/library/schemas/user')).toBe(true));
    expect(screen.queryByTestId('open-schema-panel')).toBeNull();
  });

  it('hides the button when capabilities name an ALS role even if the probe answered', async () => {
    routes['GET /v1/capabilities'] = { status: 200, body: { role: 'admin', profile: 'als' } };
    mountForm();
    await screen.findByLabelText('Schema');
    await waitFor(() => expect(calls.some(c => c.path === '/v1/capabilities')).toBe(true));
    await new Promise(r => setTimeout(r, 50));
    expect(screen.queryByTestId('open-schema-panel')).toBeNull();
  });

  it('shows the button on the local profile and opens the panel in a dialog', async () => {
    calls = stubFetch(routes);
    const Wrapper = wrapper();
    render(<Wrapper><SchemaPanelButton /></Wrapper>);
    await userEvent.click(await screen.findByTestId('open-schema-panel'));
    expect(await screen.findByTestId('schema-panel')).toBeInTheDocument();
  });
});

describe('the chooser', () => {
  it('marks a user schema as a user schema and selects the saved one on the form', async () => {
    routes['GET /v1/library/schemas'] = { status: 200, body: { schemas: [shipped] } };
    mountForm();
    const select = (await screen.findByLabelText('Schema')) as HTMLSelectElement;
    expect(within(select).queryByRole('group')).toBeNull();
    // after a save the server lists the new schema: the form's list is refreshed and the choice selected
    routes['GET /v1/library/schemas'] = { status: 200, body: { schemas: [shipped, choice] } };
    routes['POST /v1/library/schemas'] = { status: 200, body: saveFixture };
    await userEvent.click(await screen.findByTestId('open-schema-panel'));
    const dialog = await screen.findByTestId('schema-dialog');
    const editor = within(dialog).getByTestId('schema-textarea');
    await userEvent.click(editor);
    await userEvent.paste('name: catfish\n');
    await within(dialog).findByText('No problems found.', undefined, { timeout: 3000 });
    await userEvent.type(within(dialog).getByLabelText('Schema name'), 'catfish');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Accept and save' }));
    await waitFor(() => expect(within(dialog).getByTestId('schema-notice')).toBeInTheDocument());
    await waitFor(() => expect(select.value).toBe('user/catfish@v1'));
    const group = within(select).getByRole('group', { name: 'Your schemas (user schema)', hidden: true });
    expect(within(group).getByRole('option', { name: 'Loricariid catfish (user schema, v1)', hidden: true })).toBeInTheDocument();
    expect(await screen.findByTestId('user-schema-note', undefined)).toHaveTextContent('user schema');
  });

  it('states when the prompt of a user schema will be condensed, using the estimate and the chosen backend', async () => {
    routes['GET /v1/library/schemas'] = { status: 200, body: { schemas: [choice] } };
    const user = userEvent.setup();
    mountForm();
    await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('lbl/cborg-chat'));
    await user.click(await screen.findByRole('checkbox', { name: /Title p1/ }));
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    const note = await screen.findByTestId('schema-prompt-note');
    expect(note).toHaveTextContent('14,200 characters');
    expect(note).toHaveTextContent('over the 12,000 allowed for CBORG');
    expect(note).toHaveTextContent('condensed view');
    expect(calls.find(c => c.path === '/v1/library/estimate')?.body).toMatchObject({ schema: 'user/catfish@v1' });
  });

  it('says nothing when the prompt fits', async () => {
    routes['GET /v1/library/schemas'] = { status: 200, body: { schemas: [choice] } };
    routes['POST /v1/library/estimate'] = { status: 200, body: { pages: 1, model: 'm', estimated_tokens: 1, estimated_cost_usd: 0, confirmation: 't', schema_prompt: { ...sizes, full_chars: 900 } } };
    const user = userEvent.setup();
    mountForm();
    await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('lbl/cborg-chat'));
    await user.click(await screen.findByRole('checkbox', { name: /Title p1/ }));
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await screen.findByTestId('estimate-summary');
    expect(screen.queryByTestId('schema-prompt-note')).toBeNull();
  });
});

describe('the run view', () => {
  const job: JobInfo = { id: 'j1', kind: 'library_extract', state: 'succeeded', progress: { done: 1, total: 1 }, error: null, created_at: '', updated_at: '' };
  const summary = (extra: Partial<JobRunSummary>): JobRunSummary => ({
    outcome: 'succeeded', state: 'succeeded', started_at: null, finished_at: null, elapsed_seconds: 1,
    stage: { name: 'run', status: 'done', phase: 'finished' }, current_paper: null,
    papers: { total: 1, done: 1, failed: 0, skipped: 0 }, growth: {}, counters: {}, params: { bundle: 'kg1' }, error: null,
    logs: { kept: 0, total: 0, cap: 10 }, ...extra,
  });
  const view = (s: JobRunSummary) => render(
    <MemoryRouter><ExtractionRunView job={{ ...job, summary: s }} kgName="kg1" lines={[]} cancelling={false} exporting={false} onCancel={() => {}} onExport={() => {}} onNew={() => {}} /></MemoryRouter>);

  it('shows the condensed-prompt warning with the sizes', () => {
    view(summary({ schema_prompt_condensed: condensed }));
    const note = screen.getByTestId('schema-prompt-condensed');
    expect(note).toHaveTextContent('14,200 characters in full');
    expect(note).toHaveTextContent('11,950 sent');
    expect(note).toHaveTextContent('budget 12,000');
  });

  it('shows nothing when the prompt was not condensed', () => {
    view(summary({ schema_prompt_condensed: null }));
    expect(screen.queryByTestId('schema-prompt-condensed')).toBeNull();
  });
});

describe('SchemaDiff', () => {
  it('classifies unified diff lines', () => {
    expect(['+++ b', '--- a', '@@ -1 +1 @@', '+x', '-y', ' z'].map(diffLineKind)).toEqual(['header', 'header', 'hunk', 'added', 'removed', 'context']);
  });
  it('says identical versions are identical', () => {
    render(<SchemaDiff diff="" from={1} to={2} />);
    expect(screen.getByTestId('diff-empty')).toHaveTextContent('identical');
  });
  it('renders diff text as text', () => {
    render(<SchemaDiff diff={'+<img src=x onerror=alert(1)>\n'} from={1} to={2} />);
    expect(document.querySelector('img')).toBeNull();
  });
});
