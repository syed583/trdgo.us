import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight, Clock, Loader2, PauseCircle, RefreshCw, ShieldAlert,
  TrendingDown, TrendingUp,
} from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num } from '../lib/format';
import WhyCall from '../components/WhyCall';
import Scorecard from '../components/Scorecard';
import HorizonSwitch, { HORIZON_COPY, defaultHorizon } from '../components/HorizonSwitch';
import type { Horizon } from '../components/HorizonSwitch';

const EMPTY: Row[] = [];

/** Which side a decision reads as, so a card is coloured by its CURRENT call
 *  even after it has been pinned to a column. */
function toneOf(decision: string): 'buy' | 'sell' | 'neutral' {
  const d = (decision || '').toUpperCase();
  if (d.includes('BUY')) return 'buy';
  if (d.includes('SELL')) return 'sell';
  return 'neutral';
}

const SIDE_LABEL: Record<'buy' | 'sell' | 'neutral', string> = {
  buy: 'BUY', sell: 'SELL', neutral: 'NEUTRAL',
};

/** Today's date in US market time, as the key a day's pinned list resets on. */
function etDay(): string {
  try {
    return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

type Side = 'buy' | 'sell' | 'neutral';
interface Change { symbol: string; from: Side; to: Side; }

interface PinStore {
  day: string;
  buy: string[];
  sell: string[];
  rows: Record<string, Row>;
  changes: Change[];
}

/**
 * Pin the day's Buy / Sell lists. Once a name shows up on a side it keeps its
 * slot for the rest of the trading day -- it does not drop out when its score
 * dips below the bar or its call flips; its figures and colour just update in
 * place (and the change flag lights it up). The lists reset to fresh names on
 * the next trading day, and persist across reloads within the day via
 * localStorage, keyed by horizon. Returns the ordered buyers/sellers plus the
 * set of names whose call changed since the last refresh.
 */
function useDailyPinned(d: Board | null, horizon: string): {
  buyers: Row[]; sellers: Row[]; changeMap: Map<string, Change>; changes: Change[];
} {
  const key = `at-pinned:${horizon}`;
  const storeRef = useRef<PinStore | null>(null);
  const keyRef = useRef<string>('');

  return useMemo(() => {
    const day = etDay();
    // The ref is per-horizon: when the horizon (key) changes, reload from that
    // horizon's own storage rather than carrying the previous one over and
    // writing it to the new key.
    let store = keyRef.current === key ? storeRef.current : null;
    if (!store) {
      try {
        const raw = localStorage.getItem(key);
        if (raw) store = JSON.parse(raw) as PinStore;
      } catch { /* ignore */ }
    }
    if (store && store.day !== day) store = { day, buy: [], sell: [], rows: {}, changes: [] };
    if (!store) store = { day, buy: [], sell: [], rows: {}, changes: [] };
    if (!store.changes) store.changes = [];

    if (d) {
      const all = [
        ...(d.buyers ?? EMPTY), ...(d.sellers ?? EMPTY),
        ...(d.waiting ?? EMPTY), ...(d.held ?? EMPTY),
      ];
      const allMap = new Map(all.map((r) => [r.symbol, r]));
      const pinned = new Set([...store.buy, ...store.sell]);

      // A name joins the side it FIRST appears on, and stays there.
      for (const r of (d.buyers ?? EMPTY)) {
        if (!pinned.has(r.symbol)) { store.buy.push(r.symbol); pinned.add(r.symbol); }
      }
      for (const r of (d.sellers ?? EMPTY)) {
        if (!pinned.has(r.symbol)) { store.sell.push(r.symbol); pinned.add(r.symbol); }
      }
      // Refresh each pinned name, and record a change when its SIDE flips
      // (green/yellow/red), persisted for the day so the banner accumulates.
      for (const sym of pinned) {
        const fresh = allMap.get(sym);
        if (!fresh) continue;
        const old = store.rows[sym];
        if (old) {
          const from = rowTone(old);
          const to = rowTone(fresh);
          if (from !== to) {
            store.changes = store.changes.filter((c) => c.symbol !== sym);
            store.changes.push({ symbol: sym, from, to });
          }
        }
        // Carry forward the entry (the price and call when the name first
        // appeared on its side), or set it the first time a price is known.
        if (old?.entry_price != null) {
          fresh.entry_price = old.entry_price;
          fresh.entry_decision = old.entry_decision;
          fresh.entry_at = old.entry_at;
        } else if (fresh.spot != null) {
          fresh.entry_price = fresh.spot;
          fresh.entry_decision = store.buy.includes(sym) ? 'BUY' : 'SELL';
          fresh.entry_at = Date.now();
        }
        store.rows[sym] = fresh;
      }
    }

    storeRef.current = store;
    keyRef.current = key;
    try { localStorage.setItem(key, JSON.stringify(store)); } catch { /* ignore */ }

    const buyers = store.buy.map((s) => store!.rows[s]).filter(Boolean);
    const sellers = store.sell.map((s) => store!.rows[s]).filter(Boolean);
    const changes = store.changes.slice().reverse();     // most recent first
    const changeMap = new Map(changes.map((c) => [c.symbol, c]));
    return { buyers, sellers, changeMap, changes };
  }, [d, key]);
}

interface Row {
  symbol: string;
  horizon?: string;
  decision: string;
  blocked: boolean;
  blocked_reasons: string[];
  direction_score: number | null;
  lean: number | null;
  age_seconds?: number;
  confidence: number | null;
  agreement_pct: number | null;
  coverage_pct: number | null;
  top_reasons: string[];
  target?: number | null;
  stop?: number | null;
  spot?: number | null;
  // Intraday dual-score model (Trdgo Stock + Tradgo Call only).
  buy_score?: number | null;
  sell_score?: number | null;
  im_decision?: string | null;
  im_side?: 'buy' | 'sell' | 'none' | null;
  im_color?: 'green' | 'yellow' | 'none' | null;
  im_full_size?: boolean | null;
  // Where the call was first given today (set client-side on first appearance).
  entry_price?: number | null;
  entry_decision?: string | null;
  entry_at?: number | null;
}

/** The side a row reads as, preferring the intraday model's call. */
function rowTone(row: Row): 'buy' | 'sell' | 'neutral' {
  if (row.im_side === 'buy') return 'buy';
  if (row.im_side === 'sell') return 'sell';
  if (row.im_side === 'none') return 'neutral';
  return toneOf(row.decision);
}

/** The call text to show -- the intraday signal state when present. */
function rowDecision(row: Row): string {
  return row.im_decision || row.decision;
}

interface Board {
  status: string;
  buyers: Row[];
  sellers: Row[];
  waiting: Row[];
  held: Row[];
  universe: number;
  scored: number;
  pending: string[];
  buy_count: number;
  sell_count: number;
  held_count: number;
  elapsed_seconds: number;
  note: string;
  building?: boolean;
  youngest_seconds: number | null;
  oldest_seconds: number | null;
}

/**
 * AI Trade: the same model the analysis screen runs, over a universe.
 *
 * The two columns are the whole point -- what the model likes and what it
 * dislikes, strongest conviction first. Everything else on the page exists to
 * stop those columns being read as more than they are: how much of the
 * universe was actually scored, and which names the model refused to call.
 *
 * Nothing here suggests size, entry or exit, and there is no order control on
 * the page. It ranks readings; acting on one is done elsewhere, by a person.
 */
export default function AiTradePage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const [tick, setTick] = useState(0);

  // Which outlook is on screen. It starts from the market clock -- today
  // during the session, tomorrow otherwise -- and stays wherever the reader
  // puts it; the clock only picks the starting point.
  const clock = useApi<any>((s) => api.status(s), []);
  const session = clock.data?.market?.session as string | undefined;
  const [picked, setPicked] = useState<Horizon | null>(null);
  const horizon: Horizon = picked ?? defaultHorizon(session);

  // Once a minute. The ranking is composed from scores the background scan
  // keeps updating, so a poll is a sort on the server rather than a scan --
  // cheap enough to ask this often, and it means a name that flips side shows
  // up within the minute.
  const board = useApi<Board>((s) => api2.aiTradeBoard(horizon, s), [tick, horizon], {
    refreshMs: 60000,
  });
  const d = board.data;
  const building = !!d?.building;

  // Hold each card's position steady across the minute-ly re-sort, so the list
  // does not shuffle under the reader while they are looking at it.
  // Pin the day's Buy / Sell names so a card stays put once it appears, resets
  // next trading day, and lights up when its call changes.
  const { buyers, sellers, changeMap, changes } = useDailyPinned(d ?? null, horizon);

  // While a build is running the answer changes every few seconds, so poll
  // until it settles rather than leaving the page on whatever it first saw.
  useEffect(() => {
    if (!building) return undefined;
    const t = setTimeout(() => setTick((n) => n + 1), 8000);
    return () => clearTimeout(t);
  }, [building, tick]);

  return (
    <div className="page">
      <div className="at-head">
        <div>
          <h1>Trdgo Stock</h1>
          <p className="at-basis">
            <b>{HORIZON_COPY[horizon].label} outlook.</b>{' '}
            {HORIZON_COPY[horizon].basis}
          </p>
        </div>
        <div className="at-head-right">
          {/* The refresh sits with the horizon tabs it reloads, not pushed to
              the far edge by the coverage note, so the control and the data it
              affects read as one group. */}
          <div className="at-tabgroup">
            <HorizonSwitch value={horizon} onChange={setPicked} session={session} />
            <button className="icon-btn" onClick={board.refresh}
              aria-label="Refresh" disabled={board.loading}>
              {board.loading
                ? <Loader2 size={15} className="spin" />
                : <RefreshCw size={15} />}
            </button>
          </div>
          {d && (
            <span className="at-cover">
              {d.scored} of {d.universe} scored
              {d.pending.length > 0 ? ` - ${d.pending.length} still loading` : ''}
              {/* Re-fetching every minute is not the same as being a minute
                  old, and the difference is the whole value of the number. */}
              {d.oldest_seconds != null
                ? ` - scores ${ago(d.youngest_seconds)}-${ago(d.oldest_seconds)} old`
                : ''}
            </span>
          )}
        </div>
      </div>

      {/* Only while there is nothing to show. The scan runs continuously, so
          "building" is true most of the time; showing the banner on it made
          the page flash every pass. */}
      {(board.initialLoading || (building && (!d || d.scored === 0))) && (
        <div className="at-wait">
          <Loader2 size={18} className="spin" />
          <div>
            <b>
              {d && d.scored > 0
                ? `Scoring the universe... ${d.scored} of ${d.universe} so far`
                : 'Scoring the universe...'}
            </b>
            <span>
              {d?.note || (
                'Each name wakes a dozen providers, so the first build takes '
                + 'a couple of minutes. It is cached once done and refreshed '
                + 'in the background, so this wait is the first visit rather '
                + 'than every visit.'
              )}
              {d?.status === 'STALE'
                ? ' Showing the last completed board until this one finishes.'
                : ''}
            </span>
          </div>
        </div>
      )}

      {d?.status === 'SESSION_CLOSED' && (
        <div className="at-wait">
          <Clock size={18} />
          <div>
            <b>No Today board right now</b>
            <span>{d.note}</span>
            <span style={{ marginTop: 8, display: 'flex', gap: 8 }}>
              <button className="why-ai-btn" onClick={() => setPicked('TOMORROW')}>
                Show Tomorrow
              </button>
              <button className="why-ai-btn" onClick={() => setPicked('SWING')}>
                Show Swing
              </button>
            </span>
          </div>
        </div>
      )}

      {d && d.status !== 'BUILDING' && d.status !== 'SESSION_CLOSED' && (
        <>
          <div className="at-stats">
            <Stat label="Buy-rated" value={d.buy_count} tone="buy"
              sub="model calls a long side" />
            <Stat label="Sell-rated" value={d.sell_count} tone="sell"
              sub="model calls a short side" />
            <Stat label="No edge" value={d.waiting.length} tone="wait"
              sub="scored, but neither way" />
            <Stat label="Withheld" value={d.held_count} tone="held"
              sub="model declined to call" />
          </div>

          {/* The scorecard is the model's track record -- the thing that says
              whether any of this is worth trusting -- so it sits up here with
              the summary, not buried under the full withheld list. */}
          <Scorecard horizon={horizon} />

          <ChangedBanner changes={changes} />

          <div className="at-cols">
            <Column
              title="Top Buyers" icon={<TrendingUp size={15} />} tone="buy"
              rows={buyers} changeMap={changeMap}
              empty="Nothing in the universe cleared the bar on the long side." />
            <Column
              title="Top Sellers" icon={<TrendingDown size={15} />} tone="sell"
              rows={sellers} changeMap={changeMap}
              empty="Nothing in the universe cleared the bar on the short side." />
          </div>

          <div className="at-cols">
            <Panel title="No edge either way"
              right={<span className="at-chip">{d.waiting.length}</span>}>
              {d.waiting.length === 0
                ? <p className="at-note">Every scored name leaned one way.</p>
                : (
                  <div className="at-mini">
                    {d.waiting.map((r) => <MiniRow key={r.symbol} row={r} changed={changeMap.has(r.symbol)} />)}
                  </div>
                )}
            </Panel>

            <Panel title="Withheld by the model"
              right={<span className="at-chip">{d.held_count}</span>}>
              <p className="at-note">
                <ShieldAlert size={13} /> These returned a number, but not one
                the model trusts. They are held out of both columns rather
                than ranked low: a weak reading and a bearish one are
                different things.
              </p>
              {d.held.length === 0
                ? <p className="at-note">Nothing withheld.</p>
                : (
                  <div className="at-mini">
                    {d.held.map((r) => <MiniRow key={r.symbol} row={r} reason changed={changeMap.has(r.symbol)} />)}
                  </div>
                )}
            </Panel>
          </div>

          <PastSessions />

          <p className="at-foot">
            {d.note} Built in {d.elapsed_seconds}s and cached.
            {d.pending.length > 0
              ? ` Still waiting on ${d.pending.slice(0, 8).join(', ')}${
                d.pending.length > 8 ? ` +${d.pending.length - 8} more` : ''}.`
              : ''}
          </p>
        </>
      )}

      {board.error && !d && (
        <div className="at-wait">Could not build the board: {board.error}</div>
      )}
    </div>
  );
}

/** Compact age: seconds under a minute, then whole minutes. */
function ago(seconds: number | null | undefined): string {
  if (seconds == null) return '--';
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  return `${Math.round(seconds / 60)}m`;
}

function Stat({
  label, value, sub, tone,
}: { label: string; value: number; sub: string; tone: string }) {
  return (
    <div className={`at-stat ${tone}`}>
      <span className="at-stat-label">{label}</span>
      <b>{value}</b>
      <span className="at-stat-sub">{sub}</span>
    </div>
  );
}

/** A short date label like "Mon Oct 06" from a YYYY-MM-DD day key. */
function dayLabel(day: string): string {
  try {
    return new Date(day + 'T12:00:00').toLocaleDateString(undefined,
      { weekday: 'short', month: 'short', day: '2-digit' });
  } catch { return day; }
}

interface PastChip { symbol: string; score: number | null; }
interface PastDay { day: string; buy: PastChip[]; sell: PastChip[]; }

/** History of earlier trading days' Buy / Sell calls, read from the SAME stored
 *  daily snapshots the Signal History page uses -- so it is the real record, not
 *  whatever this browser happened to see. Newest day first, capped per side. */
function PastSessions() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);
  const hist = useApi<any>((s) => api2.signalHistory(10, '', s), [], { refreshMs: 600_000 });

  const days: PastDay[] = useMemo(() => {
    const data = hist.data;
    if (!data || data.status !== 'OK') return [];
    const today = etDay();
    const dates: string[] = (data.dates || []).filter((dt: string) => dt < today);
    const rows: any[] = data.rows || [];
    return dates.slice().reverse().map((date) => {
      const buy: PastChip[] = [];
      const sell: PastChip[] = [];
      for (const r of rows) {
        const cell = (r.cells || []).find((c: any) => c.date === date);
        const call = cell?.stock;
        if (call === 'BUY') buy.push({ symbol: r.symbol, score: cell.score ?? null });
        else if (call === 'SELL') sell.push({ symbol: r.symbol, score: cell.score ?? null });
      }
      buy.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));       // strongest buy first
      sell.sort((a, b) => (a.score ?? 0) - (b.score ?? 0));      // strongest sell first
      return { day: date, buy: buy.slice(0, 15), sell: sell.slice(0, 15) };
    }).filter((s) => s.buy.length || s.sell.length);
  }, [hist.data]);

  if (days.length === 0) return null;

  const chip = (c: PastChip, tone: 'buy' | 'sell') => (
    <button key={c.symbol} className={`at-pastchip ${tone}`}
      onClick={() => navigate(`/trade-plan/${c.symbol}`)} title={c.symbol}>
      <b>{c.symbol}</b>{c.score != null && <em>{Math.round(c.score)}</em>}
    </button>
  );

  return (
    <Panel
      title={<span className="at-col-title">Previous sessions</span>}
      right={(
        <button className="at-chip at-past-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide' : `${days.length} day${days.length === 1 ? '' : 's'}`}
        </button>
      )}
    >
      {open && (
        <div className="at-past">
          {days.map((snap) => (
            <div key={snap.day} className="at-past-day">
              <div className="at-past-date">{dayLabel(snap.day)}</div>
              <div className="at-past-lists">
                <div className="at-past-side">
                  <span className="at-past-h buy">Buyers</span>
                  <div className="at-past-chips">
                    {snap.buy.length ? snap.buy.map((c) => chip(c, 'buy'))
                      : <span className="at-past-none">—</span>}
                  </div>
                </div>
                <div className="at-past-side">
                  <span className="at-past-h sell">Sellers</span>
                  <div className="at-past-chips">
                    {snap.sell.length ? snap.sell.map((c) => chip(c, 'sell'))
                      : <span className="at-past-none">—</span>}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/** The "N calls changed" notice, mirroring the earnings-trade banner: a pink
 *  box listing each name's OLD -> NEW side. */
function ChangedBanner({ changes }: { changes: Change[] }) {
  const [showAll, setShowAll] = useState(false);
  if (changes.length === 0) return null;
  const shown = showAll ? changes : changes.slice(0, 12);
  const more = changes.length - shown.length;
  return (
    <div className="at-chgnote">
      <div className="at-chgnote-top">
        <ShieldAlert size={13} />
        <span className="at-chgnote-h">{changes.length} call{changes.length === 1 ? '' : 's'} changed today</span>
      </div>
      <div className="at-chgnote-list">
        {shown.map((c) => (
          <span key={c.symbol} className={`at-chgchip to-${c.to}`}>
            <b>{c.symbol}</b> {SIDE_LABEL[c.from]}→{SIDE_LABEL[c.to]}
          </span>
        ))}
        {more > 0 && (
          <button className="at-chgmore" onClick={() => setShowAll(true)}>+{more} more</button>
        )}
      </div>
    </div>
  );
}

function Column({
  title, icon, tone, rows, empty, changeMap,
}: {
  title: string; icon: React.ReactNode; tone: string;
  rows: Row[]; empty: string; changeMap: Map<string, Change>;
}) {
  return (
    <Panel
      title={<span className={`at-col-title ${tone}`}>{icon} {title}</span>}
      right={<span className="at-chip">{rows.length}</span>}
      noBody
    >
      {rows.length === 0
        ? <p className="at-note at-empty">{empty}</p>
        : (
          <div className="at-rows">
            {rows.map((r, i) => (
              <BigRow key={r.symbol} row={r} rank={i + 1} tone={tone}
                change={changeMap.get(r.symbol)} />
            ))}
          </div>
        )}
    </Panel>
  );
}

function BigRow({
  row, rank, change,
}: { row: Row; rank: number; tone: string; change?: Change }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const lean = row.lean ?? 0;
  // Colour the card by DIRECTION so buy / sell / neutral read at a glance:
  // green = buy, red = sell, grey = no trade. The signal STATE (Strong /
  // Active / Weakening / No Trade) is carried by the badge text.
  const stateClass = rowTone(row);
  // The headline number is the intraday score for this name's side; for a
  // No-Trade name it is the stronger of the two sides. It is never the old
  // directional score (that belongs to the other model) -- that mismatch is
  // what showed "NO TRADE" next to an 82.
  const sideScore = row.im_side === 'sell' ? row.sell_score
    : row.im_side === 'buy' ? row.buy_score
    : (row.buy_score != null || row.sell_score != null)
      ? Math.max(row.buy_score ?? 0, row.sell_score ?? 0)
      : row.direction_score;
  return (
    <>
      {/* A click opens why this name is on the list, read from the stored call.
          The card is coloured like the earnings cards, by its current call. */}
      <button
        className={`at-card ${stateClass} ${open ? 'at-open' : ''} ${change ? 'at-changed' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={change ? `${SIDE_LABEL[change.from]} → ${SIDE_LABEL[change.to]}` : 'Why this call'}
      >
        <span className="at-rank">{rank}</span>

        <div className="at-card-body">
          <div className="at-card-head">
            <span className="at-sym">{row.symbol}</span>
            <span className="at-decision">{rowDecision(row)}</span>
            {change && <span className="at-why-pill">why?</span>}
          </div>
          <div className="at-meta">
            {row.buy_score != null && (
              <em className="im-buy" title="Intraday BUY score (0-100)">BUY {Math.round(row.buy_score)}</em>
            )}
            {row.sell_score != null && (
              <em className="im-sell" title="Intraday SELL score (0-100)">SELL {Math.round(row.sell_score)}</em>
            )}
            <em title="How long ago this name was scored">{ago(row.age_seconds)}</em>
          </div>
          {(row.spot != null || row.target != null || row.stop != null) && (
            <div className="at-levels">
              {row.spot != null && (
                <span className="at-lvl now" title="Current price">
                  Price ${row.spot}
                </span>
              )}
              {row.target != null && (
                <span className="at-lvl tgt" title="Exit target (take profit)">
                  Target ${row.target}
                </span>
              )}
              {row.stop != null && (
                <span className="at-lvl stp" title="Stop loss">
                  Stop ${row.stop}
                </span>
              )}
            </div>
          )}
          {row.entry_price != null && (
            <div className="at-entry" title="The call and price when this name first appeared today">
              Called <b className={(row.entry_decision || '').includes('SELL') ? 'neg' : 'pos'}>
                {row.entry_decision}</b> @ ${row.entry_price}
              {row.entry_at && <em> · {new Date(row.entry_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</em>}
              {((row.entry_decision || '').includes('SELL') ? 'sell' : 'buy') !== stateClass && (
                <span className="at-entry-now"> → now {rowDecision(row)}</span>
              )}
            </div>
          )}
        </div>

        {/* The direction score, rounded exactly as the analysis screen's ring
            rounds it, so the two screens cannot appear to disagree. */}
        <span className="at-score"
          title={`${lean > 0 ? '+' : ''}${num(lean)} from neutral`}>
          {sideScore == null ? '--' : Math.round(sideScore)}
        </span>

        <ArrowRight size={15} className={`at-go ${open ? 'at-go-open' : ''}`} />
      </button>

      {open && (
        <div className="at-why">
          <RowWhy symbol={row.symbol} horizon={row.horizon} />
          <button className="at-why-open"
            onClick={() => navigate(
              `/ai-insights/${row.symbol}?run=1&horizon=${row.horizon || 'SWING'}`)}>
            Run full analysis on {row.symbol} <ArrowRight size={12} />
          </button>
        </div>
      )}
    </>
  );
}

function RowWhy({ symbol, horizon }: { symbol: string; horizon?: string }) {
  const call = useApi<any>((s) => api2.callLatest(symbol, horizon, s), [symbol, horizon]);
  if (call.loading && !call.data) {
    return <p className="at-note"><Loader2 size={12} className="spin" /> Loading the stored callâ€¦</p>;
  }
  if (!call.data?.call) {
    return (
      <p className="at-note">
        No stored call for {symbol} yet. Calls are saved as the board scores
        each name, so this fills in on its next pass.
      </p>
    );
  }
  return <WhyCall call={call.data.call} compact />;
}

function MiniRow({ row, reason, changed }: { row: Row; reason?: boolean; changed?: boolean }) {
  const navigate = useNavigate();
  return (
    <button className={`at-mini-row ${changed ? 'at-changed' : ''}`}
      onClick={() => navigate(`/ai-insights/${row.symbol}?run=1`)}
      title={changed ? 'Call just changed' : undefined}>
      <b>{row.symbol}</b>
      <span className="at-mini-call">
        {reason ? <PauseCircle size={12} /> : null}
        {rowDecision(row)}
        {changed && <span className="at-changed-tag">changed</span>}
      </span>
      <em title={`${(row.lean ?? 0) > 0 ? '+' : ''}${num(row.lean)} from neutral`}>
        {row.direction_score == null ? '--' : Math.round(row.direction_score)}
      </em>
      {reason && row.blocked_reasons[0] ? <i>{row.blocked_reasons[0]}</i> : null}
    </button>
  );
}
