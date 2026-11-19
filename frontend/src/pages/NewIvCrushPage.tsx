import React from 'react';
import { NavLink } from 'react-router-dom';
import { AlertTriangle, CalendarDays, Flame, Gauge, TrendingUp } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * IV Crush Check in the redesigned shell (Figma frame 8:2).
 *
 * THE FRAME ASKS FOR FAR MORE THAN THIS ENDPOINT HOLDS. A row carries the ATM call and put as they were
 * priced 10 trading sessions ago and as they are priced now - each as {rate, iv} - at ONE locked strike,
 * plus the report date and the ATM's liquidity. That is all. So these parts of the frame are absent
 * rather than approximated:
 *
 *   - "Implied Volatility Trend (30 days)": needs an IV series per symbol. We hold two points, ten
 *     sessions apart. Two points are not a trend line.
 *   - "IV Crush History (Past 4 Earnings)" and "IV Crush (Post)": need IV measured AFTER a report. The
 *     scan only looks forward to the next one, so there is no post-event reading to compare.
 *   - "Options IV Comparison" across five strikes, with bid/ask/last: the scan locks ONE strike (the ATM
 *     of ten sessions ago, kept fixed so the two readings are comparable) and a leg carries no quote
 *     beyond its rate and IV.
 *
 * ONE DELIBERATE CHANGE, FLAGGED TO THE OWNER. The page this replaces states "No ranking, scores,
 * status, colours or extra columns" - it was built to show the two prices and nothing else. The frame
 * ranks by IV change. Both are offered: the default order is still the report date, and ranking by IV
 * change is a choice the reader makes, not one made for them.
 */

type Any = any;

const num2 = (v: Any): string => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
const iv = (v: Any): string => (v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%`);
const pp = (v: Any): string => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)} pp`);
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

export default function NewIvCrushPage({ ctx }: { ctx: PageContext }) {
  const isAdmin = !!ctx?.isAdmin;
  const [days, setDays] = React.useState(15);
  const [find, setFind] = React.useState('');
  const [order, setOrder] = React.useState<'date' | 'ivchange'>('date');

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
      .slice(0, 5),
    [priced],
  );

  const stats = [
    { tone: 'blue', icon: <CalendarDays size={18} />, value: d.universe != null ? String(d.universe) : String(rows.length),
      label: 'Reporting soon', sub: `next ${days} days` },
    { tone: 'violet', icon: <Gauge size={18} />, value: avgNow != null ? iv(avgNow) : '—',
      label: 'Average IV today', sub: priced.length ? `across ${priced.length} priced` : 'nothing priced yet' },
    { tone: 'blue', icon: <Gauge size={18} />, value: avgThen != null ? iv(avgThen) : '—',
      label: 'Average IV 10 sessions ago', sub: '' },
    { tone: avgChange != null && avgChange >= 0 ? 'red' : 'green', icon: <TrendingUp size={18} />,
      value: avgChange != null ? pp(avgChange) : '—',
      label: 'Average IV change', sub: 'into the report' },
    { tone: 'amber', icon: <Flame size={18} />,
      value: rows.length ? String(rows.length - priced.length) : '—',
      label: 'Still pricing', sub: rows.length - priced.length ? 'filling in' : 'all priced' },
  ];

  return (
    <div className="nd nst nua">
      <section className="nua-head">
        <h1><Flame size={22} /> IV Crush Check</h1>
        <p>
          What the at-the-money call and put cost ten trading sessions ago, and what they cost now, for
          every company reporting in the next {days} days. The strike is locked to the one that was at
          the money then, so the two readings compare like with like.
        </p>
      </section>

      <div className="nd-stats nst-stats">
        {stats.map((s) => (
          <div className="nd-stat" key={s.label}>
            <span className={`nd-stat-ico ${s.tone}`}>{s.icon}</span>
            <div><b>{s.value}</b><span>{s.label}</span>{!!s.sub && <i className="nst-stat-sub">{s.sub}</i>}</div>
          </div>
        ))}
      </div>

      <div className="nua-explain">
        <b>What this shows.</b> Option premium usually swells into a report and falls once the result is
        out. The two IV readings here are that swell, measured on the same strike and the same expiry.
        A large rise says the option market expects a big move — not which way.
        <b> It is not a trade plan</b>, and this page places no orders.
      </div>

      <div className="nst-filters">
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
        <label className="nst-field nst-find">
          <span>Symbol</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="e.g. NVDA" />
        </label>
        <span className="nst-count">{shown.length} of {rows.length}</span>
      </div>

      {/* biggest IV rises - the frame's "High IV Change" list, from the rows we have */}
      {!!topMovers.length && (
        <section className="nd-card nst-card niv-top">
          <header className="nd-card-head"><h3>Biggest rise in implied volatility</h3></header>
          <ul className="niv-top-list">
            {topMovers.map(({ r, c }) => (
              <li key={r.symbol}>
                <NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink>
                <span className="nst-mute">{r.date_label || dmy(r.date)}{r.result_time ? ` · ${r.result_time}` : ''}</span>
                <b className={c >= 0 ? 'up' : 'down'}>{pp(c)}</b>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="nd-card nst-card">
        {q.loading && !rows.length && <div className="nd-empty">Loading…</div>}
        {!q.loading && !rows.length && <div className="nd-empty">No company reports in this window.</div>}
        {!!rows.length && !shown.length && <div className="nd-empty">Nothing matches this filter.</div>}

        {!!shown.length && (
          <div className="nst-scroll">
            <table className="nd-table nst-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Company</th><th>Reports</th><th>Timing</th><th>Expiry</th>
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
                    <tr key={`${r.symbol}-${r.date}`}>
                      <td><NavLink to={`/ai-insights?symbol=${r.symbol}`} className="nd-sym">{r.symbol}</NavLink></td>
                      <td className="nst-mute npe-co">{r.company || '—'}</td>
                      <td>{r.date_label || dmy(r.date)}</td>
                      <td className="nst-mute">{r.result_time || '—'}</td>
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

      {isAdmin && (
        <details className="nd-ops">
          <summary>Scan detail (administrator)</summary>
          <div>
            source {d.source || '—'} · universe {d.universe ?? '—'} · priced {priced.length} ·
            window {days} days
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
