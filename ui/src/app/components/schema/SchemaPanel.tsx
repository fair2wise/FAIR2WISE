import { Download, FilePlus2, Loader2, Save, Upload, Wand2 } from 'lucide-react';
import { useMemo, useRef, useState, type ChangeEvent } from 'react';
import { buildModelChoices, modelSourceNote } from '../extraction/modelChoices';
import { useExtractionModels } from '../extractionHooks';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { SchemaEditor, type SchemaEditorHandle } from './SchemaEditor';
import { SchemaGenerateBox } from './SchemaGenerateBox';
import { SchemaPreview } from './SchemaPreview';
import { SchemaVersions } from './SchemaVersions';
import { ValidationReport } from './ValidationReport';
import {
  MAX_SCHEMA_BYTES,
  SchemaInvalidError,
  fetchStoredSchema,
  insertCore,
  isValidSchemaName,
  schemaErrorText,
  utf8Length,
  type SchemaProblem,
} from './schemaApi';
import { useSaveSchema, useSchemaValidation, useUserSchemas } from './schemaHooks';

/** Props of {@link SchemaPanel}. */
export interface SchemaPanelProps {
  /** `<select>` value of the model the calling form has chosen (the panel starts on it). */
  modelValue?: string;
  /** A new version was saved: the id (`user/<name>@vN`) to select in the caller's chooser. */
  onSaved?: (id: string) => void;
}

/** The `name:` of the schema text when it is a valid schema name (used to suggest the save name). */
export function suggestName(text: string): string {
  const match = /^name:\s*["']?([a-z][a-z0-9_-]{0,63})["']?\s*$/m.exec(text);
  return match ? match[1] : '';
}

/** File name of a download: `<name>@vN.yaml` for a stored version, `<name>@draft.yaml` for text not yet saved. */
export function downloadName(loadedId: string | null, name: string): string {
  if (loadedId) return `${loadedId.replace(/^user\//, '')}.yaml`;
  return `${isValidSchemaName(name) ? name : 'schema'}@draft.yaml`;
}

const SELECT_CLASS =
  'h-9 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm shadow-sm outline-none focus:border-ring disabled:opacity-50';

/**
 * The schema panel: describe a domain and let the model draft a LinkML schema, or paste, upload or open one; edit it
 * with continuous validation (problems by line, what was and was not checked); try it on one paper; save it as a
 * new immutable version. A saved version appears in the schema chooser as a user schema. A generated draft is never
 * saved automatically.
 */
export function SchemaPanel({ modelValue, onSaved }: SchemaPanelProps) {
  const models = useExtractionModels();
  const choices = useMemo(() => buildModelChoices(models.data), [models.data]);
  const [modelPick, setModelPick] = useState(modelValue ?? '');
  const choice = choices.find(c => c.value === modelPick) ?? choices.find(c => c.value === modelValue) ?? choices[0];
  const selection = choice ? { model: choice.model, backend: choice.backend } : null;

  const user = useUserSchemas();
  const save = useSaveSchema();
  const editor = useRef<SchemaEditorHandle>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveProblems, setSaveProblems] = useState<SchemaProblem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const validation = useSchemaValidation(text);
  const overCap = utf8Length(text) > MAX_SCHEMA_BYTES;
  const report = validation.report;
  const current = report !== null && validation.checkedText === text;
  const problemLines = useMemo(() => {
    const lines = new Map<number, string>();
    for (const problem of report?.problems ?? []) if (!lines.has(problem.line)) lines.set(problem.line, problem.message);
    return lines;
  }, [report]);
  const textOk = current && report.ok && !overCap;
  const nameValid = isValidSchemaName(name);
  const canSave = textOk && nameValid && !save.isPending;

  function load(next: string, from: string | null, suggest = true) {
    setText(next);
    setLoadedId(from);
    setSaveProblems([]);
    setError(null);
    setNotice(null);
    if (suggest && !name) setName(suggestName(next));
  }

  async function onInsertCore() {
    setBusy('core');
    setError(null);
    try {
      setText(await insertCore(text));
      setLoadedId(null);
    } catch (failure) {
      setError(schemaErrorText(failure));
    } finally {
      setBusy(null);
    }
  }

  async function onOpen(id: string) {
    setBusy('open');
    setError(null);
    try {
      const stored = await fetchStoredSchema(id);
      setText(stored.text);
      setName(stored.name);
      setLoadedId(stored.id);
      setSaveProblems([]);
      setNotice(`Opened ${stored.id}. Saving creates a new version; this one stays as it is.`);
    } catch (failure) {
      setError(schemaErrorText(failure));
    } finally {
      setBusy(null);
    }
  }

  async function onSave() {
    if (!canSave) return;
    setError(null);
    setSaveProblems([]);
    setNotice(null);
    try {
      const saved = await save.mutateAsync({ name, text });
      setLoadedId(saved.id);
      setNotice(`Saved as ${saved.id}. It is now in the schema chooser as a user schema; a KG can be created on it.`);
      onSaved?.(saved.id);
    } catch (failure) {
      setError(schemaErrorText(failure, 'save'));
      if (failure instanceof SchemaInvalidError) setSaveProblems(failure.report.problems);
    }
  }

  function onDownload() {
    const blob = new Blob([text], { type: 'text/yaml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = downloadName(loadedId, name);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setError(null);
    if (!/\.ya?ml$/i.test(file.name)) {
      setError('Choose a .yaml or .yml file.');
      return;
    }
    if (file.size > MAX_SCHEMA_BYTES) {
      setError(`The file is ${Math.ceil(file.size / 1024)} KiB; the limit is ${MAX_SCHEMA_BYTES / 1024} KiB. It was not read.`);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError('The file could not be read.');
    reader.onload = () => {
      // Read as text only; it is never executed or interpreted here. The validator checks it as it checks any text.
      load(typeof reader.result === 'string' ? reader.result : '', null);
    };
    reader.readAsText(file);
  }

  const rows = user.data ?? [];

  return (
    <div className="space-y-6" data-testid="schema-panel">
      <div className="space-y-1">
        <Label htmlFor="schema-model">Model for drafts and previews (free models only)</Label>
        <select id="schema-model" className={SELECT_CLASS} value={choice?.value ?? ''} disabled={choices.length === 0} onChange={event => setModelPick(event.target.value)}>
          {choices.length === 0 && <option value="">No models available</option>}
          {choices.map(c => <option key={c.value} value={c.value}>{c.group === 'Ollama' ? `Ollama: ${c.model}` : c.model}</option>)}
        </select>
        <p className="text-xs text-muted-foreground">{modelSourceNote(models.data)}</p>
      </div>

      <SchemaGenerateBox
        selection={selection}
        onDraft={draft => load(draft, null)}
        onPickAttempt={attempt => load(attempt, null)}
      />

      <section className="space-y-2" aria-labelledby="schema-editor-heading">
        <h4 id="schema-editor-heading" className="text-sm font-semibold">2. Edit, check and accept</h4>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" disabled={busy !== null || overCap} onClick={() => void onInsertCore()}>
            {busy === 'core' ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Wand2 aria-hidden="true" />}
            Insert required core
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => fileInput.current?.click()}>
            <Upload aria-hidden="true" /> Upload .yaml
          </Button>
          <input ref={fileInput} type="file" accept=".yaml,.yml" className="sr-only" tabIndex={-1} aria-label="Upload a schema file" data-testid="schema-upload" onChange={onUpload} />
          <Button type="button" size="sm" variant="outline" disabled={text === ''} onClick={onDownload}>
            <Download aria-hidden="true" /> Download
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={text === ''} onClick={() => load('', null, false)}>
            <FilePlus2 aria-hidden="true" /> Clear
          </Button>
        </div>
        <SchemaEditor ref={editor} id="schema-editor" value={text} onChange={value => { setText(value); setNotice(null); }} problemLines={problemLines} describedBy="schema-validation-status" />
        <ValidationReport validation={validation} text={text} overCap={overCap} onGoToLine={line => editor.current?.goToLine(line)} />
      </section>

      <section className="space-y-2" aria-labelledby="schema-save-heading" data-testid="schema-save">
        <h4 id="schema-save-heading" className="text-sm font-semibold">3. Accept and save as a new version</h4>
        <div className="space-y-1">
          <Label htmlFor="schema-name">Schema name</Label>
          <Input id="schema-name" value={name} maxLength={64} aria-invalid={name !== '' && !nameValid} aria-describedby="schema-name-help" onChange={event => setName(event.target.value)} />
          <p id="schema-name-help" className={name !== '' && !nameValid ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
            A lowercase letter first, then lowercase letters, digits, "_" or "-"; up to 64 characters. The text's own top-level
            name must be the same. Each save is a new immutable version (v1, v2, ...); a KG keeps the version it started with.
          </p>
        </div>
        <Button type="button" size="sm" disabled={!canSave} onClick={() => void onSave()} className="disabled:cursor-not-allowed disabled:opacity-50">
          {save.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Save aria-hidden="true" />}
          Accept and save
        </Button>
        {!textOk && text.trim() !== '' && <p className="text-xs text-muted-foreground">Saving needs a text that passes the checks.</p>}
        {notice && <p role="status" className="rounded-md border border-border bg-muted p-2 text-sm" data-testid="schema-notice">{notice}</p>}
        {error && (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive" data-testid="schema-error">
            <p>{error}</p>
            {saveProblems.length > 0 && (
              <ul className="list-disc pl-5" data-testid="save-problems">
                {saveProblems.slice(0, 20).map((p, i) => (
                  <li key={i}><button type="button" className="text-left underline" onClick={() => editor.current?.goToLine(p.line)}>Line {p.line}: {p.message}</button></li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <SchemaVersions rows={rows} onOpen={id => void onOpen(id)} onDeleted={id => { if (id === loadedId) setLoadedId(null); }} />

      <SchemaPreview text={text} textOk={textOk} selection={selection} />
    </div>
  );
}
