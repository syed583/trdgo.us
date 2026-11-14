/**
 * B.0 — the gate every kit must pass before it is installed.
 *
 * Walks every page as an administrator and as an ordinary user, and fails on a console error, a failed request,
 * a page over 3 s, a Python traceback leaking into the UI, or any forbidden string in a response a non-admin got.
 *
 * Credentials come from the environment and are redacted from every line this file prints.
 */
import { expect, test, type Page as PwPage, type Request } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { PAGES } from './pages';
import { FORBIDDEN, isDestructive } from './guards';

const BASE = process.env.E2E_BASE_URL || 'http://localhost:8000';
const SLOW_MS = 3000;
const OUT = path.join(__dirname, 'screenshots');
const LEAKS: any[] = [];

const CREDS = {
  admin: { user: process.env.E2E_ADMIN_USER, pass: process.env.E2E_ADMIN_PASS },
  user: { user: process.env.E2E_USER_USER, pass: process.env.E2E_USER_PASS },
};

/** Never let a credential reach a log, a report or a screenshot. */
function redact(s: string): string {
  let out = s;
  for (const c of [CREDS.admin.pass, CREDS.user.pass, CREDS.admin.user, CREDS.user.user]) {
    if (c) out = out.split(c).join('«redacted»');
  }
  return out;
}

async function signIn(page: PwPage, role: 'admin' | 'user') {
  const { user, pass } = CREDS[role];
  if (!user || !pass) test.skip(true, `no credentials for ${role} — set E2E_${role.toUpperCase()}_USER/PASS`);
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"], input[type="text"]', user!);
  await page.fill('input[type="password"]', pass!);
  await page.click('button[type="submit"]');
  await page.waitForLoadState('networkidle');
}

for (const role of ['admin', 'user'] as const) {
  test.describe(`${role}`, () => {
    test.beforeEach(async ({ page }) => {
      await signIn(page, role);
    });

    for (const p of PAGES) {
      test(`${role} · ${p.name} (${p.path})`, async ({ page }) => {
        const consoleErrors: string[] = [];
        const badRequests: string[] = [];

        page.on('console', (m) => {
          if (m.type() === 'error') consoleErrors.push(redact(m.text()));
        });
        page.on('requestfailed', (r: Request) => badRequests.push(`${r.method()} ${r.url()} failed`));
        page.on('response', async (res) => {
          const url = res.url();
          if (!url.includes('/api/')) return;
          const status = res.status();
          // 401 on a page this role cannot see is the correct answer, not a failure.
          const expectedRefusal = status === 401 || status === 403;
          if (status >= 400 && !(expectedRefusal && p.admin && role === 'user')) {
            badRequests.push(`${status} ${url}`);
          }
          if (role !== 'user' || status >= 400) return;
          let body = '';
          try { body = await res.text(); } catch { return; }
          for (const word of FORBIDDEN) {
            if (body.includes(word)) {
              LEAKS.push({ page: p.path, route: url, word });
            }
          }
        });

        const started = Date.now();
        const resp = await page.goto(BASE + p.path, { waitUntil: 'networkidle' });
        const elapsed = Date.now() - started;

        // an admin-only page must refuse an ordinary user
        if (p.admin && role === 'user') {
          const text = await page.textContent('body');
          expect(
            /not authorised|not authorized|administrator|forbidden|sign in/i.test(text || '') ||
              (resp?.status() ?? 200) >= 400,
            `an ordinary user reached ${p.path}`,
          ).toBeTruthy();
        }

        // no Python error should ever be visible in the browser
        const body = (await page.textContent('body')) || '';
        expect(body, `a Python error is visible on ${p.path}`).not.toMatch(
          /AttributeError|TypeError: |Traceback \(most recent call last\)|KeyError/,
        );

        for (const w of [320, 768, 1440]) {
          await page.setViewportSize({ width: w, height: 900 });
          await page.screenshot({
            path: path.join(OUT, `${p.name.replace(/[^\w]+/g, '_')}.${role}.${w}.png`),
            fullPage: true,
          });
        }

        expect(consoleErrors, `console errors on ${p.path}`).toEqual([]);
        expect(badRequests, `failed requests on ${p.path}`).toEqual([]);
        expect(elapsed, `${p.path} took ${elapsed} ms`).toBeLessThan(SLOW_MS);
      });
    }
  });
}

test.afterAll(async () => {
  fs.mkdirSync(path.join(__dirname), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'leaks.json'), JSON.stringify(LEAKS, null, 1));
  // A leak is a failure of the whole run, not of one page: the same route feeds several pages.
  expect(LEAKS, `the model's working reached an ordinary user: ${JSON.stringify(LEAKS.slice(0, 20))}`).toEqual([]);
});

test('the suite never clicks a destructive control', async () => {
  // guards.ts lists them; this test documents the rule and fails if the list is emptied by accident.
  expect(isDestructive('Place order')).toBeTruthy();
  expect(isDestructive('Acknowledge')).toBeTruthy();
  expect(isDestructive('Refresh')).toBeFalsy();
});
