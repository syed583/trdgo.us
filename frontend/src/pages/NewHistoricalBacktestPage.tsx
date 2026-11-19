import React from 'react';
import {
  AlertTriangle, CheckCircle2, FlaskConical, Percent, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Historical Backtest (Track B) in the redesigned shell (Figma frame 8:18).
 *
 * TWO THINGS THIS PAYLOAD LEAKS, AND WHAT THIS PAGE DOES ABOUT THEM.
 *
 * 1. `by_model` is keyed by the vendor's own name, and /api/earnings/historical-backtest applies no
 *    model_privacy wrap - the same gap as the forward scoreboard. Nothing per-model is drawn for a user.
 * 2. `present_factors` and `missing_factors` enumerate the model's COMPLETE input list - seventeen named
 *    factors. A.29 gated exactly that to administrators on six routes; this is not one of them.
 *
 * The factor lists are not merely internals, though: they are the reason this backtest is honest. Only
 * four of the seventeen inputs can be reconstructed point-in-time, so the simulation is a fraction of
 * the live model and must say so. The page therefore states the LIMITATION with its COUNT - "4 of 17
 * inputs" - and keeps the names for an administrator. A reader learns the result is partial without
 * being handed the model's ingredient list.
 *
 * Both are mitigations on the page. The fix is server-side and has been reported.
 */

type Any = any;

const pct = (v: Any, dp = 2): string =>
  (v === null || v === undefined || Number.isNaN(Number(v))
    ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(dp)}%`);
const plain = (v: Any, dp = 1): string =>
  (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(dp));
const dir = (v: Any): string => (v === null || v === undefined ? '' : Number(v) >= 0 ? 'up' : 'down');

/** Each aggregate is {cost, zero_cost}; after costs is the one worth quoting. */
const net = (b: Any) => b?.cost || null;

export default function NewHistoricalBacktestPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const q = useApi<Any>((s) => api2.historicalBacktest(s), [], { refreshMs: 8_000 });
  const d = q.data || {};
  const computing = d.status === 'COMPUTING';
  const overall = net(d.overall);

  // Kit L: a user now receives COUNTS and empty name lists; an administrator still receives the names.
  // Reading the counts when they are there keeps the limitation statement intact for both.
  const present: string[] = d.present_factors || [];
  const missing: string[] = d.missing_factors || [];
  const presentCount: number = d.present_factor_count ?? present.length;
  const totalFactors: number = d.factor_total ?? (present.length + missing.length);

  const portfolioAll = d.portfolio?.all_timings?.net || null;

  const stats = [
    { tone: 'blue', icon: <FlaskConical size={18} />,
      value: d.months != null ? `${d.months} mo` : '—',
      label: 'Window tested', sub: d.symbols_tested != null ? `${d.symbols_tested} symbols` : '' },
    { tone: 'violet', icon: <FlaskConical size={18} />,
      value: d.events_tested != null ? String(d.events_tested) : '—',
      label: 'Earnings events', sub: d.model_predictions_count != null ? `${d.model_predictions_count} calls priced` : '' },
    { tone: overall && overall.win_rate >= 50 ? 'green' : 'red', icon: <CheckCircle2 size={18} />,
      value: overall?.win_rate != null ? `${plain(overall.win_rate)}%` : '—',
      label: 'Win rate, after costs', sub: overall?.n != null ? `${overall.n} calls` : '' },
    { tone: 'violet', icon: <Percent size={18} />,
      value: overall?.avg_pct != null ? pct(overall.avg_pct, 2) : '—',
      label: 'Average per call', sub: overall?.median_pct != null ? `median ${pct(overall.median_pct, 2)}` : '' },
    { tone: portfolioAll?.total_return_pct >= 0 ? 'green' : 'red', icon: <TrendingUp size={18} />,
      value: portfolioAll?.total_return_pct != null ? pct(portfolioAll.total_return_pct, 2) : '—',
      label: 'Simulated portfolio', sub: portfolioAll?.positions != null ? `${portfolioAll.positions} positions` : '' },
    { tone: 'red', icon: <TrendingDown size={18} />,
      value: portfolioAll?.max_drawdown_pct != null ? pct(-Math.abs(portfolioAll.max_drawdown_pct), 2) : '—',
      label: 'Deepest drawdown', sub: '' },
  ];

  return (
    <div className="nd nst nua">
      <section className="nua-head">
        <h1><FlaskConical size={22} /> Historical Backtest</h1>
        <p>
          The pre-earnings calls replayed over the past {d.months ?? '—'} months, rebuilt from what was
          knowable at the time.
        </p>
      </section>

      {/* The limitation comes FIRST, because the result is meaningless without it. */}
      <div className="nfs-warn">
        <AlertTriangle size={16} />
        <div>
          <b>A partial simulation, not the live model.</b>{' '}
          {totalFactors > 0 && (
            <>Only <b>{presentCount} of {totalFactors}</b> of the model&apos;s inputs can be
              rebuilt point-in-time — price action and prior-report history. The rest, including the
              options-market inputs, are not retained historically and are <b>absent here</b>. </>
          )}
          This is therefore <b>not</b> the live model&apos;s record, and it is never mixed with the
          genuinely recorded predictions on the Forward Scoreboard. No live order came from any of it.
        </div>
      </div>

      {computing ? (
        <section className="nd-card nst-card">
          <div className="nd-empty">
            Rebuilding the history now — this takes a while the first time. The page will fill in.
          </div>
        </section>
      ) : (
        <>
          <div className="nd-stats nua-stats">
            {stats.map((s) => (
              <div className="nd-stat" key={s.label}>
                <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
                <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
              </div>
            ))}
          </div>

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

            <section className="nd-card">
              <header className="nd-card-head"><h3>Simulated portfolio, by timing</h3></header>
              <table className="nd-table">
                <thead><tr><th>Timing</th><th className="r">Positions</th><th className="r">Return</th><th className="r">Drawdown</th></tr></thead>
                <tbody>
                  {Object.keys(d.portfolio || {}).map((k) => {
                    const p = d.portfolio[k]?.net;
                    if (!p) return null;
                    return (
                      <tr key={k}>
                        <td className="nst-mute">{k === 'all_timings' ? 'All' : k}</td>
                        <td className="r">{p.positions ?? '—'}</td>
                        <td className={`r ${dir(p.total_return_pct)}`}>{pct(p.total_return_pct, 2)}</td>
                        <td className="r down">{p.max_drawdown_pct != null ? pct(-Math.abs(p.max_drawdown_pct), 2) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          </div>

          <div className="nua-explain">
            <b>What the figures are.</b> {d.disclaimer}
            {!!d.metrics_note && <> {d.metrics_note}</>}
            {!!d.publication?.reason && <> <b>Publication times:</b> {d.publication.reason}</>}
          </div>

          {isAdmin && (
            <details className="nd-ops">
              <summary>Backtest detail (administrator)</summary>
              <div>
                as of {d.as_of || '—'} · label {d.label || '—'} ·
                consensus trades {d.consensus_trades ?? '—'}
                {!!d.consensus_side_distribution && <> · sides {JSON.stringify(d.consensus_side_distribution)}</>}
              </div>
              <div>reconstructable inputs: {present.join(', ') || '—'}</div>
              <div>not retained historically: {missing.join(', ') || '—'}</div>
              {!!d.by_model && (
                <div>
                  by model: {Object.keys(d.by_model).map((m) => {
                    const a = net(d.by_model[m]);
                    return `${m} ${a?.win_rate != null ? `${plain(a.win_rate)}%` : '—'} (${a?.n ?? 0})`;
                  }).join(' · ')}
                </div>
              )}
            </details>
          )}
        </>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The backtest could not be loaded.</div>
      )}
    </div>
  );
}
