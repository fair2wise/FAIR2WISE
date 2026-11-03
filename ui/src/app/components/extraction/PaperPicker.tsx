import { useMemo, useState } from 'react';
import type { LibraryPaper } from '../libraryApi';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';

/** Props of {@link PaperPicker}. */
export interface PaperPickerProps {
  papers: LibraryPaper[];
  /** Selected T35 library paper ids. */
  selected: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
  disabled?: boolean;
}

/** True when the paper matches the filter text (file name, title or DOI, case-insensitive). */
export function paperMatches(paper: LibraryPaper, filter: string): boolean {
  const needle = filter.trim().toLowerCase();
  if (!needle) return true;
  return [paper.file_name, paper.title, paper.doi].some(value => value?.toLowerCase().includes(needle));
}

/**
 * Paper picker for an extraction: filter box, "N of M selected" count, Select all (applies to the
 * filtered rows) and one checkbox per paper.
 */
export function PaperPicker({ papers, selected, onChange, disabled = false }: PaperPickerProps) {
  const [filter, setFilter] = useState('');
  const visible = useMemo(() => papers.filter(paper => paperMatches(paper, filter)), [papers, filter]);
  const allVisible = visible.length > 0 && visible.every(paper => selected.has(paper.id));

  const toggle = (id: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    onChange(next);
  };
  const toggleAll = (on: boolean) => {
    const next = new Set(selected);
    for (const paper of visible) {
      if (on) next.add(paper.id);
      else next.delete(paper.id);
    }
    onChange(next);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <Input
          type="search"
          aria-label="Filter papers"
          placeholder="Filter by file name, title or DOI"
          value={filter}
          disabled={disabled}
          onChange={event => setFilter(event.target.value)}
          className="disabled:cursor-not-allowed disabled:opacity-50"
        />
        <span className="shrink-0 text-sm text-muted-foreground" data-testid="paper-count">
          {selected.size} of {papers.length} selected
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id="select-all-papers"
          checked={allVisible}
          disabled={disabled || visible.length === 0}
          onCheckedChange={checked => toggleAll(checked === true)}
          className="disabled:cursor-not-allowed disabled:opacity-50"
        />
        <Label htmlFor="select-all-papers" className="text-sm">
          Select all{filter.trim() ? ' shown' : ''}
        </Label>
      </div>
      <ul aria-label="Papers" className="max-h-64 divide-y divide-border overflow-y-auto rounded-md border border-border">
        {visible.length === 0 && (
          <li className="p-3 text-sm text-muted-foreground">
            {papers.length === 0 ? 'No papers in the library yet.' : 'No papers match the filter.'}
          </li>
        )}
        {visible.map(paper => (
          <li key={paper.id} className="flex items-center gap-2 p-2">
            <Checkbox
              id={`paper-${paper.id}`}
              checked={selected.has(paper.id)}
              disabled={disabled}
              onCheckedChange={checked => toggle(paper.id, checked === true)}
              className="disabled:cursor-not-allowed disabled:opacity-50"
            />
            <Label htmlFor={`paper-${paper.id}`} className="min-w-0 flex-1 flex-col items-start gap-0">
              <span className="w-full truncate text-sm" title={paper.title ?? paper.file_name}>
                {paper.title ?? paper.file_name}
              </span>
              <span className="w-full truncate text-xs text-muted-foreground" title={paper.file_name}>
                {paper.file_name}{paper.doi ? ` · ${paper.doi}` : ''}
              </span>
            </Label>
          </li>
        ))}
      </ul>
    </div>
  );
}
