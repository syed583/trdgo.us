import React from 'react';
import { Radio } from 'lucide-react';

/**
 * The live event strip on Strategy Trades (kit T).
 *
 * The classic page pinned and blinked each new trade event at the top; the redesign lost that and
 * left only the spoken call-out. This brings the strip back, built on the SAME rule the classic
 * page and the voice alert already use - compare each read of /api/strategy-trades with the one
 * before it:
 *
 *   a trade id not seen before, status OPEN          -> BUY or SELL
 *   a trade that was OPEN and is now closed          -> STOP / TARGET / SCORE_EXIT / CLOSED
 *
 * EVERY EVENT IS A REAL TRADE ROW. Nothing is synthesised, and nothing is shown for the first
 * read: that read only seeds the baseline, otherwise opening the page would "announce" every
 * trade of the day as though it had just happened.
 *
 * ABOUT THE TIME SHOWN. An entry carries the server's own entry_time (ET). An exit does NOT - the
 * payload has the exit date but no exit time of day - so an exit shows the ET time this page SAW
 * the status change, which trails the real exit by up to one refresh (15s). It is labelled "seen"
 * so it is never read as the fill time.
 */

export type TradeEventKind = 'BUY' | 'SELL' | 'STOP' | 'TARGET' | 'SCORE_EXIT' | 'CLOSED';

export type TradeEvent = {
  /** `${tradeId}:${kind}` - the identity that makes a second blink for the same event impossible. */
  key: string;
  id: number;
  kind: TradeEventKind;
  symbol: string;
  price: number | null;
  /** ET, already formatted. */
  time: string;
  /** true when `time` is when this page noticed, not when the server stamped it. */
  timeIsSeen: boolean;
  /** exits only */
  profit: number | null;
  /** "Agent 2" for a user - whatever the server sent, never resolved here (A.26). */
  model: string;
};

/** Every status that means the trade is no longer open. Mirrors the classic page's CLOSED set. */
export const CLOSED_STATUSES = new Set([
  'STOP', 'TARGET', 'TIMED_EXIT', 'SCORE_EXIT', 'NO_PRICE', 'CLEANUP', 'EOD_CLOSE', 'MARKET_CLOSE',
]);

const EXIT_KIND: Record<string, TradeEventKind> = {
  STOP: 'STOP', TARGET: 'TARGET', SCORE_EXIT: 'SCORE_EXIT',
};

export const EVENT_LABEL: Record<TradeEventKind, string> = {
  BUY: 'BUY', SELL: 'SELL', STOP: 'STOP LOSS', TARGET: 'TARGET', SCORE_EXIT: 'SCORE EXIT', CLOSED: 'CLOSED',
};

/** green / red / amber / neutral - the colour family, which the stylesheet turns into a colour. */
export const EVENT_TONE: Record<TradeEventKind, 'up' | 'down' | 'warn' | 'flat'> = {
  BUY: 'up', SELL: 'down', STOP: 'down', TARGET: 'up', SCORE_EXIT: 'warn', CLOSED: 'flat',
};

/** The current time of day in New York, e.g. "10:42:07 AM". */
export function etClock(now: Date = new Date()): string {
  try {
    return now.toLocaleTimeString('en-US', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return now.toLocaleTimeString('en-US');
  }
}

/**
 * What happened between two reads of the blotter.
 *
 * `seen` is the id -> status map from the previous read and IS UPDATED IN PLACE, so calling this
 * twice with the same rows returns events the first time and nothing the second. That is the
 * whole "no duplicate blink on refresh" guarantee: an event exists only at the moment a status
 * differs from the one remembered.
 *
 * `seeded` false means this is the first read: the map is filled and no event is returned.
 */
export function diffTradeEvents(
  seen: Map<number, string>,
  rows: any[],
  seeded: boolean,
  nowEt: string = etClock(),
): TradeEvent[] {
  const out: TradeEvent[] = [];
  for (const r of rows || []) {
    if (!r || r.id == null) continue;
    const prev = seen.get(r.id);
    const status = String(r.status || '');
    if (seeded) {
      const base = { id: r.id, symbol: String(r.symbol || ''), model: String(r.strategy_label || r.strategy || '') };
      if (prev === undefined) {
        if (status === 'OPEN') {
          const kind: TradeEventKind = r.side === 'SHORT' ? 'SELL' : 'BUY';
          out.push({
            ...base, key: `${r.id}:${kind}`, kind,
            price: r.entry_price ?? null,
            time: r.entry_time ? `${r.entry_time}` : nowEt, timeIsSeen: !r.entry_time,
            profit: null,
          });
        }
      } else if (prev === 'OPEN' && CLOSED_STATUSES.has(status)) {
        const kind: TradeEventKind = EXIT_KIND[status] || 'CLOSED';
        out.push({
          ...base, key: `${r.id}:${kind}`, kind,
          price: r.exit_price ?? r.last_price ?? null,
          time: nowEt, timeIsSeen: true,
          profit: r.profit ?? null,
        });
      }
    }
    seen.set(r.id, status);
  }
  return out;
}

/** Newest first, at most `limit`, and never the same event twice. */
export function mergeEvents(current: TradeEvent[], incoming: TradeEvent[], limit = 10): TradeEvent[] {
  if (!incoming.length) return current;
  const have = new Set(current.map((e) => e.key));
  const fresh = incoming.filter((e) => !have.has(e.key));
  if (!fresh.length) return current;
  return [...fresh.reverse(), ...current].slice(0, limit);
}

const px = (v: number | null) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
const pnl = (v: number | null) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const x = Number(v);
  return `${x < 0 ? '-' : x > 0 ? '+' : ''}$${Math.abs(x).toFixed(2)}`;
};
const pnlDir = (v: number | null) => (v == null || Number(v) === 0 ? '' : Number(v) > 0 ? 'up' : 'down');

export default function StrategyEventStrip({
  events, blinking,
}: {
  events: TradeEvent[];
  /** keys of the events still inside their blink window */
  blinking: Set<string>;
}) {
  return (
    <section className={`nst-events${events.length ? '' : ' is-empty'}`} aria-label="Live trade events"
      aria-live="polite" data-testid="nst-events">
      <span className="nst-events-h">
        <Radio size={14} aria-hidden="true" /> LIVE
      </span>
      {events.length === 0 ? (
        <span className="nst-events-none">
          No trade event yet since this page was opened. New entries and exits appear here as they happen.
        </span>
      ) : (
        <ol className="nst-events-list">
          {events.map((e) => {
            const tone = EVENT_TONE[e.kind];
            const exit = e.kind !== 'BUY' && e.kind !== 'SELL';
            return (
              <li key={e.key} data-kind={e.kind}
                className={`nst-event ${tone}${blinking.has(e.key) ? ' is-blink' : ''}`}>
                {/* the SYMBOL first: it is what the eye is looking for */}
                <b className="nst-event-sym">{e.symbol}</b>
                <span className={`nst-event-tag ${tone}`}>{EVENT_LABEL[e.kind]}</span>
                <span className="nst-event-px">@ {px(e.price)}</span>
                <span className="nst-event-at" title={e.timeIsSeen
                  ? 'The time this page saw the change - up to one refresh after the exit itself'
                  : 'Entry time, from the server'}>
                  {e.time} ET{e.timeIsSeen ? ' · seen' : ''}
                </span>
                {exit && <span className={`nst-event-pl ${pnlDir(e.profit)}`}>{pnl(e.profit)}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
