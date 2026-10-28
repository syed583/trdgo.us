// Phase 1 frontend check. Feeds the backend's real output (gen_signal_cases.py) through the
// new signalView() and through the OLD browser logic (55/45 on the score) and compares both
// with the backend's final signal. Usage: node check_signal_view.mjs <cases.json> <compiled earningsSignal.js>
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const [, , casesPath, libPath] = process.argv;
const cases = JSON.parse(readFileSync(casesPath, 'utf8'));
const { signalView } = await import(pathToFileURL(libPath).href);

// The code that shipped before the fix (ClaudeLastDetail.tsx, removed).
const OLD = (s) => (s == null ? '--' : s >= 55 ? 'BUY' : s <= 45 ? 'SELL' : 'NEUTRAL');

const TONE_OF = { BUY: 'buy', SELL: 'sell', NEUTRAL: 'flat', LOW_DATA: 'flat' };
const COLOR_OF = { buy: 'var(--green)', sell: 'var(--red)', flat: 'var(--amber)' };

let fails = 0;
let beforeMismatch = 0;
const fixed = [];
for (const c of cases) {
  const b = c.backend;
  const view = signalView({ score: c.score, ...b });
  const bs = b.signal;

  // After: label, tone, colour and setup must all follow the backend's final call.
  const errs = [];
  if (c.expect_signal != null && bs !== c.expect_signal) errs.push(`backend ${bs} != spec ${c.expect_signal}`);
  if (c.expect_label != null && view.label !== c.expect_label && !(c.expect_label === 'LOW DATA' && view.label === 'LOW DATA')) {
    // The page shows the tier when the backend has one; Gemini/ChatGPT/Kimi labels are checked here.
    const hasTier = b.tier && b.tier !== 'Low data';
    const want = hasTier ? c.expect_label : c.expect_signal === 'LOW_DATA' ? 'LOW DATA' : c.expect_label;
    if (view.label !== want) errs.push(`label ${view.label} != ${want}`);
  }
  if (bs === 'PENDING') {
    if (view.label !== '--') errs.push('pending must show --');
  } else if (bs === 'LOW_DATA') {
    if (view.label !== 'LOW DATA' || view.tone !== 'flat' || view.color !== 'var(--text-mute)') errs.push('low data view wrong');
  } else {
    if (view.tone !== TONE_OF[bs]) errs.push(`tone ${view.tone} != ${TONE_OF[bs]}`);
    if (view.color !== COLOR_OF[TONE_OF[bs]]) errs.push(`colour ${view.color} disagrees with ${bs}`);
    if (!view.label.includes(bs === 'SELL' && /SHORT/.test(view.label) ? 'SHORT' : bs)) errs.push(`badge ${view.label} lacks ${bs}`);
  }
  if (errs.length) { fails++; console.log('FAIL', c.model, c.case, c.score, errs.join('; ')); }

  // Before: what the old page would have shown.
  const old = OLD(c.score);
  // Pre-Earnings Direction has its own list page (always showed r.signal); it never used the 55/45 detail.
  const wasWrong = c.model !== 'ped' && bs !== 'PENDING' && old !== bs;
  if (wasWrong) {
    beforeMismatch++;
    fixed.push({ model: c.model, case: c.case, score: c.score, cov: c.coverage, backend: b.tier && b.tier !== 'Low data' ? `${bs} (${b.tier})` : bs, before: old, after: view.label });
  }
}

console.log(`\ncases: ${cases.length}   after-fix failures: ${fails}   old-page mismatches: ${beforeMismatch}`);
console.log('\nBEFORE vs AFTER (every case where the old page disagreed with the backend):');
console.log('| model | case | score | cov% | backend final | OLD page | NEW page |');
console.log('|---|---|---:|---:|---|---|---|');
for (const f of fixed) console.log(`| ${f.model} | ${f.case} | ${f.score} | ${f.cov} | ${f.backend} | ${f.before} | ${f.after} |`);
process.exit(fails ? 1 : 0);
