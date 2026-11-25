import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import invalid from '../__fixtures__/schema_save_invalid.json';
import reportInvalid from '../__fixtures__/schema_report_invalid.json';
import { SchemaPanel, applySchemaName, downloadName, suggestName } from './SchemaPanel';
import { MAX_SCHEMA_BYTES } from './schemaApi';
import {
  defaultRoutes, diffFixture, generateFixture, previewEstimateFixture, previewResultFixture, reportOk, saveFixture, storedFixture,
  stubFetch, wrapper, type Call, type Route,
} from './testRoutes';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

let routes: Record<string, Route>;
let calls: Call[];
function mount(props: Parameters<typeof SchemaPanel>[0] = {}) {
  calls = stubFetch(routes);
  const Wrapper = wrapper();
  return render(<Wrapper><SchemaPanel {...props} /></Wrapper>);
}
const textarea = () => screen.getByTestId('schema-textarea') as HTMLTextAreaElement;
const post = (path: string) => calls.filter(c => c.method === 'POST' && c.path === path);

beforeEach(() => { routes = defaultRoutes(); });
afterEach(() => vi.unstubAllGlobals());

describe('generate', () => {
  it('sends the description with the form picker model, then shows the draft in the editor and saves nothing', async () => {
    routes['POST /v1/library/schemas/generate'] = { status: 200, body: generateFixture };
    mount();
    await screen.findByRole('option', { name: 'm2' });
    await userEvent.type(screen.getByLabelText('What should the schema cover?'), 'armoured catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(textarea().value).toContain('name: catfish'));
    expect(post('/v1/library/schemas/generate')[0].body).toEqual({ description: 'armoured catfish', model: 'lbl/cborg-chat' });
    expect(screen.getByTestId('generate-result')).toHaveTextContent('Draft by cborg / lbl/cborg-chat, 1 repair round');
    expect(screen.getByTestId('generate-result')).toHaveTextContent('It is not saved.');
    expect(screen.getByLabelText('Schema name')).toHaveValue('catfish');
    expect(post('/v1/library/schemas')).toHaveLength(0);
  });

  it('sends backend ollama for an Ollama model', async () => {
    routes['GET /v1/library/models'] = { status: 200, body: { available: false, models: [], source: 'none', error: null, backend: null, ollama: { state: 'ok', models: ['llama3'], error: null } } };
    routes['POST /v1/library/schemas/generate'] = { status: 200, body: generateFixture };
    mount();
    await screen.findByRole('option', { name: 'Ollama: llama3' });
    await userEvent.type(screen.getByLabelText('What should the schema cover?'), 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(post('/v1/library/schemas/generate')).toHaveLength(1));
    expect(post('/v1/library/schemas/generate')[0].body).toEqual({ description: 'x', model: 'llama3', backend: 'ollama' });
  });

  it.each([
    [501, { code: 'unsupported_capability', message: 'no LLM backend is configured for schema generation', retryable: false }, 'no LLM backend is configured'],
    [403, { code: 'denied', message: 'model not free', retryable: false, reason: 'model_not_free' }, 'not on the free list'],
    [503, { code: 'unavailable', message: 'x', retryable: true, reason: 'model_timeout' }, 'did not answer in time'],
    [422, { code: 'invalid_input', message: 'x', retryable: false, reason: 'body_too_large' }, 'too large'],
    [503, { code: 'unavailable', message: 'x', retryable: true, reason: 'cborg_ip_not_authorized' }, 'network address'],
  ])('shows status %i in plain words', async (status, body, words) => {
    routes['POST /v1/library/schemas/generate'] = { status, body };
    mount();
    await screen.findByRole('option', { name: 'm2' });
    await userEvent.type(screen.getByLabelText('What should the schema cover?'), 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByTestId('generate-error')).toHaveTextContent(words);
    expect(textarea().value).toBe('');
  });

  it('shows progress while waiting and lets every attempt be shown', async () => {
    let release: (() => void) | undefined;
    routes['POST /v1/library/schemas/generate'] = () => new Promise(resolve => { release = () => resolve({ status: 200, body: generateFixture }); });
    mount();
    await screen.findByRole('option', { name: 'm2' });
    await userEvent.type(screen.getByLabelText('What should the schema cover?'), 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByTestId('generate-status')).toHaveTextContent('Asking lbl/cborg-chat');
    release?.();
    expect(await screen.findByText('Attempt 1: 2 problem(s)')).toBeInTheDocument();
    expect(screen.getByText('Attempt 2: passes')).toBeInTheDocument();
  });

  it('keeps Send disabled for an empty description', async () => {
    mount();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});

describe('editing, validation and insert-core', () => {
  it('validates pasted text and lists problems by line, marking the gutter', async () => {
    routes['POST /v1/library/schemas/validate'] = { status: 200, body: reportInvalid };
    mount();
    fireEvent.change(textarea(), { target: { value: Array.from({ length: 45 }, (_, i) => `k${i}: v`).join('\n') } });
    await waitFor(() => expect(screen.getByTestId('problem-list')).toBeInTheDocument(), { timeout: 3000 });
    const marked = [...screen.getByTestId('schema-gutter').querySelectorAll('[data-problem="true"]')].map(e => e.getAttribute('data-line'));
    expect(marked).toEqual(['2', '43']);
    await userEvent.click(within(screen.getByTestId('problem-list')).getByText(/Line 2, column 7/));
    expect(textarea().value.slice(textarea().selectionStart, textarea().selectionEnd)).toBe('k1: v');
  });

  it('the insert-core button replaces the text with the server text', async () => {
    routes['POST /v1/library/schemas/insert-core'] = { status: 200, body: { text: 'with: core\n' } };
    mount();
    fireEvent.change(textarea(), { target: { value: 'a: 1\n' } });
    await userEvent.click(screen.getByRole('button', { name: 'Insert required core' }));
    await waitFor(() => expect(textarea().value).toBe('with: core\n'));
    expect(post('/v1/library/schemas/insert-core')[0].body).toEqual({ text: 'a: 1\n' });
  });
});

describe('the schema name', () => {
  const draft = 'id: https://example.org/user/new_schema\nname: new_schema\ndescription: x\nclasses:\n  Thing:\n    name: kept\n';

  it('is never suggested from a placeholder name', () => {
    expect(suggestName(draft)).toBe('');
    expect(suggestName('name: catfish\n')).toBe('catfish');
  });

  it('is written into the text: name, and id when it is the example.org placeholder; nothing else changes', () => {
    expect(applySchemaName(draft, 'catfish')).toBe(
      'id: https://example.org/user/catfish\nname: catfish\ndescription: x\nclasses:\n  Thing:\n    name: kept\n');
    // The person's own id URL stays; a text without a name gets one first; a quoted name is replaced.
    expect(applySchemaName('id: https://lab.example/s\nname: "old"\n', 'fish')).toBe('id: https://lab.example/s\nname: fish\n');
    expect(applySchemaName('id: https://example.org/user/x\nclasses: {}\n', 'fish')).toBe('name: fish\nid: https://example.org/user/fish\nclasses: {}\n');
    expect(applySchemaName('name: fish\n', 'fish')).toBe('name: fish\n');
  });

  it('saving under a different name rewrites the text first, so the saved name and the text agree', async () => {
    routes['POST /v1/library/schemas'] = { status: 200, body: saveFixture };
    mount();
    fireEvent.change(textarea(), { target: { value: 'id: https://example.org/user/new_schema\nname: new_schema\n' } });
    await screen.findByText('No problems found.', undefined, { timeout: 3000 });
    expect(screen.getByLabelText('Schema name')).toHaveValue('');
    expect(screen.getByTestId('schema-name-needed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept and save' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Schema name'), 'catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Accept and save' }));
    await waitFor(() => expect(post('/v1/library/schemas')).toHaveLength(1));
    expect(post('/v1/library/schemas')[0].body).toEqual({ name: 'catfish', text: 'id: https://example.org/user/catfish\nname: catfish\n' });
    await waitFor(() => expect(textarea().value).toBe('id: https://example.org/user/catfish\nname: catfish\n'));
    expect(screen.getByTestId('schema-notice')).toHaveTextContent('the text now says name: catfish');
  });
});

describe('save', () => {
  async function typeValid(name = 'catfish') {
    fireEvent.change(textarea(), { target: { value: 'name: catfish\n' } });
    await screen.findByText('No problems found.', undefined, { timeout: 3000 });
    const field = screen.getByLabelText('Schema name');
    await userEvent.clear(field);
    if (name) await userEvent.type(field, name);
  }

  it('saves a valid text under a valid name, shows the new id and tells the caller', async () => {
    routes['POST /v1/library/schemas'] = { status: 200, body: saveFixture };
    const onSaved = vi.fn();
    mount({ onSaved });
    await typeValid();
    await userEvent.click(screen.getByRole('button', { name: 'Accept and save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('user/catfish@v1'));
    expect(post('/v1/library/schemas')[0].body).toEqual({ name: 'catfish', text: 'name: catfish\n' });
    expect(screen.getByTestId('schema-notice')).toHaveTextContent('Saved as user/catfish@v1');
    await waitFor(() => expect(calls.filter(c => c.method === 'GET' && c.path === '/v1/library/schemas/user').length).toBeGreaterThan(1));
  });

  it('refuses an invalid name before any request', async () => {
    mount();
    await typeValid('Bad Name');
    expect(screen.getByRole('button', { name: 'Accept and save' })).toBeDisabled();
    expect(screen.getByLabelText('Schema name')).toHaveAttribute('aria-invalid', 'true');
    expect(post('/v1/library/schemas')).toHaveLength(0);
  });

  it('keeps Save disabled while the text does not validate', async () => {
    routes['POST /v1/library/schemas/validate'] = { status: 200, body: reportInvalid };
    mount();
    fireEvent.change(textarea(), { target: { value: 'x: 1\n' } });
    await screen.findByTestId('problem-list', undefined, { timeout: 3000 });
    await userEvent.type(screen.getByLabelText('Schema name'), 'catfish');
    expect(screen.getByRole('button', { name: 'Accept and save' })).toBeDisabled();
  });

  it('shows the 422 report with its lines and selects the line on click', async () => {
    routes['POST /v1/library/schemas'] = { status: 422, body: invalid };
    mount();
    await typeValid();
    await userEvent.click(screen.getByRole('button', { name: 'Accept and save' }));
    const list = await screen.findByTestId('save-problems');
    expect(list).toHaveTextContent('Line 2:');
    expect(list).toHaveTextContent('Line 43:');
    expect(screen.getByTestId('schema-error')).toHaveTextContent('does not validate');
  });
});

describe('versions, diff and delete', () => {
  it('lists versions and opens one in the editor with the name filled', async () => {
    routes['GET /v1/library/schemas/user/catfish@v2'] = { status: 200, body: storedFixture };
    mount();
    expect(await screen.findByTestId('schema-row-catfish')).toHaveTextContent('user schema');
    await userEvent.click(screen.getByRole('button', { name: 'Open user/catfish@v2 in the editor' }));
    await waitFor(() => expect(textarea().value).toContain('name: catfish'));
    expect(screen.getByLabelText('Schema name')).toHaveValue('catfish');
    expect(screen.getByTestId('schema-notice')).toHaveTextContent('Saving creates a new version');
  });

  it('shows a diff of two versions with added and removed lines marked beyond colour', async () => {
    routes['GET /v1/library/schemas/user/catfish/diff'] = (_b, url) => {
      expect(url.search).toBe('?from=1&to=2');
      return { status: 200, body: diffFixture };
    };
    mount();
    await screen.findByTestId('schema-compare');
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }));
    const diff = await screen.findByTestId('schema-diff');
    expect(diff).toHaveTextContent('1 line added, 1 removed');
    const kinds = [...diff.querySelectorAll('[data-kind]')].map(e => e.getAttribute('data-kind'));
    expect(kinds).toEqual(['header', 'header', 'hunk', 'context', 'removed', 'added']);
    expect(diff.querySelector('[data-kind="removed"]')?.textContent).toContain('removed: -');
  });

  it('confirms before deleting, then deletes and refreshes', async () => {
    routes['DELETE /v1/library/schemas/user/catfish@v1'] = { status: 200, body: { deleted: 'user/catfish@v1' } };
    mount();
    await screen.findByTestId('schema-row-catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Delete user/catfish@v1' }));
    expect(calls.some(c => c.method === 'DELETE')).toBe(false);
    await userEvent.click(await screen.findByRole('button', { name: 'Delete version' }));
    await waitFor(() => expect(calls.some(c => c.method === 'DELETE')).toBe(true));
  });

  it('says a version in use cannot be deleted, in plain words', async () => {
    routes['DELETE /v1/library/schemas/user/catfish@v1'] = { status: 409, body: { code: 'conflict', message: 'a KG uses this schema version; it cannot be deleted', retryable: false, reason: 'schema_in_use' } };
    mount();
    await screen.findByTestId('schema-row-catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Delete user/catfish@v1' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Delete version' }));
    expect(await screen.findByTestId('delete-error')).toHaveTextContent('A KG was built on this version');
  });

  it('keeps a version when the confirmation is cancelled', async () => {
    mount();
    await screen.findByTestId('schema-row-catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Delete user/catfish@v1' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Keep it' }));
    expect(calls.some(c => c.method === 'DELETE')).toBe(false);
  });
});

describe('preview', () => {
  async function ready() {
    mount();
    fireEvent.change(textarea(), { target: { value: 'name: catfish\n' } });
    await screen.findByText('No problems found.', undefined, { timeout: 3000 });
    await screen.findByRole('option', { name: 'Title p1' });
    await userEvent.selectOptions(screen.getByLabelText('Paper'), 'p1');
  }

  it('asks for the estimate first (no confirm), then runs only after confirmation with the token', async () => {
    routes['POST /v1/library/schemas/preview'] = body =>
      (body as { confirm?: boolean }).confirm ? { status: 200, body: previewResultFixture } : { status: 200, body: previewEstimateFixture };
    await ready();
    await userEvent.click(screen.getByRole('button', { name: 'Preview (estimate first)' }));
    const estimate = await screen.findByTestId('preview-estimate');
    expect(estimate).toHaveTextContent('3 pages (at most 3)');
    expect(estimate).toHaveTextContent('about 4,800 tokens');
    expect(estimate).toHaveTextContent('cost unknown');
    expect(post('/v1/library/schemas/preview')).toHaveLength(1);
    expect(post('/v1/library/schemas/preview')[0].body).toEqual({ schema: 'name: catfish\n', paper_id: 'p1', model: 'lbl/cborg-chat' });
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and run the preview' }));
    expect(await screen.findByTestId('preview-summary')).toHaveTextContent('1 fell back to Thing'.replace('1 fell', '1 fell'));
    expect(post('/v1/library/schemas/preview')[1].body).toMatchObject({ confirm: true, confirmation: previewEstimateFixture.confirmation });
    const terms = screen.getAllByTestId('preview-term');
    expect(terms[0]).toHaveTextContent('lives_in Rio Negro (verified)');
    expect(terms[1]).toHaveTextContent('the model said Waterbody');
    expect(screen.getByTestId('preview-summary')).toHaveTextContent('1 became related_to and 0 stayed unverified');
    expect(screen.getByTestId('preview-note')).toHaveTextContent('Nothing was stored');
  });

  it('asks again when the paper changes after the estimate', async () => {
    routes['POST /v1/library/schemas/preview'] = { status: 200, body: previewEstimateFixture };
    await ready();
    await userEvent.click(screen.getByRole('button', { name: 'Preview (estimate first)' }));
    await screen.findByTestId('preview-estimate');
    await userEvent.selectOptions(screen.getByLabelText('Paper'), 'p2');
    expect(screen.queryByTestId('preview-estimate')).toBeNull();
    expect(screen.getByText(/estimate again/)).toBeInTheDocument();
  });

  it('shows a refusal in plain words', async () => {
    routes['POST /v1/library/schemas/preview'] = { status: 403, body: { code: 'denied', message: 'x', retryable: false } };
    await ready();
    await userEvent.click(screen.getByRole('button', { name: 'Preview (estimate first)' }));
    expect(await screen.findByTestId('preview-error')).toHaveTextContent('not on the free list');
  });
});

describe('download and upload', () => {
  it('downloads the editor text client side as <name>@vN.yaml without a server request', async () => {
    const create = vi.fn(() => 'blob:x');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download); });
    routes['GET /v1/library/schemas/user/catfish@v2'] = { status: 200, body: storedFixture };
    mount();
    await screen.findByTestId('schema-row-catfish');
    await userEvent.click(screen.getByRole('button', { name: 'Open user/catfish@v2 in the editor' }));
    await waitFor(() => expect(textarea().value).not.toBe(''));
    const before = calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(names).toEqual(['catfish@v2.yaml']);
    expect(create).toHaveBeenCalledTimes(1);
    expect(((create.mock.calls[0] as unknown[])[0] as Blob).type).toContain('text/yaml');
    expect(calls.length).toBe(before);
    click.mockRestore();
  });

  it('loads a .yaml file into the editor and validates it', async () => {
    mount();
    const file = new File(['name: fromfile\n'], 'x.yaml', { type: 'text/yaml' });
    await userEvent.upload(screen.getByTestId('schema-upload'), file);
    await waitFor(() => expect(textarea().value).toBe('name: fromfile\n'));
    await waitFor(() => expect(post('/v1/library/schemas/validate')).toHaveLength(1), { timeout: 3000 });
    expect(screen.getByLabelText('Schema name')).toHaveValue('fromfile');
  });

  it('refuses a file over the cap before reading it', async () => {
    mount();
    const big = new File([new Uint8Array(MAX_SCHEMA_BYTES + 1)], 'big.yaml');
    const read = vi.spyOn(FileReader.prototype, 'readAsText');
    await userEvent.upload(screen.getByTestId('schema-upload'), big);
    expect(await screen.findByTestId('schema-error')).toHaveTextContent('limit is 256 KiB');
    expect(read).not.toHaveBeenCalled();
    expect(textarea().value).toBe('');
    read.mockRestore();
  });

  it('refuses a file that is not .yaml or .yml', async () => {
    mount();
    await userEvent.upload(screen.getByTestId('schema-upload'), new File(['x'], 'x.txt'), { applyAccept: false });
    expect(await screen.findByTestId('schema-error')).toHaveTextContent('.yaml or .yml');
  });
});

describe('helpers', () => {
  it('suggests the name from the text and names downloads', () => {
    expect(suggestName('id: x\nname: catfish\n')).toBe('catfish');
    expect(suggestName('name: Bad Name\n')).toBe('');
    expect(downloadName('user/catfish@v3', '')).toBe('catfish@v3.yaml');
    expect(downloadName(null, 'eels')).toBe('eels@draft.yaml');
    expect(downloadName(null, 'Bad')).toBe('schema@draft.yaml');
  });
  it('is never saved to browser storage', async () => {
    const set = vi.spyOn(Storage.prototype, 'setItem');
    mount();
    fireEvent.change(textarea(), { target: { value: 'secret: text\n' } });
    expect(set).not.toHaveBeenCalled();
  });
  it('uses the shared fixture for a valid report', () => {
    expect(reportOk.ok).toBe(true);
  });
});
