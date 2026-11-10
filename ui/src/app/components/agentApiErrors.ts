export function parseAgentApiError(detail: string, status: number): string {
  const trimmed = detail.trim();
  if (!trimmed) {
    return `Agent API returned ${status}`;
  }

  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown };
    if (typeof parsed.detail === 'string') {
      return parsed.detail;
    }
    // Typed errors of the graph sources: {"detail": {"code": "...", "message": "..."}}.
    if (parsed.detail && typeof parsed.detail === 'object' && !Array.isArray(parsed.detail)) {
      const message = (parsed.detail as { message?: unknown }).message;
      if (typeof message === 'string' && message) return message;
    }
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map(item => (typeof item === 'object' && item && 'msg' in item ? String(item.msg) : String(item)))
        .join('; ');
    }
    // Typed /v1 contract errors: {"code": "...", "message": "...", "retryable": false, "reason"?: "..."}.
    const top = parsed as { code?: unknown; message?: unknown };
    if (parsed.detail === undefined && typeof top.code === 'string' && typeof top.message === 'string' && top.message) {
      return top.message;
    }
  } catch {
    // Plain-text error body.
  }

  return trimmed;
}

/**
 * The typed error of a `/v1` body: `{code, message, retryable, reason?}` at the top level (the contract-error
 * handler) or `{detail: {code, ...}}` (the graph-source routes). `null` when the body carries none.
 */
export function parseAgentApiErrorCode(detail: string): { code: string | null; reason: string | null } {
  const none = { code: null, reason: null };
  const trimmed = detail.trim();
  if (!trimmed) return none;
  try {
    const parsed = JSON.parse(trimmed) as { code?: unknown; reason?: unknown; detail?: unknown };
    const nested = parsed.detail && typeof parsed.detail === 'object' && !Array.isArray(parsed.detail)
      ? (parsed.detail as { code?: unknown; reason?: unknown })
      : null;
    const code = typeof parsed.code === 'string' ? parsed.code : typeof nested?.code === 'string' ? nested.code : null;
    const reason = typeof parsed.reason === 'string' ? parsed.reason : typeof nested?.reason === 'string' ? nested.reason : null;
    return { code, reason };
  } catch {
    return none;
  }
}

export function agentNetworkErrorMessage(apiBase: string, error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (message === 'Failed to fetch' || message.includes('NetworkError') || message.includes('Load failed')) {
    return `Cannot reach the FAIR2WISE agent backend at ${apiBase}. Start it with: bash scripts/start_agent_backend.sh`;
  }
  return message;
}

export function settingsApiErrorMessage(detail: string, status: number): string {
  if (status === 404) {
    return 'Settings endpoint not found. Restart the FAIR2WISE agent backend so the new API route is loaded.';
  }
  return parseAgentApiError(detail, status);
}
