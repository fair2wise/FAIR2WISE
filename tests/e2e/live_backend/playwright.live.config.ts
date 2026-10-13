/**
 * T18B live-backend Playwright project: the frozen UI build against the real
 * legacy FastAPI app with fake inference. Kept outside ui/ so the frozen ui
 * tree hash does not change. Run with ui/node_modules on NODE_PATH; see
 * docs/migration/ui_contracts/live_backend.md.
 */
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const UI_PORT = Number.parseInt(process.env.F2W_LIVE_UI_PORT || '5175', 10);
const ARTIFACTS = path.resolve(process.env.F2W_LIVE_ARTIFACTS || 'live-backend-artifacts');

export default defineConfig({
  testDir: __dirname,
  testMatch: /.*\.spec\.ts$/,
  outputDir: path.join(ARTIFACTS, 'test-results'),
  globalSetup: path.join(__dirname, 'global-setup.ts'),
  // One shared backend with server-side session/pending state: run serially.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['json', { outputFile: path.join(ARTIFACTS, 'results.json') }]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${UI_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'on',
    video: 'off',
    locale: 'en-US',
  },
  projects: [
    {
      name: 'live-backend',
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
  ],
});
