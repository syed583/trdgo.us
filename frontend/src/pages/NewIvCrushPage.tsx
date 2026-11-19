import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  AlertTriangle, CalendarDays, Flame, Gauge, RefreshCw, Search, TrendingUp, X,
} from 'lucide-react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import ivHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';
import '../new-strategy.css';
import '../new-ivcrush.css';

/**
 * IV Crush Check in the redesigned shell (Figma frame 8:2).
 *
 * THE FRAME IS SYMBOL-DETAIL-CENTRIC; the live page was a long list. So this adds the detail half -
 * a selected symbol with its IV trend, its option ladder and its strike tables - and keeps the full
 * ranked list underneath, because the list is live content and nothing live is removed.
 *
 * WHAT COSTS A REQUEST. The scan is one call for the whole window. The chain and the IV series are
 * ONE REQUEST EACH PER SYMBOL YOU OPEN, never for all twenty - that is why they hang off `sel` and
 * not off the row list. Owner approved that cost on 2026-11-19.
 *
 * WHAT THE FRAME DRAWS THAT THIS DOES NOT, and why - each checked against the code that would have
 * to supply it, not assumed:
 *   * "Run-up Analysis" and "History (10 Sessions)" tabs - no endpoint produces either view. The
 *     ten-session comparison the frame means IS this page's table.
 *   * "Expected IV Crush" tile - a projection of the crush yet to happen. Nothing computes it; the
 *     measured crush of PAST events is not stored either (see next).
 *   * "IV Crush History (Past 4 Earnings)" - tab_b keeps result_code/result_date for the LATEST
 *     event only; no store holds the IV before and after previous reports.
 *   * The "100 IV" column beside each strike - the chain quotes current IV per contract; there is
 *     no per-strike 100-day average behind it. The IV Change Comparison panel therefore charts
 *     call IV against put IV across strikes, which is a reading we actually have, rather than a
 *     column we would have to invent.
 */

type Any = any;

const iv = (v: Any): string => (v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%`);
const pp = (v: Any): string =>
  (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)} pp`);
const num2 = (v: Any): string => (v === null || v === undefined ? '—' : Number(v).toFixed(2));
const money = (v: Any): string => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(2)}`);
const dmy = (s: Any): string => {
  if (!s) return '—';
  try {
    return new Date(`${String(s).slice(0, 10)}T12:00:00`)
      .toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
  } catch { return String(s); }
};

/** The mean IV of a row's two legs at one point in time, or null if neither is priced. */
function legIv(call: Any, put: Any): number | null {
  const vals = [call?.iv, put?.iv].map(Number).filter((n) => Number.isFinite(n));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}
function ivChange(r: Any): number | null {
  const now = legIv(r.call_now, r.put_now);
  const then = legIv(r.call_then, r.put_then);
  return now != null && then != null ? now - then : null;
}
/** Trading days until the report, or null when the date is unusable. */
function daysTo(date: Any): number | null {
  if (!date) return null;
  const d = new Date(`${String(date).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  return Math.max(0, Math.round((d.getTime() - Date.now()) / 86_400_000));
}

export default function NewIvCrushPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [days, setDays] = React.useState(15);
  const [find, setFind] = React.useState('');
  const [order, setOrder] = React.useState<'date' | 'ivchange'>('date');
  const [sel, setSel] = React.useState<string>('');

  const q = useApi<Any>((s) => api2.tabBScan(days, s), [days], { refreshMs: 60_000 });
  const d = q.data || {};
  const rows: Any[] = d.rows || [];

  const priced = rows.filter((r) => r.status !== 'PENDING' && legIv(r.call_now, r.put_now) != null);

  const avgNow = priced.length
    ? priced.map((r) => legIv(r.call_now, r.put_now) as number).reduce((a, b) => a + b, 0) / priced.length
    : null;
  const thenVals = priced.map((r) => legIv(r.call_then, r.put_then)).filter((n): n is number => n != null);
  const avgThen = thenVals.length ? thenVals.reduce((a, b) => a + b, 0) / thenVals.length : null;
  const avgChange = avgNow != null && avgThen != null ? avgNow - avgThen : null;

  const shown = React.useMemo(() => {
    const f = find.trim().toUpperCase();
    let r = f ? rows.filter((x) => String(x.symbol || '').includes(f)) : rows;
    r = [...r];
    if (order === 'ivchange') {
      r.sort((a, b) => {
        const A = ivChange(a); const B = ivChange(b);
        if (A == null && B == null) return 0;
        if (A == null) return 1;          // unpriced rows sort last, never as a zero change
        if (B == null) return -1;
        return B - A;
      });
    } else {
      r.sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')));
    }
    return r;
  }, [rows, find, order]);

  const topMovers = React.useMemo(
    () => priced
      .map((r) => ({ r, c: ivChange(r) }))
      .filter((x): x is { r: Any; c: number } => x.c != null)
      .sort((a, b) => b.c - a.c)
      .slice(0, 6),
    [priced],
  );

  // The selected row: whatever the reader picked, else the biggest IV rise, else the first priced
  // name. One symbol is open at a time, which is what keeps the per-symbol requests to two.
  const selRow: Any = React.useMemo(() => {
    if (sel) return rows.find((r) => r.symbol === sel) || null;
    return topMovers[0]?.r || priced[0] || null;
  }, [sel, rows, topMovers, priced]);
  const selSym: string = selRow?.symbol || '';

  const chain = useApi<Any>((s) => (selSym ? api2.optionChain(selSym, undefined, s)
    : Promise.resolve(null)), [selSym]);
  const ivHist = useApi<Any>((s) => (selSym ? api2.optionIvHistory(selSym, 30, s)
    : Promise.resolve(null)), [selSym]);

  const strikes: Any[] = (chain.data?.strikes || []).filter((x: Any) => x && x.strike != null);
  const trend: Any[] = (ivHist.data?.series || [])
    .filter((p: Any) => p && p.iv != null)
    .map((p: Any) => ({ date: String(p.date || '').slice(5), iv: Number(p.iv) }));

  const selNow = selRow ? legIv(selRow.call_now, selRow.put_now) : null;
  const selThen = selRow ? legIv(selRow.call_then, selRow.put_then) : null;
  const selChange = selRow ? ivChange(selRow) : null;
  const dte = daysTo(selRow?.date);

  const stats = [
    { tone: 'blue', icon: <CalendarDays size={18} />,
      value: d.universe != null ? String(d.universe) : String(rows.length),
      label: 'Upcoming earnings', sub: `next ${days} days` },
    { tone: 'violet', icon: <Gauge size={18} />, value: avgNow != null ? iv(avgNow) : '—',
      label: 'Avg IV (current)', sub: priced.length ? `across ${priced.length} priced` : 'nothing priced yet' },
    { tone: 'blue', icon: <Gauge size={18} />, value: avgThen != null ? iv(avgThen) : '—',
      label: 'Avg IV (10 sessions ago)', sub: 'same strike, same expiry' },
    { tone: avgChange != null && avgChange >= 0 ? 'red' : 'green', icon: <TrendingUp size={18} />,
      value: avgChange != null ? pp(avgChange) : '—',
      label: 'Avg IV change', sub: 'into the report' },
    { tone: 'amber', icon: <Flame size={18} />,
      value: rows.length ? String(rows.length - priced.length) : '—',
      label: 'Still pricing', sub: rows.length - priced.length ? 'filling in' : 'all priced' },
  ];

  // Call IV against put IV at each strike of the event expiry - the reading we hold, in place of the
  // frame's current-vs-100-day column, which nothing measures per strike.
  const byStrike = strikes.map((s: Any) => ({
    strike: s.strike,
    call: s.call?.iv ?? null,
    put: s.put?.iv ?? null,
    atm: !!s.atm,
  }));

  return (
    <div className="nd nst niv">
      {/* ------------------------------------------------------------------ hero */}
      <section className="nd-hero niv-hero">
        <img className="nd-hero-img" src={ivHero} alt="" aria-hidden="true" loading="eager" />
        <div className="nd-hero-left">
          <span className="nd-eyebrow"><Flame size={13} /> Implied volatility</span>
          <h1>IV Crush <em>Check</em></h1>
          <p>
            Track implied volatility before and after earnings to find potential IV crush
            opportunities. The strike is locked to the one that was at the money ten sessions ago, so
            the two readings compare like with like.
          </p>
        </div>
      </section>

      {/* ----------------------------------------------------------------- tiles */}
      <div className="nd-stats niv-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
          </div>
        ))}
      </div>

      {/* --------------------------------------------------------------- filters */}
      <div className="niv-filters">
        <label className="niv-search">
          <Search size={15} />
          <input value={find} onChange={(e) => setFind(e.target.value.toUpperCase())}
            placeholder="Filter by ticker" aria-label="Filter by ticker" spellCheck={false} />
          {!!find && (
            <button type="button" className="icon-btn" onClick={() => setFind('')} aria-label="Clear">
              <X size={13} />
            </button>
          )}
        </label>
        <label className="nst-field">
          <span>Window</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 10, 15, 21, 30].map((x) => <option key={x} value={x}>Next {x} days</option>)}
          </select>
        </label>
        <label className="nst-field">
          <span>Order</span>
          <select value={order} onChange={(e) => setOrder(e.target.value as Any)}>
            <option value="date">By report date</option>
            <option value="ivchange">By IV change</option>
          </select>
        </label>
        <button type="button" className="ghost-btn" onClick={() => { setFind(''); setOrder('date'); setDays(15); setSel(''); }}>
          <RefreshCw size={13} /> Reset
        </button>
        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* ------------------------------------------------- detail + upcoming list */}
      <div className="niv-split">
        <section className="nd-card niv-detail">
          {!selRow && (
            <div className="nd-empty">{q.loading ? 'Loading…' : 'No priced company in this window yet.'}</div>
          )}
          {!!selRow && (
            <>
              <header className="niv-detail-head">
                <div>
                  <h3>{selRow.symbol}</h3>
                  <span className="nst-mute">{selRow.company || '—'}</span>
                </div>
                <div className="niv-detail-price">
                  <b>{money(selRow.spot ?? chain.data?.spot)}</b>
                  <span className="nst-mute">strike {selRow.strike != null ? Number(selRow.strike).toFixed(0) : '—'} · exp {dmy(selRow.expiry)}</span>
                </div>
                <div className="niv-detail-when">
                  <span className="niv-badge">{selRow.date_label || dmy(selRow.date)}</span>
                  {dte != null && <span className="nst-mute">{dte === 0 ? 'reports today' : `${dte} day${dte === 1 ? '' : 's'} to earnings`}</span>}
                </div>
              </header>

              <div className="niv-metrics">
                <div><span>Current IV</span><b>{iv(selNow)}</b></div>
                <div><span>IV 10 sessions ago</span><b>{iv(selThen)}</b></div>
                <div><span>IV change</span>
                  <b className={selChange == null ? '' : selChange >= 0 ? 'up' : 'down'}>{pp(selChange)}</b>
                </div>
                <div><span>IV rank</span>
                  <b>{ivHist.data?.iv_rank != null ? `${Number(ivHist.data.iv_rank).toFixed(0)}/100` : '—'}</b>
                </div>
              </div>

              <div className="niv-chart">
                <div className="niv-chart-head">
                  <h4>Implied volatility trend (30 sessions)</h4>
                  {ivHist.data?.iv_low != null && ivHist.data?.iv_high != null && (
                    <span className="nst-mute">{iv(ivHist.data.iv_low)} – {iv(ivHist.data.iv_high)}</span>
                  )}
                </div>
                {trend.length > 1 ? (
                  <ResponsiveContainer width="100%" height={168}>
                    <AreaChart data={trend} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                      <defs>
                        <linearGradient id="nivFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--nu-accent-2)" stopOpacity={0.45} />
                          <stop offset="100%" stopColor="var(--nu-accent-2)" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="var(--nu-line)" vertical={false} />
                      <XAxis dataKey="date" tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)"
                        minTickGap={22} tickLine={false} />
                      <YAxis tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)" width={44}
                        tickLine={false} unit="%" />
                      <Tooltip formatter={(v: Any) => [`${Number(v).toFixed(1)}%`, 'IV']} />
                      {selNow != null && <ReferenceLine y={selNow} stroke="var(--nu-warn)" strokeDasharray="4 3" />}
                      <Area type="monotone" dataKey="iv" stroke="var(--nu-accent-2)" strokeWidth={2}
                        fill="url(#nivFill)" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="nd-empty">
                    {ivHist.loading ? 'Loading the IV series…' : 'No implied-volatility history for this name.'}
                  </div>
                )}
              </div>
            </>
          )}
        </section>

        <section className="nd-card niv-upcoming">
          <header className="nd-card-head"><h3>Upcoming earnings (high IV change)</h3></header>
          {!topMovers.length && <div className="nd-empty">{q.loading ? 'Loading…' : 'Nothing priced yet.'}</div>}
          {!!topMovers.length && (
            <ul className="niv-top-list">
              {topMovers.map(({ r, c }) => (
                <li key={r.symbol}>
                  <button type="button" className={`niv-pick ${r.symbol === selSym ? 'is-on' : ''}`}
                    onClick={() => setSel(r.symbol)}>
                    <span className="nd-sym">{r.symbol}</span>
                    <span className="nst-mute">{r.date_label || dmy(r.date)}</span>
                    <b className={c >= 0 ? 'up' : 'down'}>{pp(c)}</b>
                    <i className="nst-mute">{iv(legIv(r.call_now, r.put_now))}</i>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* ------------------------------------------------ calls | strike | puts */}
      <section className="nd-card niv-ladder">
        <header className="nd-card-head">
          <h3>Options IV comparison{selSym ? ` — ${selSym}` : ''}</h3>
          {!!chain.data?.expiry && <span className="nst-mute">expiry {dmy(chain.data.expiry)}</span>}
        </header>
        {!strikes.length && (
          <div className="nd-empty">
            {chain.loading ? 'Loading the option ladder…'
              : selSym ? 'No option ladder for this name right now.' : 'Pick a company above.'}
          </div>
        )}
        {!!strikes.length && (
          <div className="nst-scroll">
            <table className="nd-table niv-ladder-table">
              <thead>
                <tr>
                  <th colSpan={5} className="niv-calls">Calls</th>
                  <th className="niv-strike-head">Strike</th>
                  <th colSpan={5} className="niv-puts">Puts</th>
                </tr>
                <tr>
                  <th className="r">IV</th><th className="r">Bid</th><th className="r">Ask</th>
                  <th className="r">Last</th><th className="r">OI</th>
                  <th className="r niv-strike-head" />
                  <th className="r">IV</th><th className="r">Bid</th><th className="r">Ask</th>
                  <th className="r">Last</th><th className="r">OI</th>
                </tr>
              </thead>
              <tbody>
                {strikes.map((s: Any) => (
                  <tr key={s.strike} className={s.atm ? 'niv-atm' : undefined}>
                    <td className="r"><b>{iv(s.call?.iv)}</b></td>
                    <td className="r">{num2(s.call?.bid)}</td>
                    <td className="r">{num2(s.call?.ask)}</td>
                    <td className="r">{num2(s.call?.last)}</td>
                    <td className="r nst-mute">{s.call?.open_interest ?? '—'}</td>
                    <td className="r niv-strike">{Number(s.strike).toFixed(0)}</td>
                    <td className="r"><b>{iv(s.put?.iv)}</b></td>
                    <td className="r">{num2(s.put?.bid)}</td>
                    <td className="r">{num2(s.put?.ask)}</td>
                    <td className="r">{num2(s.put?.last)}</td>
                    <td className="r nst-mute">{s.put?.open_interest ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* -------------------------------------------------- IV across the ladder */}
      <section className="nd-card niv-bars">
        <header className="nd-card-head">
          <h3>Implied volatility across strikes</h3>
          <span className="nst-mute">call against put, same expiry</span>
        </header>
        {byStrike.length > 1 ? (
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={byStrike} margin={{ top: 8, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="var(--nu-line)" vertical={false} />
              <XAxis dataKey="strike" tick={{ fontSize: 11 }} stroke="var(--nu-ink-dim)" tickLine={false} />
              <YAxis tick={{ fontSize: 10 }} stroke="var(--nu-ink-dim)" width={44} tickLine={false} unit="%" />
              <Tooltip formatter={(v: Any, n: Any) => [`${Number(v).toFixed(1)}%`, n === 'call' ? 'Call IV' : 'Put IV']} />
              <Bar dataKey="call" radius={[3, 3, 0, 0]}>
                {byStrike.map((b) => <Cell key={`c${b.strike}`} fill={b.atm ? 'var(--nu-accent-2)' : 'var(--nu-up)'} />)}
              </Bar>
              <Bar dataKey="put" radius={[3, 3, 0, 0]}>
                {byStrike.map((b) => <Cell key={`p${b.strike}`} fill={b.atm ? 'var(--nu-warn)' : 'var(--nu-down)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="nd-empty">
            {chain.loading ? 'Loading…' : 'No ladder to compare for this name.'}
          </div>
        )}
      </section>

      <div className="nua-explain">
        <b>What this shows.</b> Option premium usually swells into a report and falls once the result is
        out. The two IV readings here are that swell, measured on the same strike and the same expiry.
        A large rise says the option market expects a big move — not which way.
        <b> It is not a trade plan</b>, and this page places no orders.
      </div>

      {/* ------------------------------------------------------- the full window */}
      <section className="nd-card nst-card">
        <header className="nd-card-head"><h3>Every company reporting in this window</h3></header>
        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && <div className="nd-empty">No company reports in this window.</div>}
        {!!rows.length && !shown.length && <div className="nd-empty">Nothing matches this filter.</div>}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Company</th><th>Reports</th><th>Expiry</th>
                  <th className="r">Strike</th>
                  <th className="r">Call then</th><th className="r">Call now</th>
                  <th className="r">Put then</th><th className="r">Put now</th>
                  <th className="r">IV change</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const pending = r.status === 'PENDING' || !r.call_now;
                  const c = ivChange(r);
                  return (
                    <tr key={`${r.symbol}-${r.date}`}
                      className={r.symbol === selSym ? 'niv-row-on' : undefined}
                      onClick={() => setSel(r.symbol)}>
                      <td><span className="nd-sym">{r.symbol}</span></td>
                      <td className="nst-mute npe-co">{r.company || '—'}</td>
                      <td>{r.date_label || dmy(r.date)}</td>
                      <td className="nst-mute">{dmy(r.expiry)}</td>
                      <td className="r">{r.strike != null ? Number(r.strike).toFixed(0) : '—'}</td>
                      {pending ? (
                        <td className="r nst-mute" colSpan={5}>pricing…</td>
                      ) : (
                        <>
                          <td className="r">
                            {num2(r.call_then?.rate)}<div className="nst-sub">{iv(r.call_then?.iv)}</div>
                          </td>
                          <td className="r">
                            <b>{num2(r.call_now?.rate)}</b><div className="nst-sub">{iv(r.call_now?.iv)}</div>
                          </td>
                          <td className="r">
                            {num2(r.put_then?.rate)}<div className="nst-sub">{iv(r.put_then?.iv)}</div>
                          </td>
                          <td className="r">
                            <b>{num2(r.put_now?.rate)}</b><div className="nst-sub">{iv(r.put_now?.iv)}</div>
                          </td>
                          <td className={`r ${c == null ? '' : c >= 0 ? 'up' : 'down'}`}>
                            <b>{pp(c)}</b>
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="niv-legend">
        <span><i className="niv-dot live" /> Live quote</span>
        <span><i className="niv-dot calc" /> At the money</span>
        <span><i className="niv-dot wait" /> Still pricing</span>
        <span className="nst-mute">IV change = IV now − IV ten sessions ago, same strike and expiry.</span>
      </p>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Scan detail (administrator)</summary>
          <div>
            source {d.source || '—'} · universe {d.universe ?? '—'} · priced {priced.length} ·
            window {days} days · open symbol {selSym || '—'} ·
            chain {chain.data?.status || '—'} · iv series {ivHist.data?.samples ?? '—'}
            {!!(d.missing || []).length && <> · awaiting {(d.missing || []).length}</>}
            {!!(d.refetch || []).length && <> · refetching after 15:55 ET: {(d.refetch || []).length}</>}
          </div>
        </details>
      )}

      {q.error && (
        <div className="nd-err"><AlertTriangle size={15} /> The scan could not be loaded. Nothing is shown rather than stale prices.</div>
      )}
    </div>
  );
}
