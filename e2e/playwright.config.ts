import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  // A wrong selector used to cost 60 s PER TEST: 116 tests x 60 s = 3.8 hours for no information.
  // Now a broken run dies in about a minute and says why.
  timeout: 45_000,
  expect: { timeout: 8_000 },
  retries: 0,
  maxFailures: 8,
  workers: 2,                      // gentle on the server: this may run against production
  reporter: [['html', { outputFolder: 'report', open: 'never' }], ['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
    storageState: process.env.E2E_STORAGE_STATE || undefined,
    ignoreHTTPSErrors: false,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
