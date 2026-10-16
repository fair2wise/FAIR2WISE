/**
 * T18C full-parity Playwright project: the frozen UI build against the migrated
 * API (create_api_app), a disposable Tiled 0.2.18 and fake inference. Kept
 * outside ui/ so the frozen ui tree hash does not change (same reason as the
 * T18B suite). See docs/migration/ui_reports/T18C_full_parity.md.
 */
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const UI_PORT = Number.parseInt(process.env.F2W_LIVE_UI_PORT || '5175', 10);
const ARTIFACTS = path.resolve(process.env.F2W_LIVE_ARTIFACTS || 'live-backend-artifacts');

export default defineConfig({
  testDir: __dirname,
  // *.full.ts, not *.spec.ts: the T18B config (testDir ..) matches *.spec.ts recursively.
  testMatch: /.*\.full\.ts$/,
  outputDir: path.join(ARTIFACTS, 'test-results'),
  globalSetup: path.join(__dirname, 'global-setup.ts'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['json', { outputFile: path.join(ARTIFACTS, 'results.json') }]],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${UI_PORT}`,
    trace: 'on',
    screenshot: 'on',
    video: 'off',
    locale: 'en-US',
  },
  projects: [
    {
      name: 'live-backend-full',
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
  ],
});
