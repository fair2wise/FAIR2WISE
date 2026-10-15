import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// T24C: the unchanged e2e specs against a production build with F2W_PUBLIC_PREFIX=/fair2wise,
// served by e2e/prefix-gateway.mjs (the docker/nginx.conf public routes) instead of `vite dev`.
// The page's same-origin /fair2wise/api calls are mocked by e2e/fixtures/agent.ts.
//   F2W_UI_PORT=<free port> npx playwright test -c playwright.prefix.config.ts
const UI_PORT = Number.parseInt(process.env.F2W_UI_PORT || '5176', 10);
const UI_HOST = '127.0.0.1';
const ROOT = 'dist/prefix-root';
process.env.F2W_E2E_API_BASE = '/fair2wise/api';

export default defineConfig({
  ...base,
  use: { ...base.use, baseURL: `http://${UI_HOST}:${UI_PORT}` },
  webServer: {
    command:
      `npx vite build --outDir ${ROOT}/fair2wise --emptyOutDir --logLevel warn && ` +
      `node e2e/prefix-gateway.mjs ${ROOT} ${UI_PORT}`,
    env: { F2W_PUBLIC_PREFIX: '/fair2wise' },
    url: `http://${UI_HOST}:${UI_PORT}/fair2wise/healthz`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
