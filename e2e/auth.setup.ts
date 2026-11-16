/**
 * Sign in ONCE per run and save the session, then every page test reuses it.
 *
 * The first design signed in inside each test: 58 admin pages = 58 sign-ins, against a server throttle of
 * 8 failures per 5 minutes (backend/auth_service.py, _MAX_FAILS = 8, _LOCKOUT = 300 s). It tripped the
 * lockout on its own and could never have finished, even with correct credentials.
 */
import { test as setup } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { CREDS, hasState, signIn, type Role } from './auth';

const BASE = process.env.E2E_BASE_URL || 'http://localhost:8000';
export const STATE_DIR = path.join(__dirname, '.auth');

export function statePath(role: Role): string {
  return CREDS[role].state || path.join(STATE_DIR, `${role}.json`);
}

for (const role of ['admin', 'user'] as const) {
  setup(`sign in once as ${role}`, async ({ page }) => {
    // A session captured by hand (E2E_<ROLE>_STATE) is used as-is: nothing to do here.
    setup.skip(hasState(role), `using the saved session in ${CREDS[role].state}`);
    setup.skip(!CREDS[role].pass, `no E2E_${role.toUpperCase()}_PASS, so the ${role} pages are skipped`);

    await signIn(page, role as Role, BASE);
    fs.mkdirSync(STATE_DIR, { recursive: true });
    await page.context().storageState({ path: statePath(role as Role) });
  });
}
