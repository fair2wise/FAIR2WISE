import { useMemo } from 'react';

/** Props of {@link SchemaDiff}. */
export interface SchemaDiffProps {
  /** Unified diff text from `GET /v1/library/schemas/user/{name}/diff`. */
  diff: string;
  from: number;
  to: number;
}

type Kind = 'added' | 'removed' | 'hunk' | 'header' | 'context';

/** Classify one line of a unified diff (`+++` and `---` are the file headers, not changes). */
export function diffLineKind(line: string): Kind {
  if (line.startsWith('+++') || line.startsWith('---')) return 'header';
  if (line.startsWith('@@')) return 'hunk';
  if (line.startsWith('+')) return 'added';
  if (line.startsWith('-')) return 'removed';
  return 'context';
}

const CLASS: Record<Kind, string> = {
  added: 'bg-primary/10 font-medium text-foreground',
  removed: 'bg-destructive/10 text-destructive line-through decoration-1',
  hunk: 'text-muted-foreground',
  header: 'font-semibold text-muted-foreground',
  context: '',
};

const LABEL: Record<Kind, string> = { added: 'added: ', removed: 'removed: ', hunk: '', header: '', context: '' };

/**
 * A unified diff in monospace. Added lines carry their `+` and a tinted background, removed lines their `-`, a tinted
 * background and a strike-through, and a visually hidden "added" or "removed" for screen readers, so the colour is
 * never the only signal. Lines are text nodes.
 */
export function SchemaDiff({ diff, from, to }: SchemaDiffProps) {
  const rows = useMemo(() => (diff === '' ? [] : diff.replace(/\n$/, '').split('\n').map(line => ({ line, kind: diffLineKind(line) }))), [diff]);
  const added = rows.filter(r => r.kind === 'added').length;
  const removed = rows.filter(r => r.kind === 'removed').length;
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground" data-testid="diff-empty">Versions {from} and {to} are identical.</p>;
  }
  return (
    <div className="space-y-1" data-testid="schema-diff">
      <p className="text-xs text-muted-foreground">Version {from} to version {to}: {added} {added === 1 ? 'line' : 'lines'} added, {removed} removed.</p>
      <pre className="max-h-80 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-xs leading-5" tabIndex={0} aria-label={`Differences between version ${from} and version ${to}`}>
        {rows.map((row, index) => (
          <div key={index} data-kind={row.kind} className={CLASS[row.kind]}>
            {LABEL[row.kind] ? <span className="sr-only">{LABEL[row.kind]}</span> : null}
            {row.line === '' ? ' ' : row.line}
          </div>
        ))}
      </pre>
    </div>
  );
}
