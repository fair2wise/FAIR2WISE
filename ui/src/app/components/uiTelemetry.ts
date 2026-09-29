import { AGENT_API_BASE } from './data/liveAgent';

export interface UiTelemetryEvent {
  type: string;
  turn_id?: string;
  ts: string;
  data?: Record<string, unknown>;
}

const FLUSH_MS = 5000;
const queue: UiTelemetryEvent[] = [];
let flushTimer: number | undefined;
let activeTurnId: string | undefined;
let installed = false;

export function setActiveTurnId(turnId?: string | null): void {
  activeTurnId = turnId || undefined;
}

export function currentTurnId(): string | undefined {
  return activeTurnId;
}

export function recordUiEvent(
  type: string,
  data?: Record<string, unknown>,
  turnId?: string | null,
): void {
  const resolved = turnId || activeTurnId;
  const event: UiTelemetryEvent = {
    type,
    ts: new Date().toISOString(),
  };
  if (resolved) event.turn_id = resolved;
  if (data && Object.keys(data).length > 0) event.data = data;
  queue.push(event);
  if (flushTimer === undefined && typeof window !== 'undefined') {
    flushTimer = window.setTimeout(() => {
      flushTimer = undefined;
      void flushUiEvents();
    }, FLUSH_MS);
  }
}

export async function flushUiEvents(): Promise<void> {
  if (flushTimer !== undefined && typeof window !== 'undefined') {
    window.clearTimeout(flushTimer);
    flushTimer = undefined;
  }
  if (queue.length === 0) return;
  const events = queue.splice(0, queue.length);
  try {
    await fetch(`${AGENT_API_BASE}/telemetry/ui`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events }),
      keepalive: true,
    });
  } catch {
    // Telemetry must not break the chat UI.
  }
}

export function installUiTelemetry(): void {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') void flushUiEvents();
  });
}

export async function postTurnAnnotation(input: {
  turnId: string;
  verdict: 'pass' | 'fail';
  failureTags: string[];
  note: string;
  annotator?: string;
}): Promise<boolean> {
  try {
    const response = await fetch(`${AGENT_API_BASE}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        turn_id: input.turnId,
        verdict: input.verdict,
        failure_tags: input.failureTags,
        note: input.note,
        annotator: input.annotator || '',
        ts: new Date().toISOString(),
      }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export function traceUrl(turnId: string): string {
  const path = `${AGENT_API_BASE}/traces/${encodeURIComponent(turnId)}`;
  if (/^https?:\/\//.test(path)) return path;
  if (typeof window === 'undefined') return path;
  return new URL(path, window.location.origin).toString();
}
