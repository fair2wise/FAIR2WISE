import { describe, expect, it } from 'vitest';
import { ApiError } from './apiHttp';
import {
  CBORG_IP_NOT_AUTHORIZED,
  CBORG_IP_NOT_AUTHORIZED_TEXT,
  errorWithReason,
  modelListReason,
  providerReasonText,
} from './providerErrors';

describe('providerErrors', () => {
  it('names the CBORG address refusal specifically and never echoes an address', () => {
    expect(providerReasonText(CBORG_IP_NOT_AUTHORIZED, 'generic')).toBe(CBORG_IP_NOT_AUTHORIZED_TEXT);
    expect(CBORG_IP_NOT_AUTHORIZED_TEXT).toContain('LBLnet/VPN');
    expect(CBORG_IP_NOT_AUTHORIZED_TEXT).not.toMatch(/\d+\.\d+\.\d+\.\d+/);
  });

  it('keeps the generic message for any other reason', () => {
    expect(providerReasonText('gate_closed', 'generic')).toBe('generic');
    expect(providerReasonText(null, 'generic')).toBe('generic');
  });

  it('renders an ApiError by its typed reason, not its status text', () => {
    const error = new ApiError('Service unavailable', 503, 'unavailable', CBORG_IP_NOT_AUTHORIZED);
    expect(errorWithReason(error, 'x')).toBe(CBORG_IP_NOT_AUTHORIZED_TEXT);
    expect(errorWithReason(new ApiError('boom', 500), 'x')).toBe('boom');
    expect(errorWithReason('not an error', 'x')).toBe('x');
  });

  it('explains an unavailable model list from its error code', () => {
    expect(modelListReason({ error: 'ignored', error_code: CBORG_IP_NOT_AUTHORIZED })).toBe(CBORG_IP_NOT_AUTHORIZED_TEXT);
    expect(modelListReason({ error: 'no CBORG API key configured', error_code: null })).toBe('no CBORG API key configured');
    expect(modelListReason({})).toBe('');
  });
});
