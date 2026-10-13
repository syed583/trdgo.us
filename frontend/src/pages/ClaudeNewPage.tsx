import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, TrendingUp, ArrowUpDown } from 'lucide-react';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './tab-scan.css';

// Tab A -- Pre-Earnings Stock Direction v2 (all-positive scale, 65/35 cuts).
// One column per parameter; sortable by score; filter by days to earnings.

const PARAMS: { key: string; label: string }[] = [
  { key: 'options', label: 'Options' },
  { key: 'price_action', label: 'Price' },
  { key: 'insider', label: 'Insider' },
  { key: 'peers', label: 'Peers' },
  { key: 'eps_rev', label: 'EPS' },
  { key: 'hist_reaction', label: 'Hist' },
  { key: 'rev_rev', label: 'Rev' },
  { key: 'prior_guidance', label: 'Guid' },
  { key: 'dark_pool', label: 'Dark' },
];

const sigClass = (sig: string) =>
  sig === 'BUY' ? 'pos' : sig === 'SELL' ? 'neg' : 'neu';

export default function ClaudeNewPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [days, setDays] = useState(15);
  const [maxDays, setMaxDays] = useState<number | ''>('');
  const [sortKey, setSortKey] = useState<string>('score');
  const [asc, setAsc] = useState(false);

  const resp = useApi<any>((s) => api2.tabAScan(days, s), [days],
    { refreshMs: ctx.demo ? undefined : 120_000 });
  const rows: any[] = resp.data?.rows || [];

  const pmap = (r: any) => {
    const m: Record<string, number> = {};
    for (const p of r.params || []) m[p.name] = p.points;
    return m;
  };

  const view = useMemo(() => {
    let xs = rows.slice();
    if (maxDays !== '') xs = xs.filter((r) => r.days_to != null && r.days_to <= maxDays);
    const val = (r: any) => {
      if (sortKey === 'score') return r.score ?? -1;
      if (sortKey === 'days_to') return r.days_to ?? 9999;
      if (sortKey === 'symbol') return r.symbol;
      return pmap(r)[sortKey] ?? -1;
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
    else { setSortKey(k); setAsc(k === 'symbol' || k === 'days_to'); }
  };
  const Th = ({ k, children, cls }: any) => (
    <th className={`ts-sort ${cls || ''} ${sortKey === k ? 'on' : ''}`} onClick={() => toggleSort(k)}>
      {children}{sortKey === k ? (asc ? ' ▲' : ' ▼') : ''}
    </th>
  );

  return (
    <div className="page">
      <div className="ts-head">
        <h1><TrendingUp size={20} /> Claude New · 13 Oct</h1>
        <p>Pre-earnings direction (v2). All-positive score 0–100: BUY ≥ 65, SELL ≤ 35,
          else NEUTRAL. One column per parameter — every number is checkable.</p>
      </div>
      <Panel
        title={<span className="at-col-title">Direction scan</span>}
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
          <p className="at-note"><Loader2 size={12} className="spin" /> Scoring upcoming reports…</p>
        ) : !rows.length ? (
          <p className="at-note">No scored names yet. Scores fill in as the earnings engine
            warms each upcoming name.{resp.data?.missing?.length
              ? ` (${resp.data.missing.length} still warming)` : ''}</p>
        ) : (
          <div className="ts-wrap">
            <table className="ts-tbl">
              <thead>
                <tr>
                  <Th k="symbol" cls="ts-sym">Ticker</Th>
                  <th>Earnings</th>
                  <Th k="days_to">Days</Th>
                  <Th k="score">Score</Th>
                  <th>Signal</th>
                  {PARAMS.map((p) => <Th key={p.key} k={p.key} cls="ts-num">{p.label}</Th>)}
                </tr>
              </thead>
              <tbody>
                {view.map((r) => {
                  const m = pmap(r);
                  return (
                    <tr key={r.symbol} onClick={() => navigate(`/ai-insights/${r.symbol}`)}>
                      <td className="ts-sym">{r.symbol}</td>
                      <td className="ts-date">{r.date_label || r.date || '—'}</td>
                      <td className="ts-num">{r.days_to ?? '—'}</td>
                      <td className={`ts-score ${sigClass(r.signal)}`}>{r.score}</td>
                      <td><span className={`ts-badge ${sigClass(r.signal)}`}>{r.signal}</span></td>
                      {PARAMS.map((p) => (
                        <td key={p.key} className="ts-num">{m[p.key] != null ? m[p.key].toFixed(1) : '—'}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="ts-foot"><ArrowUpDown size={11} /> Click a column to sort · a row opens the stock.
          Each cell is the parameter's points (max in the spec: Options 17.8 / Price 15.6 / Insider 13.3 /
          Peers·EPS·Hist 11.1 / Rev 8.9 / Guid 6.7 / Dark 4.4 after normalizing to 100).</div>
      </Panel>
    </div>
  );
}
