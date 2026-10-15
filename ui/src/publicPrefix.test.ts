// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { normalizePrefix, publicLayout } from '../vite.config';

// T24C: the build-side half of app/modules/deployment/gateway.py (same rules, same manifest).
describe('public prefix build settings', () => {
  it('keeps the unprefixed developer layout when F2W_PUBLIC_PREFIX is unset', () => {
    expect(publicLayout({})).toEqual({ public_prefix: '', ui_base: '/', api_base: '', docs_url: '/docs/' });
    expect(publicLayout({ F2W_PUBLIC_PREFIX: '/' }).ui_base).toBe('/');
  });

  it('derives base, same-origin API and docs from /fair2wise', () => {
    expect(publicLayout({ F2W_PUBLIC_PREFIX: '/fair2wise/' })).toEqual({
      public_prefix: '/fair2wise', ui_base: '/fair2wise/', api_base: '/fair2wise/api', docs_url: '/fair2wise/docs/',
    });
    expect(publicLayout({ F2W_PUBLIC_PREFIX: '/fair2wise', VITE_F2W_AGENT_API_URL: '/fair2wise/api/' }).api_base)
      .toBe('/fair2wise/api');
  });

  it('refuses a prefixed build whose API or docs disagree with the gateway', () => {
    expect(() => publicLayout({ F2W_PUBLIC_PREFIX: '/fair2wise', VITE_F2W_AGENT_API_URL: 'http://127.0.0.1:8090' }))
      .toThrow(/disagrees/);
    expect(() => publicLayout({ F2W_PUBLIC_PREFIX: '/fair2wise', VITE_F2W_AGENT_API_URL: '/api' })).toThrow(/disagrees/);
    expect(() => publicLayout({ F2W_PUBLIC_PREFIX: '/fair2wise', VITE_F2W_DOCS_URL: '/docs/' })).toThrow(/outside/);
  });

  it.each(['fair2wise', 'http://x/fair2wise', '/a/../b', '/a//b', '/a b', '/%2e'])('rejects %s', raw => {
    expect(() => normalizePrefix(raw)).toThrow();
  });
});
