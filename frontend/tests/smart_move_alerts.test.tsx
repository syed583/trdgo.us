import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { LiveView } from '../src/pages/SmartMoveEngine20Page';
import { SmartMoveAlertStrip } from '../src/components/SmartMoveAlert';
import Sidebar from '../src/components/Sidebar';

const g = globalThis as any;
const wrap = (el: React.ReactElement) => renderToStaticMarkup(<MemoryRouter initialEntries={['/smart-move/engine20']}>{el}</MemoryRouter>);
const alert = (o: any = {}) => ({
  id: '2026-11-10T16:20:01+00:00', kind: 'GATE_REFUSAL', variant: 'LIVE', task: 'TRDGO-SmartMove-LIVE-6h', at: '2026-11-10T16:20:01+00:00', at_et: '2026-11-10 11:20 EST', minutes_ago: 95,
  exit_code: 3, exit_code_confirmed: true, blocked_record_confirmed: true, schedule_log_checked: true, reasons: ['installed engine = exactly kit v6.1', 'owner attestation valid'], reason_text: 'gates failed: …',
  consecutive: 2, last_success_before_et: '2026-11-10 05:29 EST', next_attempt_et: '2026-11-10 17:20 EST', gates_output: 'sched_run_20261110_162001.txt', acknowledged: false, acknowledged_by: null, acknowledged_at: null, severity: 'critical', ...o,
});
const base = (alerts: any[]) => ({ configured: true, state: alerts.length ? 'BLOCKED' : 'OK', state_text: alerts.length ? 'The last trigger was blocked: gates failed' : 'Scanner running.', now_et: 'x', scan: { last_successful_scan_et: 'y', mode: 'LIVE' },
  requests: { used_today: 1, daily_limit: 700, remaining_today: 699, run_limit: 500 }, events: {}, ledger: { ok: true }, note: 'n', rows: [], alerts });

test('a refusal shows a red card above the predictions with every fact and an Acknowledge button', () => {
  g.__apiState = { emsmartlive: { data: base([alert()]) } };
  const h = wrap(<LiveView />);
  assert.match(h, /data-testid="sm-alert-card"[^>]*data-severity="critical"/);
  assert.ok(h.indexOf('sm-alert-card') < h.indexOf('sm-live-status'), 'the card comes before the status line / predictions');
  assert.match(h, /TRDGO-SmartMove-LIVE-6h/); assert.match(h, /variant LIVE/); assert.match(h, /2026-11-10 11:20 EST ET · 2026-11-10 17:20 VPS/);
  assert.match(h, /Exit code<\/span><span>3 \(confirmed by the schedule log\) · BLOCKED record confirmed/);
  assert.match(h, /installed engine = exactly kit v6.1/); assert.match(h, /owner attestation valid/);
  assert.match(h, /2 refusals since the last successful pass \(2026-11-10 05:29 EST ET\)/);
  assert.match(h, /Next attempt<\/span><span>2026-11-10 17:20 EST ET/);
  assert.match(h, /data-testid="sm-alert-ack"/); assert.match(h, /sched_run_20261110_162001\.txt/);
  assert.match(h, /data-testid="sm-live-state"/); assert.match(h, /BLOCKED: The last trigger was blocked/);
});

test('an acknowledged refusal is grey, keeps the card, hides the Acknowledge button and names who acknowledged', () => {
  g.__apiState = { emsmartlive: { data: base([alert({ acknowledged: true, acknowledged_by: 'owner', acknowledged_at: '2026-11-10T17:00:00+00:00', severity: 'acknowledged' })]) } };
  const h = wrap(<LiveView />);
  assert.match(h, /data-severity="acknowledged"/); assert.match(h, /sm-q-state ack/); assert.match(h, /Gate refusal \(acknowledged\)/);
  assert.match(h, /by owner at 2026-11-10T17:00:00\+00:00/);
  assert.ok(!h.includes('data-testid="sm-alert-ack"'), 'no Acknowledge button once acknowledged');
});

test('no alert means no card, and the page state is not BLOCKED', () => {
  g.__apiState = { emsmartlive: { data: base([]) } };
  const h = wrap(<LiveView />);
  assert.ok(!h.includes('sm-alert-card')); assert.match(h, /<b>Live\.<\/b>/);
});

test('the header strip and the sidebar badge are red until acknowledged, grey afterwards, absent without an alert', () => {
  const red = wrap(<SmartMoveAlertStrip alerts={[alert()]} />);
  assert.match(red, /sm-alert-strip /); assert.match(red, /role="alert"/); assert.match(red, /refused to run at 2026-11-10 11:20 EST ET \(exit 3\), 2 refusals in a row/); assert.match(red, /Open the LIVE scanner/);
  const grey = wrap(<SmartMoveAlertStrip alerts={[alert({ acknowledged: true, acknowledged_by: 'owner' })]} />);
  assert.match(grey, /sm-alert-strip ack/); assert.match(grey, /role="status"/); assert.match(grey, /acknowledged by owner/);
  assert.equal(wrap(<SmartMoveAlertStrip alerts={[]} />), '');
  const side = wrap(<Sidebar optionsMode={false} search="" smAlert="critical" />);
  assert.match(side, /nav-badge critical/); assert.match(side, /data-testid="sm-alert-badge"/);
  assert.match(wrap(<Sidebar optionsMode={false} search="" smAlert="ack" />), /nav-badge ack/);
  assert.ok(!wrap(<Sidebar optionsMode={false} search="" />).includes('sm-alert-badge'));
});
