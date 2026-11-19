import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  AlertTriangle, CheckCircle2, Clock, Percent, ShieldCheck, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Forward Scoreboard in the redesigned shell (Figma frame 8:10).
 *
 * A KNOWN LEAK THIS PAGE REFUSES TO RENDER. /api/earnings/forward-scoreboard returns `by_model`,
 * `attribution.by_model` and a `model` field on every trade row, keyed by the vendor's own name, and the
 * route applies no model_privacy wrap. B.1 gates exactly this, and B.1a already made per-model accuracy
 * administrator-only. So nothing per-model is drawn for a user here.
 *
 * That is a MITIGATION, NOT A FIX: the names are still in the payload, and anyone can read it. The fix
 * is server-side - wrap the route like the other seventeen - and has been reported to the owner.
 *
 * NEVER ADD AN AGENT MAPPING HERE. Translating "grok" to "Agent 1" in the frontend would put the vendor
 * list back into the bundle, which B.2a removed on purpose. The server maps; the page renders.
 *
 * WHAT THE NUMBERS ARE. Every figure is a SIMULATED execution on historical daily bars against
 * predictions that were recorded before each announcement. No live order was ever placed from them. The
 * page says so prominently, because a scoreboard that looks like trading results and is not would be the
 * most misleading thing we could publish.
 */

type Any = any;

const pct = (v: Any, dp = 2): string =>
  (v === null || v === undefined || Number.isNaN(Number(v))
    ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(dp)}%`);
const plain = (v: Any, dp = 1): string =>
  (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(dp));
const dir = (v: Any): string => (v === null || v === undefined ? '' : Number(v) >= 0 ? 'up' : 'down');

/** One aggregate block: {cost:{...}, zero_cost:{...}}. The after-cost figure is the honest one. */
const net = (block: Any) => block?.cost || null;

export default function NewForwardScoreboardPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [side, setSide] = React.useState<'all' | 'BUY' | 'SELL'>('all');

  const q = useApi<Any>((s) => api2.forwardScoreboard(undefined, s), [], { refreshMs: 8_000 });
  const d = q.data || {};
  const computing = d.status === 'COMPUTING';
  const counts = d.counts || {};
  const overall = net(d.overall);
  const pf = d.portfolio || {};

  const trades: Any[] = d.trades || [];
  const shownTrades = React.useMemo(
    () => (side === 'all' ? trades : trades.filter((t) => String(t.side) === side)).slice(0, 200),
    [trades, side],
  );

  const stats = [
    { tone: 'blue', icon: <ShieldCheck size={18} />,
      value: counts.distinct_events != null ? String(counts.distinct_events) : '—',
      label: 'Events with a recorded call', sub: counts.priced_trades != null ? `${counts.priced_trades} priced calls` : '' },
    { tone: overall && overall.win_rate >= 50 ? 'green' : 'red', icon: <CheckCircle2 size={18} />,
      value: overall?.win_rate != null ? `${plain(overall.win_rate)}%` : '—',
      label: 'Win rate, after costs', sub: overall?.n != null ? `${overall.n} calls` : '' },
    { tone: 'violet', icon: <Percent size={18} />,
      value: overall?.avg_pct != null ? pct(overall.avg_pct, 2) : '—',
      label: 'Average per call, after costs', sub: overall?.median_pct != null ? `median ${pct(overall.median_pct, 2)}` : '' },
    { tone: pf.total_return_pct >= 0 ? 'green' : 'red', icon: <TrendingUp size={18} />,
      value: pf.total_return_pct != null ? pct(pf.total_return_pct, 2) : '—',
      label: 'Simulated portfolio return', sub: pf.positions != null ? `${pf.positions} positions` : '' },
    { tone: 'red', icon: <TrendingDown size={18} />,
      value: pf.max_drawdown_pct != null ? pct(-Math.abs(pf.max_drawdown_pct), 2) : '—',
      label: 'Deepest drawdown', sub: pf.trading_days != null ? `over ${pf.trading_days} sessions` : '' },
  ];

  return (
    <div className="nd nst nua">
      <section className="nua-head">
        <h1><ShieldCheck size={22} /> Forward Scoreboard</h1>
        <p>
          How the pre-earnings calls would have done, measured against what actually happened.
          Every call here was recorded <b>before</b> its announcement.
        </p>
      </section>

      {/* The single most important sentence on the page. */}
      <div className="nfs-warn">
        <AlertTriangle size={16} />
        <div>
          <b>These are simulated results, not trading results.</b> Entries and exits are priced on
          historical daily bars; no live order was placed from any of these calls
          {d.provenance?.live_trades === 0 && <> (live orders from this record: <b>0</b>)</>}.
          Costs are modelled assumptions, not fills.
        </div>
      </div>

      {computing ? (
        <section className="nd-card nst-card">
          <div className="nd-empty">
            Pricing the record now — this takes a moment the first time. The page will fill in.
          </div>
        </section>
      ) : (
        <>
          <div className="nd-stats nst-stats">
            {stats.map((s) => (
              <div className="nd-stat" key={s.label}>
                <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
                <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
              </div>
            ))}
          </div>

          {/* ---------------------------------------------------------------- cuts we may show */}
          <div className="nfs-grid">
            <section className="nd-card">
              <header className="nd-card-head"><h3>By direction</h3></header>
              <table className="nd-table">
                <thead><tr><th>Side</th><th className="r">Calls</th><th className="r">Win rate</th><th className="r">Average</th><th className="r">Total</th></tr></thead>
                <tbody>
                  {['BUY', 'SELL'].map((k) => {
                    const a = net(d.by_side?.[k]);
                    return (
                      <tr key={k}>
                        <td><span className={`nd-chip ${k === 'SELL' ? 'put' : 'call'}`}>{k}</span></td>
                        <td className="r">{a?.n ?? '—'}</td>
                        <td className="r">{a?.win_rate != null ? `${plain(a.win_rate)}%` : '—'}</td>
                        <td className={`r ${dir(a?.avg_pct)}`}>{pct(a?.avg_pct, 2)}</td>
                        <td className={`r ${dir(a?.total_pct)}`}>{pct(a?.total_pct, 2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>

            <section className="nd-card">
              <header className="nd-card-head"><h3>By how early the call was made</h3></header>
              <table className="nd-table">
                <thead><tr><th>Timing</th><th className="r">Calls</th><th className="r">Win rate</th><th className="r">Average</th></tr></thead>
                <tbody>
                  {Object.keys(d.by_entry_timing || {}).map((k) => {
                    const a = net(d.by_entry_timing[k]);
                    return (
                      <tr key={k}>
                        <td className="nst-mute">{k}</td>
                        <td className="r">{a?.n ?? '—'}</td>
                        <td className="r">{a?.win_rate != null ? `${plain(a.win_rate)}%` : '—'}</td>
                        <td className={`r ${dir(a?.avg_pct)}`}>{pct(a?.avg_pct, 2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>

            {!!(d.attribution?.by_sector || []).length && (
              <section className="nd-card">
                <header className="nd-card-head"><h3>By sector</h3></header>
                <table className="nd-table">
                  <thead><tr><th>Sector</th><th className="r">Calls</th><th className="r">Average</th><th className="r">Total</th></tr></thead>
                  <tbody>
                    {(d.attribution.by_sector || []).slice(0, 10).map((r: Any) => (
                      <tr key={r.key}>
                        <td className="nst-mute">{r.key || '—'}</td>
                        <td className="r">{r.n}</td>
                        <td className={`r ${dir(r.avg_net_pct)}`}>{pct(r.avg_net_pct, 2)}</td>
                        <td className={`r ${dir(r.total_net_pct)}`}>{pct(r.total_net_pct, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </div>

          {/* ---------------------------------------------------------------- the calls */}
          <div className="nst-filters">
            <div className="nd-tabs">
              {(['all', 'BUY', 'SELL'] as const).map((k) => (
                <button type="button" key={k} className={side === k ? 'is-on' : ''} onClick={() => setSide(k)}>
                  {k === 'all' ? 'All calls' : k}
                </button>
              ))}
            </div>
            <span className="nst-count">
              showing {shownTrades.length} of {trades.length}
              {trades.length > 200 && ' (newest 200)'}
            </span>
          </div>

          <section className="nd-card nst-card">
            {!shownTrades.length && <div className="nd-empty">No priced calls in the record.</div>}
            {!!shownTrades.length && (
              <div className="nst-scroll">
                <table className="nd-table nst-table">
                  <thead>
                    <tr>
                      <th>Symbol</th><th>Side</th><th>Sector</th><th>Reported</th>
                      <th className="r">Called</th><th className="r">Held</th>
                      <th className="r">Entry</th><th className="r">Exit</th>
                      <th className="r">Gross</th><th className="r">Cost</th><th className="r">Net</th>
                      {/* the model column is administrator-only: B.1a, and the route does not yet mask it */}
                      {isAdmin && <th>Model</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {shownTrades.map((t, i) => (
                      <tr key={`${t.symbol}-${t.earnings_date}-${t.t_minus}-${i}`}>
                        <td><NavLink to={`/ai-insights?symbol=${t.symbol}`} className="nd-sym">{t.symbol}</NavLink></td>
                        <td><span className={`nd-chip ${t.side === 'SELL' ? 'put' : 'call'}`}>{t.side}</span></td>
                        <td className="nst-mute">{t.sector || '—'}</td>
                        <td className="nst-mute">{t.earnings_date || '—'}</td>
                        <td className="r nst-mute">T-{t.t_minus}</td>
                        <td className="r nst-mute">{t.held_days != null ? `${t.held_days}d` : '—'}</td>
                        <td className="r">{plain(t.entry_price, 2)}</td>
                        <td className="r">{plain(t.exit_price, 2)}</td>
                        <td className={`r ${dir(t.gross_pct)}`}>{pct(t.gross_pct, 2)}</td>
                        <td className="r nst-mute">{pct(-Math.abs(Number(t.cost_pct ?? 0)), 2)}</td>
                        <td className={`r ${dir(t.net_pct)}`}><b>{pct(t.net_pct, 2)}</b></td>
                        {isAdmin && <td className="nst-mute">{t.model}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* what the numbers rest on - stated, not buried */}
          <div className="nua-explain">
            <b>How this is measured.</b>{' '}
            {d.provenance?.predictions} {d.provenance?.executions}
            {d.cost_model && (
              <> Costs applied: half-spread {d.cost_model.half_spread_bps}bps, commission{' '}
                {d.cost_model.commission_bps}bps{d.cost_model.short_borrow_annual_pct != null
                  && <>, short borrow {d.cost_model.short_borrow_annual_pct}% a year</>}.</>
            )}
            {d.missing_data?.extended_hours_exit && (
              <> <b>Known limit:</b> {d.missing_data.extended_hours_exit}</>
            )}
          </div>

          {isAdmin && (
            <details className="nd-ops">
              <summary>Scoreboard detail (administrator)</summary>
              <div>
                as of {d.as_of || '—'} · eligible {counts.eligible_predictions ?? '—'} ·
                priced {counts.priced_trades ?? '—'} · valid prices {counts.valid_prices ?? '—'} ·
                symbols {counts.symbols_priced ?? '—'}
                {counts.symbols_capped_out ? ` · capped out ${counts.symbols_capped_out}` : ''}
                {!!d.publication?.classification && <> · publication: {d.publication.classification}</>}
              </div>
              {!!(d.attribution?.by_model || []).length && (
                <div>
                  by model: {(d.attribution.by_model || [])
                    .map((r: Any) => `${r.key} ${pct(r.avg_net_pct, 2)} (${r.n})`).join(' · ')}
                </div>
              )}
            </details>
          )}
        </>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The scoreboard could not be loaded.</div>
      )}
    </div>
  );
}
