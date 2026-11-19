import React from 'react';
import { NavLink } from 'react-router-dom';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Layers, Scale, Zap } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * Unusual Activity in the redesigned shell (Figma frame 8:14).
 *
 * TWO THINGS FROM THE FRAME ARE NOT HERE, because nothing can fill them:
 *
 *  - Its "Call/Put Premium Ratio (Universe)" chart is a time series across the whole universe. The scan
 *    returns each name's CURRENT ratio against its own 20-day normal, not a universe series, so there is
 *    no line to draw. One cached point per symbol is not a chart.
 *  - Its "NORMAL 66" counter cannot exist: Part B only returns names that LEAN (call-heavy or put-heavy);
 *    the rest are filtered out server-side and never arrive, so a count of them would be invented.
 *
 * THE QUALIFYING NUMBERS. The frame prints the exact scan thresholds ("3x the 20-day average, volume >=
 * open interest, premium >= $100K, 60% at the ask"). Those live in tab_settings, which A.25 made
 * administrator-only, and the scan payload deliberately does not carry them. So the method is described
 * in words and the figures are left to the settings page rather than hard-coded here, where they would
 * also go stale the moment the settings changed.
 */

type Any = any;

const money = (v: Any): string => {
  const n = Number(v);
  if (v === null || v === undefined || Number.isNaN(n)) return '—';
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
};
const num = (v: Any): string => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-US'));
const x1 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `${Number(v).toFixed(1)}×`);
const x2 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `${Number(v).toFixed(2)}×`);
const pc = (v: Any): string => (v === null || v === undefined ? '—' : `${Math.round(Number(v) * (Number(v) <= 1 ? 100 : 1))}%`);

export default function NewUnusualActivityPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [part, setPart] = React.useState<'a' | 'b'>('a');
  const [find, setFind] = React.useState('');
  const [kind, setKind] = React.useState<'all' | 'C' | 'P'>('all');

  const q = useApi<Any>((s) => api2.unusualActivityScan(s), [], { refreshMs: 300_000 });
  const d = q.data || {};
  const a: Any[] = d.part_a || [];
  const b: Any[] = d.part_b || [];

  const shownA = React.useMemo(() => {
    let r = a;
    if (kind !== 'all') r = r.filter((x) => String(x.right || '').toUpperCase() === kind);
    const f = find.trim().toUpperCase();
    if (f) r = r.filter((x) => String(x.symbol || '').includes(f));
    return r;
  }, [a, kind, find]);

  const shownB = React.useMemo(() => {
    const f = find.trim().toUpperCase();
    return f ? b.filter((x) => String(x.symbol || '').includes(f)) : b;
  }, [b, find]);

  // Every counter below is computed from the rows that arrived.
  const calls = a.filter((r) => String(r.right || '').toUpperCase() === 'C').length;
  const puts = a.filter((r) => String(r.right || '').toUpperCase() === 'P').length;
  const premium = a.reduce((t, r) => t + (Number(r.premium) || 0), 0);
  const mults = a.map((r) => Number(r.vol_mult)).filter((n) => Number.isFinite(n));
  const avgMult = mults.length ? mults.reduce((s, n) => s + n, 0) / mults.length : null;
  const callHeavy = b.filter((r) => r.leaning === 'CALL HEAVY').length;
  const putHeavy = b.filter((r) => r.leaning === 'PUT HEAVY').length;

  const stats = [
    { tone: 'blue', icon: <Layers size={18} />, value: d.universe != null ? num(d.universe) : '—',
      label: 'Names tracked', sub: d.asof ? `as of ${d.asof}` : '' },
    { tone: 'violet', icon: <Zap size={18} />, value: a.length ? num(a.length) : '—',
      label: 'Unusual contracts', sub: a.length ? 'passed every test' : 'none right now' },
    { tone: 'green', icon: <ArrowUpRight size={18} />, value: a.length ? String(calls) : '—',
      label: 'Calls', sub: a.length ? `${Math.round((calls / a.length) * 100)}% of contracts` : '' },
    { tone: 'red', icon: <ArrowDownRight size={18} />, value: a.length ? String(puts) : '—',
      label: 'Puts', sub: a.length ? `${Math.round((puts / a.length) * 100)}% of contracts` : '' },
    { tone: 'amber', icon: <Scale size={18} />, value: a.length ? money(premium) : '—',
      label: 'Premium on these contracts', sub: '' },
    { tone: 'blue', icon: <Zap size={18} />, value: avgMult != null ? x1(avgMult) : '—',
      label: 'Average volume vs its 20-day', sub: mults.length ? `across ${mults.length}` : '' },
  ];

  return (
    <div className="nd nst nua">
      <section className="nua-head">
        <h1><Zap size={22} /> Unusual Activity</h1>
        <p>
          Option prints and call/put skews across the names reporting soon. Read-only — nothing here
          places an order.
        </p>
      </section>

      <div className="nd-stats nua-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
          </div>
        ))}
      </div>

      <div className="nst-filters">
        <div className="nd-tabs">
          <button type="button" className={part === 'a' ? 'is-on' : ''} onClick={() => setPart('a')}>
            Part A — Unusual contracts
          </button>
          <button type="button" className={part === 'b' ? 'is-on' : ''} onClick={() => setPart('b')}>
            Part B — Call/put disparity
          </button>
        </div>
        {part === 'a' && (
          <label className="nst-field">
            <span>Type</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as Any)}>
              <option value="all">Calls and puts</option>
              <option value="C">Calls only</option>
              <option value="P">Puts only</option>
            </select>
          </label>
        )}
        <label className="nst-field nst-find">
          <span>Symbol</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="e.g. NVDA" />
        </label>
        <span className="nst-count">
          {part === 'a' ? `${shownA.length} of ${a.length}` : `${shownB.length} of ${b.length}`}
        </span>
      </div>

      {/* ---------------------------------------------------------------- Part A */}
      {part === 'a' && (
        <>
          <div className="nua-explain">
            <b>What qualifies.</b> A contract whose volume today is far above its own 20-day average,
            is at least as large as the open interest standing before today (so it is mostly new
            positions), and carries meaningful premium. <b>Side</b> is read from where the volume traded
            in the spread — at the ask, at the bid, or mixed.
            {isAdmin && <> The exact thresholds are on the settings page.</>}
          </div>

          <section className="nd-card nst-card">
            {q.loading && !a.length && <div className="nd-empty">Loading…</div>}
            {!q.loading && !a.length && <div className="nd-empty">No contract qualifies right now.</div>}
            {!!a.length && !shownA.length && <div className="nd-empty">Nothing matches this filter.</div>}
            {!!shownA.length && (
              <div className="nst-scroll">
                <table className="nd-table nst-table">
                  <thead>
                    <tr>
                      <th>#</th><th>Symbol</th><th>Expiry</th><th className="r">Strike</th><th>C/P</th>
                      <th className="r">Volume</th><th className="r">vs 20-day</th>
                      <th className="r">Vol / OI</th><th className="r">Premium</th>
                      <th>Side</th><th className="r">Ask / Bid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownA.map((r, i) => (
                      <tr key={`${r.symbol}-${r.expiry}-${r.strike}-${r.right}-${i}`}>
                        <td className="nst-mute">{i + 1}</td>
                        <td><NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink></td>
                        <td className="nst-mute">{r.expiry || '—'}</td>
                        <td className="r">{r.strike != null ? Number(r.strike).toFixed(0) : '—'}</td>
                        <td>
                          <span className={`nd-chip ${r.right === 'P' ? 'put' : 'call'}`}>
                            {r.right === 'P' ? 'PUT' : r.right === 'C' ? 'CALL' : '—'}
                          </span>
                        </td>
                        <td className="r">
                          {num(r.volume)}
                          {r.avg_volume != null && <div className="nst-sub">20d {num(Math.round(r.avg_volume))}</div>}
                        </td>
                        <td className="r">{x1(r.vol_mult)}</td>
                        <td className="r">{x2(r.vol_oi)}</td>
                        <td className="r">{money(r.premium)}</td>
                        <td>
                          <span className={`nst-status ${r.side === 'BUY' ? 'win' : r.side === 'SELL' ? 'loss' : 'flat'}`}>
                            {r.side || '—'}
                          </span>
                        </td>
                        <td className="r nst-mute">
                          {r.ask_pct != null && r.bid_pct != null ? `${pc(r.ask_pct)} / ${pc(r.bid_pct)}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* ---------------------------------------------------------------- Part B */}
      {part === 'b' && (
        <>
          <div className="nua-explain">
            <b>How to read this.</b> Each name&apos;s call premium against its put premium today, measured
            against what is normal <em>for that name</em> over the last 20 sessions — so a stock that always
            trades call-heavy only appears when it is unusually so. Only names that lean are listed; the
            rest are filtered out before they reach this page, which is why there is no “normal” count.
          </div>

          <div className="nd-stats nua-bstats">
            <div className="nd-stat">
              <span className="nd-stat-ico green"><ArrowUpRight size={18} /></span>
              <div><b>{b.length ? String(callHeavy) : '—'}</b><span>Call heavy</span></div>
            </div>
            <div className="nd-stat">
              <span className="nd-stat-ico red"><ArrowDownRight size={18} /></span>
              <div><b>{b.length ? String(putHeavy) : '—'}</b><span>Put heavy</span></div>
            </div>
          </div>

          <section className="nd-card nst-card">
            {q.loading && !b.length && <div className="nd-empty">Loading…</div>}
            {!q.loading && !b.length && <div className="nd-empty">No name is leaning right now.</div>}
            {!!b.length && !shownB.length && <div className="nd-empty">Nothing matches this filter.</div>}
            {!!shownB.length && (
              <div className="nst-scroll">
                <table className="nd-table nst-table">
                  <thead>
                    <tr>
                      <th>#</th><th>Symbol</th><th className="r">Call premium</th><th className="r">Put premium</th>
                      <th className="r">Call / put today</th><th className="r">Normal for it</th>
                      <th className="r">Disparity</th><th className="r">Sessions</th><th>Leaning</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownB.map((r, i) => (
                      <tr key={r.symbol}>
                        <td className="nst-mute">{i + 1}</td>
                        <td><NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink></td>
                        <td className="r">{money(r.call_premium)}</td>
                        <td className="r">{money(r.put_premium)}</td>
                        <td className="r">{x2(r.ratio)}</td>
                        <td className="r nst-mute">{x2(r.normal_ratio)}</td>
                        <td className="r"><b>{x2(r.disparity)}</b></td>
                        <td className="r nst-mute">{r.hist_n ?? '—'}</td>
                        <td>
                          <span className={`nst-status ${r.leaning === 'CALL HEAVY' ? 'win' : 'loss'}`}>
                            {r.leaning || '—'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* The refresh state names our provider budget - operator detail, not a user's concern (B.1). */}
      {isAdmin && !!d.refresh && (
        <details className="nd-ops">
          <summary>Scan state (administrator)</summary>
          <div>
            priority {d.refresh.priority} · in session {String(d.refresh.in_session)} ·
            hourly {d.refresh.hourly_used}/{d.refresh.hourly_cap} · max in flight {d.refresh.max_inflight} ·
            paused {String(d.refresh.paused)} · capped {String(d.refresh.capped)} ·
            standing aside {String(d.refresh.standing_aside)}
            {!!(d.warm_occs || []).length && <> · warming {d.warm_occs.length} contracts</>}
            {!!(d.warm_syms || []).length && <> · warming {d.warm_syms.length} symbols</>}
            {!!d.refresh.note && <> · {d.refresh.note}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The scan could not be loaded. Nothing is shown rather than a stale tape.</div>
      )}
    </div>
  );
}
