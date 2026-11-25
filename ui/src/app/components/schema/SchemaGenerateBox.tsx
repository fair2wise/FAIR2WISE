import { Loader2, Send, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { MAX_DESCRIPTION_CHARS, cancelGenerate, generateSchema, schemaErrorText, type GenerateResult } from './schemaApi';

/** What the model picker chose (the extraction form's own choice list). */
export interface ModelSelection {
  model: string;
  backend: 'ollama' | undefined;
}

/** Props of {@link SchemaGenerateBox}. */
export interface SchemaGenerateBoxProps {
  /** `null` while no model is available: Send stays disabled and says why. */
  selection: ModelSelection | null;
  /** The draft (the last attempt) goes to the editor; it is never saved by this component. */
  onDraft: (text: string, result: GenerateResult) => void;
  /** An earlier attempt of the same answer goes to the editor. */
  onPickAttempt: (text: string) => void;
}

/**
 * "Describe the domain" box with a Send button (V96). The model and backend are the ones the extraction form's picker
 * offers (free models only; the server checks again). It shows progress, the draft's provenance and every attempt, and
 * says plainly that a draft is a draft: nothing is saved until the user accepts it.
 */
const newRequestId = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);

export function SchemaGenerateBox({ selection, onDraft, onPickAttempt }: SchemaGenerateBoxProps) {
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [seconds, setSeconds] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef<string | null>(null);

  useEffect(() => {
    if (!busy) return undefined;
    setSeconds(0);
    const timer = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => () => controller.current?.abort(), []);

  const trimmed = description.trim();
  const tooLong = description.length > MAX_DESCRIPTION_CHARS;
  const canSend = selection !== null && trimmed !== '' && !tooLong && !busy;

  async function send() {
    if (!selection || !canSend) return;
    const mine = new AbortController();
    controller.current = mine;
    const id = newRequestId();
    requestId.current = id;
    setBusy(true);
    setError(null);
    try {
      const out = await generateSchema(
        { description: trimmed, model: selection.model, ...(selection.backend ? { backend: selection.backend } : {}), request_id: id },
        mine.signal,
      );
      if (mine.signal.aborted) return;
      setResult(out);
      onDraft(out.text, out);
    } catch (failure) {
      if (mine.signal.aborted) return;
      setResult(null);
      setError(schemaErrorText(failure, 'generate'));
    } finally {
      if (controller.current === mine) setBusy(false);
    }
  }

  function stop() {
    controller.current?.abort();
    const id = requestId.current;
    if (id) void cancelGenerate(id).catch(() => undefined);   // the server stops after the call in flight
    setBusy(false);
    setError('Stopped. The server makes no further model call after the one in flight; its answer is ignored.');
  }

  return (
    <section className="space-y-2" aria-labelledby="schema-generate-heading" data-testid="schema-generate">
      <h4 id="schema-generate-heading" className="text-sm font-semibold">1. Describe the domain</h4>
      <div className="space-y-1">
        <Label htmlFor="schema-description">What should the schema cover?</Label>
        <textarea
          id="schema-description"
          value={description}
          rows={3}
          disabled={busy}
          aria-describedby="schema-description-help"
          aria-invalid={tooLong}
          onChange={event => setDescription(event.target.value)}
          className="w-full min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm shadow-sm outline-none focus:border-ring disabled:opacity-50"
          placeholder="For example: armoured catfish (Loricariidae): species, genera, habitats, morphology, methods."
        />
        <p id="schema-description-help" className={tooLong ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
          {description.length} of {MAX_DESCRIPTION_CHARS} characters. The model drafts a schema with the shipped schemas as a pattern.
          A draft is a draft: it may be wrong or incomplete, it is checked below, and it is saved only when you accept it.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" disabled={!canSend} onClick={() => void send()} className="disabled:cursor-not-allowed disabled:opacity-50">
          {busy ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Send aria-hidden="true" />}
          Send
        </Button>
        {busy && (
          <Button type="button" size="sm" variant="outline" onClick={stop}>
            <X aria-hidden="true" /> Stop waiting
          </Button>
        )}
        {selection === null && <span className="text-xs text-muted-foreground">No model is available, so a draft cannot be generated. You can still paste or upload a schema.</span>}
      </div>
      <p role="status" aria-live="polite" className="min-h-4 text-xs text-muted-foreground" data-testid="generate-status">
        {busy ? `Asking ${selection?.model ?? 'the model'}. This can take a few minutes: a draft, then up to two repair rounds (${seconds} s).` : ''}
      </p>
      {error && <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive" data-testid="generate-error">{error}</p>}
      {result && !busy && (
        <div className="space-y-1 rounded-md border border-border p-2 text-sm" data-testid="generate-result">
          <p>
            Draft by {result.backend} / {result.model}, {result.repairs === 0 ? 'no repair rounds' : `${result.repairs} repair ${result.repairs === 1 ? 'round' : 'rounds'}`}.{' '}
            {result.ok ? 'The last attempt passes the checks listed under the editor.' : `The last attempt still has ${result.report.problems.length} problem(s); fix them in the editor.`}{' '}
            It is not saved.
          </p>
          {result.stopped && <p className="text-muted-foreground">A repair round stopped: {result.stopped}</p>}
          {result.attempts.length > 1 && (
            <ul className="space-y-1">
              {result.attempts.map(attempt => (
                <li key={attempt.round} className="flex flex-wrap items-center gap-2">
                  <span>Attempt {attempt.round + 1}: {attempt.report.ok ? 'passes' : `${attempt.report.problems.length} problem(s)`}</span>
                  <Button type="button" size="sm" variant="outline" onClick={() => onPickAttempt(attempt.text)}>Show in editor</Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
