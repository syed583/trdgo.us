/**
 * Capture the REAL pages - not the demo frames - at each frame's exact canvas size.
 *
 * The existing _shoot.mjs captures figma-demo's frame components, which answered "did the demo
 * reproduce the design". This answers the different question the owner asked: "does the SHIPPING page
 * match the design". Those are not the same picture, because the shipping page shows real data and
 * leaves out everything in the design we cannot measure.
 *
 * WHAT A DIFF HERE CAN AND CANNOT TELL YOU. There is no backend attached (a local backend on the
 * production database is forbidden), so every panel renders its empty state. Differences inside a
 * data region are therefore expected and meaningless. What the numbers DO measure is the chrome:
 * the rail, the top bar, page padding, card surfaces, radii, shadows, type and spacing - which is
 * exactly what the design dictates and what we control.
 *
 * PNG at deviceScaleFactor 1 so one captured pixel is one design pixel and nothing is rescaled.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const PAGES = JSON.parse(fs.readFileSync(new URL('./_pages.json', import.meta.url), 'utf8'));
const BASE = process.env.BASE || 'http://localhost:5311';
const only = process.argv[2];
const out = new URL('./_compare/real/', import.meta.url);
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
for (const [key, [route, w, h, theme]] of Object.entries(PAGES)) {
  if (only && only !== key) continue;
  // EACH FRAME IS CAPTURED IN ITS OWN THEME. Nine of the twelve frames are drawn dark and three
  // light; capturing everything light compared a dark design against a light page and reported the
  // theme as a defect on nine pages. The theme per frame was measured from the frame's own top bar.
  const page = await browser.newPage({
    viewport: { width: w, height: h }, deviceScaleFactor: 1, colorScheme: theme,
  });
  await page.addInitScript((t) => { try { localStorage.setItem('usr.theme', t); } catch {} }, theme);
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  try {
    await page.goto(`${BASE}${route}?newui=1`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(2500);          // let the empty states settle
    await page.screenshot({ path: new URL(`${key}.png`, out).pathname.replace(/^\//, '') });
    const shell = await page.$('.app-nu');
    const got = await page.getAttribute('html', 'data-theme');
    console.log(`${key.padEnd(12)} ${w}x${h} ${theme.padEnd(5)} ${got === theme ? 'ok' : 'THEME=' + got} shell=${shell ? 'new' : 'OLD!'}  ${errs.length ? 'errors: ' + errs[0].slice(0, 90) : ''}`);
  } catch (e) {
    console.log(`${key.padEnd(12)} FAILED ${String(e).slice(0, 110)}`);
  }
  await page.close();
}
await browser.close();
