import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import EarningsMonitorPanel, { SignalCell, agoText, scanBadge, nice } from '../src/pages/EarningsMonitorPanel';

const g = globalThis as any;
const panel = (state: any, tab?: any) => {
  g.__apiState = state;
  return renderToStaticMarkup(<EarningsMonitorPanel initialTab={tab} />);
};
const NOW = '2026-11-09T18:00:00+00:00';
const on = { now: NOW, status: 'OK', scheduling: 'REFRESH', last_any_success: '2026-11-09T17:30:00+00:00', auto_refresh_seconds: 60, provider_requests_today: 42, daily_budget: 450, pending_alerts: 2, gate_reasons: [],
  smart_move_plan: { requests_needed: 48, symbols: ['AAA', 'BBB'] } };
const off = { now: NOW, status: 'OK', scheduling: 'OFF', last_any_success: null, gate_reasons: ['NO_OWNER_APPROVAL_FILE', 'NOT_ENABLED_BY_ENVIRONMENT'], provider_requests_today: 0, daily_budget: 0, pending_alerts: 0 };
const upcoming = { status: 'OK', horizon_days: 15, rows: [
  { id: 1, symbol: 'AAA', company: 'Alpha', earnings_date: '2026-11-12', session: 'BMO', status: 'ACTIVE', days_to: 3, verification: 'CONFIRMED', eligible: true, ineligible_reasons: [], valid_predictions: 4,
    latest_change: { kind: 'EVENT_RESCHEDULED', detected_at: '2026-11-09T23:00:00+00:00' }, versions: [], sources: [], outcome: null },
  { id: 2, symbol: 'CCC', company: 'Gamma', earnings_date: '2026-11-13', session: 'UNKNOWN', status: 'ACTIVE', days_to: 4, verification: 'CONFLICT', eligible: false, ineligible_reasons: ['SESSION_NOT_CONFIRMED', 'CALENDAR_VENDORS_DISAGREE'],
    valid_predictions: 0, latest_change: null, versions: [], sources: [], outcome: null }] };

test('the scan indicator says OFF and why when nothing is approved, and ON with the last successful scan when it is', () => {
  const a = panel({ emstatus: { data: off }, emupcoming: { data: upcoming } });
  assert.match(a, /Automatic scan: OFF — waiting for owner approval/);
  assert.match(a, /Not scanning because: no owner approval file; not enabled by environment/);
  assert.match(a, /data-testid="last-scan"[^>]*>never</);
  const b = panel({ emstatus: { data: on }, emupcoming: { data: upcoming } });
  assert.match(b, /Automatic scan: ON \(cache reads \+ budgeted provider refresh\)/);
  assert.match(b, /30 min ago/);
  assert.match(b, /this view every 60 s/);
  assert.match(b, /42 \/ 450/);
  assert.match(b, /Smart Move evidence request on file: 48 provider request\(s\) for 2 stock\(s\)/);
  assert.equal(scanBadge(null).tone, 'gray');
  assert.equal(scanBadge({ status: 'OK', scheduling: 'CACHE_ONLY' }).text.includes('no provider requests'), true);
});

test('the five views exist; Upcoming Earnings shows session, vendor check, eligibility and the latest change', () => {
  const html = panel({ emstatus: { data: on }, emupcoming: { data: upcoming } });
  for (const t of ['Upcoming Earnings', 'All Models', 'Prediction Changes', 'Model Consensus', 'Historical Accuracy']) assert.ok(html.includes('>' + t + '<'), t);
  for (const c of ['Symbol', 'Earnings date', 'Session', 'Days', 'Calendar check', 'Status', 'Eligible', 'Valid predictions', 'Latest change']) assert.ok(html.includes('<th>' + c + '</th>'), c);
  assert.ok(html.includes('Before Open') && html.includes('not confirmed') && html.includes('event rescheduled') && html.includes('confirmed') && html.includes('calendar vendors disagree'));
  assert.match(html, /Looks 15 days ahead/);
});

test('only BUY, SELL, NEUTRAL and NO TRADE are drawn as calls; PENDING, STALE, LOW DATA and NO VALID PREDICTION never are', () => {
  const call = (label: string, status = 'VALID') => renderToStaticMarkup(<SignalCell c={{ status, label, signal: label, score: 61, coverage_fraction: 0.9, age_h: 2.5, source: 'swr_cache' }} />);
  for (const l of ['BUY', 'SELL', 'NEUTRAL', 'NO_TRADE']) assert.match(call(l), /data-call="yes"/, l);
  for (const l of ['PENDING', 'STALE', 'LOW_DATA', 'NO_VALID_PREDICTION']) assert.match(call(l, l === 'STALE' ? 'VALID' : 'INSUFFICIENT_DATA'), /data-call="no"/, l);
  assert.match(call('NO_TRADE'), />NO TRADE</);
  assert.match(call('NO_VALID_PREDICTION', 'INSUFFICIENT_DATA'), />NO VALID PREDICTION</);
  const cls = (h: string) => h.match(/class="([^"]*)" data-testid="cell-label"/)?.[1];
  assert.notEqual(cls(call('NO_TRADE')), cls(call('NO_VALID_PREDICTION', 'INSUFFICIENT_DATA')));
  assert.match(call('BUY'), /cov 90%/);
  assert.match(call('BUY'), /swr cache/);
  assert.match(renderToStaticMarkup(<SignalCell c={null} />), /—/);
});

test('the model grid shows every model per event with its label, and the sources section is reachable', () => {
  const models = { status: 'OK', stale_after_h: 36, note: 'A cell is a model call.', models: [{ id: 'claude', label: 'Claude' }, { id: 'smart_move', label: 'Smart Move score40-v1' }], rows: [
    { id: 1, symbol: 'AAA', earnings_date: '2026-11-12', session: 'BMO', cells: {
      claude: { status: 'VALID', label: 'BUY', signal: 'BUY', coverage_fraction: 0.9, age_h: 2, source: 'swr_cache' },
      smart_move: { status: 'INSUFFICIENT_DATA', label: 'NO_VALID_PREDICTION', signal: 'NO_VALID_PREDICTION' } } }] };
  const html = panel({ emstatus: { data: on }, emmodels: { data: models } }, 'models');
  assert.ok(html.includes('>Claude<') && html.includes('>Smart Move score40-v1<') && html.includes('>BUY<') && html.includes('>NO VALID PREDICTION<'));
  assert.match(html, /Show model sources/);
  assert.match(html, /Click a row for every stored version/);
});

test('accuracy percentages are withheld below the minimum sample and the comparison table says so', () => {
  const acc = { status: 'OK', min_sample: 30, events_with_verified_outcome: 6, events_unverified: 3, note: 'Verified outcomes only.', rows: [
    { model: 'claude', label: 'Claude', decisive: 6, correct: 5, incorrect: 1, flat_outcome: 0, abstained: 1, accuracy_pct: null, sample_status: 'TOO_SMALL (6 of 30 scored calls needed)' },
    { model: 'grok', label: 'Grok', decisive: 40, correct: 24, incorrect: 16, flat_outcome: 1, abstained: 0, accuracy_pct: 60.0, sample_status: 'OK' }] };
  const cmp = { status: 'OK', min_shared: 20, labels: { claude: 'Claude', grok: 'Grok' }, rows: [{ a: 'claude', b: 'grok', shared_events: 6, status: 'TOO_SMALL (6 of 20 shared events needed)' }] };
  const html = panel({ emstatus: { data: on }, emaccuracy: { data: acc }, emcomparison: { data: cmp } }, 'accuracy');
  assert.ok(html.includes('needs 30+ scored calls') && html.includes('60%') && html.includes('Verified outcomes only.'));
  assert.ok(html.includes('not enough shared events yet'));
  assert.ok(!html.includes('83.3'));
  const none = panel({ emstatus: { data: on }, emaccuracy: { data: { status: 'OK', rows: [], events_unverified: 5, min_sample: 30, note: 'n' } } }, 'accuracy');
  assert.match(none, /No verified outcomes yet, so no accuracy can be shown/);
});

test('empty and unstarted states are explained, never filled in', () => {
  const html = panel({ emstatus: { data: { status: 'NOT_STARTED', scheduling: 'OFF', detail: 'x', now: NOW } }, emupcoming: { data: { status: 'NO_DATA', detail: 'The monitor has not run yet: no data store exists.', rows: [] } } });
  assert.match(html, /NOT STARTED on this server/);
  assert.match(html, /No monitor data yet/);
});

test('agoText and nice are plain and never invent a time', () => {
  assert.equal(agoText(null, NOW), 'never');
  assert.equal(agoText('2026-11-09T17:59:45+00:00', NOW), 'just now');
  assert.equal(agoText('2026-11-09T12:00:00+00:00', NOW), '6 h ago');
  assert.equal(nice('SIGNAL_CHANGED'), 'signal changed');
  assert.equal(nice(null), '—');
});

test('the monitor lives inside the Earnings workspace: a sub-tab of the Earnings landing, no separate route or navigation entry', () => {
  const trade = readFileSync('src/pages/EarningsTradePage.tsx', 'utf-8');
  assert.match(trade, /EarningsMonitorPanel = lazy\(\(\) => import\('\.\/EarningsMonitorPanel'\)\)/);
  assert.match(trade, />Upcoming earnings<\/button>/);
  assert.match(trade, />Earnings Monitor<\/button>/);
  assert.ok(!/earnings-monitor/i.test(readFileSync('src/App.tsx', 'utf-8')), 'no standalone route');
  assert.ok(!/earnings-monitor|Earnings Monitor/i.test(readFileSync('src/components/Sidebar.tsx', 'utf-8')), 'no standalone navigation entry');
});

test('the panel contains no trade, order or scoring code', () => {
  const src = readFileSync('src/pages/EarningsMonitorPanel.tsx', 'utf-8');
  assert.ok(!/place.?order|submit.?order|\bbroker\b|trade.?now/i.test(src));
  assert.ok(!/Math\.random|\bscore\s*=|weights?\s*=/.test(src.replace(/score \$\{/g, '')));
});
