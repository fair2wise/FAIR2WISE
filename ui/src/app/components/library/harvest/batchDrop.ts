import {
  fileHarvestPdf,
  fileHarvestRunPdf,
  MAX_FILES_PER_DROP,
  type HarvestFileResult,
  type HarvestNeedsConfirmation,
  type HarvestRunDropResult,
  type MatchKind,
} from '../../harvestApi';
import { isPdfFile, uploadErrorMessage } from '../../libraryApi';

/** How many files of one drop are in flight at the same time. */
export const BATCH_CONCURRENCY = 2;

/** Outcome of one file of a multi-file drop. */
export type BatchOutcome =
  | { state: 'queued' }
  | { state: 'sending' }
  | { state: 'filed'; kind: MatchKind; confirmed: boolean; candidateId: string; title: string; duplicate: boolean }
  | { state: 'confirm'; answer: HarvestNeedsConfirmation }
  | { state: 'none' }
  | { state: 'rejected'; message: string }
  | { state: 'error'; message: string };

/** One row of the per-file result table. */
export interface BatchRow {
  key: string;
  file: File;
  outcome: BatchOutcome;
}

/** What a drop of `files` leads to: the files to send (PDFs) and the ones refused without sending anything. */
export type DropPlan =
  | { ok: true; send: File[]; notPdf: File[] }
  | { ok: false; message: string };

/** Plan a multi-file drop: more than {@link MAX_FILES_PER_DROP} files are refused whole (nothing is sent). */
export function planDrop(files: readonly File[]): DropPlan {
  if (files.length > MAX_FILES_PER_DROP) {
    return {
      ok: false,
      message: `You dropped ${files.length} files; at most ${MAX_FILES_PER_DROP} can be dropped at once. `
        + 'Nothing was sent. Drop fewer files.',
    };
  }
  return { ok: true, send: files.filter(isPdfFile), notPdf: files.filter(file => !isPdfFile(file)) };
}

/** The table cell text for a file's matched paper (empty while unknown). */
export function matchedTitle(outcome: BatchOutcome): string | null {
  if (outcome.state === 'filed') return outcome.title;
  if (outcome.state === 'confirm') return outcome.answer.candidate.title;
  return null;
}

/** The outcome that answers a batch request. */
export function outcomeOf(result: HarvestRunDropResult): BatchOutcome {
  if (result.filed) {
    return { state: 'filed', kind: result.verdict.kind, confirmed: false, candidateId: result.candidate.id,
      title: result.candidate.title, duplicate: result.duplicate };
  }
  if ('unmatched' in result) return { state: 'none' };
  return { state: 'confirm', answer: result };
}

/**
 * Run `worker` over `items` with at most `limit` in flight. A worker that throws does not stop the others (the
 * caller's worker is expected to catch and record its own failure); `stopped()` ends the pool early.
 */
export async function runPool<T>(items: readonly T[], limit: number, worker: (item: T) => Promise<void>,
  stopped: () => boolean = () => false): Promise<void> {
  let next = 0;
  const lane = async () => {
    while (next < items.length && !stopped()) {
      const item = items[next];
      next += 1;
      try {
        await worker(item);
      } catch {
        // the worker records its own failure; one file never fails the batch
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
}

/** Send one file as its own request to the run's batch route and describe what came back. */
export async function dropOne(runId: string, file: File, fetcher: typeof fetch = fetch): Promise<{
  outcome: BatchOutcome; result: HarvestFileResult | null;
}> {
  try {
    const answer = await fileHarvestRunPdf(runId, file, fetcher);
    return { outcome: outcomeOf(answer), result: answer.filed ? answer : null };
  } catch (error) {
    return { outcome: { state: 'error', message: uploadErrorMessage(error) }, result: null };
  }
}

/** Confirm a weak match: repeat the request on the candidate's route with `confirm=true`. */
export async function confirmOne(candidateId: string, file: File, fetcher: typeof fetch = fetch): Promise<{
  outcome: BatchOutcome; result: HarvestFileResult | null;
}> {
  try {
    const answer = await fileHarvestPdf(candidateId, file, { confirm: true }, fetcher);
    if (!answer.filed) return { outcome: { state: 'confirm', answer }, result: null };
    return {
      outcome: { state: 'filed', kind: answer.verdict.kind, confirmed: true, candidateId: answer.candidate.id,
        title: answer.candidate.title, duplicate: answer.duplicate },
      result: answer,
    };
  } catch (error) {
    return { outcome: { state: 'error', message: uploadErrorMessage(error) }, result: null };
  }
}
