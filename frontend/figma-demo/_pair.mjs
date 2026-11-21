/**
 * Side-by-side: the Figma frame export beside the live page, both at the frame's own canvas size.
 *
 * Captures at the frame's EXACT width and height, in the frame's own theme, with
 * deviceScaleFactor 1 - so one captured pixel is one design pixel and the two halves can be
 * compared without rescaling either of them.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const [key, route, w, h, theme] = process.argv.slice(2);
const W = Number(w);
const H = Number(h);
const base = process.env.BASE || 'http://localhost:5311';

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: W, height: H },
  deviceScaleFactor: 1,
  colorScheme: theme,
});
await page.addInitScript((t) => {
  try { localStorage.setItem('usr.theme', t); } catch { /* private window */ }
}, theme);
await page.goto(`${base}${route}?newui=1`, { waitUntil: 'domcontentloaded' });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(3200);

const out = new URL('./_compare/live/', import.meta.url);
fs.mkdirSync(out, { recursive: true });
const shot = new URL(`${key}.png`, out).pathname.replace(/^\//, '');
await page.screenshot({ path: shot });
console.log(`captured ${key} ${W}x${H} ${theme}`);
await browser.close();
