import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveEngine20Page, { MondayBmoView } from '../src/pages/SmartMoveEngine20Page';

const g = globalThis as any;
const show = (d: any) => { g.__apiState = { emsmartmondaybmo: { data: d } }; return renderToStaticMarkup(<MondayBmoView />); };
const row = (o: any) => ({ symbol: 'AAA', company: 'Alpha Inc', monday_earnings_date: '2026-11-16', session: 'BMO', label: 'LIVE', variant: 'MONDAY_BMO_FRIDAY', signal: 'BUY', signal_code: 'BUY', bull: 16, bear: 2, coverage: '30/40', liquidity: 'PASS',
  predicted_at_et: '2026-11-13 14:05 EST', friday_entry_deadline_et: '2026-11-13 15:30 EST', friday_entry_deadline_utc: '2026-11-13T20:30:00+00:00', hours_to_deadline: 1.2, reaction_session: '2026-11-16', reason: 'frozen rule met: bull 16 / bear 2', outcome_status: null, ...o });
const acc = { total_predictions: 2, graded_events: 2, decided_calls: 2, buy_accuracy: { n: 1, hits: 1, rate: 1 }, sell_accuracy: { n: 1, hits: 0, rate: 0 }, overall_directional_accuracy: { strict_hit_rate: 0.5 }, average_post_earnings_move_pct: 2.5,
  weekend_gap_risk: { mean_abs_gap_pct: 1.25 }, hypothetical_trading_performance: { mean_net_pct_per_trade_cost_x1: 0.3 }, earnings_weeks: 1, minimum_sample_met: false, claim_allowed: false, claim_text: 'NO ACCURACY CLAIM: shadow descriptive statistics only' };
const base = (rows: any[], extra: any = {}) => ({ configured: true, available: true, variant: 'MONDAY_BMO_FRIDAY', note: 'Separate variant', ledger: { ok: true, records: rows.length }, scan: { mode: 'LIVE', last_scan_et: '2026-11-13 14:05 EST', last_successful_scan_et: '2026-11-13 14:05 EST', last_scan_ok: true },
  counts: rows.reduce((m: any, r: any) => { m[r.label] = (m[r.label] || 0) + 1; return m; }, {}), rows, accuracy: null, ...extra });

test('every required column is on the screen: company, symbol, Monday date, call, bull/bear, coverage, timestamp, Friday deadline, label', () => {
  const h = show(base([row({}), row({ symbol: 'BBB', company: 'Beta Corp', signal: 'SELL', signal_code: 'SELL', bull: 2, bear: 16 }), row({ symbol: 'CCC', signal: 'NO TRADE', signal_code: 'NO_TRADE', bull: 8, bear: 8 })]));
  assert.match(h, /data-testid="sm-monday-bmo-table"/); assert.match(h, /Alpha Inc/); assert.match(h, />AAA</); assert.match(h, />BBB</); assert.match(h, /2026-11-16/);
  assert.match(h, /2026-11-13 14:05 EST/); assert.match(h, /2026-11-13 15:30 EST/); assert.match(h, /1\.2 h left/); assert.match(h, /30\/40/); assert.match(h, /MONDAY_BMO_FRIDAY/);
  assert.match(h, /Friday entry deadline \(ET\)/); assert.match(h, /pending Monday close/); assert.match(h, /LIVE \(3\)/);
  const cell = (sym: string) => h.split('<tr data-testid="sm-monday-bmo-row">').find((x) => x.includes('>' + sym + '<')) || '';
  assert.match(cell('AAA'), /BUY/); assert.match(cell('BBB'), /SELL/); assert.match(cell('CCC'), /NO TRADE/);
});

test('the separate accuracy block shows BUY, SELL, directional, average move, weekend gap and net per trade, with the no-claim text', () => {
  const h = show(base([row({ outcome_status: 'GRADED', move_pct: 3, gap_pct: 1.5, outcome_class: 'UP', hit: true })], { accuracy: acc }));
  assert.match(h, /sm-monday-bmo-accuracy/); assert.match(h, /BUY accuracy/); assert.match(h, /1\/1 \(100%\)/); assert.match(h, /SELL accuracy/); assert.match(h, /0\/1 \(0%\)/);
  assert.match(h, /\+2\.50%/); assert.match(h, /\+1\.25%/); assert.match(h, /\+0\.30%/); assert.match(h, /NO ACCURACY CLAIM/); assert.match(h, /never combined/i);
  assert.match(h, /\+3\.00% UP · HIT/); assert.match(h, /gap \+1\.50%/);
});

test('a missed Friday window and a broken variant ledger are shown, not hidden; no pass yet is explained', () => {
  const h = show(base([row({ symbol: 'MISS', label: 'WINDOW MISSED', signal: null, signal_code: null })], { ledger: { ok: false, problem: 'hash chain broken', records: null } }));
  assert.match(h, /sm-monday-bmo-ledger/); assert.match(h, /hash chain broken/); assert.match(h, /WINDOW MISSED/);
  assert.match(show({ configured: true, available: false, rows: [], note: 'No variant pass has run yet' }), /sm-monday-bmo-off/);
});

test('the page offers the Monday-BMO variant as its own tab between the universe and the snapshot', () => {
  g.__apiState = { emsmartlive: { data: { configured: false, note: 'x', rows: [] } } };
  const h = renderToStaticMarkup(<SmartMoveEngine20Page ctx={{} as any} />);
  assert.ok(h.indexOf('tab-universe') < h.indexOf('tab-monday-bmo') && h.indexOf('tab-monday-bmo') < h.indexOf('tab-snapshot')); assert.match(h, /Monday BMO \(Friday window\)/);
});
