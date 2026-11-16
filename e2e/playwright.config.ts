import { defineConfig } from '@playwright/test';
import * as path from 'path';

const STATE = (role: string) =>
  process.env[`E2E_${role.toUpperCase()}_STATE`] || path.join(__dirname, '.auth', `${role}.json`);

export default defineConfig({
  testDir: '.',
  // A wrong selector used to cost 60 s PER TEST: 116 tests x 60 s = 3.8 hours for no information.
  // Now a broken run dies in about a minute and says why.
  timeout: 45_000,
  expect: { timeout: 8_000 },
  retries: 0,
  // No maxFailures: the point of this suite is the FULL list in one run. It was capped at 8 while a wrong
  // selector could burn an hour per mistake; the fast-fail timeouts below make that unnecessary now.
  workers: 2,                      // gentle on the server: this may run against production
  reporter: [['html', { outputFolder: 'report', open: 'never' }], ['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
    ignoreHTTPSErrors: false,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    // ONE sign-in per role per run; every page test reuses the saved session. The first design signed in
    // inside each test - 58 admin pages = 58 logins - against a throttle of 8 failures per 5 minutes, so it
    // locked itself out and could never have finished.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'admin',
      dependencies: ['setup'],
      testMatch: /audit\.spec\.ts/,
      grep: /admin · |destructive/,
      use: { storageState: STATE('admin') },
    },
    {
      name: 'user',
      dependencies: ['setup'],
      testMatch: /audit\.spec\.ts/,
      grep: /user · /,
      use: { storageState: STATE('user') },
    },
  ],
});
