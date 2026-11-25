import { GitCompare, Loader2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../ui/alert-dialog';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { SchemaDiff } from './SchemaDiff';
import { fetchSchemaDiff, schemaErrorText, type SchemaDiffResult, type UserSchemaRow } from './schemaApi';
import { useDeleteSchemaVersion } from './schemaHooks';

const SELECT_CLASS =
  'h-9 min-w-0 rounded-md border border-border bg-background px-2 text-sm shadow-sm outline-none focus:border-ring disabled:opacity-50';

/** Props of {@link SchemaVersions}. */
export interface SchemaVersionsProps {
  rows: readonly UserSchemaRow[];
  /** Open one version in the editor (V93). */
  onOpen: (id: string) => void;
  /** A version was deleted. */
  onDeleted?: (id: string) => void;
}

/**
 * The user's own schemas with their versions: open a version in the editor, delete one (after a confirmation; a KG
 * built on it refuses with `schema_in_use`, said in plain words), and compare two versions of one schema (V94).
 */
export function SchemaVersions({ rows, onOpen, onDeleted }: SchemaVersionsProps) {
  const remove = useDeleteSchemaVersion();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [diffName, setDiffName] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [diff, setDiff] = useState<SchemaDiffResult | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffBusy, setDiffBusy] = useState(false);

  const comparable = rows.filter(row => row.versions.length >= 2);
  const active = comparable.find(row => row.name === diffName) ?? comparable[0];

  async function confirmDelete() {
    const id = confirming;
    setConfirming(null);
    if (!id) return;
    setDeleteError(null);
    try {
      await remove.mutateAsync(id);
      onDeleted?.(id);
    } catch (failure) {
      setDeleteError(`${id}: ${schemaErrorText(failure)}`);
    }
  }

  async function compare() {
    if (!active) return;
    const a = Number(from || active.versions[active.versions.length - 2]);
    const b = Number(to || active.versions[active.versions.length - 1]);
    setDiffBusy(true);
    setDiffError(null);
    try {
      setDiff(await fetchSchemaDiff(active.name, a, b));
    } catch (failure) {
      setDiff(null);
      setDiffError(schemaErrorText(failure));
    } finally {
      setDiffBusy(false);
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="schema-versions-heading" data-testid="schema-versions">
      <h4 id="schema-versions-heading" className="text-sm font-semibold">4. Your schemas and versions</h4>
      {rows.length === 0 && <p className="text-sm text-muted-foreground">No user schema saved yet.</p>}
      <ul className="space-y-2">
        {rows.map(row => (
          <li key={row.name} className="space-y-1 rounded-md border border-border p-2" data-testid={`schema-row-${row.name}`}>
            <p className="text-sm font-medium break-all">{row.name} <span className="text-xs font-normal text-muted-foreground">(user schema)</span></p>
            <ul className="flex flex-wrap gap-2">
              {row.ids.map((id, index) => (
                <li key={id} className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs">
                  <span className="font-mono">v{row.versions[index]}</span>
                  <Button type="button" size="sm" variant="outline" onClick={() => onOpen(id)} aria-label={`Open ${id} in the editor`}>Open</Button>
                  <Button type="button" size="sm" variant="outline" onClick={() => { setDeleteError(null); setConfirming(id); }} aria-label={`Delete ${id}`}>
                    <Trash2 aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {deleteError && <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive" data-testid="delete-error">{deleteError}</p>}

      {active && (
        <div className="space-y-2 rounded-md border border-border p-2" data-testid="schema-compare">
          <p className="text-sm font-medium">Compare two versions</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="diff-name">Schema</Label>
              <select id="diff-name" className={SELECT_CLASS} value={active.name} onChange={event => { setDiffName(event.target.value); setFrom(''); setTo(''); setDiff(null); }}>
                {comparable.map(row => <option key={row.name} value={row.name}>{row.name}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="diff-from">From</Label>
              <select id="diff-from" className={SELECT_CLASS} value={from || String(active.versions[active.versions.length - 2])} onChange={event => setFrom(event.target.value)}>
                {active.versions.map(v => <option key={v} value={v}>v{v}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="diff-to">To</Label>
              <select id="diff-to" className={SELECT_CLASS} value={to || String(active.versions[active.versions.length - 1])} onChange={event => setTo(event.target.value)}>
                {active.versions.map(v => <option key={v} value={v}>v{v}</option>)}
              </select>
            </div>
            <Button type="button" size="sm" variant="outline" disabled={diffBusy} onClick={() => void compare()}>
              {diffBusy ? <Loader2 aria-hidden="true" className="animate-spin" /> : <GitCompare aria-hidden="true" />}
              Compare
            </Button>
          </div>
          {diffError && <p role="alert" className="text-sm text-destructive">{diffError}</p>}
          {diff && <SchemaDiff diff={diff.diff} from={diff.from} to={diff.to} />}
        </div>
      )}

      <AlertDialog open={confirming !== null} onOpenChange={open => { if (!open) setConfirming(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {confirming}?</AlertDialogTitle>
            <AlertDialogDescription>
              The version is removed from disk and cannot be restored. It cannot be deleted while a KG was built on it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDelete()}>Delete version</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
