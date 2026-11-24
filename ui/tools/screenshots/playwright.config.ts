import { defineConfig, devices } from '@playwright/test';

// Developer tool, not part of any CI job: screenshots of the main pages and dialogs against the mocked backend.
const UI_PORT = Number.parseInt(process.env.F2W_UI_PORT || '5175', 10);
const UI_HOST = process.env.F2W_UI_HOST || '127.0.0.1';
const baseURL = `http://${UI_HOST}:${UI_PORT}`;

export default defineConfig({
  testDir: '.',
  testMatch: 'screenshots.spec.ts',
  fullyParallel: true,
  timeout: 45_000,
  use: { baseURL, locale: 'en-US', viewport: { width: 1280, height: 720 } },
  webServer: {
    command: `npm run dev -- --host ${UI_HOST} --port ${UI_PORT}`,
    cwd: '../..',
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: 'chrome', viewport: { width: 1280, height: 720 } } }],
});
