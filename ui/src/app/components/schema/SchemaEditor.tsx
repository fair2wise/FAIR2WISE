import { forwardRef, useImperativeHandle, useRef, useState, type ChangeEvent, type KeyboardEvent, type UIEvent } from 'react';
import { MAX_SCHEMA_BYTES, utf8Length } from './schemaApi';

/** Handle of {@link SchemaEditor}. */
export interface SchemaEditorHandle {
  /** Scroll to the 1-based `line`, select it and focus the editor. */
  goToLine: (line: number) => void;
}

/** Props of {@link SchemaEditor}. */
export interface SchemaEditorProps {
  value: string;
  onChange: (value: string) => void;
  /** Lines with a problem (1-based) mapped to the first message of that line: marked in the gutter. */
  problemLines?: ReadonlyMap<number, string>;
  id?: string;
  /** Id of the element that describes the editor (validation status). */
  describedBy?: string;
  disabled?: boolean;
}

/** Spaces a Tab inserts (YAML forbids tab indentation). */
export const TAB_SPACES = '  ';
/** Pixel height of one line (`leading-5`); the gutter and the textarea must agree. */
const LINE_PX = 20;
/** Fraction of the cap from which the counter warns. */
export const NEAR_CAP = 0.9;

/** `text` size as the counter shows it: `12.3 KiB of 256 KiB`. */
export function formatSize(bytes: number): string {
  const kib = bytes / 1024;
  return `${kib < 10 ? kib.toFixed(1) : Math.round(kib)} KiB of ${MAX_SCHEMA_BYTES / 1024} KiB`;
}

/**
 * Monospace editor: a controlled textarea with a line-number gutter kept in step by copying the scroll position.
 * No wrapping, so one gutter number is one text line. Tab inserts two spaces (Escape, then Tab, leaves the field, so
 * the keyboard is not trapped). The counter shows the UTF-8 size against the server's 256 KiB cap and warns from 90%.
 * The text is only ever rendered as text (a textarea value and numbers), never as HTML.
 */
export const SchemaEditor = forwardRef<SchemaEditorHandle, SchemaEditorProps>(function SchemaEditor(
  { value, onChange, problemLines, id, describedBy, disabled = false },
  ref,
) {
  const area = useRef<HTMLTextAreaElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const [escaped, setEscaped] = useState(false);
  const lines = value === '' ? 1 : value.split('\n').length;
  const bytes = utf8Length(value);
  const ratio = bytes / MAX_SCHEMA_BYTES;
  const counterId = `${id ?? 'schema-editor'}-counter`;

  useImperativeHandle(ref, () => ({
    goToLine(line: number) {
      const el = area.current;
      if (!el) return;
      const all = el.value.split('\n');
      const target = Math.min(Math.max(Math.floor(line), 1), all.length);
      let start = 0;
      for (let i = 0; i < target - 1; i += 1) start += all[i].length + 1;
      el.focus();
      el.setSelectionRange(start, start + all[target - 1].length);
      el.scrollTop = Math.max((target - 1) * LINE_PX - el.clientHeight / 3, 0);
      if (gutter.current) gutter.current.scrollTop = el.scrollTop;
    },
  }), []);

  function onScroll(event: UIEvent<HTMLTextAreaElement>) {
    if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Escape') {
      setEscaped(true);
      return;
    }
    if (event.key !== 'Tab' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
    if (escaped) {
      setEscaped(false);
      return;
    }
    event.preventDefault();
    const el = event.currentTarget;
    const { selectionStart: from, selectionEnd: to } = el;
    const next = value.slice(0, from) + TAB_SPACES + value.slice(to);
    onChange(next);
    const caret = from + TAB_SPACES.length;
    requestAnimationFrame(() => el.setSelectionRange(caret, caret));
  }

  function onInput(event: ChangeEvent<HTMLTextAreaElement>) {
    setEscaped(false);
    onChange(event.target.value);
  }

  return (
    <div className="space-y-1">
      <div className="flex h-72 min-h-40 overflow-hidden rounded-md border border-border bg-background font-mono text-xs leading-5 focus-within:border-ring">
        <div
          ref={gutter}
          aria-hidden="true"
          data-testid="schema-gutter"
          className="shrink-0 select-none overflow-hidden border-r border-border bg-muted py-2 pr-2 pl-1 text-right text-muted-foreground"
        >
          {Array.from({ length: lines }, (_, index) => {
            const number = index + 1;
            const marked = problemLines?.has(number) ?? false;
            return (
              <div
                key={number}
                data-line={number}
                data-problem={marked ? 'true' : undefined}
                className={marked ? 'font-bold text-destructive' : undefined}
                style={{ height: LINE_PX }}
              >
                {marked ? '! ' : '  '}{number}
              </div>
            );
          })}
          <div style={{ height: 16 }} />
        </div>
        <textarea
          ref={area}
          id={id}
          value={value}
          disabled={disabled}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          wrap="off"
          aria-label="Schema text (YAML)"
          aria-describedby={[describedBy, counterId].filter(Boolean).join(' ')}
          data-testid="schema-textarea"
          onChange={onInput}
          onKeyDown={onKeyDown}
          onScroll={onScroll}
          className="min-w-0 flex-1 resize-none overflow-auto bg-transparent px-2 py-2 leading-5 whitespace-pre outline-none disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>
      <p
        id={counterId}
        data-testid="schema-counter"
        className={ratio > 1 ? 'text-xs font-medium text-destructive' : ratio >= NEAR_CAP ? 'text-xs font-medium text-foreground' : 'text-xs text-muted-foreground'}
      >
        {lines} {lines === 1 ? 'line' : 'lines'}, {formatSize(bytes)}
        {ratio > 1 ? ': over the limit, the server will refuse this text. Shorten it.' : ratio >= NEAR_CAP ? ': close to the limit.' : '.'}
        {' '}Tab inserts spaces; press Escape, then Tab, to leave the field.
      </p>
    </div>
  );
});
