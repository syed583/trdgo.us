import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveSignalsPage, { SignalBadge, StatusBadge } from '../src/pages/SmartMoveSignalsPage';
import fx from './fixtures/live_signals.json';

const g = globalThis as any;
const ctx: any = {};
const page = (state: any) => {
  g.__apiState = state;
  return renderToStaticMarkup(<SmartMoveSignalsPage ctx={ctx} />);
};
const ok = { sigsummary: { data: (fx as any).summary }, sigrows: { data: (fx as any).rows } };
const row = (sym: string) => (fx as any).rows.rows.find((r: any) => r.symbol === sym);

test('the table has the required columns and the BUY / SELL / NO TRADE filters', () => {
  const html = page(ok);
  for (const c of ['Symbol', 'Earnings date', 'Session', 'Signal', 'Confidence', 'Model', 'Updated', 'Data status']) assert.ok(html.includes('<th>' + c + '</th>'), c);
  for (const b of ['>All<', '>BUY<', '>SELL<', '>NO TRADE<']) assert.ok(html.includes(b), b);
  assert.match(html, /aria-label="Search symbol or company"/);
});

test('BUY, SELL and NO TRADE rows are shown with company, date and session', () => {
  const html = page(ok);
  for (const s of ['SYNB', 'SYNTHETIC BUY CORP', '2026-11-04', 'Before Open', 'SYNS', 'After Close', '2026-11-03']) assert.ok(html.includes(s), s);
  assert.match(renderToStaticMarkup(<SignalBadge row={row('SYNB')} />), /sm-sig buy[^>]*>BUY</);
  assert.match(renderToStaticMarkup(<SignalBadge row={row('SYNS')} />), /sm-sig sell[^>]*>SELL</);
  assert.match(renderToStaticMarkup(<SignalBadge row={row('SYNN')} />), />NO TRADE</);
});

test('NO TRADE is split into missing inputs and a neutral model, so missing data never looks like a view', () => {
  const html = page(ok);
  assert.match(html, /insufficient inputs/);
  assert.match(html, /model neutral/);
  assert.ok((html.match(/insufficient inputs/g) || []).length >= 4);                    // the 3 real rows and the synthetic missing-data row
});

test('no confidence figure is ever shown: it reads "not calibrated"', () => {
  const html = page(ok);
  assert.ok((html.match(/not calibrated/g) || []).length === 8);
  assert.doesNotMatch(html, /\d+(\.\d+)?\s?% confidence|confidence:\s?\d/i);
});

test('only LIVE rows can look current: stale, historical, unverified and synthetic rows carry their own label', () => {
  const html = page(ok);
  for (const st of ['SYNTHETIC', 'HISTORICAL', 'UNVERIFIED']) assert.ok(html.includes('>' + st + '<'), st);
  assert.doesNotMatch(html, /data-testid="data-status">LIVE</);                         // the fixture holds no live row, so no row is badged live
  assert.match(renderToStaticMarkup(<StatusBadge status="STALE" />), /amber[^>]*>STALE</);
  assert.match(html, /Only rows marked LIVE are current/);
});

test('coverage cards show counts from the API and never claim full coverage', () => {
  const html = page(ok);
  for (const k of ['Eligible companies', 'Complete data', 'Partial data', 'No valid prediction', 'Live rows']) assert.ok(html.includes(k), k);
  assert.match(html, /covers only part of the earnings calendar/);
  assert.match(html, /companies without a row have no signal from this page/);
});

test('an unverified batch marker is called out', () => {
  const s = { ...(fx as any).summary, marker_valid: false };
  assert.match(page({ sigsummary: { data: s }, sigrows: { data: (fx as any).rows } }), /completion marker could not be verified/);
});

test('loading, API failure, no signals, unreadable store, empty filter and a rows failure are all visible states', () => {
  assert.match(page({ sigsummary: { initialLoading: true }, sigrows: {} }), /Loading signals/);
  const failed = page({ sigsummary: { error: 'SERVICE_UNAVAILABLE' }, sigrows: {} });
  assert.match(failed, /could not be loaded: SERVICE_UNAVAILABLE/);
  assert.match(failed, /Retry/);
  const none = page({ sigsummary: { data: { status: 'NO_SIGNALS', detail: 'No signals have been generated yet.' } }, sigrows: {} });
  assert.match(none, /No signals yet/);
  assert.match(none, /only after a validated, sealed collection batch/);
  assert.doesNotMatch(none, /Eligible companies/);
  assert.match(page({ sigsummary: { data: { status: 'UNREADABLE', detail: 'x' } }, sigrows: {} }), /Signal store unreadable/);
  assert.match(page({ sigsummary: { data: (fx as any).summary }, sigrows: { data: { status: 'OK', rows: [], total: 0 } } }), /No signals match these filters/);
  assert.match(page({ sigsummary: { data: (fx as any).summary }, sigrows: { error: 'HTTP_500' } }), /HTTP_500/);
});

test('the page contains no order, trade-placement or score computation', () => {
  const html = page(ok);
  assert.doesNotMatch(html, /<button[^>]*>[^<]*(place order|buy now|sell now|execute)/i);
  assert.match(html, /does not place orders/);
});
