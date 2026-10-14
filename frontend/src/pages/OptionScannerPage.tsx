import { Fragment, useEffect, useMemo, useState } from 'react';
import { Loader2, Flame, ArrowUpDown, Maximize2, Minimize2 } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Short Straddle Scanner (IV-crush). Ranks the 67-name watchlist names reporting
// the NEXT session by how inflated their options are RIGHT NOW: the average of
// the ATM-IV rank and the straddle-premium-% rank (1 = most inflated). No IV
// history, no implied-vs-historical filter. Status is SELL STRADDLE / SKIP.

const stClass = (s: string) =>
  s === 'SELL STRADDLE' ? 'pos' : s === 'PENDING' ? 'neu' : 'skip';
const n1 = (v: any) => (v == null ? '—' : Number(v).toFixed(1));
const n2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2));
const nInt = (v: any) => (v == null ? '—' : Number(v).toLocaleString());
const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);

// A tiny inline IV sparkline: the last ~15 days of IV. Rising into earnings is
// green (more IV left to crush); falling is red.
function Spark({ data, up }: { data: number[]; up: boolean }) {
  if (!data || data.length < 2) return <span className="ts-muted">—</span>;
  const w = 68, h = 18, lo = Math.min(...data), hi = Math.max(...data);
  const rng = hi - lo || 1;
  const pts = data.map((v, i) =>
    `${(i / (data.length - 1)) * w},${h - ((v - lo) / rng) * h}`).join(' ');
  const col = up ? 'var(--green)' : 'var(--red)';
  return (
    <svg width={w} height={h} style={{ display: 'block' }} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={col} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export default function OptionScannerPage({ ctx }: { ctx: PageContext }) {
  const [sortKey, setSortKey] = useState<string>('rank');
  const [asc, setAsc] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [full, setFull] = useState(false);

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [full]);

  const resp = useApi<any>((s) => api2.tabBScan(5, s), [],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const rows: any[] = resp.data?.rows || [];

  const view = useMemo(() => {
    const xs = rows.slice();
    const val = (r: any) => {
      switch (sortKey) {
        case 'symbol': return r.symbol;
        case 'iv_ratio': return r.iv_ratio ?? -1;
        case 'premium_ratio': return r.premium_ratio ?? -1;
        case 'leg_spread_pct': return r.leg_spread_pct ?? 9999;
        default: return r.rank ?? 9999;   // ranked first, unranked last
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
    else { setSortKey(k); setAsc(k === 'symbol' || k === 'rank' || k === 'leg_spread_pct'); }
  };
  const Th = ({ k, children, cls }: any) => (
    <th className={`ts-sort ${cls || ''} ${sortKey === k ? 'on' : ''}`} onClick={() => toggleSort(k)}>
      {children}{sortKey === k ? (asc ? ' ▲' : ' ▼') : ''}
    </th>
  );

  const sellable = rows.filter((r) => r.status === 'SELL STRADDLE').length;

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Flame size={20} /> Short Straddle Scanner · IV Crush</h1>
        <p>Sell the ATM call + ATM put the session before earnings and profit from the IV crush
          after the result. Ranks the 67-name watchlist names reporting the <b>next session</b> by how
          much their premium and IV have <b>inflated vs 10 trading sessions ago</b> — the average of the
          premium-ratio and IV-ratio ranks (#1 = most inflated). It does not predict direction.</p>
      </div>
      <div className={`ts-fswrap${full ? ' on' : ''}`}>
      <Panel
        title={<span className="at-col-title">Next-session straddles</span>}
        right={(
          <span className="ts-controls">
            <span className="at-chip">{sellable} sellable</span>
            <button className="ts-fsbtn" onClick={() => setFull((v) => !v)}
              title={full ? 'Exit full screen (Esc)' : 'Full screen'}
              aria-label={full ? 'Exit full screen' : 'Full screen'}>
              {full ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </span>
        )}
      >
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Scanning next-session straddles…</p>
        ) : !rows.length ? (
          <p className="at-note">No watchlist names report the next session. This tab fills the
            evening before a watchlist name's earnings (reports due after today's close or before the
            next open).</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <Th k="rank" cls="ts-num">Rank</Th>
                  <Th k="symbol" cls="ts-sym">Ticker</Th>
                  <th className="ts-num">Price</th>
                  <th>Result</th>
                  <th className="ts-num">Strike / Expiry</th>
                  <Th k="premium_ratio" cls="ts-num">Premium (now · 10d · ×)</Th>
                  <Th k="iv_ratio" cls="ts-num">IV (now · 10d · ×)</Th>
                  <Th k="leg_spread_pct" cls="ts-num">Spread %</Th>
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
                    <td className="ts-num ts-score pos">{r.rank ?? '—'}</td>
                    <td className="ts-sym">{r.symbol}</td>
                    <td className="ts-num">{money(r.spot)}</td>
                    <td className="ts-date">{(r.date_label || r.date || '—')}
                      {r.result_code ? ` · ${r.result_code}` : ''}</td>
                    <td className="ts-num">{r.atm_strike != null ? r.atm_strike : '—'}
                      {r.expiry ? ` · ${String(r.expiry).slice(5)}` : ''}</td>
                    <td className="ts-num">{money(r.premium)}
                      {r.premium_prev != null ? ` · ${money(r.premium_prev)}` : ' · —'}
                      {r.premium_ratio != null
                        ? <b className={r.premium_ratio >= 1 ? 'pos' : 'neg'}> ×{n2(r.premium_ratio)}</b>
                        : <span className="ts-muted"> (building)</span>}</td>
                    <td className="ts-num">{n1(r.atm_iv)}
                      {r.iv_prev != null ? ` · ${n1(r.iv_prev)}` : ' · —'}
                      {r.iv_ratio != null
                        ? <b className={r.iv_ratio >= 1 ? 'pos' : 'neg'}> ×{n2(r.iv_ratio)}</b> : ''}</td>
                    <td className="ts-num">{n1(r.leg_spread_pct)}</td>
                    <td><span className={`ts-badge ${stClass(r.status)}`}>{pending ? 'warming' : r.status}</span>
                      {r.exclusion ? <span className="ts-excl"> {r.exclusion}</span> : null}</td>
                  </tr>
                  {isOpen && (
                    <tr className="ts-detailrow">
                      <td colSpan={9}>
                        <div className="ts-detail">
                          <div className="ts-detail-h">
                            {r.symbol} — sell the ATM call + ATM put (short straddle) the session
                            before the result; buy both back after the IV crush.
                            {r.exclusion ? ` · Excluded: ${r.exclusion}` : ''}
                          </div>
                          <div className="ts-detail-grid">
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">IV inflation</span>
                              <span className="ts-detail-pts"><b>×{n2(r.iv_ratio)}</b> (rank #{r.rank_iv ?? '—'})
                                <em> · {n1(r.atm_iv)}% now vs {n1(r.iv_prev)}% {r.lookback ?? 10}d ago</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Premium inflation</span>
                              <span className="ts-detail-pts">
                                {r.premium_ratio != null
                                  ? <><b>×{n2(r.premium_ratio)}</b> (rank #{r.rank_prem ?? '—'})</>
                                  : <b className="ts-muted">building</b>}
                                <em> · {money(r.premium)} now vs {r.premium_prev != null ? money(r.premium_prev) : '—'}</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Combined rank</span>
                              <span className="ts-detail-pts"><b>#{r.rank ?? '—'}</b>
                                <em> · avg of the two inflation ranks</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">Liquidity</span>
                              <span className="ts-detail-pts">leg spread <b>{n1(r.leg_spread_pct)}%</b>
                                <em> · OI {nInt(r.oi)}</em></span>
                            </div>
                            <div className="ts-detail-item">
                              <span className="ts-detail-lbl">IV trend (10 sessions)</span>
                              <span className="ts-detail-pts" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <Spark data={r.iv_spark} up={(r.iv_ratio ?? 1) >= 1} />
                                <em>{r.iv_prev != null ? `${n1(r.iv_prev)}% → ${n1(r.atm_iv)}%` : 'no history'}</em>
                              </span>
                            </div>
                          </div>
                          <div className="ts-detail-sub">Strike {r.atm_strike ?? '—'} · expiry {r.expiry || '—'}
                            {r.ex_div ? ` · ex-div ${r.ex_div}` : ''} · ⚠ short straddle = unlimited risk on a
                            big gap; size small. Premium inflation fills in once ~10 daily snapshots exist.</div>
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
          <span><b>SELL STRADDLE</b> = sell both the ATM call and the ATM put (short straddle)</span>
          <span><b>Rank</b> = avg of the premium-inflation and IV-inflation ranks vs 10 sessions ago; #1 = most inflated</span>
          <span><b>SKIP</b> = no real inflation, illiquid, ex-dividend before expiry, or date unconfirmed</span>
        </div>
        <div className="ts-foot"><ArrowUpDown size={11} /> Click a column to sort, or a row for its
          breakdown. Only watchlist names reporting the next session appear. The IV ratio is live; the
          premium ratio fills in once ~10 daily option snapshots have been recorded.</div>
      </Panel>
      </div>
    </div>
  );
}
