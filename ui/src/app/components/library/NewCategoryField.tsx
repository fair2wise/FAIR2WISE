import { Loader2, Plus } from 'lucide-react';
import { useId, useState, type KeyboardEvent } from 'react';
import { categoryNameProblem, type CreatedCategory } from '../libraryApi';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';

/** Props of {@link NewCategoryField}. */
export interface NewCategoryFieldProps {
  /** The typed name (owned by the parent so it survives a re-render of the tab). */
  value: string;
  onChange: (value: string) => void;
  /** Names already in the shared list: a duplicate (any case) selects the existing one without a request. */
  existing: readonly string[];
  /** Creates the category on the server (the shared `useCreateCategory`). */
  onCreate: (name: string) => Promise<CreatedCategory>;
  /** Called with the final name once it is in the shared list: the parent selects it. */
  onCreated: (name: string, created: boolean) => void;
  disabled?: boolean;
}

/**
 * "New category name" field with a visible Add button. Enter or the button validates the name (trimmed, 64
 * characters, letters, digits, `_` and `-`), creates it through the shared list and selects it; a duplicate selects
 * the existing category. Enter never submits an enclosing form (the Harvester's form starts a harvest).
 */
export function NewCategoryField({ value, onChange, existing, onCreate, onCreated, disabled = false }: NewCategoryFieldProps) {
  const inputId = useId();
  const helpId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const typed = value.trim();
  const problem = typed === '' ? null : categoryNameProblem(typed);

  async function submit() {
    if (busy || disabled) return;
    const invalid = categoryNameProblem(typed);
    if (invalid !== null) {
      setError(invalid);
      return;
    }
    const duplicate = existing.find(name => name.toLowerCase() === typed.toLowerCase());
    if (duplicate !== undefined) {
      setError(null);
      onCreated(duplicate, false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const made = await onCreate(typed);
      onCreated(made.name, made.created);
    } catch (failure) {
      setError(failure instanceof Error && failure.message ? failure.message : 'The category could not be created.');
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.stopPropagation();
    void submit();
  }

  const shown = error ?? problem;
  return (
    <div className="space-y-1">
      <Label htmlFor={inputId}>New category name</Label>
      <div className="flex items-center gap-2">
        <Input
          id={inputId}
          value={value}
          maxLength={64}
          disabled={disabled || busy}
          aria-invalid={shown !== null}
          aria-describedby={helpId}
          onChange={event => {
            setError(null);
            onChange(event.target.value);
          }}
          onKeyDown={onKeyDown}
          className="w-48 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || busy || typed === '' || problem !== null}
          onClick={() => void submit()}
          className="disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Plus aria-hidden="true" />}
          Add category
        </Button>
      </div>
      <p
        id={helpId}
        role={error !== null ? 'alert' : undefined}
        className={shown !== null ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
      >
        {shown ?? 'Press Enter or Add category. Letters, digits, "_" and "-"; up to 64 characters.'}
      </p>
    </div>
  );
}
