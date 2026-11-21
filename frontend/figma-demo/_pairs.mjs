/**
 * Capture the live page at its frame's canvas size and theme, for the side-by-side pairs.
 *
 *   node _pairs.mjs [pageKey ...]     (no args = every page in _frames.json)
 *
 * deviceScaleFactor 1 so one captured pixel is one design pixel and neither half of the pair is
 * rescaled before the owner looks at it.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const SPEC = JSON.parse(fs.readFileSync(new URL('./_frames.json', import.meta.url), 'utf8'));
const base = process.env.BASE || 'http://localhost:5311';
const keys = process.argv.slice(2).filter((k) => k in SPEC);
const pages = keys.length ? keys : Object.keys(SPEC).filter((k) => !k.startsWith('_'));

const out = new URL('./_compare/live/', import.meta.url);
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
for (const key of pages) {
  const s = SPEC[key];
  const page = await browser.newPage({
    viewport: { width: s.w, height: s.h }, deviceScaleFactor: 1, colorScheme: s.theme,
  });
  await page.addInitScript((t) => {
    try { localStorage.setItem('usr.theme', t); } catch { /* private window */ }
  }, s.theme);
  await page.goto(`${base}${s.route}?newui=1`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(3200);
  const shot = new URL(`${key}.png`, out).pathname.replace(/^\//, '');
  await page.screenshot({ path: shot });
  await page.close();
  console.log(`captured ${key} ${s.w}x${s.h} ${s.theme}`);
}
await browser.close();
