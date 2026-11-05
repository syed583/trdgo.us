import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveEngine20Page, { UniverseView } from '../src/pages/SmartMoveEngine20Page';

const g = globalThis as any;
const show = (d: any) => { g.__apiState = { emsmartuniverse: { data: d } }; return renderToStaticMarkup(<UniverseView />); };
const row = (o: any) => ({ symbol: 'AAA', company: 'Alpha', event_date: '2026-11-10', session: 'AMC', priority_proxy_tier: 'A', excluded_by_proxy: false, window: 'OPEN', hours_to_cutoff: 6.5, window_start: { new_york: '2026-11-08 16:00 EST' }, cutoff: { new_york: '2026-11-09 16:00 EST' },
  evidence: { F5_price: 'FRESH', F3_earnings_history: 'FRESH', F8_market: 'FRESH', F8_sector_etf: 'MISSING' }, calendar_tier: 'T0_OPEN', calendar_slot_state: 'FRESH', calendar_change: 'LISTED', collection_status: 'EVIDENCE_COMPLETE', deferred: false, deferred_reason: null, ineligible_reasons: [], ...o });
const base = (rows: any[], extra: any = {}) => ({ configured: true, available: true, generated_et: '2026-11-08 12:00 EST', age_h: 0.5, stale: false, stale_after_h: 8, horizon_days: 30, ledger_ok: true,
  calendar_completeness: { t0_complete: true, slots_fresh: 8, slots_total: 40, t0_open_stale: [], t0_next_stale: [], t1_stale: ['a', 'b'] },
  summary: { events: rows.length, excluded_by_proxy: 0, by_status: rows.reduce((m: any, r: any) => { const k = r.collection_status.split(':')[0]; m[k] = (m[k] || 0) + 1; return m; }, {}) }, rows, ...extra });

test('the universe lists every event with its window, evidence state, tier and status; deferred events are explained and never excluded', () => {
  const h = show(base([row({}), row({ symbol: 'BBB', collection_status: 'DEFERRED', deferred: true, deferred_reason: 'ranked below the run slots; still eligible while its window is open', priority_proxy_tier: 'D' }), row({ symbol: 'CCC', collection_status: 'WAITING_WINDOW' })]));
  assert.match(h, /data-testid="sm-universe-table"/); assert.match(h, />AAA</); assert.match(h, />BBB</);
  assert.match(h, /2026-11-08 16:00 EST/); assert.match(h, /still eligible while its window is open/); assert.match(h, /0 excluded/); assert.match(h, /orders work only/);
  assert.match(h, /3 events in the next 30 days/); assert.match(h, /DEFERRED \(1\)/); assert.match(h, /F8 market \/ ETF/);
});

test('not tradable, deferred by budget, served and eligibility unknown are four different things on the screen', () => {
  const h = show(base([row({ symbol: 'MBMO', collection_status: 'NOT_TRADABLE', tradable: false, status_note: 'not tradable under the frozen order-deadline rule', priority_proxy_tier: 'A' }),
    row({ symbol: 'DEFR', collection_status: 'DEFERRED', deferred: true, deferred_reason: 'selected but not funded: the run budget is used up; still eligible while its window is open' }),
    row({ symbol: 'SERV', collection_status: 'EVIDENCE_COMPLETE' }),
    row({ symbol: 'CONF', collection_status: 'ELIGIBILITY_UNKNOWN', date_confirmed: false, status_note: 'the calendar vendors disagree on the event date' })]));
  const cell = (sym: string) => h.split('<tr data-testid="sm-universe-row">').find((x) => x.includes('>' + sym + '<')) || '';
  assert.match(cell('MBMO'), /NOT TRADABLE/); assert.ok(!/DEFERRED/.test(cell('MBMO')) && !/still eligible/.test(cell('MBMO')));
  assert.match(cell('DEFR'), /DEFERRED/); assert.match(cell('DEFR'), /still eligible/);
  assert.match(cell('SERV'), /EVIDENCE COMPLETE/); assert.match(cell('CONF'), /ELIGIBILITY UNKNOWN/); assert.ok(!/DEFERRED/.test(cell('CONF')));
  assert.match(h, /NOT TRADABLE \(1\)/); assert.match(h, /ELIGIBILITY UNKNOWN \(1\)/);
});

test('stale calendar slots, a stale table and a failed ledger are shown, not hidden', () => {
  const h = show(base([row({})], { stale: true, age_h: 11, ledger_ok: false, ledger_problem: 'hash chain broken', calendar_completeness: { t0_complete: false, slots_fresh: 3, slots_total: 40, t0_open_stale: ['2026-11-09|premarket'], t0_next_stale: [], t1_stale: [] } }));
  assert.match(h, /sm-universe-stale/); assert.match(h, /11 h old/); assert.match(h, /integrity check FAILED/); assert.match(h, /STALE slots: 2026-11-09\|premarket/);
});

test('no universe yet is explained, not faked', () => {
  assert.match(show({ configured: true, available: false, rows: [], note: 'The collector has not written an event universe yet' }), /sm-universe-off/);
});

test('the page offers LIVE scanner, event universe and the historical snapshot as separate tabs, LIVE first', () => {
  g.__apiState = { emsmartlive: { data: { configured: false, note: 'x', rows: [] } } };
  const h = renderToStaticMarkup(<SmartMoveEngine20Page ctx={{} as any} />);
  assert.ok(h.indexOf('tab-live') < h.indexOf('tab-universe') && h.indexOf('tab-universe') < h.indexOf('tab-snapshot')); assert.match(h, /Event universe \(30 days\)/); assert.match(h, /sm-live-view/);
});
