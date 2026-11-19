import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity, AlertTriangle, ArrowRight, CheckCircle2, Clock, Crosshair, Target, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import strategyHero from '../assets/strategy-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Strategy Trades in the redesigned shell (Figma frame 2:430).
 *
 * WHAT THE DESIGN ASKS FOR AND WE DO NOT HAVE. The frame's table has Setup (Intraday/Swing/Position),
 * Target 2, Trend, and filters for Market, Sector and Signal. The blotter has none of those: a trade
 * carries one target, no setup class and no sector. Per the owner's rule, what we cannot measure is
 * removed rather than invented - so those columns and filters are absent, not filled with plausible
 * values. Everything that IS shown comes from /api/strategy-trades.
 *
 * R:R is the one derived figure, and it is arithmetic on the trade's own levels
 * (|target-entry| / |entry-stop|), not an estimate.
 *
 * A.26: a non-administrator sees Agent 1-4, never which model a strategy is. The server already maps
 * this - `strategy_label` arrives as "Agent 2" for a user - so the page simply renders what it is given
 * and must never try to resolve a name itself.
 */

type Any = any;

const n2 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
const money = (v: Any): string => {
  const x = Number(v);
  if (v === null || v === undefined || Number.isNaN(x)) return '—';
  return `${x < 0 ? '-' : ''}$${Math.abs(x).toFixed(2)}`;
};
const pct = (v: Any): string =>
  (v === null || v === undefined ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
const dir = (v: Any): string => (v === null || v === undefined ? '' : Number(v) >= 0 ? 'up' : 'down');

/** |target − entry| / |entry − stop|, or null when a level is missing. Never a guess. */
function rr(row: Any): number | null {
  const e = Number(row.entry_price), s = Number(row.stop), t = Number(row.target);
  if (!Number.isFinite(e) || !Number.isFinite(s) || !Number.isFinite(t)) return null;
  const risk = Math.abs(e - s);
  if (risk < 1e-9) return null;
  return Math.abs(t - e) / risk;
}

const STATUS_TONE: Record<string, string> = {
  OPEN: 'open', TARGET: 'win', STOP: 'loss', SCORE_EXIT: 'flat', EOD_CLOSE: 'flat',
  CLEANUP: 'flat', NO_PRICE: 'flat', MARKET_CLOSE: 'flat', PENDING_RECON: 'flat',
};

export default function NewStrategyTradesPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [strategy, setStrategy] = React.useState('all');
  const [date, setDate] = React.useState('');
  const [find, setFind] = React.useState('');
  const [view, setView] = React.useState<'all' | 'open' | 'closed'>('all');

  const q = useApi<Any>((s) => api2.strategyTrades(strategy, date, s), [strategy, date],
    { refreshMs: 15_000 });
  const d = q.data || {};
  const rows: Any[] = d.rows || [];

  const shown = React.useMemo(() => {
    let r = rows;
    if (view === 'open') r = r.filter((x) => x.status === 'OPEN');
    if (view === 'closed') r = r.filter((x) => x.status !== 'OPEN');
    const f = find.trim().toUpperCase();
    if (f) r = r.filter((x) => String(x.symbol || '').includes(f));
    return r;
  }, [rows, view, find]);

  // Every figure below is in the payload or arithmetic on it.
  const closed = d.closed ?? 0;
  const wins = d.wins ?? 0;
  const winRate = closed ? (wins / closed) * 100 : null;
  const rrs = rows.map(rr).filter((x): x is number => x !== null);
  const avgRr = rrs.length ? rrs.reduce((a, b) => a + b, 0) / rrs.length : null;

  const stats = [
    { icon: <Activity size={18} />, tone: 'blue', value: d.count != null ? String(d.count) : '—',
      label: 'Trades on this date', sub: d.open != null ? `${d.open} still open` : '' },
    { icon: <CheckCircle2 size={18} />, tone: 'green', value: winRate != null ? `${winRate.toFixed(1)}%` : '—',
      label: 'Win rate', sub: closed ? `${wins} of ${closed} closed` : 'nothing closed yet' },
    { icon: <Target size={18} />, tone: 'violet', value: avgRr != null ? `1 : ${avgRr.toFixed(2)}` : '—',
      label: 'Average reward to risk', sub: rrs.length ? `across ${rrs.length} trades` : '' },
    { icon: <TrendingUp size={18} />, tone: d.total_profit >= 0 ? 'green' : 'red',
      value: d.total_profit != null ? money(d.total_profit) : '—',
      label: 'P&L, trades opened on this date', sub: d.total_pct != null ? pct(d.total_pct) : '' },
    { icon: <Clock size={18} />, tone: 'amber',
      value: d.realized_on_date != null ? money(d.realized_on_date) : '—',
      label: 'Realised on this date', sub: d.closed_on_date != null ? `${d.closed_on_date} positions closed` : '' },
  ];

  return (
    <div className="nd nst">
      {/* ---------------------------------------------------------------- hero */}
      <section className="nd-hero nst-hero">
        <img className="nd-hero-img" src={strategyHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><Crosshair size={14} /> Tracked paper trades</span>
          <h1>Strategy Trades</h1>
          <p>
            Every signal the intraday models opened, with its entry, stop and target, tracked to the close.
            Paper only — no order is placed from this page.
          </p>
          <div className="nst-rulebar">
            {d.rules?.notional_usd != null && <span>${Number(d.rules.notional_usd).toLocaleString('en-US')} a trade</span>}
            {d.rules?.max_stop_pct != null && <span>stop capped at {d.rules.max_stop_pct}%</span>}
            {d.rules?.eod_close_et && <span>flat by {d.rules.eod_close_et} ET</span>}
            {d.rth != null && <span className={d.rth ? 'on' : ''}>{d.rth ? 'Market open' : 'Market closed'}</span>}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- stat row */}
      <div className="nd-stats nst-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div>
              <b>{s.value}</b>
              <span>{s.label}</span>
              {!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}
            </div>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------------- filters */}
      <div className="nst-filters">
        <div className="nd-tabs">
          {(['all', 'open', 'closed'] as const).map((k) => (
            <button type="button" key={k} className={view === k ? 'is-on' : ''} onClick={() => setView(k)}>
              {k === 'all' ? 'All trades' : k === 'open' ? 'Open now' : 'Closed'}
            </button>
          ))}
        </div>

        <label className="nst-field">
          <span>Model</span>
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All</option>
            {(d.strategies || []).map((s: Any) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </label>

        <label className="nst-field">
          <span>Date</span>
          <input type="date" value={date || (d.date || '')} onChange={(e) => setDate(e.target.value)} />
        </label>

        <label className="nst-field nst-find">
          <span>Symbol</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="e.g. NVDA" />
        </label>

        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* ---------------------------------------------------------------- the blotter */}
      <section className="nd-card nst-card">
        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && (
          <div className="nd-empty">No trades on this date.</div>
        )}
        {!!rows.length && !shown.length && (
          <div className="nd-empty">No trades match this filter.</div>
        )}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Model</th><th>Signal</th>
                  <th className="r">Entry</th><th className="r">Target</th><th className="r">Stop</th>
                  <th className="r">R:R</th><th className="r">Price</th><th className="r">Change</th>
                  <th className="r">P&amp;L</th><th className="r">Score</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const ratio = rr(r);
                  const tone = STATUS_TONE[r.status] || 'flat';
                  return (
                    <tr key={r.id}>
                      <td>
                        <NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink>
                        {r.entry_time && <div className="nst-sub">{r.entry_time}</div>}
                      </td>
                      {/* strategy_label is already "Agent 2" for a user - rendered, never resolved here (A.26) */}
                      <td className="nst-mute">{r.strategy_label || r.strategy}</td>
                      <td>
                        <span className={`nd-chip ${r.side === 'SHORT' ? 'put' : 'call'}`}>
                          {r.side === 'SHORT' ? 'SELL' : 'BUY'}
                        </span>
                      </td>
                      <td className="r">{n2(r.entry_price)}</td>
                      <td className="r">{n2(r.target)}</td>
                      <td className="r">
                        {n2(r.stop)}
                        {r.be_moved && <div className="nst-sub">at breakeven</div>}
                      </td>
                      <td className="r">{ratio != null ? `1:${ratio.toFixed(2)}` : '—'}</td>
                      <td className="r">{n2(r.last_price)}</td>
                      <td className={`r ${dir(r.profit_pct)}`}>{pct(r.profit_pct)}</td>
                      <td className={`r ${dir(r.profit)}`}>{money(r.profit)}</td>
                      <td className="r nst-mute">{r.score_at_entry != null ? n2(r.score_at_entry) : '—'}</td>
                      <td>
                        <span className={`nst-status ${tone}`}>{r.exit_reason_label || r.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Positions carried from an earlier date are deliberately outside the table and the totals. */}
        {!!d.carried_open_count && (
          <p className="nd-note">
            {d.carried_open_count} position{d.carried_open_count === 1 ? '' : 's'} opened on an earlier date
            {' '}are still open. Their P&amp;L stays attributed to the day they were opened, so they are not in
            the table or the totals above.
          </p>
        )}
        {!!d.realized_note && <p className="nd-note">{d.realized_note}</p>}
      </section>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Blotter detail (administrator)</summary>
          <div>
            source: {d.source || '—'} · date {d.date || '—'} · notional {money(d.total_notional)} ·
            gap effect {money(d.gap_effect_total)} · entries open: {String(d.entries_open)} ·
            after close: {String(d.after_close)}
            {d.rules?.stop_cap_hits != null && <> · stop-cap hits {d.rules.stop_cap_hits}</>}
            {d.rules?.score_drop_exit != null && <> · score-drop exit {d.rules.score_drop_exit}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The blotter could not be loaded. Nothing is shown rather than a stale book.</div>
      )}
    </div>
  );
}
