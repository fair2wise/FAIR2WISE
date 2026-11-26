/**
 * Real-backend browser smoke suite (backlog Tier 2 E, 2026-11): the production UI build against the migrated API
 * over a generated library, with the library jobs run by a real worker. Kept outside ui/ so the frozen ui tree hash
 * does not change (the T18B/T18C precedent). Specs are `*.lib.ts` so neither sibling config picks them up.
 */
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const UI_PORT = Number.parseInt(process.env.F2W_LIVE_UI_PORT || '5175', 10);
const ARTIFACTS = path.resolve(process.env.F2W_LIVE_ARTIFACTS || 'live-backend-artifacts');

export default defineConfig({
  testDir: __dirname,
  testMatch: /.*\.lib\.ts$/,
  outputDir: path.join(ARTIFACTS, 'test-results'),
  globalSetup: path.join(__dirname, 'global-setup.ts'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['json', { outputFile: path.join(ARTIFACTS, 'results.json') }]],
  timeout: 120_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: `http://127.0.0.1:${UI_PORT}`,
    trace: 'on',
    screenshot: 'on',
    video: 'off',
    locale: 'en-US',
  },
  projects: [
    {
      name: 'live-backend-library',
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
  ],
});
