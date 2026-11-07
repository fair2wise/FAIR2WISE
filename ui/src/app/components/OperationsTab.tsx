import { useMutation } from '@tanstack/react-query';
import { Calculator, Loader2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ApiError } from './apiHttp';
import { useGraphSources } from './graphSourcesHooks';
import type { GraphSourceInfo } from './graphSourcesApi';
import {
  formatMetric,
  OPERATION_LABELS,
  runOperation,
  type OperationArgs,
  type OperationName,
  type OperationReport,
  type OperationResult,
} from './operationsApi';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';

/** Option text of a graph: its label and the operations id it is run under. */
export function graphOptionLabel(source: GraphSourceInfo): string {
  return source.graph_id && source.graph_id !== source.label ? `${source.label} (${source.graph_id})` : source.label;
}

/** The sources the operations can be run on: those the server gave a `graph_id` (deduplicated, JSON first). */
export function operableGraphs(sources: GraphSourceInfo[]): GraphSourceInfo[] {
  const seen = new Set<string>();
  return [...sources.filter(s => s.kind === 'json'), ...sources.filter(s => s.kind === 'tiled')].filter(source => {
    if (!source.graph_id || seen.has(source.graph_id)) return false;
    seen.add(source.graph_id);
    return true;
  });
}

/** Message for a failed read: the server's message; a 501 and a 404 get a plain explanation. */
export function operationErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 501) return `Not supported for this graph: ${error.message}`;
    if (error.status === 404) return `Not found: ${error.message}`;
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring';

interface FormState {
  operation: OperationName;
  graphId: string;
  compareIds: string[];
  paper: string;
  maxNodes: string;
  maxEdges: string;
  since: string;
  limit: string;
  corpus: string;
  sections: string;
}

const EMPTY_FORM: FormState = {
  operation: 'kg_compute_statistics', graphId: '', compareIds: [], paper: '', maxNodes: '', maxEdges: '',
  since: '', limit: '', corpus: '', sections: '',
};

const int = (value: string): number | undefined => {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

/** Build the request from the form, or a reason it cannot be sent yet. */
export function buildArgs(form: FormState): { args: OperationArgs } | { problem: string } {
  switch (form.operation) {
    case 'kg_compute_statistics':
      return form.graphId ? { args: { operation: form.operation, graph_id: form.graphId } } : { problem: 'Choose a graph.' };
    case 'kg_compare_snapshots':
      return form.compareIds.length >= 2
        ? { args: { operation: form.operation, graph_ids: form.compareIds } }
        : { problem: 'Choose at least two graphs, in order.' };
    case 'kg_paper_subgraph': {
      const paper = form.paper.trim();
      if (!form.graphId) return { problem: 'Choose a graph.' };
      if (paper.length < 3) return { problem: 'Enter a DOI, arXiv id or paper key (at least 3 characters).' };
      return { args: { operation: form.operation, graph_id: form.graphId, paper, max_nodes: int(form.maxNodes), max_edges: int(form.maxEdges) } };
    }
    case 'kg_recent_entities':
      return form.graphId
        ? { args: { operation: form.operation, graph_id: form.graphId, since: form.since.trim() || undefined, limit: int(form.limit) } }
        : { problem: 'Choose a graph.' };
    case 'curate_audit_corpus': {
      const corpus = form.corpus.trim();
      if (!corpus) return { problem: 'Enter the corpus name (a file stem under storage/terminology).' };
      const sections = form.sections.split(',').map(s => s.trim()).filter(Boolean);
      return { args: { operation: form.operation, corpus, sections: sections.length ? sections : undefined } };
    }
  }
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === 'ok' ? 'bg-emerald-100 text-emerald-800' : status === 'partial' ? 'bg-amber-100 text-amber-900' : 'bg-slate-200 text-slate-800';
  return (
    <span data-testid="operation-status" className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${tone}`}>
      {status}
    </span>
  );
}

function MetricsTable({ report }: { report: OperationReport }) {
  const rows = Object.entries(report.metrics);
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No metrics in this report.</p>;
  return (
    <table className="w-full text-sm" aria-label="Operation metrics">
      <thead>
        <tr className="text-left text-xs uppercase text-muted-foreground">
          <th className="py-1 pr-3">Metric</th>
          <th className="py-1 pr-3">Value</th>
          <th className="py-1">Counting unit</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([key, value]) => (
          <tr key={key} className="border-t border-border align-top">
            <td className="py-1 pr-3 font-mono text-xs">{key}</td>
            <td className="py-1 pr-3 break-all">{formatMetric(value)}</td>
            <td className="py-1 text-xs text-muted-foreground">{report.counting_units[key] ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ResultView({ result }: { result: OperationResult }) {
  const report = result.data;
  return (
    <section aria-label="Operation result" className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <header className="flex flex-wrap items-center gap-2">
        <StatusBadge status={result.status} />
        <span className="font-mono text-xs">{result.tool ?? ''}</span>
        {report?.method ? <span className="text-xs text-muted-foreground">method {report.method}</span> : null}
      </header>
      {result.warnings.length > 0 ? (
        <ul role="list" aria-label="Warnings" className="list-disc pl-5 text-sm text-amber-900">
          {result.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {report ? (
        <>
          {report.population ? (
            <p className="text-sm">
              <span className="font-semibold">Population:</span> {report.population}
            </p>
          ) : null}
          {report.coverage ? (
            <p className="text-sm text-muted-foreground" data-testid="operation-coverage">
              Coverage: {report.completeness ?? 'unknown'}
              {report.coverage.exactness ? `, ${report.coverage.exactness}` : ''}
              {report.coverage.records_scanned !== null ? `, ${report.coverage.records_scanned.toLocaleString()} records scanned` : ''}
              {report.coverage.reasons.length ? ` (${report.coverage.reasons.join('; ')})` : ''}
            </p>
          ) : null}
          <MetricsTable report={report} />
          {Object.keys(report.excluded).length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer">Excluded ({Object.keys(report.excluded).length})</summary>
              <ul className="list-disc pl-5">
                {Object.entries(report.excluded).map(([key, reason]) => (
                  <li key={key}>
                    <span className="font-mono text-xs">{key}</span>: {reason}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {Object.keys(report.preview).length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer">
                Preview{report.display_truncated ? ' (truncated)' : ''}
              </summary>
              <pre className="max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(report.preview, null, 2)}</pre>
            </details>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

/**
 * Operations tab (T19E frontend closure): runs the five Knowledge Operations the server already exposes on
 * `GET /v1/operations/*` and shows the bounded typed result (status, population, coverage, metrics with their
 * counting units, warnings, preview). Read-only: it submits no job and edits nothing. Graphs are the sources of
 * `GET /v1/graph/sources` that carry a `graph_id`; the tab itself is shown by `App` only when
 * `GET /v1/capabilities` reports `can_run_operations` (the `chat.ask` entry capability of these routes).
 */
export function OperationsTab() {
  const sourcesQuery = useGraphSources();
  const graphs = useMemo(() => operableGraphs(sourcesQuery.data?.sources ?? []), [sourcesQuery.data]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const run = useMutation({ mutationFn: (args: OperationArgs) => runOperation(args) });

  const meta = OPERATION_LABELS.find(entry => entry.name === form.operation) ?? OPERATION_LABELS[0];
  const built = buildArgs(form);
  const update = (patch: Partial<FormState>) => setForm(current => ({ ...current, ...patch }));
  const needsGraph = form.operation !== 'curate_audit_corpus' && form.operation !== 'kg_compare_snapshots';
  const noGraphs = sourcesQuery.isSuccess && graphs.length === 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if ('args' in built) run.mutate(built.args);
  };

  return (
    <section
      aria-labelledby="operations-tab-title"
      className="flex h-full w-full flex-col gap-4 overflow-auto rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <header className="flex items-center gap-2">
        <Calculator aria-hidden="true" size={24} />
        <h2 id="operations-tab-title" className="text-lg font-semibold">
          Operations
        </h2>
        <span className="text-sm text-muted-foreground">read-only computations over the knowledge graphs and corpora</span>
      </header>

      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2" aria-label="Operation parameters">
        <div className="flex flex-col gap-1">
          <Label htmlFor="op-name">Operation</Label>
          <select
            id="op-name"
            className={SELECT_CLASS}
            value={form.operation}
            onChange={event => update({ operation: event.target.value as OperationName })}
          >
            {OPERATION_LABELS.map(entry => (
              <option key={entry.name} value={entry.name}>
                {entry.label}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">{meta.help}</p>
        </div>

        {needsGraph ? (
          <div className="flex flex-col gap-1">
            <Label htmlFor="op-graph">Graph</Label>
            <select id="op-graph" className={SELECT_CLASS} value={form.graphId} onChange={event => update({ graphId: event.target.value })}>
              <option value="">{sourcesQuery.isPending ? 'Loading graphs...' : 'Choose a graph'}</option>
              {graphs.map(source => (
                <option key={source.graph_id ?? source.id} value={source.graph_id ?? ''}>
                  {graphOptionLabel(source)}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        {form.operation === 'kg_compare_snapshots' ? (
          <div className="flex flex-col gap-1">
            <Label htmlFor="op-compare">Graphs (in order)</Label>
            <select
              id="op-compare"
              multiple
              className={`${SELECT_CLASS} h-auto min-h-24`}
              value={form.compareIds}
              onChange={event => {
                // Keep the order the graphs were chosen in (growth is read in that order), not the list order.
                const picked = Array.from(event.target.selectedOptions, option => option.value);
                update({ compareIds: [...form.compareIds.filter(id => picked.includes(id)), ...picked.filter(id => !form.compareIds.includes(id))] });
              }}
            >
              {graphs.map(source => (
                <option key={source.graph_id ?? source.id} value={source.graph_id ?? ''}>
                  {graphOptionLabel(source)}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {form.compareIds.length ? `Order: ${form.compareIds.join(' -> ')}` : 'Select two or more; the listing order is used.'}
            </p>
          </div>
        ) : null}

        {form.operation === 'kg_paper_subgraph' ? (
          <>
            <div className="flex flex-col gap-1">
              <Label htmlFor="op-paper">Paper</Label>
              <Input id="op-paper" value={form.paper} maxLength={300} placeholder="10.1021/... or arXiv:... or paper key" onChange={event => update({ paper: event.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="op-max-nodes">Max nodes</Label>
                <Input id="op-max-nodes" inputMode="numeric" value={form.maxNodes} placeholder="default" onChange={event => update({ maxNodes: event.target.value })} />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="op-max-edges">Max edges</Label>
                <Input id="op-max-edges" inputMode="numeric" value={form.maxEdges} placeholder="default" onChange={event => update({ maxEdges: event.target.value })} />
              </div>
            </div>
          </>
        ) : null}

        {form.operation === 'kg_recent_entities' ? (
          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="op-since">Since</Label>
              <Input id="op-since" value={form.since} maxLength={40} placeholder="2026-01-01T00:00:00Z" onChange={event => update({ since: event.target.value })} />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="op-limit">Limit</Label>
              <Input id="op-limit" inputMode="numeric" value={form.limit} placeholder="default" onChange={event => update({ limit: event.target.value })} />
            </div>
          </div>
        ) : null}

        {form.operation === 'curate_audit_corpus' ? (
          <>
            <div className="flex flex-col gap-1">
              <Label htmlFor="op-corpus">Corpus</Label>
              <Input id="op-corpus" value={form.corpus} maxLength={128} placeholder="extracted_terms_..." onChange={event => update({ corpus: event.target.value })} />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="op-sections">Sections (optional, comma-separated)</Label>
              <Input id="op-sections" value={form.sections} placeholder="summary, stubs" onChange={event => update({ sections: event.target.value })} />
            </div>
          </>
        ) : null}

        <div className="flex items-end gap-3 md:col-span-2">
          <Button type="submit" disabled={'problem' in built || run.isPending}>
            {run.isPending ? <Loader2 aria-hidden="true" className="mr-1 animate-spin" size={16} /> : null}
            Run
          </Button>
          {'problem' in built ? <span className="text-sm text-muted-foreground">{built.problem}</span> : null}
          {noGraphs && needsGraph ? <span className="text-sm text-muted-foreground">The server lists no graph with an operations id.</span> : null}
        </div>
      </form>

      {run.isError ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {operationErrorMessage(run.error)}
        </p>
      ) : null}
      {run.data ? <ResultView result={run.data} /> : null}
    </section>
  );
}
