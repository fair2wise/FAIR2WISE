import { describe, expect, it } from 'vitest';
import type { JobRunSummary } from '../extractionApi';
import { elapsedSeconds, formatDuration, outcomeLabel, papersPercent } from './runFormat';

const base: JobRunSummary = {
  outcome: 'running', state: 'running', started_at: '2026-11-23T10:00:00+00:00', finished_at: null, elapsed_seconds: 30,
  stage: { name: 'run', status: 'running', phase: null }, current_paper: null,
  papers: { total: 4, done: 1, failed: 1, skipped: 0 }, growth: {}, counters: {}, params: {}, error: null,
  logs: { kept: 0, total: 0, cap: 2000 },
};

describe('runFormat', () => {
  it('formats durations', () => {
    expect(formatDuration(42.9)).toBe('42 s');
    expect(formatDuration(185)).toBe('3 min 05 s');
    expect(formatDuration(3720)).toBe('1 h 02 min');
    expect(formatDuration(-5)).toBe('0 s');
  });

  it('ticks elapsed time from the server figure while moving (no browser/server clock difference) and stops once ended', () => {
    expect(elapsedSeconds(base, 'running', 1_000_000 + 12_000, 1_000_000)).toBe(42);
    expect(elapsedSeconds(base, 'running', 5, 1_000_000)).toBe(30); // never backwards
    expect(elapsedSeconds({ ...base, elapsed_seconds: 90 }, 'succeeded', 9_000_000, 1_000_000)).toBe(90);
    expect(elapsedSeconds(null, 'running', 1, 0)).toBeNull();
  });

  it('labels outcomes, partially failed with the counts', () => {
    expect(outcomeLabel('succeeded', null)).toEqual({ text: 'Succeeded', tone: 'ok' });
    expect(outcomeLabel('partially_failed', base)).toEqual({ text: 'Partially failed (1 of 2 papers failed)', tone: 'warn' });
    expect(outcomeLabel('failed', base).tone).toBe('bad');
    expect(outcomeLabel('paused_budget', null).text).toBe('Paused');
  });

  it('counts failed papers as handled in the percentage', () => {
    expect(papersPercent(base, {})).toBe(50);
    expect(papersPercent(null, { done: 1, total: 4 })).toBe(25);
    expect(papersPercent(null, {})).toBeNull();
  });
});
