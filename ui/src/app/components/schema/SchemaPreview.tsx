import { Calculator, FlaskConical, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useLibraryPapers } from '../libraryHooks';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import {
  previewEstimate,
  previewRun,
  schemaErrorText,
  SchemaInvalidError,
  type PreviewEstimate,
  type PreviewResult,
  type SchemaProblem,
} from './schemaApi';
import type { ModelSelection } from './SchemaGenerateBox';

/** Props of {@link SchemaPreview}. */
export interface SchemaPreviewProps {
  /** The editor's text; the preview refuses text that does not validate. */
  text: string;
  /** Whether the text passed the check (the server validates again). */
  textOk: boolean;
  selection: ModelSelection | null;
}

/** Sentence for the cost of an estimate (`null` cost: unknown, the free models cost nothing to the user). */
export function costSentence(cost: number | null): string {
  return cost === null ? 'cost unknown' : `about $${cost.toFixed(2)}`;
}

const SELECT_CLASS =
  'h-9 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm shadow-sm outline-none focus:border-ring disabled:opacity-50';

/**
 * Try the schema on one library paper (V97). Step one asks the server for an estimate only (no model call, no PDF
 * read): pages (at most 3), tokens, cost and a confirmation token. Step two, after the user confirms, runs it and
 * shows the terms with their categories, definitions and relations (`verified`), the summary of what fell back and
 * the server's note. A preview is three pages, not the real extraction, and nothing is stored.
 */
export function SchemaPreview({ text, textOk, selection }: SchemaPreviewProps) {
  const papers = useLibraryPapers(true);
  const [paperId, setPaperId] = useState('');
  const [estimate, setEstimate] = useState<{ key: string; value: PreviewEstimate } | null>(null);
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [busy, setBusy] = useState<'estimate' | 'run' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<SchemaProblem[]>([]);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const list = papers.data?.papers ?? [];
  const paper = list.find(p => p.id === paperId);
  const key = JSON.stringify([text, paperId, selection?.model ?? '', selection?.backend ?? '']);
  const current = estimate !== null && estimate.key === key;
  const base = { schema: text, paper_id: paperId, ...(selection ? { model: selection.model } : {}), ...(selection?.backend ? { backend: selection.backend } : {}) };

  function fail(failure: unknown) {
    setError(schemaErrorText(failure, 'preview'));
    setProblems(failure instanceof SchemaInvalidError ? failure.report.problems : []);
  }

  async function estimateNow() {
    setBusy('estimate');
    setError(null);
    setProblems([]);
    setResult(null);
    try {
      setEstimate({ key, value: await previewEstimate(base) });
    } catch (failure) {
      setEstimate(null);
      fail(failure);
    } finally {
      setBusy(null);
    }
  }

  async function run() {
    if (!current || !estimate) return;
    const mine = new AbortController();
    controller.current = mine;
    setBusy('run');
    setError(null);
    try {
      const out = await previewRun({ ...base, confirmation: estimate.value.confirmation }, mine.signal);
      if (!mine.signal.aborted) setResult(out);
    } catch (failure) {
      if (!mine.signal.aborted) fail(failure);
    } finally {
      setBusy(null);
    }
  }

  const canEstimate = textOk && paperId !== '' && selection !== null && busy === null;

  return (
    <section className="space-y-2" aria-labelledby="schema-preview-heading" data-testid="schema-preview">
      <h4 id="schema-preview-heading" className="text-sm font-semibold">5. Try it on one paper</h4>
      <p className="text-xs text-muted-foreground">
        A preview reads at most the first 3 pages of one paper with the model chosen above. It is not the real extraction
        (no agent, no merging), and nothing is stored: no KG, no terms.
      </p>
      <div className="space-y-1">
        <Label htmlFor="preview-paper">Paper</Label>
        <select id="preview-paper" className={SELECT_CLASS} value={paperId} onChange={event => { setPaperId(event.target.value); setResult(null); }}>
          <option value="">{papers.isPending ? 'Loading papers' : list.length === 0 ? 'No papers in the library' : 'Choose a paper'}</option>
          {list.map(p => <option key={p.id} value={p.id}>{p.title?.trim() ? p.title : p.file_name}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" disabled={!canEstimate} onClick={() => void estimateNow()} className="disabled:cursor-not-allowed disabled:opacity-50">
          {busy === 'estimate' ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Calculator aria-hidden="true" />}
          Preview (estimate first)
        </Button>
        {!textOk && <span className="text-xs text-muted-foreground">The schema must pass the checks first.</span>}
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive" data-testid="preview-error">
          <p>{error}</p>
          {problems.length > 0 && <ul className="list-disc pl-5">{problems.slice(0, 10).map((p, i) => <li key={i}>Line {p.line}: {p.message}</li>)}</ul>}
        </div>
      )}
      {estimate && current && !result && (
        <div className="space-y-2 rounded-md border border-border p-2 text-sm" data-testid="preview-estimate">
          <p>
            {estimate.value.paper}: {estimate.value.pages} {estimate.value.pages === 1 ? 'page' : 'pages'} (at most {estimate.value.max_pages}),{' '}
            {estimate.value.estimated_tokens === null ? 'tokens unknown' : `about ${estimate.value.estimated_tokens.toLocaleString('en-US')} tokens`},{' '}
            {costSentence(estimate.value.estimated_cost_usd)}, with {estimate.value.backend} / {estimate.value.model}.
            Each page is one model call of at most {estimate.value.limits.call_seconds} s; the whole preview stops after {estimate.value.limits.overall_seconds} s.
          </p>
          <Button type="button" size="sm" disabled={busy !== null} onClick={() => void run()}>
            {busy === 'run' ? <Loader2 aria-hidden="true" className="animate-spin" /> : <FlaskConical aria-hidden="true" />}
            Confirm and run the preview
          </Button>
        </div>
      )}
      {estimate && !current && !result && <p className="text-xs text-muted-foreground">The schema, paper or model changed; estimate again.</p>}
      <p role="status" aria-live="polite" className="text-xs text-muted-foreground">{busy === 'run' ? 'Reading the pages and asking the model, one call per page.' : ''}</p>
      {result && (
        <div className="space-y-2" data-testid="preview-result">
          <p className="text-sm" data-testid="preview-summary">
            {result.summary.terms} {result.summary.terms === 1 ? 'term' : 'terms'} from {result.pages_used.length} {result.pages_used.length === 1 ? 'page' : 'pages'}.
            Categories: {result.summary.categories_exact} exact, {result.summary.categories_mapped} mapped,{' '}
            {result.summary.categories_fell_back_to_thing} fell back to Thing.
            Relations: {result.summary.relations}, of which {result.summary.relations_became_related_to} became related_to and{' '}
            {result.summary.relations_unverified} stayed unverified.
          </p>
          {result.stopped && <p className="text-sm text-muted-foreground">Stopped early: {result.stopped}</p>}
          {result.page_results.filter(r => r.note).map(r => <p key={r.page} className="text-xs text-muted-foreground">Page {r.page}: {r.note}</p>)}
          <div className="max-h-72 space-y-2 overflow-auto rounded-md border border-border p-2">
            {result.terms.length === 0 && <p className="text-sm text-muted-foreground">The model returned no usable terms.</p>}
            {result.terms.map((term, index) => (
              <div key={`${term.term}-${index}`} className="text-sm" data-testid="preview-term">
                <p><span className="font-medium">{term.term}</span> <span className="text-xs text-muted-foreground">category {term.category}{term.raw_category && term.raw_category !== term.category ? ` (the model said ${term.raw_category})` : ''}, page {term.page}</span></p>
                {term.definition && <p className="text-xs">{term.definition}</p>}
                {term.relations.length > 0 && (
                  <ul className="list-disc pl-5 text-xs">
                    {term.relations.map((rel, i) => <li key={i}>{rel.relation} {rel.related_term} ({rel.verified ? 'verified' : 'not verified'})</li>)}
                  </ul>
                )}
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground" data-testid="preview-note">{result.note} This preview covers {result.pages_used.length} page(s) only and nothing was stored.</p>
        </div>
      )}
    </section>
  );
}
