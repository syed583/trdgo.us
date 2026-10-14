import { Fragment, useEffect, useMemo, useState } from 'react';
import { Loader2, TrendingUp, ArrowUpDown, Maximize2, Minimize2 } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Strategy A -- Long Straddle (IV run-up). Buy the ATM call + ATM put ~15 days
// before a watchlist name's earnings, exit the day BEFORE the result. Profit
// from IV / premium rising into the event; risk limited to the premium paid.

const stClass = (s: string) =>
  s === 'ENTER' ? 'pos' : s === 'EXIT' ? 'neg' : s === 'PENDING' || s === 'SKIP' ? 'skip' : 'neu';
const viewClass = (v: string) =>
  v === 'bullish' ? 'pos' : v === 'bearish' ? 'neg' : 'neu';
const n1 = (v: any) => (v == null ? '—' : Number(v).toFixed(1));
const nInt = (v: any) => (v == null ? '—' : Number(v).toLocaleString());
const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);

export default function LongStraddlePage({ ctx }: { ctx: PageContext }) {
  const [sortKey, setSortKey] = useState<string>('status');
  const [asc, setAsc] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [full, setFull] = useState(false);

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [full]);

  const resp = useApi<any>((s) => api2.longStraddleScan(21, s), [],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const rows: any[] = resp.data?.rows || [];

  const view = useMemo(() => {
    const ORDER: Record<string, number> = { ENTER: 0, HOLD: 1, EXIT: 2, WATCH: 3, SKIP: 4, PENDING: 5 };
    const xs = rows.slice();
    const val = (r: any) => {
      switch (sortKey) {
        case 'symbol': return r.symbol;
        case 'days_to': return r.days_to ?? 9999;
        case 'iv_rank': return r.iv_rank ?? 9999;
        case 'atm_iv': return r.atm_iv ?? -1;
        case 'premium_pct': return r.premium_pct ?? -1;
        case 'leg_spread_pct': return r.leg_spread_pct ?? 9999;
        default: return ORDER[r.status] ?? 9;
      }
    };
    xs.sort((a, b) => {
      const va = val(a), vb = val(b);
      const c = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
      return asc ? c : -c;
    });
    return xs;
  }, [rows, sortKey, asc]);

  const toggleSort = (k: string) => {
    if (sortKey === k) setAsc((v) => !v);
    else { setSortKey(k); setAsc(k !== 'atm_iv' && k !== 'premium_pct'); }
  };
  const Th = ({ k, children, cls }: any) => (
    <th className={`ts-sort ${cls || ''} ${sortKey === k ? 'on' : ''}`} onClick={() => toggleSort(k)}>
      {children}{sortKey === k ? (asc ? ' ▲' : ' ▼') : ''}
    </th>
  );

  const enterable = rows.filter((r) => r.status === 'ENTER').length;

  return (
    <div className="page">
      <div className="ts-head">
        <h1><TrendingUp size={20} /> Long Straddle · IV Run-up</h1>
        <p>Buy the ATM call + ATM put ~15 days before a watchlist name's earnings, when IV still has
          room to rise, and exit the day <b>before</b> the result (never hold through it). Profit from
          IV / premium rising into the event — risk is limited to the premium paid. At exit the
          direction view decides which leg to keep.</p>
      </div>
      <div className={`ts-fswrap${full ? ' on' : ''}`}>
      <Panel
        title={<span className="at-col-title">Long straddle plan</span>}
        right={(
          <span className="ts-controls">
            <span className="at-chip">{enterable} to enter</span>
            <button className="ts-fsbtn" onClick={() => setFull((v) => !v)}
              title={full ? 'Exit full screen (Esc)' : 'Full screen'}
              aria-label={full ? 'Exit full screen' : 'Full screen'}>
              {full ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </span>
        )}
      >
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Planning long straddles…</p>
        ) : !rows.length ? (
          <p className="at-note">No watchlist names report in the next ~3 weeks.</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <Th k="symbol" cls="ts-sym">Ticker</Th>
                  <th className="ts-num">Price</th>
                  <th>Earnings</th>
                  <Th k="days_to" cls="ts-num">Days</Th>
                  <th className="ts-num">Strike / Expiry</th>
                  <Th k="atm_iv" cls="ts-num">ATM IV %</Th>
                  <Th k="iv_rank" cls="ts-num">IV Rank</Th>
                  <Th k="premium_pct" cls="ts-num">Debit ($ · %)</Th>
                  <Th k="leg_spread_pct" cls="ts-num">Spread %</Th>
                  <th>View</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {view.map((r) => {
                  const pending = r.status === 'PENDING';
                  const isOpen = open === r.symbol && !pending;
                  return (
                  <Fragment key={r.symbol}>
                  <tr className={isOpen ? 'ts-open' : ''}
                    onClick={() => !pending && setOpen(isOpen ? null : r.symbol)}>
                    <td className="ts-sym">{r.symbol}</td>
                    <td className="ts-num">{money(r.spot)}</td>
                    <td className="ts-date">{(r.date_label || r.date || '—')}
                      {r.result_code ? ` · ${r.result_code}` : ''}</td>
                    <td className="ts-num">{r.days_to ?? '—'}</td>
                    <td className="ts-num">{r.atm_strike != null ? r.atm_strike : '—'}
                      {r.expiry ? ` · ${String(r.expiry).slice(5)}` : ''}</td>
                    <td className="ts-num">{n1(r.atm_iv)}</td>
                    <td className="ts-num">{n1(r.iv_rank)}</td>
                    <td className="ts-num">{money(r.premium)}{r.premium_pct != null ? ` · ${n1(r.premium_pct)}%` : ''}</td>
                    <td className="ts-num">{n1(r.leg_spread_pct)}</td>
                    <td>{r.view ? <span className={viewClass(r.view)}>{r.view}</span> : '—'}</td>
                    <td><span className={`ts-badge ${stClass(r.status)}`}>{pending ? 'warming' : r.status}</span>
                      {r.note ? <span className="ts-excl"> {r.note}</span> : null}</td>
                  </tr>
                  {isOpen && (
                    <tr className="ts-detailrow">
                      <td colSpan={11}>
                        <div className="ts-detail">
                          <div className="ts-detail-h">
                            {r.symbol} — {r.status === 'EXIT'
                              ? `exit now: ${r.exit_plan} (view: ${r.view})`
                              : r.status === 'ENTER'
                                ? 'buy 1× ATM call + 1× ATM put, same strike & expiry (first after earnings)'
                                : r.status === 'HOLD'
                                  ? 'position open — ride the IV run-up; exit the day before the result'
                                  : r.note || ''}
                          </div>
                          <div className="ts-detail-grid">
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Entry debit</span>
                              <span className="ts-detail-pts"><b>{money(r.premium)}</b>
                                <em> · {n1(r.premium_pct)}% of price</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">IV rank (want low)</span>
                              <span className="ts-detail-pts"><b>{n1(r.iv_rank)}</b>
                                <em> · {r.iv_has_room ? 'room to rise' : 'already high'}</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Liquidity</span>
                              <span className="ts-detail-pts">leg spread <b>{n1(r.leg_spread_pct)}%</b>
                                <em> · OI {nInt(r.oi)}</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Exit plan (view: {r.view})</span>
                              <span className="ts-detail-pts"><b>{r.exit_plan}</b></span>
                            </div>
                          </div>
                          <div className="ts-detail-sub">Strike {r.atm_strike ?? '—'} · expiry {r.expiry || '—'}
                            {' '}(first after earnings) · risk limited to the ${money(r.premium).replace('$', '')} debit ·
                            set a theta stop (~−20–25%) if the stock stays flat.</div>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="cn-legend" style={{ margin: '10px 2px 0', flexWrap: 'wrap', gap: 14 }}>
          <span><b>ENTER</b> ~15d out, IV rank low, liquid · <b>HOLD</b> position open</span>
          <span><b>EXIT</b> day before the result · <b>WATCH</b> too early · <b>SKIP</b> IV high / illiquid</span>
          <span><b>View</b> → exit: bullish keep call · bearish keep put · no view close both</span>
        </div>
        <div className="ts-foot"><ArrowUpDown size={11} /> Click a column to sort, or a row for the
          plan. ENTER wants IV rank below ~40 (room to rise) and a tight spread. Never hold a long
          straddle through the result — the IV crush would wipe the run-up gain.</div>
      </Panel>
      </div>
    </div>
  );
}
