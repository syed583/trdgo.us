/**
 * Capture each demo frame as a PNG at its exact canvas size, for comparison with the Figma export.
 *
 * PNG, not the preview pane's JPEG: comparing a JPEG against a PNG puts compression noise into every
 * photo area and reports it as a difference, which is how a page that already matches looks wrong.
 *
 * deviceScaleFactor 1 and a viewport of exactly the frame size, so one captured pixel is one design
 * pixel and the diff needs no rescaling.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const FRAMES = JSON.parse(fs.readFileSync(new URL('./_frames.json', import.meta.url), 'utf8'));
const only = process.argv[2];
const out = new URL('./_compare/demo/', import.meta.url);
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
for (const [key, [w, h]] of Object.entries(FRAMES)) {
  if (only && only !== key) continue;
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await page.goto(`http://localhost:5300/#${key}`, { waitUntil: 'networkidle' });
  // the demo's own frame switcher is not part of the design
  await page.addStyleTag({ content: '.demo-nav{display:none!important}.frame-wrap{margin:0!important}' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(700);
  const el = await page.$('.frame-wrap > div');
  await el.screenshot({ path: new URL(`${key}.png`, out).pathname.replace(/^\//, '') });
  console.log('captured', key, `${w}x${h}`);
  await page.close();
}
await browser.close();
