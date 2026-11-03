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

test('the eight views exist; Upcoming Earnings shows session, vendor check, eligibility and the latest change', () => {
  const html = panel({ emstatus: { data: on }, emupcoming: { data: upcoming } });
  for (const t of ['Upcoming Earnings', 'All 9 Models', 'Smart Money Move', 'Prediction Changes', 'News &amp; Options Activity', 'Model Consensus', 'Historical Accuracy', 'Scanner Health']) assert.ok(html.includes('>' + t + '<'), t);
  for (const c of ['Symbol', 'Earnings date', 'Session', 'Days', 'Calendar check', 'Last verified', 'Status', 'Eligible', 'Models with a fresh call', 'Evidence', 'Latest change']) assert.ok(html.includes('<th>' + c + '</th>'), c);
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


test('Upcoming marks events beyond the model window instead of leaving them blank', () => {
  const up = { status: 'OK', horizon_days: 30, model_window_days: 15, rows: [{ id: 9, symbol: 'FAR', company: 'Far', earnings_date: '2026-12-03', session: 'AMC', status: 'ACTIVE', days_to: 24, verification: 'SINGLE_VENDOR', last_verified: null, out_of_window: true,
    eligible: false, ineligible_reasons: ['BEYOND_15_DAY_MODEL_WINDOW'], valid_predictions: 0, latest_change: null, versions: [], sources: [], outcome: null }] };
  const html = panel({ emstatus: { data: on }, emupcoming: { data: up } });
  assert.match(html, /data-testid="oow"[^>]*>outside 15-day model window</);
  assert.match(html, /Looks 30 days ahead; the eight models score the next 15 days/);
  assert.match(html, />never</);
});

test('Smart Money Move shows the engine category, coverage and engine detail, and labels what it has not scored', () => {
  const sm = { status: 'OK', engine: { model_version: 'score40-v1', how_predictions_arise: 'verified snapshot only' }, counts: { NO_TRADE: 1, PENDING: 1 }, plan: { requests_needed: 12, symbols: ['AAA'] }, note: 'The monitor never scores Smart Move.', rows: [
    { id: 1, symbol: 'AAA', company: 'A', earnings_date: '2026-11-12', session: 'BMO', days_to: 3, label: 'NO_TRADE', signal: 'NO_TRADE', coverage: '30/40', as_of: '2026-11-09T12:00:00+00:00', age_h: 6, stale: false, source: 'smart_move_engine', details: { result: 'NO TRADE (genuine)', coverage_points: 30 } },
    { id: 2, symbol: 'BBB', company: 'B', earnings_date: '2026-11-13', session: 'AMC', days_to: 4, label: 'PENDING', signal: null, coverage: null, as_of: null, age_h: null, stale: false, source: null, details: null }] };
  const html = panel({ emstatus: { data: on }, emsmart: { data: sm } }, 'smart');
  assert.match(html, /score40-v1: verified snapshot only/);
  assert.match(html, />NO TRADE</);
  assert.match(html, /30\/40/);
  assert.match(html, /the engine has not scored this event/);
  assert.match(html, /Evidence request on file: 12 provider request/);
  assert.match(html, /data-call="no"[^>]*>PENDING</);
});

test('News and options activity keeps collected apart from verified and says news cannot change a score', () => {
  const ev = { status: 'OK', last_scan: { at: '2026-11-09T17:45:00+00:00', items: 5, new: 2, changed: 1 }, note: 'discovered = ...', unsupported: ['Licensed analyst-revision feeds are not connected.'],
    summary: [{ kind: 'NEWS', collected: 3, verified: 0, feeds_a_score: false }, { kind: 'OPTIONS_FLOW', collected: 0, verified: 2, feeds_a_score: true }],
    rows: [{ id: 1, symbol: 'AAA', kind: 'OPTIONS_FLOW', title: 'Options tape summary', stage: 'VERIFIED', relevant_models: ['claude'], used_by_models: [], last_seen: '2026-11-09T17:45:00+00:00', data: { total_premium: 2000000, bullish_share: 75, prints: 12 } },
      { id: 2, symbol: 'AAA', kind: 'NEWS', title: 'AAA outlook raised', stage: 'COLLECTED', relevant_models: [], used_by_models: [], last_seen: '2026-11-09T17:45:00+00:00', data: {} }] };
  const html = panel({ emstatus: { data: on }, emevidence: { data: ev } }, 'evidence');
  assert.match(html, /2 new, 1 changed/);
  assert.match(html, /never: shown here only/);
  assert.match(html, /\$2000k premium · 75% bullish · 12 prints/);
  assert.match(html, /Licensed analyst-revision feeds are not connected/);
  assert.match(html, />verified</);
  assert.match(html, />collected</);
});

test('Scanner Health flags stuck jobs and duplicates and otherwise says healthy', () => {
  const ok = { status: 'OK', healthy: true, queue: { DONE: 5 }, heartbeat: { scans_age_s: 12 }, provider_requests_today: 0, pending_alerts: 0, stuck_jobs: [], dead_jobs: [], duplicate_active_events: [], duplicate_prediction_rows: 0,
    last_runs: { ELIGIBILITY_CHECK: { started_at: '2026-11-09T17:30:00+00:00', finished_at: '2026-11-09T17:30:02+00:00', ok: true, duration_s: 1.8 } } };
  const a = panel({ emstatus: { data: on }, emhealth: { data: ok } }, 'health');
  assert.match(a, />HEALTHY</);
  const bad = { ...ok, healthy: false, stuck_jobs: [{ kind: 'EVIDENCE_SCAN' }], duplicate_active_events: [{ symbol: 'AAA' }], duplicate_prediction_rows: 2 };
  const b = panel({ emstatus: { data: on }, emhealth: { data: bad } }, 'health');
  assert.match(b, />NEEDS ATTENTION</);
  assert.match(b, /past their lease \(stuck\): EVIDENCE_SCAN/);
  assert.match(b, /Duplicate active events: AAA/);
  assert.match(b, /2 duplicated prediction version/);
});

test('a prediction cell shows the previous call beside the latest with the reason', () => {
  const html = renderToStaticMarkup(<SignalCell c={{ status: 'VALID', label: 'BUY', signal: 'BUY', score: 72, coverage_fraction: 0.9, age_h: 1, source: 'swr_cache', previous: { signal: 'NEUTRAL', score: 50, recorded_at: '2026-11-09T12:00:00+00:00' }, reason: 'the model own call changed' }} />);
  assert.match(html, /data-testid="cell-previous"[^>]*>was neutral \(50\)/);
  assert.match(html, /the model own call changed/);
});

test('Scanner Health shows real start, finish, milliseconds, events processed and queue wait per run, and marks an interrupted run', () => {
  const h = { status: 'OK', healthy: true, queue: { DONE: 3 }, heartbeat: { scans_age_s: 5 }, provider_requests_today: 0, pending_alerts: 0, stuck_jobs: [], dead_jobs: [], duplicate_active_events: [], duplicate_prediction_rows: 0, last_runs: {},
    recent_runs: [{ kind: 'ELIGIBILITY_CHECK', started_at: '2026-11-09T17:30:00+00:00', finished_at: '2026-11-09T17:30:02+00:00', ok: true, duration_ms: 1840, events_processed: 302, queue_wait_ms: 120 },
      { kind: 'EARNINGS_DAY_CHECK', started_at: '2026-11-09T12:45:00+00:00', finished_at: null, ok: null, interrupted: true, duration_ms: null, events_processed: null, queue_wait_ms: null }] };
  const html = panel({ emstatus: { data: on }, emhealth: { data: h } }, 'health');
  assert.match(html, /<td>1840<\/td><td>302<\/td><td>120<\/td>/);
  assert.match(html, /interrupted \(never finished\)/);
  assert.match(html, /not recorded/);
});

test('Upcoming shows how many of the nine models have a fresh call and how fresh the evidence is', () => {
  const up = { status: 'OK', horizon_days: 30, model_window_days: 15, rows: [{ id: 1, symbol: 'AAA', company: 'A', earnings_date: '2026-11-12', session: 'BMO', status: 'ACTIVE', days_to: 3, verification: 'CONFIRMED', last_verified: '2026-11-09T12:00:00+00:00', out_of_window: false,
    eligible: true, ineligible_reasons: [], valid_predictions: 8, fresh_predictions: 6, models_total: 9, evidence_items: 4, last_evidence_at: '2026-11-09T17:45:00+00:00', latest_change: null, versions: [], sources: [], outcome: null }] };
  const html = panel({ emstatus: { data: on }, emupcoming: { data: up } });
  assert.match(html, /data-testid="coverage"[^>]*>.*6 of 9/);
  assert.match(html, /2 stale/);
  assert.match(html, /4 item\(s\), newest/);
});

test('Smart Money Move separates a historical snapshot from a live engine result', () => {
  const sm = { status: 'OK', engine: { model_version: 'score40-v1', how_predictions_arise: 'verified snapshot only' }, counts: {}, note: 'n', rows: [
    { id: 1, symbol: 'AAA', company: 'A', earnings_date: '2026-11-12', session: 'BMO', days_to: 3, label: 'BUY', signal: 'BUY', coverage: '30/40', as_of: null, age_h: null, stale: false, source: 'smart_move_snapshot', origin: 'HISTORICAL_SNAPSHOT', details: null },
    { id: 2, symbol: 'BBB', company: 'B', earnings_date: '2026-11-13', session: 'AMC', days_to: 4, label: 'SELL', signal: 'SELL', coverage: '31/40', as_of: null, age_h: null, stale: false, source: 'smart_move_engine', origin: 'LIVE_ENGINE', details: null }] };
  const html = panel({ emstatus: { data: on }, emsmart: { data: sm } }, 'smart');
  assert.match(html, />historical snapshot</);
  assert.match(html, />live engine</);
});

test('Scanner Health shows the approval end date, daily usage and the refresh plan with the events it cannot cover', () => {
  const h = { status: 'OK', healthy: true, queue: {}, heartbeat: { scans_age_s: 3 }, provider_requests_today: 0, pending_alerts: 0, stuck_jobs: [], dead_jobs: [], duplicate_active_events: [], duplicate_prediction_rows: 0, recent_runs: [],
    approval_expires_at: '2026-11-17T12:12:35+00:00', approval_days_left: 2.5, usage_by_day: [{ day: '2026-11-09', requests: 120, failures: 1 }],
    refresh_plan: { profile: 'lean', daily_cap: 1000, external_reserve: 250, calendar_reserve: 92, outcome_reserve: 10, event_slots: 10, needing_refresh: 30, unfunded_count: 20, app_requests_today: 345, unfunded: [{ symbol: 'ZZZ', earnings_date: '2026-11-14' }] } };
  const html = panel({ emstatus: { data: on }, emhealth: { data: h } }, 'health');
  assert.match(html, /2\.5 days left/);
  assert.match(html, /role="alert"/);
  assert.match(html, /<td>120<\/td><td>1<\/td>/);
  assert.match(html, /10 event refresh\(es\) fit; 20 of 30 events that need one do not/);
  assert.match(html, /The application has made 345 request/);
  assert.match(html, /Not covered: ZZZ 2026-11-14/);
});

test('Historical accuracy lists verified reactions with the cutoff and waiting events', () => {
  const acc = { status: 'OK', rows: [], events_unverified: 1, events_with_verified_outcome: 1, min_sample: 30, note: 'n' };
  const out = { status: 'OK', rows: [{ symbol: 'AAA', earnings_date: '2026-11-10', session: 'AMC', move_pct: 4, verified: true, prediction_cutoff: '2026-11-10T20:00:00+00:00', calls: { claude: { signal: 'BUY', recorded_at: 'x' } }, results: { claude: 'CORRECT' } }], waiting_for_outcome: [{ symbol: 'BBB', earnings_date: '2026-11-09' }] };
  const html = panel({ emstatus: { data: on }, emaccuracy: { data: acc }, emoutcomes: { data: out } }, 'accuracy');
  assert.match(html, /Verified earnings reactions/);
  assert.match(html, /claude buy/);
  assert.match(html, /claude correct/);
  assert.match(html, /Waiting for a verified reaction: BBB 2026-11-09/);
});

test('Smart Money Move groups events by market date and says NO PREDICTION AVAILABLE with the real reason instead of leaving Nov 3 blank', () => {
  const sm = { status: 'OK', engine: { model_version: 'score40-v1', how_predictions_arise: 'verified snapshot only' }, counts: {}, note: 'n',
    coverage: { source: 'HISTORICAL_SNAPSHOT', generated_at: '2026-11-03T08:29:08+00:00', rows: 20, event_dates: ['2026-11-04'] },
    rows: [
      { id: 1, symbol: 'AAA', company: 'A', earnings_date: '2026-11-03', session: 'BMO', days_to: 0, label: 'NO_PREDICTION_AVAILABLE', signal: null, coverage: null, as_of: null, age_h: null, stale: false, source: null, origin: 'NOT_GENERATED',
        reason: { code: 'WINDOW_CLOSED_BEFORE_RUN', text: 'This report was less than 24 hours away when the snapshot was made.' }, details: null },
      { id: 2, symbol: 'CCC', company: 'C', earnings_date: '2026-11-04', session: 'BMO', days_to: 1, label: 'BUY', signal: 'BUY', coverage: '30/40', as_of: '2026-11-03T07:00:00+00:00', age_h: 6, stale: false, source: 'smart_move_snapshot', origin: 'HISTORICAL_SNAPSHOT', reason: null, details: null },
      { id: 3, symbol: 'DDD', company: 'D', earnings_date: '2026-11-04', session: 'AMC', days_to: 1, label: 'NO_PREDICTION_AVAILABLE', signal: null, coverage: null, as_of: null, age_h: null, stale: false, source: null, origin: 'NOT_GENERATED',
        reason: { code: 'NOT_SELECTED_OR_NOT_COLLECTED', text: 'The latest snapshot scored 20 event(s); this one was not among them.' }, details: null }] };
  const html = panel({ emstatus: { data: on }, emsmart: { data: sm } }, 'smart');
  const h3 = html.indexOf('2026-11-03'), h4 = html.indexOf('2026-11-04');
  assert.ok(h3 > -1 && h4 > h3, 'Nov 3 group comes first');
  assert.equal((html.match(/data-testid="sm-date-header"/g) || []).length, 2);
  assert.match(html, /market date · 1 event\(s\) · 0 with a prediction · 1 without/);
  assert.match(html, /market date · 2 event\(s\) · 1 with a prediction · 1 without/);
  assert.match(html, />NO PREDICTION AVAILABLE</);
  assert.match(html, /less than 24 hours away when the snapshot was made/);
  assert.match(html, />historical snapshot</);
  assert.match(html, />not generated</);
  assert.match(html, /Latest historical snapshot: made .* scored 20 event\(s\) dated 2026-11-04/);
  assert.doesNotMatch(html, /data-call="yes"[^>]*>NO PREDICTION/);                      // never drawn as a call
});

test('dates are printed as the exchange dates they are, and times are always UTC, whatever the viewer timezone', () => {
  const sm = { status: 'OK', engine: { model_version: 'score40-v1', how_predictions_arise: 'x' }, counts: {}, note: 'n', rows: [
    { id: 1, symbol: 'AAA', company: 'A', earnings_date: '2026-11-03', session: 'AMC', days_to: 0, label: 'BUY', signal: 'BUY', coverage: '30/40', as_of: '2026-11-03T22:30:00+00:00', age_h: 1, stale: false, source: 'smart_move_engine', origin: 'LIVE_ENGINE', reason: null, details: null }] };
  const prev = process.env.TZ;
  const seen: string[] = [];
  for (const tz of ['Asia/Dubai', 'America/New_York', 'Pacific/Auckland']) {
    process.env.TZ = tz;
    const html = panel({ emstatus: { data: on }, emsmart: { data: sm } }, 'smart');
    assert.match(html, /<b>2026-11-03<\/b>/);                                            // the market date never shifts
    assert.match(html, /03 Nov, 10:30 PM UTC/);                        // 22:30 UTC stays Nov 03 and says UTC
    seen.push(tz);
  }
  if (prev === undefined) delete process.env.TZ; else process.env.TZ = prev;
  assert.equal(seen.length, 3);
});

test('the nine-model grid shows NO PREDICTION AVAILABLE and its reason for Smart Money Move', () => {
  const html = renderToStaticMarkup(<SignalCell c={{ status: 'PENDING', label: 'NO_PREDICTION_AVAILABLE', reason: 'Less than 24 hours away when the snapshot was made.', origin: 'NOT_GENERATED' }} />);
  assert.match(html, />NO PREDICTION AVAILABLE</);
  assert.match(html, /data-testid="cell-reason"[^>]*>Less than 24 hours/);
  assert.match(html, /data-call="no"/);
});

test('Smart Money Move shows the prediction-window watch from the live engine, and says plainly when it cannot be read', () => {
  const base = { status: 'OK', engine: { model_version: 'score40-v1', how_predictions_arise: 'x' }, counts: {}, note: 'n', rows: [] };
  const live = panel({ emstatus: { data: on }, emsmart: { data: { ...base, windows: { as_of: '2026-11-03T08:00:00+00:00', open: 40, open_without_result: 25, first_cutoff_utc: '2026-11-03T12:00:00+00:00', missing_by_date: { '2026-11-04': 25 } } } } }, 'smart');
  assert.match(live, /data-testid="sm-windows"[^>]*role="alert"/);
  assert.match(live, /40 event\(s\) inside the window now, 25 still without a result/);
  assert.match(live, /after that no prediction is allowed/);
  assert.match(live, /missing by date: 2026-11-04 25/);
  const none = panel({ emstatus: { data: on }, emsmart: { data: base } }, 'smart');
  assert.match(none, /data-testid="sm-windows-na"/);
  assert.match(none, /so they are not guessed/);
});
