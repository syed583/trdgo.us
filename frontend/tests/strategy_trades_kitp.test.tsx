import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import NewStrategyTradesPage from '../src/pages/NewStrategyTradesPage';

/**
 * Kit P addendum, Strategy Trades (owner, 2026-11-21).
 *
 * These assert INTENT, not markup trivia: R:R is gone from the page entirely, a P&L carries a
 * direction class so the stylesheet can colour it, and the voice-alert control is there to be
 * pressed. The speaking itself is an effect, which does not run under server rendering - it is
 * covered by the browser check instead.
 */

const g = globalThis as any;
const wrap = (el: React.ReactElement) =>
  renderToStaticMarkup(<MemoryRouter initialEntries={['/strategy-trades']}>{el}</MemoryRouter>);

const row = (o: any = {}) => ({
  id: 1, symbol: 'NVDA', side: 'LONG', status: 'OPEN', strategy_label: 'Agent 2',
  entry_price: 100, stop: 98, target: 104, last_price: 101,
  profit: 12.5, profit_pct: 1.25, score_at_entry: 61, entry_time: '10:05:00 AM', ...o,
});

const payload = (o: any = {}) => ({
  status: 'OK', count: 2, open: 1, closed: 1, wins: 1,
  total_profit: 12.5, total_pct: 0.4, realized_on_date: -8.25, closed_on_date: 1,
  date: '2026-11-20', strategies: [], rules: {},
  rows: [row(), row({ id: 2, symbol: 'TSLA', side: 'SHORT', status: 'STOP', profit: -8.25, profit_pct: -0.9 })],
  ...o,
});

const render = (o: any = {}) => {
  g.__apiState = { strategytrades: { data: payload(o) } };
  return wrap(<NewStrategyTradesPage ctx={{ isAdmin: false } as any} />);
};

test('reward-to-risk is gone from the page: no column, no tile, no wording', () => {
  const h = render();
  assert.doesNotMatch(h, />R:R</, 'the R:R column header is still rendered');
  assert.doesNotMatch(h, /reward to risk/i, 'the reward-to-risk tile is still rendered');
  assert.doesNotMatch(h, /breakeven — no risk left/i, 'the R:R breakeven explanation survived');
});

test('a profit and a loss each carry a direction class, so the stylesheet can colour them', () => {
  const h = render();
  // the winning row's P&L and the losing row's P&L
  assert.match(h, /class="r up"[^>]*>\$12\.50</, 'a profit is not marked as up');
  assert.match(h, /class="r down"[^>]*>-\$8\.25</, 'a loss is not marked as down');
});

test('the P&L tiles colour the number itself, not just the icon beside it', () => {
  const h = render();
  assert.match(h, /<b class="up">\$12\.50<\/b>/, 'the P&L tile value is not marked as up');
  assert.match(h, /<b class="down">-\$8\.25<\/b>/, 'the realised tile value is not marked as down');
  // a count is not a P&L and must stay plain ink
  assert.match(h, /<b>2<\/b>/, 'the trade count was given a direction colour it should not have');
});

test('the voice-alert control is present and starts OFF', () => {
  const h = render();
  assert.match(h, /class="nst-snd"/, 'the voice-alert button is missing');
  assert.match(h, /Enable voice alerts/, 'the button does not offer to turn voice alerts on');
  assert.match(h, /aria-pressed="false"/, 'the button does not report its state to a screen reader');
});

test('a model name is never resolved here: the server already sent Agent 2 (A.26)', () => {
  const h = render();
  assert.match(h, /Agent 2/);
  assert.doesNotMatch(h, /Claude|ChatGPT|Gemini|Grok|Kimi/i, 'a vendor name reached the blotter');
});
