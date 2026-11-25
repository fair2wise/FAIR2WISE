import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import reportInvalid from '../__fixtures__/schema_report_invalid.json';
import { ValidationReport } from './ValidationReport';
import { MAX_SCHEMA_BYTES } from './schemaApi';
import { VALIDATE_DEBOUNCE_MS, useSchemaValidation } from './schemaHooks';
import { reportOk, stubFetch } from './testRoutes';

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe('useSchemaValidation', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('waits about 500 ms after the last change and sends one request', async () => {
    const calls = stubFetch({ 'POST /v1/library/schemas/validate': { status: 200, body: reportOk } });
    const { result, rerender } = renderHook(({ text }) => useSchemaValidation(text), { initialProps: { text: 'a: 1' } });
    expect(result.current.status).toBe('waiting');
    await act(async () => { vi.advanceTimersByTime(VALIDATE_DEBOUNCE_MS - 100); });
    rerender({ text: 'a: 12' });
    await act(async () => { vi.advanceTimersByTime(VALIDATE_DEBOUNCE_MS - 100); });
    expect(calls).toHaveLength(0);
    await act(async () => { vi.advanceTimersByTime(200); });
    await flush();
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ text: 'a: 12' });
    expect(result.current.status).toBe('done');
    expect(result.current.checkedText).toBe('a: 12');
  });

  it('cancels the request in flight when the text changes and ignores its late answer', async () => {
    const resolvers: Array<() => void> = [];
    const calls = stubFetch({
      'POST /v1/library/schemas/validate': body =>
        new Promise(resolve => {
          const text = (body as { text: string }).text;
          resolvers.push(() => resolve({ status: 200, body: { ...reportOk, ok: text === 'second', note: text } }));
        }),
    });
    const { result, rerender } = renderHook(({ text }) => useSchemaValidation(text), { initialProps: { text: 'first' } });
    await act(async () => { vi.advanceTimersByTime(VALIDATE_DEBOUNCE_MS); });
    expect(calls).toHaveLength(1);
    rerender({ text: 'second' });
    expect(calls[0].signal?.aborted).toBe(true);
    await act(async () => { vi.advanceTimersByTime(VALIDATE_DEBOUNCE_MS); });
    expect(calls).toHaveLength(2);
    await act(async () => { resolvers[1](); });
    await act(async () => { resolvers[0](); });
    await flush();
    expect(result.current.checkedText).toBe('second');
    expect(result.current.report?.note).toBe('second');
  });

  it('sends nothing for empty text or text over the cap', async () => {
    const calls = stubFetch({ 'POST /v1/library/schemas/validate': { status: 200, body: reportOk } });
    const { result, rerender } = renderHook(({ text }) => useSchemaValidation(text), { initialProps: { text: '  ' } });
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(result.current.status).toBe('idle');
    rerender({ text: 'x'.repeat(MAX_SCHEMA_BYTES + 1) });
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(calls).toHaveLength(0);
  });

  it('reports a failed check without a report', async () => {
    stubFetch({ 'POST /v1/library/schemas/validate': { status: 500, body: { code: 'internal', message: 'boom', retryable: false } } });
    const { result } = renderHook(() => useSchemaValidation('a: 1'));
    await act(async () => { vi.advanceTimersByTime(VALIDATE_DEBOUNCE_MS); });
    await flush();
    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('boom');
  });
});

describe('ValidationReport', () => {
  const done = { status: 'done' as const, report: reportInvalid as never, checkedText: 'text', error: null };

  it('lists problems with their lines and selects the line on click', async () => {
    const onGoToLine = vi.fn();
    render(<ValidationReport validation={done} text="text" onGoToLine={onGoToLine} />);
    expect(screen.getByTestId('validation-status')).toHaveTextContent('2 problems found.');
    const items = screen.getAllByRole('button');
    expect(items[0]).toHaveTextContent('Line 2, column 7:');
    expect(items[0]).toHaveTextContent('[name_mismatch]');
    expect(items[1]).toHaveTextContent('Line 43:');
    await userEvent.click(items[1]);
    expect(onGoToLine).toHaveBeenCalledWith(43);
  });

  it('shows exactly what was checked and what was not, in the server words', () => {
    render(<ValidationReport validation={done} text="text" onGoToLine={() => {}} />);
    const box = screen.getByTestId('checked-list');
    expect(box).toHaveTextContent('parses as YAML (no tags, anchors or duplicate keys)');
    expect(box).toHaveTextContent('required core (Thing, CodeSnippet, name, description, related_to)');
    expect(screen.getByTestId('report-note')).toHaveTextContent('Not full LinkML conformance: the linkml linter and validator are not used.');
  });

  it('says a report belongs to an earlier text', () => {
    render(<ValidationReport validation={done} text="changed" onGoToLine={() => {}} />);
    expect(screen.getByTestId('validation-status')).toHaveTextContent('earlier text');
  });

  it('announces success and busy states in a polite live region', () => {
    const { rerender } = render(<ValidationReport validation={{ ...done, report: reportOk as never }} text="text" onGoToLine={() => {}} />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveTextContent('No problems found.');
    rerender(<ValidationReport validation={{ ...done, status: 'waiting' }} text="text" onGoToLine={() => {}} />);
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for you to stop typing');
  });

  it('renders server text as text, not markup', () => {
    const hostile = { ...reportInvalid, problems: [{ line: 1, column: null, fixable: false, code: 'x', message: '<b onmouseover=alert(1)>hi</b>' }] };
    render(<ValidationReport validation={{ ...done, report: hostile as never }} text="text" onGoToLine={() => {}} />);
    expect(document.querySelector('b')).toBeNull();
    expect(screen.getByRole('button')).toHaveTextContent('<b onmouseover=alert(1)>hi</b>');
  });

  it('lists warnings apart from problems, each selecting its line, and says the schema can still be saved', async () => {
    const goTo = vi.fn();
    const withWarnings = { ...reportOk, warnings: [
      { line: 41, column: 3, fixable: false, code: 'attribute_slot', message: "'country' has the value type 'string': it is stored as an attribute of Locality, not as a link to another node" },
      { line: 38, column: 3, fixable: false, code: 'relation_direction', message: "'has_measurement' reads from a Species to a Measurement" },
    ] };
    render(<ValidationReport validation={{ ...done, report: withWarnings as never }} text="text" onGoToLine={goTo} />);
    expect(screen.getByTestId('validation-status')).toHaveTextContent('No problems found.');
    expect(screen.getByTestId('warning-list')).toHaveTextContent('2 warnings (the schema can still be saved)');
    await userEvent.click(screen.getByRole('button', { name: /has_measurement/ }));
    expect(goTo).toHaveBeenCalledWith(38);
    expect(screen.queryByTestId('problem-list')).toBeNull();
  });
});
