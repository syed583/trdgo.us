import { useMemo, useState } from 'react';
import { Loader2, Flame, ArrowUpDown } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Tab B -- Short Straddle Scanner. Ranks names by inflated premium / IV.
// Action SELL >= 70 / WATCH 50-69 / SKIP, plus a bullish/bearish/neutral tilt.

const actClass = (a: string) => (a === 'SELL' ? 'pos' : a === 'WATCH' ? 'neu' : 'skip');
const tiltClass = (t: string) => (t === 'bullish' ? 'pos' : t === 'bearish' ? 'neg' : 'neu');
const n1 = (v: any) => (v == null ? '—' : Number(v).toFixed(1));
const n2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2));

export default function OptionScannerPage({ ctx }: { ctx: PageContext }) {
  const [days, setDays] = useState(15);
  const [maxDays, setMaxDays] = useState<number | ''>('');
  const [sortKey, setSortKey] = useState<string>('score');
  const [asc, setAsc] = useState(false);

  const resp = useApi<any>((s) => api2.tabBScan(days, s), [days],
    { refreshMs: ctx.demo ? undefined : 60_000 });
  const rows: any[] = resp.data?.rows || [];

  const view = useMemo(() => {
    let xs = rows.slice();
    if (maxDays !== '') xs = xs.filter((r) => r.days_to != null && r.days_to <= maxDays);
    const val = (r: any) => {
      switch (sortKey) {
        case 'symbol': return r.symbol;
        case 'days_to': return r.days_to ?? 9999;
        case 'iv_rank': return r.iv_rank ?? -1;
        case 'implied_move': return r.implied_move ?? -1;
        case 'impl_vs_hist': return r.impl_vs_hist ?? -1;
        case 'spread_pct': return r.spread_pct ?? 9999;
        default: return r.score ?? -1;
      }
    };
    xs.sort((a, b) => {
      const va = val(a), vb = val(b);
      const c = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
      return asc ? c : -c;
    });
    return xs;
  }, [rows, maxDays, sortKey, asc]);

  const toggleSort = (k: string) => {
    if (sortKey === k) setAsc((v) => !v);
    else { setSortKey(k); setAsc(k === 'symbol' || k === 'days_to' || k === 'spread_pct'); }
  };
  const Th = ({ k, children, cls }: any) => (
    <th className={`ts-sort ${cls || ''} ${sortKey === k ? 'on' : ''}`} onClick={() => toggleSort(k)}>
      {children}{sortKey === k ? (asc ? ' ▲' : ' ▼') : ''}
    </th>
  );

  return (
    <div className="page">
      <div className="ts-head">
        <h1><Flame size={20} /> Option Scanner · Call &amp; Premium</h1>
        <p>Short-straddle scanner: ranks upcoming reports by how inflated their option
          premium / IV is (bigger IV crush = better sell). SELL ≥ 70 · WATCH 50–69 · SKIP.
          Tilt says where to centre the short strikes. It does not predict direction.</p>
      </div>
      <Panel
        title={<span className="at-col-title">Straddle scan</span>}
        right={(
          <span className="ts-controls">
            <label>Window
              <select value={days} onChange={(e) => setDays(+e.target.value)}>
                <option value={7}>7d</option><option value={15}>15d</option><option value={21}>21d</option>
              </select>
            </label>
            <label>≤ days
              <input type="number" min={0} placeholder="all" value={maxDays}
                onChange={(e) => setMaxDays(e.target.value === '' ? '' : +e.target.value)} />
            </label>
            <span className="at-chip">{view.length}</span>
          </span>
        )}
      >
        {resp.initialLoading && !rows.length ? (
          <p className="at-note"><Loader2 size={12} className="spin" /> Scanning straddles…</p>
        ) : !rows.length ? (
          <p className="at-note">No scanned names yet. Each name's IV / premium fills in as the
            scanner warms it.{resp.data?.missing?.length ? ` (${resp.data.missing.length} warming)` : ''}</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <Th k="symbol" cls="ts-sym">Ticker</Th>
                  <th>Earnings</th>
                  <Th k="days_to">Days</Th>
                  <Th k="iv_rank" cls="ts-num">IV Rank</Th>
                  <Th k="implied_move" cls="ts-num">Impl %</Th>
                  <Th k="impl_vs_hist" cls="ts-num">Impl÷Hist</Th>
                  <Th k="spread_pct" cls="ts-num">Spread %</Th>
                  <Th k="score" cls="ts-num">Score</Th>
                  <th>Action</th>
                  <th>Tilt</th>
                  <th>Exclusion</th>
                </tr>
              </thead>
              <tbody>
                {view.map((r) => (
                  <tr key={r.symbol}>
                    <td className="ts-sym">{r.symbol}</td>
                    <td className="ts-date">{r.date_label || r.date || '—'}</td>
                    <td className="ts-num">{r.days_to ?? '—'}</td>
                    <td className="ts-num">{n1(r.iv_rank)}</td>
                    <td className="ts-num">{n1(r.implied_move)}</td>
                    <td className="ts-num">{n2(r.impl_vs_hist)}</td>
                    <td className="ts-num">{n1(r.spread_pct)}</td>
                    <td className={`ts-score ${actClass(r.action)}`}>{r.score}</td>
                    <td><span className={`ts-badge ${actClass(r.action)}`}>{r.action}</span></td>
                    <td><span className={tiltClass(r.tilt)}>{r.tilt}</span></td>
                    <td className="ts-date ts-excl">{r.exclusion || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot"><ArrowUpDown size={11} /> Click a column to sort. IV Rank 40 · Implied
          Move % 25 · Implied÷Historical 20 · Liquidity 15 = 100. Hard exclusions (ratio &lt; 0.90,
          illiquid, unconfirmed date) force SKIP. OI isn't in the straddle feed, so Liquidity uses
          spread only.</div>
      </Panel>
    </div>
  );
}
