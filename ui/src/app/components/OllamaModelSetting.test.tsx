import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OllamaModelSetting } from './OllamaModelSetting';

let body: { status: number; json: unknown };
let urls: string[];

beforeEach(() => {
  urls = [];
  body = { status: 200, json: { state: 'ok', models: ['llama3:8b', 'qwen2.5:7b'], error: null } };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    urls.push(new URL(String(input), 'http://x').pathname);
    return new Response(JSON.stringify(body.json), { status: body.status });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function setup(props: Partial<React.ComponentProps<typeof OllamaModelSetting>> = {}) {
  const onChange = vi.fn();
  const view = render(<OllamaModelSetting open value="llama3:8b" onChange={onChange} placeholder="deepseek-r1:70b" {...props} />);
  return { onChange, ...view };
}

describe('OllamaModelSetting', () => {
  it('fills the dropdown from the API and selects through it', async () => {
    const { onChange } = setup();
    const select = await screen.findByLabelText('Ollama model');
    await waitFor(() => expect(screen.getByRole('option', { name: 'qwen2.5:7b' })).toBeInTheDocument());
    expect(select).toBeEnabled();
    fireEvent.change(select, { target: { value: 'qwen2.5:7b' } });
    expect(onChange).toHaveBeenCalledWith('qwen2.5:7b');
    expect(screen.getByTestId('ollama-model-source')).toHaveTextContent('2 models on the Ollama server');
    expect(new Set(urls)).toEqual(new Set(['/v1/ollama/models'])); // the browser only ever calls the API
  });

  it('asks again each time the panel opens, the dropdown is focused and refresh is pressed', async () => {
    const { rerender } = setup({ open: false });
    expect(urls).toEqual([]);
    rerender(<OllamaModelSetting open value="llama3:8b" onChange={() => {}} placeholder="p" />);
    await waitFor(() => expect(urls).toHaveLength(1));
    await screen.findByRole('option', { name: 'qwen2.5:7b' });
    fireEvent.focus(screen.getByLabelText('Ollama model'));
    await waitFor(() => expect(urls).toHaveLength(2));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Ollama models' }));
    await waitFor(() => expect(urls).toHaveLength(3));
    rerender(<OllamaModelSetting open={false} value="llama3:8b" onChange={() => {}} placeholder="p" />);
    rerender(<OllamaModelSetting open value="llama3:8b" onChange={() => {}} placeholder="p" />);
    await waitFor(() => expect(urls).toHaveLength(4)); // a re-open lists again
  });

  it('keeps the current setting selectable when the server does not list it', async () => {
    setup({ value: 'gone-model:1b' });
    const option = await screen.findByRole('option', { name: 'gone-model:1b (not on the Ollama server)' });
    expect(option).toHaveValue('gone-model:1b');
  });

  it.each([
    ['unset', 'OLLAMA_URL is unset'],
    ['unreachable', 'could not reach Ollama'],
    ['empty', 'holds no models'],
  ])('shows a clear state when Ollama is %s and does not offer a choice', async (state, text) => {
    body = { status: 200, json: { state, models: [], error: 'x' } };
    setup();
    await waitFor(() => expect(screen.getByTestId('ollama-model-source')).toHaveTextContent(text));
    expect(screen.getByLabelText('Ollama model')).toBeDisabled();
  });

  it('falls back to the plain text field when the server has no such route', async () => {
    body = { status: 404, json: {} };
    const { onChange } = setup({ value: '' });
    const input = await screen.findByRole('textbox', { name: 'Ollama model' });
    fireEvent.change(input, { target: { value: 'my-model' } });
    expect(onChange).toHaveBeenCalledWith('my-model');
  });
});
