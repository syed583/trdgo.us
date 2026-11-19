import React from 'react';
import {
  AlertTriangle, CheckCircle2, FlaskConical, Percent, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { PageContext } from '../App';
import btHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';
import '../new-forward.css';

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
      <section className="nd-hero nfs-hero">
        <img className="nd-hero-img" src={btHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow">Track B · partial-factor simulation</span>
          <h1>Historical <em>Backtest</em></h1>
          <div className="nfs-pills">
            <span className="nfs-pill info">Simulated</span>
            <span className="nfs-pill flat">Partial factor</span>
            {!!d.months && <span className="nfs-pill flat">{d.months} months</span>}
            {!!d.as_of && <span className="nfs-pill flat">as of {d.as_of}</span>}
          </div>
        </div>
      </section>
      <section className="nua-head nfs-sub">
        <h1 className="sr-only">Historical Backtest</h1>
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

          {/* The disclaimer, the publication caveat and the metrics note used to be crammed into one
              paragraph here. They now sit where the frame puts them - the warning at the top, the
              footnotes at the bottom - so this block is gone rather than repeating all three. */}

          {/* ------------------------------------------- what this simulation is NOT */}
          {!!d.disclaimer && (
            <div className="nfs-caveat warn nbt-disclaimer">
              <b>Partial-factor simulation.</b> {d.disclaimer}
            </div>
          )}

          {/* ------------------------------------------- prediction-level performance */}
          <section className="nd-card nfs-perf">
            <header className="nd-card-head">
              <h3>Performance</h3>
              <span className="nst-mute">per model x event x timing — correlated, not a portfolio</span>
            </header>
            <div className="nst-scroll">
              <table className="nd-table nst-table">
                <thead>
                  <tr>
                    <th>Group</th><th className="r">Trades</th><th className="r">Win rate</th>
                    <th className="r">Avg (zero-cost)</th><th className="r">Avg (net)</th>
                    <th className="r">Median (net)</th><th className="r">Profit factor</th>
                  </tr>
                </thead>
                <tbody>
                  {([['Overall', d.overall],
                     ...(['BUY', 'SELL'] as const).map((k) => [k, d.by_side?.[k]] as const),
                     ...Object.keys(d.by_entry_timing || {}).map((k) => [k, d.by_entry_timing[k]] as const),
                  ] as Any[]).filter(([, b]) => b?.cost).map(([label, b]: Any) => {
                    const c = b.cost || {}; const z = b.zero_cost || {};
                    return (
                      <tr key={label}>
                        <td><b>{label}</b></td>
                        <td className="r">{c.n ?? '—'}</td>
                        <td className="r">{c.win_rate != null ? `${c.win_rate}%` : '—'}</td>
                        <td className={`r ${dir(z.avg_pct)}`}>{pct(z.avg_pct, 2)}</td>
                        <td className={`r ${dir(c.avg_pct)}`}><b>{pct(c.avg_pct, 2)}</b></td>
                        <td className={`r ${dir(c.median_pct)}`}>{pct(c.median_pct, 2)}</td>
                        <td className="r nst-mute">{c.profit_factor ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* -------------------------------------------------------- the two books */}
          {!!d.portfolio && (
            <section className="nd-card nfs-portfolio">
              <header className="nd-card-head">
                <h3>Portfolio</h3>
                <span className="nst-mute">event-level consensus, daily-marked</span>
              </header>
              <div className="nfs-pf-grid">
                {Object.entries(d.portfolio as Record<string, Any>)
                  .filter(([, v]) => v?.net)
                  .map(([k, v]) => (
                    <div className="nfs-pf" key={k}>
                      <h4>{k === 'all_timings' ? 'All timings' : k}</h4>
                      <div className="nfs-pf-rows">
                        <div><span>Total return (net)</span>
                          <b className={dir(v.net?.total_return_pct)}>{pct(v.net?.total_return_pct, 2)}</b></div>
                        <div><span>Total return (zero-cost)</span>
                          <b className={dir(v.zero_cost?.total_return_pct)}>{pct(v.zero_cost?.total_return_pct, 2)}</b></div>
                        <div><span>Max drawdown (net)</span>
                          <b className="down">{pct(v.net?.max_drawdown_pct, 2)}</b></div>
                        <div><span>Positions</span><b>{v.net?.positions ?? '—'}</b></div>
                      </div>
                    </div>
                  ))}
              </div>
              <p className="nfs-note">
                No equity or drawdown curve is drawn: this record stores the totals above, not a daily series.
              </p>
            </section>
          )}

          {/* ----------------------------------------- realistic fixed-fraction sizing */}
          {!!d.portfolio_realistic && (
            <section className="nd-card nfs-portfolio">
              <header className="nd-card-head">
                <h3>Portfolio — realistic sizing</h3>
                <span className="nst-mute">fixed fraction of equity per position, capacity-capped</span>
              </header>
              <div className="nfs-pf-grid">
                {Object.entries(d.portfolio_realistic as Record<string, Any>)
                  .filter(([, v]) => v && v.total_return_pct != null)
                  .map(([k, v]) => (
                    <div className="nfs-pf" key={k}>
                      <h4>{k === 'all_timings' ? 'All timings' : k}</h4>
                      <div className="nfs-pf-rows">
                        <div><span>Total return</span>
                          <b className={dir(v.total_return_pct)}>{pct(v.total_return_pct, 2)}</b></div>
                        <div><span>Max drawdown</span><b className="down">{pct(v.max_drawdown_pct, 2)}</b></div>
                        <div><span>Positions taken</span><b>{v.positions_taken ?? '—'}</b></div>
                        <div><span>Skipped for capacity</span><b>{v.positions_skipped_capacity ?? '—'}</b></div>
                        <div><span>Most held at once</span><b>{v.max_concurrent ?? '—'}</b></div>
                      </div>
                    </div>
                  ))}
              </div>
            </section>
          )}

          {/* ------------------------------------------------- by model (admin only) */}
          {isAdmin && !!d.by_model && Object.keys(d.by_model).length > 0 && (() => {
            const rows = Object.entries(d.by_model as Record<string, Any>)
              .map(([k, v]) => ({ key: k, n: v?.cost?.n ?? 0, win: v?.cost?.win_rate ?? null,
                avg: v?.cost?.avg_pct ?? null, pf: v?.cost?.profit_factor ?? null }))
              .sort((a, b) => (b.win ?? -1) - (a.win ?? -1));
            return (
              <section className="nd-card nfs-attr">
                <header className="nd-card-head">
                  <h3>By model</h3>
                  <span className="nst-mute">administrator only — a model&apos;s identity is not shown to users</span>
                </header>
                <ResponsiveContainer width="100%" height={Math.max(150, rows.length * 34)}>
                  <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                    <XAxis type="number" tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)"
                      tickLine={false} unit="%" />
                    <YAxis type="category" dataKey="key" width={120} tick={{ fontSize: 11 }}
                      stroke="var(--nu-ink-dim)" tickLine={false} />
                    <Tooltip formatter={(v: Any) => [`${Number(v).toFixed(1)}%`, 'win rate']} />
                    <Bar dataKey="win" radius={[0, 4, 4, 0]}>
                      {rows.map((r) => (
                        <Cell key={r.key} fill={(r.win ?? 0) >= 50 ? 'var(--nu-up)' : 'var(--nu-accent-2)'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="nst-scroll">
                  <table className="nd-table nst-table">
                    <thead><tr><th>Model</th><th className="r">Trades</th><th className="r">Win rate</th>
                      <th className="r">Avg (net)</th><th className="r">Profit factor</th></tr></thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.key}>
                          <td>{r.key}</td><td className="r">{r.n}</td>
                          <td className="r">{r.win != null ? `${r.win}%` : '—'}</td>
                          <td className={`r ${dir(r.avg)}`}>{pct(r.avg, 2)}</td>
                          <td className="r nst-mute">{r.pf ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            );
          })()}

          {/* ----------------------------------------------- what could be rebuilt */}
          <div className="nfs-missing">
            {d.factor_total != null && (
              <div className="nfs-caveat warn">
                <b>Reconstructable factors.</b> {d.present_factor_count ?? 0} of {d.factor_total} model
                inputs could be rebuilt point-in-time; {d.missing_factor_count ?? 0} were not retained
                historically and are absent from this result.
                {isAdmin && !!(d.present_factors || []).length && (
                  <> <span className="nst-mute">Present: {(d.present_factors || []).join(', ')}.</span></>
                )}
              </div>
            )}
            {!!d.publication?.reason && (
              <div className="nfs-caveat warn">
                <b>Publication timestamp.</b> {d.publication.classification}. {d.publication.reason}
              </div>
            )}
            {!!d.metrics_note && (
              <div className="nfs-caveat"><b>How these are measured.</b> {d.metrics_note}</div>
            )}
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
