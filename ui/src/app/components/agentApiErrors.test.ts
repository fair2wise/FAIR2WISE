import { describe, expect, it } from 'vitest';
import {
  agentNetworkErrorMessage,
  parseAgentApiError,
  settingsApiErrorMessage,
} from './agentApiErrors';

describe('agentApiErrors', () => {
  it('parses JSON error bodies', () => {
    expect(parseAgentApiError('{"detail":"Invalid graph"}', 400)).toBe('Invalid graph');
  });

  it('returns a helpful message for settings 404', () => {
    expect(settingsApiErrorMessage('{"detail":"Not Found"}', 404)).toContain('Restart the FAIR2WISE agent backend');
  });

  it('explains failed fetch as backend unreachable', () => {
    expect(agentNetworkErrorMessage('http://127.0.0.1:8090', new TypeError('Failed to fetch')))
      .toContain('Cannot reach the FAIR2WISE agent backend');
    expect(agentNetworkErrorMessage('http://127.0.0.1:8090', new TypeError('Failed to fetch')))
      .toContain('start_agent_backend.sh');
  });
});
