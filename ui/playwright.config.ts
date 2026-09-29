import { defineConfig, devices } from '@playwright/test';

const UI_PORT = Number.parseInt(process.env.F2W_UI_PORT || '5175', 10);
const UI_HOST = process.env.F2W_UI_HOST || '127.0.0.1';
const baseURL = `http://${UI_HOST}:${UI_PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'off',
    locale: 'en-US',
  },
  webServer: {
    command: `npm run dev -- --host ${UI_HOST} --port ${UI_PORT}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
      },
    },
  ],
});
