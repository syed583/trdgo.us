/**
 * Geometry harness: the live page against its Figma frame, block by block.
 *
 * The numbers on the left come from the FILE (read with _frame.py out of get_metadata dumps and
 * recorded in _frames.json). The numbers on the right are the live page's own
 * getBoundingClientRect at the frame's canvas size and theme. Nothing here is judged by eye.
 *
 * Two deliberate normalisations, both because the design file disagrees with itself:
 *
 *   - The sidebar is 247 wide in one frame and 162 in another, and the top bar 67 or 51. The app
 *     has ONE of each. So x is compared relative to the CONTENT COLUMN's left edge and y relative
 *     to the bar's bottom, instead of to the canvas corner.
 *   - A frame drawn with a 162 rail gives its content more room than the same canvas gives ours.
 *     Widths are therefore compared against the frame's width SCALED to the live content column,
 *     so "the table fills the column" reads as a match rather than as an 85px error.
 *
 * Heights and vertical rhythm are compared raw: nothing about the rail changes those.
 *
 *   node _diff.mjs [pageKey ...]        (no args = every page in _frames.json)
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const SPEC = JSON.parse(fs.readFileSync(new URL('./_frames.json', import.meta.url), 'utf8'));
const base = process.env.BASE || 'http://localhost:5311';
const keys = process.argv.slice(2).filter((k) => k in SPEC);
const pages = keys.length ? keys : Object.keys(SPEC).filter((k) => !k.startsWith('_'));

const browser = await chromium.launch();
let worst = 0;
const report = [];

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

  // Where the live page actually puts its content column and its bar, measured not assumed.
  const frame = await page.evaluate(() => {
    const rail = document.querySelector('.nu-rail');
    const bar = document.querySelector('.nu-bar, .nu-topbar, header');
    return {
      rail: rail ? Math.round(rail.getBoundingClientRect().width) : 0,
      navH: bar ? Math.round(bar.getBoundingClientRect().height) : 0,
    };
  });
  const liveContentW = s.w - frame.rail;
  const scale = liveContentW / s.contentW;

  const live = await page.evaluate((sel) => Object.fromEntries(sel.map((q) => {
    const e = document.querySelector(q);
    if (!e) return [q, null];
    const r = e.getBoundingClientRect();
    return [q, [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]];
  })), Object.keys(s.blocks));
  await page.close();

  const lines = [];
  lines.push(`\n=== ${key}  ${s.route}  ${s.w}x${s.h} ${s.theme}`);
  lines.push(`    frame rail ${s.rail}/bar ${s.navH} -> live rail ${frame.rail}/bar ${frame.navH}`
    + `; widths scaled x${scale.toFixed(3)}`);
  lines.push(`${'block'.padEnd(16)} ${'frame x,y w,h'.padEnd(22)} ${'live (normalised)'.padEnd(22)} delta`);
  for (const [sel, f] of Object.entries(s.blocks)) {
    const l = live[sel];
    if (!l) { lines.push(`${sel.padEnd(16)} MISSING FROM PAGE`); worst = Math.max(worst, 999); continue; }
    // normalise the live box into the frame's content space
    const n = [
      Math.round((l[0] - frame.rail) / scale),
      Math.round(l[1] - frame.navH),
      Math.round(l[2] / scale),
      l[3],
    ];
    const d = n.map((v, i) => v - f[i]);
    worst = Math.max(worst, ...d.map(Math.abs));
    lines.push(`${sel.padEnd(16)} ${f.join(',').padEnd(22)} ${n.join(',').padEnd(22)} `
      + d.map((x, i) => `${'xywh'[i]}${x >= 0 ? '+' : ''}${x}`).join(' '));
  }
  report.push(lines.join('\n'));
}

await browser.close();
console.log(report.join('\n'));
console.log(`\nlargest single-axis difference across ${pages.length} page(s): ${worst}px`);
