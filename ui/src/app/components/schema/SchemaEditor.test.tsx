import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef, useState } from 'react';
import { describe, expect, it } from 'vitest';
import { SchemaEditor, formatSize, type SchemaEditorHandle } from './SchemaEditor';
import { MAX_SCHEMA_BYTES } from './schemaApi';

function Host({ initial = '', problems, handle }: { initial?: string; problems?: Map<number, string>; handle?: React.Ref<SchemaEditorHandle> }) {
  const [value, setValue] = useState(initial);
  return <SchemaEditor ref={handle} value={value} onChange={setValue} problemLines={problems} />;
}

const numbers = () => [...screen.getByTestId('schema-gutter').querySelectorAll('[data-line]')].map(el => el.getAttribute('data-line'));

describe('SchemaEditor', () => {
  it('shows one gutter number per line and follows edits', async () => {
    render(<Host initial={'a: 1\nb: 2\nc: 3'} />);
    expect(numbers()).toEqual(['1', '2', '3']);
    await userEvent.type(screen.getByTestId('schema-textarea'), '{Enter}d: 4');
    expect(numbers()).toEqual(['1', '2', '3', '4']);
  });

  it('shows line 1 for an empty text', () => {
    render(<Host />);
    expect(numbers()).toEqual(['1']);
  });

  it('marks problem lines in the gutter with a mark that is not only colour', () => {
    render(<Host initial={'a\nb\nc'} problems={new Map([[2, 'bad']])} />);
    const marked = screen.getByTestId('schema-gutter').querySelector('[data-problem="true"]');
    expect(marked?.getAttribute('data-line')).toBe('2');
    expect(marked?.textContent).toContain('!');
  });

  it('copies the textarea scroll position to the gutter', () => {
    render(<Host initial={Array.from({ length: 100 }, (_, i) => `k${i}: v`).join('\n')} />);
    const area = screen.getByTestId('schema-textarea');
    area.scrollTop = 240;
    fireEvent.scroll(area);
    expect(screen.getByTestId('schema-gutter').scrollTop).toBe(240);
  });

  it('Tab inserts two spaces and keeps focus; Escape then Tab leaves the field', async () => {
    const user = userEvent.setup();
    render(<><Host initial="a:" /><button type="button">next</button></>);
    const area = screen.getByTestId('schema-textarea') as HTMLTextAreaElement;
    await user.click(area);
    await user.keyboard('{End}{Tab}x');
    expect(area.value).toBe('a:  x');
    expect(area).toHaveFocus();
    await user.keyboard('{Escape}{Tab}');
    expect(screen.getByRole('button', { name: 'next' })).toHaveFocus();
  });

  it('goToLine selects the line and scrolls to it', () => {
    const ref = createRef<SchemaEditorHandle>();
    render(<Host initial={'one\ntwo\nthree'} handle={ref} />);
    act(() => ref.current?.goToLine(2));
    const area = screen.getByTestId('schema-textarea') as HTMLTextAreaElement;
    expect(area.value.slice(area.selectionStart, area.selectionEnd)).toBe('two');
    expect(area).toHaveFocus();
  });

  it('the counter shows the size, warns near the cap and refuses over it', () => {
    const { rerender } = render(<SchemaEditor value="a: 1" onChange={() => {}} />);
    expect(screen.getByTestId('schema-counter').textContent).toContain('1 line, 0.0 KiB of 256 KiB');
    rerender(<SchemaEditor value={'x'.repeat(Math.ceil(MAX_SCHEMA_BYTES * 0.92))} onChange={() => {}} />);
    expect(screen.getByTestId('schema-counter').textContent).toContain('close to the limit');
    rerender(<SchemaEditor value={'x'.repeat(MAX_SCHEMA_BYTES + 1)} onChange={() => {}} />);
    expect(screen.getByTestId('schema-counter').textContent).toContain('over the limit');
  });

  it('counts bytes, so multi-byte text reaches the cap sooner', () => {
    render(<SchemaEditor value={'é'.repeat(MAX_SCHEMA_BYTES / 2 + 1)} onChange={() => {}} />);
    expect(screen.getByTestId('schema-counter').textContent).toContain('over the limit');
  });

  it('renders text as text, never as markup', () => {
    render(<Host initial={'<img src=x onerror=alert(1)>'} />);
    expect(document.querySelector('img')).toBeNull();
  });
});

describe('formatSize', () => {
  it('formats KiB', () => {
    expect(formatSize(2048)).toBe('2.0 KiB of 256 KiB');
    expect(formatSize(100 * 1024)).toBe('100 KiB of 256 KiB');
  });
});
