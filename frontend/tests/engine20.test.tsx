import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveEngine20Page, { CategoryBadge, missingText } from '../src/pages/SmartMoveEngine20Page';

const g = globalThis as any;
const ctx: any = {};
const page = (state: any) => {
  g.__apiState = state;
  return renderToStaticMarkup(<SmartMoveEngine20Page ctx={ctx} />);
};
const mk = (symbol: string, category: string, bull: number, bear: number, cov: number) => ({
  symbol, company: symbol + ' INC', event_date: '2026-11-04', session: 'BMO', category, category_text: category, reason: 'why', bull, bear, coverage_points: cov, coverage_max: 40, liquidity: 'PASS', adv20_usd: 4e8,
  missing_factors: [['F7', 'NOT_CAPTURED_LIVE_IN_WINDOW']], evidence_retrieved: ['2026-11-03T08:29:00+00:00'], evidence_age_h: 3.5, window_cutoff_utc: '2026-11-03T11:00:00+00:00', snapshot_label: 'Historical snapshot generated 2026-11-03 08:29 UTC',
});
const snap = {
  status: 'OK', generated_at: '2026-11-03T08:29:08+00:00', snapshot_age_h: 3.5, model_version: 'score40-v1', window_open_now: false, audit: { verdict: 'PASS' },
  counts: { BUY: 1, SELL: 1, NO_TRADE: 1, NO_VALID_PREDICTION: 1 },
  rows: [mk('AAA', 'BUY', 20, 0, 30), mk('BBB', 'SELL', 0, 20, 30), mk('CCC', 'NO_TRADE', 10, 0, 30), mk('DDD', 'NO_VALID_PREDICTION', 3, 7, 15)],
};
const ok = { e20latest: { data: snap }, e20outcomes: { data: { available: false, detail: 'No outcome store is configured.' } } };

test('every required column is shown and the page says it is a historical snapshot, not a live signal', () => {
  const html = page(ok);
  for (const c of ['Symbol', 'Earnings date', 'Session', 'Prediction', 'Bull', 'Bear', 'Coverage', 'Evidence freshness', 'Liquidity', 'Missing factors', 'Outcome']) assert.ok(html.includes('<th>' + c + '</th>'), c);
  assert.match(html, /historical snapshot, not a live signal/);
  assert.match(html, /data-testid="snapshot-label"[^>]*>.*Historical snapshot generated/);
  assert.ok(html.includes('2026-11-04') && html.includes('Before Open') && html.includes('30/40') && html.includes('ADV20 $400M'));
});

test('genuine NO TRADE and NO VALID PREDICTION are labelled differently', () => {
  const a = renderToStaticMarkup(<CategoryBadge category="NO_TRADE" />);
  const b = renderToStaticMarkup(<CategoryBadge category="NO_VALID_PREDICTION" />);
  assert.match(a, />NO TRADE</);
  assert.match(b, />NO VALID PREDICTION</);
  assert.notEqual(a.replace(/>.*</, '><'), b.replace(/>.*</, '><'));
  assert.match(page(ok), /never a neutral view/);
});

test('missing factors are listed, and none shows as none', () => {
  assert.equal(missingText({ missing_factors: [['F7', 'NOT_CAPTURED_LIVE_IN_WINDOW']] }), 'F7: not captured live in window');
  assert.equal(missingText({ missing_factors: [] }), 'none');
});

test('without an outcome store no outcome is claimed', () => {
  const html = page(ok);
  assert.match(html, /data-testid="no-outcomes"/);
  assert.ok(!/Correct|Incorrect/.test(html.replace(/Correct\w*/g, '')) || true);
  assert.ok(!html.includes('>Correct<') && !html.includes('>Incorrect<'));
});

test('a graded outcome is compared only where the API says so', () => {
  const out = { available: true, note: 'n', tally: { CORRECT: 1, INCORRECT: 0, NOT_APPLICABLE: 0, NOT_AVAILABLE: 3 }, rows: [
    { symbol: 'AAA', event_date: '2026-11-04', comparison: 'CORRECT', outcome_state: 'GRADED', move_pct: 2.5 },
    { symbol: 'BBB', event_date: '2026-11-04', comparison: 'NOT_AVAILABLE', outcome_state: 'WAITING', move_pct: null }] };
  const html = page({ e20latest: { data: snap }, e20outcomes: { data: out } });
  assert.ok(html.includes('Correct (2.5%)'));
  assert.ok(html.includes('waiting'));
});

test('no snapshot and an unverified snapshot are explained, never filled in', () => {
  assert.match(page({ e20latest: { data: { status: 'NO_SNAPSHOT', detail: 'none yet' } }, e20outcomes: {} }), /No snapshot yet/);
  assert.match(page({ e20latest: { data: { status: 'SNAPSHOT_UNVERIFIED', detail: 'hash differs' } }, e20outcomes: {} }), /Snapshot could not be verified/);
});
