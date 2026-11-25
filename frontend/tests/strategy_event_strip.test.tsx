import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import StrategyEventStrip, {
  diffTradeEvents, mergeEvents, type TradeEvent,
} from '../src/components/StrategyEventStrip';
import NewStrategyTradesPage from '../src/pages/NewStrategyTradesPage';

/**
 * Kit T: the live event strip on Strategy Trades.
 *
 * The rule under test is the one the classic page used: an event exists only at the moment a
 * trade's status differs from the status remembered from the previous read. Everything else -
 * the blink, the row flash, the voice - hangs off that single list.
 */

const row = (o: any = {}) => ({
  id: 1, symbol: 'NVDA', side: 'LONG', status: 'OPEN', strategy_label: 'Agent 2',
  entry_price: 100.5, entry_time: '10:05:00 AM', last_price: 101, exit_price: null, profit: 0, ...o,
});
const NOW = '11:22:33 AM';

/** Seed a baseline with `before`, then diff `after` against it. */
function between(before: any[], after: any[]): TradeEvent[] {
  const seen = new Map<number, string>();
  assert.deepEqual(diffTradeEvents(seen, before, false, NOW), [], 'the first read must be silent');
  return diffTradeEvents(seen, after, true, NOW);
}

// ------------------------------------------------------------------ the five event types
test('BUY: a new OPEN long that was not there before', () => {
  const [e, ...rest] = between([], [row()]);
  assert.equal(rest.length, 0);
  assert.equal(e.kind, 'BUY');
  assert.equal(e.symbol, 'NVDA');
  assert.equal(e.price, 100.5);
  assert.equal(e.time, '10:05:00 AM', 'an entry shows the SERVER entry time');
  assert.equal(e.timeIsSeen, false);
  assert.equal(e.profit, null, 'an entry has no P&L');
});

test('SELL: a new OPEN short', () => {
  const [e] = between([], [row({ id: 2, symbol: 'TSLA', side: 'SHORT' })]);
  assert.equal(e.kind, 'SELL');
  assert.equal(e.symbol, 'TSLA');
});

test('STOP LOSS: an open trade that is now STOP, with its loss', () => {
  const [e] = between([row()], [row({ status: 'STOP', exit_price: 98.2, profit: -23.4 })]);
  assert.equal(e.kind, 'STOP');
  assert.equal(e.price, 98.2);
  assert.equal(e.profit, -23.4);
  assert.equal(e.time, NOW);
  assert.equal(e.timeIsSeen, true, 'the payload has no exit time of day, so this is when the page saw it');
});

test('TARGET: an open trade that is now TARGET, with its profit', () => {
  const [e] = between([row()], [row({ status: 'TARGET', exit_price: 104, profit: 35 })]);
  assert.equal(e.kind, 'TARGET');
  assert.equal(e.profit, 35);
});

test('SCORE EXIT: an open trade closed on the score, profit or loss as it fell', () => {
  const [e] = between([row()], [row({ status: 'SCORE_EXIT', exit_price: 100.9, profit: 4 })]);
  assert.equal(e.kind, 'SCORE_EXIT');
});

test('any other close (end of day, the bell) is CLOSED - shown, never mislabelled as a stop', () => {
  for (const status of ['EOD_CLOSE', 'MARKET_CLOSE', 'TIMED_EXIT']) {
    const [e] = between([row()], [row({ status, exit_price: 100.2, profit: -3 })]);
    assert.equal(e.kind, 'CLOSED', status);
  }
});

test('an exit with no exit_price falls back to the last price rather than inventing one', () => {
  const [e] = between([row()], [row({ status: 'STOP', exit_price: null, last_price: 97.7 })]);
  assert.equal(e.price, 97.7);
});

// ------------------------------------------------------------------ no duplicate blink
test('NO DUPLICATE ON REFRESH: the same rows read again produce nothing', () => {
  const seen = new Map<number, string>();
  diffTradeEvents(seen, [row()], false, NOW);
  const closed = [row({ status: 'TARGET', exit_price: 104, profit: 35 })];
  assert.equal(diffTradeEvents(seen, closed, true, NOW).length, 1, 'the change is reported once');
  for (let i = 0; i < 5; i++) {
    assert.deepEqual(diffTradeEvents(seen, closed, true, NOW), [], `refresh ${i + 1} must be silent`);
  }
});

test('the FIRST read never announces anything, however many trades the day already has', () => {
  const seen = new Map<number, string>();
  const day = [row(), row({ id: 2, status: 'STOP' }), row({ id: 3, status: 'TARGET' })];
  assert.deepEqual(diffTradeEvents(seen, day, false, NOW), []);
  assert.deepEqual(diffTradeEvents(seen, day, true, NOW), [], 'and the next identical read is silent too');
});

test('a trade already closed when the page opened does not fire later', () => {
  const closed = row({ status: 'STOP', profit: -5 });
  assert.deepEqual(between([closed], [closed]), []);
});

test('mergeEvents keeps the last ten, newest first, and never the same event twice', () => {
  let list: TradeEvent[] = [];
  for (let i = 1; i <= 14; i++) {
    const [e] = between([], [row({ id: i, symbol: `S${i}` })]);
    list = mergeEvents(list, [e]);
    assert.equal(mergeEvents(list, [e]), list, 'merging the same event again changes nothing');
  }
  assert.equal(list.length, 10);
  assert.equal(list[0].symbol, 'S14', 'newest first');
  assert.equal(list[9].symbol, 'S5');
});

// ------------------------------------------------------------------ the strip itself
const ev = (kind: TradeEvent['kind'], o: Partial<TradeEvent> = {}): TradeEvent => ({
  key: `9:${kind}`, id: 9, kind, symbol: 'AMZN', price: 201.25, time: NOW, timeIsSeen: true,
  profit: kind === 'BUY' || kind === 'SELL' ? null : 12.5, model: 'Agent 1', ...o,
});
const strip = (events: TradeEvent[], blink: string[] = []) =>
  renderToStaticMarkup(<StrategyEventStrip events={events} blinking={new Set(blink)} />);

test('each event type carries its colour family: BUY/TARGET green, SELL/STOP red, SCORE EXIT amber', () => {
  const tone = (k: TradeEvent['kind']) => (strip([ev(k)]).match(/class="nst-event (\w+)/) || [])[1];
  assert.equal(tone('BUY'), 'up');
  assert.equal(tone('TARGET'), 'up');
  assert.equal(tone('SELL'), 'down');
  assert.equal(tone('STOP'), 'down');
  assert.equal(tone('SCORE_EXIT'), 'warn');
});

test('the SYMBOL comes first, then the event type, price and ET time', () => {
  const h = strip([ev('STOP')]);
  const order = ['nst-event-sym', 'nst-event-tag', 'nst-event-px', 'nst-event-at', 'nst-event-pl']
    .map((c) => h.indexOf(c));
  assert.ok(order.every((i) => i > 0), 'a part of the event is missing');
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'the parts are out of order');
  assert.match(h, /STOP LOSS/);
  assert.match(h, /@ 201\.25/);
  assert.match(h, /11:22:33 AM ET/);
});

test('an exit shows its P&L - profit green, loss red - and an entry shows none', () => {
  assert.match(strip([ev('TARGET', { profit: 35 })]), /class="nst-event-pl up">\+\$35\.00</);
  assert.match(strip([ev('STOP', { profit: -23.4 })]), /class="nst-event-pl down">-\$23\.40</);
  assert.doesNotMatch(strip([ev('BUY')]), /nst-event-pl/, 'an entry must not show a P&L');
  // a flat exit is neither a profit nor a loss - no colour, as on the rest of the page
  assert.match(strip([ev('SCORE_EXIT', { profit: 0 })]), /class="nst-event-pl ">\$0\.00</);
});

test('only an event inside its blink window blinks; afterwards it stays, still', () => {
  assert.match(strip([ev('BUY')], ['9:BUY']), /nst-event up is-blink/);
  const settled = strip([ev('BUY')]);
  assert.doesNotMatch(settled, /is-blink/);
  assert.match(settled, /AMZN/, 'the event must remain visible after the blink ends');
});

test('with no event yet the strip is still there, and says so honestly', () => {
  const h = strip([]);
  assert.match(h, /data-testid="nst-events"/);
  assert.match(h, /No trade event yet/);
});

test('the page renders the strip above the table, and a model name never reaches it (A.26)', () => {
  (globalThis as any).__apiState = { strategytrades: { data: {
    status: 'OK', count: 1, open: 1, closed: 0, wins: 0, total_profit: 0, realized_on_date: 0,
    date: '2026-11-24', strategies: [], rules: {}, rows: [row()],
  } } };
  const h = renderToStaticMarkup(
    <MemoryRouter initialEntries={['/strategy-trades']}>
      <NewStrategyTradesPage ctx={{ isAdmin: false } as any} />
    </MemoryRouter>);
  assert.ok(h.indexOf('data-testid="nst-events"') > 0, 'the strip is missing from the page');
  assert.ok(h.indexOf('data-testid="nst-events"') < h.indexOf('nst-table'), 'the strip must sit above the table');
  assert.doesNotMatch(h, /Claude|ChatGPT|Gemini|Grok|Kimi/i);
});
