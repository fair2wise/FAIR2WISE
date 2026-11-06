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
  } catch {
    // Plain-text error body.
  }

  return trimmed;
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
