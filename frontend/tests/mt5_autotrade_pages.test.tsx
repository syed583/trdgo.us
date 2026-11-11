import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { Mt5AccountView, Mt5SymbolsView, Mt5AuditView } from '../src/pages/Mt5AutoTradePage';

const g = globalThis as any;
const render = (el: React.ReactElement, path = '/mt5') => renderToStaticMarkup(<MemoryRouter initialEntries={[path]}>{el}</MemoryRouter>);

test('account page shows the switch, connection status, the pre-filled server and never a password', () => {
  g.__apiState = { mt5status: { data: { installed: true, connected: true, auto_trade: false, paused: false, server: 'STGlobalMarketsLtd-Live-1', default_server: 'STGlobalMarketsLtd-Live-1',
    account: { login: 123, account_kind: 'DEMO', company: 'ST', currency: 'USD', balance: 10000, equity: 10010 }, open_positions: [{ trade_id: 1, strategy: 'grok', symbol: 'AAPL', broker_symbol: 'AAPL', side: 'LONG', volume: 99, ticket: 501, price: 100.1, sl: 98, tp: 104, opened_at: 'x' }] } } };
  const h = render(<Mt5AccountView />);
  assert.match(h, /AUTO_TRADE = 0 \(OFF\)/); assert.match(h, />Connected</); assert.match(h, /STGlobalMarketsLtd-Live-1/); assert.match(h, /DEMO/);
  assert.match(h, /data-testid="mt5-open-table"/); assert.match(h, /type="password"/);
  assert.ok(!/s3cret/.test(h));
});

test('account page reports a failed connection and the paused state', () => {
  g.__apiState = { mt5status: { data: { installed: false, install_note: 'pip install MetaTrader5', connected: false, auto_trade: true, paused: true, server: 'STGlobalMarketsLtd-Live-1', last_error: 'INITIALIZE_FAILED: -6 auth', open_positions: [] } } };
  const h = render(<Mt5AccountView />);
  assert.match(h, />Failed</); assert.match(h, /INITIALIZE_FAILED/); assert.match(h, /mtapi\.io/); assert.match(h, /entries PAUSED/); assert.match(h, /MT API endpoint/);
});

test('symbol mapping page lists every symbol with its status and the X of Y summary', () => {
  g.__apiState = { mt5symbols: { data: { connected: true, mapped: 2, total: 3, summary: '2 of 3 symbols mapped', rows: [
    { symbol: 'AAPL', broker_symbol: 'AAPL', status: 'MAPPED', volume_min: 1, volume_step: 1, trade_mode: 'FULL' },
    { symbol: 'TSLA', broker_symbol: 'TSLA.NAS', status: 'MAPPED', volume_min: 10, volume_step: 10, trade_mode: 'FULL' },
    { symbol: 'NVDA', broker_symbol: null, status: 'UNMAPPED' }] } } };
  const h = render(<Mt5SymbolsView />, '/mt5/symbols');
  assert.match(h, /2 of 3 symbols mapped/);
  assert.equal((h.match(/data-testid="mt5-map-row"/g) || []).length, 3);
  assert.match(h, /data-status="UNMAPPED"/); assert.match(h, /TSLA\.NAS/); assert.match(h, /10 \/ 10/);
});

test('audit page renders the event table newest first with reasons', () => {
  g.__apiState = { mt5audit: { data: { auto_trade: true, paused: false, rows: [
    { at: '2026-11-11T15:00:00+00:00', event: 'REJECTED', symbol: 'AAPL', broker_symbol: 'AAPL', action: 'LONG', quantity: 99, reason: 'NO_MONEY (10019): refused', trade_id: 7, strategy: 'grok' },
    { at: '2026-11-11T14:00:00+00:00', event: 'FILLED', symbol: 'TSLA', broker_symbol: 'TSLA.NAS', action: 'SHORT', quantity: 40, price: 200, ticket: 502, trade_id: 6, strategy: 'kimi', result: 'DONE' }] } } };
  const h = render(<Mt5AuditView />, '/mt5/audit');
  assert.match(h, /data-testid="mt5-audit-table"/);
  const t = h.slice(h.indexOf('data-testid="mt5-audit-table"'));
  assert.ok(t.indexOf('>REJECTED<') < t.indexOf('>FILLED<'), 'newest first inside the table');
  assert.match(h, /NO_MONEY \(10019\)/); assert.match(h, /#7 grok/); assert.match(h, /502/);
  g.__apiState = { mt5audit: { data: { rows: [] } } };
  assert.match(render(<Mt5AuditView />, '/mt5/audit'), /mt5-audit-none/);
});
