/**
 * Specific wording for typed provider refusals the backend reports (`reason` of a `/v1` error body, `error_code`
 * of a model listing). CBORG authorizes by client IP: off LBLnet it answers 403 `ip_not_authorized`, which the
 * backend reports as reason `cborg_ip_not_authorized`. The text is fixed on purpose: the refusal body can name
 * the caller's address, and the UI never shows or builds a message from it.
 */
export const CBORG_IP_NOT_AUTHORIZED = 'cborg_ip_not_authorized';

export const CBORG_IP_NOT_AUTHORIZED_TEXT =
  'CBORG refused this network address; connect via LBLnet/VPN or authorize the address';

/** The message for a typed reason/code, or `fallback` when it is not a known provider refusal. */
export function providerReasonText(reason: string | null | undefined, fallback: string): string {
  return reason === CBORG_IP_NOT_AUTHORIZED ? CBORG_IP_NOT_AUTHORIZED_TEXT : fallback;
}

/** A thrown error rendered for a toast: an `ApiError`-like `reason` wins over its generic message. */
export function errorWithReason(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  const reason = (error as { reason?: unknown }).reason;
  return providerReasonText(typeof reason === 'string' ? reason : null, error.message || fallback);
}

/** Why a model list is unavailable: the specific provider refusal, else the server's reason, else nothing. */
export function modelListReason(list: { error?: string | null; error_code?: string | null }): string {
  return providerReasonText(list.error_code, list.error ?? '');
}
