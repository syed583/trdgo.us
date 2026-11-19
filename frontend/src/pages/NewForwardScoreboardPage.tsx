import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  AlertTriangle, CheckCircle2, Clock, Percent, ShieldCheck, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { PageContext } from '../App';
import boardHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';
import '../new-forward.css';

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
  // The frame's four Loss Attribution tabs, in its own order.
  const [attrTab, setAttrTab] = React.useState<'by_side' | 'by_timing' | 'by_sector' | 'by_model'>('by_side');

  const q = useApi<Any>((s) => api2.forwardScoreboard(undefined, s), [], { refreshMs: 8_000 });
  const d = q.data || {};
  const computing = d.status === 'COMPUTING';
  const counts = d.counts || {};
  const overall = net(d.overall);
  // portfolio is NESTED: {all_events_consensus:{net,zero_cost}, t1_events_consensus:{...}} and each
  // leaf is {positions, trading_days, total_return_pct, max_drawdown_pct}. Reading d.portfolio.total_pct
  // - which never existed - is why two of the five tiles showed a dash.
  const pf = d.portfolio?.all_events_consensus?.net || {};

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
      <section className="nd-hero nfs-hero">
        <img className="nd-hero-img" src={boardHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><ShieldCheck size={13} /> Genuine forward record</span>
          <h1>Forward <em>Scoreboard</em></h1>
          {/* The provenance pills the frame puts under the title - read from the payload, never
              asserted by the page: it is the service that knows nothing is a live order. */}
          <div className="nfs-pills">
            <span className="nfs-pill ok">Recorded predictions</span>
            <span className="nfs-pill info">Simulated executions</span>
            <span className="nfs-pill flat">Live trades: {d.provenance?.live_trades ?? 0}</span>
            {!!d.as_of && <span className="nfs-pill flat">as of {d.as_of}</span>}
          </div>
        </div>
      </section>
      <section className="nua-head nfs-sub">
        <h1 className="sr-only"><ShieldCheck size={22} /> Forward Scoreboard</h1>
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
          <div className="nd-stats nst-stats nfs-stats">
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

          {/* ------------------------------------------------- caveats the frame states */}
          {(d.publication?.note || d.consensus?.note) && (
            <div className="nfs-caveats">
              {!!d.publication?.note && (
                <div className="nfs-caveat warn">
                  <b>Publication timestamp.</b> {d.publication.note}
                  {d.publication.verified != null && (
                    <> Verified instants: {d.publication.verified} of{' '}
                      {(d.publication.verified || 0) + (d.publication.assumed || 0)}.</>
                  )}
                </div>
              )}
              {!!d.consensus?.note && (
                <div className="nfs-caveat">
                  <b>Consensus.</b> {d.consensus.note}
                </div>
              )}
            </div>
          )}

          {/* -------------------------------------------- prediction-level performance */}
          <section className="nd-card nfs-perf">
            <header className="nd-card-head">
              <h3>Prediction-level performance</h3>
              <span className="nst-mute">per model x event x timing — not a portfolio</span>
            </header>
            <div className="nst-scroll">
              <table className="nd-table nst-table">
                <thead>
                  <tr>
                    <th>Group</th><th className="r">Calls</th><th className="r">Win rate</th>
                    <th className="r">Avg (zero-cost)</th><th className="r">Avg (net)</th>
                    <th className="r">Median (net)</th><th className="r">Profit factor</th>
                  </tr>
                </thead>
                <tbody>
                  {([['Overall', d.overall],
                     ...(['BUY', 'SELL'] as const).map((k) => [k, d.by_side?.[k]] as const),
                     ...Object.keys(d.by_entry_timing || {}).map((k) => [k, d.by_entry_timing[k]] as const),
                  ] as Any[]).filter(([, b]) => b).map(([label, b]: Any) => {
                    const c = b?.cost || {};
                    const z = b?.zero_cost || {};
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

          {/* ------------------------------------------------------------- portfolio */}
          {!!d.portfolio && (
            <section className="nd-card nfs-portfolio">
              <header className="nd-card-head">
                <h3>Portfolio</h3>
                <span className="nst-mute">event-level consensus, daily-marked, equal-weight</span>
              </header>
              <div className="nfs-pf-grid">
                {([['All events', d.portfolio.all_events_consensus],
                   ['T-1 events', d.portfolio.t1_events_consensus]] as Any[])
                  .filter(([, v]) => v).map(([label, v]: Any) => (
                    <div className="nfs-pf" key={label}>
                      <h4>{label}</h4>
                      <div className="nfs-pf-rows">
                        <div><span>Total return (net)</span>
                          <b className={dir(v.net?.total_return_pct)}>{pct(v.net?.total_return_pct, 2)}</b></div>
                        <div><span>Total return (zero-cost)</span>
                          <b className={dir(v.zero_cost?.total_return_pct)}>{pct(v.zero_cost?.total_return_pct, 2)}</b></div>
                        <div><span>Max drawdown (net)</span>
                          <b className="down">{pct(v.net?.max_drawdown_pct, 2)}</b></div>
                        <div><span>Positions</span><b>{v.net?.positions ?? '—'}</b></div>
                        <div><span>Trading days</span><b>{v.net?.trading_days ?? '—'}</b></div>
                      </div>
                    </div>
                  ))}
              </div>
              {/* The frame draws a drawdown CURVE here. The payload carries no equity series at all -
                  only the figures above - so the curve is absent rather than drawn from invented points. */}
              <p className="nfs-note">
                No equity curve is drawn: the record stores these totals, not a daily series.
              </p>
            </section>
          )}

          {/* ----------------------------------------------------- loss attribution */}
          {!!d.attribution && (
            <section className="nd-card nfs-attr">
              <header className="nd-card-head">
                <h3>Loss attribution</h3>
                <div className="nd-tabs">
                  {([['by_side', 'By side'], ['by_timing', 'By entry timing'],
                     ['by_sector', 'By sector'], ['by_model', 'By model']] as const)
                    .filter(([k]) => k !== 'by_model' || isAdmin)   /* B.1: model identity is admin-only */
                    .map(([k, label]) => (
                      <button type="button" key={k} className={attrTab === k ? 'is-on' : ''}
                        onClick={() => setAttrTab(k as Any)}>{label}</button>
                    ))}
                </div>
              </header>
              {(() => {
                const list: Any[] = (d.attribution as Any)[attrTab] || [];
                const sorted = [...list].sort((a, b) => (a.total_net_pct ?? 0) - (b.total_net_pct ?? 0));
                if (!sorted.length) return <div className="nd-empty">Nothing recorded for this split.</div>;
                return (
                  <>
                    <ResponsiveContainer width="100%" height={Math.max(150, sorted.length * 32)}>
                      <BarChart data={sorted} layout="vertical"
                        margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                        <XAxis type="number" tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)"
                          tickLine={false} unit="%" />
                        <YAxis type="category" dataKey="key" width={140} tick={{ fontSize: 11 }}
                          stroke="var(--nu-ink-dim)" tickLine={false} />
                        <Tooltip formatter={(v: Any) => [`${Number(v).toFixed(2)}%`, 'net']} />
                        <Bar dataKey="total_net_pct" radius={[0, 4, 4, 0]}>
                          {sorted.map((r) => (
                            <Cell key={r.key}
                              fill={(r.total_net_pct ?? 0) >= 0 ? 'var(--nu-up)' : 'var(--nu-down)'} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                    <div className="nst-scroll">
                      <table className="nd-table nst-table">
                        <thead>
                          <tr><th>Group</th><th className="r">Calls</th>
                            <th className="r">Net total</th><th className="r">Net average</th></tr>
                        </thead>
                        <tbody>
                          {sorted.map((r) => (
                            <tr key={r.key}>
                              <td>{r.key}</td>
                              <td className="r">{r.n}</td>
                              <td className={`r ${dir(r.total_net_pct)}`}><b>{pct(r.total_net_pct, 2)}</b></td>
                              <td className={`r ${dir(r.avg_net_pct)}`}>{pct(r.avg_net_pct, 2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                );
              })()}
            </section>
          )}

          {/* --------------------------------------------- what is NOT in these numbers */}
          {!!d.missing_data && (
            <div className="nfs-missing">
              {Object.entries(d.missing_data as Record<string, string>).map(([k, v]) => (
                <div className="nfs-caveat warn" key={k}>
                  <b>{k.replace(/_/g, ' ')}.</b> {v}
                </div>
              ))}
            </div>
          )}

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
