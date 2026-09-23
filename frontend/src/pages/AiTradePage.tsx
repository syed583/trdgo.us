import React, { useEffect, useState } from 'react';
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
          <h1>AI Trade</h1>
          <p className="at-basis">
            <b>{HORIZON_COPY[horizon].label} outlook.</b>{' '}
            {HORIZON_COPY[horizon].basis}
          </p>
        </div>
        <div className="at-head-right">
          <HorizonSwitch value={horizon} onChange={setPicked} session={session} />
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
          <button className="icon-btn" onClick={board.refresh}
            aria-label="Refresh" disabled={board.loading}>
            {board.loading
              ? <Loader2 size={15} className="spin" />
              : <RefreshCw size={15} />}
          </button>
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

          <div className="at-cols">
            <Column
              title="Top Buyers" icon={<TrendingUp size={15} />} tone="buy"
              rows={d.buyers}
              empty="Nothing in the universe cleared the bar on the long side." />
            <Column
              title="Top Sellers" icon={<TrendingDown size={15} />} tone="sell"
              rows={d.sellers}
              empty="Nothing in the universe cleared the bar on the short side." />
          </div>

          <div className="at-cols">
            <Panel title="No edge either way"
              right={<span className="at-chip">{d.waiting.length}</span>}>
              {d.waiting.length === 0
                ? <p className="at-note">Every scored name leaned one way.</p>
                : (
                  <div className="at-mini">
                    {d.waiting.map((r) => <MiniRow key={r.symbol} row={r} />)}
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
                    {d.held.map((r) => <MiniRow key={r.symbol} row={r} reason />)}
                  </div>
                )}
            </Panel>
          </div>

          <Scorecard horizon={horizon} />

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

function Column({
  title, icon, tone, rows, empty,
}: {
  title: string; icon: React.ReactNode; tone: string;
  rows: Row[]; empty: string;
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
              <BigRow key={r.symbol} row={r} rank={i + 1} tone={tone} />
            ))}
          </div>
        )}
    </Panel>
  );
}

function BigRow({
  row, rank, tone,
}: { row: Row; rank: number; tone: string }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const lean = row.lean ?? 0;
  // Distance from neutral, against the widest reading the model produces in
  // practice, so two rows are comparable to each other rather than each being
  // scaled to itself.
  const width = Math.min(100, (Math.abs(lean) / 35) * 100);

  return (
    <>
      {/* A click opens why this name is on the list, read from the stored
          call; the analysis is one further click from there. Going straight
          to a fresh analysis answered a different question -- what the model
          thinks now -- not why it put this row here. */}
      <button
        className={`at-row ${tone} ${open ? 'at-open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Why this call"
      >
        <span className="at-rank">{rank}</span>
        <span className="at-sym">{row.symbol}</span>
        <span className="at-decision">{row.decision}</span>

        <span className="at-bar"><i style={{ width: `${width}%` }} /></span>

        {/* The direction score, rounded exactly as the analysis screen's ring
            rounds it, so the two screens cannot appear to disagree. */}
        <span className="at-score"
          title={`${lean > 0 ? '+' : ''}${num(lean)} from neutral`}>
          {row.direction_score == null ? '--' : Math.round(row.direction_score)}
        </span>

        <span className="at-meta">
          <em title="How sure the model is of this reading">
            Conf {Math.round(row.confidence ?? 0)}%
          </em>
          <em title="How much of the model leans the same way">
            Agree {Math.round(row.agreement_pct ?? 0)}%
          </em>
          <em title="How long ago this name was scored">{ago(row.age_seconds)}</em>
        </span>

        <ArrowRight size={14} className={`at-go ${open ? 'at-go-open' : ''}`} />
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

function MiniRow({ row, reason }: { row: Row; reason?: boolean }) {
  const navigate = useNavigate();
  return (
    <button className="at-mini-row"
      onClick={() => navigate(`/ai-insights/${row.symbol}?run=1`)}>
      <b>{row.symbol}</b>
      <span className="at-mini-call">
        {reason ? <PauseCircle size={12} /> : null}
        {row.decision}
      </span>
      <em title={`${(row.lean ?? 0) > 0 ? '+' : ''}${num(row.lean)} from neutral`}>
        {row.direction_score == null ? '--' : Math.round(row.direction_score)}
      </em>
      {reason && row.blocked_reasons[0] ? <i>{row.blocked_reasons[0]}</i> : null}
    </button>
  );
}
