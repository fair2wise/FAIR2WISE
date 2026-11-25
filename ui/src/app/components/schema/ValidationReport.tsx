import { CheckCircle2, Loader2, TriangleAlert } from 'lucide-react';
import type { SchemaReport } from './schemaApi';
import type { SchemaValidation } from './schemaHooks';

/** Most problems listed (the count is always exact). */
export const MAX_LISTED_PROBLEMS = 100;

/** Props of {@link ValidationReport}. */
export interface ValidationReportProps {
  validation: Pick<SchemaValidation, 'status' | 'report' | 'checkedText' | 'error'>;
  /** The text in the editor now: a report for another text is shown as out of date. */
  text: string;
  /** Click on a problem: scroll to and select its line. */
  onGoToLine: (line: number) => void;
  /** Text over the server cap is never sent. */
  overCap?: boolean;
}

/** One sentence for the live status region. */
export function statusSentence(
  validation: Pick<SchemaValidation, 'status' | 'report' | 'checkedText' | 'error'>,
  text: string,
  overCap = false,
): string {
  if (overCap) return 'The text is over the 256 KiB limit and was not checked.';
  if (text.trim() === '') return 'Nothing to check yet. Describe a domain and send, paste YAML, or upload a file.';
  if (validation.status === 'error') return `The check could not run: ${validation.error ?? 'unknown error'}`;
  if (validation.status === 'waiting') return 'Waiting for you to stop typing, then checking.';
  if (validation.status === 'checking') return 'Checking.';
  const report = validation.report;
  if (!report) return 'Not checked yet.';
  const n = report.problems.length;
  return report.ok ? 'No problems found.' : `${n} ${n === 1 ? 'problem' : 'problems'} found.`;
}

/**
 * The validator's answer: a live status line, the problems with their line numbers (a button each that selects the
 * line in the editor), and exactly what was checked and what was not, in the server's own words. All server text is
 * rendered as plain text.
 */
export function ValidationReport({ validation, text, onGoToLine, overCap = false }: ValidationReportProps) {
  const report: SchemaReport | null = validation.report;
  const stale = report !== null && validation.checkedText !== text;
  const busy = validation.status === 'waiting' || validation.status === 'checking';
  const listed = report?.problems.slice(0, MAX_LISTED_PROBLEMS) ?? [];
  const sentence = statusSentence(validation, text, overCap);
  const ok = report?.ok === true && !stale && !busy && !overCap;

  return (
    <div className="space-y-2" data-testid="validation-report">
      <p
        role="status"
        aria-live="polite"
        id="schema-validation-status"
        data-testid="validation-status"
        className="flex items-center gap-2 text-sm"
      >
        {busy ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
        {ok ? <CheckCircle2 aria-hidden="true" className="size-4" /> : null}
        {!busy && !ok && report && !report.ok ? <TriangleAlert aria-hidden="true" className="size-4" /> : null}
        <span>{sentence}</span>
        {stale && !busy && report ? <span className="text-xs text-muted-foreground">(the list below is for an earlier text)</span> : null}
      </p>
      {listed.length > 0 && (
        <ol className="space-y-1 text-sm" aria-label="Problems by line" data-testid="problem-list">
          {listed.map((problem, index) => (
            <li key={`${problem.line}-${problem.code}-${index}`}>
              <button
                type="button"
                onClick={() => onGoToLine(problem.line)}
                className="w-full rounded px-2 py-1 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <span className="font-mono text-xs font-semibold">Line {problem.line}{problem.column ? `, column ${problem.column}` : ''}:</span>{' '}
                <span>{problem.message}</span>{' '}
                {problem.code ? <span className="font-mono text-xs text-muted-foreground">[{problem.code}]</span> : null}
                {problem.fixable ? <span className="text-xs text-muted-foreground"> (Insert required core fixes this)</span> : null}
              </button>
            </li>
          ))}
          {(report?.problems.length ?? 0) > listed.length && (
            <li className="px-2 text-xs text-muted-foreground">and {(report?.problems.length ?? 0) - listed.length} more</li>
          )}
        </ol>
      )}
      {report && report.warnings.length > 0 && (
        <div className="space-y-1" data-testid="warning-list">
          <p className="text-sm font-medium">{report.warnings.length === 1 ? '1 warning' : `${report.warnings.length} warnings`} (the schema can still be saved)</p>
          <ul aria-label="Warnings by line" className="space-y-1 text-sm">
            {report.warnings.slice(0, MAX_LISTED_PROBLEMS).map((warning, index) => (
              <li key={`${warning.line}-${warning.code}-${index}`}>
                <button
                  type="button"
                  onClick={() => onGoToLine(warning.line)}
                  className="w-full rounded px-2 py-1 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span className="font-mono text-xs font-semibold">Line {warning.line}:</span> <span>{warning.message}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {report && (
        <details className="text-xs text-muted-foreground" data-testid="checked-list">
          <summary className="cursor-pointer">What was checked, and what was not</summary>
          <p className="mt-1 font-medium text-foreground">Checked</p>
          <ul className="list-disc pl-5">
            {report.checked.map(item => <li key={item}>{item}</li>)}
          </ul>
          <p className="mt-1 font-medium text-foreground">Not checked</p>
          <p data-testid="report-note">{report.note}</p>
        </details>
      )}
    </div>
  );
}
