/**
 * Geometry diff: every element of frame 2:2 that has a measurable counterpart on the live page,
 * compared by its absolute position and size in the frame's own 1536x1024 canvas.
 *
 * This exists because "looks about right" is how the first build shipped. The numbers on the left
 * are read from Figma via MCP (get_metadata on 2:2 and its children); the numbers on the right are
 * the live page's own getBoundingClientRect at the same canvas size and theme.
 */
import { chromium } from 'playwright';

// name -> [x, y, w, h] in frame 2:2. null = not pinned by the frame.
const FRAME = {
  '.nal-hero':               [247, 67, 1289, 277],
  '.nal-hero .nd-eyebrow':   [284, 89, 261, 33],
  '.nal-hero h1':            [284, 132, null, 102],
  '.nal-feats':              [783, 96, 311, 226],
  '.nal-strip':              [268, 343, 1252, 67],
  '.nal-stats':              [268, 430, 1252, 73],
  '.nal-rowC':               [268, 516, 1252, 304],
  '.nal-rowD':               [268, 830, 1252, 174],
};

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1536, height: 1024 }, deviceScaleFactor: 1, colorScheme: 'dark' });
await p.addInitScript(() => { try { localStorage.setItem('usr.theme', 'dark'); } catch { /* private */ } });
await p.goto('http://localhost:5311/ai-insights?newui=1', { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(3200);
const live = await p.evaluate((sels) => Object.fromEntries(sels.map((s) => {
  const e = document.querySelector(s);
  if (!e) return [s, null];
  const r = e.getBoundingClientRect();
  return [s, [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]];
})), Object.keys(FRAME));
await b.close();

const pad = (s, n) => String(s).padEnd(n);
console.log(pad('element', 24), pad('frame', 22), pad('live', 22), 'delta');
let worst = 0;
for (const [k, f] of Object.entries(FRAME)) {
  const l = live[k];
  if (!l) { console.log(pad(k, 24), 'MISSING FROM PAGE'); continue; }
  const d = f.map((v, i) => (v == null ? null : l[i] - v));
  worst = Math.max(worst, ...d.filter((x) => x != null).map(Math.abs));
  console.log(pad(k, 24), pad(f.join(','), 22), pad(l.join(','), 22),
    d.map((x, i) => (x == null ? '-' : `${'xywh'[i]}${x >= 0 ? '+' : ''}${x}`)).join(' '));
}
console.log(`\nlargest single-axis difference: ${worst}px`);
