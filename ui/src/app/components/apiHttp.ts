/**
 * Shared HTTP helpers for the `/v1` Library and Extraction clients (A8, T40).
 *
 * Mirrors `liveAgent.ts`: the base URL is `AGENT_API_BASE`, errors are rendered with
 * `parseAgentApiError`, and a transport failure uses `agentNetworkErrorMessage`.
 */
import { AGENT_API_BASE } from './data/liveAgent';
import { agentNetworkErrorMessage, parseAgentApiError } from './agentApiErrors';

/** Statuses that mean "this caller or server has no such route": the tab hides itself. */
export const HIDDEN_STATUSES: ReadonlySet<number> = new Set([401, 403, 404, 405]);

/** An HTTP failure from a `/v1` route, carrying the status for callers that branch on it. */
export class ApiError extends Error {
  readonly status: number;

  /**
   * @param message - Human readable message (already parsed from the response body).
   * @param status - HTTP status code; 0 for a transport failure.
   */
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** A query value: a scalar, or a list sent as a repeated parameter (`graph_ids=a&graph_ids=b`). */
export type QueryValue = string | number | boolean | undefined | ReadonlyArray<string | number>;

/** Build the absolute URL of a `/v1` path, with an optional query object (undefined values dropped). */
export function v1Url(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return `${AGENT_API_BASE}${path}${qs ? `?${qs}` : ''}`;
}

/**
 * Perform a request and return the raw response, throwing `ApiError` on a non-2xx status or a
 * transport failure. Cookies are sent same-origin, like `judgeModel.ts`.
 */
export async function v1Fetch(
  url: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetcher(url, { credentials: 'same-origin', ...init });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError(agentNetworkErrorMessage(AGENT_API_BASE, error), 0);
  }
  if (!response.ok) {
    throw new ApiError(parseAgentApiError(await response.text(), response.status), response.status);
  }
  return response;
}

/** `v1Fetch` then parse the JSON body as `T` (the caller owns the shape; the server is trusted). */
export async function v1Json<T>(
  url: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
): Promise<T> {
  const response = await v1Fetch(url, init, fetcher);
  return (await response.json()) as T;
}

/** JSON request body init for POST/PUT. */
export function jsonBody(method: 'POST' | 'PUT', body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}
