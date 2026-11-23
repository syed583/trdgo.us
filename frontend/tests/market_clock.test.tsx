import test from 'node:test';
import assert from 'node:assert/strict';
import { setMarketOpen, pollScale, isMarketOpen, CLOSED_POLL_SCALE } from '../src/hooks/marketClock';

/**
 * Kit S: polling slows down when the market is shut.
 *
 * The rule that matters is the FAIL-SAFE one. Getting this wrong in the cautious direction costs
 * a few requests; getting it wrong the other way shows someone a stale price and calls it live.
 * So anything other than a confirmed CLOSED runs at full rate.
 *
 * This is also the test that would have caught my first measurement being meaningless: the fixture
 * reported no market session at all, so the back-off correctly never engaged and the before/after
 * came out identical. The logic was right; the probe was blind.
 */

test('a confirmed CLOSED market slows polling down', () => {
  setMarketOpen(false);
  assert.equal(pollScale(), CLOSED_POLL_SCALE);
  assert.ok(CLOSED_POLL_SCALE > 1, 'the closed-market scale must actually slow something down');
});

test('an open market polls at full rate', () => {
  setMarketOpen(true);
  assert.equal(pollScale(), 1);
});

test('UNKNOWN market state polls at full rate - staleness is the worse failure', () => {
  setMarketOpen(null);
  assert.equal(isMarketOpen(), null);
  assert.equal(pollScale(), 1, 'an unknown market must never slow polling down');
});

test('pre-market and after-hours are not "closed": prices move, so the rate must not drop', () => {
  // The shell maps session -> boolean: only a literal CLOSED is false. This mirrors that mapping,
  // so a future change to it has to break this test rather than quietly halve the refresh rate
  // during the sessions the owner trades around.
  for (const session of ['OPEN', 'PRE_MARKET', 'AFTER_HOURS']) {
    setMarketOpen(session !== 'CLOSED');
    assert.equal(pollScale(), 1, `${session} must poll at full rate`);
  }
  setMarketOpen('CLOSED' !== 'CLOSED');
  assert.equal(pollScale(), CLOSED_POLL_SCALE);
});

test('the scale is sane: slow enough to matter, not so slow a page goes cold', () => {
  // 25s shell quote -> 2.5 min when shut; 60s indices -> 6 min. A page left open overnight must
  // not be showing something hours old when the bell rings.
  assert.ok(CLOSED_POLL_SCALE >= 3 && CLOSED_POLL_SCALE <= 10,
    `CLOSED_POLL_SCALE is ${CLOSED_POLL_SCALE}; outside 3-10 it is either pointless or stale`);
  setMarketOpen(false);
  assert.ok(25_000 * pollScale() <= 300_000, 'the quote must still refresh at least every 5 minutes');
});
