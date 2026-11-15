/**
 * Signing in, against the forms this application actually has.
 *
 * Probed live on 2026-11-15:
 *   /admin/login   one password field, one other input, button.adl-btn      -> automatable
 *   /login         input[placeholder="Phone number"] + password + button.lp-btn,
 *                  THEN a WhatsApp one-time code ("6-digit code")           -> NOT automatable
 *
 * So an ordinary user's session cannot be scripted. Capture it once by hand instead:
 *
 *     npx.cmd playwright open --save-storage=user.json https://tradgo.us/login
 *     (sign in, including the code, then close the browser)
 *     $env:E2E_USER_STATE='user.json'
 *
 * Everything here fails FAST and LOUD. The first version waited 60 s per test for a selector that
 * never existed, which turned one wrong guess into a 3.8 hour run for no information at all.
 */
import { expect, type Page } from '@playwright/test';

export const FIELD_TIMEOUT = 8000;

export type Role = 'admin' | 'user';

export const CREDS = {
  admin: { user: process.env.E2E_ADMIN_USER, pass: process.env.E2E_ADMIN_PASS, state: process.env.E2E_ADMIN_STATE },
  user: { user: process.env.E2E_USER_USER, pass: process.env.E2E_USER_PASS, state: process.env.E2E_USER_STATE },
};

export function redact(s: string): string {
  let out = s;
  for (const c of [CREDS.admin.pass, CREDS.user.pass, CREDS.admin.user, CREDS.user.user]) {
    if (c) out = out.split(c).join('«redacted»');
  }
  return out;
}

/** True when this role already has a saved session, so no form has to be driven at all. */
export function hasState(role: Role): boolean {
  return Boolean(CREDS[role].state);
}

export async function signIn(page: Page, role: Role, base: string): Promise<void> {
  const { user, pass } = CREDS[role];
  if (!pass) {
    throw new Error(
      `no way to sign in as ${role}: set E2E_${role.toUpperCase()}_PASS, or capture a session once and set ` +
        `E2E_${role.toUpperCase()}_STATE (see auth.ts).`,
    );
  }

  if (role === 'admin') {
    await page.goto(`${base}/admin/login`, { waitUntil: 'domcontentloaded' });
    const pw = page.locator('input[type="password"]');
    await expect(pw, 'the admin sign-in password field').toBeVisible({ timeout: FIELD_TIMEOUT });
    if (user) {
      const other = page.locator('input:not([type="password"])').first();
      if (await other.count()) await other.fill(user);
    }
    await pw.fill(pass);
    await page.locator('button.adl-btn, button[type="submit"]').first().click();
  } else {
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' });
    const phone = page.locator('input[placeholder="Phone number"]');
    await expect(phone, 'the phone field on /login').toBeVisible({ timeout: FIELD_TIMEOUT });
    await phone.fill(user || '');
    await page.locator('input[type="password"]').fill(pass);
    await page.locator('button.lp-btn').first().click();

    // The one-time code step. No script can read a WhatsApp message, so say so plainly and stop.
    const otp = page.locator('input[placeholder="6-digit code"]');
    if (await otp.isVisible({ timeout: 4000 }).catch(() => false)) {
      throw new Error(
        'this account needs a WhatsApp one-time code, which cannot be automated. Capture the session once:\n' +
          '  npx.cmd playwright open --save-storage=user.json https://tradgo.us/login\n' +
          '  $env:E2E_USER_STATE=\'user.json\'   then run the suite again.',
      );
    }
  }

  await page.waitForLoadState('networkidle').catch(() => undefined);
  if (/\/login$/.test(new URL(page.url()).pathname)) {
    throw new Error(`${role} sign-in did not leave the login page: the credentials were refused.`);
  }
}
