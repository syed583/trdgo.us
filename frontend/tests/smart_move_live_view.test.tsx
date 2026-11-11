import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveEngine20Page, { LiveView } from '../src/pages/SmartMoveEngine20Page';

const g = globalThis as any;
const live = (d: any) => { g.__apiState = { emsmartlive: { data: d } }; return renderToStaticMarkup(<LiveView />); };
const row = (o: any) => ({ symbol: 'AAA', company: 'Alpha', earnings_date: '2026-11-06', session: 'AMC', label: 'LIVE', signal_code: 'BUY', bull: 15, bear: 2, coverage: '27/40', liquidity: 'PASS', adv20_usd: 45e6, missing: [], reason: 'bull 15 bear 2', evidence_age_h: 1.2, window_cutoff_et: '2026-11-05 16:00 EST', hours_to_cutoff: 5, record_sha256: 'abc', mode: 'LIVE', ...o });
const base = (rows: any[]) => ({ configured: true, state: 'OK', state_text: 'Scanner running.', now_et: 'x', scan: { last_successful_scan_et: 'y', mode: 'LIVE' }, requests: { used_today: 118, daily_limit: 100, remaining_today: 0, run_limit: 50 }, events: {}, ledger: { ok: true }, note: 'n', rows });

test('live table shows predictions with bull/bear, coverage and a LIVE label', () => {
  const h = live(base([row({}), row({ symbol: 'BBB', signal_code: 'NO_VALID_PREDICTION', label: 'HISTORICAL', bull: null, bear: null }), row({ symbol: 'CCC', signal_code: 'PENDING_REFRESH' }), row({ symbol: 'MMM', label: 'WINDOW MISSED', signal_code: null })]));
  assert.match(h, /data-testid="sm-live-table"/);
  assert.match(h, />AAA</); assert.match(h, /27\/40/); assert.match(h, /ADV20 \$45M/);
  assert.match(h, />LIVE</); assert.match(h, />HISTORICAL</);
  assert.ok(!h.includes('>MMM<'), 'missed windows hidden by default');
  assert.match(h, /Missed windows \(1\)/); assert.match(h, /PENDING \(not a prediction\)/);
});

test('an unconfigured server says so and shows no rows; a failed ledger shows no calls', () => {
  assert.match(live({ configured: false, note: 'not installed', rows: [] }), /sm-live-off/);
  const bad = live({ ...base([row({})]), ledger: { ok: false, problem: 'hash chain broken' } });
  assert.match(bad, /Prediction ledger integrity check FAILED/);
});

test('default export opens on the LIVE scanner and offers the historical snapshot tab', () => {
  g.__apiState = { emsmartlive: { data: base([row({})]) } };
  const h = renderToStaticMarkup(<SmartMoveEngine20Page ctx={{} as any} />);
  assert.match(h, /data-testid="tab-live"/); assert.match(h, /Historical snapshot \(20 stocks\)/); assert.match(h, /sm-live-view/);
});

// ---------------------------------------------------------------- 2026-11-11: upcoming first, past last, only upcoming BUY/SELL blink
test('upcoming earnings are listed first (earliest date first) and past earnings go to the bottom section', () => {
  const h = live(base([
    row({ symbol: 'OLD', earnings_date: '2026-11-05', label: 'HISTORICAL', signal_code: 'BUY' }),
    row({ symbol: 'NXT', earnings_date: '2026-11-13', label: 'LIVE', signal_code: 'SELL' }),
    row({ symbol: 'TMR', earnings_date: '2026-11-12', label: 'LIVE', signal_code: 'BUY' }),
    row({ symbol: 'OLDER', earnings_date: '2026-11-03', label: 'HISTORICAL', signal_code: 'NO_TRADE' }),
  ]));
  const order = ['TMR', 'NXT', 'OLD', 'OLDER'].map((s) => h.indexOf('<b>' + s + '</b>'));
  assert.ok(order.every((i) => i > 0) && order[0] < order[1] && order[1] < order[2] && order[2] < order[3], 'order: TMR, NXT, OLD, OLDER');
  assert.ok(h.indexOf('data-testid="sm-section-upcoming"') < h.indexOf('<b>TMR</b>'));
  const past = h.indexOf('data-testid="sm-section-past"');
  assert.ok(past > h.indexOf('<b>NXT</b>') && past < h.indexOf('<b>OLD</b>'), 'past section header sits between the last upcoming and the first past row');
});

test('only upcoming BUY / SELL badges blink; past events and non-directional calls never do', () => {
  const h = live(base([
    row({ symbol: 'UPB', earnings_date: '2026-11-12', label: 'LIVE', signal_code: 'BUY' }),
    row({ symbol: 'UPS', earnings_date: '2026-11-12', label: 'STALE', signal_code: 'SELL' }),
    row({ symbol: 'UPN', earnings_date: '2026-11-12', label: 'LIVE', signal_code: 'NO_TRADE' }),
    row({ symbol: 'PAB', earnings_date: '2026-11-05', label: 'HISTORICAL', signal_code: 'BUY' }),
  ]));
  const rows = h.split('data-testid="sm-live-row"').slice(1);
  const blink = (sym: string) => rows.find((r) => r.includes('<b>' + sym + '</b>'))!.includes('sm-blink');
  assert.equal(blink('UPB'), true); assert.equal(blink('UPS'), true);
  assert.equal(blink('UPN'), false); assert.equal(blink('PAB'), false);
});
